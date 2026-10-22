"""Synthetische demo-wereld: twee dorpen, een bedrijventerrein, een oude en een nieuwe buslijn.

Wordt gebruikt voor tests en om de app zonder echte data te kunnen draaien.
ALLE DATA HIERIN IS NEP.
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, box

from .config import Config
from .data.prepare import build_addresses, write_geoparquet
from .geo import RD, WGS84, rd_to_lonlat
from .gtfs import index_feed

log = logging.getLogger(__name__)

# Middelpunt (RD) — ergens in het Gooi, puur als kaartcontext
X0, Y0 = 143000.0, 476000.0


def make_world(seed: int = 42):
    rng = np.random.default_rng(seed)
    rows = []

    def cluster(cx, cy, r, n, doel, opp_mu, opp_sd, prefix):
        ang = rng.uniform(0, 2 * np.pi, n)
        rad = r * np.sqrt(rng.uniform(0, 1, n))
        for i in range(n):
            rows.append(
                {
                    "vbo_id": f"{prefix}{i:05d}",
                    "gebruiksdoel": doel,
                    "oppervlakte": float(max(15, rng.normal(opp_mu, opp_sd))),
                    "status": "Verblijfsobject in gebruik",
                    "geometry": Point(cx + rad[i] * np.cos(ang[i]), cy + rad[i] * np.sin(ang[i])),
                }
            )

    # Dorp Noord (groot) en Dorp Zuid
    cluster(X0, Y0 + 1500, 550, 1600, "woonfunctie", 110, 35, "N")
    cluster(X0 + 300, Y0 - 900, 420, 1000, "woonfunctie", 120, 40, "Z")
    # Centrum Noord: winkels + wonen boven winkels
    cluster(X0 - 50, Y0 + 1450, 120, 60, "winkelfunctie", 180, 80, "W")
    cluster(X0 - 50, Y0 + 1450, 120, 40, "woonfunctie,winkelfunctie", 140, 40, "M")
    # Bedrijventerrein Oost
    cluster(X0 + 2200, Y0 + 300, 300, 80, "kantoorfunctie", 900, 400, "K")
    cluster(X0 + 2300, Y0 + 100, 300, 40, "industriefunctie", 2500, 900, "I")
    # Verspreide bebouwing
    for i in range(120):
        rows.append(
            {
                "vbo_id": f"V{i:05d}",
                "gebruiksdoel": "woonfunctie",
                "oppervlakte": float(rng.normal(160, 50)),
                "status": "Verblijfsobject in gebruik",
                "geometry": Point(X0 + rng.uniform(-2000, 3000), Y0 + rng.uniform(-2500, 3000)),
            }
        )
    # Een paar objecten met status die niet meetelt
    rows.append({"vbo_id": "X00001", "gebruiksdoel": "woonfunctie", "oppervlakte": 90.0, "status": "Verblijfsobject gevormd", "geometry": Point(X0, Y0)})
    vbo = gpd.GeoDataFrame(rows, geometry="geometry", crs=RD)

    # "Ware" inwoners per woning (onbekend voor de methode)
    is_woon = vbo["gebruiksdoel"].str.contains("woonfunctie") & (vbo["status"] == "Verblijfsobject in gebruik")
    true_pp = np.where(is_woon, rng.choice([1, 1, 2, 2, 2, 3, 3, 4, 5], len(vbo)), 0).astype(float)
    vbo["_true_inw"] = true_pp

    # CBS 100m-grid
    gx = (vbo.geometry.x // 100) * 100
    gy = (vbo.geometry.y // 100) * 100
    agg = pd.DataFrame({"gx": gx, "gy": gy, "inw": vbo["_true_inw"], "won": is_woon.astype(int)}).groupby(["gx", "gy"]).sum().reset_index()
    agg = agg[(agg["inw"] >= 5) | (agg["won"] >= 5)].copy()
    agg["inw_pub"] = (np.round(agg["inw"] / 5) * 5).astype(float)
    secret = rng.uniform(0, 1, len(agg)) < 0.08
    agg.loc[secret, "inw_pub"] = -99997
    grid = gpd.GeoDataFrame(
        {"aantal_inwoners": agg["inw_pub"].values, "aantal_woningen": agg["won"].values},
        geometry=[box(x, y, x + 100, y + 100) for x, y in zip(agg["gx"], agg["gy"])],
        crs=RD,
    )

    # Buurten: 4 kwadranten rond het middelpunt (+ ruime rand)
    quads = {
        "BU0000NW": box(X0 - 4000, Y0 + 500, X0 + 500, Y0 + 4000),
        "BU0000NO": box(X0 + 500, Y0 + 500, X0 + 5000, Y0 + 4000),
        "BU0000ZW": box(X0 - 4000, Y0 - 4000, X0 + 500, Y0 + 500),
        "BU0000ZO": box(X0 + 500, Y0 - 4000, X0 + 5000, Y0 + 500),
    }
    b_rows = []
    for code, poly in quads.items():
        inside = vbo.within(poly)
        inw = float(vbo.loc[inside, "_true_inw"].sum())
        won = float((inside & is_woon).sum())
        b_rows.append({"buurtcode": code, "buurtnaam": f"Demo {code[-2:]}", "aantal_inwoners": inw, "woningvoorraad": won, "geometry": poly})
    buurten = gpd.GeoDataFrame(b_rows, geometry="geometry", crs=RD)

    # Voorzieningen
    am_rows = [
        ("school", "onderwijs", X0 + 100, Y0 + 1700),
        ("school", "onderwijs", X0 + 350, Y0 - 800),
        ("huisarts", "zorg", X0 - 100, Y0 + 1400),
        ("apotheek", "zorg", X0 - 20, Y0 + 1480),
        ("supermarkt", "supermarkt", X0 - 80, Y0 + 1420),
        ("supermarkt", "supermarkt", X0 + 250, Y0 - 950),
        ("sporthal", "sport", X0 + 700, Y0 + 300),
        ("bibliotheek", "gemeenschap", X0, Y0 + 1500),
    ]
    amen = gpd.GeoDataFrame(
        {"osm_id": [f"n{i}" for i in range(len(am_rows))], "categorie": [r[1] for r in am_rows], "tag": "", "naam": [r[0] for r in am_rows]},
        geometry=[Point(r[2], r[3]) for r in am_rows],
        crs=RD,
    )
    return vbo, grid, buurten, amen


def _gtfs_zip(path: Path, route_short: str, route_long: str, stops_rd: list[tuple[str, float, float]], shape_rd: list[tuple[float, float]]):
    """Schrijf een minimale GTFS-zip met één lijn, twee richtingen."""
    stops = []
    for sid, x, y in stops_rd:
        lon, lat = rd_to_lonlat(x, y)
        stops.append((sid, lon, lat))
    # tegenrichting: haltes ~25 m verschoven, eigen stop_id, zelfde naam
    rev = []
    for sid, x, y in reversed(stops_rd):
        lon, lat = rd_to_lonlat(x + 25, y)
        rev.append((sid + "_t", lon, lat))
    names = {sid: f"Demo, {sid.split('_')[0]}" for sid, *_ in stops_rd}
    files = {}
    files["agency.txt"] = "agency_id,agency_name,agency_url,agency_timezone\nDEMO,Demo Vervoer,https://example.org,Europe/Amsterdam\n"
    files["routes.txt"] = f"route_id,agency_id,route_short_name,route_long_name,route_type\nR1,DEMO,{route_short},{route_long},3\n"
    st = "stop_id,stop_name,stop_lat,stop_lon\n"
    for sid, lon, lat in stops + rev:
        st += f'{sid},"{names[sid.replace("_t", "")]}",{lat:.7f},{lon:.7f}\n'
    files["stops.txt"] = st
    trips = "route_id,service_id,trip_id,direction_id,shape_id\n"
    times = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    for k in range(6):
        for d, seq in ((0, stops), (1, rev)):
            tid = f"T{d}_{k}"
            trips += f"R1,WD,{tid},{d},S{d}\n"
            t0 = 7 * 3600 + k * 1800
            for i, (sid, *_r) in enumerate(seq):
                t = t0 + i * 120
                hh = f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"
                times += f"{tid},{hh},{hh},{sid},{i + 1}\n"
    files["trips.txt"] = trips
    # werkdagen in oktober 2026; dinsdag 13-10 vervalt (uitzondering), zaterdag 17-10 rijdt extra
    files["calendar.txt"] = "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nWD,1,1,1,1,1,0,0,20261001,20261031\n"
    files["calendar_dates.txt"] = "service_id,date,exception_type\nWD,20261013,2\nWD,20261017,1\n"
    files["stop_times.txt"] = times
    sh = "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
    for d, pts in ((0, shape_rd), (1, list(reversed(shape_rd)))):
        for i, (x, y) in enumerate(pts):
            lon, lat = rd_to_lonlat(x, y)
            sh += f"S{d},{lat:.7f},{lon:.7f},{i + 1}\n"
    files["shapes.txt"] = sh
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return path


def demo_feeds(out_dir: Path) -> list[Path]:
    # Oude lijn: door de dorpskernen
    old_stops = [
        ("Noordeinde", X0 - 200, Y0 + 2000),
        ("Centrum", X0 - 50, Y0 + 1450),
        ("Kerkplein", X0 + 50, Y0 + 900),
        ("Zuiderweg", X0 + 250, Y0 - 600),
        ("Zuidpark", X0 + 350, Y0 - 1200),
        ("Bedrijvenpark", X0 + 2200, Y0 + 300),
    ]
    old_shape = [(x, y) for _, x, y in old_stops[:-1]] + [(X0 + 1200, Y0 - 300), (X0 + 2200, Y0 + 300)]
    # Nieuwe lijn: rondweg oostelijk van de kernen, minder haltes
    new_stops = [
        ("Noordeinde", X0 - 200, Y0 + 2000),
        ("Rondweg Noord", X0 + 900, Y0 + 1300),
        ("Rondweg Zuid", X0 + 1000, Y0 - 700),
        ("Bedrijvenpark", X0 + 2200, Y0 + 300),
    ]
    new_shape = [(X0 - 200, Y0 + 2000), (X0 + 800, Y0 + 1900), (X0 + 900, Y0 + 1300), (X0 + 1000, Y0 - 700), (X0 + 1600, Y0 - 300), (X0 + 2200, Y0 + 300)]
    a = _gtfs_zip(out_dir / "demo_2024_oud.zip", "101", "Noordeinde - Bedrijvenpark (oude route)", old_stops, old_shape)
    b = _gtfs_zip(out_dir / "demo_2026_nieuw.zip", "101", "Noordeinde - Bedrijvenpark", new_stops, new_shape)
    return [a, b]


def build_demo(cfg: Config) -> dict:
    cfg.ensure_dirs()
    vbo_raw, grid_raw, buurten_raw, amen = make_world()
    from .data.bag import standardize_vbo
    from .data.cbs import standardize_buurten, standardize_vk

    vbo = standardize_vbo(vbo_raw.drop(columns="_true_inw"), cfg)
    grid = standardize_vk(grid_raw, cfg)
    buurten = standardize_buurten(buurten_raw, cfg)
    addresses, report = build_addresses(cfg, vbo, grid, buurten)
    write_geoparquet(addresses, cfg.addresses_path)
    write_geoparquet(amen, cfg.amenities_path)
    buurten.to_parquet(cfg.processed_dir / "buurten.parquet")
    for z in demo_feeds(cfg.raw_dir / "gtfs"):
        index_feed(z, cfg.gtfs_dir)
    return {"addresses": len(addresses), "report": report}


if __name__ == "__main__":  # pragma: no cover
    from .config import load_config

    logging.basicConfig(level=logging.INFO)
    print(build_demo(load_config(Path(__file__).resolve().parent.parent / "config.demo.yaml")))
