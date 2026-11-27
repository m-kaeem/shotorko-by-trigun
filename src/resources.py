"""Constrained resource allocation engine (Phase 2, requirement 1).

Turns a ward forecast into supply demand (saline bags, diagnostic kits, hospital
beds) and vector-control crew demand, then shares a limited regional stock
across wards so that high-risk, high-density wards are served first.

Everything here is plain numpy: deterministic, no network, no LLM.

Supply rules
------------
The onsite dataset gives, for every ward-week, the supplies needed for the
observed case count. They are an exact function of cases:

    saline_bags_needed      = round(cases x 2.385)
    diagnostic_kits_needed  = round(cases x 1.165)
    hospital_beds_required  = round(cases x 0.298)      (round half up)

These ratios reproduce all 31,320 rows exactly (see ``fit_ratio`` and the test
suite). For *predicted* cases we use the same ratios but round up (ceil), as the
team's ward-plan contract specifies (docs/PHASE2_PLAN_CONTRACT.md).

Allocation rule (priority-weighted water filling)
------------------------------------------------
Each ward i has demand d_i and a priority weight w_i = tier weight x
(density_i / mean density). With stock S below total demand we find the level
L such that

    sum_i d_i * min(1, L * w_i) = S

so ward i receives the share min(1, L * w_i) of its demand. Wards with the
highest weight are filled to 100 % first; every other ward gets a share in
proportion to its priority, so nobody is silently zeroed unless stock is tiny.
Shares are turned into whole units with the largest-remainder method, so the
allocated total equals the stock exactly.

Officers can switch to strict triage instead ("strict"): wards are served in
full, one by one, in priority order until stock runs out.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

SUPPLY_COLS = ("saline_bags_needed", "diagnostic_kits_needed", "hospital_beds_required")
SUPPLY_RATIOS = {"saline_bags_needed": 2.385, "diagnostic_kits_needed": 1.165, "hospital_beds_required": 0.298}
TIER_WEIGHT = {"Low": 1.0, "Medium": 2.0, "High": 3.0, "Critical": 4.0}
CREWS_BY_TIER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}   # larvicide/fogging crew-weeks per ward
DENSE_WARD = 60_000           # people/km2: one extra crew for very dense wards (more breeding sites per km2)
STOCK_RANGE = (0.30, 1.00)    # interactive scarcity range required by the problem set
CRITICAL_FILL = 0.50         # a ward with any item below half of its need is a critical deficit (ward-plan contract)


def round_half_up(x):
    return np.floor(np.asarray(x, dtype=float) + 0.5).astype(int)


def supplies_for(cases, rounding: str = "ceil") -> dict:
    """Supply demand for an array of case counts.

    rounding="ceil" (default) is the planning rule from the team's ward-plan contract: round predicted
    needs *up*. rounding="round" (half up) is the organisers' rule for observed counts and reproduces the
    dataset's supply columns exactly."""
    c = np.clip(np.asarray(cases, dtype=float), 0, None)
    if rounding == "round":
        return {k: round_half_up(c * r) for k, r in SUPPLY_RATIOS.items()}
    # round(…, 6) first so float noise like 2.0000000001 doesn't become 3
    return {k: np.ceil(np.round(c * r, 6)).astype(int) for k, r in SUPPLY_RATIOS.items()}


def crews_for(tiers, density) -> np.ndarray:
    """Crews needed per ward-week: Low 0, Medium 1, High 2, Critical 3, +1 if density >= 60,000/km2 (Medium and up)."""
    base = np.asarray([CREWS_BY_TIER.get(t, 0) for t in tiers])
    dense = np.asarray(density, dtype=float) >= DENSE_WARD
    return base + ((base > 0) & dense).astype(int)


def fit_ratio(cases, needed, lo=0.05, hi=3.0, step=0.0005):
    """Find the ratio k for which round_half_up(cases * k) matches `needed` best.

    Returns (k, share of rows matched exactly). Used to derive SUPPLY_RATIOS from the data."""
    c = np.asarray(cases, dtype=float)
    y = np.asarray(needed)
    best = (None, -1.0)
    for k in np.arange(lo, hi, step):
        acc = float((round_half_up(c * k) == y).mean())
        if acc > best[1] + 1e-12:
            best = (round(float(k), 4), acc)
    # centre of the exact-match band is the most stable choice; report the first exact one
    return best


def priority_weights(tiers, density) -> np.ndarray:
    d = np.asarray(density, dtype=float)
    tw = np.asarray([TIER_WEIGHT.get(t, 1.0) for t in tiers])
    return tw * (d / d.mean())


def strict_fill(demand, weight, stock) -> np.ndarray:
    """Strict triage: serve wards fully in priority order until the stock runs out."""
    d = np.asarray(demand, dtype=int)
    alloc = np.zeros_like(d)
    left = int(max(0, stock))
    for i in np.argsort(-np.asarray(weight, dtype=float), kind="stable"):
        take = min(int(d[i]), left)
        alloc[i], left = take, left - take
        if left == 0:
            break
    return alloc


def water_fill(demand, weight, stock) -> np.ndarray:
    """Integer allocation of `stock` units across wards (see module docstring).

    Guarantees: 0 <= alloc_i <= demand_i, sum(alloc) == min(stock, sum(demand)),
    and a ward never gets a smaller share than a ward with a lower weight."""
    d = np.asarray(demand, dtype=int)
    w = np.asarray(weight, dtype=float)
    stock = int(max(0, stock))
    total = int(d.sum())
    if stock >= total:
        return d.copy()
    if stock == 0 or total == 0:
        return np.zeros_like(d)
    pos = d > 0
    share = lambda L: np.minimum(1.0, L * w)
    lo, hi = 0.0, 1.0 / w[pos].min()          # at `hi` every ward is fully served
    for _ in range(80):                       # bisection on the water level L
        mid = (lo + hi) / 2
        if (d * share(mid)).sum() > stock:
            hi = mid
        else:
            lo = mid
    exact = d * share(lo)
    alloc = np.floor(exact + 1e-9).astype(int)
    left = stock - int(alloc.sum())
    if left > 0:   # largest remainder; ties go to the higher-priority ward
        frac = np.where(alloc < d, exact - alloc, -1.0)
        order = np.lexsort((-w, -frac))
        for i in order[:left]:
            if alloc[i] < d[i]:
                alloc[i] += 1
    return np.minimum(alloc, d)


def lifeline_fill(demand, weight, stock, min_share: float, fill) -> np.ndarray:
    """Give every ward a guaranteed minimum first, then share the rest with `fill`.

    Each ward with need first gets ceil(min_share x need), so its coverage is at least min_share.
    If the stock cannot cover all those minimums, everyone gets the same share instead (equal water
    filling), which is the fairest a minimum can be. The remainder is shared by priority as usual."""
    d = np.asarray(demand, dtype=int)
    stock = int(max(0, stock))
    if min_share <= 0 or stock >= d.sum():
        return fill(d, weight, stock)
    base = np.minimum(d, np.ceil(np.round(d * min_share, 6)).astype(int))
    if base.sum() > stock:
        return water_fill(d, np.ones(len(d)), stock)
    return base + fill(d - base, weight, stock - int(base.sum()))


def _covers(demand, alloc, share=CRITICAL_FILL):
    d, a = np.asarray(demand), np.asarray(alloc)
    return bool(np.all((d == 0) | (a >= share * d - 1e-9)))


def stock_to_clear(demand, weight, fill_fn) -> int:
    """Smallest stock with no ward under CRITICAL_FILL coverage for this item (binary search; allocation
    is monotone in stock)."""
    d = np.asarray(demand, dtype=int)
    lo, hi = 0, int(d.sum())
    while lo < hi:
        mid = (lo + hi) // 2
        if _covers(d, fill_fn(d, weight, mid)):
            hi = mid
        else:
            lo = mid + 1
    return lo


def allocate(fc: pd.DataFrame, stock_pct: float = 1.0, crews: int = 60, basis: str = "expected",
             stock_units: dict | None = None, policy: str = "weighted", min_share: float = 0.0) -> dict:
    """Run the allocation for one forecast week.

    fc columns: ward_id, city, density, tier, pred, hi (80 % upper bound).
    basis: "expected" plans for the forecast mean, "upper" for the 80 % upper bound.
    stock_pct: available regional stock as a share of total demand (0.30-1.00), applied to every item.
    stock_units: optional absolute stock per item (overrides stock_pct for that item).
    policy: "weighted" (priority-weighted water filling) or "strict" (fill wards fully in priority order).
    min_share: lifeline, 0-0.5. Every ward first gets at least this share of each supply (not crews),
        then the rest is shared by `policy`. At 0.5 no ward is in critical deficit while stock allows it.
    """
    base_fill = strict_fill if policy == "strict" else water_fill
    min_share = float(min(max(min_share, 0.0), 0.5))
    fill = (lambda d, w_, s_: lifeline_fill(d, w_, s_, min_share, base_fill)) if min_share > 0 else base_fill
    stock_pct = float(min(max(stock_pct, 0.0), 1.0))   # the UI/API keep it inside STOCK_RANGE
    fc = fc.reset_index(drop=True)
    cases = fc["hi"] if basis == "upper" else fc["pred"]
    need = supplies_for(cases)
    w = priority_weights(fc["tier"], fc["density"])
    rank = (-w).argsort(kind="stable").argsort() + 1

    items, alloc = {}, {}
    for k in SUPPLY_COLS:
        total = int(need[k].sum())
        stock = int((stock_units or {}).get(k, math.floor(stock_pct * total + 1e-9)))
        a = fill(need[k], w, stock)
        alloc[k] = a
        clear = stock_to_clear(need[k], w, fill)
        items[k] = {"required": total, "stock": stock, "allocated": int(a.sum()),
                    "unmet": int(total - a.sum()), "fill": round(float(a.sum()) / total, 4) if total else 1.0,
                    "stock_pct": round(stock / total, 4) if total else 1.0,
                    # what it takes: smallest stock with no critical ward for this item, and the gap to it
                    "stock_to_clear_critical": clear, "extra_to_clear_critical": max(0, clear - stock)}

    crew_need = crews_for(fc["tier"], fc["density"])
    crew_alloc = base_fill(crew_need, w, crews)          # crews follow priority only (no lifeline)
    crews_out = {"needed": int(crew_need.sum()), "available": int(crews), "assigned": int(crew_alloc.sum()),
                 "unmet": int(crew_need.sum() - crew_alloc.sum()), "idle": int(max(0, crews - crew_need.sum()))}

    wards, warnings = [], []
    for i, r in fc.iterrows():
        fills = [alloc[k][i] / need[k][i] for k in SUPPLY_COLS if need[k][i] > 0]
        min_fill = min(fills) if fills else 1.0
        short = {k: int(need[k][i] - alloc[k][i]) for k in SUPPLY_COLS if alloc[k][i] < need[k][i]}
        crew_short = int(crew_need[i] - crew_alloc[i])
        if short:
            level = "critical" if min_fill < CRITICAL_FILL else "deficit"
        else:
            level = "ok"
        row = {"ward": r.ward_id, "city": r.city, "tier": r.tier, "density": int(r.density),
               "cases": round(float(cases.iloc[i]), 2), "priority": round(float(w[i]), 3), "rank": int(rank[i]),
               "status": level, "min_fill": round(float(min_fill), 3),
               "crews_needed": int(crew_need[i]), "crews_assigned": int(crew_alloc[i]), "crew_shortfall": crew_short}
        for k in SUPPLY_COLS:
            row[k] = {"required": int(need[k][i]), "allocated": int(alloc[k][i])}
        wards.append(row)
        if short:
            warnings.append({"ward": r.ward_id, "city": r.city, "tier": r.tier, "level": level,
                             "shortfall": short, "min_fill": round(float(min_fill), 3)})
    wards.sort(key=lambda x: x["rank"])
    warnings.sort(key=lambda x: (x["level"] != "critical", x["min_fill"]))
    pct_to_clear = max((v["stock_to_clear_critical"] / v["required"] for v in items.values() if v["required"]), default=0.0)
    fix = {"extra": {k: v["extra_to_clear_critical"] for k, v in items.items()},
           "stock_pct": min(1.0, math.ceil(pct_to_clear * 100 - 1e-9) / 100),
           "needed": any(v["extra_to_clear_critical"] for v in items.values())}
    return {"basis": basis, "policy": policy, "min_share": min_share, "stock_pct": stock_pct, "fix": fix,
            "items": items, "crews": crews_out, "wards": wards,
            "warnings": warnings, "ratios": SUPPLY_RATIOS, "tier_weight": TIER_WEIGHT,
            "crews_by_tier": CREWS_BY_TIER, "dense_ward": DENSE_WARD,
            "n_deficit": len(warnings), "n_critical": sum(x["level"] == "critical" for x in warnings)}
