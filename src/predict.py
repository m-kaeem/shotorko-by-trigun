"""Inference engine: loads the serialized bundle once and serves ward-level
forecasts, risk tiers, intervals and driver attributions.

Driver attributions use LightGBM's built-in TreeSHAP (``pred_contrib=True``),
so no extra SHAP dependency or remote API is needed. Because the model works on
a log scale (Tweedie/Poisson), contributions are additive in log space and are
reported as multiplicative effects on the expected case count.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd

from . import config as C
from .features import (add_horizon_features, build_base_features, feature_group, load_raw,
                       pretty_feature)
from .risk import channel_lookup, predict_tiers


@dataclass
class Scenario:
    """What-if adjustments applied to the last `weeks` of observed weather up to the reference week."""
    rain_multiplier: float = 1.0
    temp_delta_c: float = 0.0
    humidity_delta_pct: float = 0.0
    weeks: int = 4

    @property
    def active(self) -> bool:
        return self.rain_multiplier != 1.0 or self.temp_delta_c != 0.0 or self.humidity_delta_pct != 0.0


@dataclass
class Engine:
    bundle: dict = field(default_factory=lambda: joblib.load(C.MODEL_BUNDLE))
    raw: pd.DataFrame = field(default_factory=load_raw)

    def __post_init__(self):
        self.base = build_base_features(self.raw, self.bundle["thresholds"])
        self.frames = {h: add_horizon_features(self.base, h) for h in C.HORIZONS}
        self.wards = self.raw[["city", "ward_id", "population_density_sqkm", "water_logging_risk_index",
                               "sanitation_score"]].drop_duplicates("ward_id").reset_index(drop=True)
        self.dates = np.sort(self.raw["date"].unique())
        self.last_latency_ms = 0.0

    # ------------------------------------------------------------------ helpers
    def _rows(self, h: int, date, ward_ids=None, scenario: Scenario | None = None, raw_override=None) -> pd.DataFrame:
        date = pd.Timestamp(date)
        if raw_override is not None:
            fr = add_horizon_features(build_base_features(raw_override, self.bundle["thresholds"]), h)
        elif scenario is not None and scenario.active:
            fr = add_horizon_features(self._scenario_base(date, ward_ids, scenario), h)
        else:
            fr = self.frames[h]
        rows = fr[fr["date"] == date]
        if ward_ids is not None:
            rows = rows[rows["ward_id"].isin(list(ward_ids))]
        return rows

    @staticmethod
    def apply_scenario(raw: pd.DataFrame, date, sc: Scenario) -> pd.DataFrame:
        """Return a copy of `raw` with the last `sc.weeks` weeks of weather (up to `date`) adjusted."""
        date = pd.Timestamp(date)
        raw = raw.copy()
        win = (raw["date"] <= date) & (raw["date"] > date - pd.Timedelta(weeks=sc.weeks))
        raw.loc[win, "rainfall_mm"] *= sc.rain_multiplier
        for col in ("avg_temp_c", "max_temp_c", "min_temp_c"):
            raw.loc[win, col] += sc.temp_delta_c
        raw.loc[win, "relative_humidity_pct"] = (raw.loc[win, "relative_humidity_pct"] + sc.humidity_delta_pct).clip(0, 100)
        return raw

    def _scenario_base(self, date, ward_ids, sc: Scenario) -> pd.DataFrame:
        raw = self.raw if ward_ids is None else self.raw[self.raw.ward_id.isin(list(ward_ids))]
        # only the window the features need: 52-week seasonal lag + 12-week lags, plus 4 weeks of targets
        raw = raw[(raw["date"] > date - pd.Timedelta(weeks=60)) & (raw["date"] <= date + pd.Timedelta(weeks=5))]
        return build_base_features(self.apply_scenario(raw, date, sc), self.bundle["thresholds"])

    # ------------------------------------------------------------------ public API
    def forecast(self, date, ward_ids=None, scenario: Scenario | None = None, raw_override=None) -> pd.DataFrame:
        """Forecast all horizons for the given reference week; one row per ward x horizon."""
        t0 = time.perf_counter()
        out = []
        for h in C.HORIZONS:
            rows = self._rows(h, date, ward_ids, scenario, raw_override)
            if rows.empty:
                continue
            cols = self.bundle["feature_cols"][h]
            X = rows[cols]
            m = self.bundle["models"][h]
            mu = m["point"].predict(X).clip(0)
            margin = m.get("cqr_margin", 0.0)
            lo = (np.minimum(m["q10"].predict(X), mu) - margin).clip(0)
            hi = np.maximum(m["q90"].predict(X), mu) + margin
            tiers = predict_tiers(self.bundle["risk"], mu, rows["ward_id"], rows["target_date"], h)
            out.append(pd.DataFrame({
                "ward_id": rows["ward_id"].values, "city": rows["city"].values, "horizon": h,
                "ref_date": rows["date"].values, "target_date": rows["target_date"].values,
                "pred_cases": mu, "lo80": lo, "hi80": hi, "actual": rows["target"].values,
                "seasonal_normal": tiers["mean"].values, "alert_threshold": tiers["T_high"].values,
                "risk_score": tiers["risk_score"].values, "tier": tiers["tier"].values,
                "tier_idx": tiers["tier_idx"].values}))
        self.last_latency_ms = (time.perf_counter() - t0) * 1000
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def explain(self, ward_id: str, date, h: int, scenario: Scenario | None = None, raw_override=None):
        """Return (grouped drivers, top features) for one ward/week/horizon."""
        rows = self._rows(h, date, [ward_id], scenario, raw_override)
        if rows.empty:
            return pd.DataFrame(), pd.DataFrame()
        cols = self.bundle["feature_cols"][h]
        contrib = self.bundle["models"][h]["point"].predict(rows[cols], pred_contrib=True)[0]
        phi, bias = contrib[:-1], contrib[-1]
        feats = pd.DataFrame({"feature": cols, "value": rows[cols].iloc[0].values, "log_effect": phi})
        feats["group"] = feats["feature"].map(feature_group)
        feats["label"] = [pretty_feature(f, h) for f in feats["feature"]]
        feats["multiplier"] = np.exp(feats["log_effect"])
        grouped = (feats.groupby("group", as_index=False)["log_effect"].sum()
                   .assign(multiplier=lambda d: np.exp(d["log_effect"]))
                   .sort_values("log_effect", key=np.abs, ascending=False))
        grouped.attrs["baseline_cases"] = float(np.exp(bias))
        top = feats.reindex(feats["log_effect"].abs().sort_values(ascending=False).index).head(10)
        return grouped, top

    def history(self, ward_id: str, end, weeks: int = 30) -> pd.DataFrame:
        end = pd.Timestamp(end)
        g = self.raw[(self.raw.ward_id == ward_id) & (self.raw.date <= end + pd.Timedelta(weeks=4))
                     & (self.raw.date > end - pd.Timedelta(weeks=weeks))].copy()
        ch = channel_lookup(self.bundle["risk"], g["ward_id"], g["date"])
        g["seasonal_normal"], g["alert_threshold"] = ch["mean"].values, ch["T_high"].values
        return g

    def climatology(self, ward_id: str, date, weeks: int = 4) -> dict:
        """Training-period normal for the same weeks of year (used for plain-language explanations)."""
        date = pd.Timestamp(date)
        woys = {(date - pd.Timedelta(weeks=k)).isocalendar().week for k in range(weeks)}
        tr = self.raw[(self.raw.ward_id == ward_id) & (self.raw.date <= self.bundle["risk"]["fit_end"])]
        tr = tr[tr.date.dt.isocalendar().week.isin(woys)]
        return {"rain_week_mean": float(tr.rainfall_mm.mean()), "temp_mean": float(tr.avg_temp_c.mean()),
                "hum_mean": float(tr.relative_humidity_pct.mean())}


def narrative(engine: Engine, ward_id: str, date, fc_row: pd.Series, grouped: pd.DataFrame,
              scenario: Scenario | None = None) -> str:
    """Template-based plain-language summary (deterministic, zero LLM tokens)."""
    date = pd.Timestamp(date)
    hist = engine.history(ward_id, date, weeks=4)
    hist = hist[hist.date <= date]
    if scenario is not None and scenario.active:
        hist = hist.assign(rainfall_mm=hist.rainfall_mm * scenario.rain_multiplier,
                           avg_temp_c=hist.avg_temp_c + scenario.temp_delta_c)
    clim = engine.climatology(ward_id, date)
    rain4 = hist.rainfall_mm.sum()
    ratio = rain4 / max(4 * clim["rain_week_mean"], 1e-6)
    t4 = hist.avg_temp_c.mean()
    parts = [f"**{fc_row.tier} risk** for {ward_id} in the week of {pd.Timestamp(fc_row.target_date):%d %b %Y} "
             f"({fc_row.horizon} weeks ahead): about **{fc_row.pred_cases:.1f} cases** expected "
             f"(80% range {fc_row.lo80:.1f}-{fc_row.hi80:.1f}) vs a seasonal normal of {fc_row.seasonal_normal:.1f}."]
    parts.append(f"Rainfall over the last 4 weeks was {rain4:.0f} mm, **{ratio:.1f}x** the usual for this time of year; "
                 f"average temperature {t4:.1f} °C (Aedes transmission optimum ≈29 °C).")
    if not grouped.empty:
        top = grouped.iloc[0]
        direction = "raising" if top.log_effect > 0 else "lowering"
        parts.append(f"Biggest driver: **{top.group}**, {direction} expected cases by x{top.multiplier:.2f}.")
    if fc_row.tier_idx >= 2:
        parts.append("Suggested action: schedule larval source reduction and drain clearing in this ward now, "
                     "before the forecast week.")
    return " ".join(parts)
