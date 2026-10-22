# Ontwerpbeslissingen

Format: **keuze** — reden — (alternatief).

## Data en opslag
- **GeoParquet (EPSG:28992) met bbox-kolom, ruimtelijk gesorteerd per km-cel** — snel bbox-lezen met `geopandas.read_parquet(bbox=...)`, geen database nodig, werkt op laptop. (Alt: DuckDB spatial / PostGIS. DuckDB wordt wél gebruikt voor het indexeren van GTFS, maar zonder spatial-extensie, zodat er geen extensie-download nodig is.)
- **Alles intern in RD (EPSG:28992)**, isochronen/routes in en uit Valhalla in WGS84 — afstanden en oppervlaktes kloppen in meters.
- **BAG uit landelijke `bag-light.gpkg`, geknipt op `region.bbox_wgs84` bij inlezen** (pyogrio bbox) — één bron voor heel NL; regio-ontwikkeling is snel. Voor heel NL: `bbox_wgs84: null`.
- **Statusfilter BAG**: alleen 'in gebruik', 'in gebruik (niet ingemeten)', 'verbouwing'. Ligplaatsen/standplaatsen (woonboten/woonwagens) tellen als woning met 60 m² (configureerbaar).
- **BAG-oppervlakte 1 m² en ≥ 999.999 m² = onbekend** (bekende plaatshouders).
- **Kolomnamen via kandidatenlijsten in `config.yaml`** — CBS wijzigt kolomnamen per jaargang; zo hoeft code niet te veranderen.

## Bevolkingsdisaggregatie
- **Stap A**: CBS 100m-vierkant met bekend inwonertal → verdelen over woonobjecten in dat vierkant naar rato van oppervlakte, geknipt op [20, 400] m² (uitschieters dempen). Instelbaar: `area`, `count`, `mix`.
- **Stap B**: woonobjecten in geheime/ontbrekende vierkanten → *rest* van het buurt-totaal (CBS buurt − al toegewezen via A). Is die rest onrealistisch (≤ 0, of per woning buiten 0,5–2× het buurtgemiddelde) → buurtgemiddelde inwoners/woning. Reden: houdt buurttotalen kloppend zonder absurde waarden bij jaargangverschillen.
- **Stap C**: buurt ook geheim → landelijke gemiddelde huishoudensgrootte (2,1, config).
- **Validatie** (`data/processed/validatie_buurten.csv`, `disaggregatie_rapport.json`): som per buurt vs. CBS-buurttotaal (buurten ≥ 50 inw.). Let op: door stap B is deze check deels per constructie goed; afwijkingen ontstaan vooral door jaargangverschil vierkant ↔ buurt en vierkanten zonder BAG-woning (`pop_grid_unassigned`). De onafhankelijke check zit in de tests (synthetische 'waarheid').
- **Jaargangen**: vierkant en buurt bij voorkeur hetzelfde peiljaar. Gebruikt jaar staat in de bron-URL's in `config.yaml`.

## Werk/winkel/voorzieningen
- **m² per categorie uit BAG-gebruiksdoel**; object met meerdere doelen → oppervlakte gelijk verdeeld (bijv. woon+winkel 50/50). BAG-oppervlakte is gebruiksoppervlak (GO), niet BVO.
- **Arbeidsplaatsen = m² ÷ ratio (config)**, overal gelabeld als *ruwe schatting*. (Alt: LISA — niet open.)
- **OSM-voorzieningen**: nodes + ways (zwaartepunt). Multipolygon-relaties overgeslagen (klein effect in NL).

## Bereikbaarheid
- **Valhalla** (Docker `ghcr.io/valhalla/valhalla-scripted`): pedestrian/bicycle-isochronen met **afstandscontouren** (km); valt automatisch terug op tijdcontouren (5 km/u lopen, 15 km/u fietsen) als de server die niet kent. Bus-costing voor de lijnroute. Max. 4 contouren per verzoek → automatisch opgesplitst.
- **Drempels**: lopen 400/800/1200 m, fietsen 1,5/3 km (config).
- **Isochronen gecachet op schijf** (`data/cache/isochrones/`), sleutel = coördinaat (6 dec.) + modus + drempels.
- **Benaderende router `euclid`** (cirkels met omrijfactor 1,3) alleen voor demo/tests of als Valhalla niet draait; de UI toont dan een waarschuwing.

## Toewijzing en dubbeltelling
- **raw** per halte = alles binnen de isochroon (overlap telt dubbel).
- **assigned** per halte = elk object bij precies één halte: kleinste drempelband waarin het valt, bij gelijke band de hemelsbreed dichtstbijzijnde halte. (Alt: Valhalla-matrix voor exacte netwerkafstand per adres — veel duurder; band-benadering is binnen één drempelstap nauwkeurig.)
- **Lijntotaal** = unieke objecten = som van assigned.

## Lijnmodel
- **Representatief patroon** per route/richting = meest gereden stoppatroon (bij gelijkspel het langste).
- **Logische haltes**: haltes van beide richtingen samengevoegd bij zelfde naam ≤ 150 m, of ≤ 40 m ongeacht naam; geordend langs de shape van richting 0. Lijnen met een lus/ring kunnen een afwijkende volgorde krijgen — controleer visueel.
- **Scenario-formaat** (JSON): `stops[]` (id, name, lon, lat, members), `vias[]` (after = index halte, lon, lat), `geometry`, `modified`, `source`, `feed`, `route_id`. Origineel ongewijzigd → GTFS-shape wordt getoond; na bewerking → Valhalla-busroute.
- **Rijtijd** van beide scenario's via dezelfde router (appels met appels); GTFS-dienstregelingstijd van het origineel wordt apart getoond.

## Frontend
- **Vanilla JS + MapLibre GL (unpkg) + PDOK BRT grijs** — geen build-stap, één `web/`-map.
- **Origineel vs. variant**: elk kan uit een andere GTFS-feed komen (bijv. archief-feed van vóór de omlegging als origineel, actuele feed als variant).
