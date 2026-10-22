"""Catchment-analyse per halte en per lijn, en vergelijking van scenario's.

Definities
- raw (per halte): alles binnen de isochroon van die halte (overlap met buurhaltes telt dubbel).
- assigned (per halte): elk adres/voorziening hoort bij precies één halte: de halte met de
  kleinste drempelband waarbinnen het valt; bij gelijke band de hemelsbreed dichtstbijzijnde.
- line (per lijn): unieke totalen = som van assigned = alles binnen de vereniging van isochronen.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping

from .config import Config
from .geo import RD, WGS84, lonlat_to_rd, to_rd
from .routing import Router
from .store import DataStore

log = logging.getLogger(__name__)

BASE_METRICS = ["n_adressen", "n_woningen", "inwoners", "banen_schatting"]

LABELS = {
    "n_adressen": "Adressen (verblijfsobjecten)",
    "n_woningen": "Woningen",
    "inwoners": "Inwoners (geschat)",
    "banen_schatting": "Arbeidsplaatsen (ruwe schatting)",
}


def metric_keys(cfg: Config) -> list[str]:
    cats = sorted({c for c in cfg["categories"].values() if c != "woon"} | {"overig"})
    return BASE_METRICS + [f"m2_{c}" for c in cats] + [f"voorz_{c}" for c in cfg["amenities"]]


def metric_labels(cfg: Config) -> dict[str, str]:
    out = dict(LABELS)
    for k in metric_keys(cfg):
        if k.startswith("m2_"):
            out[k] = f"m² {k[3:]}"
        elif k.startswith("voorz_"):
            out[k] = f"Voorzieningen: {k[6:]}"
    return out


def route_points(scenario: dict) -> tuple[list[tuple[float, float]], list[bool]]:
    """Haltes met via-punten ertussen. via['after'] = index van de halte waarna het via-punt komt."""
    stops = scenario["stops"]
    vias = scenario.get("vias") or []
    by_after: dict[int, list] = {}
    for v in vias:
        by_after.setdefault(int(v["after"]), []).append(v)
    pts, mask = [], []
    for i, s in enumerate(stops):
        pts.append((float(s["lon"]), float(s["lat"])))
        mask.append(True)
        for v in by_after.get(i, []):
            pts.append((float(v["lon"]), float(v["lat"])))
            mask.append(False)
    return pts, mask


def _empty_metrics(keys) -> dict[str, float]:
    return {k: 0.0 for k in keys}


def _aggregate(addr: pd.DataFrame, am: pd.DataFrame, keys: list[str], amen_cats: list[str]) -> dict[str, float]:
    m = {}
    m["n_adressen"] = float(len(addr))
    m["n_woningen"] = float(addr["is_woon"].sum()) if len(addr) else 0.0
    m["inwoners"] = float(addr["inwoners"].sum()) if len(addr) else 0.0
    m["banen_schatting"] = float(addr["banen_schatting"].sum()) if "banen_schatting" in addr and len(addr) else 0.0
    for k in keys:
        if k.startswith("m2_"):
            m[k] = float(addr[k].sum()) if k in addr and len(addr) else 0.0
    vc = am["categorie"].value_counts() if len(am) else {}
    for c in amen_cats:
        m[f"voorz_{c}"] = float(vc.get(c, 0)) if len(am) else 0.0
    return {k: round(m.get(k, 0.0), 2) for k in keys}


def _membership(points: gpd.GeoDataFrame, polys: gpd.GeoDataFrame) -> pd.DataFrame:
    """Per (punt, halte): kleinste drempelindex (band) waarin het punt valt."""
    if points.empty or polys.empty:
        return pd.DataFrame(columns=["pt", "stop", "band"])
    j = gpd.sjoin(points[["geometry"]].assign(pt=np.arange(len(points))), polys, how="inner", predicate="intersects")
    if j.empty:
        return pd.DataFrame(columns=["pt", "stop", "band"])
    return j.groupby(["pt", "stop"], as_index=False)["band"].min()


def _assign(member: pd.DataFrame, points: gpd.GeoDataFrame, stop_xy: np.ndarray) -> pd.DataFrame:
    """Per punt één halte: kleinste band, dan dichtstbijzijnde."""
    if member.empty:
        return pd.DataFrame(columns=["pt", "stop", "band"])
    px = points.geometry.x.to_numpy()[member["pt"].to_numpy()]
    py = points.geometry.y.to_numpy()[member["pt"].to_numpy()]
    sx = stop_xy[member["stop"].to_numpy(), 0]
    sy = stop_xy[member["stop"].to_numpy(), 1]
    member = member.assign(d=np.hypot(px - sx, py - sy))
    member = member.sort_values(["pt", "band", "d"])
    return member.drop_duplicates("pt", keep="first")[["pt", "stop", "band"]]


def analyse(scenario: dict, cfg: Config, router: Router, store: DataStore, with_points: bool = True) -> dict[str, Any]:
    stops = scenario.get("stops") or []
    keys = metric_keys(cfg)
    amen_cats = list(cfg["amenities"].keys())
    thresholds = {m: [float(t) for t in ts] for m, ts in cfg["thresholds"].items()}
    result: dict[str, Any] = {
        "name": scenario.get("name", ""),
        "router": {"engine": router.name, "approximate": bool(router.approximate)},
        "metrics": keys,
        "labels": metric_labels(cfg),
        "stops": [{"id": s["id"], "name": s.get("name", "")} for s in stops],
        "modes": {},
        "isochrones": {"type": "FeatureCollection", "features": []},
        "points": {},
    }

    # Route (bus)
    pts, mask = route_points(scenario)
    rr = router.route(pts, mask) if len(pts) >= 2 else None
    geom = scenario.get("geometry") if (scenario.get("source") == "gtfs" and not scenario.get("modified")) else None
    result["route"] = {
        "geometry": geom or (rr.coords if rr else [list(p) for p in pts]),
        "length_m": round(rr.length_m, 1) if rr else 0.0,
        "duration_s": round(rr.duration_s, 1) if rr else 0.0,
        "gtfs_duration_s": (scenario.get("gtfs") or {}).get("duration_s"),
        "n_stops": len(stops),
    }
    if geom and len(geom) >= 2:
        from shapely.geometry import LineString

        result["route"]["gtfs_shape_length_m"] = round(to_rd(LineString(geom)).length, 1)

    if not stops:
        for mode, ths in thresholds.items():
            result["modes"][mode] = {"thresholds": ths, "per_stop": [], "line": {str(int(t)): _empty_metrics(keys) for t in ths}}
        return result

    stop_xy = np.array([lonlat_to_rd(float(s["lon"]), float(s["lat"])) for s in stops])

    # Isochronen (parallel: bij Valhalla is dit het grootste deel van de rekentijd)
    jobs = [(mode, si) for mode in thresholds for si in range(len(stops))]

    def fetch(job):
        mode, si = job
        return router.isochrones(float(stops[si]["lon"]), float(stops[si]["lat"]), mode, thresholds[mode])

    workers = max(1, int(cfg["routing"].get("parallel_requests", 8)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fetched = dict(zip(jobs, ex.map(fetch, jobs)))
    iso_rows = []
    for mode, ths in thresholds.items():
        for si, s in enumerate(stops):
            isos = fetched[(mode, si)]
            for ti, t in enumerate(ths):
                g = isos[float(t)]
                iso_rows.append({"mode": mode, "stop": si, "band": ti, "threshold": t, "geom_wgs": g, "geometry": to_rd(g)})
                result["isochrones"]["features"].append(
                    {
                        "type": "Feature",
                        "properties": {"stop_id": s["id"], "stop_index": si, "mode": mode, "threshold": int(t)},
                        "geometry": mapping(g),
                    }
                )
    iso = gpd.GeoDataFrame(iso_rows, geometry="geometry", crs=RD)
    minx, miny, maxx, maxy = iso.total_bounds
    pad = 50
    bbox = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    addr = store.addresses(bbox).reset_index(drop=True)
    am = store.amenities(bbox).reset_index(drop=True)
    for k in keys:
        if k.startswith("m2_") and k not in addr.columns:
            addr[k] = 0.0
    if "banen_schatting" not in addr.columns:
        addr["banen_schatting"] = 0.0

    for mode, ths in thresholds.items():
        polys = iso[iso["mode"] == mode][["stop", "band", "geometry"]]
        mem_a = _membership(addr, polys)
        mem_m = _membership(am, polys)
        asg_a = _assign(mem_a, addr, stop_xy)
        asg_m = _assign(mem_m, am, stop_xy)

        per_stop = []
        for si, s in enumerate(stops):
            by_t = {}
            for ti, t in enumerate(ths):
                ra = mem_a[(mem_a["stop"] == si) & (mem_a["band"] <= ti)]["pt"].to_numpy()
                rm = mem_m[(mem_m["stop"] == si) & (mem_m["band"] <= ti)]["pt"].to_numpy()
                aa = asg_a[(asg_a["stop"] == si) & (asg_a["band"] <= ti)]["pt"].to_numpy()
                aam = asg_m[(asg_m["stop"] == si) & (asg_m["band"] <= ti)]["pt"].to_numpy()
                by_t[str(int(t))] = {
                    "raw": _aggregate(addr.iloc[ra], am.iloc[rm], keys, amen_cats),
                    "assigned": _aggregate(addr.iloc[aa], am.iloc[aam], keys, amen_cats),
                }
            per_stop.append({"id": s["id"], "name": s.get("name", ""), "by_threshold": by_t})

        line = {}
        for ti, t in enumerate(ths):
            ua = asg_a[asg_a["band"] <= ti]["pt"].to_numpy()
            um = asg_m[asg_m["band"] <= ti]["pt"].to_numpy()
            line[str(int(t))] = _aggregate(addr.iloc[ua], am.iloc[um], keys, amen_cats)
        result["modes"][mode] = {"thresholds": [int(t) for t in ths], "per_stop": per_stop, "line": line}

        if with_points and len(asg_a):
            sel = asg_a.merge(addr[["inwoners", "is_woon"]], left_on="pt", right_index=True)
            maxp = int(cfg.get("api", {}).get("max_points", 25000))
            if len(sel) > maxp:
                sel = sel.sample(maxp, random_state=0)
            g = gpd.GeoSeries(addr.geometry.iloc[sel["pt"].to_numpy()].values, crs=RD).to_crs(WGS84)
            result["points"][mode] = {
                "columns": ["lon", "lat", "stop", "band", "inwoners", "woon"],
                "rows": [
                    [round(x, 6), round(y, 6), int(st), int(b), round(float(i), 2), int(w)]
                    for x, y, st, b, i, w in zip(g.x, g.y, sel["stop"], sel["band"], sel["inwoners"], sel["is_woon"])
                ],
                "sampled": len(asg_a) > maxp,
            }
    return result


def _pct(b: float, v: float) -> float | None:
    if b == 0 or not math.isfinite(b) or not math.isfinite(v):
        return None
    return round(100.0 * (v - b) / b, 1)


def compare(base: dict, var: dict) -> dict[str, Any]:
    """Vergelijk twee analyse-resultaten. Geeft lijntotalen- en haltetabellen (lang formaat)."""
    line_rows = []
    for mode, bm in base["modes"].items():
        vm = var["modes"].get(mode)
        if not vm:
            continue
        for t, bmet in bm["line"].items():
            vmet = vm["line"].get(t, {})
            for k in base["metrics"]:
                b, v = float(bmet.get(k, 0)), float(vmet.get(k, 0))
                line_rows.append({"mode": mode, "threshold": int(t), "metric": k, "baseline": b, "variant": v, "delta": round(v - b, 2), "pct": _pct(b, v)})
    route_rows = []
    for k in ["length_m", "duration_s", "n_stops"]:
        b, v = float(base["route"].get(k) or 0), float(var["route"].get(k) or 0)
        route_rows.append({"metric": k, "baseline": b, "variant": v, "delta": round(v - b, 1), "pct": _pct(b, v)})

    stop_rows = []
    for label, res in (("baseline", base), ("variant", var)):
        for mode, m in res["modes"].items():
            for s in m["per_stop"]:
                for t, d in s["by_threshold"].items():
                    for kind in ("raw", "assigned"):
                        row = {"scenario": label, "stop_id": s["id"], "stop_name": s["name"], "mode": mode, "threshold": int(t), "kind": kind}
                        row.update(d[kind])
                        stop_rows.append(row)
    base_ids = {s["id"] for s in base["stops"]}
    var_ids = {s["id"] for s in var["stops"]}
    return {
        "line": line_rows,
        "route": route_rows,
        "stops": stop_rows,
        "stop_changes": {"removed": sorted(base_ids - var_ids), "added": sorted(var_ids - base_ids), "kept": sorted(base_ids & var_ids)},
        "labels": base.get("labels", {}),
    }


def comparison_csv(cmp: dict) -> str:
    import io

    buf = io.StringIO()
    buf.write("# Lijntotalen (uniek, zonder dubbeltelling)\n")
    pd.DataFrame(cmp["line"]).assign(label=lambda d: d["metric"].map(cmp["labels"]).fillna(d["metric"])).to_csv(buf, index=False, sep=";", decimal=",")
    buf.write("\n# Route\n")
    pd.DataFrame(cmp["route"]).to_csv(buf, index=False, sep=";", decimal=",")
    buf.write("\n# Per halte (raw = alles binnen bereik, assigned = toegewezen aan dichtstbijzijnde halte)\n")
    pd.DataFrame(cmp["stops"]).to_csv(buf, index=False, sep=";", decimal=",")
    buf.write("\n# Let op: inwoners zijn een disaggregatie van CBS-cijfers; arbeidsplaatsen zijn een ruwe schatting op basis van m² BAG.\n")
    return buf.getvalue()
