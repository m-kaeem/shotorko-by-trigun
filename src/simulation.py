"""Requirement 3: "what-if" vector-control impact simulator.

Model-driven counterfactual. For every ward and for horizons 2, 3 and 4 the
same LightGBM models are run twice:

    without crews: the real feature row
    with crews:    the feature row the crews' work would produce

and the difference is the cases prevented. There is no fixed "x% fewer cases":
the gain depends on how much of that ward's forecast the model puts on the
features a crew can change, which is what makes the ROI ranking meaningful.

Two crew types (what a crew changes in the model's inputs):

* **larvicide**: larviciding + larval source reduction. Removes standing water, so
  it scales the breeding-water features (rain lags/totals, water-logging, poor
  sanitation and their rain interactions). Slow onset (larvae already in the water
  still emerge), lasting effect. Central effect calibrated to the Camino Verde
  cluster RCT, ~29.5% lower dengue risk (PMC4495677).
* **indoor**: targeted indoor residual spraying (non-pyrethroid) of the houses
  around recent cases. Kills the infected adult mosquitoes, so it cuts onward
  transmission from current cases (the recent-case features) and adult vector
  density (the breeding-water features, as the model's vector proxy). Fast onset,
  fades as new mosquitoes emerge. Cairns contact-tracing spraying cut transmission
  86-96% at treated premises (PMC5315446), so it is strong *where it is applied*;
  but each crew can only treat the houses around so many cases a week, so in a
  ward with many cases coverage is thin. Purely preventive with no recent cases,
  there is nothing to target.

Fogging (space spraying) is deliberately not a crew type: trials show no proven
case reduction (PMC4795802) and Dhaka Aedes is highly pyrethroid resistant
(PubMed 36942761).

Timing: effects depend on k = weeks between deployment and the target week, so
the simulator can also answer "what if we wait one week?" (preventive campaigns
beat reactive ones, Yucatán model PMC6042783).

Uncertainty: every result has a low / central / high band.

Deterministic, offline, no randomness. Public functions (seam with src/plan.py
via src/vector_sim.py):

    simulate(engine, date, deployments, scenario=None, delay_weeks=0) -> {ward_id: simulation}
    roi_ranking(engine, date, crews=1, scenario=None)                 -> wards ranked by cases prevented per crew
    best_deployment(engine, date, total_crews, scenario=None)         -> {ward_id: {"larvicide": a, "indoor": b}}
    attach_simulation(plans, engine, date, deployments, total_crews, scenario=None)
    summary(sims)
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import config as C

TYPES = ("larvicide", "indoor")
LEVELS = ("central", "low", "high")

# ---------------------------------------------------------------- larvicide crews
# GUESS: share of a ward's remaining breeding habitat one crew can treat in the window.
LARV_COVERAGE = 0.20
# Share of effective breeding water removed at full coverage. "central" gives ~28% fewer
# model-predicted cases at saturation in peak weeks (2024-09-02, 2025-07-07), close to the
# 29.5% RCT effect; "low"/"high" bracket it (~18% / ~38%).
LARV_MAX = {"low": 0.20, "central": 0.35, "high": 0.50}
# strength by weeks since deployment (k). GUESS on the exact shape: nothing in week 0-1,
# half by week 2, full from week 3.
LARV_ONSET = {0: 0.0, 1: 0.1, 2: 0.5, 3: 1.0, 4: 1.0}

# ---------------------------------------------------------------- indoor (targeted) spraying crews
# GUESS: index cases whose surrounding houses one crew can spray per week.
CASES_PER_CREW = 6.0
# Ward-level effect at full coverage of recent cases. Lower than the 86-96% measured at
# sprayed premises because not all transmission happens around known cases.
INDOOR_MAX = {"low": 0.35, "central": 0.50, "high": 0.65}
# fast onset, fading as untreated breeding sites produce new mosquitoes. GUESS on the shape.
INDOOR_ONSET = {0: 0.0, 1: 0.8, 2: 1.0, 3: 0.85, 4: 0.7}
RECENT_WEEKS = 2   # "recent cases" the indoor crews target
# GUESS: share of the ward's adult mosquitoes living around the treated houses. Indoor crews cut
# onward transmission from recent cases fully, but the ward-wide vector proxy only by this share.
INDOOR_VECTOR_SHARE = 0.4

MAX_CREWS_PER_WARD = 6

# Per-case supply ratios: the same ones Req 1 uses (src/resources.py SUPPLY_RATIOS) so
# "beds saved" / "saline saved" match the Operations tab. Fallback = Req 1's published values.
try:
    from .resources import SUPPLY_RATIOS as _R
    SALINE_PER_CASE = float(_R["saline_bags_needed"])
    KITS_PER_CASE = float(_R["diagnostic_kits_needed"])
    BEDS_PER_CASE = float(_R["hospital_beds_required"])
except (ImportError, KeyError, TypeError):
    SALINE_PER_CASE, KITS_PER_CASE, BEDS_PER_CASE = 2.385, 1.165, 0.298

# ---------------------------------------------------------------- money (BDT)
# hospital_case_bdt: societal cost of one hospitalised dengue episode in Dhaka, BDT 39,893
#   (US$479; household + provider; Sarker et al., PLOS NTD 2023, PMC10723663, 2019 data).
#   Only the hospitalised share of prevented cases (BEDS_PER_CASE) is counted, so savings are
#   conservative: outpatient costs and lost work days of non-hospitalised cases are left out.
# *_crew_bdt: GUESS for one crew over the 2-4 week window: 5 workers x BDT 800/day x 18 days
#   + chemicals and transport (larvicide ~BDT 28k, non-pyrethroid indoor insecticide ~BDT 48k).
#   Officers can override all three from the UI / API.
COSTS = {"hospital_case_bdt": 39893.0, "larvicide_crew_bdt": 100000.0, "indoor_crew_bdt": 120000.0}

_RAIN_PREFIXES = ("rain_lag", "rain_sum_")
_SCALED = ("water_logging_risk_index", "waterlog_x_rain4w", "sanit_gap_x_rain4w", "heavy_rain_weeks_4w")
_CASE_PREFIX = "cases_"


# ---------------------------------------------------------------- effect functions
def larv_reduction(crews: int, k: int, level: str = "central") -> float:
    """Share of breeding water removed by `crews` larvicide crews, k weeks after deployment."""
    n = max(int(crews), 0)
    return LARV_MAX[level] * (1.0 - (1.0 - LARV_COVERAGE) ** n) * LARV_ONSET.get(min(max(k, 0), 4), 1.0)


def indoor_coverage(crews: int, recent_cases: float) -> float:
    """Share of recent cases whose surroundings `crews` indoor crews can spray (diminishing returns)."""
    if crews <= 0 or recent_cases <= 0:
        return 0.0
    return float(1.0 - np.exp(-crews * CASES_PER_CREW / recent_cases))


def indoor_reduction(crews: int, recent_cases: float, k: int, level: str = "central") -> float:
    return INDOOR_MAX[level] * indoor_coverage(crews, recent_cases) * INDOOR_ONSET.get(min(max(k, 0), 4), 0.7)


def habitat_reduction(crews: int, h: int = 4, level: str = "central") -> float:
    """Back-compat helper: larvicide reduction h weeks after deploying now."""
    return larv_reduction(crews, h, level)


# ---------------------------------------------------------------- deployments
def _norm(engine, deployments) -> dict:
    """{ward: int | {"larvicide": a, "indoor": b}} -> {ward: (a, b)}; validates."""
    out = {}
    for w, v in (deployments or {}).items():
        if isinstance(v, dict):
            bad = set(v) - set(TYPES)
            if bad:
                raise ValueError(f"unknown crew type(s) {sorted(bad)}; use {list(TYPES)}")
            a, b = int(v.get("larvicide", 0)), int(v.get("indoor", 0))
        else:
            a, b = int(v), 0
        if a < 0 or b < 0:
            raise ValueError("crew counts must be >= 0")
        if a + b:
            out[w] = (a, b)
    unknown = set(out) - set(engine.wards.ward_id)
    if unknown:
        raise ValueError(f"unknown ward(s): {sorted(unknown)}")
    return out


def _clean(engine, deployments) -> dict:
    """Validated deployments in the API's output form {ward: {"larvicide": a, "indoor": b}}."""
    return {w: {"larvicide": a, "indoor": b} for w, (a, b) in _norm(engine, deployments).items()}


def crews_used(deployments: dict) -> int:
    return sum((v["larvicide"] + v["indoor"]) if isinstance(v, dict) else int(v) for v in deployments.values())


@lru_cache(maxsize=64)
def _recent_cases_cached(engine_id: int, date: str) -> dict:
    raw = _ENGINES[engine_id].raw
    d = pd.Timestamp(date)
    win = raw[(raw.date <= d) & (raw.date > d - pd.Timedelta(weeks=RECENT_WEEKS))]
    return win.groupby("ward_id")["dengue_cases"].sum().astype(float).to_dict()


def recent_cases(engine, date) -> dict:
    _ENGINES[id(engine)] = engine
    return _recent_cases_cached(id(engine), pd.Timestamp(date).strftime("%Y-%m-%d"))


# ---------------------------------------------------------------- core counterfactual
def _treat(X: pd.DataFrame, vec_keep: np.ndarray, case_keep: np.ndarray) -> pd.DataFrame:
    X = X.copy()
    vcols = [c for c in X.columns if c.startswith(_RAIN_PREFIXES) or c in _SCALED]
    X[vcols] = X[vcols].mul(vec_keep, axis=0)
    if "sanitation_score" in X.columns:   # fewer open containers and blocked drains
        X["sanitation_score"] = 1.0 - (1.0 - X["sanitation_score"]) * vec_keep
    ccols = [c for c in X.columns if c.startswith(_CASE_PREFIX)]
    X[ccols] = X[ccols].mul(case_keep, axis=0)
    return X


def _counterfactual(engine, date, dep: dict, scenario=None, delay_weeks: int = 0) -> pd.DataFrame:
    """One row per ward x horizon: prediction without crews and with crews at each effect level."""
    rc = recent_cases(engine, date)
    out = []
    for h in C.HORIZONS:
        rows = engine._rows(h, pd.Timestamp(date), None, scenario)
        if rows.empty:
            continue
        X = rows[engine.bundle["feature_cols"][h]]
        model = engine.bundle["models"][h]["point"]
        wards = rows["ward_id"].to_numpy()
        a = np.array([dep.get(w, (0, 0))[0] for w in wards])
        b = np.array([dep.get(w, (0, 0))[1] for w in wards])
        k = h - int(delay_weeks)
        base = model.predict(X).clip(0)
        frame = {"ward_id": wards, "horizon": h, "larvicide": a, "indoor": b, "without": base}
        active = (a + b) > 0
        for lv in LEVELS:
            rl = np.array([larv_reduction(x, k, lv) for x in a])
            ri = np.array([indoor_reduction(y, rc.get(w, 0.0), k, lv) for w, y in zip(wards, b)])
            vec_keep, case_keep = (1 - rl) * (1 - ri * INDOOR_VECTOR_SHARE), 1 - ri
            pred = model.predict(_treat(X, vec_keep, case_keep)).clip(0) if active.any() else base
            frame[f"with_{lv}"] = np.where(active, pred, base)
        out.append(pd.DataFrame(frame))
    if not out:
        raise ValueError(f"no forecast rows for {date}")
    df = pd.concat(out, ignore_index=True)
    # A tree model is not guaranteed monotone; spraying can't add cases, so floor the gain at 0.
    for lv in LEVELS:
        df[f"prevented_{lv}"] = (df["without"] - df[f"with_{lv}"]).clip(lower=0)
    df["with"], df["prevented"] = df["with_central"], df["prevented_central"]
    return df


def _sim_dict(a: int, b: int, g: pd.DataFrame, recent: float, delayed: float | None) -> dict:
    crews = a + b
    p, lo, hi = (float(g[f"prevented_{lv}"].sum()) for lv in LEVELS)
    per = lambda x: round(x / crews, 2) if crews > 0 else 0.0  # noqa: E731
    d = {
        # contract fields
        "crews_deployed": int(crews),
        "cases_prevented": round(p, 2),
        "beds_saved": round(p * BEDS_PER_CASE, 2),
        "saline_saved": round(p * SALINE_PER_CASE, 2),
        "roi_cases_per_crew": per(p),
        # extras (the contract allows additions)
        "crew_mix": {"larvicide": int(a), "indoor": int(b)},
        "kits_saved": round(p * KITS_PER_CASE, 2),
        "cases_prevented_low": round(lo, 2), "cases_prevented_high": round(hi, 2),
        "roi_low": per(lo), "roi_high": per(hi),
        "cases_without": round(float(g["without"].sum()), 2),
        "cases_with": round(float(g["with"].sum()), 2),
        "recent_cases": round(recent, 1),
        "by_week": [{"h": int(r.horizon), "without": round(float(r.without), 2),
                     "with": round(float(r.with_central), 2), "prevented": round(float(r.prevented), 2)}
                    for r in g.sort_values("horizon").itertuples()],
    }
    if delayed is not None:
        d["cases_prevented_if_delayed_1w"] = round(delayed, 2)
        d["cost_of_waiting_1w"] = round(max(p - delayed, 0.0), 2)
    return d


def economics(sim: dict, costs: dict | None = None) -> dict:
    """Money view of one ward's simulation: treatment cost avoided vs what the crews cost (BDT)."""
    c = {**COSTS, **(costs or {})}
    mix = sim.get("crew_mix", {"larvicide": sim.get("crews_deployed", 0), "indoor": 0})
    crew_cost = mix["larvicide"] * c["larvicide_crew_bdt"] + mix["indoor"] * c["indoor_crew_bdt"]
    saved = sim["cases_prevented"] * BEDS_PER_CASE * c["hospital_case_bdt"]
    return {"money_saved_bdt": round(saved), "crew_cost_bdt": round(crew_cost),
            "net_benefit_bdt": round(saved - crew_cost),
            "benefit_cost_ratio": round(saved / crew_cost, 2) if crew_cost else 0.0,
            "cost_per_case_prevented_bdt": round(crew_cost / sim["cases_prevented"]) if sim["cases_prevented"] > 0 else None}


def simulate(engine, date, deployments: dict | None, scenario=None, delay_weeks: int = 0,
             compare_delay: bool = True, costs: dict | None = None) -> dict:
    """Simulate a deployment; returns {ward_id: simulation} for ALL 60 wards.

    deployments: {ward_id: crews} (all larvicide) or {ward_id: {"larvicide": a, "indoor": b}}.
    `cases_prevented` is summed over the 2-, 3- and 4-week targets (the whole window).
    With compare_delay, each result also says how many cases the same crews would prevent
    if sent one week later."""
    dep = _norm(engine, deployments)
    df = _counterfactual(engine, date, dep, scenario, delay_weeks)
    late = None
    if compare_delay and dep:
        late = _counterfactual(engine, date, dep, scenario, delay_weeks + 1).groupby("ward_id")["prevented"].sum()
    rc = recent_cases(engine, date)
    out = {}
    for w, g in df.groupby("ward_id", sort=False):
        a, b = dep.get(w, (0, 0))
        out[w] = _sim_dict(a, b, g, rc.get(w, 0.0), float(late[w]) if late is not None and w in dep else None)
        out[w].update(economics(out[w], costs))
    return out


# ---------------------------------------------------------------- ranking / optimiser
_ENGINES: dict = {}
_SCENARIOS: dict = {}


@lru_cache(maxsize=128)
def _gain_table(engine_id: int, date: str, scenario_key, max_crews: int) -> dict:
    """{(type, level, k): {ward: cases prevented with k crews of that type}} for k = 1..max_crews."""
    engine, scenario = _ENGINES[engine_id], _SCENARIOS.get(scenario_key)
    wards = list(engine.wards.ward_id)
    table = {}
    for t, mk in (("larvicide", lambda k: (k, 0)), ("indoor", lambda k: (0, k))):
        for k in range(1, max_crews + 1):
            df = _counterfactual(engine, date, {w: mk(k) for w in wards}, scenario)
            sums = df.groupby("ward_id")[[f"prevented_{lv}" for lv in LEVELS]].sum()
            for lv in LEVELS:
                table[(t, lv, k)] = sums[f"prevented_{lv}"].to_dict()
    return table


def _table(engine, date, scenario, max_crews):
    _ENGINES[id(engine)] = engine
    key = None
    if scenario is not None and getattr(scenario, "active", False):
        key = (scenario.rain_multiplier, scenario.temp_delta_c, scenario.humidity_delta_pct, scenario.weeks)
        _SCENARIOS[key] = scenario
    return _gain_table(id(engine), pd.Timestamp(date).strftime("%Y-%m-%d"), key, int(max_crews))


def roi_ranking(engine, date, crews: int = 1, scenario=None) -> list[dict]:
    """All wards ranked by intervention ROI (cases prevented per crew) if `crews` crews went there,
    using whichever crew type works better in that ward."""
    crews = max(1, min(int(crews), MAX_CREWS_PER_WARD))
    t = _table(engine, date, scenario, crews)
    info = engine.wards.set_index("ward_id")
    rc = recent_cases(engine, date)
    rows = []
    for w in t[("larvicide", "central", crews)]:
        by = {tp: t[(tp, "central", crews)][w] for tp in TYPES}
        best = max(TYPES, key=lambda tp: (by[tp], tp == "larvicide"))
        p, lo, hi = (t[(best, lv, crews)][w] for lv in LEVELS)
        rows.append({"ward_id": w, "city": info.at[w, "city"], "crews": crews, "best_type": best,
                     "cases_prevented": round(p, 2), "roi_cases_per_crew": round(p / crews, 2),
                     "roi_low": round(lo / crews, 2), "roi_high": round(hi / crews, 2),
                     "roi_larvicide": round(by["larvicide"] / crews, 2), "roi_indoor": round(by["indoor"] / crews, 2),
                     "beds_saved": round(p * BEDS_PER_CASE, 2), "saline_saved": round(p * SALINE_PER_CASE, 2),
                     "recent_cases": round(rc.get(w, 0.0), 1)})
    rows.sort(key=lambda r: (-r["roi_cases_per_crew"], r["ward_id"]))
    for i, r in enumerate(rows, 1):
        r["roi_rank"] = i
    return rows


def best_deployment(engine, date, total_crews: int, scenario=None, max_per_ward: int = MAX_CREWS_PER_WARD,
                    types=TYPES) -> dict:
    """Greedy: give each crew to the (ward, crew type) where it prevents the most *extra* cases.

    A ward keeps the type its first crew got (one team type per ward keeps operations simple).
    Per-ward gains have diminishing returns, so greedy is near-optimal for the modelled gains."""
    total_crews = max(int(total_crews), 0)
    if total_crews == 0:
        return {}
    max_per_ward = max(1, min(int(max_per_ward), MAX_CREWS_PER_WARD))
    t = _table(engine, date, scenario, max_per_ward)
    wards = sorted(t[("larvicide", "central", 1)])
    got: dict[str, tuple[str, int]] = {}
    for _ in range(total_crews):
        best, best_gain = None, 1e-9
        for w in wards:
            cur_t, cur_k = got.get(w, (None, 0))
            if cur_k >= max_per_ward:
                continue
            for tp in ([cur_t] if cur_t else types):
                k = cur_k + 1
                gain = t[(tp, "central", k)][w] - (t[(tp, "central", k - 1)][w] if k > 1 else 0.0)
                if gain > best_gain:
                    best, best_gain = (w, tp), gain
        if best is None:   # no crew helps anywhere any more
            break
        w, tp = best
        got[w] = (tp, got.get(w, (tp, 0))[1] + 1)
    return {w: {"larvicide": k if tp == "larvicide" else 0, "indoor": k if tp == "indoor" else 0}
            for w, (tp, k) in got.items()}


# ---------------------------------------------------------------- plan seam
def attach_simulation(plans: list[dict], engine, date, deployments: dict | None, total_crews: int,
                      scenario=None) -> list[dict]:
    """Fill `spray_teams` and `simulation` on ward plans (contract sections 3 and 4), in place.

    deployments=None -> keep each plan's own `spray_teams` (Req 1's assignment) and simulate that."""
    if deployments is None:
        deployments = {p["ward_id"]: int(p.get("spray_teams") or 0) for p in plans}
    dep = _norm(engine, deployments)
    used = sum(a + b for a, b in dep.values())
    if used > int(total_crews):
        raise ValueError(f"deployments use {used} crews but only {total_crews} are available")
    sims = simulate(engine, date, deployments, scenario)
    for p in plans:
        a, b = dep.get(p["ward_id"], (0, 0))
        p["spray_teams"] = a + b
        p["simulation"] = sims[p["ward_id"]]
    return plans


def summary(sims: dict) -> dict:
    """City-wide totals for a simulate() result."""
    tot = lambda k: round(sum(s.get(k, 0.0) for s in sims.values()), 2)  # noqa: E731
    crews = sum(s["crews_deployed"] for s in sims.values())
    p, lo, hi = tot("cases_prevented"), tot("cases_prevented_low"), tot("cases_prevented_high")
    per = lambda x: round(x / crews, 2) if crews else 0.0  # noqa: E731
    late = tot("cases_prevented_if_delayed_1w")
    return {"crews_deployed": crews,
            "larvicide_crews": sum(s.get("crew_mix", {}).get("larvicide", 0) for s in sims.values()),
            "indoor_crews": sum(s.get("crew_mix", {}).get("indoor", 0) for s in sims.values()),
            "cases_prevented": p, "cases_prevented_low": lo, "cases_prevented_high": hi,
            "beds_saved": tot("beds_saved"), "saline_saved": tot("saline_saved"), "kits_saved": tot("kits_saved"),
            "cases_without": tot("cases_without"), "cases_with": tot("cases_with"),
            "roi_cases_per_crew": per(p), "roi_low": per(lo), "roi_high": per(hi),
            "cases_prevented_if_delayed_1w": late, "cost_of_waiting_1w": round(max(p - late, 0.0), 2) if crews else 0.0,
            "money_saved_bdt": round(tot("money_saved_bdt")), "crew_cost_bdt": round(tot("crew_cost_bdt")),
            "net_benefit_bdt": round(tot("money_saved_bdt") - tot("crew_cost_bdt")),
            "benefit_cost_ratio": round(tot("money_saved_bdt") / tot("crew_cost_bdt"), 2) if tot("crew_cost_bdt") else 0.0,
            "cost_per_case_prevented_bdt": round(tot("crew_cost_bdt") / p) if p > 0 else None}


# ---------------------------------------------------------------- model check (Q&A defence)
CHECK_KEEPS = (0.9, 0.75, 0.5)


@lru_cache(maxsize=4)
def _model_check(engine_id: int, start: str, step: int) -> dict:
    engine = _ENGINES[engine_id]
    dates = [d for d in pd.to_datetime(engine.dates) if d >= pd.Timestamp(start)][::step]
    base_tot, tot = 0.0, {k: 0.0 for k in CHECK_KEEPS}
    case_tot, n, not_up = 0.0, 0, {k: 0 for k in CHECK_KEEPS}
    for h in C.HORIZONS:
        fr = engine.frames[h]
        rows = fr[fr["date"].isin(dates)]
        X = rows[engine.bundle["feature_cols"][h]]
        model = engine.bundle["models"][h]["point"]
        base = model.predict(X).clip(0)
        base_tot += base.sum()
        n += len(base)
        ones = np.ones(len(X))
        for k in CHECK_KEEPS:
            pred = model.predict(_treat(X, ones * k, ones)).clip(0)
            tot[k] += pred.sum()
            not_up[k] += int((pred <= base + 1e-9).sum())
        case_tot += model.predict(_treat(X, ones, ones * 0.5)).clip(0).sum()
    # does the data itself agree? (observed, not the model)
    raw = engine.raw.sort_values(["ward_id", "date"])
    g = raw.groupby("ward_id")
    rain4 = g["rainfall_mm"].transform(lambda s: s.rolling(4).sum())
    cases_3w = g["dengue_cases"].shift(-3)
    ok = rain4.notna() & cases_3w.notna()
    ward = raw.groupby("ward_id").agg(cases=("dengue_cases", "mean"), wl=("water_logging_risk_index", "first"),
                                      san=("sanitation_score", "first"))
    return {
        "test_weeks_checked": len(dates), "ward_weeks": int(n / len(C.HORIZONS)), "from": start,
        "breeding_water_dose_response": [
            {"breeding_water_kept_pct": int(k * 100), "forecast_change_pct": round(100 * (tot[k] / base_tot - 1), 1),
             "rows_not_increased_pct": round(100 * not_up[k] / n, 1)} for k in CHECK_KEEPS],
        "recent_cases_halved_change_pct": round(100 * (case_tot / base_tot - 1), 1),
        "data_spearman": {
            "rain_4w_vs_cases_3w_later": round(float(rain4[ok].rank().corr(cases_3w[ok].rank())), 3),
            "ward_waterlogging_vs_mean_cases": round(float(ward.wl.rank().corr(ward.cases.rank())), 3),
            "ward_sanitation_vs_mean_cases": round(float(ward.san.rank().corr(ward.cases.rank())), 3),
        },
    }


def model_check(engine, start: str = "2024-01-01", step: int = 2) -> dict:
    """Does the model react to crew-changeable features the way the evidence and the data say?

    Over the out-of-sample test weeks: less breeding water must mean fewer forecast cases
    (dose-response), almost never more; and the observed data must show the same direction."""
    _ENGINES[id(engine)] = engine
    return _model_check(id(engine), start, int(step))


ASSUMPTIONS = {
    "method": "LightGBM forecast re-run (horizons 2-4) with the features each crew type changes; "
              "difference = cases prevented",
    "crew_types": {
        "larvicide": {"changes": "breeding-water features", "coverage_per_crew": LARV_COVERAGE,
                      "max_reduction": LARV_MAX, "onset_by_week": LARV_ONSET},
        "indoor": {"changes": "recent-case + vector features, around recent cases",
                   "cases_per_crew_per_week": CASES_PER_CREW, "max_reduction": INDOOR_MAX,
                   "vector_share": INDOOR_VECTOR_SHARE,
                   "onset_by_week": INDOOR_ONSET, "recent_weeks": RECENT_WEEKS},
    },
    "window_weeks": list(C.HORIZONS), "max_crews_per_ward": MAX_CREWS_PER_WARD,
    "saline_per_case": SALINE_PER_CASE, "kits_per_case": KITS_PER_CASE, "beds_per_case": BEDS_PER_CASE,
    "guesses": ["larvicide coverage per crew", "indoor cases per crew", "onset/decay shapes"],
    "evidence": [
        "Camino Verde RCT: community larval source reduction ~29.5% lower dengue risk (PMC4495677)",
        "Cairns: targeted indoor spraying cut transmission 86-96% at treated premises (PMC5315446)",
        "Yucatan model: proactive spraying beats reactive (PMC6042783)",
        "Fogging: no proven case reduction (PMC4795802); Dhaka Aedes pyrethroid resistant (PubMed 36942761)",
    ],
}
