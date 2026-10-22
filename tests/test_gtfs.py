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


def test_search_matches_word_start():
    import pandas as pd

    from ovcatch.gtfs import Feed

    f = Feed.__new__(Feed)
    f.routes = pd.DataFrame(
        {
            "route_id": ["a", "b", "c"],
            "route_short_name": ["100", "438", "7"],
            "route_long_name": ["Bussum - Huizen - Hilversum", "Enkhuizen - Andijk", "Paddepoel - Vinkhuizen"],
            "agency_name": ["Transdev", "Connexxion", "Qbuzz"],
            "route_type": [3, 3, 3],
        }
    )
    f.patterns = pd.DataFrame({"route_id": ["a", "b", "c"]})
    assert [r["route_id"] for r in f.search_routes("huizen")] == ["a"]
    assert [r["route_id"] for r in f.search_routes("hilv")] == ["a"]
    assert [r["route_id"] for r in f.search_routes("438")] == ["b"]


def test_same_name_platforms_across_junction_merge(cfg):
    """Echte casus (Huizen, Aristoteleslaan; Hilversum, Orionlaan): perrons per richting ~180–210 m uiteen."""
    s0 = [{"stop_id": "a", "name": "Orionlaan", "lon": 5.1900, "lat": 52.2300}]
    s1 = [{"stop_id": "b", "name": "Orionlaan", "lon": 5.1930, "lat": 52.2300}]  # ~205 m oostelijk
    out = merge_directions(s0, s1, [[5.18, 52.23], [5.20, 52.23]], cfg["line"]["merge_distance_m"])
    assert len(out) == 1 and out[0]["members"] == ["a", "b"]


def test_build_line_marks_directions(demo_cfg):
    f = Feed(demo_cfg.gtfs_dir / "demo_2024_oud")
    line = f.build_line("R1", demo_cfg["line"]["merge_distance_m"])
    assert all(s["dirs"] == [0, 1] for s in line["stops"])


def test_route_points_skip_return_only_stops():
    """Een halte die alleen in de terugrichting ligt hoort niet in de gereden route (wel in het bereik)."""
    from ovcatch.analysis import route_points

    sc = {
        "stops": [
            {"lon": 5.0, "lat": 52.0, "dirs": [0, 1]},
            {"lon": 5.1, "lat": 52.0, "dirs": [1]},  # perron aan de overkant
            {"lon": 5.11, "lat": 52.0, "dirs": [0]},
            {"lon": 5.2, "lat": 52.0},  # nieuwe halte: geen richting -> altijd mee
        ],
        "vias": [{"after": 1, "lon": 5.15, "lat": 52.01}],
    }
    pts, mask = route_points(sc)
    assert pts == [(5.0, 52.0), (5.15, 52.01), (5.11, 52.0), (5.2, 52.0)]
    assert mask == [True, False, True, True]


def test_frequencies_on_reference_weekday(demo_cfg):
    f = Feed(demo_cfg.gtfs_dir / "demo_2024_oud")
    # di/wo/do met de meeste ritten; bij gelijkspel de vroegste: do 1 oktober 2026
    assert f.ref_date == "2026-10-01"
    rt = f.route_trips.set_index("direction_id")
    assert rt.loc[0, "trips_day"] == 6 and rt.loc[0, "trips_peak"] == 4  # 07:00..09:30, spits 07–09
    dep = f.stop_departures.set_index("stop_id")["dep_day"]
    assert dep["Noordeinde"] == 6  # beginhalte richting 0
    first_dir1 = f.patterns[f.patterns.direction_id == 1].pattern.iloc[0].split("|")
    assert first_dir1[-1] not in dep.index or dep[first_dir1[-1]] == 0  # eindhalte: geen vertrekken
