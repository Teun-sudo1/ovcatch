"""Router-interface + caching."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape, mapping
from shapely.geometry.base import BaseGeometry

MODES = ("walk", "bike")


@dataclass
class RouteResult:
    coords: list[list[float]]  # [[lon, lat], ...]
    length_m: float
    duration_s: float


class Router:
    """Basisklasse. Alle geometrie in en uit in WGS84 (lon/lat)."""

    name = "base"
    approximate = False

    def isochrones(self, lon: float, lat: float, mode: str, distances_m: list[float]) -> dict[float, BaseGeometry]:
        raise NotImplementedError

    def route(self, points: list[tuple[float, float]], break_mask: list[bool] | None = None) -> RouteResult:
        raise NotImplementedError

    def health(self) -> dict:
        return {"ok": True, "engine": self.name}


class CachedRouter(Router):
    """Schijfcache rond isochronen (routes worden niet gecachet; die veranderen bij elke bewerking)."""

    def __init__(self, inner: Router, cache_dir: Path):
        self.inner = inner
        self.name = inner.name
        self.approximate = inner.approximate
        self.cache_dir = Path(cache_dir) / "isochrones" / inner.name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, dict] = {}

    def _key(self, lon, lat, mode, distances):
        raw = json.dumps([round(lon, 6), round(lat, 6), mode, sorted(float(d) for d in distances)])
        return hashlib.sha1(raw.encode()).hexdigest()

    def isochrones(self, lon, lat, mode, distances_m):
        k = self._key(lon, lat, mode, distances_m)
        if k in self._mem:
            return {float(d): shape(g) for d, g in self._mem[k].items()}
        f = self.cache_dir / f"{k}.json"
        if f.exists():
            data = json.loads(f.read_text())
            self._mem[k] = data
            return {float(d): shape(g) for d, g in data.items()}
        res = self.inner.isochrones(lon, lat, mode, distances_m)
        data = {str(d): mapping(g) for d, g in res.items()}
        # atomair schrijven: analyses halen isochronen parallel op
        tmp = f.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(f)
        self._mem[k] = data
        return res

    def route(self, points, break_mask=None):
        return self.inner.route(points, break_mask)

    def health(self):
        return self.inner.health()
