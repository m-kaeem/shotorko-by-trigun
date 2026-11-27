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


@app.middleware("http")
async def _fresh_page_files(request: Request, call_next):
    """Make browsers re-check page files (HTML/JS/CSS) on every load, so an update shows without a hard
    refresh. Unchanged files still come back as a cheap 304 Not Modified."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response

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
_LIVE = {"raw": None, "ref": None, "weekly": None, "at": 0.0, "fresh": None}


def _live():
    from .weather_client import CACHE_TTL_HOURS, build_live_raw, fetch_city_weather, to_weekly
    if _LIVE["raw"] is None or time.time() - _LIVE["at"] > CACHE_TTL_HOURS * 3600:
        weekly = to_weekly(fetch_city_weather())
        raw, ref = build_live_raw(ENGINE, weekly)
        from .weather_client import freshness
        _LIVE.update(raw=raw, ref=ref, weekly=weekly, at=time.time(), fresh=freshness())
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
            "scenario": {"rain": rain, "temp": temp, "hum": hum}, "latency_ms": round(latency, 1), "wards": wards,
            "weather": _LIVE.get("fresh") if mode == "live" else None}


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


# ------------------------------------------------------------------ Phase 2 ward plans (docs/PHASE2_PLAN_CONTRACT.md)
def _plans(date, h, stock_pct, total_crews, deployments=None, rain=1.0, temp=0.0, hum=0.0, floating=None,
           min_share_pct=0.0):
    from .plan import build_plans, totals
    date = date or "2025-07-07"
    if date not in VALID_SET:
        raise HTTPException(400, "date must be a dataset week between 2017-01-02 and 2025-12-01")
    if not 30 <= stock_pct <= 100:
        raise HTTPException(400, "stock_pct must be between 30 and 100")
    sc = _scenario(rain, temp, hum)
    try:
        plans = build_plans(ENGINE, date, h, stock_pct=stock_pct, total_crews=total_crews,
                            deployments=deployments, scenario=sc if sc.active else None, positions=POS,
                            floating_beds=floating or None, min_share_pct=min_share_pct)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"plans": plans, "totals": totals(plans)}


@app.get("/api/plan")
def plan(date: str | None = None, h: int = Query(3, ge=2, le=4), stock_pct: float = Query(100.0, ge=30, le=100),
         total_crews: int = Query(20, ge=0, le=1000), rain: float = Query(1.0, ge=0, le=3),
         temp: float = Query(0.0, ge=-5, le=5), hum: float = Query(0.0, ge=-30, le=30),
         floating_beds: str | None = Query(None, description="reserve beds per city, e.g. 'Gazipur:2'"),
         min_share_pct: float = Query(0.0, ge=0, le=50, description="lifeline: guaranteed % of need per ward")):
    from .surge import CITY_ORDER, parse_city_counts
    try:
        floating = parse_city_counts(floating_beds, CITY_ORDER)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _plans(date, h, stock_pct, total_crews, None, rain, temp, hum, floating, min_share_pct)


@app.post("/api/plan/simulate")
async def plan_simulate(request: Request):
    try:
        b = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    dep = b.get("deployments") or {}
    if not isinstance(dep, dict):
        raise HTTPException(400, "deployments must be an object {ward_id: crews_sent}")
    try:
        h = int(b.get("h", 3))
        stock = float(b.get("stock_pct", 100.0))
        crews = int(b.get("total_crews", 20))
    except (TypeError, ValueError):
        raise HTTPException(400, "h, stock_pct and total_crews must be numbers")
    if h not in C.HORIZONS:
        raise HTTPException(400, "h must be 2, 3 or 4")
    floating = b.get("floating_beds") or None
    if floating is not None and not isinstance(floating, (dict, int)):
        raise HTTPException(400, "floating_beds must be {city: beds} or one number for every city")
    return _plans(b.get("date"), h, stock, crews, dep, float(b.get("rain", 1.0)), float(b.get("temp", 0.0)),
                  float(b.get("hum", 0.0)), floating, float(b.get("min_share_pct", 0.0)))


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


# ------------------------------------------------------------------ Phase 2: ward plans + dispatch notices
def _dispatch_plans(date, h, stock_pct, total_crews) -> list[dict]:
    """Ward plans for notices: the real allocation engine (src/plan.py, Requirement 1), baseline weather."""
    return _plans(date, h, float(stock_pct), int(total_crews))["plans"]


def _one_plan(ward_id: str, date, h, stock_pct, total_crews) -> dict:
    if ward_id not in POS:
        raise HTTPException(404, "unknown ward")
    return next(p for p in _dispatch_plans(date, h, stock_pct, total_crews) if p["ward_id"] == ward_id)


@app.get("/api/dispatch/{ward_id}")
def dispatch_one(ward_id: str, date: str | None = None, h: int = Query(3, ge=2, le=4),
                 stock_pct: float = Query(100.0, ge=30, le=100), total_crews: int = Query(20, ge=0, le=500),
                 lang: str = Query("en", pattern="^(en|bn)$"),
                 channel: str = Query("all", pattern="^(all|sms|whatsapp|brief)$")):
    """One ward's notice. channel=brief returns the printable HTML page; all returns every channel as JSON."""
    from fastapi.responses import HTMLResponse
    from . import dispatch as D
    p = _one_plan(ward_id, date, h, stock_pct, total_crews)
    if channel == "brief":
        return HTMLResponse(D.brief_html(p, lang))
    text = D.sms(p, lang)
    out = {"notice": D.notice(p, lang), "sms": {"text": text, "chars": len(text), "segments": D.sms_segments(text)},
           "whatsapp": D.whatsapp(p, lang),
           "brief_url": f"/api/dispatch/{ward_id}?channel=brief&lang={lang}&h={h}&stock_pct={stock_pct}"
                        f"&total_crews={total_crews}&date={p['date']}"}
    if channel == "sms":
        return PlainTextResponse(text)
    if channel == "whatsapp":
        return PlainTextResponse(out["whatsapp"])
    return out


@app.get("/api/dispatch")
def dispatch_all(date: str | None = None, h: int = Query(3, ge=2, le=4), stock_pct: float = Query(100.0, ge=30, le=100),
                 total_crews: int = Query(20, ge=0, le=500), lang: str = Query("en", pattern="^(en|bn)$"),
                 min_tier: str = Query("High", pattern="^(Low|Medium|High|Critical)$")):
    """Notices for every ward at or above min_tier, in priority order (a whole dispatch round)."""
    from . import dispatch as D
    keep = C.TIERS[C.TIERS.index(min_tier):]
    plans = [p for p in _dispatch_plans(date, h, stock_pct, total_crews) if p["tier"] in keep]
    plans.sort(key=lambda p: p["priority_rank"])
    return {"count": len(plans), "notices": [{"ward_id": p["ward_id"], "city": p["city"], "tier": p["tier"], "priority_rank": p["priority_rank"],
                                              "deficit_level": p["deficit_level"], "sms": D.sms(p, lang),
                                              "whatsapp": D.whatsapp(p, lang)} for p in plans]}


# ------------------------------------------------------------------ Phase 2: automatic SMS through the Android gateway
@app.get("/api/sms/status")
def sms_status():
    from . import sms_gateway as G
    return {**G.public_settings(), **G.gateway_status()}


@app.post("/api/sms/send")
async def sms_send(request: Request):
    """Queue the SMS notice for one ward ({"ward_id": ...}) or a whole round ({"round": true, "min_tier": "High"})."""
    from . import dispatch as D
    from . import sms_gateway as G
    b = await request.json()
    date, h = b.get("date"), int(b.get("h", 3))
    stock, crews, lang = float(b.get("stock_pct", 100)), int(b.get("total_crews", 20)), b.get("lang", "en")
    if lang not in ("en", "bn") or h not in C.HORIZONS:
        raise HTTPException(400, "bad lang or h")
    plans = _dispatch_plans(date, h, stock, crews)
    if b.get("round"):
        tier = b.get("min_tier", "High")
        if tier not in C.TIERS:
            raise HTTPException(400, "bad min_tier")
        keep = C.TIERS[C.TIERS.index(tier):]
        chosen = sorted((p for p in plans if p["tier"] in keep), key=lambda p: p["priority_rank"])
    else:
        chosen = [p for p in plans if p["ward_id"] == b.get("ward_id")]
        if not chosen:
            raise HTTPException(404, "unknown ward")
    results = [G.enqueue(p["ward_id"], D.sms(p, lang), p["target_week"], lang, force=bool(b.get("force"))) for p in chosen]
    count = lambda st: sum(1 for r in results if r["status"] == st and not r["duplicate"])
    return {"mode": G.settings()["mode"], "requested": len(results), "queued": count("queued"),
            "skipped": count("skipped"), "duplicates": sum(1 for r in results if r["duplicate"]),
            "messages": [{"id": r["id"], "ward_id": r["ward_id"], "status": r["status"], "error": r["error"],
                          "duplicate": r["duplicate"]} for r in results]}


@app.get("/api/sms/outbox")
def sms_outbox(week: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    from . import sms_gateway as G
    return {"mode": G.settings()["mode"], "messages": G.outbox(limit, week)}


@app.post("/api/sms/retry")
def sms_retry():
    from . import sms_gateway as G
    return {"requeued": G.retry_failed()}


@app.post("/api/offline")
async def offline_switch(request: Request):
    """The in-app offline switch: {"on": true} forbids every internet request until switched back."""
    from . import offline
    b = await request.json()
    offline.set_forced(bool(b.get("on")))
    _LIVE.update(raw=None, at=0.0)   # next live request re-checks the network instead of reusing in-memory weather
    _forecast_cached.cache_clear()
    return offline.status()


# ------------------------------------------------------------------ Req 3: vector-control simulator
def _sim_args(body: dict):
    from . import simulation as SIM
    date = body.get("date") or "2025-07-07"
    if date not in VALID_SET:
        raise HTTPException(400, "date must be a dataset week between 2017-01-02 and 2025-12-01")
    h = int(body.get("h", 3))
    if h not in C.HORIZONS:
        raise HTTPException(400, "h must be 2, 3 or 4")
    total_crews = int(body.get("total_crews", 20))
    if not 0 <= total_crews <= 200:
        raise HTTPException(400, "total_crews must be 0-200")
    sc = _scenario(float(body.get("rain", 1.0)), float(body.get("temp", 0.0)), float(body.get("hum", 0.0)))
    sc = sc if sc.active else None
    dep = body.get("deployments")
    auto = dep is None or dep == "auto"
    if auto:   # no deployment given: recommend the one that prevents the most cases
        dep = SIM.best_deployment(ENGINE, date, total_crews, sc)
    if not isinstance(dep, dict):
        raise HTTPException(400, "deployments must be {ward_id: crews}, {ward_id: {larvicide, indoor}} or \"auto\"")
    try:
        dep = SIM._clean(ENGINE, dep)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    if SIM.crews_used(dep) > total_crews:
        raise HTTPException(400, f"deployments use {SIM.crews_used(dep)} crews but total_crews is {total_crews}")
    return SIM, date, h, total_crews, sc, dep, auto


@app.post("/api/simulate")
async def simulate_crews(request: Request):
    """Standalone simulator (works without the plan builder): per-ward counterfactual + ROI ranking."""
    body = await request.json()
    SIM, date, h, total_crews, sc, dep, auto = _sim_args(body)
    costs = {k: float(v) for k, v in (body.get("costs") or {}).items() if k in SIM.COSTS and float(v) >= 0}
    t0 = time.perf_counter()
    sims = SIM.simulate(ENGINE, date, dep, sc, costs=costs)
    ranking = SIM.roi_ranking(ENGINE, date, 1, sc)
    return {"date": date, "total_crews": total_crews, "auto": auto, "deployments": dep,
            "summary": SIM.summary(sims), "wards": sims, "roi_ranking": ranking,
            "costs": {**SIM.COSTS, **costs}, "assumptions": SIM.ASSUMPTIONS,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.get("/api/simulate/check")
def simulate_check():
    """Does the model respond to crew-changeable features the way the data says? (out-of-sample weeks)"""
    from . import simulation as SIM
    return SIM.model_check(ENGINE)


@app.get("/api/simulate/roi")
def simulate_roi(date: str = "2025-07-07", crews: int = Query(1, ge=1, le=6)):
    """All 60 wards ranked by cases prevented per crew if `crews` crews were sent there."""
    from . import simulation as SIM
    if date not in VALID_SET:
        raise HTTPException(400, "date must be a dataset week between 2017-01-02 and 2025-12-01")
    return {"date": date, "crews": crews, "ranking": SIM.roi_ranking(ENGINE, date, crews),
            "assumptions": SIM.ASSUMPTIONS}


@app.get("/api/health")
def health():
    """Liveness plus the offline picture: what runs locally, and whether the internet is there at all."""
    from . import offline
    from . import sms_gateway as G
    net = offline.status()
    sms = G.settings()["mode"]
    return JSONResponse({
        "ok": True, **net,
        "local": {"forecast_model": bool(ENGINE.bundle), "dataset_rows": int(len(ENGINE.raw)),
                  "supply_plans": True, "dispatch_templates": True, "web_assets": "bundled (deck.gl, map, fonts)"},
        "optional_network": {"live_weather": "available" if net["internet"] else "offline: replay and cached weather only",
                             "sms_gateway": f"{sms} (local Wi-Fi phone; unsent SMS wait in the outbox)"},
        "llm_calls": 0,
    })


from .ops_api import router as ops_router  # noqa: E402  (Phase 2 operations endpoints)

app.include_router(ops_router)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
