"""TEMPORARY stand-in for Requirements 1 and 3 (see docs/PHASE2_PLAN_CONTRACT.md).

Produces ward plans in the exact contract shape so dispatch notices (Requirement 4) and the
offline checks (Requirement 5) work before the real allocation engine lands in src/plan.py.
Once src/plan.py exists, get_build_plans() returns that instead and this file is unused.

Stand-in rules (deliberately simple, labelled source="stub"):
- required  = forecast cases x per-case ratios from the Phase 2 columns (src/supply.py)
- allocated = a stock pool of stock_pct % of total required per item, handed out in priority order
              (tier, then risk score, then population density), each ward up to its requirement
- crews     = handed out in priority order: Critical 2, High 1, until total_crews runs out
- simulation and overflow are left null (Requirements 3 and 2)
"""
from __future__ import annotations

import math

import pandas as pd

from . import config as C
from .supply import ITEMS, required as need

TIER_RANK = {t: i for i, t in enumerate(C.TIERS)}
CREWS_FOR = {"Critical": 2, "High": 1, "Medium": 0, "Low": 0}


def build_plans(engine, date: str, h: int, stock_pct: float = 100.0, total_crews: int = 20,
                deployments: dict | None = None, scenario=None, forecast: pd.DataFrame | None = None) -> list[dict]:
    ref = pd.Timestamp(date)
    stock_pct = float(min(100.0, max(30.0, stock_pct)))
    fc = forecast if forecast is not None else engine.forecast(ref, None, scenario)
    fc = fc[fc.horizon == h]
    wards = engine.wards.set_index("ward_id")
    rows = []
    for r in fc.itertuples():
        rows.append({
            "ward_id": r.ward_id, "city": str(wards.at[r.ward_id, "city"]), "date": ref.strftime("%Y-%m-%d"), "h": int(h),
            "target_week": pd.Timestamp(r.target_date).strftime("%Y-%m-%d"),
            "predicted_cases": round(float(r.pred_cases), 2), "cases_low": round(float(r.lo80), 2),
            "cases_high": round(float(r.hi80), 2), "tier": str(r.tier), "risk_score": int(round(float(r.risk_score))),
            "population_density_sqkm": int(wards.at[r.ward_id, "population_density_sqkm"]),
            "required": need(r.ward_id, r.pred_cases),
        })
    # priority: highest tier, then risk score, then density
    rows.sort(key=lambda x: (-TIER_RANK[x["tier"]], -x["risk_score"], -x["population_density_sqkm"], x["ward_id"]))
    pool = {k: math.floor(sum(x["required"][k] for x in rows) * stock_pct / 100.0) for k in ITEMS}
    crews_left = int(max(0, total_crews))
    for i, x in enumerate(rows, start=1):
        x["priority_rank"] = i
        x["allocated"] = {}
        for k in ITEMS:
            give = min(x["required"][k], pool[k])
            pool[k] -= give
            x["allocated"][k] = int(give)
        x["deficit"] = {k: x["required"][k] - x["allocated"][k] for k in ITEMS}
        x["coverage_pct"] = {k: (100.0 if x["required"][k] == 0 else round(100.0 * x["allocated"][k] / x["required"][k], 1))
                             for k in ITEMS}
        x["deficit_level"] = ("none" if all(v == 0 for v in x["deficit"].values())
                              else "critical" if any(v < 50 for v in x["coverage_pct"].values()) else "partial")
        if deployments is not None:
            x["spray_teams"] = int(max(0, deployments.get(x["ward_id"], 0)))
        else:
            x["spray_teams"] = min(CREWS_FOR[x["tier"]], crews_left)
            crews_left -= x["spray_teams"]
        x["simulation"] = None
        x["overflow"] = None
        x["stock_pct"] = stock_pct
        x["total_crews"] = int(total_crews)
        x["source"] = "stub"
    return rows


def get_build_plans():
    """The real engine when a teammate has committed src/plan.py, otherwise this stand-in."""
    try:
        from .plan import build_plans as real   # type: ignore[import-not-found]
        return real, "engine"
    except ImportError:
        return build_plans, "stub"


def totals(plans: list[dict]) -> dict:
    return {blk: {k: int(sum(p[blk][k] for p in plans)) for k in ITEMS} for blk in ("required", "allocated", "deficit")}
