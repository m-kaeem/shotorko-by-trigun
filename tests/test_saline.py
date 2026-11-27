"""Saline outlook (surge alarm, order-by dates), ORS for mild cases, and the supply backtest."""
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from src import config as C
from src.resources import allocate
from src.saline import METHODS, assess, outlook, saline_need, supply_backtest, thresholds


def test_thresholds_come_from_fit_years_and_are_ordered():
    th = thresholds(C.VAL_END)
    assert 0 <= th["typical"] <= th["busy"] <= th["very_busy"] <= th["max"]
    assert th["years"] == "2016-2023"


def test_alarm_levels():
    th = thresholds(C.VAL_END)
    assert assess(th["very_busy"] + 1, C.VAL_END)["level"] == "surge"
    assert assess(th["busy"], C.VAL_END)["level"] == "high"
    assert assess(0, C.VAL_END)["level"] == "normal"


def test_order_by_is_target_minus_lead_and_ors_lowers_saline():
    rows = {3: ("2025-07-28", [10.0, 5.0, 0.0])}
    o = outlook(rows, "2025-07-07", C.VAL_END, lead_days=7, ors_share=0.3, packets_per_patient=4)
    w = o["weeks"][0]
    assert w["order_by"] == "2025-07-21" and w["days_to_order"] == 14
    assert w["saline"] < w["saline_without_ors"] == int(saline_need([10.0, 5.0, 0.0]).sum())
    assert w["ors_patients"] == 4.5 and w["ors_packets"] == 18


def test_ors_frees_saline_without_changing_the_stock():
    fc = pd.DataFrame({"ward_id": ["A", "B", "C"], "city": ["X"] * 3, "density": [60000, 40000, 20000],
                       "tier": ["Critical", "High", "Medium"], "pred": [20.0, 10.0, 4.0], "hi": [25.0, 13.0, 6.0]})
    base = allocate(fc, stock_pct=0.6, crews=0)
    ors = allocate(fc, stock_pct=0.6, crews=0, ors_share=0.3)
    k = "saline_bags_needed"
    assert ors["items"][k]["stock"] == base["items"][k]["stock"]          # same bags in the store
    assert ors["items"][k]["required"] < base["items"][k]["required"]
    assert ors["items"][k]["unmet"] < base["items"][k]["unmet"]
    assert ors["items"]["hospital_beds_required"]["required"] == base["items"]["hospital_beds_required"]["required"]


def test_supply_backtest_is_deterministic_and_consistent():
    a = supply_backtest(0.6, 3)
    assert a == supply_backtest(0.6, 3)
    assert {m["method"] for m in a["methods"]} == set(METHODS) and a["weeks"] > 50
    for m in a["methods"]:
        assert 0 <= m["reached_pct"] <= 100 and m["bags_short"] >= 0 and m["bags_misplaced"] >= 0
    shotorko_best = max(m["reached_pct"] for m in a["methods"] if m["method"] in ("weighted", "lifeline"))
    reactive = next(m for m in a["methods"] if m["method"] == "reactive")
    assert shotorko_best > reactive["reached_pct"]      # forecast-based sharing beats "last reported week"


def test_saline_endpoints():
    c = TestClient(__import__("src.api", fromlist=["app"]).app)
    j = c.get("/api/ops/saline", params={"date": "2025-07-07", "lead_days": 10, "ors": 0.2}).json()
    assert [w["h"] for w in j["weeks"]] == [2, 3, 4] and j["alarm"] in ("normal", "high", "surge")
    assert all(w["saline"] <= w["saline_without_ors"] for w in j["weeks"])
    assert c.get("/api/ops/saline", params={"ors": 0.9}).status_code == 422
    b = c.get("/api/ops/supply-backtest", params={"stock": 0.8, "h": 2}).json()
    assert b["h"] == 2 and len(b["methods"]) == len(METHODS)
    a = c.get("/api/ops/allocate", params={"date": "2025-07-07", "ors": 0.3}).json()
    assert a["ors_share"] == 0.3 and a["ors_patients"] > 0
