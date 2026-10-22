"""GTFS inlezen, indexeren (DuckDB) en een lijn opbouwen.

Indexeren gebeurt eenmalig per feed: de grote stop_times.txt wordt met DuckDB
samengevat tot stoppatronen per route/richting. Daarna is een lijn opvragen snel.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
from shapely.geometry import LineString, Point

from .geo import lonlat_to_rd, to_rd

log = logging.getLogger(__name__)

NEEDED = ["agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt"]


def feed_name_from_zip(zip_path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(zip_path).stem)


def index_feed(zip_path: Path, gtfs_dir: Path, name: str | None = None, keep_raw: bool = False) -> Path:
    """Maak data/gtfs/<name>/ met routes/stops/patterns/shapes als parquet."""
    name = name or feed_name_from_zip(zip_path)
    out = Path(gtfs_dir) / name
    raw = out / "_raw"
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        members = {Path(m).name: m for m in z.namelist()}
        for f in NEEDED:
            if f in members:
                target = raw / f
                if not target.exists():
                    with z.open(members[f]) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    for req in ["routes.txt", "trips.txt", "stops.txt", "stop_times.txt"]:
        if not (raw / req).exists():
            raise FileNotFoundError(f"{req} ontbreekt in {zip_path}")

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")

    def csv(f):
        return f"read_csv('{(raw / f).as_posix()}', all_varchar=true, header=true, quote='\"')"

    log.info("GTFS indexeren: %s", name)
    has_agency = (raw / "agency.txt").exists()
    con.execute(f"CREATE TABLE routes AS SELECT * FROM {csv('routes.txt')}")
    rcols = {r[0] for r in con.execute("DESCRIBE routes").fetchall()}
    agency_join = ""
    agency_sel = "NULL AS agency_name"
    if has_agency and "agency_id" in rcols:
        con.execute(f"CREATE TABLE agency AS SELECT * FROM {csv('agency.txt')}")
        agency_join = "LEFT JOIN agency a USING (agency_id)"
        agency_sel = "a.agency_name"
    con.execute(
        f"""
        COPY (
          SELECT r.route_id,
                 {'r.agency_id' if 'agency_id' in rcols else 'NULL'} AS agency_id,
                 {agency_sel},
                 coalesce(r.route_short_name, '') AS route_short_name,
                 coalesce(r.route_long_name, '') AS route_long_name,
                 TRY_CAST(r.route_type AS INTEGER) AS route_type
          FROM routes r {agency_join}
        ) TO '{(out / 'routes.parquet').as_posix()}' (FORMAT parquet)
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT stop_id, stop_name, TRY_CAST(stop_lat AS DOUBLE) AS lat, TRY_CAST(stop_lon AS DOUBLE) AS lon
          FROM {csv('stops.txt')}
        ) TO '{(out / 'stops.parquet').as_posix()}' (FORMAT parquet)
        """
    )
    tcols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {csv('trips.txt')}").fetchall()}
    dir_expr = "coalesce(TRY_CAST(direction_id AS INTEGER), 0)" if "direction_id" in tcols else "0"
    shape_expr = "shape_id" if "shape_id" in tcols else "NULL"
    con.execute(
        f"CREATE TABLE trips AS SELECT trip_id, route_id, {dir_expr} AS direction_id, {shape_expr} AS shape_id FROM {csv('trips.txt')}"
    )

    def secs(col):
        return (
            f"(TRY_CAST(split_part({col}, ':', 1) AS INTEGER)*3600 + "
            f"TRY_CAST(split_part({col}, ':', 2) AS INTEGER)*60 + TRY_CAST(split_part({col}, ':', 3) AS INTEGER))"
        )

    con.execute(
        f"""
        CREATE TABLE trip_patterns AS
        SELECT trip_id,
               string_agg(stop_id, '|' ORDER BY seq) AS pattern,
               max({secs('arrival_time')}) - min({secs('departure_time')}) AS duration_s
        FROM (
          SELECT trip_id, stop_id, TRY_CAST(stop_sequence AS INTEGER) AS seq, arrival_time, departure_time
          FROM {csv('stop_times.txt')}
        )
        GROUP BY trip_id
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT t.route_id, t.direction_id, p.pattern,
                 count(*) AS n_trips,
                 mode(t.shape_id) AS shape_id,
                 median(p.duration_s) AS duration_s
          FROM trips t JOIN trip_patterns p USING (trip_id)
          GROUP BY 1, 2, 3
        ) TO '{(out / 'patterns.parquet').as_posix()}' (FORMAT parquet)
        """
    )
    if (raw / "shapes.txt").exists():
        con.execute(
            f"""
            COPY (
              SELECT s.shape_id, TRY_CAST(shape_pt_lat AS DOUBLE) AS lat, TRY_CAST(shape_pt_lon AS DOUBLE) AS lon,
                     TRY_CAST(shape_pt_sequence AS INTEGER) AS seq
              FROM {csv('shapes.txt')} s
              WHERE s.shape_id IN (SELECT DISTINCT shape_id FROM read_parquet('{(out / 'patterns.parquet').as_posix()}'))
            ) TO '{(out / 'shapes.parquet').as_posix()}' (FORMAT parquet)
            """
        )
    con.close()
    meta = {"name": name, "source_zip": str(zip_path)}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if not keep_raw:
        shutil.rmtree(raw, ignore_errors=True)
    log.info("GTFS index klaar: %s", out)
    return out


def list_feeds(gtfs_dir: Path) -> list[str]:
    gtfs_dir = Path(gtfs_dir)
    if not gtfs_dir.exists():
        return []
    return sorted(p.name for p in gtfs_dir.iterdir() if (p / "patterns.parquet").exists())


class Feed:
    def __init__(self, feed_dir: Path):
        self.dir = Path(feed_dir)
        self.name = self.dir.name
        self.routes = pd.read_parquet(self.dir / "routes.parquet")
        self.stops = pd.read_parquet(self.dir / "stops.parquet").set_index("stop_id")
        self.patterns = pd.read_parquet(self.dir / "patterns.parquet")
        sp = self.dir / "shapes.parquet"
        self.shapes = pd.read_parquet(sp) if sp.exists() else None

    def search_routes(self, q: str = "", route_types: list[int] | None = None, limit: int = 50) -> list[dict]:
        r = self.routes
        if route_types:
            r = r[r["route_type"].isin(route_types)]
        r = r[r["route_id"].isin(self.patterns["route_id"].unique())]
        if q:
            ql = q.lower()
            hay = (
                r["route_short_name"].fillna("") + " " + r["route_long_name"].fillna("") + " " + r["agency_name"].fillna("").astype(str)
            ).str.lower()
            # match aan het begin van een woord: 'huizen' vindt Huizen, niet Enkhuizen
            mask = hay.str.contains(r"(?<![^\W_])" + re.escape(ql), regex=True)
            # exacte lijnnummer-match eerst
            exact = r["route_short_name"].fillna("").str.lower() == ql
            r = pd.concat([r[exact], r[mask & ~exact]])
        r = r.head(limit)
        return [
            {
                "route_id": x.route_id,
                "short_name": x.route_short_name,
                "long_name": x.route_long_name,
                "agency": x.agency_name if isinstance(x.agency_name, str) else "",
                "route_type": int(x.route_type) if pd.notna(x.route_type) else None,
            }
            for x in r.itertuples()
        ]

    def representative(self, route_id: str, direction_id: int) -> pd.Series | None:
        p = self.patterns[(self.patterns["route_id"] == route_id) & (self.patterns["direction_id"] == direction_id)]
        if p.empty:
            return None
        # meest gereden patroon; bij gelijkspel het langste
        p = p.assign(_len=p["pattern"].str.count(r"\|")).sort_values(["n_trips", "_len"], ascending=False)
        return p.iloc[0]

    def shape_line(self, shape_id) -> list[list[float]] | None:
        if self.shapes is None or shape_id is None or pd.isna(shape_id):
            return None
        s = self.shapes[self.shapes["shape_id"] == shape_id].sort_values("seq")
        if len(s) < 2:
            return None
        return s[["lon", "lat"]].to_numpy().tolist()

    def build_line(self, route_id: str, merge_m: float = 150) -> dict:
        routes = self.routes.set_index("route_id")
        if route_id not in routes.index:
            raise KeyError(f"route_id {route_id} niet in feed {self.name}")
        rinfo = routes.loc[route_id]
        dirs = sorted(self.patterns.loc[self.patterns["route_id"] == route_id, "direction_id"].unique())
        main = self.representative(route_id, dirs[0])
        other = self.representative(route_id, dirs[1]) if len(dirs) > 1 else None

        def stops_of(pattern: str) -> list[dict]:
            out = []
            for sid in pattern.split("|"):
                if sid in self.stops.index:
                    s = self.stops.loc[sid]
                    out.append({"stop_id": sid, "name": str(s["stop_name"]), "lon": float(s["lon"]), "lat": float(s["lat"])})
            return out

        s0 = stops_of(main["pattern"])
        geom = self.shape_line(main["shape_id"]) or [[s["lon"], s["lat"]] for s in s0]
        logical = merge_directions(s0, stops_of(other["pattern"]) if other is not None else [], geom, merge_m)
        name = " ".join(x for x in [str(rinfo["route_short_name"] or ""), str(rinfo["route_long_name"] or "")] if x).strip()
        return {
            "name": f"{name} (origineel)",
            "feed": self.name,
            "route_id": route_id,
            "route_name": name,
            "agency": rinfo["agency_name"] if isinstance(rinfo["agency_name"], str) else "",
            "stops": logical,
            "vias": [],
            "geometry": geom,
            "source": "gtfs",
            "gtfs": {
                "direction_id": int(dirs[0]),
                "n_trips": int(main["n_trips"]),
                "duration_s": float(main["duration_s"]) if pd.notna(main["duration_s"]) else None,
            },
        }


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def merge_directions(s0: list[dict], s1: list[dict], geom_lonlat: list[list[float]], merge_m: float) -> list[dict]:
    """Voeg haltes van beide richtingen samen tot logische haltes, in de volgorde van richting 0."""
    line_rd = to_rd(LineString(geom_lonlat)) if len(geom_lonlat) >= 2 else None
    logical = []
    for s in s0:
        x, y = lonlat_to_rd(s["lon"], s["lat"])
        logical.append({"members": [s], "x": [x], "y": [y], "name": s["name"]})
    for s in reversed(s1):
        x, y = lonlat_to_rd(s["lon"], s["lat"])
        best, best_d = None, 1e18
        for L in logical:
            if any(m["stop_id"] == s["stop_id"] for m in L["members"]):
                best, best_d = L, 0
                break
            lx, ly = sum(L["x"]) / len(L["x"]), sum(L["y"]) / len(L["y"])
            d = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
            same = _norm(L["name"]) == _norm(s["name"])
            if (same and d <= merge_m) or d <= min(40, merge_m):
                if d < best_d:
                    best, best_d = L, d
        if best is not None:
            if all(m["stop_id"] != s["stop_id"] for m in best["members"]):
                best["members"].append(s)
                best["x"].append(x)
                best["y"].append(y)
        else:
            logical.append({"members": [s], "x": [x], "y": [y], "name": s["name"], "_insert": True})
    # ordenen langs de lijn
    if line_rd is not None:
        for L in logical:
            L["_pos"] = line_rd.project(Point(sum(L["x"]) / len(L["x"]), sum(L["y"]) / len(L["y"])))
        logical.sort(key=lambda L: L["_pos"])
    out = []
    for L in logical:
        lon = sum(m["lon"] for m in L["members"]) / len(L["members"])
        lat = sum(m["lat"] for m in L["members"]) / len(L["members"])
        out.append(
            {
                "id": L["members"][0]["stop_id"],
                "name": L["name"],
                "lon": round(lon, 7),
                "lat": round(lat, 7),
                "members": [m["stop_id"] for m in L["members"]],
            }
        )
    return out
