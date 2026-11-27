"""Season backtest for the crew simulator: would Shotorko's crew plan have beaten the usual rule?

For every week of the 2024 and 2025 dengue seasons (out-of-sample test years) the same
number of crews is deployed four ways and each plan is scored with the simulator:

* shotorko  - the optimiser (best_deployment: ward + crew type by marginal cases prevented)
* shotorko_larvicide - the same optimiser allowed larvicide crews only, so the gain from
              choosing *where* is separated from the gain of the indoor crew type
* tier_rule - Requirement 1's rule of thumb (Medium 1, High 2, Critical 3 crews, +1 if dense),
              shared by priority when crews run short; larvicide crews
* top_cases - one larvicide crew each to the wards with the most forecast cases

Scores are model-based (the same counterfactual as the simulator), so this compares plans,
it does not measure real-world impact. Each week's deployment is scored on its own 2-4 week
window. Heavy to compute (~7 min), so it is precomputed into artifacts/sim_season.json:

    python -m src.sim_season          # rebuild the artifact
"""
from __future__ import annotations

import json
import time

import pandas as pd

from . import config as C
from . import simulation as S

ARTIFACT = C.ARTIFACT_DIR / "sim_season.json"
SEASONS = {"2024": ("2024-05-06", "2024-11-25"), "2025": ("2025-05-05", "2025-11-24")}
CREWS = 20
POLICIES = ("shotorko", "shotorko_larvicide", "tier_rule", "top_cases")


def _tier_rule(engine, date, crews):
    from .plan import build_plans
    return {p["ward_id"]: p["spray_teams"] for p in build_plans(engine, date, 3, total_crews=crews) if p["spray_teams"]}


def _top_cases(engine, date, crews):
    fc = engine.forecast(pd.Timestamp(date))
    top = fc[fc.horizon == 3].sort_values("pred_cases", ascending=False).ward_id.head(crews)
    return {w: 1 for w in top}


def run(engine, crews: int = CREWS) -> dict:
    t0 = time.perf_counter()
    weeks, totals = [], {}
    dates = {pd.Timestamp(d).strftime("%Y-%m-%d") for d in engine.dates}
    for season, (a, b) in SEASONS.items():
        tot = {p: 0.0 for p in POLICIES}
        for d in pd.date_range(a, b, freq="7D"):
            date = d.strftime("%Y-%m-%d")
            if date not in dates:
                continue
            plans = {"shotorko": S.best_deployment(engine, date, crews, max_per_ward=4),
                     "shotorko_larvicide": S.best_deployment(engine, date, crews, max_per_ward=4, types=("larvicide",)),
                     "tier_rule": _tier_rule(engine, date, crews),
                     "top_cases": _top_cases(engine, date, crews)}
            row = {"date": date, "season": season}
            for p, dep in plans.items():
                s = S.summary(S.simulate(engine, date, dep, compare_delay=False))
                row[p] = round(s["cases_prevented"], 1)
                row[f"{p}_beds"] = round(s["beds_saved"], 1)
                tot[p] += s["cases_prevented"]
            row["forecast_cases"] = round(s["cases_without"], 1)
            weeks.append(row)
        totals[season] = {p: round(v, 1) for p, v in tot.items()}
    allt = {p: round(sum(t[p] for t in totals.values()), 1) for p in POLICIES}
    gain = lambda other: round(100 * (allt["shotorko"] / allt[other] - 1), 1) if allt[other] else None  # noqa: E731
    return {
        "crews_per_week": crews, "seasons": SEASONS, "weeks": weeks, "totals": totals, "all": allt,
        "shotorko_vs_tier_rule_pct": gain("tier_rule"), "shotorko_vs_top_cases_pct": gain("top_cases"),
        "larvicide_only_vs_tier_rule_pct": round(100 * (allt["shotorko_larvicide"] / allt["tier_rule"] - 1), 1)
        if allt["tier_rule"] else None,
        "shotorko_weeks_best": sum(1 for w in weeks if w["shotorko"] >= max(w["tier_rule"], w["top_cases"]) - 1e-9),
        "n_weeks": len(weeks),
        "note": "Model-based comparison of plans with the same crews, each week scored on its own 2-4 week window.",
        "compute_seconds": round(time.perf_counter() - t0, 1),
    }


def load() -> dict | None:
    return json.loads(ARTIFACT.read_text()) if ARTIFACT.exists() else None


if __name__ == "__main__":
    from .predict import Engine
    res = run(Engine())
    ARTIFACT.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "weeks"}, indent=1))
