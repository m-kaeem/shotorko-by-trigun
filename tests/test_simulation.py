"""Requirement 3: vector-control simulator.  Run: python -m pytest -q"""
import time

import pytest
from fastapi.testclient import TestClient

from src import simulation as S
from src.api import ENGINE, app

DATE = "2025-07-07"   # peak-season week
DRY = "2025-02-03"    # dry-season week


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_all_wards_and_contract_fields():
    sims = S.simulate(ENGINE, DATE, {"DNCC_Ward_01": 2})
    assert len(sims) == 60
    s = sims["DNCC_Ward_01"]
    for k, t in [("crews_deployed", int), ("cases_prevented", float), ("beds_saved", float),
                 ("saline_saved", float), ("roi_cases_per_crew", float)]:
        assert isinstance(s[k], t), k
    assert s["crews_deployed"] == 2
    assert s["roi_cases_per_crew"] == pytest.approx(s["cases_prevented"] / 2, abs=0.01)
    assert s["cases_prevented_low"] <= s["cases_prevented"] <= s["cases_prevented_high"]
    untouched = sims["DNCC_Ward_02"]
    assert untouched["cases_prevented"] == 0 and untouched["roi_cases_per_crew"] == 0.0


def test_deterministic_and_fast():
    t0 = time.perf_counter()
    a = S.simulate(ENGINE, DATE, {"DSCC_Ward_03": 3})
    assert time.perf_counter() - t0 < 1.0
    assert a == S.simulate(ENGINE, DATE, {"DSCC_Ward_03": 3})


def test_model_driven_not_fixed_percent():
    """Same crews give different % reductions in different wards and seasons."""
    wet = S.simulate(ENGINE, DATE, {w: 2 for w in ENGINE.wards.ward_id})
    pct = {w: s["cases_prevented"] / s["cases_without"] for w, s in wet.items() if s["cases_without"] > 1}
    assert max(pct.values()) - min(pct.values()) > 0.05
    dry = S.summary(S.simulate(ENGINE, DRY, {w: 2 for w in ENGINE.wards.ward_id}))
    assert dry["cases_prevented"] < S.summary(wet)["cases_prevented"]


def test_more_crews_help_with_diminishing_returns():
    w = "DNCC_Ward_05"
    p = [S.simulate(ENGINE, DATE, {w: k})[w]["cases_prevented"] for k in (1, 2, 4)]
    assert p[0] <= p[1] <= p[2]
    assert S.habitat_reduction(2) - S.habitat_reduction(1) < S.habitat_reduction(1)


def test_central_effect_close_to_rct():
    """Saturated coverage in peak season ~ the 29.5% Camino Verde effect, not more than ~40%."""
    s = S.summary(S.simulate(ENGINE, DATE, {w: 6 for w in ENGINE.wards.ward_id}))
    assert 0.15 < s["cases_prevented"] / s["cases_without"] < 0.40


def test_ranking_and_best_deployment():
    r = S.roi_ranking(ENGINE, DATE)
    assert len(r) == 60 and [x["roi_rank"] for x in r] == list(range(1, 61))
    assert r[0]["roi_cases_per_crew"] >= r[-1]["roi_cases_per_crew"]
    dep = S.best_deployment(ENGINE, DATE, 20)
    assert S.crews_used(dep) <= 20 and max(v["larvicide"] + v["indoor"] for v in dep.values()) <= S.MAX_CREWS_PER_WARD
    greedy = S.summary(S.simulate(ENGINE, DATE, dep))["cases_prevented"]
    spread = S.summary(S.simulate(ENGINE, DATE, {x["ward_id"]: 1 for x in r[-20:]}))["cases_prevented"]
    assert greedy > spread


def test_attach_simulation_rejects_too_many_crews():
    plans = [{"ward_id": w, "spray_teams": 0} for w in ENGINE.wards.ward_id]
    S.attach_simulation(plans, ENGINE, DATE, {"CCC_Ward_01": 3}, total_crews=5)
    assert sum(p["spray_teams"] for p in plans) == 3
    assert all(p["simulation"] is not None for p in plans)
    with pytest.raises(ValueError):
        S.attach_simulation(plans, ENGINE, DATE, {"CCC_Ward_01": 6}, total_crews=5)
    with pytest.raises(ValueError):
        S.simulate(ENGINE, DATE, {"Nowhere_Ward": 1})


def test_api_simulate(client):
    r = client.post("/api/simulate", json={"date": DATE, "total_crews": 10, "deployments": {"DNCC_Ward_01": 2}})
    assert r.status_code == 200
    j = r.json()
    assert len(j["wards"]) == 60 and j["summary"]["crews_deployed"] == 2 and len(j["roi_ranking"]) == 60
    auto = client.post("/api/simulate", json={"date": DATE, "total_crews": 10}).json()
    assert auto["auto"] and S.crews_used(auto["deployments"]) <= 10
    assert client.post("/api/simulate", json={"date": DATE, "total_crews": 1,
                                              "deployments": {"DNCC_Ward_01": 2}}).status_code == 400
    assert client.get("/api/simulate/roi", params={"date": DATE}).status_code == 200


def test_vector_sim_hook_matches_plan_py_signature():
    from src import vector_sim
    out = vector_sim.simulate(ENGINE, DATE, 3, {"DNCC_Ward_01": 2}, None)
    s = out["DNCC_Ward_01"]
    assert s["crews_deployed"] == 2 and s["cases_prevented"] > 0
    assert s["beds_saved"] == pytest.approx(s["cases_prevented"] * S.BEDS_PER_CASE, abs=0.02)


def test_two_crew_types_and_mixed_deployment():
    w = "DSCC_Ward_06"
    lv = S.simulate(ENGINE, DATE, {w: {"larvicide": 2}})[w]
    ind = S.simulate(ENGINE, DATE, {w: {"indoor": 2}})[w]
    mix = S.simulate(ENGINE, DATE, {w: {"larvicide": 1, "indoor": 1}})[w]
    assert lv["crew_mix"] == {"larvicide": 2, "indoor": 0} and mix["crews_deployed"] == 2
    assert lv["cases_prevented"] != ind["cases_prevented"]   # different features, different gains
    with pytest.raises(ValueError):
        S.simulate(ENGINE, DATE, {w: {"fogging": 1}})


def test_indoor_needs_recent_cases_to_target():
    assert S.indoor_coverage(3, 0) == 0.0
    assert S.indoor_coverage(1, 50) < S.indoor_coverage(1, 5)


def test_waiting_a_week_costs_cases_for_larvicide():
    s = S.summary(S.simulate(ENGINE, DATE, {w: {"larvicide": 2} for w in ENGINE.wards.ward_id}))
    assert s["cost_of_waiting_1w"] > 0
    assert s["cases_prevented_if_delayed_1w"] < s["cases_prevented"]


def test_optimiser_mixes_types_and_beats_naive():
    dep = S.best_deployment(ENGINE, DATE, 20)
    assert S.crews_used(dep) <= 20
    used = {t for v in dep.values() for t in S.TYPES if v[t]}
    assert used   # at least one type picked; in peak season both usually appear
    best = S.summary(S.simulate(ENGINE, DATE, dep))["cases_prevented"]
    larv_top = {r["ward_id"]: {"larvicide": 1} for r in S.roi_ranking(ENGINE, DATE)[:20]}
    assert best >= S.summary(S.simulate(ENGINE, DATE, larv_top))["cases_prevented"] - 1e-6


def test_money_view_follows_costs():
    w = "DNCC_Ward_09"
    s = S.simulate(ENGINE, DATE, {w: {"larvicide": 2}})[w]
    assert s["crew_cost_bdt"] == 2 * S.COSTS["larvicide_crew_bdt"]
    assert s["money_saved_bdt"] == pytest.approx(s["cases_prevented"] * S.BEDS_PER_CASE * S.COSTS["hospital_case_bdt"], abs=1)
    cheap = S.simulate(ENGINE, DATE, {w: {"larvicide": 2}}, costs={"larvicide_crew_bdt": 10000})[w]
    assert cheap["benefit_cost_ratio"] > s["benefit_cost_ratio"]
    assert S.simulate(ENGINE, DATE, {})[w]["benefit_cost_ratio"] == 0.0


def test_model_check_direction_matches_evidence():
    c = S.model_check(ENGINE)
    changes = [d["forecast_change_pct"] for d in c["breeding_water_dose_response"]]
    assert all(x < 0 for x in changes) and changes == sorted(changes, reverse=True)   # less water, fewer cases
    assert all(d["rows_not_increased_pct"] > 80 for d in c["breeding_water_dose_response"])
    assert c["data_spearman"]["rain_4w_vs_cases_3w_later"] > 0.3


def test_api_costs_and_check(client):
    j = client.post("/api/simulate", json={"date": DATE, "total_crews": 5, "deployments": {"DNCC_Ward_09": 2},
                                           "costs": {"larvicide_crew_bdt": 50000}}).json()
    assert j["costs"]["larvicide_crew_bdt"] == 50000 and j["summary"]["crew_cost_bdt"] > 0
    assert client.get("/api/simulate/check").status_code == 200


def test_season_backtest_artifact(client):
    r = client.get("/api/simulate/season")
    assert r.status_code == 200
    j = r.json()
    assert j["n_weeks"] >= 50 and set(j["all"]) == {"shotorko", "shotorko_larvicide", "tier_rule", "top_cases"}
    assert j["all"]["shotorko"] >= j["all"]["shotorko_larvicide"] >= j["all"]["tier_rule"]
