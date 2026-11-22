"""'Your data': forecasts from a short or incomplete CSV a user uploads.

Accepted: any subset of the dataset columns, with flexible names (rain, rainfall,
temperature, humidity, cases, ...). Daily rows are rolled up to Monday weeks.
Only a date column and at least rainfall or average temperature are required.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from . import config as C
from .features import REQUIRED_COLUMNS, STATIC_COLS, add_horizon_features, build_base_features
from .risk import predict_tiers
from .robust import groups_present

MAX_BYTES = 2_000_000
SYNONYMS = {
    "date": ["date", "week", "week_start", "week_of", "day", "time"],
    "ward_id": ["ward_id", "ward", "area", "area_id", "location", "zone"],
    "city": ["city", "city_corporation", "corporation"],
    "rainfall_mm": ["rainfall_mm", "rainfall", "rain", "rain_mm", "precipitation", "precip", "precipitation_mm", "precipitation_sum"],
    "avg_temp_c": ["avg_temp_c", "avg_temp", "temperature", "temp", "mean_temp", "temp_c", "temperature_2m_mean", "tavg"],
    "max_temp_c": ["max_temp_c", "max_temp", "tmax", "temperature_2m_max"],
    "min_temp_c": ["min_temp_c", "min_temp", "tmin", "temperature_2m_min"],
    "relative_humidity_pct": ["relative_humidity_pct", "humidity", "relative_humidity", "rh", "humidity_pct", "relative_humidity_2m_mean"],
    "dengue_cases": ["dengue_cases", "cases", "dengue", "case_count", "patients"],
    "population_density_sqkm": ["population_density_sqkm", "population_density", "density"],
    "water_logging_risk_index": ["water_logging_risk_index", "water_logging", "waterlogging", "water_logging_index"],
    "sanitation_score": ["sanitation_score", "sanitation"],
}
CITY_ALIASES = {"dhaka north": "Dhaka North", "dncc": "Dhaka North", "dhaka south": "Dhaka South", "dscc": "Dhaka South",
                "chattogram": "Chattogram", "chittagong": "Chattogram", "ccc": "Chattogram", "gazipur": "Gazipur",
                "gcc": "Gazipur", "narayanganj": "Narayanganj", "ncc": "Narayanganj"}


class UploadError(ValueError):
    pass


def parse(text: str, registry: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if len(text.encode()) > MAX_BYTES:
        raise UploadError("File is larger than 2 MB. Please upload at most a few years of weekly rows.")
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        raise UploadError(f"Could not read the CSV: {exc}")
    norm = {c: c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns}
    df = df.rename(columns=norm)
    mapping, used = {}, set()
    for target, names in SYNONYMS.items():
        for n in names:
            if n in df.columns and n not in used:
                mapping[n] = target
                used.add(n)
                break
    df = df[list(mapping)].rename(columns=mapping)
    if "date" not in df:
        raise UploadError("A 'date' column is required (one row per week or per day).")
    if "rainfall_mm" not in df and "avg_temp_c" not in df:
        raise UploadError("At least a rainfall or an average-temperature column is required.")
    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=False)
    df = df.dropna(subset=["date"])
    if df.empty:
        raise UploadError("No valid dates found.")
    if len(df) > 20000:
        raise UploadError("Too many rows (max 20,000).")
    if "ward_id" not in df:
        df["ward_id"] = "Uploaded_Area"
    df["ward_id"] = df["ward_id"].astype(str).str.strip()
    provided = set(df.columns)
    num = [c for c in df.columns if c not in ("date", "ward_id", "city")]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")

    # daily -> weekly (Monday weeks, like the training data)
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")
    daily = df.groupby("ward_id")["date"].apply(lambda s: s.sort_values().diff().dt.days.median()).fillna(7).min() < 7
    total = lambda s: s.sum(min_count=1)   # keep NaN (missing) instead of turning it into 0
    agg = {c: (total if c in ("rainfall_mm", "dengue_cases") else "mean") for c in num}
    if "max_temp_c" in agg: agg["max_temp_c"] = "max"
    if "min_temp_c" in agg: agg["min_temp_c"] = "min"
    if "city" in df: agg["city"] = "first"
    wk = df.groupby(["ward_id", "week"], as_index=False).agg(agg).rename(columns={"week": "date"})

    # known wards: fill city + fixed ward profile from the registry (these are properties of the ward, not observations)
    reg = registry.set_index("ward_id")
    filled = []
    if "city" in wk:
        wk["city"] = wk["city"].astype(str).str.strip().str.lower().map(CITY_ALIASES).astype(object)
    else:
        wk["city"] = pd.Series([None] * len(wk), index=wk.index, dtype=object)
    for col in ["city", *STATIC_COLS]:
        if col not in wk:
            wk[col] = np.nan
        known = wk["ward_id"].isin(reg.index) & wk[col].isna()
        if known.any():
            wk.loc[known, col] = wk.loc[known, "ward_id"].map(reg[col])
            filled.append(col)
    for col in REQUIRED_COLUMNS:
        if col not in wk:
            wk[col] = np.nan
    wk = wk[REQUIRED_COLUMNS].sort_values(["ward_id", "date"]).reset_index(drop=True)
    after = {c for c in REQUIRED_COLUMNS if wk[c].notna().any()}
    info = {
        "rows_uploaded": int(len(df)), "aggregated_from_daily": bool(daily), "wards": sorted(wk.ward_id.unique().tolist()),
        "columns_recognised": sorted(provided - {"week"}), "filled_from_ward_registry": filled,
        "groups": groups_present(after),
        "weeks_per_ward": {w: int(n) for w, n in wk.groupby("ward_id").size().items()},
    }
    return wk, info


def scenario_for(groups: dict, weeks: int) -> str:
    """Closest evaluated completeness scenario (for the expected-error estimate)."""
    rain, temp = groups["rainfall"], groups["avg_temperature"]
    cases, profile, hum, mm = groups["case_history"], groups["ward_profile"], groups["humidity"], groups["max_min_temperature"]
    if rain and not temp and not cases and weeks <= 6:
        return "rain_only_4w"
    if not cases and not profile:
        return "new_area"
    if not cases and not hum and weeks <= 12:
        return "rain_temp_8w"
    if not temp:
        return "no_temperature"
    if weeks <= 6:
        return "short_4w"
    if weeks < 53:
        return "short_12w"
    if not cases:
        return "no_cases"
    if not mm:
        return "no_minmax"
    if not hum:
        return "no_humidity"
    return "full"


def forecast_upload(engine, wk: pd.DataFrame, info: dict, metrics: dict) -> list[dict]:
    b = engine.bundle
    base = build_base_features(wk, b["thresholds"])
    ward_groups = {w: groups_present({c for c in REQUIRED_COLUMNS if g[c].notna().any()}) for w, g in wk.groupby("ward_id")}
    info["groups_by_ward"] = ward_groups
    out = []
    for h in C.HORIZONS:
        fr = add_horizon_features(base, h)
        last = fr.loc[fr.groupby("ward_id")["date"].idxmax()]
        cols = b["feature_cols"][h]
        X = last[cols]
        # complete rows use the deployed model; anything with gaps uses the dropout-trained robust model
        full_rows = X.notna().all(axis=1).values
        mu, lo, hi = np.zeros(len(X)), np.zeros(len(X)), np.zeros(len(X))
        for flag, models in ((True, b["models"][h]), (False, b["robust"][h])):
            sel = full_rows == flag
            if not sel.any():
                continue
            m = models["point"].predict(X[sel]).clip(0)
            margin = models.get("cqr_margin", 0.0)
            mu[sel] = m
            lo[sel] = (np.minimum(models["q10"].predict(X[sel]), m) - margin).clip(0)
            hi[sel] = np.maximum(models["q90"].predict(X[sel]), m) + margin
        tiers = predict_tiers(b["risk"], mu, last["ward_id"], last["target_date"], h)
        for i, (_, r) in enumerate(last.iterrows()):
            weeks = info["weeks_per_ward"][r.ward_id]
            key = scenario_for(ward_groups[r.ward_id], weeks)
            sc = metrics["horizons"][str(h)]["completeness"]["scenarios"][key]
            actual = engine.raw[(engine.raw.ward_id == r.ward_id) & (engine.raw.date == r.target_date)].dengue_cases
            out.append({
                "ward": r.ward_id, "city": None if pd.isna(r.city) else r.city, "horizon": h,
                "ref_date": r.date.strftime("%Y-%m-%d"), "target_date": r.target_date.strftime("%Y-%m-%d"),
                "pred": round(float(mu[i]), 2), "lo": round(float(lo[i]), 2), "hi": round(float(hi[i]), 2),
                "tier": tiers["tier"].iloc[i], "score": round(float(tiers["risk_score"].iloc[i])),
                "seasonal_normal": round(float(tiers["mean"].iloc[i]), 2),
                "model": "full" if full_rows[i] else "robust", "features_available": round(float(X.iloc[i].notna().mean()), 2),
                "scenario": key, "scenario_label": sc["label"], "expected_mae": round(sc["robust"]["MAE"], 2),
                "weeks_of_history": weeks,
                "actual_in_dataset": None if actual.empty else int(actual.iloc[0]),
            })
    return out


def sample_csv(engine) -> str:
    """A deliberately incomplete example: 10 weeks, rain + avg temperature (+ cases for known wards)."""
    end = pd.Timestamp("2025-07-07")
    r = engine.raw[(engine.raw.date > end - pd.Timedelta(weeks=10)) & (engine.raw.date <= end)]
    a = r[r.ward_id.isin(["DNCC_Ward_01", "CCC_Ward_03"])][["date", "ward_id", "rainfall_mm", "avg_temp_c", "dengue_cases"]]
    new = r[r.ward_id == "DSCC_Ward_11"][["date", "rainfall_mm", "avg_temp_c"]].assign(ward_id="New_Area_A")
    out = pd.concat([a, new])[["date", "ward_id", "rainfall_mm", "avg_temp_c", "dengue_cases"]]
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out.to_csv(index=False)
