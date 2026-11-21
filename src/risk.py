"""Risk tiers: WHO/TDR EWARS-style endemic channel + outbreak probability.

1. Endemic channel (learned from training years only): for every ward and ISO
   week, the seasonal normal (mean) and spread (SD) of weekly cases, smoothed
   over +/-1 week.
2. Tier thresholds per ward-week (cases), each capped by an absolute burden level
   so that a ward with a very high count is flagged even in peak season:
       T_medium   = min(max(mean + 0.5 SD, 1), P75)
       T_high     = min(max(mean + 1.25 SD, 2), P90)   # EWARS endemic-channel upper limit, z = 1.25
       T_critical = min(max(mean + 2.0 SD, 4), P97)
   (P75/P90/P97 = percentiles of all training ward-weeks.)
3. The model forecasts the expected count mu for week t+h. Treating weekly
   counts as Negative Binomial(mu, r) (r estimated from validation residuals),
   we compute exceedance probabilities P(Y > T_k).
4. Tier = highest k with P(Y > T_k) >= p_alarm. p_alarm is calibrated on the
   validation years (maximising F2 for High+ alerts: missing an outbreak costs
   more than a false alarm), like EWARS' locally calibrated alarm threshold.
Risk score (0-100) = 100 * P(Y > T_high): the chance this ward-week breaks
above its normal seasonal ceiling.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import nbinom

from . import config as C

Z_BANDS = {"medium": 0.5, "high": 1.25, "critical": 2.0}
FLOORS = {"medium": 1.0, "high": 2.0, "critical": 4.0}
ALERT_BETA = 1.0   # F-beta used to calibrate the alarm (1.0 = balanced precision/recall)


def fit_endemic_channel(raw: pd.DataFrame, end: str) -> dict:
    tr = raw[raw.date <= end].copy()
    tr["woy"] = tr.date.dt.isocalendar().week.clip(upper=52).astype(int)
    rows = []
    for ward, g in tr.groupby("ward_id"):
        for w in range(1, 53):
            near = [(w - 2) % 52 + 1, w, w % 52 + 1]   # w-1, w, w+1 (cyclic)
            v = g.loc[g.woy.isin(near), "dengue_cases"]
            rows.append((ward, w, v.mean(), v.std(ddof=1)))
    ch = pd.DataFrame(rows, columns=["ward_id", "woy", "mean", "sd"]).fillna(0.0)
    ch["sd"] = ch["sd"].clip(lower=0.5)
    q = tr["dengue_cases"].quantile([0.75, 0.90, 0.97]).tolist()
    return {"channel": ch, "abs_caps": {"medium": q[0], "high": q[1], "critical": q[2]},
            "nb_r": 5.0, "p_alarm": {h: 0.3 for h in C.HORIZONS}, "fit_end": end}


def channel_lookup(risk_model: dict, ward_ids, dates) -> pd.DataFrame:
    woy = pd.to_datetime(pd.Series(list(dates))).dt.isocalendar().week.clip(upper=52).astype(int).values
    key = pd.DataFrame({"ward_id": list(ward_ids), "woy": woy})
    out = key.merge(risk_model["channel"], on=["ward_id", "woy"], how="left")
    if out["mean"].isna().any():   # unknown ward (e.g. an uploaded area): use the all-ward seasonal channel
        glob = risk_model["channel"].groupby("woy")[["mean", "sd"]].mean()
        miss = out["mean"].isna()
        out.loc[miss, "mean"] = out.loc[miss, "woy"].map(glob["mean"]).values
        out.loc[miss, "sd"] = out.loc[miss, "woy"].map(glob["sd"]).values
    out = out.fillna({"mean": 0.0, "sd": 0.5})
    for k in Z_BANDS:
        out[f"T_{k}"] = np.minimum(np.maximum(out["mean"] + Z_BANDS[k] * out["sd"], FLOORS[k]),
                                   risk_model["abs_caps"][k])
    return out


def exceed_prob(mu, thr, r) -> np.ndarray:
    mu = np.clip(np.asarray(mu, float), 1e-6, None)
    p = r / (r + mu)
    return nbinom.sf(np.floor(np.asarray(thr, float)), r, p)


def tier_from_probs(probs: dict, p_alarm: float) -> np.ndarray:
    tier = np.zeros(len(probs["medium"]), dtype=int)
    for i, k in enumerate(["medium", "high", "critical"], start=1):
        tier = np.where(probs[k] >= p_alarm, i, tier)
    return tier


def predict_tiers(risk_model: dict, mu, ward_ids, target_dates, h: int) -> pd.DataFrame:
    ch = channel_lookup(risk_model, ward_ids, target_dates)
    r = risk_model.get("nb_r_by_h", {}).get(h, risk_model["nb_r"])
    probs = {k: exceed_prob(mu, ch[f"T_{k}"], r) for k in Z_BANDS}
    ch["p_exceed_high"] = probs["high"]
    ch["risk_score"] = np.round(100 * probs["high"], 1)
    ch["tier_idx"] = tier_from_probs(probs, risk_model["p_alarm"][h])
    ch["tier"] = [C.TIERS[i] for i in ch["tier_idx"]]
    return ch


def observed_tiers(risk_model: dict, actual_smoothed, ward_ids, target_dates) -> np.ndarray:
    """Ground-truth tier from the (3-week centred) observed count, EWARS 'sustained excess'."""
    ch = channel_lookup(risk_model, ward_ids, target_dates)
    a = np.asarray(actual_smoothed, float)
    return np.select([a > ch.T_critical, a > ch.T_high, a > ch.T_medium], [3, 2, 1], 0)


def estimate_nb_r(y, mu) -> float:
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    inv_r = np.mean((y - mu) ** 2 - mu) / max(np.mean(mu ** 2), 1e-9)
    return float(np.clip(1 / inv_r, 0.5, 100)) if inv_r > 0 else 100.0


def calibrate_p_alarm(risk_model, mu, truth_tier, ward_ids, target_dates, h, beta=ALERT_BETA) -> tuple[float, float]:
    """Pick the probability cut that maximises F-beta of High+ alerts on validation data."""
    best = (0.3, -1.0)
    truth = np.asarray(truth_tier) >= 2
    for p in np.arange(0.05, 0.75, 0.025):
        rm = {**risk_model, "p_alarm": {h: p}}
        pred = predict_tiers(rm, mu, ward_ids, target_dates, h)["tier_idx"].values >= 2
        tp = np.sum(pred & truth)
        prec = tp / max(pred.sum(), 1)
        rec = tp / max(truth.sum(), 1)
        f2 = (1 + beta**2) * prec * rec / max(beta**2 * prec + rec, 1e-9)
        if f2 > best[1]:
            best = (float(round(p, 3)), float(f2))
    return best
