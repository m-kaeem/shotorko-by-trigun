"""Optional live-weather adapter (Open-Meteo, free, no API key, CC BY 4.0).

Cost controls
* ONE HTTP request covers all 5 cities (comma-separated coordinates).
* Responses are cached on disk for CACHE_TTL_HOURS (default 6 h), so a whole
  day of dashboard use costs at most 4 calls; Streamlit adds an in-memory cache.
* 10 s timeout, a single retry, and graceful fallback to the offline dataset.
* Every network call is counted in .cache/api_calls.json and shown in the UI.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date

import numpy as np
import pandas as pd
import requests

from . import config as C

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARS = "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean"
CACHE_TTL_HOURS = 6
COUNTER = C.CACHE_DIR / "api_calls.json"


def _bump_counter(kind: str) -> None:
    C.CACHE_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    try:
        data = json.loads(COUNTER.read_text())
    except Exception:
        data = {}
    day = data.setdefault(today, {"network_calls": 0, "cache_hits": 0})
    day[kind] += 1
    COUNTER.write_text(json.dumps(data, indent=1))


def api_usage_today() -> dict:
    try:
        return json.loads(COUNTER.read_text()).get(date.today().isoformat(), {"network_calls": 0, "cache_hits": 0})
    except Exception:
        return {"network_calls": 0, "cache_hits": 0}


def _cached_get(url: str, params: dict) -> dict:
    C.CACHE_DIR.mkdir(exist_ok=True)
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
    path = C.CACHE_DIR / f"openmeteo_{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_HOURS * 3600:
        _bump_counter("cache_hits")
        return json.loads(path.read_text())
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            _bump_counter("network_calls")
            payload = r.json()
            path.write_text(json.dumps(payload))
            return payload
        except Exception as e:  # network down, rate limit, etc.
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    if path.exists():   # stale cache is better than nothing
        return json.loads(path.read_text())
    raise RuntimeError(f"Open-Meteo unavailable: {last_err}")


def fetch_city_weather(past_days: int = 92, forecast_days: int = 16) -> pd.DataFrame:
    """Daily weather for every city in ONE request -> tidy DataFrame."""
    cities = list(C.CITY_CENTROIDS)
    params = {
        "latitude": ",".join(f"{C.CITY_CENTROIDS[c][0]:.4f}" for c in cities),
        "longitude": ",".join(f"{C.CITY_CENTROIDS[c][1]:.4f}" for c in cities),
        "daily": DAILY_VARS, "timezone": "Asia/Dhaka",
        "past_days": past_days, "forecast_days": forecast_days,
    }
    payload = _cached_get(FORECAST_URL, params)
    payload = payload if isinstance(payload, list) else [payload]
    frames = []
    for city, loc in zip(cities, payload):
        d = pd.DataFrame(loc["daily"])
        d["city"] = city
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    return df


def to_weekly(daily: pd.DataFrame, today: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate to Monday-starting weeks (same convention as the dataset)."""
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Dhaka").date())
    d = daily.copy()
    d["date"] = d["time"] - pd.to_timedelta(d["time"].dt.dayofweek, unit="D")
    wk = d.groupby(["city", "date"]).agg(
        avg_temp_c=("temperature_2m_mean", "mean"), max_temp_c=("temperature_2m_max", "max"),
        min_temp_c=("temperature_2m_min", "min"), rainfall_mm=("precipitation_sum", "sum"),
        relative_humidity_pct=("relative_humidity_2m_mean", "mean"), n_days=("time", "count")).reset_index()
    wk["observed"] = (wk["date"] + pd.Timedelta(days=6)) < today
    wk = wk[wk["n_days"] == 7]
    return wk


def build_live_raw(engine, weekly: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Build a ward panel whose recent weather is live and whose older history is the
    training climatology. Case counts are set to the seasonal normal because no live
    surveillance feed is connected yet (clearly flagged in the UI)."""
    obs = weekly[weekly["observed"]]
    ref = obs["date"].max()
    start = ref - pd.Timedelta(weeks=60)
    dates = pd.date_range(start, ref, freq="7D")
    tr = engine.raw[engine.raw.date <= engine.bundle["risk"]["fit_end"]].copy()
    tr["woy"] = tr.date.dt.isocalendar().week.clip(upper=52).astype(int)
    clim = tr.groupby(["ward_id", "woy"])[["avg_temp_c", "max_temp_c", "min_temp_c", "rainfall_mm",
                                           "relative_humidity_pct", "dengue_cases"]].mean().reset_index()
    panel = engine.wards.merge(pd.DataFrame({"date": dates}), how="cross")
    panel["woy"] = panel.date.dt.isocalendar().week.clip(upper=52).astype(int)
    panel = panel.merge(clim, on=["ward_id", "woy"], how="left")
    live = obs.set_index(["city", "date"])
    cols = ["avg_temp_c", "max_temp_c", "min_temp_c", "rainfall_mm", "relative_humidity_pct"]
    idx = pd.MultiIndex.from_arrays([panel.city, panel.date])
    hit = idx.isin(live.index)
    panel.loc[hit, cols] = live.reindex(idx[hit])[cols].values
    panel["weather_source"] = np.where(hit, "Open-Meteo (live)", "climatology")
    panel["dengue_cases"] = panel["dengue_cases"].round(0)
    return panel.drop(columns="woy"), ref
