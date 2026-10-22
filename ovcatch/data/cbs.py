"""CBS 100m-vierkanten en buurten inlezen en standaardiseren."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

from ..config import Config, pick_column
from ..geo import RD, bbox_wgs_to_rd

log = logging.getLogger(__name__)


def clean_count(s: pd.Series) -> pd.Series:
    """CBS-codes voor geheim/onbekend (negatief, bijv. -99997) -> NaN."""
    v = pd.to_numeric(s, errors="coerce").astype(float)
    return v.where(v >= 0)


def _first_layer(path: Path, preferred: str | None = None) -> str:
    layers = [name for name, _ in pyogrio.list_layers(path)]
    if preferred and preferred in layers:
        return preferred
    if preferred:
        for l in layers:
            if preferred.lower() in l.lower():
                return l
    return layers[0]


def read_vk100(cfg: Config, gpkg: Path, bbox_rd=None) -> gpd.GeoDataFrame:
    bbox = bbox_rd or bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    layer = _first_layer(gpkg, "vierkant")
    gdf = pyogrio.read_dataframe(gpkg, layer=layer, bbox=bbox)
    return standardize_vk(gdf, cfg)


def standardize_vk(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    c = cfg["columns"]
    inw = pick_column(gdf.columns, c["cbs_vk_inwoners"], "CBS vierkant inwoners")
    try:
        won = pick_column(gdf.columns, c["cbs_vk_woningen"], "CBS vierkant woningen")
    except KeyError:
        won = None
    out = gpd.GeoDataFrame(
        {
            "cell_id": np.arange(len(gdf)),
            "inwoners": clean_count(gdf[inw]).values,
            "woningen": clean_count(gdf[won]).values if won else np.nan,
        },
        geometry=gdf.geometry.values,
        crs=gdf.crs or RD,
    ).to_crs(RD)
    log.info("CBS vierkanten: %d (geheim/onbekend: %d)", len(out), out["inwoners"].isna().sum())
    return out


def read_buurten(cfg: Config, gpkg: Path) -> gpd.GeoDataFrame:
    bbox = bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    layer = _first_layer(gpkg, cfg["sources"].get("cbs_buurten_layer", "buurten"))
    gdf = pyogrio.read_dataframe(gpkg, layer=layer, bbox=bbox)
    return standardize_buurten(gdf, cfg)


def standardize_buurten(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    c = cfg["columns"]
    code = pick_column(gdf.columns, c["cbs_buurt_code"], "buurtcode")
    try:
        naam = pick_column(gdf.columns, c["cbs_buurt_naam"], "buurtnaam")
    except KeyError:
        naam = code
    inw = pick_column(gdf.columns, c["cbs_buurt_inwoners"], "buurt inwoners")
    # Woningvoorraad ontbreekt in de eerste publicatie van een jaargang (bijv. 2025);
    # dan telt disaggregate() de BAG-woningen per buurt.
    try:
        won = pick_column(gdf.columns, c["cbs_buurt_woningen"], "buurt woningen")
    except KeyError:
        log.warning("CBS buurten: geen kolom woningvoorraad; BAG-woningen worden gebruikt")
        won = None
    out = gpd.GeoDataFrame(
        {
            "buurtcode": gdf[code].astype(str).values,
            "buurtnaam": gdf[naam].astype(str).values,
            "inwoners": clean_count(gdf[inw]).values,
            "woningen": clean_count(gdf[won]).values if won else np.nan,
        },
        geometry=gdf.geometry.values,
        crs=gdf.crs or RD,
    ).to_crs(RD)
    # Water-buurten zonder inwoners zijn niet interessant
    log.info("CBS buurten: %d (inwoners geheim: %d)", len(out), out["inwoners"].isna().sum())
    return out
