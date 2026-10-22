# OV-catchment

Vergelijk het bereik (catchment) van een buslijn en haar haltes — origineel vs. variant — met open data.
Per halte en per lijn: geschatte inwoners, woningen, m² werk/winkel per functie, ruwe schatting arbeidsplaatsen en voorzieningen, binnen loop- en fietsafstand over het netwerk.

## Snel starten (demo, zonder echte data)

```bash
make setup        # venv + dependencies (Python ≥ 3.11)
make test         # tests op synthetische data
make serve-demo   # http://localhost:8000
```
De demo gebruikt een verzonnen dorp en een benaderende router (cirkels). Bovenin staat dan "DEMO" en "Benadering".

## Echte data (macOS met Docker Desktop of OrbStack)

1. **Regio kiezen** in `config.yaml` → `region.bbox_wgs84` (standaard: het Gooi) en `region.osm_pbf_url` (standaard: Noord-Holland). Voor heel NL: bbox `null` en `netherlands-latest.osm.pbf`.
2. **Data**: `make data` — downloadt BAG (~7 GB), CBS-vierkanten, CBS-buurten, OSM en GTFS NL, verwerkt alles naar `data/processed/` en indexeert GTFS.
   Validatie van de bevolkingsverdeling: `python -m ovcatch validate`.
3. **Routing**: `make valhalla` — kopieert het OSM-extract naar `data/valhalla/` en start Valhalla. De eerste start bouwt routing-tiles (Noord-Holland: ~10–20 min; NL: langer, ≥ 8 GB RAM voor Docker). Volg met `make valhalla-logs`; klaar als `curl localhost:8002/status` antwoordt.
4. **App**: `make serve` → http://localhost:8000

Alles in Docker kan ook: `docker compose up -d --build`.

### Oude route vergelijken
De oorspronkelijke lijn komt uit een **gearchiveerde GTFS-feed** van vóór de omlegging (bijv. via Mobility Database / Transitland, of eigen archief). Index die feed met een eigen naam:
```bash
python -m ovcatch gtfs-index --gtfs pad/naar/gtfs-2024.zip --name nl_2024
```
In de UI: kies feed `nl_2024` → lijn → **Origineel**; kies de actuele feed → zelfde lijn → **Variant**. Daarna haltes verslepen/toevoegen/verwijderen en **Bereken catchment**.

## CLI
```
python -m ovcatch download|prepare|gtfs-index|routes|analyse|validate|demo|serve|all
python -m ovcatch routes --feed gtfs-nl --q huizen
python -m ovcatch analyse --feed gtfs-nl --route <route_id>
```

## Gebruik in de web-UI
1. **Lijn laden**: feed kiezen, lijn zoeken, laden als *Origineel* en/of *Variant*.
2. **Variant bewerken**: verslepen, `+ Halte` (wordt op de logische plek in de volgorde ingevoegd), `+ Via-punt` (stuurt de route), `Verwijderen`. Dubbelklik = hernoemen. Scenario's opslaan/laden.
3. **Analyse**: lopen/fietsen en drempel kiezen; KPI's tonen variant t.o.v. origineel. Export naar CSV (puntkomma, decimale komma — opent direct in Excel NL).

## Methode (kort)
- **Inwoners**: CBS 100m-vierkant verdeeld over BAG-woningen naar oppervlakte; geheime vierkanten via buurtrest/buurtgemiddelde. Zie `DECISIONS.md`.
- **Werk/winkel**: m² BAG-gebruiksoppervlak per gebruiksfunctie; arbeidsplaatsen = m² ÷ aanname (config).
- **Bereik**: Valhalla-isochronen over OSM (pedestrian/bicycle); lijnroute via Valhalla bus-costing.
- **Geen dubbeltelling**: elk adres hoort bij één halte (kleinste afstandsband, dan dichtstbijzijnde).

## Bronnen en licenties
- BAG — Kadaster/PDOK, CC0
- CBS Vierkantstatistieken 100 m en Wijken & Buurten — CBS, CC-BY 4.0
- OpenStreetMap — © OpenStreetMap-bijdragers, ODbL
- GTFS NL — OVapi
- Achtergrondkaart — PDOK BRT-Achtergrondkaart (Kadaster), CC-BY

Code: MIT (zie `LICENSE`).
