"""Bevolking verdelen over BAG-woningen.

Methode (zie DECISIONS.md):
  A. CBS 100m-vierkant met bekend inwonertal -> verdelen over woonobjecten in dat
     vierkant, naar rato van (geknipte) oppervlakte / aantal / mix.
  B. Woonobjecten in geheime of ontbrekende vierkanten -> de *rest* van het
     buurt-inwonertal (buurt totaal minus wat al via A is toegewezen) verdelen.
     Is die rest onrealistisch (<= 0 of per woning buiten 0,5–2x het buurtgemiddelde),
     dan buurtgemiddelde inwoners/woning.
  C. Buurt ook geheim/onbekend -> landelijk gemiddelde huishoudensgrootte.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from .config import Config
from .geo import bbox_wgs_to_rd

log = logging.getLogger(__name__)


@dataclass
class DisaggReport:
    n_woon: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    pop_grid_total: float = 0.0
    pop_grid_unassigned: float = 0.0  # inwoners in vierkanten zonder woonobject
    pop_estimated_total: float = 0.0
    validation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n_woon": self.n_woon,
            "by_source": self.by_source,
            "pop_grid_total": round(self.pop_grid_total, 1),
            "pop_grid_unassigned": round(self.pop_grid_unassigned, 1),
            "pop_estimated_total": round(self.pop_estimated_total, 1),
            "validation": self.validation,
        }


def dwelling_weights(woon: pd.DataFrame, cfg: Config) -> np.ndarray:
    d = cfg["disaggregation"]
    lo, hi = d.get("area_clip_m2", [20, 400])
    area = woon["oppervlakte"].astype(float).clip(lo, hi)
    med = float(area.median()) if area.notna().any() else 1.0
    area = area.fillna(med).to_numpy()
    mode = d.get("weight", "area")
    if mode == "count":
        return np.ones(len(woon))
    if mode == "area":
        return area
    if mode == "mix":
        a = float(d.get("mix_alpha", 0.5))
        return a * area / max(area.mean(), 1e-9) + (1 - a)
    raise ValueError(f"Onbekende weging: {mode}")


def _assign_poly(points: gpd.GeoDataFrame, polys: gpd.GeoDataFrame, cols: list[str]) -> pd.DataFrame:
    """Ken per punt de attributen van het polygoon toe waarin het ligt (eerste match)."""
    j = gpd.sjoin(points[["geometry"]], polys[cols + ["geometry"]], how="left", predicate="intersects")
    j = j[~j.index.duplicated(keep="first")]
    return j.reindex(points.index)[cols]


def disaggregate(
    vbo: gpd.GeoDataFrame,
    grid: gpd.GeoDataFrame,
    buurten: gpd.GeoDataFrame,
    cfg: Config,
) -> tuple[gpd.GeoDataFrame, DisaggReport, pd.DataFrame]:
    """Geeft (vbo met kolommen inwoners/pop_source/buurtcode, rapport, validatietabel per buurt)."""
    rep = DisaggReport()
    vbo = vbo.copy()
    vbo["inwoners"] = 0.0
    vbo["pop_source"] = ""
    b_attr = _assign_poly(vbo, buurten, ["buurtcode"])
    vbo["buurtcode"] = b_attr["buurtcode"].astype("object").where(b_attr["buurtcode"].notna(), None)

    woon_idx = vbo.index[vbo["is_woon"].astype(bool)]
    woon = vbo.loc[woon_idx]
    rep.n_woon = len(woon)
    if woon.empty:
        return vbo, rep, pd.DataFrame()

    w = pd.Series(dwelling_weights(woon, cfg), index=woon_idx)

    # --- A: vierkanten
    g_attr = _assign_poly(woon, grid, ["cell_id", "inwoners"])
    cell = g_attr["cell_id"]
    cell_inw = g_attr["inwoners"]
    in_valid = cell.notna() & cell_inw.notna()
    sum_w = w[in_valid].groupby(cell[in_valid]).transform("sum")
    est_a = cell_inw[in_valid] * w[in_valid] / sum_w.replace(0, np.nan)
    vbo.loc[est_a.index, "inwoners"] = est_a.fillna(0).values
    vbo.loc[est_a.index, "pop_source"] = "vierkant"

    rep.pop_grid_total = float(grid["inwoners"].sum(skipna=True))
    used_cells = set(cell[in_valid].astype(int).unique())
    rep.pop_grid_unassigned = float(grid.loc[~grid["cell_id"].isin(used_cells), "inwoners"].sum(skipna=True))

    # --- B/C: rest via buurt
    rest_idx = woon_idx[~in_valid.to_numpy()]
    nat = float(cfg["disaggregation"]["national_persons_per_dwelling"])
    if len(rest_idx):
        bt = buurten.set_index("buurtcode")[["inwoners", "woningen"]]
        assigned_by_buurt = vbo.loc[woon_idx].groupby("buurtcode")["inwoners"].sum()
        bag_won = vbo.loc[woon_idx].groupby("buurtcode").size()
        rest = vbo.loc[rest_idx]
        for code, grp in rest.groupby(rest["buurtcode"].fillna("__geen__")):
            idx = grp.index
            ww = w.loc[idx]
            wn = ww / ww.sum() if ww.sum() > 0 else pd.Series(1 / len(idx), index=idx)
            b_inw = bt["inwoners"].get(code, np.nan) if code != "__geen__" else np.nan
            b_won = bt["woningen"].get(code, np.nan) if code != "__geen__" else np.nan
            if pd.isna(b_won):
                b_won = bag_won.get(code, np.nan)
            if pd.isna(b_inw):
                vbo.loc[idx, "inwoners"] = (len(idx) * nat * wn).values
                vbo.loc[idx, "pop_source"] = "landelijk"
                continue
            avg = b_inw / b_won if (pd.notna(b_won) and b_won > 0) else nat
            residual = b_inw - float(assigned_by_buurt.get(code, 0.0))
            per = residual / len(idx)
            if residual > 0 and 0.5 * avg <= per <= 2.0 * avg:
                vbo.loc[idx, "inwoners"] = (residual * wn).values
                vbo.loc[idx, "pop_source"] = "buurt_rest"
            else:
                vbo.loc[idx, "inwoners"] = (len(idx) * avg * wn).values
                vbo.loc[idx, "pop_source"] = "buurt_gem"

    rep.by_source = vbo.loc[woon_idx, "pop_source"].value_counts().to_dict()
    rep.pop_estimated_total = float(vbo["inwoners"].sum())

    val = validate_against_buurten(vbo, buurten, cfg)
    tol = cfg["disaggregation"]["validation"]["tolerance"]
    ok = val["abs_rel_err"] <= tol
    rep.validation = {
        "n_buurten": int(len(val)),
        "tolerance": tol,
        "share_within": round(float(ok.mean()) if len(val) else float("nan"), 4),
        "median_abs_rel_err": round(float(val["abs_rel_err"].median()) if len(val) else float("nan"), 4),
        "p90_abs_rel_err": round(float(val["abs_rel_err"].quantile(0.9)) if len(val) else float("nan"), 4),
        "min_share_within": cfg["disaggregation"]["validation"]["min_share_within"],
    }
    log.info("Disaggregatie: %s", rep.to_dict())
    return vbo, rep, val


def validate_against_buurten(vbo: pd.DataFrame, buurten: pd.DataFrame, cfg: Config, min_inw: float = 50) -> pd.DataFrame:
    """Vergelijk som geschatte inwoners per buurt met CBS-buurttotaal.

    Alleen buurten die geheel binnen de regio-bbox liggen: van randbuurten is de BAG
    afgeknipt, dus daar is het CBS-totaal niet vergelijkbaar.
    """
    est = vbo.groupby("buurtcode")["inwoners"].sum().rename("geschat")
    region = bbox_wgs_to_rd(cfg["region"].get("bbox_wgs84"))
    if region is not None:
        buurten = buurten[buurten.geometry.within(box(*region))]
    b = buurten.set_index("buurtcode")[["buurtnaam", "inwoners"]].rename(columns={"inwoners": "cbs"})
    v = b.join(est, how="inner")
    v = v[v["cbs"].notna() & (v["cbs"] >= min_inw)]
    v["rel_err"] = (v["geschat"] - v["cbs"]) / v["cbs"]
    v["abs_rel_err"] = v["rel_err"].abs()
    return v.reset_index()
