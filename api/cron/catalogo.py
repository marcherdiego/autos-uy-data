"""Corrida diaria del catálogo, como cron de Vercel.

Reemplaza al workflow de GitHub Actions, que algunos días no corría. Hace lo
mismo que `python3 tools/scrape_catalog.py`, pero sin checkout: lee
`catalog.json`, `changelog.json` y `overrides.json` de `main` por la API de
GitHub, corre el scraper y, si el catálogo cambió, commitea los dos archivos en
un solo commit. Cada corrida (con o sin cambios, o fallida) queda anotada en
`runs.json` de la rama `corridas`, que es lo que leen el panel y el vigía de
Cargadores UY. Si falla, avisa por ntfy.

    GET /api/cron/catalogo              el cron de Vercel (Authorization: Bearer $CRON_SECRET)
    GET /api/cron/catalogo?dry=1        corre todo menos los commits

Variables: CRON_SECRET, GITHUB_TOKEN (fine-grained, Contents read/write sobre
este repo) y NTFY_TOPIC.
"""

import datetime
import hmac
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import scrape_catalog  # noqa: E402

REPO = "marcherdiego/autos-uy-data"
BRANCH = "main"
RUNS_BRANCH = "corridas"
RUNS_FILE = "runs.json"
RUNS_MAX = 90
API = "https://api.github.com"
AUTHOR = {"name": "autos-uy-cron", "email": "autos-uy-cron@users.noreply.github.com"}


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None, accept="application/vnd.github+json"):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "User-Agent": "autos-uy-cron",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(request, timeout=30, context=scrape_catalog.tls_context()) as response:
            raw = response.read()
        if accept.endswith(".raw"):
            return raw.decode("utf-8")
        return json.loads(raw) if raw else None

    def head(self, branch):
        return self.request("GET", f"/repos/{REPO}/git/ref/heads/{branch}")["object"]["sha"]

    def raw_file(self, path, ref, default=None):
        try:
            return self.request("GET", f"/repos/{REPO}/contents/{path}?ref={ref}",
                                accept="application/vnd.github.raw")
        except urllib.error.HTTPError as error:
            if error.code == 404 and default is not None:
                return default
            raise

    def commit(self, branch, parent, files, message):
        """Un commit con `files` ({ruta: texto}) sobre `parent`. Devuelve el sha."""
        base_tree = self.request("GET", f"/repos/{REPO}/git/commits/{parent}")["tree"]["sha"]
        tree = []
        for path, text in files.items():
            blob = self.request("POST", f"/repos/{REPO}/git/blobs",
                                {"content": text, "encoding": "utf-8"})
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree_sha = self.request("POST", f"/repos/{REPO}/git/trees",
                                {"base_tree": base_tree, "tree": tree})["sha"]
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        sha = self.request("POST", f"/repos/{REPO}/git/commits", {
            "message": message, "tree": tree_sha, "parents": [parent],
            "author": {**AUTHOR, "date": now},
        })["sha"]
        # Sin force: si alguien pusheó en el medio (publicar_fichas.py), la
        # actualización falla y la corrida se reintenta sobre el main nuevo.
        self.request("PATCH", f"/repos/{REPO}/git/refs/heads/{branch}", {"sha": sha, "force": False})
        return sha


def publish(github, run_id, dry):
    """Corre el scraper contra `main` y commitea si cambió. Devuelve el resultado."""
    html = scrape_catalog.fetch_html()
    for attempt in range(2):
        head = github.head(BRANCH)
        previous = json.loads(github.raw_file("catalog.json", head))
        overrides = json.loads(github.raw_file("overrides.json", head, default="{}"))
        entries = json.loads(github.raw_file("changelog.json", head, default='{"entries":[]}'))["entries"]
        catalog, new_entries, summary, changed = scrape_catalog.run(
            html, previous, overrides, entries, run_id=run_id)
        result = {"changed": changed, "dataVersion": catalog["dataVersion"],
                  "previousVersion": previous.get("dataVersion"), "summary": summary, "commit": None}
        if not changed or dry:
            return result
        files = {"catalog.json": scrape_catalog.catalog_text(catalog),
                 "changelog.json": scrape_catalog.changelog_text(new_entries)}
        try:
            result["commit"] = github.commit(
                BRANCH, head, files, f"Catálogo {catalog['dataVersion']}\n\n{summary}")
            return result
        except urllib.error.HTTPError as error:
            if error.code != 422 or attempt == 1:
                raise
    raise RuntimeError("no se pudo commitear")


def record_run(github, run):
    """Agrega la corrida a runs.json de la rama `corridas` (la crea si falta)."""
    try:
        head = github.head(RUNS_BRANCH)
        runs = json.loads(github.raw_file(RUNS_FILE, head, default='{"runs":[]}'))["runs"]
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        head, runs = None, []
    text = json.dumps({"runs": ([run] + runs)[:RUNS_MAX]}, ensure_ascii=False, indent=1) + "\n"
    message = f"Corrida {run['id']}: {run['conclusion']}"
    if head:
        github.commit(RUNS_BRANCH, head, {RUNS_FILE: text}, message)
        return
    # Primera vez: una rama huérfana que sólo tiene runs.json.
    blob = github.request("POST", f"/repos/{REPO}/git/blobs", {"content": text, "encoding": "utf-8"})
    tree = github.request("POST", f"/repos/{REPO}/git/trees", {"tree": [
        {"path": RUNS_FILE, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
    sha = github.request("POST", f"/repos/{REPO}/git/commits",
                         {"message": message, "tree": tree["sha"], "parents": [], "author": AUTHOR})["sha"]
    github.request("POST", f"/repos/{REPO}/git/refs", {"ref": f"refs/heads/{RUNS_BRANCH}", "sha": sha})


def alert(title, message):
    """Push por ntfy. Nunca tira: una alerta que falla no debe tapar el error real.

    Se publica como JSON (no con headers) para que el título pueda llevar tildes.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        body = {"topic": topic, "title": title, "message": message, "priority": 4,
                "tags": ["warning", "car"], "click": "https://autos-uy-panel.vercel.app"}
        request = urllib.request.Request(
            "https://ntfy.sh/", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=10, context=scrape_catalog.tls_context()).read()
    except Exception as error:  # noqa: BLE001
        print(f"no se pudo mandar la alerta: {error}")


def authorized(header):
    secret = os.environ.get("CRON_SECRET")
    return bool(secret) and hmac.compare_digest(header or "", f"Bearer {secret}")


class handler(BaseHTTPRequestHandler):  # noqa: N801 (nombre que exige Vercel)
    def do_GET(self):  # noqa: N802
        if not authorized(self.headers.get("Authorization")):
            return self.reply(401, {"error": "No autorizado"})
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        # Una corrida dry es una prueba a mano: no alerta ni anota nada.
        dry = query.get("dry") == ["1"]
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            if not dry:
                alert("Autos UY: el catálogo no corrió", "Falta la variable GITHUB_TOKEN en Vercel.")
            return self.reply(503, {"error": "Falta GITHUB_TOKEN"})

        started = time.time()
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        github = GitHub(token)
        run = {"id": run_id, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "event": ("retry" if query.get("retry") == ["1"]
                         else "manual" if query.get("manual") == ["1"] else "schedule")}
        try:
            result = publish(github, run_id, dry)
            run.update(conclusion="success", **result)
            status = 200
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            reason = str(error)
            if isinstance(error, scrape_catalog.PageChanged):
                reason = f"La página de precios cambió de estructura: {error}"
            run.update(conclusion="failure", error=reason[:500])
            # Un 429 de Autoblog en la corrida de las 11 es un freno pasajero de Google
            # a la IP de Vercel: el vigía la relanza a las 13 UTC y alerta él si vuelve
            # a fallar. Cualquier otra falla, o el reintento mismo, avisa ya.
            transient = (isinstance(error, urllib.error.HTTPError) and error.code == 429
                         and run["event"] == "schedule")
            if transient:
                run["retryPending"] = True
            if not dry and not transient:
                alert("Autos UY: falló la actualización del catálogo", reason[:300])
            status = 500
        run["durationMs"] = int((time.time() - started) * 1000)
        if dry:
            return self.reply(status, run)
        try:
            record_run(github, run)
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            run["recordError"] = str(error)[:300]
        self.reply(status, run)

    def reply(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
