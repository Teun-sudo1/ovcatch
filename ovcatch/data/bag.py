"""BAG verblijfsobjecten inlezen en standaardiseren."""
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

STANDARD_COLS = ["vbo_id", "gebruiksdoel", "oppervlakte", "status", "geometry"]


def read_bag(cfg: Config, gpkg: Path) -> gpd.GeoDataFrame:
    """Lees verblijfsobjecten (+ optioneel lig-/standplaatsen) binnen de regio."""
    bbox = bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    layers = {name for name, _ in pyogrio.list_layers(gpkg)}
    vbo_layer = "verblijfsobject" if "verblijfsobject" in layers else _guess_layer(layers, "verblijfsobject")
    info = pyogrio.read_info(gpkg, layer=vbo_layer)
    fields = list(info["fields"])
    c = cfg["columns"]
    col_doel = pick_column(fields, c["bag_gebruiksdoel"], "BAG gebruiksdoel")
    col_opp = pick_column(fields, c["bag_oppervlakte"], "BAG oppervlakte")
    col_status = pick_column(fields, c["bag_status"], "BAG status")
    col_id = pick_column(fields, c["bag_id"], "BAG id")
    log.info("BAG lezen (laag %s, bbox %s)", vbo_layer, bbox)
    gdf = pyogrio.read_dataframe(gpkg, layer=vbo_layer, columns=[col_id, col_doel, col_opp, col_status], bbox=bbox)
    gdf = gdf.rename(columns={col_id: "vbo_id", col_doel: "gebruiksdoel", col_opp: "oppervlakte", col_status: "status"})
    gdf = standardize_vbo(gdf, cfg)

    if cfg["bag"].get("include_lig_standplaats"):
        extra = []
        for layer in ("ligplaats", "standplaats"):
            if layer not in layers:
                continue
            g = pyogrio.read_dataframe(gpkg, layer=layer, bbox=bbox)
            idc = pick_column(g.columns, c["bag_id"], f"{layer} id")
            stc = pick_column(g.columns, c["bag_status"], f"{layer} status")
            g = g[g[stc].astype(str).str.contains("aangewezen", case=False, na=False)]
            extra.append(
                gpd.GeoDataFrame(
                    {
                        "vbo_id": g[idc].astype(str).values,
                        "gebruiksdoel": "woonfunctie",
                        "oppervlakte": float(cfg["bag"]["lig_standplaats_default_m2"]),
                        "status": layer,
                    },
                    geometry=g.geometry.representative_point().values,
                    crs=g.crs,
                )
            )
        if extra:
            gdf = pd.concat([gdf, *[e.to_crs(RD) for e in extra]], ignore_index=True)
            gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=RD)
    log.info("BAG: %d objecten", len(gdf))
    return gdf


def _guess_layer(layers: set[str], needle: str) -> str:
    for l in layers:
        if needle in l.lower():
            return l
    raise KeyError(f"Laag '{needle}' niet gevonden in {layers}")


def standardize_vbo(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    """Filter op status, normaliseer types, zorg voor punt-geometrie in RD."""
    statuses = {s.lower() for s in cfg["bag"]["statuses_in_use"]}
    st = gdf["status"].astype(str).str.lower()
    gdf = gdf[st.isin(statuses)].copy()
    if gdf.crs is None:
        gdf = gdf.set_crs(RD)
    gdf = gdf.to_crs(RD)
    if not (gdf.geom_type == "Point").all():
        gdf["geometry"] = gdf.geometry.representative_point()
    gdf["vbo_id"] = gdf["vbo_id"].astype(str)
    gdf["gebruiksdoel"] = gdf["gebruiksdoel"].fillna("").astype(str).str.lower()
    gdf["oppervlakte"] = pd.to_numeric(gdf["oppervlakte"], errors="coerce").astype(float)
    # 1 m² en 999999 m² zijn bekende plaatshouders in de BAG
    gdf.loc[(gdf["oppervlakte"] <= 1) | (gdf["oppervlakte"] >= 999_999), "oppervlakte"] = np.nan
    return gdf[STANDARD_COLS].reset_index(drop=True)


def split_uses(gebruiksdoel: pd.Series) -> pd.Series:
    """'woonfunctie,winkelfunctie' -> ['woonfunctie', 'winkelfunctie']"""
    return gebruiksdoel.fillna("").str.lower().str.split(r"\s*[,;|]\s*", regex=True).map(lambda xs: [x for x in xs if x])


def add_category_area(gdf: pd.DataFrame, categories: dict[str, str]) -> pd.DataFrame:
    """Voeg m2_<categorie> kolommen toe. Oppervlakte wordt gelijk over de gebruiksdoelen verdeeld.

    Ook kolom `is_woon` (bevat woonfunctie).
    """
    cats = sorted({c for c in categories.values() if c != "woon"} | {"overig"})
    n = len(gdf)
    uses = split_uses(gdf["gebruiksdoel"]).reset_index(drop=True)
    ex = uses.explode().dropna()
    gdf = gdf.copy()
    for c in cats:
        gdf[f"m2_{c}"] = 0.0
    if ex.empty:
        gdf["is_woon"] = False
        return gdf
    rows = ex.index.to_numpy()
    n_uses = uses.str.len().to_numpy()
    area = gdf["oppervlakte"].fillna(0).to_numpy()
    share = area[rows] / np.maximum(n_uses[rows], 1)
    cat = ex.map(lambda u: categories.get(u, "overig")).to_numpy()
    is_woon = np.zeros(n, dtype=bool)
    is_woon[rows[cat == "woon"]] = True
    gdf["is_woon"] = is_woon
    for c in cats:
        m = cat == c
        if m.any():
            gdf[f"m2_{c}"] = np.bincount(rows[m], weights=share[m], minlength=n)
    return gdf
