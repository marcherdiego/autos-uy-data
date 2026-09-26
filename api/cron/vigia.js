// Vigía de Cargadores UY: una vez por día mira que el sync de UTE de
// cargadores-server haya corrido, y si no, avisa por ntfy.
//
// Vive acá, y no en cargadores-server, a propósito: si los crons de ese
// proyecto dejan de correr, un vigía en el mismo proyecto tampoco correría.
// cargadores-server tiene el vigía espejo, que mira el catálogo de este repo.
//
// GET /api/cron/vigia (cron de Vercel, Authorization: Bearer $CRON_SECRET).
// Variables: CRON_SECRET y NTFY_TOPIC.
import { timingSafeEqual } from "node:crypto";

const HEALTH_URL = "https://cargadores-server.vercel.app/api/health";
const RUNS_URL = "https://raw.githubusercontent.com/marcherdiego/autos-uy-data/corridas/runs.json";
const CATALOG_RETRY_URL = "https://autos-uy-panel.vercel.app/api/cron/catalogo?retry=1";
const ADMIN_URL = "https://cargadores-server.vercel.app/admin";
/** El sync corre a diario: 26 h deja margen para que Vercel lo largue tarde. */
export const MAX_SYNC_AGE_MIN = 26 * 60;

function safeEqual(a, b) {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}

/** Qué anda mal según /api/health (null si está todo bien). */
export function problemOf(health) {
  if (!health || health.ok !== true) return "El health de cargadores-server no responde ok.";
  if (health.lastUteSyncAgeMin == null) return "El health no sabe cuándo fue el último sync de UTE (¿la base no responde?).";
  if (health.lastUteSyncAgeMin > MAX_SYNC_AGE_MIN) {
    const hours = Math.round(health.lastUteSyncAgeMin / 60);
    return `El último sync de UTE fue hace ${hours} h (${health.lastUteSyncAt}).`;
  }
  return null;
}

async function alert(title, message) {
  const topic = process.env.NTFY_TOPIC;
  if (!topic) return;
  try {
    // JSON y no headers: el título lleva tildes.
    await fetch("https://ntfy.sh/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, title, message, priority: 4, tags: ["warning", "electric_plug"], click: ADMIN_URL }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch (error) {
    console.error("no se pudo mandar la alerta", error);
  }
}

/**
 * Si la corrida del catálogo de hoy falló con un freno pasajero (429 de Autoblog,
 * marcada `retryPending`), hay que relanzarla. Las corridas vienen de la más nueva
 * a la más vieja.
 */
export function catalogRunToRetry(runs, now = new Date()) {
  const last = runs?.[0];
  if (!last || last.conclusion !== "failure" || !last.retryPending) return null;
  const sameDay = last.at?.slice(0, 10) === now.toISOString().slice(0, 10);
  return sameDay ? last : null;
}

/** Relanza el catálogo; si vuelve a fallar, la corrida `retry` alerta ella misma. */
async function retryCatalogIfNeeded(secret) {
  try {
    const r = await fetch(RUNS_URL, { signal: AbortSignal.timeout(15_000), cache: "no-store" });
    const runs = r.ok ? (await r.json()).runs : null;
    const pending = catalogRunToRetry(runs);
    if (!pending) return null;
    const retry = await fetch(CATALOG_RETRY_URL, {
      headers: { Authorization: `Bearer ${secret}` },
      signal: AbortSignal.timeout(55_000),
    });
    const run = await retry.json().catch(() => ({}));
    return { retried: pending.id, conclusion: run.conclusion ?? `HTTP ${retry.status}` };
  } catch (error) {
    await alert("Autos UY: no se pudo reintentar el catálogo", String(error.message ?? error));
    return { retried: null, error: String(error.message ?? error) };
  }
}

export default async function handler(req, res) {
  const secret = process.env.CRON_SECRET;
  const provided = req.headers.authorization;
  if (!secret || typeof provided !== "string" || !safeEqual(provided, `Bearer ${secret}`)) {
    return res.status(401).json({ error: "No autorizado" });
  }

  let problem;
  let health = null;
  try {
    const r = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(15_000) });
    health = r.ok ? await r.json() : null;
    problem = r.ok ? problemOf(health) : `/api/health respondió ${r.status}.`;
  } catch (error) {
    problem = `No se pudo consultar /api/health: ${error.message ?? error}`;
  }

  if (problem) await alert("Cargadores UY: el sync de UTE no está corriendo", problem);
  const catalogRetry = await retryCatalogIfNeeded(secret);
  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({ ok: !problem, problem, health, catalogRetry });
}
