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
- **Jaargangen**: vierkant en buurt bij voorkeur hetzelfde peiljaar. Gebruikt jaar staat in de bron-URL's in `config.yaml`. Gekozen: vk100 2025 + wijkenbuurten 2025 (beide peildatum 1-1-2025).
- **Woningvoorraad buurt optioneel** — wijkenbuurten 2025 (eerste publicatie) heeft `aantal_inwoners` maar nog geen woningvoorraad; dan is de noemer voor "inwoners per woning" (stap B) het aantal BAG-woningen in de buurt. Liever dan terugvallen op 2024: dan lopen vierkant- en buurtjaar uiteen. (Alt: `gemiddelde_huishoudsgrootte`.)
- **Validatie alleen op buurten geheel binnen de regio-bbox** — van randbuurten is de BAG afgeknipt, dus het CBS-totaal is daar niet vergelijkbaar (Gooi: randbuurten 57% binnen ±10%, binnenbuurten 96%). Resterende afwijkingen zitten vooral in nieuwbouwwijken (BAG is actueler dan CBS 1-1-2025).

## Werk/winkel/voorzieningen
- **m² per categorie uit BAG-gebruiksdoel**; object met meerdere doelen → oppervlakte gelijk verdeeld (bijv. woon+winkel 50/50). BAG-oppervlakte is gebruiksoppervlak (GO), niet BVO.
- **Arbeidsplaatsen = m² ÷ ratio (config)**, overal gelabeld als *ruwe schatting*. (Alt: LISA — niet open.)
- **OSM-voorzieningen**: nodes + ways (zwaartepunt). Multipolygon-relaties overgeslagen (klein effect in NL).
- **Meerdere OSM-extracten** (`region.osm_pbf_urls`) — het Gooi-bbox valt deels in Utrecht (Baarn, Soest, Eemnes); NH + Utrecht samen is kleiner dan heel NL. Voorzieningen worden ontdubbeld op OSM-id; Valhalla bouwt tiles uit alle extracten.

## Bereikbaarheid
- **Valhalla** (Docker `ghcr.io/valhalla/valhalla-scripted`): pedestrian/bicycle-isochronen met **afstandscontouren** (km); valt automatisch terug op tijdcontouren (5 km/u lopen, 15 km/u fietsen) als de server die niet kent. Bus-costing voor de lijnroute. Max. 4 contouren per verzoek → automatisch opgesplitst.
- **Valhalla native via pyvalhalla als Docker ontbreekt** — de wheel bevat `valhalla_build_tiles` en `valhalla_service`; geen Docker Desktop nodig op macOS. Zelfde HTTP-API op :8002, dus de client is ongewijzigd. Getest: 3.9.1, afstandscontouren werken.
- **Isochronen parallel** (`routing.parallel_requests`, standaard 8) — koude analyse van 25 haltes × 2 modi in ~3 s; cachebestanden atomair geschreven.
- **Drempels**: lopen 400/800/1200 m, fietsen 1,5/3 km (config).
- **Isochronen gecachet op schijf** (`data/cache/isochrones/`), sleutel = coördinaat (6 dec.) + modus + drempels.
- **Benaderende router `euclid`** (cirkels met omrijfactor 1,3) alleen voor demo/tests of als Valhalla niet draait; de UI toont dan een waarschuwing.

## Toewijzing en dubbeltelling
- **raw** per halte = alles binnen de isochroon (overlap telt dubbel).
- **assigned** per halte = elk object bij precies één halte: kleinste drempelband waarin het valt, bij gelijke band de hemelsbreed dichtstbijzijnde halte. (Alt: Valhalla-matrix voor exacte netwerkafstand per adres — veel duurder; band-benadering is binnen één drempelstap nauwkeurig.)
- **Lijntotaal** = unieke objecten = som van assigned.

## Lijnmodel
- **Representatief patroon** per route/richting = meest gereden stoppatroon (bij gelijkspel het langste).
- **Logische haltes**: haltes van beide richtingen samengevoegd bij zelfde naam ≤ 250 m (`line.merge_distance_m`; 150 m bleek te krap: Huizen Aristoteleslaan 178 m, Hilversum Orionlaan 207 m), of ≤ 40 m ongeacht naam; geordend langs de shape van richting 0. Lijnen met een lus/ring kunnen een afwijkende volgorde krijgen — controleer visueel.
- **Scenario-formaat** (JSON): `stops[]` (id, name, lon, lat, members), `vias[]` (after = index halte, lon, lat), `geometry`, `modified`, `source`, `feed`, `route_id`. Origineel ongewijzigd → GTFS-shape wordt getoond; na bewerking → Valhalla-busroute.
- **Rijtijd** van beide scenario's via dezelfde router (appels met appels); GTFS-dienstregelingstijd van het origineel wordt apart getoond.

## Frontend
- **Vanilla JS + MapLibre GL (unpkg) + PDOK BRT grijs** — geen build-stap, één `web/`-map.
- **Origineel vs. variant**: elk kan uit een andere GTFS-feed komen (bijv. archief-feed van vóór de omlegging als origineel, actuele feed als variant).

## Heel Nederland
- **Prepare per tegel van 20 km, uitgelijnd op het 100m-raster** — in één keer kost heel NL ~12 GB en loopt vast in swap op 16 GB. Stap A (vierkant) is lokaal en loopt per tegel; stap B/C heeft alleen kleine buurtsommen nodig (optelbaar over tegels) en wordt daarna in één keer bepaald. Een vierkant valt altijd in precies één tegel (middelpunt). Resultaat gelijk aan de enkele run (test; Gooi: 631.770 vs 631.741 inwoners, verschil door mediane oppervlakte per tegel voor objecten zonder oppervlakte).
- **OSM-voorzieningen met C++ `KeyFilter`** — de Python-handler ziet alleen objecten met een relevante sleutel; NL in 35 s.

## Webviewer
- **Statisch op GitHub Pages, vooraf berekend** — geen server/Valhalla nodig voor bezoekers. (Alt: de FastAPI-app hosten — vergt server met ~10 GB data en Valhalla.)
- **Eenheid: haltelocatie** = bus-perrons met dezelfde naam binnen `line.merge_distance_m` (verbonden componenten); één isochroon op het gemiddelde punt. `parent_station` ontbreekt bij ruim een derde van de perrons, dus niet bruikbaar.
- **Per halte bruto, per lijn uniek** — per halte telt alles binnen bereik (wat je bij één halte wilt weten); per lijn telt elk adres één keer, zoals `analyse`.
- **Alleen haltes binnen NL** (CBS-gemeentegrenzen + 200 m) — de OVapi-feed bevat ook België (De Lijn) en Duitsland, waar BAG/CBS ontbreekt. Lijnen met < 50% haltes in NL vallen weg.
- **Compacte JSON + polylines (1e-5°)**; isochronen per tegel van 10 km, pas geladen bij klikken op een halte.
- **Data op `gh-pages` als één commit zonder geschiedenis** (force push) — elke nieuwe export vervangt de vorige; `main` blijft klein.
- **Bewerken in de browser i.p.v. eigen server** (keuze gebruiker) — blijft gratis op GitHub Pages. Isochronen: vooraf berekend voor bestaande haltes, anders de openbare FOSSGIS-Valhalla (zelfde versie 3.9.1, CORS open, max. 2 verzoeken tegelijk uit beleefdheid). Busroute idem. (Alt: FastAPI + Valhalla op een VPS, ~€10–30/mnd.)
- **Tellen op 100m-vierkanten in de browser** — 9,7 mln adressen passen niet in een webpagina; 715.703 gevulde vierkanten (23 MB, per tegel van 10 km geladen) wel. Een vierkant telt als zijn middelpunt binnen de isochroon valt. Origineel en variant worden beide zo geteld (eerlijke vergelijking); t.o.v. adresniveau ~0,1–1% verschil (lijn 100: 25.125 vs 24.888 ≤ 400 m).
- **Snappen**: een halte die binnen 40 m van een bestaande haltelocatie wordt neergezet, wordt die haltelocatie (vooraf berekende isochroon, geen serververzoek).
- **Scenario's in localStorage + JSON-download** — geen backend; delen gaat via het bestand.
- **Busroute volgt één richting + `radius` 25 m** — het lijnmodel voegt beide richtingen samen, dus bevat ook perrons die maar één richting bedienen (bijv. Eemnes/Laren P+R Oost/West aan weerszijden van de A27) en haltes die tussen twee rijbanen gemiddeld zijn. Een route langs álle haltes reed daardoor om (lijn 320: 79 km i.p.v. 49 km). Nu: haltes met alleen de terugrichting (`dirs == [1]`) tellen mee voor het bereik maar niet voor de gereden route, en Valhalla mag elke weg binnen 25 m kiezen. Resultaat ≈ lengte GTFS-shape (320: 48,9 = 48,9 km; 200: 29,9 vs 29,7 km).
- **Openbare routeserver: max. 10 punten per route** — de viewer splitst langere routes in stukken die op een halte beginnen en eindigen (via-punten kunnen geen begin/eind zijn) en plakt ze aan elkaar. Lokaal getest: in stukken = in één keer (79,12 km beide). De FOSSGIS-server rekent iets andere afstanden dan onze eigen Valhalla (andere OSM-stand/instellingen), maar origineel en variant gaan beide via FOSSGIS, dus het verschil blijft eerlijk.
