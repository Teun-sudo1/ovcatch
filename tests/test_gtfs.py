from ovcatch.gtfs import Feed, list_feeds, merge_directions


def test_feeds_indexed(demo_cfg):
    assert list_feeds(demo_cfg.gtfs_dir) == ["demo_2024_oud", "demo_2026_nieuw"]


def test_search_and_build_line(demo_cfg):
    f = Feed(demo_cfg.gtfs_dir / "demo_2024_oud")
    rs = f.search_routes("101", demo_cfg["line"]["bus_route_types"])
    assert rs and rs[0]["route_id"] == "R1"
    line = f.build_line("R1", demo_cfg["line"]["merge_distance_m"])
    # 6 haltes per richting, tegenrichting 25 m verschoven -> 6 logische haltes met elk 2 leden
    assert len(line["stops"]) == 6
    assert all(len(s["members"]) == 2 for s in line["stops"])
    assert line["stops"][0]["name"].endswith("Noordeinde")
    assert line["gtfs"]["n_trips"] == 6
    assert line["gtfs"]["duration_s"] == 600
    assert len(line["geometry"]) >= 2


def test_merge_keeps_unmatched_stop_in_order():
    s0 = [
        {"stop_id": "a", "name": "A", "lon": 5.20, "lat": 52.30},
        {"stop_id": "c", "name": "C", "lon": 5.22, "lat": 52.30},
    ]
    s1 = [
        {"stop_id": "c2", "name": "C", "lon": 5.2201, "lat": 52.3001},
        {"stop_id": "b", "name": "B", "lon": 5.21, "lat": 52.3005},  # alleen in tegenrichting
        {"stop_id": "a2", "name": "A", "lon": 5.2001, "lat": 52.3001},
    ]
    geom = [[5.20, 52.30], [5.22, 52.30]]
    out = merge_directions(s0, s1, geom, 150)
    assert [s["name"] for s in out] == ["A", "B", "C"]
    assert out[0]["members"] == ["a", "a2"]
