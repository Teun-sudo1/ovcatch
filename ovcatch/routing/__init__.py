from __future__ import annotations

from ..config import Config
from .base import MODES, CachedRouter, RouteResult, Router
from .euclid import EuclidRouter
from .valhalla import ValhallaRouter


def make_router(cfg: Config, cache: bool = True) -> Router:
    r = cfg["routing"]
    engine = r.get("engine", "valhalla")
    if engine == "valhalla":
        inner = ValhallaRouter(r["valhalla_url"], r.get("timeout_s", 60), r.get("generalize_m", 10))
    elif engine == "euclid":
        inner = EuclidRouter(r.get("euclid_detour_factor", 1.3), r.get("euclid_bus_kmh", 30))
    else:
        raise ValueError(f"Onbekende routing-engine: {engine}")
    return CachedRouter(inner, cfg.cache_dir) if cache else inner


__all__ = ["MODES", "Router", "RouteResult", "make_router", "EuclidRouter", "ValhallaRouter", "CachedRouter"]
