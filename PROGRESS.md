# Voortgang

## Status bij overdracht (gebouwd zonder internettoegang tot de databronnen)

| Fase | Status | Toelichting |
|---|---|---|
| 1. Data-pipeline | Code klaar, **niet gedraaid op echte data** | download, BAG/CBS/OSM inlezen, disaggregatie, validatie. Getest op synthetische data. |
| 2. Baseline-analyse (CLI) | Klaar (demo) | `python -m ovcatch analyse` |
| 3. API | Klaar (demo) | FastAPI, tests slagen |
| 4. Web-UI | Klaar, **niet in browser bekeken** | MapLibre, PDOK-achtergrond; JS syntax-gecheckt, API-flow getest |
| 5. Editor + vergelijking + CSV | Klaar (demo) | |
| 6. Afronding | Gedeeltelijk | README/DECISIONS aanwezig; screenshots ontbreken |

Tests: `make test` → 22 geslaagd (synthetische data, gemockte Valhalla).

## Nog te verifiëren met echte data (zie CLAUDE_CODE_PROMPT.md)
- Download-URL's in `config.yaml` (BAG `bag-light.gpkg`, CBS vk100 2025, wijkenbuurten 2025 — bevat die al kerncijfers?).
- Laag- en kolomnamen in BAG-gpkg (`verblijfsobject`, `gebruiksdoel` als komma-lijst?, statuswaarden) en CBS-gpkg's.
- Valhalla-image op Apple Silicon; afstandscontouren en bus-costing op de gebruikte versie.
- Doorlooptijd/geheugen van `prepare` en `gtfs-index` voor NL.
- UI in de browser: kaart, markers, isochronen, tabellen.

## Bekende beperkingen
- Toewijzing gebruikt drempelbanden + hemelsbrede tie-break, geen exacte netwerkafstand per adres.
- OSM-multipolygonen (relaties) niet meegenomen als voorziening.
- Ringlijnen: haltevolgorde kan afwijken (projectie op shape).
