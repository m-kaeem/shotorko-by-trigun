"""Ward plans: the shared Phase 2 data format (see docs/PHASE2_PLAN_CONTRACT.md).

Requirement 1 fills supplies, priority and spraying teams (src/resources.py);
Requirement 2 adds the optional ``overflow`` block (src/surge.py). Requirement 3
fills ``simulation`` when crews are deployed: if a module ``src/vector_sim.py``
exposes ``simulate(engine, date, h, deployments, scenario) -> {ward_id: {...}}``,
it is used; otherwise ``simulation`` is null.

Offline and deterministic: only the local model and the dataset are used.
"""
from __future__ import annotations

import sys
from functools import lru_cache

import pandas as pd

from . import config as C
from .resources import SUPPLY_COLS, allocate
from .surge import load_capacity, route

ITEM_KEYS = {"saline_bags_needed": "saline_bags", "diagnostic_kits_needed": "diagnostic_kits",
             "hospital_beds_required": "hospital_beds"}
LEVEL = {"ok": "none", "deficit": "partial", "critical": "critical"}


def _scenario_key(scenario):
    if scenario is None or not getattr(scenario, "active", False):
        return None
    return (scenario.rain_multiplier, scenario.temp_delta_c, scenario.humidity_delta_pct, scenario.weeks)


@lru_cache(maxsize=128)
def _forecast(engine_id: int, date: str, h: int, skey):
    engine = _ENGINES[engine_id]
    sc = None
    if skey is not None:
        from .predict import Scenario
        sc = Scenario(*skey)
    fc = engine.forecast(pd.Timestamp(date), None, sc)
    fc = fc[fc.horizon == h]
    if fc.empty:
        raise ValueError(f"no forecast for {date} at horizon {h}")
    w = engine.wards.set_index("ward_id")["population_density_sqkm"]
    return pd.DataFrame({
        "ward_id": fc.ward_id.values, "city": fc.city.values, "density": fc.ward_id.map(w).astype(int).values,
        "tier": fc.tier.values, "pred": fc.pred_cases.round(2).values, "lo": fc.lo80.round(2).values,
        "hi": fc.hi80.round(2).values, "score": fc.risk_score.round(0).astype(int).values,
        "target": pd.to_datetime(fc.target_date).dt.strftime("%Y-%m-%d").values})


_ENGINES: dict = {}


def _positions(positions):
    if positions is not None:
        return positions
    api = sys.modules.get("src.api")      # same schematic layout as the map, if the server is loaded
    return getattr(api, "POS", None)


def _simulation(engine, date, h, deployments, scenario):
    if not deployments:
        return {}
    try:
        from .vector_sim import simulate   # Requirement 3, when it lands
    except ImportError:
        return {}
    return simulate(engine, date, h, deployments, scenario) or {}


def build_plans(engine, date: str, h: int, stock_pct: float = 100.0, total_crews: int = 20,
                deployments: dict | None = None, scenario=None, positions: dict | None = None,
                floating_beds=None, min_share_pct: float = 0.0, emergency_bedding_pct: float = 0.0) -> list[dict]:
    """One ward plan per ward (all 60). stock_pct is a percent (30-100).

    floating_beds (optional, Phase 2 extra): reserve beds per city, {city: beds} or one int for every
    city. They are opened, after transfers, at wards whose patients the city's beds can't take.
    min_share_pct (optional): lifeline, 0-50. Every ward first gets at least this % of each supply.
    emergency_bedding_pct (optional): 0-50, extra floor beds per ward as a % of its beds."""
    if h not in C.HORIZONS:
        raise ValueError("h must be 2, 3 or 4")
    total_crews = int(total_crews)
    if total_crews < 0:
        raise ValueError("total_crews must be >= 0")
    _ENGINES[id(engine)] = engine
    fc = _forecast(id(engine), str(date), int(h), _scenario_key(scenario)).copy()

    if not 0 <= float(emergency_bedding_pct) <= 50:
        raise ValueError("emergency_bedding_pct must be between 0 and 50")
    if not 0 <= float(min_share_pct) <= 50:
        raise ValueError("min_share_pct must be between 0 and 50")
    alloc = allocate(fc, stock_pct=float(stock_pct) / 100.0, crews=total_crews, basis="expected",
                     min_share=float(min_share_pct) / 100.0)
    by_ward = {w["ward"]: w for w in alloc["wards"]}

    if deployments is not None:
        unknown = set(deployments) - set(fc.ward_id)
        if unknown:
            raise ValueError(f"unknown ward_id in deployments: {sorted(unknown)[:3]}")
        dep = {k: int(v) for k, v in deployments.items()}
        if any(v < 0 for v in dep.values()):
            raise ValueError("crews_sent must be >= 0")
        if sum(dep.values()) > total_crews:
            raise ValueError(f"deployments use {sum(dep.values())} crews but only {total_crews} are available")
        teams = {w: dep.get(w, 0) for w in fc.ward_id}
    else:
        teams = {w: by_ward[w]["crews_assigned"] for w in fc.ward_id}

    surge = route(fc, load_capacity(str(engine.bundle["risk"]["fit_end"])), positions=_positions(positions),
                  floating=floating_beds, bedding=float(emergency_bedding_pct) / 100.0)
    s_ward = {w["ward"]: w for w in surge["wards"]}
    sends: dict = {}
    for t in surge["transfers"]:
        sends.setdefault(t["from"], []).append({"ward_id": t["to"], "beds": int(t["patients"])})

    sims = _simulation(engine, str(date), int(h), deployments, scenario)
    shown_pct = stock_pct if float(stock_pct) != int(stock_pct) else int(stock_pct)

    plans = []
    for r in fc.itertuples():
        a, s = by_ward[r.ward_id], s_ward[r.ward_id]
        req = {ITEM_KEYS[k]: int(a[k]["required"]) for k in SUPPLY_COLS}
        got = {ITEM_KEYS[k]: int(a[k]["allocated"]) for k in SUPPLY_COLS}
        cov = {k: (round(100.0 * got[k] / req[k], 1) if req[k] else 100.0) for k in req}
        plans.append({
            "ward_id": r.ward_id, "city": r.city, "date": str(date), "h": int(h), "target_week": r.target,
            "predicted_cases": float(r.pred), "cases_low": float(r.lo), "cases_high": float(r.hi),
            "tier": r.tier, "risk_score": int(r.score), "population_density_sqkm": int(r.density),
            "required": req, "allocated": got, "deficit": {k: req[k] - got[k] for k in req},
            "coverage_pct": cov, "deficit_level": LEVEL[a["status"]],
            "priority_rank": int(a["rank"]), "spray_teams": int(teams[r.ward_id]),
            "simulation": sims.get(r.ward_id),
            "stock_pct": shown_pct, "total_crews": total_crews, "source": "engine",
            # Requirement 2 (optional block in the contract) + extra context
            "overflow": {"beds_short": int(s["required"] - s["local"]), "send_to": sends.get(r.ward_id, []),
                         "local_capacity": int(s["capacity"]), "received": int(s["received"]),
                         "floating_beds": int(s["floating"]), "emergency_beds": int(s["emergency"]),
                         "unplaced": int(s["unresolved"])},
            "spray_teams_needed": int(a["crews_needed"]),
        })
    return plans


def totals(plans: list[dict]) -> dict:
    keys = ("saline_bags", "diagnostic_kits", "hospital_beds")
    out = {b: {k: sum(p[b][k] for p in plans) for k in keys} for b in ("required", "allocated", "deficit")}
    out["spray_teams"] = sum(p["spray_teams"] for p in plans)
    return out
