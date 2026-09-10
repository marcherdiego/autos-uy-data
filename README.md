# Autos UY — catálogo

Los datos que consume la app **Autos UY**: precios y fichas de los autos 0km a la
venta en Uruguay.

**El archivo es [`catalog.json`](catalog.json)**, y la app lo lee de:

```
https://raw.githubusercontent.com/marcherdiego/autos-uy-data/main/catalog.json
```

## Cómo se actualiza

Un job de GitHub Actions ([`actualizar-catalogo.yml`](.github/workflows/actualizar-catalogo.yml))
corre **una vez por día a las 08:00 de Uruguay**, lee la página de precios de
[Autoblog Uruguay](https://www.autoblog.com.uy/p/precios-0km.html) y commitea
`catalog.json` sólo si algo cambió. El mensaje del commit y el resumen del job
listan qué entró, qué salió y qué cambió de precio.

También se puede disparar a mano desde la pestaña Actions ("Run workflow"), y
correr local:

```bash
pip install beautifulsoup4
python3 tools/scrape_catalog.py --dry-run   # sólo informa
python3 tools/scrape_catalog.py             # escribe catalog.json
```

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
