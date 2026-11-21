"""Graceful degradation for incomplete data.

Real field data is often partial: a city may send only rainfall and temperature,
a new area has no case history, or a feed starts only a few weeks ago. LightGBM
handles missing values natively, but only if it has *seen* missing values while
training. So the robust model is trained on the normal rows plus copies in which
whole feature groups are randomly blanked out and long-history features are cut
off ("feature-group dropout").

The same masks are used to *evaluate* every completeness scenario on the
untouched test years (see SCENARIOS), so the numbers shown in the app are
measured, not assumed.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .features import feature_group

# feature groups a user's data can be missing (these map 1:1 to input columns)
DROP_GROUPS = {
    "humidity": lambda c: c.startswith("hum_"),
    "temperature": lambda c: c.startswith(("temp_", "tmax_", "tmin_", "dtr_")),
    "temp_minmax": lambda c: c.startswith(("tmax_", "tmin_", "dtr_")),
    "rainfall": lambda c: c.startswith(("rain_", "heavy_rain")) or c in ("waterlog_x_rain4w", "sanit_gap_x_rain4w"),
    "cases": lambda c: c.startswith("cases_"),
    "ward_profile": lambda c: c in ("population_density_sqkm", "water_logging_risk_index", "sanitation_score",
                                     "waterlog_x_rain4w", "sanit_gap_x_rain4w", "city_code"),
}


def history_needed(col: str) -> int:
    """Weeks of history (including week t) a feature needs before it is defined."""
    m = re.search(r"_lag(\d+)$", col)
    if m:
        return int(m.group(1)) + 1
    m = re.search(r"_sum_(\d+)w$", col)
    if m:
        return int(m.group(1))
    if col == "rain_sum_lag1_3":
        return 4
    if col.endswith("_4w") or col in ("waterlog_x_rain4w", "sanit_gap_x_rain4w"):
        return 4
    if col == "cases_trend_2w":
        return 3
    if col == "cases_same_week_last_year":
        return 53
    return 1


def apply_mask(X: pd.DataFrame, drop=(), history_weeks: int | None = None) -> pd.DataFrame:
    X = X.copy()
    cols = [c for c in X.columns if any(DROP_GROUPS[g](c) for g in drop)]
    if history_weeks is not None:
        cols += [c for c in X.columns if history_needed(c) > history_weeks]
    X.loc[:, sorted(set(cols))] = np.nan
    return X


def augment(X: pd.DataFrame, y: pd.Series, copies: int = 3, seed: int = 7):
    """Original rows + `copies` randomly degraded copies (row-wise random group dropout + history cuts)."""
    rng = np.random.default_rng(seed)
    parts_X, parts_y = [X], [y]
    group_cols = {g: [c for c in X.columns if f(c)] for g, f in DROP_GROUPS.items()}
    hist_need = np.array([history_needed(c) for c in X.columns])
    probs = {"humidity": 0.35, "temperature": 0.25, "temp_minmax": 0.25, "rainfall": 0.10, "cases": 0.35, "ward_profile": 0.30}
    for _ in range(copies):
        A = X.to_numpy(dtype=float, copy=True)
        n = len(A)
        for g, p in probs.items():
            rows = rng.random(n) < p
            idx = [X.columns.get_loc(c) for c in group_cols[g]]
            if rows.any() and idx:
                A[np.ix_(rows, idx)] = np.nan
        # history truncation: some rows only have 1-12 weeks of past data
        cut = rng.random(n) < 0.35
        L = rng.choice([1, 2, 3, 4, 6, 8, 12], size=n)
        mask = cut[:, None] & (hist_need[None, :] > L[:, None])
        A[mask] = np.nan
        parts_X.append(pd.DataFrame(A, columns=X.columns, index=X.index))
        parts_y.append(y)
    return pd.concat(parts_X, ignore_index=True), pd.concat(parts_y, ignore_index=True)


# completeness scenarios evaluated on the test years: (key, label, drop groups, history weeks)
SCENARIOS = [
    ("full", "Complete data (all columns, 1+ year of history)", (), None),
    ("no_humidity", "No humidity column", ("humidity",), None),
    ("no_minmax", "Only average temperature (no max/min)", ("temp_minmax",), None),
    ("no_temperature", "No temperature columns", ("temperature",), None),
    ("no_cases", "No case history (weather + ward profile only)", ("cases",), None),
    ("short_12w", "Only the last 12 weeks of data", (), 12),
    ("short_4w", "Only the last 4 weeks of data", (), 4),
    ("rain_temp_8w", "Rain + avg temperature, 8 weeks, no cases", ("humidity", "temp_minmax", "cases"), 8),
    ("new_area", "New area: rain + temperature, 8 weeks, no cases, no ward profile",
     ("humidity", "temp_minmax", "cases", "ward_profile"), 8),
    ("rain_only_4w", "Rainfall only, 4 weeks", ("humidity", "temperature", "cases", "ward_profile"), 4),
]


def groups_present(cols_available: set[str]) -> dict:
    """Which input groups a user's upload contains (for display)."""
    return {
        "rainfall": "rainfall_mm" in cols_available,
        "avg_temperature": "avg_temp_c" in cols_available,
        "max_min_temperature": {"max_temp_c", "min_temp_c"} <= cols_available,
        "humidity": "relative_humidity_pct" in cols_available,
        "case_history": "dengue_cases" in cols_available,
        "ward_profile": {"population_density_sqkm", "water_logging_risk_index", "sanitation_score"} <= cols_available,
    }


__all__ = ["DROP_GROUPS", "SCENARIOS", "apply_mask", "augment", "history_needed", "groups_present", "feature_group"]
