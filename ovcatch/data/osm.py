"""Voorzieningen uit OSM halen (nodes + ways; ways als zwaartepunt van hun nodes).

Multipolygon-relaties worden overgeslagen; in NL zijn de meeste voorzieningen
nodes of gesloten ways, dus dit effect is klein (zie DECISIONS.md).
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import osmium

from ..config import Config
from ..geo import RD, WGS84

log = logging.getLogger(__name__)


def _parse_tag_rules(amenities: dict[str, list[str]]) -> dict[tuple[str, str], str]:
    rules = {}
    for cat, tags in amenities.items():
        for t in tags:
            k, v = t.split("=", 1)
            rules[(k.strip(), v.strip())] = cat
    return rules


class _Handler(osmium.SimpleHandler):
    def __init__(self, rules, bbox):
        super().__init__()
        self.rules = rules
        self.keys = {k for k, _ in rules}
        self.bbox = bbox
        self.rows = []

    def _match(self, tags):
        for k in self.keys:
            v = tags.get(k)
            if v is not None and (k, v) in self.rules:
                return self.rules[(k, v)], f"{k}={v}"
        return None, None

    def _in_bbox(self, lon, lat):
        if not self.bbox:
            return True
        x0, y0, x1, y1 = self.bbox
        return x0 <= lon <= x1 and y0 <= lat <= y1

    def node(self, n):
        cat, tag = self._match(n.tags)
        if cat and n.location.valid() and self._in_bbox(n.location.lon, n.location.lat):
            self.rows.append((f"n{n.id}", cat, tag, n.tags.get("name", ""), n.location.lon, n.location.lat))

    def way(self, w):
        cat, tag = self._match(w.tags)
        if not cat:
            return
        xs, ys = [], []
        for nd in w.nodes:
            if nd.location.valid():
                xs.append(nd.location.lon)
                ys.append(nd.location.lat)
        if xs:
            lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
            if self._in_bbox(lon, lat):
                self.rows.append((f"w{w.id}", cat, tag, w.tags.get("name", ""), lon, lat))


def extract_amenities(cfg: Config, osm_file: Path) -> gpd.GeoDataFrame:
    rules = _parse_tag_rules(cfg["amenities"])
    h = _Handler(rules, cfg["region"].get("bbox_wgs84"))
    log.info("OSM-voorzieningen extraheren uit %s", osm_file)
    h.apply_file(str(osm_file), locations=True)
    if not h.rows:
        return gpd.GeoDataFrame(columns=["osm_id", "categorie", "tag", "naam", "geometry"], geometry="geometry", crs=RD)
    ids, cats, tags, names, lons, lats = zip(*h.rows)
    gdf = gpd.GeoDataFrame(
        {"osm_id": ids, "categorie": cats, "tag": tags, "naam": names},
        geometry=gpd.points_from_xy(lons, lats),
        crs=WGS84,
    ).to_crs(RD)
    log.info("OSM-voorzieningen: %d", len(gdf))
    return gdf
