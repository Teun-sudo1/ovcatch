import json

from ovcatch.webexport import encode_polyline, export_web


def test_encode_polyline_reference():
    # voorbeeld uit de documentatie van het Google polyline-formaat (lat, lon)
    assert encode_polyline([(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_export_demo(demo_cfg, tmp_path):
    out = tmp_path / "web"
    res = export_web(demo_cfg, "demo_2026_nieuw", out, processes=2)
    assert res["n_haltes"] > 0 and res["n_lijnen"] == 1
    meta = json.loads((out / "meta.json").read_text())
    stops = json.loads((out / "stops.json").read_text())["rows"]
    lines = json.loads((out / "lines.json").read_text())["rows"]
    n_metrics = len(meta["kengetallen"])
    n_vals = sum(len(t) for t in meta["drempels"].values()) * n_metrics
    assert all(len(s[3]) == n_vals for s in stops)
    # haltes: oplopend met drempel; lijn: uniek totaal <= som bruto per halte en >= grootste halte
    walk = meta["drempels"]["walk"]
    for s in stops:
        inw = [s[3][b * n_metrics] for b in range(len(walk))]
        assert inw == sorted(inw)
    line = lines[0]
    for b in range(len(walk)):
        per_stop = [stops[a][3][b * n_metrics] for a in line[3]]
        assert max(per_stop) - 1 <= line[4][b * n_metrics] <= sum(per_stop) + len(per_stop)  # afronding
    assert list((out / "iso").glob("*.json"))
    # frequentie: demo heeft 6 ritten per richting, 4 in de spits (07–09) -> 12 per dag, 2 per uur per richting
    assert meta["referentiedag"] == "2026-10-01"
    assert line[7] == [12, 2.0]
    assert max(s[6][0] for s in stops) > 0
    # leeftijdsgroepen staan in de waarden en zijn deel van de inwoners
    k = meta["kengetallen"]
    for s in stops:
        assert s[3][k.index("inw_65plus")] <= s[3][k.index("inwoners")] + 1


def test_cells_match_address_totals(demo_cfg, tmp_path):
    import geopandas as gpd

    from ovcatch.webexport import export_cells

    res = export_cells(demo_cfg, tmp_path)
    cells = [json.loads(f.read_text()) for f in (tmp_path / "cells").glob("*.json")]
    assert res["n_vierkanten"] == sum(len(c["v"]) for c in cells)
    ad = gpd.read_parquet(demo_cfg.addresses_path)
    inw = sum(v[0] for c in cells for v in c["v"])
    assert abs(inw - ad["inwoners"].sum()) < 0.05 * len(cells[0]["v"]) + 1  # afronding op 0,1 per vierkant
    assert sum(v[4] for c in cells for v in c["v"]) == len(ad)
    assert sum(v[1] for c in cells for v in c["v"]) == int(ad["is_woon"].sum())
