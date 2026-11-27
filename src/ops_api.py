"""Operations endpoints (Phase 2): constrained allocation and hospital overflow routing.

Both use the same cached LightGBM forecast as the map, so they work fully offline.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from .resources import allocate
from .saline import outlook, supply_backtest
from .surge import CITY_ORDER, load_capacity, parse_city_counts, route

router = APIRouter(prefix="/api/ops", tags=["operations"])


def _api():
    from . import api   # imported lazily: api.py includes this router at import time
    return api


def _capacity() -> pd.Series:
    return load_capacity(str(_api().ENGINE.bundle["risk"]["fit_end"]))


def _frame(date, h, rain, temp, hum, mode, city=None):
    a = _api()
    out = a._forecast_cached(date or "2025-07-07", round(rain, 2), round(temp, 2), round(hum, 1), mode)
    w = a.ENGINE.wards.set_index("ward_id")
    rows = []
    for x in out["wards"]:
        f = x["h"][h - 2]
        rows.append({"ward_id": x["id"], "city": w.at[x["id"], "city"], "density": int(w.at[x["id"], "population_density_sqkm"]),
                     "tier": f["tier"], "pred": f["pred"] or 0.0, "hi": f["hi"] or 0.0, "actual": f["actual"]})
    fc = pd.DataFrame(rows)
    if city and city != "All":
        if city not in set(fc.city):
            raise HTTPException(400, "unknown city")
        fc = fc[fc.city == city]
    ctx = {"ref_date": out["ref_date"], "target_date": out["targets"][h - 2], "h": h, "mode": mode,
           "scenario": out["scenario"], "city": city or "All"}
    return fc, ctx


@router.get("/allocate")
def allocate_ep(date: str | None = None, h: int = Query(3, ge=2, le=4), rain: float = Query(1.0, ge=0, le=3),
                temp: float = Query(0.0, ge=-5, le=5), hum: float = Query(0.0, ge=-30, le=30),
                mode: str = Query("replay", pattern="^(replay|live)$"),
                basis: str = Query("expected", pattern="^(expected|upper)$"),
                stock: float = Query(1.0, ge=0.3, le=1.0, description="available regional stock, share of demand"),
                crews: int = Query(60, ge=0, le=500, description="total spraying crews available"),
                policy: str = Query("weighted", pattern="^(weighted|strict)$"),
                min_share: float = Query(0.0, ge=0.0, le=0.5, description="lifeline: guaranteed share of need per ward"),
                ors: float = Query(0.0, ge=0.0, le=0.7, description="share of patients treated with ORS (mild cases)"),
                saline_stock: int | None = Query(None, ge=0, description="real saline bags in stock (overrides stock)"),
                kits_stock: int | None = Query(None, ge=0, description="real diagnostic kits in stock"),
                beds_stock: int | None = Query(None, ge=0, description="real hospital beds available"),
                city: str | None = None):
    fc, ctx = _frame(date, h, rain, temp, hum, mode, city)
    units = {k: v for k, v in (("saline_bags_needed", saline_stock), ("diagnostic_kits_needed", kits_stock),
                               ("hospital_beds_required", beds_stock)) if v is not None}
    return {**ctx, **allocate(fc, stock_pct=stock, crews=crews, basis=basis, policy=policy, min_share=min_share,
                              stock_units=units or None, ors_share=ors)}


@router.get("/saline")
def saline_outlook_ep(date: str | None = None, rain: float = Query(1.0, ge=0, le=3), temp: float = Query(0.0, ge=-5, le=5),
                      hum: float = Query(0.0, ge=-30, le=30), mode: str = Query("replay", pattern="^(replay|live)$"),
                      lead_days: int = Query(7, ge=0, le=60, description="supplier lead time in days"),
                      ors: float = Query(0.0, ge=0.0, le=0.7), packets: float | None = Query(None, gt=0, le=100,
                      description="ORS packets per patient, from the hospital's protocol")):
    """Surge alarm and order-by dates for IV saline, 2-4 weeks ahead, from the model's own forecast."""
    a = _api()
    out = a._forecast_cached(date or "2025-07-07", round(rain, 2), round(temp, 2), round(hum, 1), mode)
    rows = {h: (out["targets"][h - 2], [x["h"][h - 2]["pred"] or 0.0 for x in out["wards"]]) for h in (2, 3, 4)}
    return outlook(rows, out["ref_date"], str(a.ENGINE.bundle["risk"]["fit_end"]), lead_days=lead_days,
                   ors_share=ors, packets_per_patient=packets)


@router.get("/supply-backtest")
def supply_backtest_ep(stock: float = Query(0.6, ge=0.3, le=1.0), h: int = Query(3, ge=2, le=4)):
    """Replay 2024-25: how much of the same saline stock each sharing rule got to wards that needed it."""
    return supply_backtest(round(stock, 2), h)


@router.get("/surge")
def surge_ep(date: str | None = None, h: int = Query(3, ge=2, le=4), rain: float = Query(1.0, ge=0, le=3),
             temp: float = Query(0.0, ge=-5, le=5), hum: float = Query(0.0, ge=-30, le=30),
             mode: str = Query("replay", pattern="^(replay|live)$"),
             basis: str = Query("expected", pattern="^(expected|upper)$"),
             cap_scale: float = Query(1.0, ge=0.25, le=3.0, description="multiplier on baseline bed capacity"),
             max_util: float = Query(1.0, ge=0.5, le=1.0, description="highest bed occupancy a receiving ward may reach"),
             floating: str | None = Query(None, description="reserve beds per city, e.g. 'Gazipur:2,Dhaka North:1'"),
             bedding: float = Query(0.0, ge=0.0, le=0.5, description="emergency bedding: extra floor beds per ward, share of its beds"),
             city: str | None = None):
    fc, ctx = _frame(date, h, rain, temp, hum, mode, city)
    pos = _api().POS
    try:
        pools = parse_city_counts(floating, CITY_ORDER)
        out = route(fc, _capacity(), scale=cap_scale, basis=basis, positions=pos, max_util=max_util, floating=pools,
                    bedding=bedding)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**ctx, **out, "positions": {k: pos[k] for k in fc.ward_id}}


@router.get("/capacity")
def capacity_ep():
    cap = _capacity()
    return {"rule": "95th percentile of weekly hospital_beds_required per ward, 2016-2023 (train + validation years; 2024-25 test years never used), min 2 beds",
            "capacity": {k: int(v) for k, v in cap.items()}, "total": int(cap.sum())}
