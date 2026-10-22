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

## Heel Nederland (22-10-2026)

Draait op deze Mac (16 GB) zonder Docker. `config.yaml` staat standaard op heel NL.

| Stap | Doorlooptijd | Piekgeheugen | Resultaat |
|---|---|---|---|
| OSM NL downloaden | ~1 min | — | netherlands-latest.osm.pbf 1,4 GB |
| `make prepare` (133 tegels van 20 km) | 6 min | 2,8 GB | 9,7 mln objecten, 8,46 mln woningen, 43.735 voorzieningen |
| `make valhalla-build` | 81 s | 8,6 GB | tiles.tar 945 MB |
| `make web-export` | 15,6 min | 3,0 GB | 21.396 haltelocaties, 2.282 buslijnen, 40 MB |
| vierkanten voor bewerken (`--cells-only`) | 4 s | 2,3 GB | 715.703 vierkanten, 23 MB |

- Eerste poging `prepare` in één keer: ~9 GB, waarvan 8,3 GB in swap; na minuten geen voortgang → per tegel (zie DECISIONS.md). Gooi per tegel gelijk aan de enkele run.
- Validatie NL: 13.202 buurten, **96,3% binnen ±10%**, p90 3,0%. Totaal 18,10 mln inwoners geschat (CBS 1-1-2025: 18,04 mln).
- Web-export: 42.792 isochronen in ~3,5 min (~185/s, 0 mislukt), haltes 4,4 min, lijnen 5,9 min (8 processen).
- Controle export: elke lijn ligt tussen grootste halte en som bruto; waarden stijgen met de drempel; lijn 100 Huizen 24.888 inwoners ≤ 400 m (CLI-analyse: 24.845).
- 780 haltelocaties liggen alleen in nevenpatronen en hebben daarom geen lijn in de viewer; 214 hebben geen inwoners binnen 1,2 km (bedrijventerreinen, P+R).
- De OVapi-feed bevat ook België en Duitsland: 3.942 haltelocaties buiten NL weggelaten.

## Webviewer
- Code in `viewer/`, gepubliceerd op https://teun-sudo1.github.io/ovcatch/ via de branch `gh-pages` (`make deploy-site`).
- Getest: bestanden via HTTP (`make serve-site`) en de viewerlogica headless (JavaScriptCore met stubs) op de NL-data: laden, kleurklassen, zoeken, halte + isochronen, lopen/fietsen, lijn met unieke totalen. Niet visueel in een browser bekeken.
- **Bewerken in de viewer** (variant maken bij een lijn): headless getest op NL-data met gestubde routing: ongewijzigd → variant = origineel zonder serververzoeken voor isochronen; halte verslepen (2 isochroon-verzoeken), halte en via-punt toevoegen, CSV (293 regels), scenario opslaan/laden, bewerken sluiten. Gevonden en opgelost: tabel na bewerken liep uit de pas met de berekende haltes → resultaat bewaart een momentopname van de scenario's. De FOSSGIS-API zelf getest met curl (isochroon lopen/fietsen met afstandscontouren, busroute, CORS).
- **Busroute in de editor** (melding gebruiker: rechte lijnen i.p.v. over de weg): de openbare server weigert routes met > 10 punten ("Exceeded max locations: 10"); lijn 320 heeft 22 haltes. Opgelost door in stukken te routeren. Daarbij bleek de route langs álle haltes van het samengevoegde lijnmodel om te rijden via perrons van de andere richting (320: 79 km i.p.v. 49 km); nu volgt de route één richting met `radius` 25 m: lokaal 48,9 km = GTFS-shape, via FOSSGIS 54,4 km (andere kaartstand). Zelfde correctie in de lokale app (`route_points`, `ValhallaRouter`).
- Bijwerken na een nieuwe GTFS-feed: `make download` (alleen gtfs), `make gtfs`, `make valhalla`, `make web-export`, `make deploy-site`.
