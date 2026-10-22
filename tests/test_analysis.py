import copy

import pytest

from ovcatch.analysis import analyse, compare, comparison_csv, route_points
from ovcatch.gtfs import Feed
from ovcatch.routing import EuclidRouter
from ovcatch.store import DataStore


@pytest.fixture(scope="module")
def ctx(demo_cfg):
    store = DataStore(demo_cfg.addresses_path, demo_cfg.amenities_path)
    router = EuclidRouter()
    old = Feed(demo_cfg.gtfs_dir / "demo_2024_oud").build_line("R1")
    new = Feed(demo_cfg.gtfs_dir / "demo_2026_nieuw").build_line("R1")
    return demo_cfg, store, router, old, new


def test_no_double_counting(ctx):
    cfg, store, router, old, _ = ctx
    res = analyse(old, cfg, router, store)
    for mode, m in res["modes"].items():
        for t in m["line"]:
            line_inw = m["line"][t]["inwoners"]
            assigned = sum(s["by_threshold"][t]["assigned"]["inwoners"] for s in m["per_stop"])
            raw = sum(s["by_threshold"][t]["raw"]["inwoners"] for s in m["per_stop"])
            assert line_inw == pytest.approx(assigned, abs=0.5)
            assert raw >= line_inw - 0.5


def test_thresholds_monotonic(ctx):
    cfg, store, router, old, _ = ctx
    res = analyse(old, cfg, router, store)
    for m in res["modes"].values():
        vals = [m["line"][str(t)]["inwoners"] for t in m["thresholds"]]
        assert vals == sorted(vals)


def test_old_route_serves_more_walkers(ctx):
    """De demo is zo opgezet dat de oude route door de kernen meer inwoners op loopafstand heeft."""
    cfg, store, router, old, new = ctx
    a = analyse(old, cfg, router, store)
    b = analyse(new, cfg, router, store)
    assert a["modes"]["walk"]["line"]["400"]["inwoners"] > 2 * b["modes"]["walk"]["line"]["400"]["inwoners"]
    cmp = compare(a, b)
    row = next(r for r in cmp["line"] if r["mode"] == "walk" and r["threshold"] == 400 and r["metric"] == "inwoners")
    assert row["delta"] < 0 and row["pct"] < -50
    assert set(cmp["stop_changes"]["kept"]) == {"Noordeinde", "Bedrijvenpark"}
    csv = comparison_csv(cmp)
    assert "Lijntotalen" in csv and ";" in csv


def test_moving_stop_into_village_increases_catchment(ctx):
    cfg, store, router, _, new = ctx
    base = analyse(new, cfg, router, store, with_points=False)
    v = copy.deepcopy(new)
    v["modified"] = True
    # 'Rondweg Zuid' naar het centrum van Dorp Zuid verplaatsen
    from ovcatch.demo import X0, Y0
    from ovcatch.geo import rd_to_lonlat

    lon, lat = rd_to_lonlat(X0 + 300, Y0 - 900)
    idx = next(i for i, s in enumerate(v["stops"]) if "Rondweg Zuid" in s["name"])
    v["stops"][idx]["lon"], v["stops"][idx]["lat"] = lon, lat
    res = analyse(v, cfg, router, store, with_points=False)
    assert res["modes"]["walk"]["line"]["800"]["inwoners"] > base["modes"]["walk"]["line"]["800"]["inwoners"] + 1000


def test_route_points_with_vias():
    sc = {"stops": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 1}, {"lon": 2, "lat": 2}], "vias": [{"after": 0, "lon": 0.5, "lat": 0.4}]}
    pts, mask = route_points(sc)
    assert pts == [(0, 0), (0.5, 0.4), (1, 1), (2, 2)]
    assert mask == [True, False, True, True]


def test_empty_scenario(ctx):
    cfg, store, router, _, _ = ctx
    res = analyse({"stops": []}, cfg, router, store)
    assert res["modes"]["walk"]["line"]["400"]["inwoners"] == 0
