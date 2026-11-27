"""Tests for Phase 2 operations: constrained allocation (req 1) and overflow routing (req 2)."""
import socket

import numpy as np
import pandas as pd
import pytest

from src import config as C
from src.resources import (SUPPLY_COLS, SUPPLY_RATIOS, allocate, crews_for, fit_ratio, strict_fill,
                           supplies_for, water_fill)
from src.surge import baseline_capacity, route


@pytest.fixture(scope="module")
def onsite():
    head = pd.read_csv(C.DATA_PATH, nrows=0).columns
    if not set(SUPPLY_COLS) <= set(head):
        pytest.skip("dataset has no supply columns")
    return pd.read_csv(C.DATA_PATH, parse_dates=["date"])


def test_supply_rule_reproduces_every_row(onsite):
    got = supplies_for(onsite.dengue_cases, rounding="round")
    for k in SUPPLY_COLS:
        assert (got[k] == onsite[k].values).all(), k


def test_ratios_are_fitted_from_data(onsite):
    for k in SUPPLY_COLS:
        _, acc = fit_ratio(onsite.dengue_cases.values, onsite[k].values,
                           lo=SUPPLY_RATIOS[k] - 0.01, hi=SUPPLY_RATIOS[k] + 0.01)
        assert acc == 1.0


@pytest.mark.parametrize("seed", range(20))
def test_water_fill_invariants(seed):
    rng = np.random.default_rng(seed)
    d = rng.integers(0, 40, 60)
    w = rng.uniform(0.3, 6, 60)
    total = int(d.sum())
    for stock in (0, total // 3, total // 2, total - 1, total, total + 50):
        a = water_fill(d, w, stock)
        assert (a >= 0).all() and (a <= d).all()
        assert a.sum() == min(stock, total)
        share = np.where(d > 0, a / np.maximum(d, 1), np.nan)
        # higher priority never gets a smaller share, up to one unit of rounding on each ward
        for i in range(60):
            for j in range(60):
                if d[i] > 0 and d[j] > 0 and w[i] > w[j]:
                    assert share[i] >= share[j] - 1.0 / d[i] - 1.0 / d[j] - 1e-9


def test_strict_fill_serves_in_priority_order():
    a = strict_fill([10, 10, 10], [1.0, 3.0, 2.0], 15)
    assert a.tolist() == [0, 10, 5]


def test_crews_rule():
    assert crews_for(["Low", "High", "Medium", "Critical", "Low"], [70000, 30000, 65000, 61000, 90000]).tolist() == [0, 2, 2, 4, 0]


def _fc():
    return pd.DataFrame({
        "ward_id": ["A1", "A2", "A3", "B1", "B2"], "city": ["A", "A", "A", "B", "B"],
        "density": [70000, 30000, 30000, 50000, 50000], "tier": ["Critical", "Low", "Medium", "High", "Low"],
        "pred": [20.0, 2.0, 5.0, 12.0, 1.0], "hi": [26.0, 4.0, 8.0, 16.0, 2.0]})


def test_allocation_under_scarcity_prioritises_and_flags():
    out = allocate(_fc(), stock_pct=0.3, crews=3)
    for k in SUPPLY_COLS:
        assert out["items"][k]["allocated"] <= out["items"][k]["stock"]
        assert out["items"][k]["unmet"] > 0
    first = out["wards"][0]
    assert first["ward"] == "A1" and first["rank"] == 1          # critical + densest ward ranks first
    fills = {w["ward"]: w["saline_bags_needed"]["allocated"] / max(w["saline_bags_needed"]["required"], 1)
             for w in out["wards"]}
    assert fills["A1"] >= fills["B1"] >= fills["A2"]
    assert out["n_deficit"] > 0 and out["warnings"][0]["level"] == "critical"
    assert out["crews"]["assigned"] == 3


def test_full_stock_has_no_deficit():
    out = allocate(_fc(), stock_pct=1.0, crews=100)
    assert out["n_deficit"] == 0
    assert all(v["unmet"] == 0 for v in out["items"].values())


def test_routing_stays_within_city_and_respects_capacity():
    cap = pd.Series({"A1": 2, "A2": 5, "A3": 4, "B1": 2, "B2": 2})
    out = route(_fc(), cap)
    city = {w["ward"]: w["city"] for w in out["wards"]}
    for t in out["transfers"]:
        assert city[t["from"]] == city[t["to"]]
    for w in out["wards"]:
        assert w["occupied"] <= w["capacity"]
        assert w["required"] == w["local"] + w["sent"] + w["unresolved"]
    a1 = next(w for w in out["wards"] if w["ward"] == "A1")
    assert a1["sent"] > 0 and a1["net"] < 0                       # 6 beds needed, 2 local
    b1 = next(w for w in out["wards"] if w["ward"] == "B1")
    assert b1["unresolved"] + b1["sent"] == b1["required"] - b1["local"]


def test_capacity_uses_training_years_only(onsite):
    cap = baseline_capacity(onsite, C.TRAIN_END)
    assert len(cap) == 60 and (cap >= 2).all()
    later = onsite.copy()
    later.loc[later.date > C.TRAIN_END, "hospital_beds_required"] = 999
    assert baseline_capacity(later, C.TRAIN_END).equals(cap)


def test_operations_endpoints_work_offline(monkeypatch):
    """Kill-switch: block all outbound sockets; allocation and routing must still answer."""
    from fastapi.testclient import TestClient
    from src.api import app

    def no_net(*a, **k):
        raise OSError("network disabled for test")
    monkeypatch.setattr(socket, "create_connection", no_net)
    c = TestClient(app)
    r = c.get("/api/ops/allocate", params={"date": "2025-07-07", "stock": 0.4, "crews": 15})
    assert r.status_code == 200 and r.json()["items"]["saline_bags_needed"]["fill"] <= 0.41
    r = c.get("/api/ops/surge", params={"date": "2025-07-07", "cap_scale": 0.8})
    assert r.status_code == 200
    j = r.json()
    assert j["totals"]["required"] == sum(w["required"] for w in j["wards"])
    assert all(x["util_after"] <= 1.0 for x in j["cities"])


def test_floating_beds_used_after_transfers_and_only_in_their_city():
    cap = pd.Series({"A1": 2, "A2": 5, "A3": 4, "B1": 2, "B2": 2})
    base = route(_fc(), cap)
    b_unplaced = sum(w["unresolved"] for w in base["wards"] if w["city"] == "B")
    assert b_unplaced > 0                                         # city B is full without reserve beds
    out = route(_fc(), cap, floating={"B": b_unplaced + 1, "A": 0})
    for w in out["wards"]:
        assert w["required"] == w["local"] + w["sent"] + w["floating"] + w["unresolved"]
        assert w["occupied"] <= w["capacity"]
    assert sum(w["unresolved"] for w in out["wards"]) == sum(w["unresolved"] for w in base["wards"] if w["city"] == "A")
    city_b = next(c for c in out["cities"] if c["city"] == "B")
    assert city_b["floating_used"] == b_unplaced and city_b["floating_pool"] == b_unplaced + 1
    assert city_b["status"] != "escalate"
    # transfers are unchanged: reserve beds are only a last resort
    assert out["transfers"] == base["transfers"]
    # reserve beds never cross cities
    assert all(w["floating"] == 0 for w in out["wards"] if w["city"] == "A")


def test_floating_beds_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    base = c.get("/api/ops/surge", params={"date": "2025-07-07", "cap_scale": 0.85}).json()
    r = c.get("/api/ops/surge", params={"date": "2025-07-07", "cap_scale": 0.85, "floating": "Gazipur:50"})
    assert r.status_code == 200
    g = next(x for x in r.json()["cities"] if x["city"] == "Gazipur")
    g0 = next(x for x in base["cities"] if x["city"] == "Gazipur")
    assert g["unresolved"] == 0 and g["floating_used"] == g0["unresolved"]
    assert c.get("/api/ops/surge", params={"floating": "Atlantis:2"}).status_code == 400
    assert c.get("/api/ops/surge", params={"floating": "Gazipur:x"}).status_code == 400


@pytest.mark.parametrize("policy", ["weighted", "strict"])
def test_lifeline_removes_critical_deficits_when_stock_allows(policy):
    fc = pd.concat([_fc()] * 6, ignore_index=True)
    fc["ward_id"] = [f"W{i}" for i in range(len(fc))]
    out = allocate(fc, stock_pct=0.6, crews=10, policy=policy, min_share=0.5)
    assert out["n_critical"] == 0
    for w in out["wards"]:
        for k in SUPPLY_COLS:
            assert w[k]["allocated"] * 2 >= w[k]["required"]
    for k in SUPPLY_COLS:
        assert out["items"][k]["allocated"] <= out["items"][k]["stock"]
    without = allocate(fc, stock_pct=0.6, crews=10, policy=policy)
    assert without["n_critical"] > 0


def test_lifeline_that_does_not_fit_shares_equally():
    out = allocate(_fc(), stock_pct=0.3, crews=0, min_share=0.5)
    for k in SUPPLY_COLS:
        assert out["items"][k]["allocated"] == out["items"][k]["stock"]
    fills = [w["saline_bags_needed"]["allocated"] / w["saline_bags_needed"]["required"] for w in out["wards"]]
    assert max(fills) - min(fills) < 0.35          # equal shares, up to whole-bag rounding


@pytest.mark.parametrize("min_share", [0.0, 0.3])
def test_what_it_takes_is_exact(min_share):
    """The suggested stock clears every critical deficit, and one unit less does not."""
    fc = pd.concat([_fc()] * 6, ignore_index=True)
    fc["ward_id"] = [f"W{i}" for i in range(len(fc))]
    out = allocate(fc, stock_pct=0.4, crews=0, min_share=min_share)
    assert out["fix"]["needed"]
    units = {k: v["stock_to_clear_critical"] for k, v in out["items"].items()}
    assert allocate(fc, crews=0, min_share=min_share, stock_units=units)["n_critical"] == 0
    for k in SUPPLY_COLS:
        if units[k] > 0:
            less = dict(units, **{k: units[k] - 1})
            assert allocate(fc, crews=0, min_share=min_share, stock_units=less)["n_critical"] > 0


def test_real_counts_endpoint():
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    j = c.get("/api/ops/allocate", params={"date": "2025-07-07", "saline_stock": 900, "kits_stock": 500,
                                           "beds_stock": 120, "min_share": 0.2}).json()
    assert j["items"]["saline_bags_needed"]["stock"] == 900 and j["items"]["saline_bags_needed"]["allocated"] == 900
    assert j["items"]["hospital_beds_required"]["allocated"] == 120 and j["min_share"] == 0.2
    assert c.get("/api/ops/allocate", params={"saline_stock": -1}).status_code == 422
    assert c.get("/api/ops/allocate", params={"min_share": 0.9}).status_code == 422
    r = c.get("/api/plan", params={"date": "2025-07-07", "stock_pct": 60, "min_share_pct": 50})
    assert r.status_code == 200 and all(p["deficit_level"] != "critical" for p in r.json()["plans"])


def test_page_files_are_revalidated_so_updates_show():
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    for path in ("/", "/app.js", "/ops.js"):
        assert c.get(path).headers.get("cache-control") == "no-cache"
    assert "no-cache" not in (c.get("/api/health").headers.get("cache-control") or "")


def test_emergency_bedding_after_transfers_own_ward_only_and_before_reserve():
    cap = pd.Series({"A1": 2, "A2": 5, "A3": 4, "B1": 2, "B2": 2})
    base = route(_fc(), cap)
    out = route(_fc(), cap, bedding=0.5)
    assert out["transfers"] == base["transfers"]                  # real beds elsewhere are used first
    for w in out["wards"]:
        assert w["required"] == w["local"] + w["sent"] + w["emergency"] + w["floating"] + w["unresolved"]
        assert w["emergency"] <= w["emergency_cap"] == int(np.floor(w["capacity"] * 0.5 + 0.5))
        if w["sent"] == 0 and w["required"] <= w["capacity"]:
            assert w["emergency"] == 0                            # only wards with their own overflow use it
    assert sum(w["unresolved"] for w in out["wards"]) < sum(w["unresolved"] for w in base["wards"])
    both = route(_fc(), cap, bedding=0.5, floating={"A": 5, "B": 5})
    alone = route(_fc(), cap, floating={"A": 5, "B": 5})
    assert both["totals"]["floating_used"] <= alone["totals"]["floating_used"]   # floor beds come before reserve beds
    assert any(w["status"] == "crowded" for w in out["wards"])


def test_emergency_bedding_endpoint_and_plans():
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    j = c.get("/api/ops/surge", params={"date": "2025-07-07", "cap_scale": 0.85, "bedding": 0.25}).json()
    j0 = c.get("/api/ops/surge", params={"date": "2025-07-07", "cap_scale": 0.85}).json()
    assert j["totals"]["emergency_used"] > 0 and j["totals"]["unresolved"] < j0["totals"]["unresolved"]
    assert c.get("/api/ops/surge", params={"bedding": 0.9}).status_code == 422
    p = c.get("/api/plan", params={"date": "2025-07-07", "emergency_bedding_pct": 25}).json()["plans"]
    assert all("emergency_beds" in x["overflow"] for x in p)
