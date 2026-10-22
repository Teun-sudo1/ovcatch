"""Benaderende router zonder netwerk: cirkels en rechte lijnen.

Alleen voor demo, tests en als Valhalla (nog) niet draait. De UI toont dan een
waarschuwing dat het om een benadering gaat.
"""
from __future__ import annotations

from shapely.geometry import LineString, Point

from ..geo import lonlat_to_rd, to_wgs
from .base import RouteResult, Router


class EuclidRouter(Router):
    name = "euclid"
    approximate = True

    def __init__(self, detour_factor: float = 1.3, bus_kmh: float = 30.0):
        self.detour = detour_factor
        self.bus_ms = bus_kmh / 3.6

    def isochrones(self, lon, lat, mode, distances_m):
        x, y = lonlat_to_rd(lon, lat)
        return {float(d): to_wgs(Point(x, y).buffer(float(d) / self.detour, quad_segs=16)) for d in distances_m}

    def route(self, points, break_mask=None):
        if len(points) < 2:
            return RouteResult([list(p) for p in points], 0.0, 0.0)
        rd = [lonlat_to_rd(*p) for p in points]
        length = LineString(rd).length * self.detour
        return RouteResult([list(p) for p in points], length, length / self.bus_ms)

    def health(self):
        return {"ok": True, "engine": self.name, "approximate": True}
