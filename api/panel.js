// Datos del panel de monitoreo (public/index.html): las últimas corridas del
// cron que actualiza el catálogo y el changelog que ese cron deja en el repo.
//
// Las corridas salen de runs.json en la rama `corridas`, que escribe
// api/cron/catalogo.py en cada corrida (con o sin cambios, o fallida).
//
// Todo lo que devuelve es público (el repo lo es); la contraseña está para que
// el panel no quede abierto a cualquiera, igual que los de Celimap y Cargadores.
// Sin ADMIN_SECRET responde 503: falla cerrado.
import { timingSafeEqual } from "node:crypto";

const REPO = "marcherdiego/autos-uy-data";
const RUNS_URL = `https://raw.githubusercontent.com/${REPO}/corridas/runs.json`;
const CHANGELOG_URL = `https://raw.githubusercontent.com/${REPO}/main/changelog.json`;

function safeEqual(a, b) {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}

async function fetchRuns() {
  // `?t=` saltea la caché de raw.githubusercontent (~5 min) después de un commit.
  const r = await fetch(`${RUNS_URL}?t=${Date.now()}`, { signal: AbortSignal.timeout(10_000) });
  // Sin la rama todavía (antes de la primera corrida) no hay corridas, no es un error.
  if (r.status === 404) return [];
  if (!r.ok) throw new Error(`runs.json respondió ${r.status}`);
  return (await r.json()).runs.map(simplifyRun);
}

/** Una corrida de runs.json, con los campos que el panel ya sabía mostrar. */
export function simplifyRun(run) {
  const finished = new Date(new Date(run.at).getTime() + (run.durationMs ?? 0)).toISOString();
  return {
    id: run.id,
    status: "completed",
    conclusion: run.conclusion,
    event: run.event,
    createdAt: run.at,
    startedAt: run.at,
    updatedAt: finished,
    url: run.commit ? `https://github.com/${REPO}/commit/${run.commit}` : null,
    changed: Boolean(run.changed),
    error: run.error ?? null,
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
    fetchedAt: new Date().toISOString(),
    runs: runs.status === "fulfilled" ? runs.value : null,
    runsError: runs.status === "rejected" ? String(runs.reason?.message ?? runs.reason) : null,
    entries: changelog.status === "fulfilled" ? changelog.value : null,
    entriesError: changelog.status === "rejected" ? String(changelog.reason?.message ?? changelog.reason) : null,
  });
}
