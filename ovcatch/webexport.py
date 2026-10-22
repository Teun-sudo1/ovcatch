"""Statische export voor de webviewer (GitHub Pages): alle bushaltes en buslijnen, vooraf berekend.

Eenheid is de *haltelocatie*: perrons met dezelfde naam binnen `line.merge_distance_m`
(zoals het lijnmodel). Per haltelocatie: alles binnen elke drempel (bruto). Per lijn:
unieke totalen over al haar haltes (zonder dubbeltelling), zoals in de analyse.

Uitvoer in <out>/:
  meta.json            drempels, kengetallen, bronnen, datum
  stops.json           haltes: naam, lon/lat, kengetallen per drempel, lijnen
  lines.json           lijnen: nummer, naam, vervoerder, haltes, totalen, route (polyline)
  iso/<tx>_<ty>.json   isochronen per halte, per tegel van 10 km (laden bij klikken)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString

from .config import Config
from .geo import RD, lonlat_to_rd, to_rd
from .gtfs import Feed, _norm
from .routing import make_router
from .store import DataStore

log = logging.getLogger(__name__)

# kengetallen in de export (volgorde = kolommen in stops.json / lines.json)
METRICS = ["inwoners", "woningen", "banen", "voorzieningen", "adressen"]
ISO_TILE_M = 10_000
CALC_TILE_M = 5_000


# ---------- haltelocaties en lijnen ----------

def stop_areas(feed: Feed, route_types: list[int], merge_m: float) -> tuple[pd.DataFrame, dict[str, int]]:
    """Bus-perrons samenvoegen tot haltelocaties: zelfde naam en onderling ≤ merge_m (verbonden componenten)."""
    bus_routes = feed.routes.loc[feed.routes["route_type"].isin(route_types), "route_id"]
    pats = feed.patterns[feed.patterns["route_id"].isin(bus_routes)]
    used = sorted({s for p in pats["pattern"] for s in p.split("|")} & set(feed.stops.index))
    st = feed.stops.loc[used].copy()
    st = st[st["lon"].notna() & st["lat"].notna()]
    xy = np.array([lonlat_to_rd(lo, la) for lo, la in zip(st["lon"], st["lat"])])
    st["x"], st["y"] = xy[:, 0], xy[:, 1]
    st["key"] = st["stop_name"].astype(str).map(_norm)

    rows, quay_area = [], {}
    for _, g in st.groupby("key", sort=False):
        ids = g.index.to_list()
        pts = g[["x", "y"]].to_numpy()
        parent = list(range(len(ids)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        if len(ids) > 1:
            d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
            for i, j in zip(*np.nonzero(np.triu(d <= merge_m, 1))):
                parent[find(i)] = find(j)
        comps = defaultdict(list)
        for i in range(len(ids)):
            comps[find(i)].append(i)
        for members in comps.values():
            aid = len(rows)
            sub = g.iloc[members]
            rows.append({"name": str(sub["stop_name"].iloc[0]), "lon": float(sub["lon"].mean()), "lat": float(sub["lat"].mean()),
                         "x": float(sub["x"].mean()), "y": float(sub["y"].mean()), "quays": sub.index.to_list()})
            for q in sub.index:
                quay_area[q] = aid
    return pd.DataFrame(rows), quay_area


def bus_lines(feed: Feed, route_types: list[int], merge_m: float, quay_area: dict[str, int]) -> list[dict]:
    """Per buslijn: representatief patroon (beide richtingen samengevoegd), als haltelocaties."""
    out = []
    ids = feed.patterns["route_id"].unique()
    routes = feed.routes[feed.routes["route_id"].isin(ids) & feed.routes["route_type"].isin(route_types)]
    for r in routes.itertuples():
        try:
            ln = feed.build_line(r.route_id, merge_m)
        except Exception as e:  # noqa: BLE001 - kapotte lijn in de feed mag de export niet stoppen
            log.warning("Lijn %s overgeslagen: %s", r.route_id, e)
            continue
        areas = []
        for s in ln["stops"]:
            a = next((quay_area[m] for m in s["members"] if m in quay_area), None)
            if a is not None and a not in areas:
                areas.append(a)
        if len(areas) < 2:
            continue
        out.append({"route_id": r.route_id, "short": str(r.route_short_name or ""), "long": str(r.route_long_name or ""),
                    "agency": r.agency_name if isinstance(r.agency_name, str) else "", "areas": areas, "geometry": ln["geometry"],
                    "share_in_region": len(areas) / len(ln["stops"])})
    return out


def region_polygon(cfg: Config):
    """Gebied met BAG/CBS-data: gemeentegrenzen (CBS) + 200 m, geknipt op de regio-bbox. None = geen filter."""
    from shapely.geometry import box

    from .data.download import raw_paths
    from .geo import bbox_wgs_to_rd

    gpkg = raw_paths(cfg)["cbs_buurten"]
    if not gpkg.exists():
        return None
    g = gpd.read_file(gpkg, layer="gemeenten", columns=[]).to_crs(RD)
    poly = shapely.union_all(g.geometry.buffer(200).to_numpy())
    bbox = bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    return poly.intersection(box(*bbox)) if bbox else poly


def keep_in_region(areas: pd.DataFrame, quay_area: dict[str, int], poly) -> tuple[pd.DataFrame, dict[str, int]]:
    """Alleen haltelocaties binnen het gebied met data (de NL-feed bevat ook België en Duitsland)."""
    if poly is None:
        return areas, quay_area
    inside = shapely.contains_xy(poly, areas["x"].to_numpy(), areas["y"].to_numpy())
    new_id = np.full(len(areas), -1)
    new_id[inside] = np.arange(inside.sum())
    log.info("Haltelocaties buiten het gebied: %d van %d", int((~inside).sum()), len(areas))
    qa = {q: int(new_id[a]) for q, a in quay_area.items() if new_id[a] >= 0}
    return areas[inside].reset_index(drop=True), qa


# ---------- isochronen ----------

def fetch_isochrones(areas: pd.DataFrame, cfg: Config) -> dict[tuple[int, str], dict[float, object]]:
    router = make_router(cfg)
    thresholds = {m: [float(t) for t in ts] for m, ts in cfg["thresholds"].items()}
    jobs = [(i, m) for i in range(len(areas)) for m in thresholds]
    lon, lat = areas["lon"].to_numpy(), areas["lat"].to_numpy()
    res, t0 = {}, time.time()

    def one(job):
        i, m = job
        try:
            return job, router.isochrones(float(lon[i]), float(lat[i]), m, thresholds[m])
        except Exception as e:  # noqa: BLE001 - halte buiten het netwerk e.d.
            log.warning("Isochroon halte %d (%s) mislukt: %s", i, m, e)
            return job, None

    workers = max(1, int(cfg["routing"].get("parallel_requests", 8)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (job, iso) in enumerate(ex.map(one, jobs), 1):
            if iso is not None:
                res[job] = iso
            if n % 5000 == 0:
                log.info("Isochronen %d/%d (%.0f s)", n, len(jobs), time.time() - t0)
    return res


def iso_frame(isos: dict, cfg: Config) -> gpd.GeoDataFrame:
    """Alle isochronen als RD-polygonen: kolommen area, mode, band."""
    rows = []
    for (a, m), by_t in isos.items():
        for b, t in enumerate(sorted(by_t)):
            rows.append((a, m, b, to_rd(by_t[t])))
    return gpd.GeoDataFrame(
        {"area": [r[0] for r in rows], "mode": [r[1] for r in rows], "band": [r[2] for r in rows]},
        geometry=[r[3] for r in rows], crs=RD,
    )


# ---------- tellen (in subprocessen) ----------

_W: dict = {}  # per subproces: datastore + isochronen, één keer geladen


def _load(addresses_path, amenities_path, poly_file):
    if "store" not in _W:
        _W["store"] = DataStore(Path(addresses_path), Path(amenities_path))
        _W["polys"] = gpd.read_parquet(poly_file)
    return _W["store"], _W["polys"]


def _point_values(store: DataStore, bbox) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """Adressen + voorzieningen in bbox als één puntenset met kolommen in METRICS-volgorde."""
    ad = store.addresses(bbox)
    am = store.amenities(bbox)
    n_a, n_m = len(ad), len(am)
    vals = np.zeros((n_a + n_m, len(METRICS)))
    if n_a:
        vals[:n_a, 0] = ad["inwoners"].to_numpy()
        vals[:n_a, 1] = ad["is_woon"].to_numpy()
        vals[:n_a, 2] = ad["banen_schatting"].to_numpy()
        vals[:n_a, 4] = 1
    if n_m:
        vals[n_a:, 3] = 1
    geom = np.concatenate([ad.geometry.to_numpy(), am.geometry.to_numpy()]) if n_m else ad.geometry.to_numpy()
    return gpd.GeoDataFrame(geometry=geom, crs=RD), vals


def _sums_by_band(pairs: pd.DataFrame, vals: np.ndarray, n_bands: int, key: str) -> dict:
    """pairs: (pt, key, band) met per (pt, key) de kleinste band -> {key: cumulatieve sommen [band, metric]}."""
    out = {}
    if pairs.empty:
        return out
    v = vals[pairs["pt"].to_numpy()]
    df = pd.DataFrame(v, columns=METRICS)
    df["k"] = pairs[key].to_numpy()
    df["band"] = pairs["band"].to_numpy()
    g = df.groupby(["k", "band"])[METRICS].sum()
    for k, sub in g.groupby(level=0):
        arr = np.zeros((n_bands, len(METRICS)))
        arr[sub.index.get_level_values(1).to_numpy()] = sub.to_numpy()
        out[k] = np.cumsum(arr, axis=0)
    return out


def _area_task(args):
    """Bruto per haltelocatie voor één rekentegel."""
    addresses_path, amenities_path, poly_file, area_ids, bands = args
    store, polys = _load(addresses_path, amenities_path, poly_file)
    p = polys[polys["area"].isin(area_ids)]
    if p.empty:
        return {}
    pts, vals = _point_values(store, tuple(p.total_bounds))
    j = gpd.sjoin(pts.assign(pt=np.arange(len(pts))), p, how="inner", predicate="intersects")
    out = {}
    for m, nb in bands.items():
        jm = j[j["mode"] == m].groupby(["pt", "area"], as_index=False)["band"].min()
        for a, arr in _sums_by_band(jm, vals, nb, "area").items():
            out[(int(a), m)] = arr
    return out


def _line_task(args):
    """Unieke totalen voor één lijn: elk punt telt één keer, in de kleinste band van welke halte ook."""
    addresses_path, amenities_path, poly_file, li, area_ids, bands = args
    store, polys = _load(addresses_path, amenities_path, poly_file)
    p = polys[polys["area"].isin(area_ids)]
    out = {}
    if p.empty:
        return li, out
    pts, vals = _point_values(store, tuple(p.total_bounds))
    j = gpd.sjoin(pts.assign(pt=np.arange(len(pts))), p, how="inner", predicate="intersects")
    for m, nb in bands.items():
        jm = j[j["mode"] == m].groupby("pt", as_index=False)["band"].min().assign(line=0)
        r = _sums_by_band(jm, vals, nb, "line")
        out[m] = r.get(0, np.zeros((nb, len(METRICS))))
    return li, out


# ---------- export ----------

def encode_polyline(coords, precision: int = 5) -> str:
    """Google polyline (lat, lon), 1e5 -> ~1 m; compact voor lijnen en isochronen."""
    out, plat, plon, f = [], 0, 0, 10 ** precision
    for lon, lat in coords:
        ilat, ilon = int(round(lat * f)), int(round(lon * f))
        for d in (ilat - plat, ilon - plon):
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def _poly_rings(geom, tol_deg: float) -> list[list[str]]:
    """(Multi)polygoon -> [[buitenring, gaten...], ...] als polylines, vereenvoudigd."""
    g = shapely.simplify(geom, tol_deg, preserve_topology=True)
    polys = getattr(g, "geoms", [g])
    return [[encode_polyline(p.exterior.coords)] + [encode_polyline(r.coords) for r in p.interiors] for p in polys if not p.is_empty]


def _rows_round(arr: np.ndarray) -> list[int]:
    return [int(round(x)) for x in arr.ravel()]


def export_web(cfg: Config, feed_name: str, out: Path, processes: int | None = None) -> dict:
    t0 = time.time()
    out = Path(out)
    (out / "iso").mkdir(parents=True, exist_ok=True)
    feed = Feed(cfg.gtfs_dir / feed_name)
    rt = cfg["line"]["bus_route_types"]
    merge_m = cfg["line"]["merge_distance_m"]
    areas, quay_area = stop_areas(feed, rt, merge_m)
    areas, quay_area = keep_in_region(areas, quay_area, region_polygon(cfg))
    lines = [ln for ln in bus_lines(feed, rt, merge_m, quay_area) if ln["share_in_region"] >= 0.5]
    log.info("%d haltelocaties, %d buslijnen (%.0f s)", len(areas), len(lines), time.time() - t0)

    isos = fetch_isochrones(areas, cfg)
    log.info("Isochronen klaar (%.0f s)", time.time() - t0)
    polys = iso_frame(isos, cfg)
    poly_file = cfg.cache_dir / "web_isochronen.parquet"
    polys.to_parquet(poly_file)

    modes = list(cfg["thresholds"])
    bands = {m: len(cfg["thresholds"][m]) for m in modes}
    processes = processes or max(1, (os.cpu_count() or 2) - 2)
    common = (str(cfg.addresses_path), str(cfg.amenities_path), str(poly_file))

    # bruto per haltelocatie, per rekentegel
    tiles = defaultdict(list)
    for i, (x, y) in enumerate(zip(areas["x"], areas["y"])):
        tiles[(int(x // CALC_TILE_M), int(y // CALC_TILE_M))].append(i)
    area_res = {}
    with ProcessPoolExecutor(processes) as ex:
        for n, r in enumerate(ex.map(_area_task, [(*common, ids, bands) for ids in tiles.values()]), 1):
            area_res.update(r)
            if n % 100 == 0:
                log.info("Haltes: tegel %d/%d (%.0f s)", n, len(tiles), time.time() - t0)
    log.info("Haltes klaar (%.0f s)", time.time() - t0)

    line_res = {}
    with ProcessPoolExecutor(processes) as ex:
        tasks = [(*common, li, ln["areas"], bands) for li, ln in enumerate(lines)]
        for n, (li, r) in enumerate(ex.map(_line_task, tasks, chunksize=4), 1):
            line_res[li] = r
            if n % 250 == 0:
                log.info("Lijnen %d/%d (%.0f s)", n, len(lines), time.time() - t0)
    log.info("Lijnen klaar (%.0f s)", time.time() - t0)

    # --- schrijven
    area_lines = defaultdict(list)
    for li, ln in enumerate(lines):
        for a in ln["areas"]:
            area_lines[a].append(li)
    zeros = {m: np.zeros((bands[m], len(METRICS))) for m in modes}
    stops_rows = []
    for i, a in enumerate(areas.itertuples()):
        vals = []
        for m in modes:
            vals += _rows_round(area_res.get((i, m), zeros[m]))
        iso_key = f"{int(a.x // ISO_TILE_M)}_{int(a.y // ISO_TILE_M)}"
        stops_rows.append([a.name, round(a.lon, 6), round(a.lat, 6), vals, area_lines.get(i, []), iso_key])
    line_rows = []
    for li, ln in enumerate(lines):
        vals = []
        for m in modes:
            vals += _rows_round(line_res.get(li, {}).get(m, zeros[m]))
        geom = ln["geometry"]
        if len(geom) > 2:
            geom = list(shapely.simplify(LineString(geom), 0.00005).coords)  # ~5 m
        line_rows.append([ln["short"], ln["long"], ln["agency"], ln["areas"], vals, encode_polyline(geom)])

    thresholds = {m: [int(t) for t in cfg["thresholds"][m]] for m in modes}
    _dump(out / "stops.json", {"columns": ["naam", "lon", "lat", "waarden", "lijnen", "iso"], "rows": stops_rows})
    _dump(out / "lines.json", {"columns": ["nummer", "naam", "vervoerder", "haltes", "waarden", "route"], "rows": line_rows})

    iso_tiles = defaultdict(dict)
    for i, (x, y) in enumerate(zip(areas["x"], areas["y"])):
        key = f"{int(x // ISO_TILE_M)}_{int(y // ISO_TILE_M)}"
        entry = {}
        for m in modes:
            by_t = isos.get((i, m))
            if by_t:
                tol = 0.0002 if m == "walk" else 0.0004  # ~15 m / ~30 m
                entry[m] = [_poly_rings(by_t[t], tol) for t in sorted(by_t)]
        iso_tiles[key][str(i)] = entry
    for key, d in iso_tiles.items():
        _dump(out / "iso" / f"{key}.json", d)

    rep_file = cfg.processed_dir / "disaggregatie_rapport.json"
    meta = {
        "gemaakt": date.today().isoformat(),
        "feed": feed_name,
        "modi": modes,
        "drempels": thresholds,
        "kengetallen": METRICS,
        "iso_tegel_m": ISO_TILE_M,
        "n_haltes": len(areas),
        "n_lijnen": len(lines),
        "router": cfg["routing"]["engine"],
        "disaggregatie": json.loads(rep_file.read_text()) if rep_file.exists() else None,
    }
    meta |= export_cells(cfg, out)
    _dump(out / "meta.json", meta)
    log.info("Export klaar in %.0f s -> %s", time.time() - t0, out)
    return {k: meta[k] for k in ("n_haltes", "n_lijnen")} | {"seconden": round(time.time() - t0)}


def export_cells(cfg: Config, out: Path) -> dict:
    """100m-vierkanten met kengetallen, voor bewerken in de browser (tellen zonder server).

    cells/<tx>_<ty>.json per tegel van 10 km (zelfde sleutel als iso/): middelpunt in
    lon/lat x 1e5 en per vierkant de kengetallen in METRICS-volgorde.
    """
    import pyarrow.parquet as pq

    from .geo import RD, WGS84, _tf

    t0 = time.time()
    (out / "cells").mkdir(parents=True, exist_ok=True)
    # coördinaten uit de bbox-kolom: geen shapely-geometrieën nodig voor 10 mln punten
    tab = pq.read_table(cfg.addresses_path, columns=["bbox", "inwoners", "is_woon", "banen_schatting"])
    bb = tab.column("bbox").combine_chunks()
    df = pd.DataFrame({
        "cx": np.floor(bb.field("xmin").to_numpy() / 100).astype(np.int64),
        "cy": np.floor(bb.field("ymin").to_numpy() / 100).astype(np.int64),
        "inwoners": tab.column("inwoners").to_numpy(),
        "woningen": tab.column("is_woon").to_numpy().astype(float),
        "banen": tab.column("banen_schatting").to_numpy(),
        "voorzieningen": 0.0,
        "adressen": 1.0,
    })
    del tab, bb
    if cfg.amenities_path.exists():
        am = gpd.read_parquet(cfg.amenities_path, columns=["geometry"])
        df = pd.concat([df, pd.DataFrame({
            "cx": np.floor(am.geometry.x.to_numpy() / 100).astype(np.int64),
            "cy": np.floor(am.geometry.y.to_numpy() / 100).astype(np.int64),
            "inwoners": 0.0, "woningen": 0.0, "banen": 0.0, "voorzieningen": 1.0, "adressen": 0.0,
        })], ignore_index=True)
    cells = df.groupby(["cx", "cy"], sort=False)[METRICS].sum().reset_index()
    lon, lat = _tf(RD, WGS84).transform(cells["cx"].to_numpy() * 100 + 50.0, cells["cy"].to_numpy() * 100 + 50.0)
    cells["lon"] = np.round(lon * 1e5).astype(np.int64)
    cells["lat"] = np.round(lat * 1e5).astype(np.int64)
    cells["key"] = (cells["cx"] // 100).astype(str) + "_" + (cells["cy"] // 100).astype(str)
    n_files = 0
    for key, g in cells.groupby("key"):
        _dump(out / "cells" / f"{key}.json", {
            "lon": g["lon"].tolist(),
            "lat": g["lat"].tolist(),
            # inwoners/banen op 0,1 (vierkant-sommen van fracties), tellingen geheel
            "v": [[round(a, 1), int(b), round(c, 1), int(d), int(e)] for a, b, c, d, e in g[METRICS].itertuples(index=False)],
        })
        n_files += 1
    log.info("Vierkanten: %d in %d tegels (%.0f s)", len(cells), n_files, time.time() - t0)
    return {"n_vierkanten": len(cells), "tegels": n_files}


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
