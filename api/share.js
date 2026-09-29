// Páginas de lo que se comparte desde la app: una versión, una marca o una
// comparativa entre dos versiones.
//
//   /auto/<carId>            → ficha de una versión
//   /marca/<brandId>         → los modelos de una marca con su precio de entrada
//   /comparar/<carId>/<carId> → dos versiones lado a lado
//
// Son App Links (Android) y Universal Links (iOS), verificados con los archivos de
// public/.well-known: con Autos UY instalada, el sistema abre la app directo en esa
// pantalla y esta página no llega a verse. Sin la app, se ve la misma información
// (así el link sirve igual en un chat) con los botones para abrirla o bajarla.
//
// Los datos salen del catalog.json publicado en GitHub, el mismo que lee la app.
// vercel.json reescribe las tres rutas a esta función.

const CATALOG_URL = "https://raw.githubusercontent.com/marcherdiego/autos-uy-data/main/catalog.json";
const APP_STORE = "https://apps.apple.com/app/id6802848048";
const PLAY_STORE = "https://play.google.com/store/apps/details?id=com.awesome.apps.autoblog";
const PACKAGE = "com.awesome.apps.autoblog";
// Esquema propio de la app: el botón "Abrir en la app" de la página lo usa porque
// un Universal Link tocado desde su propio dominio no abre la app en iOS.
const SCHEME = "autosuy";

// El catálogo pesa ~2 MB: se guarda entre invocaciones de la misma instancia.
const CATALOG_TTL_MS = 10 * 60 * 1000;
let cached = null;

async function catalog() {
  if (cached && Date.now() - cached.at < CATALOG_TTL_MS) return cached.index;
  const r = await fetch(CATALOG_URL, { signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error(`catalog.json respondió ${r.status}`);
  const data = await r.json();
  const brands = new Map(data.brands.map((b) => [b.id, b]));
  const cars = new Map(data.cars.map((c) => [c.id, c]));
  const modelOf = new Map();
  for (const m of data.models) for (const v of m.versionIds) modelOf.set(v, m);
  const index = { data, brands, cars, modelOf };
  cached = { at: Date.now(), index };
  return index;
}

// ------------------------------------------------------------------ formato

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/** 23645 → "US$ 23.645", igual que en la app. */
const usd = (n) => `US$ ${Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".")}`;

/** 42.4 → "42,4 kWh" */
const battery = (kwh) => (kwh == null ? null : `${String(Math.round(kwh * 10) / 10).replace(".", ",")} kWh`);

const fullName = (ix, car) => `${ix.brands.get(car.brandId)?.name ?? ""} ${car.name}`.trim();

/** Los datos técnicos de una versión: los suyos, y lo que falte, los del modelo. */
function specs(ix, car) {
  const model = ix.modelOf.get(car.id) ?? {};
  const own = model.versionSpecs?.[car.id] ?? {};
  const pick = (k) => own[k] ?? model[k] ?? null;
  return {
    engine: pick("engine"),
    power: pick("power"),
    torque: pick("torque"),
    transmission: pick("transmission"),
    traction: pick("traction"),
    battery: car.batteryKwh != null ? battery(car.batteryKwh) : null,
    range: car.batteryKwh != null ? pick("range") : null,
    performance: pick("performance"),
    consumption: pick("consumption"),
  };
}

const SPEC_ROWS = [
  ["Motor", "engine"],
  ["Potencia", "power"],
  ["Torque", "torque"],
  ["Transmisión", "transmission"],
  ["Tracción", "traction"],
  ["Batería", "battery"],
  ["Autonomía", "range"],
  ["Prestaciones", "performance"],
  ["Consumo", "consumption"],
];

// ------------------------------------------------------------------ páginas

function carPage(ix, id) {
  const car = ix.cars.get(id);
  if (!car) return null;
  const model = ix.modelOf.get(id);
  const s = specs(ix, car);
  const name = fullName(ix, car);
  const rows = SPEC_ROWS.filter(([, k]) => s[k]).map(
    ([label, k]) => `<tr><th>${esc(label)}</th><td>${esc(s[k])}</td></tr>`,
  );
  const siblings = (model?.versionIds ?? [])
    .filter((v) => v !== id && ix.cars.has(v))
    .map((v) => ix.cars.get(v))
    .sort((a, b) => a.priceUsd - b.priceUsd);
  const highlights = [s.power, s.range, s.traction].filter(Boolean).join(" · ");
  return {
    title: `${name} — ${usd(car.priceUsd)}`,
    description: `${usd(car.priceUsd)} · ${car.fuel}${highlights ? ` · ${highlights}` : ""}. Precio 0km en Uruguay.`,
    image: ix.brands.get(car.brandId)?.logoUrl,
    appPath: `auto/${id}`,
    body: `
      <p class="eyebrow"><a href="/marca/${esc(car.brandId)}">${esc(ix.brands.get(car.brandId)?.name)}</a>${model?.bodyType ? ` · ${esc(model.bodyType)}` : ""}</p>
      <h1>${esc(name)}</h1>
      <p class="price">${esc(usd(car.priceUsd))}</p>
      <p class="chips"><span>${esc(car.fuel)}</span>${car.category ? `<span>${esc(car.category)}</span>` : ""}</p>
      ${model?.summary ? `<p class="summary">${esc(model.summary)}</p>` : ""}
      ${rows.length ? `<table class="specs">${rows.join("")}</table>` : ""}
      ${siblings.length ? `<h2>Otras versiones de ${esc(model.model)}</h2><ul class="list">${siblings
        .map((c) => `<li><a href="/auto/${esc(c.id)}">${esc(c.name)}</a><span>${esc(usd(c.priceUsd))}</span></li>`)
        .join("")}</ul>` : ""}`,
  };
}

function brandPage(ix, id) {
  const brand = ix.brands.get(id);
  if (!brand) return null;
  const cars = ix.data.cars.filter((c) => c.brandId === id);
  if (!cars.length) return null;
  // Un renglón por modelo, con su versión más barata: el mismo resumen que el texto
  // que comparte la app.
  const groups = new Map();
  for (const c of cars) {
    const key = ix.modelOf.get(c.id)?.model ?? c.name;
    const g = groups.get(key) ?? [];
    g.push(c);
    groups.set(key, g);
  }
  const models = [...groups.entries()]
    .map(([name, list]) => ({ name, list: list.sort((a, b) => a.priceUsd - b.priceUsd) }))
    .sort((a, b) => a.name.localeCompare(b.name, "es", { sensitivity: "base" }));
  const min = Math.min(...cars.map((c) => c.priceUsd));
  const max = Math.max(...cars.map((c) => c.priceUsd));
  const count = `${cars.length} ${cars.length === 1 ? "versión" : "versiones"}`;
  return {
    title: `${brand.name} 0km en Uruguay`,
    description: `${count}, de ${usd(min)} a ${usd(max)}: ${models.map((m) => m.name).join(", ")}.`,
    image: brand.logoUrl,
    appPath: `marca/${id}`,
    body: `
      ${brand.logoUrl ? `<img class="logo" src="${esc(brand.logoUrl)}" alt="">` : ""}
      <h1>${esc(brand.name)} 0km</h1>
      <p class="lede">${esc(count)}, de ${esc(usd(min))} a ${esc(usd(max))}</p>
      <ul class="list">${models
        .map(({ name, list }) => `<li><a href="/auto/${esc(list[0].id)}">${esc(name)}</a><span>desde ${esc(usd(list[0].priceUsd))}</span></li>`)
        .join("")}</ul>`,
  };
}

function comparePage(ix, a, b) {
  const left = ix.cars.get(a);
  const right = ix.cars.get(b);
  if (!left || !right) return null;
  const sl = specs(ix, left);
  const sr = specs(ix, right);
  const rows = [
    ["Precio", usd(left.priceUsd), usd(right.priceUsd)],
    ["Combustible", left.fuel, right.fuel],
    ...SPEC_ROWS.map(([label, k]) => [label, sl[k], sr[k]]),
  ].filter(([, x, y]) => x || y);
  const nl = fullName(ix, left);
  const nr = fullName(ix, right);
  return {
    title: `${nl} vs. ${nr}`,
    description: `${usd(left.priceUsd)} vs. ${usd(right.priceUsd)}. Comparativa de precio y ficha técnica, 0km en Uruguay.`,
    image: ix.brands.get(left.brandId)?.logoUrl,
    appPath: `comparar/${a}/${b}`,
    body: `
      <p class="eyebrow">Comparativa</p>
      <h1>${esc(nl)} <span class="vs">vs.</span> ${esc(nr)}</h1>
      <table class="compare">
        <thead><tr><th></th><th><a href="/auto/${esc(a)}">${esc(nl)}</a></th><th><a href="/auto/${esc(b)}">${esc(nr)}</a></th></tr></thead>
        <tbody>${rows
          .map(([label, x, y]) => `<tr><th>${esc(label)}</th><td>${esc(x ?? "—")}</td><td>${esc(y ?? "—")}</td></tr>`)
          .join("")}</tbody>
      </table>`,
  };
}

function notFound() {
  return {
    title: "Autos UY",
    description: "Precios de autos 0km en Uruguay, con la ficha técnica de cada versión.",
    appPath: "",
    status: 404,
    body: `
      <h1>Esto ya no está en el catálogo</h1>
      <p class="lede">Los precios de lista cambian seguido y esta versión salió de la lista. En la app está el catálogo completo y al día.</p>`,
  };
}

// ------------------------------------------------------------------ html

function openAppHref(ua, appPath) {
  if (/android/i.test(ua)) {
    const fallback = encodeURIComponent(PLAY_STORE);
    return `intent://${appPath}#Intent;scheme=${SCHEME};package=${PACKAGE};S.browser_fallback_url=${fallback};end`;
  }
  return `${SCHEME}://${appPath}`;
}

function html(page, url, ua) {
  const isAndroid = /android/i.test(ua);
  const isIos = /iphone|ipad|ipod/i.test(ua);
  const stores = [
    !isAndroid && `<a class="store" href="${APP_STORE}">App Store</a>`,
    !isIos && `<a class="store" href="${PLAY_STORE}">Google Play</a>`,
  ].filter(Boolean);
  const canOpen = page.appPath !== "" && (isAndroid || isIos);
  return `<!doctype html>
<html lang="es-UY">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(page.title === "Autos UY" ? page.title : `${page.title} · Autos UY`)}</title>
<meta name="description" content="${esc(page.description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Autos UY">
<meta property="og:title" content="${esc(page.title)}">
<meta property="og:description" content="${esc(page.description)}">
<meta property="og:url" content="${esc(url)}">
${page.image ? `<meta property="og:image" content="${esc(page.image)}">` : ""}
<meta name="twitter:card" content="summary">
<meta name="apple-itunes-app" content="app-id=6802848048${page.appPath ? `, app-argument=${esc(url)}` : ""}">
<meta name="theme-color" content="#0F2238">
<style>
  :root { --bg:#F5F7F9; --card:#fff; --ink:#0F2238; --muted:#56657A; --line:#E1E6EC; --accent:#2B6A9C; --soft:#E6F0F9; --dot:#7FB2DC; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0B1522; --card:#132235; --ink:#E8EEF5; --muted:#9AAABD; --line:#22344B; --accent:#7FB2DC; --soft:#1B3048; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { background:#0F2238; color:#fff; padding:14px 16px; }
  .brand { font-weight:700; letter-spacing:.2px; }
  .brand::after { content:""; display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--dot); margin-left:3px; }
  main { max-width:760px; margin:0 auto; padding:20px 16px 40px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:20px; }
  h1 { font-size:1.6rem; line-height:1.2; margin:.2rem 0 .4rem; }
  h2 { font-size:1.05rem; margin:1.6rem 0 .6rem; }
  a { color:var(--accent); }
  .eyebrow { margin:0; color:var(--muted); font-size:.9rem; }
  .eyebrow a { color:inherit; }
  .price { font-size:1.5rem; font-weight:700; margin:.2rem 0; }
  .lede, .summary { color:var(--muted); }
  .chips span { display:inline-block; background:var(--soft); border-radius:999px; padding:2px 10px; margin:0 6px 6px 0; font-size:.85rem; }
  .vs { color:var(--muted); font-weight:400; }
  .logo { max-height:48px; max-width:140px; object-fit:contain; background:#fff; border-radius:8px; padding:4px; }
  table { width:100%; border-collapse:collapse; margin-top:12px; }
  th, td { text-align:left; vertical-align:top; padding:9px 8px; border-top:1px solid var(--line); font-size:.95rem; }
  th { color:var(--muted); font-weight:500; }
  .specs th { width:36%; }
  .compare { table-layout:fixed; }
  .compare th:first-child { width:26%; }
  .compare thead th { color:var(--ink); font-weight:700; border-top:none; }
  .compare thead a { color:inherit; text-decoration:none; }
  .list { list-style:none; padding:0; margin:12px 0 0; }
  .list li { display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-top:1px solid var(--line); }
  .list li span { color:var(--muted); white-space:nowrap; }
  .actions { display:flex; flex-wrap:wrap; gap:10px; margin:20px 0 0; }
  .open, .store { display:inline-block; border-radius:12px; padding:12px 18px; font-weight:600; text-decoration:none; }
  .open { background:var(--accent); color:#fff; }
  @media (prefers-color-scheme: dark) { .open { color:#0B1522; } }
  .store { border:1px solid var(--line); color:var(--ink); background:var(--card); }
  footer { color:var(--muted); font-size:.8rem; margin-top:20px; }
</style>
</head>
<body>
<header><span class="brand">Autos UY</span></header>
<main>
  <div class="card">${page.body}</div>
  <div class="actions">
    ${canOpen ? `<a class="open" href="${esc(openAppHref(ua, page.appPath))}">Abrir en Autos UY</a>` : ""}
    ${stores.join("\n    ")}
  </div>
  <footer>Precios de lista en dólares, publicados por cada importador; pueden cambiar sin aviso. Confirmalos con el importador antes de decidir.</footer>
</main>
</body>
</html>`;
}

// Ids del catálogo: minúsculas, dígitos, guiones, guiones bajos y puntos.
const ID_RE = /^[a-z0-9_.-]{1,120}$/;

export default async function handler(req, res) {
  const { t, a, b } = req.query;
  const ua = req.headers["user-agent"] ?? "";
  const url = `https://${req.headers.host}${t === "comparar" ? `/comparar/${a}/${b}` : `/${t}/${a}`}`;
  let page = null;
  try {
    if ([a, b].every((x) => x === undefined || ID_RE.test(x))) {
      const ix = await catalog();
      if (t === "auto") page = carPage(ix, a);
      else if (t === "marca") page = brandPage(ix, a);
      else if (t === "comparar" && b) page = comparePage(ix, a, b);
    }
  } catch (e) {
    console.error("share", e);
    res.status(503).setHeader("Cache-Control", "no-store");
    return res.send(html({ ...notFound(), body: "<h1>No pudimos cargar el catálogo</h1><p class=\"lede\">Probá de nuevo en un rato, o abrilo en la app.</p>" }, url, ua));
  }
  page ??= notFound();
  res.status(page.status ?? 200);
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  // Varía por User-Agent: el botón para abrir la app es distinto en Android y en iOS.
  res.setHeader("Vary", "User-Agent");
  res.setHeader("Cache-Control", "public, s-maxage=3600, stale-while-revalidate=86400");
  res.send(html(page, url, ua));
}
