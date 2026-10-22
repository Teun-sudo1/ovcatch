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


GEEN = "__geen__"  # woonobjecten buiten elke buurt


def step_a(vbo: gpd.GeoDataFrame, grid: gpd.GeoDataFrame, buurten: gpd.GeoDataFrame, cfg: Config) -> tuple[gpd.GeoDataFrame, dict]:
    """Stap A per vierkant + buurtcode + gewicht. Werkt per tegel, mits vierkanten niet over tegels heen vallen.

    Kolommen erbij: inwoners, pop_source ('vierkant' of '' = nog te doen), buurtcode, _w (gewicht).
    Geeft ook {pop_grid_total, pop_grid_unassigned}.
    """
    vbo = vbo.copy()
    vbo["inwoners"] = 0.0
    vbo["pop_source"] = ""
    vbo["_w"] = 0.0
    b_attr = _assign_poly(vbo, buurten, ["buurtcode"])
    vbo["buurtcode"] = b_attr["buurtcode"].astype("object").where(b_attr["buurtcode"].notna(), None)
    stats = {"pop_grid_total": float(grid["inwoners"].sum(skipna=True)), "pop_grid_unassigned": 0.0}
    woon_idx = vbo.index[vbo["is_woon"].astype(bool)]
    if not len(woon_idx):
        stats["pop_grid_unassigned"] = stats["pop_grid_total"]
        return vbo, stats
    woon = vbo.loc[woon_idx]
    w = pd.Series(dwelling_weights(woon, cfg), index=woon_idx)
    vbo.loc[woon_idx, "_w"] = w.values

    g_attr = _assign_poly(woon, grid, ["cell_id", "inwoners"])
    cell = g_attr["cell_id"]
    cell_inw = g_attr["inwoners"]
    in_valid = cell.notna() & cell_inw.notna()
    sum_w = w[in_valid].groupby(cell[in_valid]).transform("sum")
    est_a = cell_inw[in_valid] * w[in_valid] / sum_w.replace(0, np.nan)
    vbo.loc[est_a.index, "inwoners"] = est_a.fillna(0).values
    vbo.loc[est_a.index, "pop_source"] = "vierkant"
    used_cells = set(cell[in_valid].astype(int).unique())
    stats["pop_grid_unassigned"] = float(grid.loc[~grid["cell_id"].isin(used_cells), "inwoners"].sum(skipna=True))
    return vbo, stats


def buurt_summary(vbo: pd.DataFrame) -> pd.DataFrame:
    """Per buurt de sommen die stap B/C nodig heeft. Optelbaar over tegels."""
    woon = vbo[vbo["is_woon"].astype(bool)]
    code = woon["buurtcode"].fillna(GEEN)
    rest = woon["pop_source"] == ""
    return pd.DataFrame(
        {
            "assigned_a": woon["inwoners"].where(~rest, 0.0).groupby(code).sum(),
            "n_rest": rest.groupby(code).sum(),
            "w_rest": woon["_w"].where(rest, 0.0).groupby(code).sum(),
            "n_bag_woon": code.groupby(code).size(),
        }
    ).fillna(0)


def buurt_factors(summary: pd.DataFrame, buurten: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Per buurt: inwoners per gewichtseenheid voor de rest-woningen, en de bron (stap B of C).

    - buurt onbekend/geheim -> landelijk gemiddelde huishoudensgrootte
    - rest van buurttotaal, als die per woning binnen 0,5–2x het buurtgemiddelde ligt
    - anders buurtgemiddelde inwoners per woning (woningvoorraad CBS, of BAG-telling)
    """
    nat = float(cfg["disaggregation"]["national_persons_per_dwelling"])
    s = summary[summary["n_rest"] > 0].copy()
    bt = buurten.set_index("buurtcode")[["inwoners", "woningen"]]
    b_inw = bt["inwoners"].reindex(s.index)
    b_won = bt["woningen"].reindex(s.index).fillna(s["n_bag_woon"])
    avg = (b_inw / b_won.where(b_won > 0)).fillna(nat)
    residual = b_inw - s["assigned_a"]
    per = residual / s["n_rest"]
    use_rest = (residual > 0) & (per >= 0.5 * avg) & (per <= 2.0 * avg)
    total = np.where(b_inw.isna(), s["n_rest"] * nat, np.where(use_rest, residual, s["n_rest"] * avg))
    source = np.where(b_inw.isna(), "landelijk", np.where(use_rest, "buurt_rest", "buurt_gem"))
    return pd.DataFrame({"factor": total / s["w_rest"], "source": source}, index=s.index)


def apply_rest(vbo: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Stap B/C: rest-woningen krijgen gewicht x buurtfactor."""
    rest = vbo["is_woon"].astype(bool) & (vbo["pop_source"] == "")
    if rest.any():
        code = vbo.loc[rest, "buurtcode"].fillna(GEEN)
        f = factors.reindex(code)
        vbo.loc[rest, "inwoners"] = (vbo.loc[rest, "_w"].to_numpy() * f["factor"].to_numpy())
        vbo.loc[rest, "pop_source"] = f["source"].to_numpy()
    return vbo


def report(vbo: pd.DataFrame, buurten: pd.DataFrame, cfg: Config, grid_stats: dict) -> tuple[DisaggReport, pd.DataFrame]:
    woon = vbo[vbo["is_woon"].astype(bool)]
    rep = DisaggReport(
        n_woon=len(woon),
        pop_grid_total=grid_stats["pop_grid_total"],
        pop_grid_unassigned=grid_stats["pop_grid_unassigned"],
    )
    rep.by_source = woon["pop_source"].value_counts().to_dict()
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
    return rep, val


def disaggregate(
    vbo: gpd.GeoDataFrame,
    grid: gpd.GeoDataFrame,
    buurten: gpd.GeoDataFrame,
    cfg: Config,
) -> tuple[gpd.GeoDataFrame, DisaggReport, pd.DataFrame]:
    """Geeft (vbo met kolommen inwoners/pop_source/buurtcode, rapport, validatietabel per buurt).

    In één keer; voor heel NL doet prepare hetzelfde per tegel (zie data/prepare.py).
    """
    vbo, stats = step_a(vbo, grid, buurten, cfg)
    vbo = apply_rest(vbo, buurt_factors(buurt_summary(vbo), buurten, cfg))
    rep, val = report(vbo, buurten, cfg, stats)
    return vbo.drop(columns="_w"), rep, val


def age_shares(vbo: gpd.GeoDataFrame, grid: gpd.GeoDataFrame, buurten: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Aandeel per leeftijdsgroep per woonobject: vierkant (aantal/inwoners), anders buurtpercentage,
    anders landelijk. Per groep apart: CBS maakt een groep in een vierkant soms geheim en de rest niet.
    Kolommen share_<groep>; werkt per tegel (vierkanten van die tegel)."""
    nat = cfg["disaggregation"].get("national_age_shares", {})
    out = pd.DataFrame(index=vbo.index)
    if not nat:
        return out
    woon = vbo[vbo["is_woon"].astype(bool)]
    cols = [f"age_{g}" for g in nat if f"age_{g}" in grid.columns]
    cell = _assign_poly(woon, grid, ["inwoners", *cols]) if len(woon) else pd.DataFrame(index=woon.index)
    bt = buurten.set_index("buurtcode")
    code = woon["buurtcode"]
    for g, share in nat.items():
        s = pd.Series(float(share), index=woon.index)
        if f"pct_{g}" in bt.columns:
            b = bt[f"pct_{g}"].reindex(code).to_numpy()
            s = s.where(np.isnan(b), b)
        if f"age_{g}" in cell.columns:
            inw = cell["inwoners"].to_numpy(dtype=float)
            c = cell[f"age_{g}"].to_numpy(dtype=float) / np.where(inw > 0, inw, np.nan)
            s = s.where(np.isnan(c), np.clip(c, 0, 1))
        out[f"share_{g}"] = 0.0
        out.loc[woon.index, f"share_{g}"] = s.to_numpy()
    return out


def add_age_groups(vbo: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    """inw_<groep> = geschatte inwoners x aandeel; de aandeel-kolommen gaan eruit."""
    for c in [c for c in shares.columns if c.startswith("share_")]:
        vbo[f"inw_{c[6:]}"] = vbo["inwoners"].to_numpy() * shares[c].reindex(vbo.index).fillna(0).to_numpy()
    return vbo


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
