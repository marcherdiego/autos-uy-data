# Autos UY — catálogo

Los datos que consume la app **Autos UY**: precios y fichas de los autos 0km a la
venta en Uruguay.

**El archivo es [`catalog.json`](catalog.json)**, y la app lo lee de:

```
https://raw.githubusercontent.com/marcherdiego/autos-uy-data/main/catalog.json
```

## Cómo se actualiza

Un **cron de Vercel** ([`api/cron/catalogo.py`](api/cron/catalogo.py)) corre **una vez
por día a las 08:00 de Uruguay** (11:00 UTC; Vercel lo larga dentro de esa hora),
lee la página de precios de
[Autoblog Uruguay](https://www.autoblog.com.uy/p/precios-0km.html) y, si algo cambió,
commitea `catalog.json` y `changelog.json` en `main` por la API de GitHub. El mensaje
del commit lista qué entró, qué salió y qué cambió de precio.

Hasta setiembre de 2026 lo hacía un workflow de GitHub Actions, pero algunos días
GitHub no lo largaba (el 23 y el 24/09 no corrió) y se reemplazó por el cron.

- La lógica es la de [`tools/scrape_catalog.py`](tools/scrape_catalog.py): el cron
  llama a su `run()` con los archivos leídos de `main`. Si alguien pushea en el medio
  (por ejemplo `publicar_fichas.py`), la corrida se reintenta sobre el `main` nuevo.
- Cada corrida, con o sin cambios, o fallida, queda en `runs.json` de la rama
  **`corridas`**, que sólo tiene ese archivo: así `main` no se llena de commits.
- Si una corrida falla (la página cambió de estructura, GitHub no responde…),
  manda un **push por ntfy**. Si el cron directamente no corre, lo avisa el vigía de
  `cargadores-server`, que mira `runs.json` todos los días.
- A mano, con el `CRON_SECRET` de Vercel:

  ```bash
  curl -H "Authorization: Bearer $CRON_SECRET" "https://autos-uy-panel.vercel.app/api/cron/catalogo?manual=1"
  curl -H "Authorization: Bearer $CRON_SECRET" "https://autos-uy-panel.vercel.app/api/cron/catalogo?dry=1"   # sin commitear
  ```

- Local, contra este checkout:

  ```bash
  pip install beautifulsoup4
  python3 tools/scrape_catalog.py --dry-run   # sólo informa
  python3 tools/scrape_catalog.py             # escribe catalog.json y changelog.json
  ```

### Vigía de Cargadores UY

Este proyecto también vigila al de Cargadores UY, y viceversa: si los crons de un
proyecto dejan de correr, un vigía en el mismo proyecto tampoco correría.
[`api/cron/vigia.js`](api/cron/vigia.js) corre a las 10:00 de Uruguay, lee
`https://cargadores-server.vercel.app/api/health` y avisa por ntfy si el último sync
de UTE tiene más de 26 horas.

## Changelog y panel de monitoreo

Cada corrida que publica un catálogo nuevo agrega una entrada a
[`changelog.json`](changelog.json) (la más nueva primero): versiones que entraron,
que salieron y que cambiaron de precio, otros campos que cambiaron (nombre,
nota, combustible, ficha), marcas e importadores nuevos o modificados, y fichas
que se quedaron sin versiones. Una corrida sin cambios no escribe nada.

El panel lo muestra junto con el estado de cada corrida:
**https://autos-uy-panel.vercel.app** (pide la contraseña, `ADMIN_SECRET`).

- `public/index.html` es el panel (HTML + JS, sin build) y `api/panel.js` le
  junta los datos: las corridas, de `runs.json` en la rama `corridas`, y el
  changelog, de `main`. Los lee en vivo, así que un cambio en los datos no necesita deploy.
- El proyecto de Vercel (`autos-uy-panel`) está conectado a este repo, pero
  `ignoreCommand` en `vercel.json` saltea el deploy salvo que el commit toque el
  panel o los crons (`public/`, `api/`, `vercel.json`, `package.json`,
  `requirements.txt`, `tools/scrape_catalog.py`): los commits diarios del catálogo y
  los de la rama `corridas` no deployan nada.
- Variables en Vercel:
  - `ADMIN_SECRET`: la contraseña del panel (sin ella responde 503).
  - `CRON_SECRET`: Vercel la manda en cada llamada del cron; sin ella los crons
    responden 401.
  - `GITHUB_TOKEN`: token *fine-grained* con **Contents: read and write** sólo sobre
    este repo, para commitear el catálogo y `runs.json`.
  - `NTFY_TOPIC`: el tema de ntfy al que van las alertas (el mismo que usa
    `cargadores-server`). Es secreto: quien lo conozca puede leer las alertas.

## Correcciones que vienen de la app

`overrides.json` corrige lo que el scraper no puede deducir del nombre de una
versión: el combustible de los enchufables que no lo dicen (un ROX de autonomía
extendida, un BYD DM-p) y la categoría que sale de la carrocería de la ficha
(pick-up, utilitario). Lo escribe `tools/publicar_fichas.py` del repo de la app al
publicar fichas; el scraper sólo lo aplica en cada corrida. No se edita a mano.

## Qué actualiza y qué no

| | |
|---|---|
| Precios, versiones nuevas y versiones que salieron de lista | ✅ automático |
| Marcas nuevas (nombre, importador, garantía) | ✅ automático |
| Logo de una marca nueva | ❌ la app le dibuja sus iniciales hasta la próxima release |
| Ficha técnica de un modelo nuevo (motor, dimensiones, a favor/en contra) | ❌ se arma aparte, leyendo las notas del modelo |

Las fichas (`models`) **se conservan** entre corridas: el job nunca las borra,
sólo saca las versiones que ya no están en la lista de precios.

## Estructura

```
catalog.json
├── source, updatedAt, currency, dataVersion   dataVersion = hash del contenido;
├── brands[]      id, name, logo, logoUrl      cuando cambia, la app rehace su base
├── importers[]   por marca: dirección, teléfono, web, garantía
├── cars[]        cada versión con su precio en dólares
└── models[]      ficha técnica y notas de cada modelo comercial
```

Los datos salen de lo que publica Autoblog Uruguay.
