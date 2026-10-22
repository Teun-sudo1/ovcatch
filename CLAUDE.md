# CLAUDE.md — projectcontext voor Claude Code

OV-catchment tool: bereik van bushaltes/lijnvarianten vergelijken met open data (BAG, CBS, OSM, GTFS). Python 3.11+ backend (FastAPI), vanilla JS + MapLibre frontend, Valhalla in Docker.

## Structuur
- `ovcatch/data/` — download, BAG/CBS/OSM inlezen, `prepare.py` orkestreert
- `ovcatch/disaggregate.py` — inwoners verdelen over woningen (kernmethode, zie DECISIONS.md)
- `ovcatch/gtfs.py` — GTFS indexeren met DuckDB, lijn opbouwen, richtingen samenvoegen
- `ovcatch/routing/` — `valhalla.py` (echt), `euclid.py` (benadering voor demo/tests), cache
- `ovcatch/analysis.py` — catchment per halte/lijn, toewijzing zonder dubbeltelling, vergelijking, CSV
- `ovcatch/api/app.py` — endpoints; `web/` — UI
- `scripts/valhalla_native.sh` — Valhalla zonder Docker (pyvalhalla), via `make valhalla`
- `config.yaml` (echt) / `config.demo.yaml` (synthetisch, euclid-router)

## Werkafspraken
- Werk zelfstandig; leg keuzes vast in `DECISIONS.md`, voortgang in `PROGRESS.md`.
- Tests moeten blijven slagen: `make test` (of `.venv/bin/python -m pytest -q`). Tests draaien op synthetische data, zonder netwerk.
- Pas kolomnamen/URL's aan in `config.yaml` (kandidatenlijsten), niet hardcoded in code.
- Geen ruwe data committen (`data/` staat in .gitignore).
- Interne geometrie in EPSG:28992; API-in/uitvoer in WGS84 (lon, lat).
- UI-teksten in het Nederlands.
