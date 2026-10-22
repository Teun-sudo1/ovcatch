# OV-catchment

Vergelijk het bereik (catchment) van een buslijn en haar haltes — origineel vs. variant — met open data.
Per halte en per lijn: geschatte inwoners, woningen, m² werk/winkel per functie, ruwe schatting arbeidsplaatsen en voorzieningen, binnen loop- en fietsafstand over het netwerk.

**Webviewer: https://teun-sudo1.github.io/ovcatch/** — alle bushaltes en buslijnen van Nederland, vooraf berekend (zie [Webviewer](#webviewer-github-pages)).

## Snel starten (demo, zonder echte data)

```bash
make setup        # venv + dependencies (Python ≥ 3.11)
make test         # tests op synthetische data
make serve-demo   # http://localhost:8000
```
De demo gebruikt een verzonnen dorp en een benaderende router (cirkels). Bovenin staat dan "DEMO" en "Benadering".

## Echte data (macOS, 16 GB is genoeg voor heel NL)

1. **Regio kiezen** in `config.yaml` → `region.bbox_wgs84` (standaard `null` = heel Nederland) en `region.osm_pbf_urls` (standaard `netherlands-latest.osm.pbf`). Snel ontwikkelen op een regio kan ook, bijv. het Gooi: bbox `[5.05, 52.17, 5.45, 52.40]` met de extracten van Noord-Holland + Utrecht (het Gooi ligt op de provinciegrens).
2. **Data**: `make data` — downloadt BAG (~7,8 GB), CBS-vierkanten, CBS-buurten, OSM en GTFS NL, verwerkt alles naar `data/processed/` en indexeert GTFS. Verwerking gaat per tegel van 20 km (`prepare.tile_km`): heel NL in ~6 min met < 3 GB geheugen.
   Validatie van de bevolkingsverdeling: `python -m ovcatch validate`.
3. **Routing**: `make valhalla` — start Valhalla op `localhost:8002`.
   - **Zonder Docker** (standaard als `docker` ontbreekt): bouwt eenmalig tiles uit `data/raw/*.osm.pbf` met de `pyvalhalla`-wheel (heel NL: ~1,5 min, 8,6 GB piek) en start `valhalla_service` op de achtergrond (log: `data/valhalla/service.log`). Los: `make valhalla-build`, `make valhalla-serve`.
   - **Met Docker**: kopieert de extracten naar `data/valhalla/` en start de container; volg met `make valhalla-logs`.
   Klaar als `curl localhost:8002/status` antwoordt. Na een nieuwe OSM-download: `rm -rf data/valhalla` en opnieuw `make valhalla`.
4. **App**: `make serve` → http://localhost:8000

Alles in Docker kan ook: `docker compose up -d --build`.

## Webviewer (GitHub Pages)

Statische kaart op https://teun-sudo1.github.io/ovcatch/ — geen server nodig, alles is vooraf berekend:
- **Per haltelocatie** (perrons met dezelfde naam binnen 250 m): inwoners, woningen, arbeidsplaatsen, voorzieningen en adressen binnen 400/800/1200 m lopen en 1,5/3 km fietsen (bruto: alles binnen bereik), plus de isochronen.
- **Per buslijn**: dezelfde kengetallen uniek over alle haltes (elk adres één keer), en per halte.
- **Leeftijdsgroepen** (0–15, 15–25, 65+) per halte en lijn, en **frequentie**: busvertrekken per halte en ritten per lijn op een representatieve werkdag (totaal en in de ochtendspits 07–09; ritten op afroep tellen niet mee). Haltes kunnen gekleurd worden naar inwoners of naar vertrekken.
- Zoeken op lijnnummer, lijnnaam, vervoerder of haltenaam (pijltjes + Enter); deelbare links (`#lijn=…`, `#halte=…`). Licht en donker thema (volgt het systeem, knop rechtsboven).
- **Bewerken** (knop bij een lijn): haltes verslepen, toevoegen (ook door op een bestaande halte te klikken), verwijderen, via-punten, en vergelijken met het origineel; frequentie van de variant aanpassen (kengetal *bereik × frequentie* = unieke inwoners × ritten per werkdag); CSV-export; **delen via een link** (`#v=…`, de variant zit gecomprimeerd in de URL) of opslaan in de browser / als JSON-bestand. Ongewijzigde haltes gebruiken de vooraf berekende isochronen; nieuwe/verplaatste haltes en de busroute komen van de openbare Valhalla-server van [FOSSGIS](https://valhalla1.openstreetmap.de) (gratis, fair use). Tellen gebeurt in de browser op 100m-vierkanten (`cells/`), voor origineel en variant op dezelfde manier; dat wijkt ~0,1–1% af van de telling per adres.

Bijwerken (bijv. na een nieuwe GTFS-feed):
```bash
make valhalla      # Valhalla moet draaien
make web-export    # data/web (heel NL: zie PROGRESS.md voor de doorlooptijd)
make serve-site    # lokaal bekijken op http://localhost:8001
make deploy-site   # publiceren: data/site als één commit op de branch gh-pages
```
De viewer-code staat in `viewer/`; de gegenereerde data staat alleen op `gh-pages`, zodat `main` klein blijft.

### Oude route vergelijken
De oorspronkelijke lijn komt uit een **gearchiveerde GTFS-feed** van vóór de omlegging (bijv. via Mobility Database / Transitland, of eigen archief). Index die feed met een eigen naam:
```bash
python -m ovcatch gtfs-index --gtfs pad/naar/gtfs-2024.zip --name nl_2024
```
In de UI: kies feed `nl_2024` → lijn → **Origineel**; kies de actuele feed → zelfde lijn → **Variant**. Daarna haltes verslepen/toevoegen/verwijderen en **Bereken catchment**.

### Wat anders ging dan gepland (echte data)
- wijkenbuurten 2025 heeft nog geen woningvoorraad: de noemer voor "inwoners per woning" komt dan uit de BAG.
- Validatie telt alleen buurten die geheel binnen de regio liggen (randbuurten zijn door de bbox afgeknipt).
- Haltes van beide richtingen met dezelfde naam worden tot 250 m samengevoegd (perrons aan weerszijden van een kruispunt liggen soms > 150 m uit elkaar).
- Lijnen zoeken matcht op woordbegin: "huizen" vindt Huizen, niet Enkhuizen.
- Valhalla draait native (pyvalhalla) als Docker ontbreekt.
Details en metingen: `PROGRESS.md`; keuzes: `DECISIONS.md`.

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
