from pathlib import Path

from fastapi.testclient import TestClient

from ovcatch.api.app import create_app


def test_api_flow(demo_cfg):
    c = TestClient(create_app(demo_cfg))
    conf = c.get("/api/config").json()
    assert conf["demo"] and conf["data_available"] and conf["router"]["approximate"]
    assert c.get("/api/feeds").json() == ["demo_2024_oud", "demo_2026_nieuw"]
    base = c.get("/api/line", params={"feed": "demo_2024_oud", "route_id": "R1"}).json()
    var = c.get("/api/line", params={"feed": "demo_2026_nieuw", "route_id": "R1"}).json()
    r = c.post("/api/route", json=var)
    assert r.status_code == 200 and r.json()["length_m"] > 0
    r = c.post("/api/compare", json={"baseline": base, "variant": var})
    assert r.status_code == 200
    body = r.json()
    assert body["variant"]["isochrones"]["features"]
    assert body["variant"]["points"]["walk"]["rows"]
    r = c.post("/api/export.csv", json={"baseline": base, "variant": var})
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert c.put("/api/scenarios/mijn scenario", json=var).json()["saved"] == "mijn_scenario"
    assert "mijn_scenario" in c.get("/api/scenarios").json()
    assert c.get("/api/scenarios/mijn_scenario").json()["route_id"] == "R1"
    assert c.get("/api/line", params={"feed": "bestaat_niet", "route_id": "R1"}).status_code == 404
    assert c.get("/").status_code == 200


OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="52.30" lon="5.25" version="1"><tag k="amenity" v="school"/><tag k="name" v="De Test"/></node>
  <node id="2" lat="52.31" lon="5.26" version="1"><tag k="shop" v="bakery"/></node>
  <node id="3" lat="52.300" lon="5.240" version="1"/>
  <node id="4" lat="52.300" lon="5.242" version="1"/>
  <node id="5" lat="52.302" lon="5.242" version="1"/>
  <way id="10" version="1"><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="3"/><tag k="shop" v="supermarket"/></way>
  <node id="6" lat="60.0" lon="20.0" version="1"><tag k="amenity" v="school"/></node>
</osm>"""


def test_osm_amenities(tmp_path: Path, cfg):
    from ovcatch.data.osm import extract_amenities

    f = tmp_path / "t.osm"
    f.write_text(OSM_XML)
    cfg["region"]["bbox_wgs84"] = [5.0, 52.0, 5.5, 52.5]
    gdf = extract_amenities(cfg, f)
    assert sorted(gdf["categorie"]) == ["onderwijs", "supermarkt"]
    assert gdf.crs.to_epsg() == 28992


def test_osm_amenities_multiple_files_dedup(tmp_path: Path, cfg):
    """Overlappende extracten (bijv. NH + Utrecht) geven geen dubbele voorzieningen."""
    from ovcatch.data.osm import extract_amenities

    a, b = tmp_path / "a.osm", tmp_path / "b.osm"
    a.write_text(OSM_XML)
    b.write_text(OSM_XML.replace('<node id="6"', '<node id="7" lat="52.2" lon="5.3" version="1"><tag k="shop" v="supermarket"/></node>\n  <node id="6"'))
    cfg["region"]["bbox_wgs84"] = [5.0, 52.0, 5.5, 52.5]
    gdf = extract_amenities(cfg, [a, b])
    assert sorted(gdf["osm_id"]) == ["n1", "n7", "w10"]


def test_osm_urls_single_or_list(cfg):
    from ovcatch.data.download import osm_urls, raw_paths

    cfg["region"].pop("osm_pbf_urls", None)
    cfg["region"]["osm_pbf_url"] = "https://x/a-latest.osm.pbf"
    assert osm_urls(cfg) == ["https://x/a-latest.osm.pbf"]
    cfg["region"]["osm_pbf_urls"] = ["https://x/a.osm.pbf", "https://x/b.osm.pbf"]
    assert [p.name for p in raw_paths(cfg)["osm"]] == ["a.osm.pbf", "b.osm.pbf"]
