"""Downloads van ruwe bronnen (idempotent: bestaande bestanden worden overgeslagen)."""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import httpx

from ..config import Config

log = logging.getLogger(__name__)


def download(url: str, dest: Path, force: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        log.info("Bestaat al, overslaan: %s", dest.name)
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Downloaden %s -> %s", url, dest)
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, read=600)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        last = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total and done - last > total / 20:
                    log.info("  %s: %.0f%%", dest.name, 100 * done / total)
                    last = done
    shutil.move(tmp, dest)
    return dest


def unzip_find(zip_path: Path, suffix: str, out_dir: Path) -> Path:
    """Pak een zip uit en geef het eerste bestand met `suffix` terug."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.rglob(f"*{suffix}"))
    if existing:
        return existing[0]
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out_dir)
    found = sorted(out_dir.rglob(f"*{suffix}"))
    if not found:
        raise FileNotFoundError(f"Geen *{suffix} in {zip_path}")
    return found[0]


def raw_paths(cfg: Config) -> dict[str, Path]:
    raw = cfg.raw_dir
    region = cfg["region"]["name"]
    return {
        "bag": raw / "bag-light.gpkg",
        "cbs_vk_zip": raw / "cbs_vk100.zip",
        "cbs_vk_dir": raw / "cbs_vk100",
        "cbs_buurten": raw / "cbs_buurten.gpkg",
        "osm": raw / f"{region}.osm.pbf",
        "gtfs": cfg.gtfs_dir / "gtfs-nl.zip",
    }


def download_all(cfg: Config, only: list[str] | None = None, force: bool = False) -> dict[str, Path]:
    cfg.ensure_dirs()
    p = raw_paths(cfg)
    s = cfg["sources"]
    jobs = {
        "bag": (s["bag_gpkg_url"], p["bag"]),
        "cbs_vk": (s["cbs_vk100_url"], p["cbs_vk_zip"]),
        "cbs_buurten": (s["cbs_buurten_url"], p["cbs_buurten"]),
        "osm": (cfg["region"]["osm_pbf_url"], p["osm"]),
        "gtfs": (s["gtfs_url"], p["gtfs"]),
    }
    for key, (url, dest) in jobs.items():
        if only and key not in only:
            continue
        download(url, dest, force=force)
    return p
