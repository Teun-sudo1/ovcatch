"""Toegang tot verwerkte data (adressen en voorzieningen) met bbox-filter."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd

from .geo import RD


def _read_bbox(path: Path, bbox_rd) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} ontbreekt — draai eerst `python -m ovcatch prepare` (of `make demo`).")
    try:
        gdf = gpd.read_parquet(path, bbox=bbox_rd)
    except Exception:  # oudere bestanden zonder bbox-kolom
        gdf = gpd.read_parquet(path)
        gdf = gdf.cx[bbox_rd[0] : bbox_rd[2], bbox_rd[1] : bbox_rd[3]]
    if gdf.crs is None:
        gdf = gdf.set_crs(RD)
    return gdf


class DataStore:
    def __init__(self, addresses_path: Path, amenities_path: Path):
        self.addresses_path = Path(addresses_path)
        self.amenities_path = Path(amenities_path)

    def addresses(self, bbox_rd) -> gpd.GeoDataFrame:
        return _read_bbox(self.addresses_path, tuple(bbox_rd))

    def amenities(self, bbox_rd) -> gpd.GeoDataFrame:
        if not self.amenities_path.exists():
            return gpd.GeoDataFrame(columns=["categorie", "geometry"], geometry="geometry", crs=RD)
        return _read_bbox(self.amenities_path, tuple(bbox_rd))

    def available(self) -> bool:
        return self.addresses_path.exists()


@lru_cache(maxsize=4)
def get_store(addresses_path: str, amenities_path: str) -> DataStore:
    return DataStore(Path(addresses_path), Path(amenities_path))
