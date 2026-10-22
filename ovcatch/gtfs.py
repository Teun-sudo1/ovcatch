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

NEEDED = ["agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt", "calendar.txt", "calendar_dates.txt"]
PEAK_S = (7 * 3600, 9 * 3600)  # ochtendspits 07:00–09:00


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
    svc_expr = "service_id" if "service_id" in tcols else "NULL"
    con.execute(
        f"CREATE TABLE trips AS SELECT trip_id, route_id, {dir_expr} AS direction_id, {shape_expr} AS shape_id, "
        f"{svc_expr} AS service_id FROM {csv('trips.txt')}"
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
               max({secs('arrival_time')}) - min({secs('departure_time')}) AS duration_s,
               min({secs('departure_time')}) AS start_s
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
    ref_date = _index_frequencies(con, raw, out, csv, secs)
    con.close()
    meta = {"name": name, "source_zip": str(zip_path), "ref_date": ref_date}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if not keep_raw:
        shutil.rmtree(raw, ignore_errors=True)
    log.info("GTFS index klaar: %s", out)
    return out


def _index_frequencies(con, raw: Path, out: Path, csv, secs) -> str | None:
    """Ritten en vertrekken op een representatieve werkdag (di/wo/do met de meeste ritten).

    Schrijft route_trips.parquet (route, richting: ritten per dag en in de ochtendspits) en
    stop_departures.parquet (perron, route_type: vertrekken per dag en in de spits).
    """
    parts = []
    if (raw / "calendar.txt").exists():
        parts.append(
            f"""SELECT c.service_id, CAST(g.d AS DATE) AS date
                FROM {csv('calendar.txt')} c,
                     generate_series(strptime(c.start_date, '%Y%m%d'), strptime(c.end_date, '%Y%m%d'), INTERVAL 1 DAY) g(d)
                WHERE [c.monday, c.tuesday, c.wednesday, c.thursday, c.friday, c.saturday, c.sunday][isodow(g.d)] = '1'"""
        )
    removed = ""
    if (raw / "calendar_dates.txt").exists():
        cd = csv("calendar_dates.txt")
        parts.append(f"SELECT service_id, CAST(strptime(date, '%Y%m%d') AS DATE) AS date FROM {cd} WHERE exception_type = '1'")
        removed = f"EXCEPT SELECT service_id, CAST(strptime(date, '%Y%m%d') AS DATE) FROM {cd} WHERE exception_type = '2'"
    if not parts:
        log.warning("Geen calendar(_dates).txt: geen frequenties")
        return None
    con.execute(f"CREATE TABLE service_dates AS SELECT DISTINCT * FROM ({' UNION ALL '.join(parts)}) {removed}")
    row = con.execute(
        """SELECT sd.date FROM trips t JOIN service_dates sd USING (service_id)
           GROUP BY sd.date
           ORDER BY (isodow(sd.date) IN (2, 3, 4)) DESC, (isodow(sd.date) <= 5) DESC, count(*) DESC, sd.date
           LIMIT 1"""
    ).fetchone()
    if not row:
        return None
    ref = row[0]
    con.execute("CREATE TABLE ref_trips AS SELECT t.* FROM trips t JOIN service_dates sd USING (service_id) WHERE sd.date = ?", [ref])
    # Een vertrek = halte waar je zonder te bellen kunt instappen, niet de laatste van de rit.
    # pickup_type 1 = geen instap, 2 = op afroep (Flex, belbus): telt niet als gewone frequentie.
    stcols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {csv('stop_times.txt')}").fetchall()}
    regular = "coalesce(nullif(pickup_type, ''), '0') NOT IN ('1', '2')" if "pickup_type" in stcols else "true"
    con.execute(
        f"""CREATE TABLE ref_deps AS
            SELECT trip_id, stop_id, dep_s FROM (
              SELECT trip_id, stop_id, {secs('departure_time')} AS dep_s, {regular} AS regular,
                     TRY_CAST(stop_sequence AS INTEGER) AS seq,
                     max(TRY_CAST(stop_sequence AS INTEGER)) OVER (PARTITION BY trip_id) AS last_seq
              FROM {csv('stop_times.txt')}
              WHERE trip_id IN (SELECT trip_id FROM ref_trips)
            ) WHERE regular AND seq < last_seq"""
    )
    p0, p1 = PEAK_S
    con.execute(
        f"""COPY (
              SELECT t.route_id, t.direction_id, count(*) AS trips_day,
                     count(*) FILTER (WHERE p.start_s >= {p0} AND p.start_s < {p1}) AS trips_peak
              FROM ref_trips t JOIN trip_patterns p USING (trip_id)
              WHERE t.trip_id IN (SELECT DISTINCT trip_id FROM ref_deps)
              GROUP BY 1, 2
            ) TO '{(out / 'route_trips.parquet').as_posix()}' (FORMAT parquet)"""
    )
    con.execute(
        f"""COPY (
              SELECT d.stop_id, TRY_CAST(r.route_type AS INTEGER) AS route_type, count(*) AS dep_day,
                     count(*) FILTER (WHERE d.dep_s >= {p0} AND d.dep_s < {p1}) AS dep_peak
              FROM ref_deps d JOIN ref_trips t USING (trip_id) JOIN routes r ON r.route_id = t.route_id
              GROUP BY 1, 2
            ) TO '{(out / 'stop_departures.parquet').as_posix()}' (FORMAT parquet)"""
    )
    log.info("Frequenties op referentiedag %s", ref)
    return ref.isoformat()


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
        # frequenties (ontbreken bij feeds zonder kalender of bij een oude index)
        rt, sd = self.dir / "route_trips.parquet", self.dir / "stop_departures.parquet"
        self.route_trips = pd.read_parquet(rt) if rt.exists() else None
        self.stop_departures = pd.read_parquet(sd) if sd.exists() else None
        mf = self.dir / "meta.json"
        self.ref_date = json.loads(mf.read_text()).get("ref_date") if mf.exists() else None

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
        # welke richting(en) een halte bedienen: 0 = heen (geometrie), 1 = terug. Haltes van één richting
        # (bijv. perrons aan weerszijden van een snelweg) horen niet allebei in één gereden route.
        ids = (set(main["pattern"].split("|")), set(other["pattern"].split("|")) if other is not None else set())
        for s in logical:
            s["dirs"] = [d for d in (0, 1) if any(m in ids[d] for m in s["members"])]
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
