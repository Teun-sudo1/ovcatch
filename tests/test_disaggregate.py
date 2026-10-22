import numpy as np
import pandas as pd

from ovcatch.data.bag import add_category_area, standardize_vbo
from ovcatch.data.cbs import clean_count, standardize_buurten, standardize_vk
from ovcatch.disaggregate import disaggregate


def _prep(cfg, world):
    vbo_raw, grid_raw, buurten_raw, _ = world
    true = vbo_raw.set_index("vbo_id")["_true_inw"]
    vbo = standardize_vbo(vbo_raw.drop(columns="_true_inw"), cfg)
    vbo = add_category_area(vbo, cfg["categories"])
    grid = standardize_vk(grid_raw, cfg)
    buurten = standardize_buurten(buurten_raw, cfg)
    return vbo, grid, buurten, true


def test_clean_count_secret_codes():
    s = clean_count(pd.Series([10, -99997, -99995, None, 0]))
    assert s.isna().tolist() == [False, True, True, True, False]


def test_status_filter_drops_non_used(cfg, demo_world):
    vbo, *_ = _prep(cfg, demo_world)
    assert "X00001" not in set(vbo["vbo_id"])  # status 'gevormd'


def test_multi_use_area_split(cfg):
    import geopandas as gpd
    from shapely.geometry import Point

    g = gpd.GeoDataFrame({"vbo_id": ["a"], "gebruiksdoel": ["woonfunctie,winkelfunctie"], "oppervlakte": [100.0], "status": ["x"]}, geometry=[Point(0, 0)], crs=28992)
    out = add_category_area(g, cfg["categories"])
    assert bool(out.loc[0, "is_woon"])
    assert out.loc[0, "m2_winkel"] == 50.0


def test_grid_cells_preserved(cfg, demo_world):
    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    out, rep, _ = disaggregate(vbo, grid, buurten, cfg)
    # per geldig vierkant: som toegewezen inwoners == vierkanttotaal
    import geopandas as gpd

    woon = out[out["pop_source"] == "vierkant"]
    j = gpd.sjoin(woon[["inwoners", "geometry"]], grid[["cell_id", "inwoners", "geometry"]].rename(columns={"inwoners": "cel"}), predicate="intersects")
    agg = j.groupby("cell_id").agg(est=("inwoners", "sum"), cel=("cel", "first"))
    assert np.allclose(agg["est"], agg["cel"], atol=1e-6)
    assert rep.pop_grid_unassigned == 0


def test_secret_cells_use_fallback(cfg, demo_world):
    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    out, rep, _ = disaggregate(vbo, grid, buurten, cfg)
    assert grid["inwoners"].isna().any()
    assert rep.by_source.get("buurt_rest", 0) + rep.by_source.get("buurt_gem", 0) > 0
    assert (out.loc[out["is_woon"], "inwoners"] > 0).all()
    assert (out.loc[~out["is_woon"], "inwoners"] == 0).all()


def test_validation_against_buurten(cfg, demo_world):
    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    _, rep, val = disaggregate(vbo, grid, buurten, cfg)
    assert rep.validation["share_within"] >= cfg["disaggregation"]["validation"]["min_share_within"]
    assert len(val) == 4


def test_estimate_close_to_truth_at_cell_level(cfg, demo_world):
    """Onafhankelijke check: op 100m-niveau zit de schatting dicht bij de (geheime) waarheid."""
    vbo, grid, buurten, true = _prep(cfg, demo_world)
    out, _, _ = disaggregate(vbo, grid, buurten, cfg)
    est = out.set_index("vbo_id")["inwoners"]
    t = true.reindex(est.index).fillna(0)
    total_err = abs(est.sum() - t.sum()) / t.sum()
    assert total_err < 0.03


def test_weight_modes(cfg, demo_world):
    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    for mode in ("area", "count", "mix"):
        cfg["disaggregation"]["weight"] = mode
        out, rep, _ = disaggregate(vbo, grid, buurten, cfg)
        assert rep.pop_estimated_total > 0
    cfg["disaggregation"]["weight"] = "area"


def test_buurten_without_woningvoorraad(cfg, demo_world):
    """Eerste publicatie van een CBS-jaargang heeft nog geen woningvoorraad: BAG-telling als noemer."""
    vbo_raw, grid_raw, buurten_raw, _ = demo_world
    won_cols = [c for c in buurten_raw.columns if c.lower() in {x.lower() for x in cfg["columns"]["cbs_buurt_woningen"]}]
    buurten = standardize_buurten(buurten_raw.drop(columns=won_cols), cfg)
    assert buurten["woningen"].isna().all()
    vbo, grid, _, true = _prep(cfg, demo_world)
    out, rep, _ = disaggregate(vbo, grid, buurten, cfg)
    assert rep.validation["share_within"] >= cfg["disaggregation"]["validation"]["min_share_within"]
    assert (out.loc[out["is_woon"], "inwoners"] > 0).all()


def test_validation_skips_buurten_cut_by_region(cfg, demo_world):
    """Randbuurten (deels buiten de regio-bbox) tellen niet mee in de validatie."""
    from ovcatch.disaggregate import validate_against_buurten
    from ovcatch.geo import rd_to_lonlat

    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    out, _, _ = disaggregate(vbo, grid, buurten, cfg)
    assert len(validate_against_buurten(out, buurten, cfg)) == 4
    # bbox die precies één buurt omvat
    x0, y0, x1, y1 = buurten.geometry.iloc[0].buffer(1).bounds
    cfg["region"]["bbox_wgs84"] = [*rd_to_lonlat(x0, y0), *rd_to_lonlat(x1, y1)]
    try:
        val = validate_against_buurten(out, buurten, cfg)
    finally:
        cfg["region"]["bbox_wgs84"] = None
    assert val["buurtcode"].tolist() == [buurten["buurtcode"].iloc[0]]


def test_tiled_equals_single_run(tmp_path, cfg, demo_world):
    """Per tegel verwerken (heel NL) geeft hetzelfde als in één keer."""
    import geopandas as gpd

    from ovcatch.data.prepare import build_addresses, build_addresses_tiled

    vbo_raw, grid_raw, buurten_raw, _ = demo_world
    bag = tmp_path / "bag.gpkg"
    vbo_raw.drop(columns="_true_inw").rename(columns={"vbo_id": "identificatie"}).to_file(bag, layer="verblijfsobject")
    vk = tmp_path / "vk.gpkg"
    grid_raw.to_file(vk, layer="vierkant_100m")
    buurten = standardize_buurten(buurten_raw, cfg)
    vbo, grid, _, _ = _prep(cfg, demo_world)

    cfg = {**cfg, "data_dir": str(tmp_path / "data"), "prepare": {"tile_km": 1}}
    from ovcatch.config import Config

    cfg = Config(cfg)
    single, rep1 = build_addresses(cfg, vbo, grid, buurten)
    out = build_addresses_tiled(cfg, bag, vk, buurten)
    tiled = gpd.read_parquet(cfg.addresses_path)
    assert out["tiles"] > 10 and out["n"] == len(single) == len(tiled)
    a = single.set_index("vbo_id")["inwoners"].sort_index()
    b = tiled.set_index("vbo_id")["inwoners"].sort_index()
    assert np.allclose(a.to_numpy(), b.reindex(a.index).to_numpy())
    assert out["report"]["by_source"] == rep1["by_source"]
    assert np.isclose(single["inw_65plus"].sum(), tiled["inw_65plus"].sum())
    assert gpd.read_parquet(cfg.addresses_path, bbox=(143000, 476000, 143500, 476500)).shape[0] < len(tiled)


def test_age_groups_cell_then_buurt_then_national(cfg, demo_world):
    from ovcatch.disaggregate import add_age_groups, age_shares

    vbo, grid, buurten, _ = _prep(cfg, demo_world)
    for g, share in {"0_15": 0.2, "15_25": 0.15, "25_45": 0.3, "45_65": 0.2, "65plus": 0.15}.items():
        grid[f"age_{g}"] = grid["inwoners"] * share  # noemer = som van alle groepen
    secret = grid.index[::3]
    grid.loc[secret, "age_0_15"] = np.nan  # leeftijd geheim, inwoners bekend
    buurten["pct_0_15"] = 0.1
    buurten.loc[buurten.index[0], "pct_0_15"] = np.nan  # ook buurt onbekend -> landelijk
    out, _, _ = disaggregate(vbo, grid, buurten, cfg)
    sh = age_shares(out, grid, buurten, cfg)["share_0_15"]
    woon = out["is_woon"].astype(bool)
    vals = set(np.round(sh[woon], 6))
    assert vals <= {0.2, 0.1, round(cfg["disaggregation"]["national_age_shares"]["0_15"], 6)} and len(vals) == 3
    out = add_age_groups(out, age_shares(out, grid, buurten, cfg))
    assert (out["inw_0_15"] <= out["inwoners"] + 1e-9).all() and out.loc[~woon, "inw_0_15"].eq(0).all()
    # 65+: in vierkanten met alle groepen bekend 15%, elders (geheim, geen buurtcijfer) landelijk
    r65 = out.loc[woon, "inw_65plus"].sum() / out.loc[woon, "inwoners"].sum()
    assert 0.15 <= r65 <= cfg["disaggregation"]["national_age_shares"]["65plus"]



def test_age_shares_robust_to_cbs_rounding(cfg):
    """CBS rondt elke leeftijdsgroep af op 5: bij 5 inwoners staat elke groep op 5. Delen door de som
    van de groepen (niet door het inwonertal) voorkomt dat kleine vierkanten 100% per groep krijgen."""
    import geopandas as gpd
    from shapely.geometry import Point, box

    from ovcatch.disaggregate import age_shares

    vbo = gpd.GeoDataFrame({"is_woon": [True], "buurtcode": ["B1"]}, geometry=[Point(50, 50)], crs=28992)
    grid = gpd.GeoDataFrame({"inwoners": [5.0], "age_0_15": [5.0], "age_15_25": [5.0], "age_25_45": [5.0], "age_45_65": [5.0], "age_65plus": [5.0]},
                            geometry=[box(0, 0, 100, 100)], crs=28992)
    buurten = gpd.GeoDataFrame({"buurtcode": ["B1"]}, geometry=[box(0, 0, 100, 100)], crs=28992)
    sh = age_shares(vbo, grid, buurten, cfg)
    assert np.allclose(sh.iloc[0].to_numpy(), 0.2)
