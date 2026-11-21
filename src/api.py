"""Shotorko web API (FastAPI) + static web app.

    python run.py            # or: uvicorn src.api:app --port 8000

Every forecast is produced on request by the serialized LightGBM bundle; results
are memoised per (week, scenario, mode) so repeated views cost nothing.
"""
from __future__ import annotations

import json
import math
import time
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from .predict import Engine, Scenario
from .risk import channel_lookup

app = FastAPI(title="Shotorko API", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)

ENGINE = Engine()
STATS = {"model_calls": 0, "cache_hits": 0, "last_latency_ms": 0.0}
WEB_DIR = C.ROOT / "web"
FIRST_DATE = pd.Timestamp("2017-01-02")


def _f(x, nd=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


# ------------------------------------------------------------------ ward layout
def _ward_positions() -> dict:
    """Schematic positions: each city's wards on a small hex grid around the real city centre.
    (Dataset ward IDs do not map to official ward boundaries.)"""
    # cluster anchors near each real city centre, nudged apart so Dhaka North/South don't overlap
    anchors = {"Dhaka North": (23.852, 90.392), "Dhaka South": (23.700, 90.412), "Gazipur": (24.020, 90.430),
               "Narayanganj": (23.612, 90.535), "Chattogram": (22.356, 91.800)}
    pos, step = {}, 0.02
    for city, g in ENGINE.wards.groupby("city"):
        lat0, lon0 = anchors[city]
        ids = sorted(g.ward_id, key=lambda w: int(w.split("_")[-1]))
        nc = math.ceil(math.sqrt(len(ids)))
        nr = math.ceil(len(ids) / nc)
        for i, w in enumerate(ids):
            r, c = divmod(i, nc)
            dx = (c - (nc - 1) / 2 + (0.5 if r % 2 else 0) - 0.25) * step
            dy = ((nr - 1) / 2 - r) * step * 0.87
            pos[w] = (lat0 + dy, lon0 + dx / math.cos(math.radians(lat0)))
    return pos


POS = _ward_positions()
VALID_DATES = [d for d in pd.to_datetime(ENGINE.dates)
               if d >= FIRST_DATE and d + pd.Timedelta(weeks=max(C.HORIZONS)) <= pd.Timestamp(ENGINE.dates[-1])]
VALID_SET = {d.strftime("%Y-%m-%d") for d in VALID_DATES}


def _scenario(rain: float, temp: float, hum: float) -> Scenario:
    return Scenario(round(rain, 2), round(temp, 2), round(hum, 1))


# ------------------------------------------------------------------ live weather (cached)
_LIVE = {"raw": None, "ref": None, "weekly": None, "at": 0.0}


def _live():
    from .weather_client import CACHE_TTL_HOURS, build_live_raw, fetch_city_weather, to_weekly
    if _LIVE["raw"] is None or time.time() - _LIVE["at"] > CACHE_TTL_HOURS * 3600:
        weekly = to_weekly(fetch_city_weather())
        raw, ref = build_live_raw(ENGINE, weekly)
        _LIVE.update(raw=raw, ref=ref, weekly=weekly, at=time.time())
    return _LIVE["raw"], _LIVE["ref"]


def _resolve(date: str | None, mode: str):
    if mode == "live":
        try:
            raw, ref = _live()
        except Exception as exc:
            raise HTTPException(503, f"Live weather unavailable: {exc}")
        return ref, raw
    if date is None:
        date = "2025-07-07"
    if date not in VALID_SET:
        raise HTTPException(400, "date must be a dataset week between 2017-01-02 and 2025-12-01")
    return pd.Timestamp(date), None


@lru_cache(maxsize=512)
def _forecast_cached(date: str, rain: float, temp: float, hum: float, mode: str):
    ref, raw_override = _resolve(date if mode == "replay" else None, mode)
    sc = _scenario(rain, temp, hum)
    t0 = time.perf_counter()
    if raw_override is not None:   # live mode: the simulator adjusts the live weather itself
        if sc.active:
            raw_override = ENGINE.apply_scenario(raw_override, ref, sc)
        fc = ENGINE.forecast(ref, raw_override=raw_override)
    else:
        fc = ENGINE.forecast(ref, None, sc if sc.active else None)
    latency = (time.perf_counter() - t0) * 1000
    STATS["model_calls"] += 1
    # observed tier from the actual count, using the same endemic-channel thresholds (replay only)
    ch = channel_lookup(ENGINE.bundle["risk"], fc.ward_id, fc.target_date)
    a = fc["actual"].to_numpy(dtype=float)
    obs = np.select([a > ch.T_critical.values, a > ch.T_high.values, a > ch.T_medium.values], [3, 2, 1], 0)
    fc = fc.assign(obs_tier=[None if np.isnan(x) else C.TIERS[int(o)] for x, o in zip(a, obs)])
    wards = []
    for wid, g in fc.groupby("ward_id", sort=False):
        g = g.sort_values("horizon")
        wards.append({"id": wid, "h": [{
            "pred": _f(r.pred_cases), "lo": _f(r.lo80), "hi": _f(r.hi80), "tier": r.tier, "score": _f(r.risk_score, 0),
            "normal": _f(r.seasonal_normal), "threshold": _f(r.alert_threshold),
            "actual": None if mode == "live" or pd.isna(r.actual) else int(r.actual),
            "obs_tier": None if mode == "live" else r.obs_tier} for r in g.itertuples()]})
    targets = [pd.Timestamp(t).strftime("%Y-%m-%d") for t in sorted(fc.target_date.unique())]
    return {"ref_date": ref.strftime("%Y-%m-%d"), "targets": targets, "mode": mode,
            "out_of_sample": bool(ref + pd.Timedelta(weeks=2) > pd.Timestamp(C.VAL_END)),
            "scenario": {"rain": rain, "temp": temp, "hum": hum}, "latency_ms": round(latency, 1), "wards": wards}


# ------------------------------------------------------------------ endpoints
@app.get("/api/meta")
def meta():
    w = ENGINE.wards
    return {
        "app": "Shotorko", "team": "TRIGUN", "horizons": list(C.HORIZONS),
        "cities": [{"id": c, "lat": lat, "lon": lon} for c, (lat, lon) in C.CITY_CENTROIDS.items()],
        "wards": [{"id": r.ward_id, "city": r.city, "n": int(r.ward_id.split("_")[-1]),
                   "lat": POS[r.ward_id][0], "lon": POS[r.ward_id][1],
                   "density": int(r.population_density_sqkm), "waterlogging": _f(r.water_logging_risk_index),
                   "sanitation": _f(r.sanitation_score)} for r in w.itertuples()],
        "dates": [d.strftime("%Y-%m-%d") for d in VALID_DATES],
        "weekly_cases": {d.strftime("%Y-%m-%d"): int(v) for d, v in ENGINE.raw.groupby("date").dengue_cases.sum().items()
                         if d.strftime("%Y-%m-%d") in VALID_SET},
        "default_date": "2025-07-07", "test_start": "2023-12-18",
        "model": ENGINE.bundle["meta"],
    }


@app.get("/api/forecast")
def forecast(date: str | None = None, rain: float = Query(1.0, ge=0, le=3), temp: float = Query(0.0, ge=-5, le=5),
             hum: float = Query(0.0, ge=-30, le=30), mode: str = Query("replay", pattern="^(replay|live)$")):
    hits = _forecast_cached.cache_info().hits
    out = _forecast_cached(date or "2025-07-07", round(rain, 2), round(temp, 2), round(hum, 1), mode)
    if _forecast_cached.cache_info().hits > hits:
        STATS["cache_hits"] += 1
    else:
        STATS["last_latency_ms"] = out["latency_ms"]
    return out


@app.get("/api/ward/{ward_id}")
def ward(ward_id: str, date: str | None = None, h: int = Query(3, ge=2, le=4), rain: float = 1.0, temp: float = 0.0,
         hum: float = 0.0, mode: str = Query("replay", pattern="^(replay|live)$")):
    if ward_id not in POS:
        raise HTTPException(404, "unknown ward")
    ref, raw_override = _resolve(date, mode)
    sc = _scenario(rain, temp, hum)
    if raw_override is not None and sc.active:
        raw_override = ENGINE.apply_scenario(raw_override, ref, sc)
    use_sc = sc if (sc.active and raw_override is None) else None
    grouped, top = ENGINE.explain(ward_id, ref, h, use_sc, raw_override)
    src = raw_override if raw_override is not None else ENGINE.raw
    hist = src[(src.ward_id == ward_id) & (src.date > ref - pd.Timedelta(weeks=12))
               & (src.date <= ref + pd.Timedelta(weeks=4))].sort_values("date").copy()
    if use_sc is not None:
        hist = ENGINE.apply_scenario(hist, ref, sc)
    ch = channel_lookup(ENGINE.bundle["risk"], hist.ward_id, hist.date)
    hist["normal"], hist["threshold"] = ch["mean"].values, ch["T_high"].values
    past = hist[hist.date <= ref]
    clim = ENGINE.climatology(ward_id, ref)
    rain4 = float(past.rainfall_mm.tail(4).sum())
    return {
        "ward": ward_id, "ref_date": ref.strftime("%Y-%m-%d"), "h": h, "mode": mode,
        "baseline_cases": _f(grouped.attrs.get("baseline_cases", 0)),
        "drivers": [{"group": r.group, "mult": _f(r.multiplier, 3)} for r in grouped.itertuples()],
        "top_features": [{"label": r.label, "value": _f(r.value), "mult": _f(r.multiplier, 3), "group": r.group}
                         for r in top.itertuples()],
        "history": [{"date": d.strftime("%Y-%m-%d"), "cases": None if (mode == "live") else int(c),
                     "normal": _f(n), "threshold": _f(t), "future": bool(d > ref)}
                    for d, c, n, t in zip(hist.date, hist.dengue_cases, hist.normal, hist.threshold)],
        "weather": [{"date": d.strftime("%Y-%m-%d"), "rain": _f(r, 1), "temp": _f(t, 1), "hum": _f(u, 0)}
                    for d, r, t, u in zip(past.date, past.rainfall_mm, past.avg_temp_c, past.relative_humidity_pct)],
        "rain4": round(rain4), "rain_ratio": _f(rain4 / max(4 * clim["rain_week_mean"], 1e-6)),
        "temp4": _f(past.avg_temp_c.tail(4).mean(), 1), "hum4": _f(past.relative_humidity_pct.tail(4).mean(), 0),
    }


@app.get("/api/validation")
def validation():
    m = _metrics()
    bt = _backtest()
    bt["month"] = pd.to_datetime(bt.target_date).dt.month
    by_month = {}
    for h, g in bt.groupby("horizon"):
        e = g.assign(err=g.actual - g.pred).groupby("month").err
        by_month[str(h)] = [{"month": int(k), "mae": _f(v.abs().mean(), 3), "rmse": _f(np.sqrt((v ** 2).mean()), 3)}
                            for k, v in e]
    return {"metrics": m, "by_month": by_month}


@lru_cache(maxsize=1)
def _backtest_df():
    return pd.read_csv(C.BACKTEST_PATH, parse_dates=["date", "target_date"])


def _backtest():
    return _backtest_df().copy()


@app.get("/api/backtest/{ward_id}")
def backtest(ward_id: str, h: int = Query(3, ge=2, le=4)):
    b = _backtest_df()
    b = b[(b.ward_id == ward_id) & (b.horizon == h)].sort_values("target_date")
    if b.empty:
        raise HTTPException(404, "unknown ward")
    return {"ward": ward_id, "h": h, "points": [
        {"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "actual": int(a), "pred": _f(p), "lo": _f(lo), "hi": _f(hi)}
        for d, a, p, lo, hi in zip(b.target_date, b.actual, b.pred, b.lo, b.hi)]}


@app.get("/api/usage")
def usage():
    from .weather_client import api_usage_today
    return {"weather_api_today": api_usage_today(), "model_calls": STATS["model_calls"],
            "forecast_cache_hits": STATS["cache_hits"], "last_inference_ms": STATS["last_latency_ms"],
            "llm_tokens": 0, "model": ENGINE.bundle["meta"], "n_models": 3 * len(C.HORIZONS)}


@lru_cache(maxsize=1)
def _metrics():
    return json.loads(C.METRICS_PATH.read_text())


@app.post("/api/upload")
async def upload(request: Request):
    """Forecast from a user's own (possibly short or incomplete) CSV, sent as the raw request body."""
    from .upload import UploadError, forecast_upload, parse
    body = await request.body()
    try:
        wk, info = parse(body.decode("utf-8-sig", errors="replace"), ENGINE.raw.drop_duplicates("ward_id"))
        rows = forecast_upload(ENGINE, wk, info, _metrics())
    except UploadError as exc:
        raise HTTPException(400, str(exc))
    return {"info": info, "forecasts": rows}


@app.get("/api/sample.csv", response_class=PlainTextResponse)
def sample():
    from .upload import sample_csv
    return PlainTextResponse(sample_csv(ENGINE), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=shotorko_sample_incomplete.csv"})


@app.get("/api/hospitals")
def hospitals():
    from .hospitals import HOSPITALS, HOTLINE
    return {"hospitals": HOSPITALS, "hotline": HOTLINE,
            "note": "Approximate locations. No open real-time bed data exists; pressure shown is forecast-based."}


@app.get("/api/heatmap")
def heatmap(h: int = Query(3, ge=2, le=4)):
    """Ward x week risk matrix over the out-of-sample test years (forecast tier, observed tier, risk score)."""
    b = _backtest_df()
    b = b[b.horizon == h]
    order = ENGINE.wards.sort_values(["city", "ward_id"], key=lambda s: s.map(
        {"Dhaka North": 0, "Dhaka South": 1, "Gazipur": 2, "Narayanganj": 3, "Chattogram": 4}) if s.name == "city" else s)
    wards = order.ward_id.tolist()
    dates = sorted(b.target_date.unique())
    piv = lambda col: b.pivot(index="ward_id", columns="target_date", values=col).reindex(index=wards, columns=dates)
    return {"h": h, "wards": wards, "cities": order.city.tolist(),
            "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates],
            "pred_tier": piv("tier_pred").fillna(0).astype(int).values.tolist(),
            "obs_tier": piv("tier_actual").fillna(0).astype(int).values.tolist(),
            "score": piv("risk_score").fillna(0).round(0).astype(int).values.tolist()}


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
