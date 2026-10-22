"""Coördinaattransformaties en kleine geometrie-hulpjes."""
from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer
import shapely
from shapely.geometry.base import BaseGeometry

RD = "EPSG:28992"
WGS84 = "EPSG:4326"


@lru_cache(maxsize=4)
def _tf(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def to_rd(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, _tf(WGS84, RD).transform, interleaved=False)


def to_wgs(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, _tf(RD, WGS84).transform, interleaved=False)


def lonlat_to_rd(lon: float, lat: float) -> tuple[float, float]:
    return _tf(WGS84, RD).transform(lon, lat)


def rd_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return _tf(RD, WGS84).transform(x, y)


def bbox_wgs_to_rd(bbox: list[float] | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    from shapely.geometry import box

    return to_rd(box(*bbox)).bounds
