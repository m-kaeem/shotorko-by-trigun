"""Weather ingestion + multi-week lag feature pipeline.

Every lag / rolling feature is computed with ``groupby("ward_id")`` so that a
ward's history never leaks into another ward, and only uses information
available at the reference week *t*. The label for horizon *h* is the case
count at week *t + h*.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

REQUIRED_COLUMNS = [
    "date", "city", "ward_id", "population_density_sqkm", "water_logging_risk_index",
    "sanitation_score", "avg_temp_c", "max_temp_c", "min_temp_c", "rainfall_mm",
    "relative_humidity_pct", "dengue_cases",
]
STATIC_COLS = ["population_density_sqkm", "water_logging_risk_index", "sanitation_score"]
CITY_CODES = {c: i for i, c in enumerate(sorted(C.CITY_CENTROIDS))}

# Thresholds learned on the training period only (see fit_thresholds)
DEFAULT_HEAVY_RAIN_MM = 146.6  # ~P95 of weekly rainfall in the training years


def load_raw(path=C.DATA_PATH) -> pd.DataFrame:
    """Load and validate the organiser dataset (CSV exported from the Google Sheet)."""
    df = pd.read_csv(path, parse_dates=["date"])
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Dataset is missing columns: {sorted(missing)}")
    df = df[REQUIRED_COLUMNS].sort_values(["ward_id", "date"]).reset_index(drop=True)
    num = df.columns.difference(["date", "city", "ward_id"])
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    # Defensive cleaning for a production feed: interpolate small gaps per ward
    df[num] = df.groupby("ward_id")[list(num)].transform(lambda s: s.interpolate(limit=2).ffill().bfill())
    if df.duplicated(["ward_id", "date"]).any():
        raise ValueError("Duplicate (ward_id, date) rows found")
    return df


def fit_thresholds(raw: pd.DataFrame, train_end: str = C.TRAIN_END) -> dict:
    tr = raw[raw.date <= train_end]
    return {"heavy_rain_mm": float(tr.rainfall_mm.quantile(0.95))}


def build_base_features(raw: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Horizon-independent features available at reference week t."""
    th = thresholds or {"heavy_rain_mm": DEFAULT_HEAVY_RAIN_MM}
    df = raw.sort_values(["ward_id", "date"]).copy()
    g = df.groupby("ward_id", sort=False)
    out = {}

    rain = g["rainfall_mm"]
    for k in C.RAIN_LAGS:
        out[f"rain_lag{k}"] = rain.shift(k)
    for w in C.ROLL_WINDOWS:
        out[f"rain_sum_{w}w"] = rain.transform(lambda s, w=w: s.rolling(w, min_periods=w).sum())
    # Breeding-clock window: rain that fell 1-3 weeks before t (eggs -> adults)
    out["rain_sum_lag1_3"] = rain.transform(lambda s: s.shift(1).rolling(3, min_periods=3).sum())
    heavy = (df["rainfall_mm"] > th["heavy_rain_mm"]).astype(float).where(df["rainfall_mm"].notna())  # NaN when rain is missing
    out["heavy_rain_weeks_4w"] = heavy.groupby(df["ward_id"]).transform(lambda s: s.rolling(4, min_periods=1).sum())

    temp = g["avg_temp_c"]
    for k in C.TEMP_LAGS:
        out[f"temp_lag{k}"] = temp.shift(k)
    for k in range(0, 5):
        out[f"tmax_lag{k}"] = g["max_temp_c"].shift(k)
        out[f"tmin_lag{k}"] = g["min_temp_c"].shift(k)
    dtr = df["max_temp_c"] - df["min_temp_c"]
    out["dtr_mean_4w"] = dtr.groupby(df["ward_id"]).transform(lambda s: s.rolling(4, min_periods=4).mean())
    out["temp_mean_4w"] = temp.transform(lambda s: s.rolling(4, min_periods=4).mean())
    out["temp_dist_opt_4w"] = (out["temp_mean_4w"] - C.AEDES_OPT_TEMP_C).abs()

    hum = g["relative_humidity_pct"]
    for k in C.HUM_LAGS:
        out[f"hum_lag{k}"] = hum.shift(k)
    out["hum_mean_4w"] = hum.transform(lambda s: s.rolling(4, min_periods=4).mean())

    cases = g["dengue_cases"]
    for k in C.CASE_LAGS:
        out[f"cases_lag{k}"] = cases.shift(k)
    out["cases_mean_4w"] = cases.transform(lambda s: s.rolling(4, min_periods=4).mean())
    out["cases_trend_2w"] = out["cases_lag0"] - out["cases_lag2"]

    feats = pd.DataFrame(out, index=df.index)
    for c in STATIC_COLS:
        feats[c] = df[c]
    feats["city_code"] = df["city"].map(CITY_CODES).astype(float)
    # Vulnerability x weather interactions: same rain, different drainage
    feats["waterlog_x_rain4w"] = df["water_logging_risk_index"] * feats["rain_sum_4w"]
    feats["sanit_gap_x_rain4w"] = (1 - df["sanitation_score"]) * feats["rain_sum_4w"]

    meta = df[["date", "city", "ward_id", "dengue_cases"]]
    return pd.concat([meta, feats], axis=1)


def add_horizon_features(base: pd.DataFrame, h: int) -> pd.DataFrame:
    """Add target + horizon-specific seasonal features for horizon h (weeks)."""
    df = base.copy()
    g = df.groupby("ward_id", sort=False)["dengue_cases"]
    target_date = df["date"] + pd.Timedelta(weeks=h)
    woy = target_date.dt.isocalendar().week.astype(float)
    df["target_woy_sin"] = np.sin(2 * np.pi * woy / 52.18)
    df["target_woy_cos"] = np.cos(2 * np.pi * woy / 52.18)
    # Same target week last year (known at t because 52 - h >= 0)
    df["cases_same_week_last_year"] = g.shift(52 - h)
    df["target_date"] = target_date
    df["target"] = g.shift(-h)
    return df


def feature_columns(frame: pd.DataFrame) -> list[str]:
    drop = {"date", "city", "ward_id", "dengue_cases", "target", "target_date"}
    return [c for c in frame.columns if c not in drop]


FEATURE_GROUPS = {
    "Rainfall (lagged)": ("rain_", "heavy_rain"),
    "Temperature": ("temp_", "tmax_", "tmin_", "dtr_"),
    "Humidity": ("hum_",),
    "Recent cases": ("cases_",),
    "Ward vulnerability": ("population_density", "water_logging", "sanitation", "waterlog_x", "sanit_gap_x", "city_code"),
    "Seasonality": ("target_woy",),
}


def feature_group(name: str) -> str:
    # interactions are attributed to vulnerability x rain, checked before plain rain prefixes
    for grp, prefixes in FEATURE_GROUPS.items():
        if grp == "Rainfall (lagged)":
            continue
        if name.startswith(prefixes):
            return grp
    if name.startswith(FEATURE_GROUPS["Rainfall (lagged)"]):
        return "Rainfall (lagged)"
    return "Other"


def pretty_feature(name: str, h: int | None = None) -> str:
    """Human-readable label, showing the effective lag relative to the predicted week."""
    import re
    m = re.match(r"(rain|temp|hum|tmax|tmin|cases)_lag(\d+)$", name)
    labels = {"rain": "Rainfall", "temp": "Avg temp", "hum": "Humidity", "tmax": "Max temp",
              "tmin": "Min temp", "cases": "Cases"}
    if m:
        k = int(m.group(2))
        eff = f" ({k + h} wk before target)" if h is not None else ""
        return f"{labels[m.group(1)]} at t-{k}{eff}"
    custom = {
        "rain_sum_lag1_3": "Rain in breeding window (t-1..t-3)",
        "heavy_rain_weeks_4w": "Heavy-rain weeks (last 4)",
        "dtr_mean_4w": "Diurnal temp range (4w)",
        "temp_mean_4w": "Avg temp (4w)",
        "temp_dist_opt_4w": "Distance from 29°C optimum",
        "hum_mean_4w": "Humidity (4w)",
        "cases_mean_4w": "Cases (4w mean)",
        "cases_trend_2w": "Case trend (2w)",
        "cases_same_week_last_year": "Cases same week last year",
        "waterlog_x_rain4w": "Water-logging x 4w rain",
        "sanit_gap_x_rain4w": "Poor sanitation x 4w rain",
        "population_density_sqkm": "Population density",
        "water_logging_risk_index": "Water-logging index",
        "sanitation_score": "Sanitation score",
        "city_code": "City",
        "target_woy_sin": "Season (week of year, sin)",
        "target_woy_cos": "Season (week of year, cos)",
    }
    if name in custom:
        return custom[name]
    m = re.match(r"rain_sum_(\d+)w", name)
    if m:
        return f"Rainfall total last {m.group(1)} wks"
    return name
