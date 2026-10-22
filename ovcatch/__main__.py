"""CLI: python -m ovcatch <commando>

  download     [--only bag cbs_vk cbs_buurten osm gtfs]  ruwe bronnen downloaden
  prepare      [--steps addresses amenities]             preprocessing -> data/processed
  gtfs-index   [--gtfs pad.zip] [--name naam]            GTFS-feed indexeren (default: data/gtfs/gtfs-nl.zip)
  routes       --feed naam [--q zoekterm]                lijnen zoeken
  analyse      --feed naam --route ROUTE_ID [--out x.json]  baseline-analyse van een lijn
  validate                                               toon disaggregatie-validatie
  web-export   --feed naam [--out data/web]              alle bushaltes/lijnen vooraf berekenen voor de webviewer
  demo                                                   synthetische demo-data bouwen
  serve        [--host 0.0.0.0] [--port 8000]            web-app starten
  all                                                    download + prepare + gtfs-index
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ovcatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="pad naar config.yaml (of env OVCATCH_CONFIG)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("download"); p.add_argument("--only", nargs="*"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("prepare"); p.add_argument("--steps", nargs="*")
    p = sub.add_parser("gtfs-index"); p.add_argument("--gtfs"); p.add_argument("--name"); p.add_argument("--keep-raw", action="store_true")
    p = sub.add_parser("routes"); p.add_argument("--feed", required=True); p.add_argument("--q", default="")
    p = sub.add_parser("analyse"); p.add_argument("--feed", required=True); p.add_argument("--route", required=True); p.add_argument("--out")
    sub.add_parser("validate")
    p = sub.add_parser("web-export"); p.add_argument("--feed", default="gtfs-nl"); p.add_argument("--out"); p.add_argument("--processes", type=int); p.add_argument("--cells-only", action="store_true")
    sub.add_parser("demo")
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000)
    sub.add_parser("all")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import os

    if a.config:
        os.environ["OVCATCH_CONFIG"] = str(Path(a.config).resolve())
    from .config import get_config

    cfg = get_config()
    cfg.ensure_dirs()

    if a.cmd in ("download", "all"):
        from .data.download import download_all

        download_all(cfg, getattr(a, "only", None), getattr(a, "force", False))
    if a.cmd in ("prepare", "all"):
        from .data.prepare import prepare_all

        print(json.dumps(prepare_all(cfg, getattr(a, "steps", None)), indent=2, ensure_ascii=False, default=str))
    if a.cmd in ("gtfs-index", "all"):
        from .data.download import raw_paths
        from .gtfs import index_feed

        z = Path(getattr(a, "gtfs", None) or raw_paths(cfg)["gtfs"])
        index_feed(z, cfg.gtfs_dir, getattr(a, "name", None), getattr(a, "keep_raw", False))
    if a.cmd == "routes":
        from .gtfs import Feed

        for r in Feed(cfg.gtfs_dir / a.feed).search_routes(a.q, cfg["line"]["bus_route_types"]):
            print(f"{r['route_id']:>20}  {r['agency']:<20} {r['short_name']:<6} {r['long_name']}")
    if a.cmd == "analyse":
        from .analysis import analyse
        from .gtfs import Feed
        from .routing import make_router
        from .store import DataStore

        line = Feed(cfg.gtfs_dir / a.feed).build_line(a.route, cfg["line"]["merge_distance_m"])
        res = analyse(line, cfg, make_router(cfg), DataStore(cfg.addresses_path, cfg.amenities_path), with_points=False)
        res.pop("isochrones", None)
        if a.out:
            Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
        _print_summary(res)
    if a.cmd == "validate":
        f = cfg.processed_dir / "disaggregatie_rapport.json"
        print(f.read_text() if f.exists() else "Nog geen rapport; draai `prepare`.")
    if a.cmd == "web-export":
        from .webexport import export_cells, export_web

        out = Path(a.out) if a.out else cfg.data_dir / "web"
        if a.cells_only:  # alleen de vierkanten (bewerken in de browser); meta.json bijwerken
            res = export_cells(cfg, out)
            meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            (out / "meta.json").write_text(json.dumps(meta | res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        else:
            res = export_web(cfg, a.feed, out, a.processes)
        print(json.dumps(res, indent=2))
    if a.cmd == "demo":
        from .demo import build_demo

        print(json.dumps(build_demo(cfg), indent=2, ensure_ascii=False))
    if a.cmd == "serve":
        import uvicorn

        from .api.app import create_app

        uvicorn.run(create_app(cfg), host=a.host, port=a.port)
    return 0


def _print_summary(res: dict) -> None:
    print(f"\n{res['name']}  — router: {res['router']['engine']}{' (BENADERING)' if res['router']['approximate'] else ''}")
    r = res["route"]
    print(f"Route: {r['length_m'] / 1000:.1f} km, {r['duration_s'] / 60:.0f} min (geschat), {r['n_stops']} haltes")
    for mode, m in res["modes"].items():
        print(f"\n[{mode}] lijntotaal (uniek)")
        for t, met in m["line"].items():
            print(f"  {t:>5} m: {met['inwoners']:>9.0f} inwoners  {met['n_woningen']:>7.0f} woningen  {met['banen_schatting']:>7.0f} banen*")
        t_mid = str(m["thresholds"][len(m["thresholds"]) // 2])
        print(f"  per halte ({t_mid} m, toegewezen):")
        for s in m["per_stop"]:
            d = s["by_threshold"][t_mid]["assigned"]
            print(f"    {s['name'][:40]:<40} {d['inwoners']:>8.0f} inw")
    print("\n* ruwe schatting o.b.v. m² BAG")


if __name__ == "__main__":
    sys.exit(main())
