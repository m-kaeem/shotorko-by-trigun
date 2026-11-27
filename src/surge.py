"""Hospital surge detection and cross-ward overflow routing (Phase 2, requirement 2).

For one forecast week:

1. Projected bed demand per ward = ceil(cases x 0.298) (the onsite ratio, rounded up as in the ward-plan contract).
2. Local dengue bed capacity per ward = the ward's 95th-percentile weekly bed
   need in 2016-2023 (train + validation, the years the deployed model and risk
   tiers are fitted on; test years 2024-25 never used), at least 2 beds, times an officer-set
   capacity scale. The dataset has no bed-count column, so capacity is the load
   a ward's facilities normally absorb in a busy-but-normal week; it can be
   overridden with real counts.
3. Wards whose demand exceeds capacity send their overflow, one patient at a
   time, to the ward *in the same city* that would have the lowest bed
   utilisation after receiving that patient (nearest ward breaks ties). Patients
   never cross city pools.
4. Emergency bedding: up to a set share of a ward's beds can be added as mattresses or cots
   between beds and in corridors, for that ward's own patients, after transfers.
5. Officers can give each city a few *floating* (reserve) beds that belong to no
   ward. They are used only after transfers, for patients no existing bed in the
   city can take, and open at the ward where those patients are.
6. Overflow left after that is reported as unresolved and flagged for
   escalation (more field beds or inter-city referral).

Deterministic, pure numpy/pandas, no network.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from . import config as C
from .resources import SUPPLY_RATIOS, supplies_for

CAPACITY_QUANTILE = 0.95
MIN_CAPACITY = 2
CITY_ORDER = ["Dhaka North", "Dhaka South", "Chattogram", "Gazipur", "Narayanganj"]


def baseline_capacity(raw: pd.DataFrame, fit_end: str) -> pd.Series:
    """Ward -> local dengue bed capacity, learned from training years only (no test leakage)."""
    tr = raw[raw["date"] <= pd.Timestamp(fit_end)]
    if "hospital_beds_required" in tr.columns:
        beds = tr["hospital_beds_required"]
    else:   # older CSV without supply columns: derive beds with the same rule
        beds = pd.Series(supplies_for(tr["dengue_cases"], rounding="round")["hospital_beds_required"], index=tr.index)
    q = beds.groupby(tr["ward_id"]).quantile(CAPACITY_QUANTILE)
    return np.ceil(q).clip(lower=MIN_CAPACITY).astype(int)


@lru_cache(maxsize=4)
def load_capacity(fit_end: str) -> pd.Series:
    """Baseline capacity from the dataset file (reads the bed column directly; the engine drops it)."""
    head = pd.read_csv(C.DATA_PATH, nrows=0).columns
    cols = ["date", "ward_id", "dengue_cases"] + (["hospital_beds_required"] if "hospital_beds_required" in head else [])
    return baseline_capacity(pd.read_csv(C.DATA_PATH, usecols=cols, parse_dates=["date"]), str(fit_end))


def _km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    y = math.radians(la2 - la1)
    return 6371.0 * math.hypot(x, y)


def floating_pools(floating, cities) -> dict:
    """Normalise the floating-bed setting to {city: beds}. Accepts None, one int for every city, or a dict."""
    if floating is None:
        return dict.fromkeys(cities, 0)
    if isinstance(floating, (int, np.integer)):
        return dict.fromkeys(cities, max(0, int(floating)))
    unknown = set(floating) - set(CITY_ORDER) - set(cities)
    if unknown:
        raise ValueError(f"unknown city in floating beds: {sorted(unknown)}")
    out = dict.fromkeys(cities, 0)
    for c, n in floating.items():
        if int(n) < 0 or int(n) > 500:
            raise ValueError("floating beds must be between 0 and 500")
        if c in out:          # a city outside the current view is simply not used
            out[c] = int(n)
    return out


def parse_city_counts(text: str | None, cities) -> dict:
    """'Gazipur:2,Dhaka North:1' -> {'Gazipur': 2, 'Dhaka North': 1} (used by the API query strings)."""
    if not text:
        return {}
    out = {}
    for part in text.split(","):
        if not part.strip():
            continue
        name, _, n = part.rpartition(":")
        try:
            out[name.strip()] = int(n)
        except ValueError:
            raise ValueError(f"bad floating-bed entry {part!r}; use City:number")
    floating_pools(out, cities)          # validates names and counts
    return out


def route(fc: pd.DataFrame, capacity: pd.Series, scale: float = 1.0, basis: str = "expected",
          positions: dict | None = None, max_util: float = 1.0, floating=None, bedding: float = 0.0) -> dict:
    """fc columns: ward_id, city, tier, pred, hi.  Returns wards, transfers and city summaries.

    floating: reserve ("floating") beds per city that are not tied to a ward (field cots, a mobile
    team). They are used only after transfers, for patients the city's existing beds can't take,
    and are opened at the ward where those patients are.

    bedding: emergency bedding, 0-0.5. In a surge, hospitals put mattresses or cots in the gaps
    between beds and in corridors. Each ward can take up to round(capacity x bedding) extra patients
    this way, for its own patients only. Used after transfers (a real bed elsewhere is better than a
    floor bed) and before the city's reserve beds."""
    bedding = float(min(max(bedding, 0.0), 0.5))
    fc = fc.reset_index(drop=True).copy()
    cases = fc["hi"] if basis == "upper" else fc["pred"]
    fc["required"] = supplies_for(cases)["hospital_beds_required"]
    fc["capacity"] = np.floor(fc["ward_id"].map(capacity).fillna(MIN_CAPACITY).to_numpy() * scale + 1e-9).astype(int)
    fc["usable"] = np.floor(fc["capacity"] * max_util + 1e-9).astype(int)
    fc["local"] = np.minimum(fc["required"], fc["usable"])
    fc["overflow"] = fc["required"] - fc["local"]
    positions = positions or {}

    occ = dict(zip(fc.ward_id, fc.local))
    usable = dict(zip(fc.ward_id, fc.usable))
    sent = dict.fromkeys(fc.ward_id, 0)
    recv = dict.fromkeys(fc.ward_id, 0)
    flows: dict[tuple, int] = {}
    unresolved = dict.fromkeys(fc.ward_id, 0)
    floated = dict.fromkeys(fc.ward_id, 0)
    bedded = dict.fromkeys(fc.ward_id, 0)
    bed_cap = {w: int(v) for w, v in zip(fc.ward_id, np.floor(fc["capacity"].to_numpy() * bedding + 0.5))}
    pools = floating_pools(floating, list(dict.fromkeys(fc.city)))
    pool_used = dict.fromkeys(pools, 0)

    for city, g in fc.groupby("city", sort=False):
        ids = g.ward_id.tolist()
        # biggest overflow first; ties go to the riskier ward
        senders = g[g.overflow > 0].copy()
        senders["tw"] = senders["tier"].map({"Low": 0, "Medium": 1, "High": 2, "Critical": 3}).fillna(0)
        senders = senders.sort_values(["overflow", "tw"], ascending=False)
        for s in senders.itertuples():
            for _ in range(int(s.overflow)):
                best, best_key = None, None
                for j in ids:
                    if j == s.ward_id or occ[j] >= usable[j]:
                        continue
                    if fc.loc[fc.ward_id == j, "overflow"].iloc[0] > 0:
                        continue   # a ward that is itself overflowing never receives
                    util_after = (occ[j] + 1) / max(usable[j], 1)
                    d = _km(positions[s.ward_id], positions[j]) if s.ward_id in positions and j in positions else 0.0
                    key = (round(util_after, 6), d)
                    if best_key is None or key < best_key:
                        best, best_key = j, key
                if best is None:
                    unresolved[s.ward_id] += 1
                    continue
                occ[best] += 1
                recv[best] += 1
                sent[s.ward_id] += 1
                flows[(s.ward_id, best)] = flows.get((s.ward_id, best), 0) + 1
        # then emergency bedding inside the patient's own ward (mattresses between beds, corridors)
        for s in senders.itertuples():
            take = min(unresolved[s.ward_id], bed_cap[s.ward_id])
            if take:
                bedded[s.ward_id] += take
                unresolved[s.ward_id] -= take
        # last resort before escalation: open the city's floating beds where the unplaced patients are
        left = pools[city]
        for s in senders.itertuples():
            take = min(unresolved[s.ward_id], left)
            if take:
                floated[s.ward_id] += take
                unresolved[s.ward_id] -= take
                left -= take
        pool_used[city] = pools[city] - left

    wards = []
    for r in fc.itertuples():
        occupied = occ[r.ward_id]
        status = ("unresolved" if unresolved[r.ward_id] else "crowded" if bedded[r.ward_id]
                  else "floating" if floated[r.ward_id]
                  else "surge" if r.overflow > 0 else "receiving" if recv[r.ward_id] else "ok")
        wards.append({"ward": r.ward_id, "city": r.city, "tier": r.tier, "cases": round(float(cases.iloc[r.Index]), 2),
                      "required": int(r.required), "capacity": int(r.capacity),
                      "net": int(r.capacity - r.required),           # + spare beds, - local deficit
                      "local": int(r.local), "sent": sent[r.ward_id], "received": recv[r.ward_id],
                      "floating": floated[r.ward_id],                # reserve beds opened here
                      "emergency": bedded[r.ward_id],                # patients on emergency floor beds here
                      "emergency_cap": bed_cap[r.ward_id],
                      "unresolved": unresolved[r.ward_id], "occupied": int(occupied),
                      "util_before": round(r.required / r.capacity, 3) if r.capacity else None,
                      "util_after": round(occupied / r.capacity, 3) if r.capacity else None,
                      "status": status})

    transfers = [{"from": a, "to": b, "patients": n, "city": fc.loc[fc.ward_id == a, "city"].iloc[0],
                  "km": round(_km(positions[a], positions[b]), 1) if a in positions and b in positions else None}
                 for (a, b), n in sorted(flows.items(), key=lambda kv: -kv[1])]

    cities = []
    wdf = pd.DataFrame(wards)
    present = list(dict.fromkeys(wdf.city))
    for city in [c for c in CITY_ORDER if c in present] + [c for c in present if c not in CITY_ORDER]:
        g = wdf[wdf.city == city]
        cap, req, occd, unr = int(g.capacity.sum()), int(g.required.sum()), int(g.occupied.sum()), int(g.unresolved.sum())
        cities.append({"city": city, "capacity": cap, "required": req,
                       "util_before": round(req / cap, 3) if cap else None,
                       "util_after": round(occd / cap, 3) if cap else None,
                       "headroom": cap - occd, "net": cap - req,
                       "surge_wards": int((g.required > g.capacity).sum()),
                       "transferred": int(g.sent.sum()), "unresolved": unr,
                       "floating_pool": pools[city], "floating_used": pool_used[city],
                       "emergency_used": int(g.emergency.sum()),
                       "status": "escalate" if unr else ("strained" if req > 0.85 * cap else "ok")})

    tot_cap, tot_req = int(wdf.capacity.sum()), int(wdf.required.sum())
    return {"basis": basis, "scale": scale, "max_util": max_util, "bedding": bedding, "wards": wards, "transfers": transfers,
            "cities": cities,
            "totals": {"capacity": tot_cap, "required": tot_req, "transferred": int(wdf.sent.sum()),
                       "unresolved": int(wdf.unresolved.sum()), "surge_wards": int((wdf.required > wdf.capacity).sum()),
                       "floating_pool": sum(pools.values()), "floating_used": sum(pool_used.values()),
                       "emergency_used": int(wdf.emergency.sum()),
                       "util_before": round(tot_req / tot_cap, 3) if tot_cap else None},
            "beds_per_case": SUPPLY_RATIOS["hospital_beds_required"],
            "capacity_rule": f"p{int(CAPACITY_QUANTILE * 100)} of weekly beds needed, 2016-2023, min {MIN_CAPACITY}"}
