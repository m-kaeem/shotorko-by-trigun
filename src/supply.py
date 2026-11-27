"""Per-case supply ratios learned from the Phase 2 dataset.

The organisers' Phase 2 sheet adds three columns to every ward-week: saline_bags_needed,
diagnostic_kits_needed and hospital_beds_required. They scale with the case count, so we learn
one ratio per ward (supply / case), falling back to the city ratio and then the overall ratio
for wards with too few cases. Ratios come from the training + validation years only.
"""
from __future__ import annotations

import math
from functools import lru_cache

import pandas as pd

from . import config as C

ITEMS = {"saline_bags": "saline_bags_needed", "diagnostic_kits": "diagnostic_kits_needed",
         "hospital_beds": "hospital_beds_required"}
MIN_CASES = 30   # a ward needs at least this many cases in the fit years to get its own ratio


@lru_cache(maxsize=1)
def ratios() -> dict:
    """{ward_id: {item: supply per case}} plus "_source" with how each ward's ratio was found."""
    cols = ["date", "city", "ward_id", "dengue_cases", *ITEMS.values()]
    df = pd.read_csv(C.DATA_PATH, usecols=lambda c: c in cols, parse_dates=["date"])
    missing = [c for c in ITEMS.values() if c not in df.columns]
    if missing:
        raise ValueError(f"Phase 2 supply columns missing from the dataset: {missing}")
    df = df[df.date <= C.VAL_END]
    tot = df[["dengue_cases", *ITEMS.values()]].sum()
    overall = {k: float(tot[v] / max(tot.dengue_cases, 1)) for k, v in ITEMS.items()}
    city = {c: g[["dengue_cases", *ITEMS.values()]].sum() for c, g in df.groupby("city")}
    out = {}
    for (wid, cty), g in df.groupby(["ward_id", "city"]):
        s = g[["dengue_cases", *ITEMS.values()]].sum()
        if s.dengue_cases >= MIN_CASES:
            out[wid] = {k: float(s[v] / s.dengue_cases) for k, v in ITEMS.items()}
        elif city[cty].dengue_cases >= MIN_CASES:
            cs = city[cty]
            out[wid] = {k: float(cs[v] / cs.dengue_cases) for k, v in ITEMS.items()}
        else:
            out[wid] = dict(overall)
    out["_overall"] = overall
    return out


def required(ward_id: str, cases: float) -> dict:
    """Supplies needed for an expected case count, rounded up to whole units."""
    r = ratios().get(ward_id, ratios()["_overall"])
    c = max(0.0, float(cases or 0))
    return {k: int(math.ceil(c * r[k] - 1e-9)) for k in ITEMS}
