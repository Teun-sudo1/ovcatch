# Voortgang

## Eindstatus (22-10-2026): werkend op echte data, regio Gooi

| Fase | Status | Toelichting |
|---|---|---|
| 1. Setup + demo | ✓ | `make setup && make test && make demo`; `/api/config` en `/` geven 200 |
| 2. Data-pipeline | ✓ echte data | BAG, CBS vk100 2025, wijkenbuurten 2025, OSM NH + Utrecht; validatie 95,6% binnen ±10% |
| 3. GTFS | ✓ | OVapi gtfs-nl; bussen = `route_type` 3; lijnen door Huizen: 100, 108, 200, 221, N32 |
| 4. Valhalla | ✓ native | pyvalhalla (geen Docker op deze machine); afstandscontouren lopen/fietsen werken, busroute werkt |
| 5. End-to-end | ✓ | lijn 100 (route_id 147007), sanity checks kloppen |
| 6. Web-UI | ✓ via API | alle UI-handelingen nagespeeld tegen de draaiende server; niet visueel in een browser bekeken |
| 7. Performance | ✓ | vergelijking 2 × 25 haltes: 4,4 s warm, 4,5 s koud |

Tests: `make test` → 28 geslaagd (synthetische data, geen netwerk).

## Metingen (MacBook, Apple Silicon, 10 cores, 16 GB)

| Stap | Doorlooptijd | Piekgeheugen | Resultaat |
|---|---|---|---|
| `make download` | ~20 min (BAG 7,8 GB) | — | 8,5 GB in `data/raw` + `data/gtfs` |
| `make prepare` (Gooi) | 66 s (BAG/CBS ~10 s, OSM 2 extracten ~57 s) | 1,25 GB | 311.502 adressen, 276.387 woningen, 1.195 voorzieningen |
| `make gtfs` (heel NL) | 4,5 s | 1,9 GB | 3.065 routes, waarvan 2.799 bus |
| `make valhalla-build` (NH + Utrecht) | 21 s | 1,9 GB | tiles.tar 200 MB |
| analyse lijn 100 (CLI, koud) | 3,0 s | — | 26 → 25 haltes na samenvoegen perrons |

## Controles echte data
- **BAG** (`bag-light.gpkg`): laag `verblijfsobject`, één rij per object, `gebruiksdoel` als komma-lijst (`industriefunctie,kantoorfunctie`). Statussen in de regio: in gebruik 191k, gevormd 7k (niet meegeteld), verbouwing 1,3k, niet ingemeten 0,4k, buiten gebruik 9. Ligplaatsen/standplaatsen: status `Plaats aangewezen` (635 / 913).
- **CBS vk100 2025**: laag `cbs_vk100_2025`, kolommen `aantal_inwoners`/`aantal_woningen`, geheim = `-99997` (2.987 van 14.087 vierkanten in de regio).
- **wijkenbuurten 2025**: bevat `aantal_inwoners` (peildatum 1-1-2025), maar **nog geen woningvoorraad** → noemer uit BAG (zie DECISIONS.md).
- **Validatie**: 385 buurten geheel binnen de regio, 95,6% binnen ±10%, mediane fout 0,45%, p90 3,2%. Randbuurten (deels buiten bbox) uitgesloten: daar was 57% binnen ±10% door de afgeknipte BAG.
- **Valhalla 3.9.1**: `contours: [{distance: km}]` werkt voor pedestrian en bicycle (geen terugval op tijd nodig); `costing: bus` werkt.
- **Lijn 100, lopen 400 m**: per halte 89–2.919 inwoners bruto; lijntotaal 24.845 ≤ som bruto 33.357; som toegewezen = lijntotaal.

## Opgeloste problemen
- `.gitignore` negeerde met `data/` ook het codepakket `ovcatch/data/` → nu `/data/`.
- Gooi-bbox valt deels in Utrecht → meerdere OSM-extracten (`region.osm_pbf_urls`), voorzieningen ontdubbeld.
- wijkenbuurten 2025 zonder woningvoorraad → kolom optioneel, BAG-telling als noemer.
- Validatie onterecht < 90% door randbuurten → alleen buurten binnen de regio.
- Lijnen zoeken op "huizen" gaf Enkhuizen, Vinkhuizen, … → match op woordbegin.
- Perrons per richting > 150 m uit elkaar (Aristoteleslaan 178 m, Orionlaan 207 m) gaven dubbele haltes → `merge_distance_m: 250`.
- Geen Docker → `scripts/valhalla_native.sh` + `make valhalla` kiest automatisch.
- Isochronen sequentieel → parallel (`routing.parallel_requests`), cache atomair geschreven.

## Bekende beperkingen
- Toewijzing gebruikt drempelbanden + hemelsbrede tie-break, geen exacte netwerkafstand per adres.
- OSM-multipolygonen (relaties) niet meegenomen als voorziening.
- Ringlijnen: haltevolgorde kan afwijken (projectie op shape).
- Representatief patroon = meest gereden; zijtakken (lijn 100 naar Hilversum: 9–18 ritten/dag) vallen weg. Laad zo nodig een ander patroon via een eigen scenario.
- CBS (1-1-2025) loopt achter op de BAG: nieuwbouwwijken (Nobelhorst, Vogelhorst) krijgen inwoners via het buurtgemiddelde.
- UI niet visueel in een browser getest (geen browser-tool beschikbaar); de API-flow achter elke knop is wel getest.

## Heel Nederland draaien
1. `config.yaml`: `region.bbox_wgs84: null`, `region.name: nl`, `region.osm_pbf_urls: [https://download.geofabrik.de/europe/netherlands-latest.osm.pbf]`.
2. `make download prepare gtfs` — niet gemeten voor heel NL. Gooi (0,3 mln objecten) kostte 1,25 GB; NL heeft ~10 mln BAG-objecten, dus reken op een veelvoud. Lukt het niet in het geheugen, draai dan per provincie (bbox per provincie).
3. `rm -rf data/valhalla && make valhalla` — tiles voor NL uit één landelijk extract; niet gemeten, verwacht minuten tot tientallen minuten.
