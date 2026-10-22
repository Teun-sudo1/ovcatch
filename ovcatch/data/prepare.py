"""Preprocessing: ruwe bronnen -> GeoParquet in data/processed/."""
from __future__ import annotations

import json
import logging

import geopandas as gpd
import numpy as np

from ..config import Config
from ..disaggregate import disaggregate
from .bag import add_category_area, read_bag
from .cbs import read_buurten, read_vk100
from .download import raw_paths, unzip_find
from .osm import extract_amenities

log = logging.getLogger(__name__)


def add_jobs_estimate(gdf, cfg: Config):
    ratios = cfg.get("jobs_m2_per_job", {})
    jobs = np.zeros(len(gdf))
    for cat, m2 in ratios.items():
        col = f"m2_{cat}"
        if col in gdf.columns and m2:
            jobs += gdf[col].to_numpy() / float(m2)
    gdf["banen_schatting"] = jobs
    return gdf


def write_geoparquet(gdf: gpd.GeoDataFrame, path) -> None:
    """Ruimtelijk gesorteerd + bbox-kolom, zodat lezen met bbox snel is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(gdf):
        cx = (gdf.geometry.x // 1000).astype(int)
        cy = (gdf.geometry.y // 1000).astype(int)
        gdf = gdf.assign(_k=cx * 100000 + cy).sort_values("_k").drop(columns="_k")
    gdf.to_parquet(path, write_covering_bbox=True, row_group_size=50_000)


def build_addresses(cfg: Config, vbo, grid, buurten) -> tuple[gpd.GeoDataFrame, dict]:
    vbo = add_category_area(vbo, cfg["categories"])
    vbo, rep, val = disaggregate(vbo, grid, buurten, cfg)
    vbo = add_jobs_estimate(vbo, cfg)
    report = rep.to_dict()
    cfg.processed_dir.mkdir(parents=True, exist_ok=True)
    val.to_csv(cfg.processed_dir / "validatie_buurten.csv", index=False)
    with open(cfg.processed_dir / "disaggregatie_rapport.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return vbo, report


def prepare_all(cfg: Config, steps: list[str] | None = None) -> dict:
    cfg.ensure_dirs()
    p = raw_paths(cfg)
    steps = steps or ["addresses", "amenities"]
    out = {}
    if "addresses" in steps:
        vbo = read_bag(cfg, p["bag"])
        vk_gpkg = unzip_find(p["cbs_vk_zip"], ".gpkg", p["cbs_vk_dir"])
        grid = read_vk100(cfg, vk_gpkg)
        buurten = read_buurten(cfg, p["cbs_buurten"])
        buurten.to_parquet(cfg.processed_dir / "buurten.parquet")
        addresses, report = build_addresses(cfg, vbo, grid, buurten)
        write_geoparquet(addresses, cfg.addresses_path)
        out["addresses"] = {"n": len(addresses), "report": report}
    if "amenities" in steps:
        am = extract_amenities(cfg, p["osm"])
        write_geoparquet(am, cfg.amenities_path)
        out["amenities"] = {"n": len(am)}
    return out
