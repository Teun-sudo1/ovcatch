"""Configuratie laden.

Volgorde: pad uit argument > env OVCATCH_CONFIG > config.yaml in projectmap.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config(dict):
    """Dict met wat gemaksfuncties voor paden."""

    @property
    def root(self) -> Path:
        return Path(self.get("_root", PROJECT_ROOT))

    @property
    def data_dir(self) -> Path:
        p = Path(self["data_dir"])
        return p if p.is_absolute() else self.root / p

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def gtfs_dir(self) -> Path:
        return self.data_dir / "gtfs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def scenario_dir(self) -> Path:
        return self.data_dir / "scenarios"

    @property
    def addresses_path(self) -> Path:
        return self.processed_dir / "addresses.parquet"

    @property
    def amenities_path(self) -> Path:
        return self.processed_dir / "amenities.parquet"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.processed_dir, self.gtfs_dir, self.cache_dir, self.scenario_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config(path: str | os.PathLike | None = None, overrides: dict[str, Any] | None = None) -> Config:
    path = Path(path or os.environ.get("OVCATCH_CONFIG") or PROJECT_ROOT / "config.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # demo-configs kunnen een basisconfig uitbreiden
    base = data.pop("extends", None)
    if base:
        base_cfg = load_config(path.parent / base)
        merged = _deep_merge(dict(base_cfg), data)
        data = merged
    if overrides:
        data = _deep_merge(data, overrides)
    cfg = Config(data)
    cfg["_root"] = str(path.parent.resolve())
    env_url = os.environ.get("VALHALLA_URL")
    if env_url:
        cfg["routing"]["valhalla_url"] = env_url
    env_engine = os.environ.get("OVCATCH_ROUTER")
    if env_engine:
        cfg["routing"]["engine"] = env_engine
    return cfg


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


def pick_column(columns, candidates: list[str], what: str) -> str:
    """Kies de eerste kolomnaam uit `candidates` die bestaat (hoofdletterongevoelig)."""
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    raise KeyError(f"Geen kolom gevonden voor {what}; gezocht: {candidates}; beschikbaar: {list(columns)[:40]}")
