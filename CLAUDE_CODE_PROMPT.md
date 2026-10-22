# Prompt voor Claude Code (plak dit na het openen van de map)

```
Deze repo (ovcatch) is een OV-halte catchment tool die al grotendeels gebouwd is, maar
zonder toegang tot de echte databronnen. Lees eerst CLAUDE.md, README.md, DECISIONS.md en
PROGRESS.md. Jouw taak: de tool werkend maken op echte data en alles verifiëren.

Werkwijze
- Werk zelfstandig alle stappen af, zonder tussentijdse goedkeuring. Stel geen vragen.
  Neem bij twijfel de meest redelijke keuze, leg die vast in DECISIONS.md en ga door.
- Stop alleen bij een echte blokkade (bijv. Docker draait niet, schijf vol, bron onbereikbaar
  zonder alternatief). Beschrijf dan exact wat ik moet doen.
- Bron-URL of kolomnaam anders dan verwacht? Zoek de actuele (PDOK, CBS, OVapi, Geofabrik),
  pas config.yaml aan en ga door.
- Na elke stap: `make test` moet slagen. Voeg tests toe voor alles wat je fixt.
- Werk PROGRESS.md bij en commit na elke afgeronde stap (git init als dat nog niet is gedaan).
- Platform: macOS, Docker Desktop. Gebruik een venv (`make setup`).

Stappen
1. Setup: `make setup && make test && make demo`. Start kort `make serve-demo` en controleer
   met curl dat /api/config en / werken.
2. Data (regio Gooi, zie config.yaml): `make download`, dan `make prepare`.
   - Controleer BAG-gpkg: laagnaam verblijfsobject, formaat gebruiksdoel (komma-lijst?),
     exacte statuswaarden (vergelijk met bag.statuses_in_use), ligplaats/standplaats-lagen.
   - Controleer CBS vk100-gpkg en wijkenbuurten-gpkg: kolomnamen inwoners/woningen,
     geheimcodes, en of wijkenbuurten 2025 al kerncijfers bevat (anders 2024 met cijfers).
     Gebruik bij voorkeur hetzelfde peiljaar voor vierkant en buurt.
   - `python -m ovcatch validate`: ≥ 90% van de buurten binnen ±10%. Zo niet: analyseer
     oorzaak (jaargang, statusfilter, ontbrekende lig/standplaatsen) en verbeter.
   - Documenteer doorlooptijd en geheugengebruik in PROGRESS.md.
3. GTFS: `make gtfs`. Controleer route_types van bussen in de OVapi-feed (line.bus_route_types).
   Zoek met `python -m ovcatch routes --feed gtfs-nl --q huizen` de buslijnen door Huizen.
4. Valhalla: `make valhalla`, wacht tot tiles gebouwd zijn (`make valhalla-logs`,
   `curl localhost:8002/status`). Op Apple Silicon: als het image niet start, zet
   `platform: linux/amd64` aan in docker-compose.yml. Controleer:
   - isochroon met afstandscontouren (pedestrian, bicycle) werkt; anders werkt de
     tijd-terugval — documenteer welke.
   - /route met costing "bus" werkt.
5. End-to-end: `python -m ovcatch analyse --feed gtfs-nl --route <lijn door Huizen>`.
   Sanity check: inwoners binnen 400 m lopen per halte plausibel (honderden tot paar duizend),
   lijntotaal ≤ som per halte (bruto). Fix wat niet klopt.
6. Web-UI: `make serve`, open http://localhost:8000 en test in de browser (gebruik een
   browser-tool als je die hebt; anders curl + controleer API-responses): lijn laden als
   origineel en variant, halte verslepen, halte toevoegen, via-punt, verwijderen,
   berekenen, CSV-export, scenario opslaan/laden. Fix bugs.
7. Performance: één analyse van een lijn met ~20 haltes moet met warme cache < 10 s duren.
   Meet en optimaliseer zo nodig (bijv. parallelle isochroon-requests).
8. Afronding: README bijwerken met wat je anders deed, PROGRESS.md met eindstatus,
   bekende beperkingen en hoe heel NL te draaien (bbox null + NL-extract).

Begin direct met stap 1.
```

## Handig vooraf
- Docker Desktop (of OrbStack) draait, met ≥ 8 GB geheugen en ≥ 40 GB schijf.
- Zet Claude Code op auto-accept edits (Shift+Tab). `.claude/settings.json` staat al veelgebruikte commando's toe.
- Oude route: zet een gearchiveerde GTFS-zip van vóór de omlegging in de map en laat Claude Code die indexeren met `python -m ovcatch gtfs-index --gtfs <zip> --name nl_2024`.
