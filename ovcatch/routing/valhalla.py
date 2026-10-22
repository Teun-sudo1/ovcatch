"""Valhalla-client: isochronen (lopen/fietsen) en busroutes."""
from __future__ import annotations

import logging

import httpx
from shapely.geometry import shape
from shapely.ops import unary_union

from .base import RouteResult, Router

log = logging.getLogger(__name__)

COSTING = {"walk": "pedestrian", "bike": "bicycle"}
FALLBACK_KMH = {"walk": 5.0, "bike": 15.0}
MAX_CONTOURS = 4
# Haltes liggen vaak tussen twee rijbanen (gemiddelde van perrons): alle wegen binnen deze
# straal zijn kandidaat, zodat de route niet omrijdt naar de 'verkeerde' rijbaan.
ROUTE_RADIUS_M = 25


def decode_polyline6(s: str) -> list[list[float]]:
    """Decodeer een Valhalla polyline (precisie 1e6) naar [[lon, lat], ...]."""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(s):
        for which in (0, 1):
            result, shift = 0, 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / 1e6, lat / 1e6])
    return coords


class ValhallaRouter(Router):
    name = "valhalla"
    approximate = False

    def __init__(self, url: str, timeout_s: float = 60, generalize_m: float = 10, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.generalize = generalize_m
        self.client = client or httpx.Client(timeout=timeout_s)
        self._metric = "distance"  # valt terug op 'time' als de server geen afstandscontouren kent

    def _post(self, path: str, body: dict) -> dict:
        r = self.client.post(f"{self.url}{path}", json=body)
        if r.status_code >= 400:
            raise ValhallaError(r.status_code, r.text[:500])
        return r.json()

    def isochrones(self, lon, lat, mode, distances_m):
        out = {}
        ds = sorted(float(d) for d in distances_m)
        for i in range(0, len(ds), MAX_CONTOURS):
            out.update(self._iso_chunk(lon, lat, mode, ds[i : i + MAX_CONTOURS]))
        return out

    def _iso_chunk(self, lon, lat, mode, ds):
        def body(metric):
            if metric == "distance":
                contours = [{"distance": d / 1000.0} for d in ds]
            else:
                kmh = FALLBACK_KMH[mode]
                contours = [{"time": d / 1000.0 / kmh * 60.0} for d in ds]
            return {
                "locations": [{"lat": lat, "lon": lon}],
                "costing": COSTING[mode],
                "contours": contours,
                "polygons": True,
                "denoise": 0.3,
                "generalize": self.generalize,
            }

        try:
            data = self._post("/isochrone", body(self._metric))
        except ValhallaError as e:
            if self._metric == "distance" and e.status == 400:
                log.warning("Valhalla ondersteunt geen afstandscontouren; terugval op tijd (%s)", e)
                self._metric = "time"
                data = self._post("/isochrone", body("time"))
            else:
                raise
        # Per contour samenvoegen (een contour kan uit meerdere polygonen bestaan),
        # dan op oppervlak sorteren: kleinste contour hoort bij kleinste afstand.
        by_c: dict = {}
        for f in data.get("features", []):
            if f.get("geometry", {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            c = f.get("properties", {}).get("contour")
            by_c.setdefault(c, []).append(shape(f["geometry"]))
        polys = sorted((unary_union(v) for v in by_c.values()), key=lambda g: g.area)
        if len(polys) != len(ds):
            raise ValhallaError(500, f"verwacht {len(ds)} contouren, kreeg {len(polys)}")
        return {d: p for d, p in zip(ds, polys)}

    def route(self, points, break_mask=None):
        if len(points) < 2:
            return RouteResult([list(p) for p in points], 0.0, 0.0)
        break_mask = break_mask or [True] * len(points)
        locs = [
            {"lon": p[0], "lat": p[1], "type": "break" if (b or i in (0, len(points) - 1)) else "through", "radius": ROUTE_RADIUS_M}
            for i, (p, b) in enumerate(zip(points, break_mask))
        ]
        data = self._post(
            "/route",
            {"locations": locs, "costing": "bus", "directions_type": "none", "units": "kilometers"},
        )
        trip = data["trip"]
        coords = []
        for leg in trip["legs"]:
            pts = decode_polyline6(leg["shape"])
            coords.extend(pts if not coords else pts[1:])
        summ = trip["summary"]
        return RouteResult(coords, float(summ["length"]) * 1000.0, float(summ["time"]))

    def health(self):
        try:
            r = self.client.get(f"{self.url}/status", timeout=5)
            return {"ok": r.status_code == 200, "engine": self.name, "url": self.url}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "engine": self.name, "url": self.url, "error": str(e)}


class ValhallaError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Valhalla {status}: {text}")
        self.status = status
