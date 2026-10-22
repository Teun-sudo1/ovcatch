import json

import httpx
from shapely.geometry import Point, mapping

from ovcatch.geo import lonlat_to_rd, to_wgs
from ovcatch.routing import CachedRouter, EuclidRouter, ValhallaRouter
from ovcatch.routing.valhalla import decode_polyline6


def _encode6(coords):
    """Minimale polyline6-encoder voor de test ([lon, lat] -> string)."""
    out, plat, plon = [], 0, 0
    for lon, lat in coords:
        ilat, ilon = round(lat * 1e6), round(lon * 1e6)
        for d in (ilat - plat, ilon - plon):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def test_polyline_roundtrip():
    c = [[5.123456, 52.654321], [5.2, 52.7], [5.19, 52.71]]
    assert decode_polyline6(_encode6(c)) == c


def _fake_valhalla(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if request.url.path == "/isochrone":
        lat, lon = body["locations"][0]["lat"], body["locations"][0]["lon"]
        x, y = lonlat_to_rd(lon, lat)
        feats = []
        for c in reversed(body["contours"]):  # Valhalla: groot -> klein
            d = c["distance"] * 1000
            feats.append({"type": "Feature", "properties": {"contour": c["distance"], "metric": "distance"}, "geometry": mapping(to_wgs(Point(x, y).buffer(d)))})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})
    if request.url.path == "/route":
        locs = body["locations"]
        assert body["costing"] == "bus"
        assert locs[1]["type"] == "through"
        coords = [[l["lon"], l["lat"]] for l in locs]
        return httpx.Response(200, json={"trip": {"legs": [{"shape": _encode6(coords)}], "summary": {"length": 3.2, "time": 420}}})
    if request.url.path == "/status":
        return httpx.Response(200, json={})
    return httpx.Response(404)


def test_valhalla_client(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(_fake_valhalla))
    r = ValhallaRouter("http://valhalla", client=client)
    isos = r.isochrones(5.25, 52.3, "walk", [400, 800, 1200, 1600, 2000])  # >4 contouren -> 2 requests
    assert sorted(isos) == [400, 800, 1200, 1600, 2000]
    assert isos[400].area < isos[2000].area
    rr = r.route([(5.2, 52.3), (5.21, 52.31), (5.22, 52.3)], [True, False, True])
    assert rr.length_m == 3200 and rr.duration_s == 420 and len(rr.coords) == 3
    assert r.health()["ok"]
    cached = CachedRouter(r, tmp_path)
    a = cached.isochrones(5.25, 52.3, "bike", [1500, 3000])
    b = cached.isochrones(5.25, 52.3, "bike", [1500, 3000])
    assert a[1500].equals_exact(b[1500], 1e-9)


def test_euclid_router():
    r = EuclidRouter(detour_factor=1.0)
    iso = r.isochrones(5.25, 52.3, "walk", [500])
    from ovcatch.geo import to_rd

    assert abs(to_rd(iso[500]).area - 3.1416 * 500**2) / (3.1416 * 500**2) < 0.02
