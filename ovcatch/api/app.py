"""FastAPI-backend + statische web-UI."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..analysis import analyse, compare, comparison_csv, metric_labels, route_points
from ..config import PROJECT_ROOT, Config, get_config
from ..gtfs import Feed, list_feeds
from ..routing import make_router
from ..store import DataStore

log = logging.getLogger(__name__)
WEB_DIR = PROJECT_ROOT / "web"


def _safe_name(name: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9_\- ]+", "", name).strip().replace(" ", "_")
    if not n:
        raise HTTPException(400, "Ongeldige scenarionaam")
    return n[:80]


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    cfg.ensure_dirs()
    router = make_router(cfg)
    store = DataStore(cfg.addresses_path, cfg.amenities_path)
    app = FastAPI(title="OV-catchment tool", version="0.1.0")

    @lru_cache(maxsize=8)
    def feed(name: str) -> Feed:
        if name not in list_feeds(cfg.gtfs_dir):
            raise HTTPException(404, f"Feed '{name}' niet gevonden. Draai `python -m ovcatch gtfs-index`.")
        return Feed(cfg.gtfs_dir / name)

    def run(fn, *a):
        try:
            return fn(*a)
        except FileNotFoundError as e:
            raise HTTPException(409, str(e)) from e
        except HTTPException:
            raise
        except Exception as e:  # router/netwerkfouten zichtbaar maken in de UI
            log.exception("Fout")
            raise HTTPException(502, f"{type(e).__name__}: {e}") from e

    @app.get("/api/config")
    def get_cfg() -> dict[str, Any]:
        return {
            "thresholds": cfg["thresholds"],
            "router": router.health() | {"approximate": bool(router.approximate)},
            "demo": cfg["region"]["name"] == "demo",
            "data_available": store.available(),
            "labels": metric_labels(cfg),
            "bbox": cfg["region"].get("bbox_wgs84"),
            "region": cfg["region"]["name"],
        }

    @app.get("/api/feeds")
    def feeds() -> list[str]:
        return list_feeds(cfg.gtfs_dir)

    @app.get("/api/routes")
    def routes(feed_name: str = Query(..., alias="feed"), q: str = "", limit: int = 50):
        return feed(feed_name).search_routes(q, cfg["line"]["bus_route_types"], limit)

    @app.get("/api/line")
    def line(feed_name: str = Query(..., alias="feed"), route_id: str = Query(...)):
        try:
            return feed(feed_name).build_line(route_id, cfg["line"]["merge_distance_m"])
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/api/route")
    def route(scenario: dict = Body(...)):
        pts, mask = route_points(scenario)
        if len(pts) < 2:
            return {"geometry": [list(p) for p in pts], "length_m": 0, "duration_s": 0}
        rr = run(router.route, pts, mask)
        return {"geometry": rr.coords, "length_m": round(rr.length_m, 1), "duration_s": round(rr.duration_s, 1)}

    @app.post("/api/analyse")
    def do_analyse(scenario: dict = Body(...)):
        return run(analyse, scenario, cfg, router, store)

    @app.post("/api/compare")
    def do_compare(body: dict = Body(...)):
        b = run(analyse, body["baseline"], cfg, router, store)
        v = run(analyse, body["variant"], cfg, router, store)
        return {"baseline": b, "variant": v, "comparison": compare(b, v)}

    @app.post("/api/export.csv", response_class=PlainTextResponse)
    def export_csv(body: dict = Body(...)):
        b = run(analyse, body["baseline"], cfg, router, store, False)
        v = run(analyse, body["variant"], cfg, router, store, False)
        csv = comparison_csv(compare(b, v))
        return PlainTextResponse(
            "﻿" + csv,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="ov-catchment-vergelijking.csv"'},
        )

    @app.get("/api/scenarios")
    def list_scenarios():
        return sorted(p.stem for p in cfg.scenario_dir.glob("*.json"))

    @app.get("/api/scenarios/{name}")
    def get_scenario(name: str):
        p = cfg.scenario_dir / f"{_safe_name(name)}.json"
        if not p.exists():
            raise HTTPException(404, "Scenario niet gevonden")
        return json.loads(p.read_text(encoding="utf-8"))

    @app.put("/api/scenarios/{name}")
    def put_scenario(name: str, body: dict = Body(...)):
        p = cfg.scenario_dir / f"{_safe_name(name)}.json"
        p.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"saved": p.stem}

    @app.delete("/api/scenarios/{name}")
    def del_scenario(name: str):
        p = cfg.scenario_dir / f"{_safe_name(name)}.json"
        if p.exists():
            p.unlink()
        return {"deleted": p.stem}

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app


def app_factory() -> FastAPI:  # voor `uvicorn --factory`
    return create_app()
