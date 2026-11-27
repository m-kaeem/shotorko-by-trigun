"""Checks src/plan.py against every rule in docs/PHASE2_PLAN_CONTRACT.md (the team's ward-plan format)."""
import math
import socket

import pytest

ITEMS = ("saline_bags", "diagnostic_kits", "hospital_beds")
TOP = {"ward_id": str, "city": str, "date": str, "h": int, "target_week": str, "predicted_cases": float,
       "cases_low": float, "cases_high": float, "tier": str, "risk_score": int, "population_density_sqkm": int,
       "required": dict, "allocated": dict, "deficit": dict, "coverage_pct": dict, "deficit_level": str,
       "priority_rank": int, "spray_teams": int, "stock_pct": (int, float), "total_crews": int, "source": str}
CITIES = {"Dhaka North", "Dhaka South", "Chattogram", "Gazipur", "Narayanganj"}


@pytest.fixture(scope="module")
def engine():
    from src.predict import Engine
    return Engine()


def check(plans, date, h, stock_pct, total_crews, deployments=None):
    from src.resources import SUPPLY_RATIOS
    assert len(plans) == 60 and len({p["ward_id"] for p in plans}) == 60
    ranks = sorted(p["priority_rank"] for p in plans)
    assert ranks == list(range(1, 61))
    for p in plans:
        for k, t in TOP.items():
            assert k in p and isinstance(p[k], t), k
        assert "simulation" in p and (p["simulation"] is None or isinstance(p["simulation"], dict))
        assert p["city"] in CITIES and p["tier"] in ("Low", "Medium", "High", "Critical")
        assert p["date"] == date and p["h"] == h and p["source"] == "engine"
        assert p["stock_pct"] == stock_pct and p["total_crews"] == total_crews
        assert 0 <= p["risk_score"] <= 100 and p["cases_low"] <= p["predicted_cases"] <= p["cases_high"] + 1e-9
        for b in ("required", "allocated", "deficit", "coverage_pct"):
            assert set(p[b]) == set(ITEMS), b
        for k, col in zip(ITEMS, ("saline_bags_needed", "diagnostic_kits_needed", "hospital_beds_required")):
            req, got = p["required"][k], p["allocated"][k]
            assert isinstance(req, int) and isinstance(got, int)
            assert req == math.ceil(round(p["predicted_cases"] * SUPPLY_RATIOS[col], 6))   # ceil rule
            assert 0 <= got <= req and p["deficit"][k] == req - got
            cov = p["coverage_pct"][k]
            assert isinstance(cov, float) and cov == (round(100 * got / req, 1) if req else 100.0)
        covs = p["coverage_pct"].values()
        want = ("none" if all(v == 0 for v in p["deficit"].values())
                else "critical" if any(c < 50 for c in covs) else "partial")
        assert p["deficit_level"] == want
        assert p["spray_teams"] >= 0
        o = p["overflow"]
        assert isinstance(o["beds_short"], int) and o["beds_short"] >= 0
        assert sum(x["beds"] for x in o["send_to"]) <= o["beds_short"]
    for k in ITEMS:
        tot_req = sum(p["required"][k] for p in plans)
        assert sum(p["allocated"][k] for p in plans) <= stock_pct / 100 * tot_req + 1e-9
    assert sum(p["spray_teams"] for p in plans) <= total_crews
    if deployments:
        for w, n in deployments.items():
            assert next(p for p in plans if p["ward_id"] == w)["spray_teams"] == n
    # higher risk and denser wards are served first
    by = {p["ward_id"]: p for p in plans}
    first = min(plans, key=lambda p: p["priority_rank"])
    assert first["tier"] == max((p["tier"] for p in plans), key=["Low", "Medium", "High", "Critical"].index)
    city_of = {p["ward_id"]: p["city"] for p in plans}
    for p in plans:
        for x in p["overflow"]["send_to"]:
            assert city_of[x["ward_id"]] == p["city"]       # overflow stays inside the city
            assert by[x["ward_id"]]["overflow"]["beds_short"] == 0


@pytest.mark.parametrize("stock,crews", [(100.0, 20), (70, 20), (30, 5), (55.5, 0)])
def test_plan_contract(engine, stock, crews):
    from src.plan import build_plans
    plans = build_plans(engine, "2025-07-07", 3, stock_pct=stock, total_crews=crews)
    check(plans, "2025-07-07", 3, stock, crews)


def test_full_stock_means_no_deficit(engine):
    from src.plan import build_plans
    plans = build_plans(engine, "2024-08-05", 2, stock_pct=100, total_crews=20)
    assert all(p["deficit_level"] == "none" for p in plans)


def test_deterministic_and_deployments(engine):
    from src.plan import build_plans
    a = build_plans(engine, "2025-07-07", 4, stock_pct=60, total_crews=20)
    b = build_plans(engine, "2025-07-07", 4, stock_pct=60, total_crews=20)
    assert a == b
    dep = {"DNCC_Ward_01": 2, "CCC_Ward_03": 1}
    c = build_plans(engine, "2025-07-07", 4, stock_pct=60, total_crews=20, deployments=dep)
    check(c, "2025-07-07", 4, 60, 20, dep)
    assert sum(p["spray_teams"] for p in c) == 3
    with pytest.raises(ValueError):
        build_plans(engine, "2025-07-07", 4, total_crews=2, deployments={"DNCC_Ward_01": 3})


def test_plan_api_offline(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import app

    def no_net(*a, **k):
        raise OSError("network disabled for test")
    monkeypatch.setattr(socket, "create_connection", no_net)
    c = TestClient(app)
    r = c.get("/api/plan", params={"date": "2025-07-07", "h": 3, "stock_pct": 70, "total_crews": 20})
    assert r.status_code == 200
    j = r.json()
    check(j["plans"], "2025-07-07", 3, 70, 20)
    assert set(j["totals"]) >= {"required", "allocated", "deficit"}
    r = c.post("/api/plan/simulate", json={"date": "2025-07-07", "h": 3, "stock_pct": 70, "total_crews": 20,
                                           "deployments": {"DNCC_Ward_01": 2}})
    assert r.status_code == 200
    assert next(p for p in r.json()["plans"] if p["ward_id"] == "DNCC_Ward_01")["spray_teams"] == 2
    assert c.post("/api/plan/simulate", json={"total_crews": 1, "deployments": {"DNCC_Ward_01": 2}}).status_code == 400
    r = c.get("/api/plan", params={"date": "2025-07-07", "floating_beds": "Gazipur:3"})
    assert r.status_code == 200
    check(r.json()["plans"], "2025-07-07", 3, 100.0, 20)
    assert sum(p["overflow"]["floating_beds"] for p in r.json()["plans"] if p["city"] == "Gazipur") >= 1
