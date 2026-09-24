#!/usr/bin/env python3
"""
Regenera el catálogo de autos 0km desde la página de precios de Autoblog.

    python3 tools/scrape_catalog.py [--dry-run]

Escribe `catalog.json` en la raíz del repo: marcas, importadores y versiones con
su precio. Es el archivo que consume la app Autos UY.

La corrida diaria no usa este `main()`: la hace el cron de Vercel
(`api/cron/catalogo.py`), que llama a `run()` con los archivos leídos de GitHub
y commitea el resultado por la API. `main()` es la misma corrida contra el
repo local.

Sobre el parseo: la página ("PRECIOS 0 KM REDISEÑADO V1") trae cada marca en un
`<section class="ab-price-brand" data-brand="...">`, con el importador en
`.ab-price-meta` y las versiones en `.ab-price-models li`. La versión anterior de
la página no nombraba las marcas —había que deducirlas del orden de los logos— y
por eso el scraper viejo se rompía cuando entraba una marca nueva; acá el nombre
viene en el HTML, así que una marca nueva entra sola.

Las fichas de cada modelo (`models`) NO se tocan: las arma otro proceso y este
script las conserva del catálogo anterior, descartando las versiones que ya no
están en la lista de precios.

Requiere: beautifulsoup4.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.request

from bs4 import BeautifulSoup

URL = "https://www.autoblog.com.uy/p/precios-0km.html"

# La página escribe algunas marcas distinto de como venían en el catálogo. Sin
# esta tabla cambiarían de id y la app las leería como marcas nuevas: perderían
# sus fichas y sus favoritos. Las dos Dongfeng son dos importadores de la misma
# marca (así estaban antes: una marca, dos importadores).
BRAND_ALIASES = {
    "GAC": "GAC Motor",
    "OMODA/JAECOO": "Omoda & Jaecoo",
    "DONGFENG (GRUPO FIDOCAR)": "Dongfeng",
    "DONGFENG (GRUPO BARRIOLA)": "Dongfeng",
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog.json")
# Lo que la app sabe mejor que el nombre de la versión: el combustible que
# relevó para cada enchufable (un ROX de autonomía extendida no lo dice en el
# nombre) y la carrocería de la ficha (pick-up, utilitario). Lo escribe
# tools/publicar_fichas.py del repo de la app; acá sólo se aplica.
OVERRIDES = os.path.join(ROOT, "overrides.json")

PICKUP_RE = re.compile(r"(?<![\w-])(?:pick[- ]?up|cabina (?:simple|doble|plus)|crew cab|"
                       r"doble cabina)(?![\w-])", re.I)
UTILITY_RE = re.compile(
    r"(?<![\w-])(?:cargo|mini ?bus|bus|chasis|truck|reefer|panel|utilitario|box(?:er)?|"
    r"furg[oó]n|van)(?![\w-])",
    re.I,
)


def category_of(name):
    if UTILITY_RE.search(name):
        return "Utilitario"
    if PICKUP_RE.search(name):
        return "Pick-up"
    return "Auto / SUV"


class PageChanged(Exception):
    """La página de precios no tiene la estructura que el parser espera."""


def tls_context():
    # El Python de python.org en macOS no trae certificados raíz hasta correr
    # "Install Certificates.command": ahí se usan los del sistema.
    context = ssl.create_default_context()
    if not context.cert_store_stats()["x509_ca"] and os.path.exists("/etc/ssl/cert.pem"):
        context.load_verify_locations("/etc/ssl/cert.pem")
    return context


def fetch_html():
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30, context=tls_context()) as response:
        return response.read().decode("utf-8", "replace")


def slug(name):
    ascii_name = unicodedata.normalize("NFKD", name.replace("&", " and "))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")


# Marcas de diésel que aparecen como palabra suelta en el nombre de la versión.
DIESEL_RE = re.compile(
    r"(?:turbo)?d[ií]e?sel"
    r"|(?<![\w-])(?:tdi|d-4d|d4d|crdi|tdci|ctdi|ddtti|ddti|dtti|hdi|bluehdi|"
    r"td4|jtd|multijet|cdi|dci|cti|vgt|isf|cummins|duramax|dld|tdd|sit|bit)(?![\w-])"
    r"|(?<![\w-])\d[.,]\d\s?(?:td|d)(?![\w-])",
    re.I,
)
# Utilitarios que sólo se ofrecen con motor diésel y no lo dicen en el nombre.
DIESEL_ONLY = ("himla", "daily", "sunray", "toano", "view cargo", "view traveller")


def fuel_of(name):
    raw, low = name, name.lower()
    ev = "kwh" in low
    battery = battery_of(name) or 0
    # "330e" / "350 e" es un enchufable alemán, pero "600e (54 kWh)" es el Fiat
    # eléctrico: con una batería de eléctrico, el patrón no alcanza.
    german_phev = bool(re.search(r"\b\d{3}\s?e\b", raw)) and battery < 40
    plug = (any(k in low for k in ["phev", "dm-i", "dm-p", "dm-o", "dmo", "em-i", "em-p",
                                   "plug-in", "plugin", "e-hybrid", "ehybrid", "reev", "erev",
                                   "4xe", "e performance"])
            # "iDD" (Changan) como palabra: "Middle" también lo contiene.
            or bool(re.search(r"(?<![a-z])idd(?![a-z])", low))
            or german_phev
            or ("recharge" in low and "t8" in low))
    hybrid = any(k in low for k in [" hev", "hybrid", "híbrid", "e-power", "dht", "mhev", "shev",
                                    "e-cvt", "ecvt", " shs", "dhi", " hyb", "eq boost", "e-boxer"])
    if plug:
        return "PHEV"
    if ev and hybrid:
        return "PHEV"
    if ev:
        return "Eléctrico"
    if hybrid:
        return "Híbrido"
    if DIESEL_RE.search(name) or any(k in low for k in DIESEL_ONLY):
        return "Diésel"
    return "Nafta"


def battery_of(name):
    match = re.search(r"\(\s*([\d]+(?:[\.,][\d]+)?)\s*kWh\s*\)", name, re.I)
    return float(match.group(1).replace(",", ".")) if match else None



def brand_name(raw, previous_names):
    """La página escribe las marcas en mayúsculas ("MERCEDES-BENZ").

    El nombre bien escrito ya está en el catálogo anterior, así que se reusa; una
    marca nueva se capitaliza con una regla simple y se reporta al final, para
    poder corregirla a mano si quedó fea.
    """
    alias = BRAND_ALIASES.get(raw.upper())
    if alias:
        return alias
    known = previous_names.get(slug(raw))
    if known:
        return known
    return " ".join(
        word if len(word) <= 4 else word.capitalize()
        for word in raw.split()
    )


def meta_pairs(section):
    """Los pares etiqueta/valor del bloque del importador."""
    out = {}
    for item in section.select(".ab-price-meta > div"):
        label = item.find("span")
        value = item.find("strong")
        if label and value:
            out[label.get_text(" ", strip=True).lower()] = value.get_text(" ", strip=True)
    return out


def price_of(text):
    """"U$S 59.990" -> 59990. Devuelve (precio, nota) o (None, None)."""
    match = re.search(r"U\$S\s*([\d.]+)", text)
    if not match:
        return None, None
    price = int(match.group(1).replace(".", ""))
    note = text[match.end():].strip().lstrip("+").strip()
    return price, (note or None)


def previous_catalog():
    if not os.path.exists(CATALOG):
        return {}
    with open(CATALOG, encoding="utf-8") as handle:
        return json.load(handle)


def read_overrides():
    if not os.path.exists(OVERRIDES):
        return {}
    with open(OVERRIDES, encoding="utf-8") as handle:
        return json.load(handle)


def build_catalog(html, previous, overrides):
    body = BeautifulSoup(html, "html.parser").select_one(".post-body")
    if body is None:
        raise PageChanged("No se encontró .post-body: la página cambió de estructura.")
    sections = body.select("section.ab-price-brand[data-brand]")
    if not sections:
        raise PageChanged("No se encontró ninguna marca: la página cambió de estructura.")

    previous_names = {slug(b["name"]): b["name"] for b in previous.get("brands", [])}
    previous_logos = {b["id"]: b for b in previous.get("brands", [])}
    # Cada versión apunta a su ficha con modelKey, y ese vínculo lo arma el
    # proceso de fichas, no este. Si no se copia del catálogo anterior, la app
    # se queda sin fotos ni ficha técnica: muestra solo el importador.
    model_of = {
        version_id: model["modelKey"]
        for model in previous.get("models", [])
        for version_id in model.get("versionIds", [])
    }

    by_id, importers, cars, new_brands = {}, [], [], []
    for section in sections:
        raw = section.get("data-brand", "").strip()
        if not raw:
            continue
        name = brand_name(raw, previous_names)
        brand_id = slug(name)
        if brand_id not in previous_logos and name not in new_brands:
            new_brands.append(name)
        old = previous_logos.get(brand_id, {})
        if brand_id not in by_id:
            by_id[brand_id] = {
                "id": brand_id,
                "name": name,
                # El logo sigue siendo el que la app trae empaquetado: la página
                # rediseñada ya no publica imágenes de marca. Una marca nueva no
                # tiene logo y la app le dibuja sus iniciales.
                "logo": old.get("logo", f"logo_{brand_id}"),
                "logoUrl": old.get("logoUrl", ""),
                "sourceIndex": len(by_id),
            }

        meta = meta_pairs(section)
        importer_id = f"{brand_id}_{len(importers)}"
        importers.append({
            "id": importer_id,
            "brandId": brand_id,
            "name": meta.get("importador") or "—",
            "address": meta.get("dirección") or meta.get("direccion"),
            "phone": meta.get("teléfono") or meta.get("telefono"),
            "web": (meta.get("web") or "").replace(" ", "") or None,
            "warranty": meta.get("garantía") or meta.get("garantia"),
        })

        used_ids = set()
        for item in section.select(".ab-price-models li"):
            model_el = item.select_one(".ab-price-model")
            value_el = item.select_one(".ab-price-value")
            if not model_el or not value_el:
                continue
            version = model_el.get_text(" ", strip=True)
            price, note = price_of(value_el.get_text(" ", strip=True))
            if not version or price is None or price < 1000:
                continue
            car_id = f"{brand_id}-{slug(version)}"[:70]
            if car_id in used_ids:
                car_id = f"{car_id}-{sum(1 for i in used_ids if i.startswith(car_id)) + 1}"
            used_ids.add(car_id)
            cars.append({
                "id": car_id, "brandId": brand_id, "importerId": importer_id,
                "name": version, "priceUsd": price, "fuel": fuel_of(version),
                "batteryKwh": battery_of(version),
                "category": category_of(version),
                "note": f"+ {note}" if note else None,
                # Null en una versión nueva: hasta que tenga ficha, la app le
                # muestra precio, importador y poco más.
                "modelKey": model_of.get(car_id),
            })

    for car in cars:
        for field in ("fuel", "category"):
            value = overrides.get(field, {}).get(car["id"])
            if value:
                car[field] = value

    text = body.get_text(" ", strip=True)
    match = re.search(r"(\d{2}/\d{2}/\d{4})", re.sub(r"\s*/\s*", "/", text))
    updated = match.group(1) if match else previous.get("updatedAt", "")

    # Las fichas se conservan, sin las versiones que ya no se venden. Un modelo
    # que se quedó sin ninguna versión sale del catálogo.
    car_ids = {car["id"] for car in cars}
    models = []
    for model in previous.get("models", []):
        kept = [v for v in model.get("versionIds", []) if v in car_ids]
        if kept:
            models.append({**model, "versionIds": kept})

    catalog = {
        "source": URL, "updatedAt": updated, "currency": "USD", "dataVersion": "0" * 12,
        "brands": list(by_id.values()), "importers": importers, "cars": cars,
        "models": models,
    }
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    catalog["dataVersion"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return catalog, new_brands


# Campos de una versión que, si cambian, entran al changelog. `importerId` queda
# afuera: es posicional ("marca_<n>") y cambia cada vez que se reordena la lista.
TRACKED_FIELDS = ("name", "note", "fuel", "category", "batteryKwh", "modelKey")
IMPORTER_FIELDS = ("address", "phone", "web", "warranty")
CHANGELOG = os.path.join(ROOT, "changelog.json")
# El panel muestra lo reciente; más de esto no hace falta y el archivo crece
# con cada ola de precios.
CHANGELOG_MAX_ENTRIES = 400


def diff_catalogs(previous, catalog):
    """Qué cambió respecto del catálogo anterior, estructurado para el changelog."""
    names = {b["id"]: b["name"] for b in previous.get("brands", [])}
    names.update({b["id"]: b["name"] for b in catalog["brands"]})

    def brief(car):
        return {"id": car["id"], "brand": names.get(car["brandId"], car["brandId"]),
                "name": car["name"], "priceUsd": car["priceUsd"]}

    old_cars = {c["id"]: c for c in previous.get("cars", [])}
    new_cars = {c["id"]: c for c in catalog["cars"]}
    shared = sorted(new_cars.keys() & old_cars.keys())

    changed = []
    for i in shared:
        fields = {f: [old_cars[i].get(f), new_cars[i].get(f)]
                  for f in TRACKED_FIELDS if old_cars[i].get(f) != new_cars[i].get(f)}
        if fields:
            changed.append({**brief(new_cars[i]), "fields": fields})

    # Los importadores se emparejan por (marca, nombre): su id es posicional.
    def importer_key(imp):
        return (imp["brandId"], imp["name"])

    old_imps = {importer_key(i): i for i in previous.get("importers", [])}
    new_imps = {importer_key(i): i for i in catalog["importers"]}
    importers = []
    for key in sorted(new_imps.keys() | old_imps.keys()):
        brand = names.get(key[0], key[0])
        if key not in old_imps:
            importers.append({"brand": brand, "name": key[1], "status": "added"})
        elif key not in new_imps:
            importers.append({"brand": brand, "name": key[1], "status": "removed"})
        else:
            fields = {f: [old_imps[key].get(f), new_imps[key].get(f)]
                      for f in IMPORTER_FIELDS if old_imps[key].get(f) != new_imps[key].get(f)}
            if fields:
                importers.append({"brand": brand, "name": key[1], "status": "changed", "fields": fields})

    old_brands = {b["id"] for b in previous.get("brands", [])}
    new_brands = {b["id"] for b in catalog["brands"]}
    new_models = {m["modelKey"] for m in catalog["models"]}
    return {
        "added": [brief(new_cars[i]) for i in sorted(new_cars.keys() - old_cars.keys())],
        "removed": [brief(old_cars[i]) for i in sorted(old_cars.keys() - new_cars.keys())],
        "repriced": [
            {**brief(new_cars[i]), "from": old_cars[i]["priceUsd"]}
            for i in shared if new_cars[i]["priceUsd"] != old_cars[i]["priceUsd"]
        ],
        "changed": changed,
        "brandsAdded": sorted(names[b] for b in new_brands - old_brands),
        "brandsRemoved": sorted(names[b] for b in old_brands - new_brands),
        "importers": importers,
        # Fichas que se quedaron sin ninguna versión a la venta y salieron.
        "modelsRemoved": sorted(m["modelKey"] for m in previous.get("models", [])
                                if m["modelKey"] not in new_models),
    }


def totals_of(catalog):
    return {
        "brands": len(catalog["brands"]), "importers": len(catalog["importers"]),
        "cars": len(catalog["cars"]), "models": len(catalog["models"]),
        "withoutModel": sum(1 for car in catalog["cars"] if not car.get("modelKey")),
    }


def changelog_entry(previous, catalog, at, run_id=None):
    return {
        "at": at,
        "runId": run_id,
        "dataVersion": catalog["dataVersion"],
        "previousVersion": previous.get("dataVersion"),
        "sourceUpdatedAt": catalog["updatedAt"],
        "totals": totals_of(catalog),
        **diff_catalogs(previous, catalog),
    }


def merge_changelog(entries, entry):
    """La entrada nueva primero; una versión que ya estaba no se repite."""
    entries = [entry] + [e for e in entries if e["dataVersion"] != entry["dataVersion"]]
    return entries[:CHANGELOG_MAX_ENTRIES]


def catalog_text(catalog):
    """catalog.json tal como se publica: compacto, sin salto de línea al final."""
    return json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))


def changelog_text(entries):
    return json.dumps({"entries": entries}, ensure_ascii=False, indent=1) + "\n"


def summary_lines(diff):
    """El resumen de una línea por cambio que va al commit y al job."""
    lines = []
    for car in diff["added"]:
        lines.append(f"+ {car['id']} — U$S {car['priceUsd']:,}".replace(",", "."))
    for car in diff["removed"]:
        lines.append(f"- {car['id']}")
    for car in diff["repriced"]:
        lines.append(f"~ {car['id']} — {car['from']} -> {car['priceUsd']}")
    return lines


def report(previous, catalog, new_brands, diff):
    """El resumen de la corrida: va al commit, a los logs y al panel."""
    changes = summary_lines(diff)
    sin_ficha = sum(1 for car in catalog["cars"] if not car.get("modelKey"))
    lines = [f"marcas: {len(catalog['brands'])} | importadores: {len(catalog['importers'])} | "
             f"versiones: {len(catalog['cars'])} | fichas: {len(catalog['models'])} | "
             f"sin ficha: {sin_ficha} | actualizado: {catalog['updatedAt']}"]
    if new_brands:
        lines.append("marcas nuevas: " + ", ".join(new_brands))
    if changes:
        lines.append(f"cambios ({len(changes)}):")
        lines.extend("  " + line for line in changes[:60])
        if len(changes) > 60:
            lines.append(f"  … y {len(changes) - 60} más")
    elif previous.get("dataVersion") == catalog["dataVersion"]:
        lines.append("sin cambios respecto del catálogo anterior")
    else:
        lines.append("sin altas, bajas ni cambios de precio, pero el contenido cambió")
    return "\n".join(lines)


def run(html, previous, overrides, changelog_entries, run_id=None, now=None):
    """Una corrida completa, sin tocar el disco ni la red.

    Devuelve (catalog, entries, resumen, changed): `entries` es el changelog
    nuevo, o None si el catálogo no cambió (una corrida sin cambios no escribe
    nada).
    """
    catalog, new_brands = build_catalog(html, previous, overrides)
    diff = diff_catalogs(previous, catalog)
    summary = report(previous, catalog, new_brands, diff)
    changed = previous.get("dataVersion") != catalog["dataVersion"]
    entries = None
    if changed:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        at = now.isoformat(timespec="seconds")
        entries = merge_changelog(changelog_entries, changelog_entry(previous, catalog, at, run_id))
    return catalog, entries, summary, changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="no escribe catalog.json, solo informa qué cambiaría")
    args = parser.parse_args()

    changelog_entries = []
    if os.path.exists(CHANGELOG):
        with open(CHANGELOG, encoding="utf-8") as handle:
            changelog_entries = json.load(handle).get("entries", [])
    try:
        catalog, entries, summary, _ = run(
            fetch_html(), previous_catalog(), read_overrides(), changelog_entries)
    except PageChanged as error:
        sys.exit(str(error))
    print(summary)

    if args.dry_run:
        return
    with open(CATALOG, "w", encoding="utf-8") as handle:
        handle.write(catalog_text(catalog))
    # Cada versión publicada deja su entrada en changelog.json, que es lo que lee
    # el panel de monitoreo. Una corrida sin cambios no escribe nada.
    if entries is not None:
        with open(CHANGELOG, "w", encoding="utf-8") as handle:
            handle.write(changelog_text(entries))


if __name__ == "__main__":
    main()
