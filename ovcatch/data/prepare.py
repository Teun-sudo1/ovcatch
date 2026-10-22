"""Preprocessing: ruwe bronnen -> GeoParquet in data/processed/."""
from __future__ import annotations

import json
import logging
import math
import shutil

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import box

from ..config import Config
from ..disaggregate import add_age_groups, age_shares, apply_rest, buurt_factors, buurt_summary, disaggregate, report, step_a
from ..geo import bbox_wgs_to_rd
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
        gdf = gdf.assign(_k=cx * 100000 + cy).sort_values("_k").drop(columns="_k").reset_index(drop=True)
    gdf.to_parquet(path, write_covering_bbox=True, row_group_size=50_000)


def build_addresses(cfg: Config, vbo, grid, buurten) -> tuple[gpd.GeoDataFrame, dict]:
    vbo = add_category_area(vbo, cfg["categories"])
    vbo, rep, val = disaggregate(vbo, grid, buurten, cfg)
    vbo = add_age_groups(vbo, age_shares(vbo, grid, buurten, cfg))
    vbo = add_jobs_estimate(vbo, cfg)
    return vbo, _write_report(cfg, rep, val)


def _write_report(cfg: Config, rep, val) -> dict:
    out = rep.to_dict()
    cfg.processed_dir.mkdir(parents=True, exist_ok=True)
    val.to_csv(cfg.processed_dir / "validatie_buurten.csv", index=False)
    with open(cfg.processed_dir / "disaggregatie_rapport.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return out


def tiles_rd(region_rd, tile_m: float) -> list[tuple[float, float, float, float]]:
    """Tegels uitgelijnd op veelvouden van `tile_m` (zelf een veelvoud van 100 m, dus
    een CBS-vierkant valt altijd in precies één tegel)."""
    x0, y0, x1, y1 = region_rd
    xs = range(int(math.floor(x0 / tile_m)), int(math.ceil(x1 / tile_m)))
    ys = range(int(math.floor(y0 / tile_m)), int(math.ceil(y1 / tile_m)))
    return [(i * tile_m, j * tile_m, (i + 1) * tile_m, (j + 1) * tile_m) for i in xs for j in ys]


def _in_tile(xs, ys, t) -> np.ndarray:
    return (xs >= t[0]) & (xs < t[2]) & (ys >= t[1]) & (ys < t[3])


def build_addresses_tiled(cfg: Config, bag_gpkg, vk_gpkg, buurten: gpd.GeoDataFrame) -> dict:
    """Zelfde resultaat als build_addresses, maar per tegel: geheugen blijft klein (heel NL op 16 GB).

    Pas 1 per tegel: BAG + vierkanten lezen, stap A, buurtsommen. Daarna buurtfactoren (stap B/C)
    uit de opgetelde sommen. Pas 2 per tegel: factoren toepassen en wegschrijven.
    """
    tile_m = float(cfg.get("prepare", {}).get("tile_km", 20)) * 1000
    region = bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    tiles = tiles_rd(region or tuple(buurten.total_bounds), tile_m)
    tmp = cfg.processed_dir / "_tegels"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    sums, done = [], []
    stats = {"pop_grid_total": 0.0, "pop_grid_unassigned": 0.0}
    for i, t in enumerate(tiles):
        vbo = read_bag(cfg, bag_gpkg, bbox_rd=t)
        # tegel ∩ regio (bovengrens inclusief, zoals de bbox-filter van de enkele run)
        clip = t if not region else (max(t[0], region[0]), max(t[1], region[1]), min(t[2], region[2] + 1e-6), min(t[3], region[3] + 1e-6))
        vbo = vbo[_in_tile(vbo.geometry.x, vbo.geometry.y, clip)]
        if vbo.empty:
            continue
        grid = read_vk100(cfg, vk_gpkg, bbox_rd=t)
        c = grid.geometry.centroid
        grid = grid[_in_tile(c.x, c.y, t)]  # elk vierkant in precies één tegel
        if region:
            grid = grid[grid.intersects(box(*region))]  # zoals de bbox-filter van de enkele run
        vbo, st = step_a(add_category_area(vbo.reset_index(drop=True), cfg["categories"]), grid, buurten, cfg)
        for k in stats:
            stats[k] += st[k]
        sums.append(buurt_summary(vbo))
        vbo = vbo.join(age_shares(vbo, grid, buurten, cfg))  # toepassen na stap B/C (pas 2)
        vbo["buurtcode"] = vbo["buurtcode"].astype("string")
        vbo["pop_source"] = vbo["pop_source"].astype("string")
        vbo.to_parquet(tmp / f"{i:04d}.parquet")
        done.append(i)
        log.info("Tegel %d/%d: %d objecten", i + 1, len(tiles), len(vbo))
    # vierkanten in tegels zonder BAG-objecten tellen niet mee in pop_grid_total (zee, grensstrook)
    summary = pd.concat(sums).groupby(level=0).sum()
    factors = buurt_factors(summary, buurten, cfg)

    light, writer = [], None
    out_path = cfg.addresses_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = tmp / "part.parquet"
    try:
        for i in done:
            vbo = gpd.read_parquet(tmp / f"{i:04d}.parquet")
            vbo = apply_rest(vbo, factors)
            shares = vbo[[c for c in vbo.columns if c.startswith("share_")]]
            vbo = add_jobs_estimate(add_age_groups(vbo.drop(columns=shares.columns), shares), cfg).drop(columns="_w")
            light.append(pd.DataFrame({c: vbo[c] for c in ("buurtcode", "inwoners", "is_woon", "pop_source")}))
            write_geoparquet(vbo, part)
            table = pq.read_table(part)
            if writer is None:
                meta = json.loads(table.schema.metadata[b"geo"])
                for col in meta["columns"].values():
                    col.pop("bbox", None)  # geldt alleen voor de eerste tegel
                schema = table.schema.with_metadata({**table.schema.metadata, b"geo": json.dumps(meta).encode()})
                writer = pq.ParquetWriter(out_path, schema)
            writer.write_table(table.cast(schema), row_group_size=50_000)
    finally:
        if writer is not None:
            writer.close()
    shutil.rmtree(tmp, ignore_errors=True)
    allv = pd.concat(light, ignore_index=True)
    allv["buurtcode"] = allv["buurtcode"].astype(object).where(allv["buurtcode"].notna(), None)
    rep, val = report(allv, buurten, cfg, stats)
    return {"n": len(allv), "tiles": len(done), "report": _write_report(cfg, rep, val)}


def prepare_all(cfg: Config, steps: list[str] | None = None) -> dict:
    cfg.ensure_dirs()
    p = raw_paths(cfg)
    steps = steps or ["addresses", "amenities"]
    out = {}
    if "addresses" in steps:
        vk_gpkg = unzip_find(p["cbs_vk_zip"], ".gpkg", p["cbs_vk_dir"])
        buurten = read_buurten(cfg, p["cbs_buurten"])
        buurten.to_parquet(cfg.processed_dir / "buurten.parquet")
        out["addresses"] = build_addresses_tiled(cfg, p["bag"], vk_gpkg, buurten)
    if "amenities" in steps:
        am = extract_amenities(cfg, p["osm"])
        write_geoparquet(am, cfg.amenities_path)
        out["amenities"] = {"n": len(am)}
    return out
