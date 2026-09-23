// Datos del panel de monitoreo (public/index.html): las últimas corridas del
// job que actualiza el catálogo y el changelog que ese job deja en el repo.
//
// Todo lo que devuelve es público (el repo lo es); la contraseña está para que
// el panel no quede abierto a cualquiera, igual que los de Celimap y Cargadores.
// Sin ADMIN_SECRET responde 503: falla cerrado.
//
// GITHUB_TOKEN es opcional. Sin token la API de GitHub da 60 pedidos por hora
// por IP, y las IPs de salida de Vercel son compartidas: si se agotan, la
// respuesta trae `runsError` y el panel pide las corridas desde el navegador.
import { timingSafeEqual } from "node:crypto";

const REPO = "marcherdiego/autos-uy-data";
const WORKFLOW = "actualizar-catalogo.yml";
const CHANGELOG_URL = `https://raw.githubusercontent.com/${REPO}/main/changelog.json`;

function safeEqual(a, b) {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}

async function fetchRuns() {
  const headers = { Accept: "application/vnd.github+json", "User-Agent": "autos-uy-panel" };
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  const r = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=60`,
    { headers, signal: AbortSignal.timeout(10_000) },
  );
  if (!r.ok) throw new Error(`GitHub respondió ${r.status}`);
  const data = await r.json();
  return data.workflow_runs.map(simplifyRun);
}

export function simplifyRun(run) {
  return {
    id: String(run.id),
    status: run.status,
    conclusion: run.conclusion,
    event: run.event,
    createdAt: run.created_at,
    updatedAt: run.updated_at,
    startedAt: run.run_started_at,
    url: run.html_url,
  };
}

async function fetchChangelog() {
  // `?t=` saltea la caché de raw.githubusercontent (~5 min) después de un commit.
  const r = await fetch(`${CHANGELOG_URL}?t=${Date.now()}`, { signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error(`changelog.json respondió ${r.status}`);
  return (await r.json()).entries;
}

export default async function handler(req, res) {
  const secret = process.env.ADMIN_SECRET;
  if (!secret) return res.status(503).json({ error: "Panel deshabilitado: falta ADMIN_SECRET" });
  const provided = req.headers["x-admin-secret"];
  if (typeof provided !== "string" || !safeEqual(provided, secret)) {
    return res.status(401).json({ error: "No autorizado" });
  }

  const [runs, changelog] = await Promise.allSettled([fetchRuns(), fetchChangelog()]);
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({
    repo: REPO,
    workflow: WORKFLOW,
    fetchedAt: new Date().toISOString(),
    runs: runs.status === "fulfilled" ? runs.value : null,
    runsError: runs.status === "rejected" ? String(runs.reason?.message ?? runs.reason) : null,
    entries: changelog.status === "fulfilled" ? changelog.value : null,
    entriesError: changelog.status === "rejected" ? String(changelog.reason?.message ?? changelog.reason) : null,
  });
}
