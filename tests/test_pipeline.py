"""Verification of the multi-week, ward-level lag pipeline and the model bundle.

Run:  python -m pytest -q
"""
import numpy as np
import pandas as pd
import pytest

from src import config as C
from src.features import add_horizon_features, build_base_features, load_raw


@pytest.fixture(scope="module")
def raw():
    return load_raw()


@pytest.fixture(scope="module")
def base(raw):
    return build_base_features(raw)


def test_rain_lag_matches_same_ward_k_weeks_back(raw, base):
    ward = "DNCC_Ward_07"
    r = raw[raw.ward_id == ward].set_index("date")["rainfall_mm"]
    b = base[base.ward_id == ward].set_index("date")
    t = pd.Timestamp("2023-08-14")
    for k in (1, 2, 3, 4, 8, 12):
        assert b.loc[t, f"rain_lag{k}"] == pytest.approx(r.loc[t - pd.Timedelta(weeks=k)])
    assert b.loc[t, "rain_sum_4w"] == pytest.approx(r.loc[t - pd.Timedelta(weeks=3): t].sum())


def test_lags_do_not_cross_ward_boundaries(base):
    first_rows = base.sort_values("date").groupby("ward_id").head(1)
    assert first_rows["rain_lag1"].isna().all()
    assert first_rows["cases_lag1"].isna().all()


def test_no_future_leakage(raw):
    """Changing data after week t must not change any feature at week t."""
    t = pd.Timestamp("2022-07-04")
    tampered = raw.copy()
    future = tampered.date > t
    tampered.loc[future, ["rainfall_mm", "avg_temp_c", "dengue_cases"]] *= 7
    a = build_base_features(raw)
    b = build_base_features(tampered)
    cols = [c for c in a.columns if c not in ("date", "city", "ward_id", "dengue_cases")]
    pd.testing.assert_frame_equal(a.loc[a.date == t, cols], b.loc[b.date == t, cols])


def test_target_is_h_weeks_ahead(raw, base):
    for h in C.HORIZONS:
        f = add_horizon_features(base, h)
        row = f[(f.ward_id == "CCC_Ward_03") & (f.date == "2021-06-07")].iloc[0]
        truth = raw[(raw.ward_id == "CCC_Ward_03") & (raw.date == pd.Timestamp("2021-06-07") + pd.Timedelta(weeks=h))]
        assert row.target == truth.dengue_cases.iloc[0]


def test_bundle_predicts_dynamically():
    from src.predict import Engine, Scenario
    e = Engine()
    f = e.forecast("2025-07-07")
    assert set(f.horizon) == set(C.HORIZONS) and f.ward_id.nunique() == 60
    assert set(f.tier) <= set(C.TIERS)
    wet = e.forecast("2025-07-07", ["DNCC_Ward_01"], Scenario(rain_multiplier=2.0))
    dry = e.forecast("2025-07-07", ["DNCC_Ward_01"], Scenario(rain_multiplier=0.2))
    assert wet.pred_cases.sum() > dry.pred_cases.sum()
    assert np.all(f.lo80 <= f.pred_cases) and np.all(f.pred_cases <= f.hi80)


def test_api_serves_dynamic_forecasts_and_app():
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    assert c.get("/").status_code == 200
    meta = c.get("/api/meta").json()
    assert len(meta["wards"]) == 60 and "2025-07-07" in meta["dates"]
    base = c.get("/api/forecast", params={"date": "2025-07-07"}).json()
    wet = c.get("/api/forecast", params={"date": "2025-07-07", "rain": 2}).json()
    tot = lambda f: sum(w["h"][1]["pred"] for w in f["wards"])
    assert tot(wet) > tot(base)
    assert {w["h"][1]["tier"] for w in base["wards"]} <= {"Low", "Medium", "High", "Critical"}
    ward = c.get("/api/ward/DNCC_Ward_01", params={"date": "2025-07-07", "h": 3}).json()
    assert ward["drivers"] and len(ward["history"]) >= 12
    assert c.get("/api/forecast", params={"date": "1999-01-01"}).status_code == 400


def test_incomplete_upload_uses_robust_model():
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    sample = c.get("/api/sample.csv").content
    r = c.post("/api/upload", content=sample)
    assert r.status_code == 200
    out = r.json()["forecasts"]
    assert {f["ward"] for f in out} == {"DNCC_Ward_01", "CCC_Ward_03", "New_Area_A"}
    assert all(f["model"] == "robust" and f["lo"] <= f["pred"] <= f["hi"] for f in out)
    assert c.post("/api/upload", content=b"foo,bar\n1,2").status_code == 400


def test_robust_model_degrades_gracefully():
    import json
    m = json.loads(C.METRICS_PATH.read_text())["horizons"]["3"]["completeness"]
    sc, clim = m["scenarios"], m["climatology_baseline"]["MAE"]
    # with only rain + temperature for 8 weeks the robust model must still beat the no-data climatology baseline
    assert sc["new_area"]["robust"]["MAE"] < clim
    assert sc["new_area"]["robust"]["MAE"] < sc["new_area"]["deployed"]["MAE"]
