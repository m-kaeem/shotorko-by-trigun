"""Saline outlook, order-by dates, ORS for mild cases and a supply backtest (Phase 2 extras).

IV fluid stock is not publicly available in Bangladesh, so nothing here depends on it:

* Surge alarm: compare the forecast saline need for the coming weeks with how much saline the
  region needed in past weeks (2016-2023, the years the model is fitted on). A week above the
  75th percentile is "high"; above the 90th percentile is a "surge" where supply is likely to
  fall short (in 2023 demand reached 10-12 times the usual requirement).
* Order-by date: the forecast is 2-4 weeks ahead, so each target week gets an "order by" date
  = target week minus the supplier lead time the officer sets.
* ORS for mild cases: the officer sets the share of patients a doctor could treat with oral
  rehydration salts (WHO group A: can drink, passing urine, no warning signs). Their IV saline
  need is removed, so the remaining saline goes to patients with warning signs.
* Supply backtest: replay every week of 2024-25 with the model's real out-of-sample forecasts,
  share the same stock with several rules, and score each against what wards actually needed.

Deterministic, offline, numpy/pandas only.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from . import config as C
from .resources import SUPPLY_RATIOS, TIER_WEIGHT, round_half_up, strict_fill, water_fill, lifeline_fill

SALINE = "saline_bags_needed"
BUSY_Q, VERY_BUSY_Q = 0.75, 0.90
ORS_MAX = 0.70          # about 70% of non-shocked dengue patients can be managed with oral rehydration
DEFAULT_LEAD_DAYS = 7
TIERS = ["Low", "Medium", "High", "Critical"]


# ------------------------------------------------------------------ history
@lru_cache(maxsize=4)
def weekly_history(fit_end: str) -> np.ndarray:
    """Total saline bags the 60 wards needed each week, up to fit_end (sorted)."""
    head = pd.read_csv(C.DATA_PATH, nrows=0).columns
    cols = ["date", "dengue_cases"] + ([SALINE] if SALINE in head else [])
    df = pd.read_csv(C.DATA_PATH, usecols=cols, parse_dates=["date"])
    df = df[df.date <= pd.Timestamp(fit_end)]
    need = df[SALINE] if SALINE in df else pd.Series(round_half_up(df.dengue_cases * SUPPLY_RATIOS[SALINE]))
    return np.sort(need.groupby(df.date).sum().to_numpy())


def thresholds(fit_end: str) -> dict:
    h = weekly_history(fit_end)
    return {"typical": int(np.median(h)), "busy": int(np.quantile(h, BUSY_Q)),
            "very_busy": int(np.quantile(h, VERY_BUSY_Q)), "max": int(h.max()), "weeks": int(len(h)),
            "years": f"2016-{pd.Timestamp(fit_end).year}"}


def assess(need: int, fit_end: str) -> dict:
    """How unusual a week's saline need is, compared with history."""
    h, th = weekly_history(fit_end), thresholds(fit_end)
    level = "surge" if need >= th["very_busy"] else "high" if need >= th["busy"] else "normal"
    return {"level": level, "vs_busy": round(need / th["busy"], 2) if th["busy"] else None,
            "percentile": round(float(np.searchsorted(h, need, side="right")) / len(h) * 100, 1)}


# ------------------------------------------------------------------ outlook + order-by
def saline_need(cases, ors_share: float = 0.0):
    """IV saline bags for expected cases when `ors_share` of patients are treated with ORS instead."""
    c = np.clip(np.asarray(cases, dtype=float), 0, None) * (1 - ors_share)
    return np.ceil(np.round(c * SUPPLY_RATIOS[SALINE], 6)).astype(int)


def outlook(rows_by_h: dict, ref_date: str, fit_end: str, lead_days: int = DEFAULT_LEAD_DAYS,
            ors_share: float = 0.0, packets_per_patient: float | None = None) -> dict:
    """rows_by_h: {h: (target_date, [expected cases per ward])}. Region-wide saline outlook per horizon."""
    ors_share = float(min(max(ors_share, 0.0), ORS_MAX))
    ref = pd.Timestamp(ref_date)
    weeks = []
    for h in sorted(rows_by_h):
        target, cases = rows_by_h[h]
        cases = np.asarray(cases, dtype=float)
        need_full = int(saline_need(cases).sum())
        need = int(saline_need(cases, ors_share).sum())
        kits = int(np.ceil(np.round(np.clip(cases, 0, None) * SUPPLY_RATIOS["diagnostic_kits_needed"], 6)).sum())
        order_by = pd.Timestamp(target) - pd.Timedelta(days=int(lead_days))
        ors_patients = float(np.clip(cases, 0, None).sum() * ors_share)
        weeks.append({"h": int(h), "target": str(pd.Timestamp(target).date()), "saline": need,
                      "saline_without_ors": need_full, "kits": kits,
                      "ors_patients": round(ors_patients, 1),
                      "ors_packets": (int(math.ceil(ors_patients * packets_per_patient - 1e-9))
                                      if packets_per_patient else None),
                      "order_by": str(order_by.date()), "days_to_order": int((order_by - ref).days),
                      **assess(need_full, fit_end)})
    worst = max(weeks, key=lambda w: w["saline_without_ors"]) if weeks else None
    return {"ref_date": str(ref.date()), "lead_days": int(lead_days), "ors_share": ors_share,
            "packets_per_patient": packets_per_patient, "thresholds": thresholds(fit_end), "weeks": weeks,
            "alarm": worst["level"] if worst else "normal", "peak": worst}


# ------------------------------------------------------------------ supply backtest
METHODS = {
    "equal": "Equal split (same for every ward)",
    "reactive": "Last reported week (no forecast)",
    "weighted": "Shotorko: share by priority",
    "strict": "Shotorko: strict triage",
    "lifeline": "Shotorko: 50% minimum + priority",
}


@lru_cache(maxsize=2)
def _backtest_frame() -> pd.DataFrame:
    bt = pd.read_csv(C.BACKTEST_PATH, parse_dates=["date", "target_date"])
    dens = pd.read_csv(C.DATA_PATH, usecols=["ward_id", "population_density_sqkm"]).drop_duplicates("ward_id")
    return bt.merge(dens, on="ward_id", how="left")


def _equal(n: int, stock: int) -> np.ndarray:
    a = np.full(n, stock // n)
    a[: stock - a.sum()] += 1
    return a


def _proportional(weight, stock: int) -> np.ndarray:
    w = np.asarray(weight, dtype=float)
    if w.sum() <= 0:
        return _equal(len(w), stock)
    exact = stock * w / w.sum()
    a = np.floor(exact).astype(int)
    a[np.argsort(-(exact - a))[: stock - a.sum()]] += 1
    return a


def _shotorko(pred, hi, tier, dens, stock: int, mode: str) -> np.ndarray:
    """Plan on the expected forecast, then top spare stock up toward the 80% upper bound."""
    need = saline_need(pred)
    w = np.asarray([TIER_WEIGHT[TIERS[int(t)]] for t in tier]) * (dens / dens.mean())
    fill = strict_fill if mode == "strict" else water_fill
    if mode == "lifeline":
        first = lifeline_fill(need, w, stock, 0.5, water_fill)
    else:
        first = fill(need, w, stock)
    left = stock - int(first.sum())
    if left > 0:
        extra = np.maximum(saline_need(hi) - first, 0)
        first = first + water_fill(extra, w, left)
        left = stock - int(first.sum())
        if left > 0:          # still spare: spread it by forecast so nothing sits in the store
            first = first + _proportional(np.asarray(pred) + 1e-6, left)
    return first


@lru_cache(maxsize=32)
def supply_backtest(stock_pct: float = 0.6, h: int = 3, min_need: int = 5) -> dict:
    """Replay 2024-25 (out-of-sample) and score each sharing rule against the saline wards actually needed.

    Every rule gets the same stock each week: stock_pct x the total the wards actually needed.
    * reached: share of that stock that landed in wards that needed it (not over their real need).
    * critical_ward_weeks: ward-weeks needing >= min_need bags that got under half of their real need.
    """
    bt = _backtest_frame()
    bt = bt[(bt.horizon == h) & (bt.target_date >= pd.Timestamp(C.VAL_END) + pd.Timedelta(days=1))]
    # last reported week = actual cases at the reference date (what an officer without a forecast would use)
    last = _backtest_frame()[["target_date", "ward_id", "actual"]].drop_duplicates(["target_date", "ward_id"])
    last = last.rename(columns={"target_date": "date", "actual": "last_week"})
    bt = bt.merge(last, on=["date", "ward_id"], how="left").fillna({"last_week": 0.0})
    tot = {m: {"reached": 0, "stock": 0, "critical": 0, "short": 0} for m in METHODS}
    weeks = 0
    for _, g in bt.groupby("target_date"):
        g = g.sort_values("ward_id")
        actual = round_half_up(g.actual.to_numpy() * SUPPLY_RATIOS[SALINE])
        stock = int(math.floor(stock_pct * actual.sum() + 1e-9))
        if stock <= 0:
            continue
        weeks += 1
        dens = g.population_density_sqkm.to_numpy(dtype=float)
        alloc = {
            "equal": _equal(len(g), stock),
            "reactive": _proportional(g.last_week.to_numpy(), stock),
            "weighted": _shotorko(g.pred, g.hi, g.tier_pred, dens, stock, "weighted"),
            "strict": _shotorko(g.pred, g.hi, g.tier_pred, dens, stock, "strict"),
            "lifeline": _shotorko(g.pred, g.hi, g.tier_pred, dens, stock, "lifeline"),
        }
        big = actual >= min_need
        for m, a in alloc.items():
            reached = np.minimum(a, actual)
            tot[m]["reached"] += int(reached.sum())
            tot[m]["stock"] += stock
            tot[m]["critical"] += int((big & (a * 2 < actual)).sum())
            tot[m]["short"] += int((actual - reached).sum())
    rows = [{"method": m, "name": METHODS[m],
             "reached_pct": round(100 * t["reached"] / t["stock"], 1) if t["stock"] else None,
             "critical_ward_weeks": t["critical"], "bags_short": t["short"],
             "bags_misplaced": t["stock"] - t["reached"]} for m, t in tot.items()]
    best = max(rows, key=lambda r: (r["reached_pct"] or 0, -r["critical_ward_weeks"]))
    base = next(r for r in rows if r["method"] == "reactive")
    return {"stock_pct": stock_pct, "h": h, "weeks": weeks, "min_need": min_need,
            "period": "2024-2025 (never seen in training)", "methods": rows, "best": best["method"],
            "vs_reactive_critical": (base["critical_ward_weeks"] - best["critical_ward_weeks"])}
