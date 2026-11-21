"""Train the dengue early-warning models.

    python -m src.train

Chronological protocol (split on the *target* week t+h, never shuffled):
    train  : target <= 2021-12-31        -> fit candidate models
    valid  : 2022-01-01 .. 2023-12-31    -> early stopping + model/hyper-parameter selection
    test   : 2024-01-01 .. 2025-12-29    -> reported once, untouched during development
The deployed model is refit on train+valid with the selected settings and the
endemic channel is learned from train+valid years only.
"""
from __future__ import annotations

import json
import platform
import time
import warnings

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config as C
from .features import add_horizon_features, build_base_features, feature_columns, fit_thresholds, load_raw
from .risk import calibrate_p_alarm, estimate_nb_r, fit_endemic_channel, observed_tiers, predict_tiers
from .robust import SCENARIOS, apply_mask, augment

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*eval_set.*")
SEED = 42

CANDIDATES = [
    {"objective": "tweedie", "tweedie_variance_power": 1.3, "num_leaves": 15, "learning_rate": 0.03, "min_child_samples": 40},
    {"objective": "tweedie", "tweedie_variance_power": 1.5, "num_leaves": 31, "learning_rate": 0.03, "min_child_samples": 30},
    {"objective": "poisson", "num_leaves": 15, "learning_rate": 0.03, "min_child_samples": 40},
]
COMMON = {"n_estimators": 3000, "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.7,
          "reg_lambda": 1.0, "random_state": SEED, "verbose": -1, "n_jobs": -1}


def metrics(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    ss_res, ss_tot = np.sum((y - p) ** 2), np.sum((y - y.mean()) ** 2)
    return {"MAE": float(np.mean(np.abs(y - p))), "RMSE": float(np.sqrt(np.mean((y - p) ** 2))),
            "WAPE": float(np.sum(np.abs(y - p)) / max(np.sum(y), 1e-9)), "R2": float(1 - ss_res / max(ss_tot, 1e-9)),
            "n": int(len(y))}


def split_profile(raw: pd.DataFrame, risk_model: dict) -> dict:
    """Data distribution of each chronological split (by week date)."""
    from .risk import channel_lookup
    out = {}
    bins = [-0.5, 0.5, 1.5, 2.5, 4.5, 9.5, 19.5, 1e9]
    labels = ["0", "1", "2", "3-4", "5-9", "10-19", "20+"]
    ranges = {"train": ("2016-01-01", C.TRAIN_END), "validation": ("2022-01-01", C.VAL_END), "test": ("2024-01-01", "2025-12-31")}
    for name, (a, b) in ranges.items():
        d = raw[(raw.date >= a) & (raw.date <= b)]
        ch = channel_lookup(risk_model, d.ward_id, d.date)
        hi = float(np.mean(d.dengue_cases.values > ch.T_high.values))
        yearly = d.groupby(d.date.dt.year).dengue_cases.sum()
        monthly = d.groupby(d.date.dt.month).dengue_cases.mean()
        out[name] = {
            "weeks": int(d.date.nunique()), "rows": int(len(d)), "from": d.date.min().strftime("%Y-%m-%d"),
            "to": d.date.max().strftime("%Y-%m-%d"),
            "cases_mean": float(d.dengue_cases.mean()), "cases_median": float(d.dengue_cases.median()),
            "cases_std": float(d.dengue_cases.std()), "cases_max": int(d.dengue_cases.max()),
            "zero_share": float((d.dengue_cases == 0).mean()), "high_plus_share": hi,
            "cases_per_year": {str(k): int(v) for k, v in yearly.items()},
            "monthly_mean_cases": [round(float(monthly.get(m, 0)), 3) for m in range(1, 13)],
            "hist": dict(zip(labels, [int(x) for x in pd.cut(d.dengue_cases, bins, labels=labels).value_counts().reindex(labels).values])),
            "rain_mean": float(d.rainfall_mm.mean()), "temp_mean": float(d.avg_temp_c.mean()),
            "hum_mean": float(d.relative_humidity_pct.mean()),
        }
    return out


def split(frame):
    td = frame["target_date"]
    return (frame[td <= C.TRAIN_END], frame[(td > C.TRAIN_END) & (td <= C.VAL_END)], frame[td > C.VAL_END])


def fit_lgb(params, X, y, Xv=None, yv=None, n_estimators=None):
    p = {**COMMON, **params}
    if n_estimators:
        p["n_estimators"] = n_estimators
    m = lgb.LGBMRegressor(**p)
    if Xv is not None:
        metric = "quantile" if params.get("objective") == "quantile" else "l1"
        m.fit(X, y, eval_set=[(Xv, yv)], eval_metric=metric,
              callbacks=[lgb.early_stopping(150, verbose=False)])
    else:
        m.fit(X, y)
    return m


def main():
    t0 = time.time()
    raw = load_raw()
    thresholds = fit_thresholds(raw)
    base = build_base_features(raw, thresholds)
    C.ARTIFACT_DIR.mkdir(exist_ok=True)
    C.MODEL_DIR.mkdir(exist_ok=True)

    risk_model = fit_endemic_channel(raw, end=C.VAL_END)
    risk_val = fit_endemic_channel(raw, end=C.TRAIN_END)   # channel without validation years, for calibration
    raw["cases_s3"] = raw.groupby("ward_id").dengue_cases.transform(lambda s: s.rolling(3, center=True, min_periods=1).mean())
    smooth = raw.set_index(["ward_id", "date"])["cases_s3"]
    truth_of = lambda f: smooth.reindex(pd.MultiIndex.from_arrays([f.ward_id, f.target_date])).values
    r_list = []
    bundle = {"models": {}, "feature_cols": {}, "thresholds": thresholds, "risk": risk_model}
    report = {"split": {"train_target_end": C.TRAIN_END, "val_target_end": C.VAL_END,
                        "test_target_range": "2024-01-01..2025-12-29"}, "horizons": {}}
    backtests = []

    for h in C.HORIZONS:
        frame = add_horizon_features(base, h)
        cols = feature_columns(frame)
        frame = frame.dropna(subset=cols + ["target"])
        tr, va, te = split(frame)
        Xtr, ytr, Xva, yva, Xte, yte = tr[cols], tr.target, va[cols], va.target, te[cols], te.target

        # 1) model selection on validation
        best = None
        for i, params in enumerate(CANDIDATES):
            m = fit_lgb(params, Xtr, ytr, Xva, yva)
            mae = metrics(yva, m.predict(Xva))["MAE"]
            if best is None or mae < best[0]:
                best = (mae, i, params, m.best_iteration_ or COMMON["n_estimators"], m.predict(Xva), m)
        _, best_i, best_params, best_iter, pva, sel_model = best

        # 1b) calibrate the risk layer on validation (NB dispersion + alarm probability)
        r_h = estimate_nb_r(yva, pva)
        r_list.append(r_h)
        tv = observed_tiers(risk_val, truth_of(va), va.ward_id, va.target_date)
        p_alarm, f2_val = calibrate_p_alarm({**risk_val, "nb_r": r_h}, pva, tv, va.ward_id, va.target_date, h)
        risk_model["p_alarm"][h] = p_alarm

        q_iters, q_val = {}, {}
        for q in C.QUANTILES:
            qp = {"objective": "quantile", "alpha": q, "num_leaves": 15, "learning_rate": 0.03, "min_child_samples": 40}
            qm = fit_lgb(qp, Xtr, ytr, Xva, yva)
            q_iters[q] = (qp, qm.best_iteration_ or 500)
            q_val[q] = qm.predict(Xva)
        # Conformalised quantile regression (Romano et al. 2019): widen the band by the
        # validation quantile of the conformity score so 80% intervals really cover ~80%.
        lo_v, hi_v = np.minimum(q_val[0.1], pva), np.maximum(q_val[0.9], pva)
        scores = np.maximum(lo_v - yva.values, yva.values - hi_v)
        level = min(1.0, np.ceil((len(scores) + 1) * 0.8) / len(scores))
        cqr_margin = float(np.quantile(scores, level))

        # 2) refit on train+valid with chosen settings -> deployed model
        trva = pd.concat([tr, va])
        point = fit_lgb(best_params, trva[cols], trva.target, n_estimators=best_iter)
        qmodels = {q: fit_lgb(qp, trva[cols], trva.target, n_estimators=it) for q, (qp, it) in q_iters.items()}

        # 3) baselines + ablation, all evaluated on the untouched test years
        pte = point.predict(Xte)
        lo, hi = qmodels[0.1].predict(Xte), qmodels[0.9].predict(Xte)
        lo, hi = (np.minimum(lo, pte) - cqr_margin).clip(0), np.maximum(hi, pte) + cqr_margin
        res_cov_raw = float(np.mean((yte >= np.minimum(qmodels[0.1].predict(Xte), pte)) & (yte <= np.maximum(qmodels[0.9].predict(Xte), pte))))
        weather_cols = [c for c in cols if not c.startswith("cases_")]
        wonly = fit_lgb(best_params, trva[weather_cols], trva.target, n_estimators=best_iter)
        glm = make_pipeline(StandardScaler(), PoissonRegressor(alpha=1e-3, max_iter=1000))
        glm.fit(trva[cols], trva.target)
        res = {
            "selected_candidate": best_i, "selected_params": best_params, "n_trees": int(best_iter),
            "LightGBM (deployed)": metrics(yte, pte),
            "LightGBM weather-only (no case lags)": metrics(yte, wonly.predict(Xte[weather_cols])),
            "Poisson GLM": metrics(yte, glm.predict(Xte)),
            "Persistence (last week's cases)": metrics(yte, te["cases_lag0"]),
            "Seasonal naive (same week last year)": metrics(yte, te["cases_same_week_last_year"]),
            "PI80_coverage": float(np.mean((yte >= lo) & (yte <= hi))),
            "PI80_coverage_before_conformal": res_cov_raw, "cqr_margin": cqr_margin,
        }
        # tier skill on test: did we flag High/Critical weeks h weeks ahead?
        risk_model.setdefault("nb_r_by_h", {})[h] = r_h
        tiers = predict_tiers(risk_model, pte, te.ward_id, te.target_date, h)
        tp = tiers["tier_idx"].values
        ta = observed_tiers(risk_model, truth_of(te), te.ward_id, te.target_date)
        alert_p, alert_a = tp >= 2, ta >= 2
        res["p_alarm"], res["nb_r"], res["val_F2"] = p_alarm, r_h, f2_val
        res["tier_exact_accuracy"] = float(np.mean(tp == ta))
        res["tier_within_one"] = float(np.mean(np.abs(tp - ta) <= 1))
        res["alert_sensitivity"] = float(alert_p[alert_a].mean()) if alert_a.any() else None
        res["alert_precision"] = float(alert_a[alert_p].mean()) if alert_p.any() else None
        res["tier_confusion"] = pd.crosstab(pd.Series(ta, name="actual"), pd.Series(tp, name="pred")).reindex(
            index=range(4), columns=range(4), fill_value=0).values.tolist()
        # accuracy on every split: the selection model (fit on train only) and the deployed model
        res["by_split"] = {
            "selection_model": {"train (in-sample)": metrics(ytr, sel_model.predict(Xtr)),
                                "validation": metrics(yva, pva), "test": metrics(yte, sel_model.predict(Xte))},
            "deployed_model": {"train+validation (in-sample)": metrics(trva.target, point.predict(trva[cols])),
                               "test": metrics(yte, pte)},
        }

        # 4) robust model: same settings, trained with feature-group dropout so partial data still works
        Xa, ya = augment(trva[cols], trva.target)
        rpoint = fit_lgb(best_params, Xa, ya, n_estimators=best_iter)
        rq = {q: fit_lgb(qp, Xa, ya, n_estimators=it) for q, (qp, it) in q_iters.items()}
        clim = trva.groupby(trva.target_date.dt.isocalendar().week.astype(int)).target.mean()
        scen = {}
        for key, label, drop, hist_w in SCENARIOS:
            Xm = apply_mask(Xte, drop, hist_w)
            pr = rpoint.predict(Xm).clip(0)
            rlo = np.minimum(rq[0.1].predict(Xm), pr).clip(0)
            rhi = np.maximum(rq[0.9].predict(Xm), pr)
            scen[key] = {"label": label,
                         "deployed": metrics(yte, point.predict(Xm).clip(0)),
                         "robust": metrics(yte, pr),
                         "robust_PI80_coverage": float(np.mean((yte >= rlo) & (yte <= rhi))),
                         "complete_features_share": float(Xm.notna().mean().mean())}
        clim_pred = te.target_date.dt.isocalendar().week.astype(int).map(clim).fillna(clim.mean())
        res["completeness"] = {"scenarios": scen, "climatology_baseline": metrics(yte, clim_pred)}
        bundle.setdefault("robust", {})[h] = {"point": rpoint, "q10": rq[0.1], "q90": rq[0.9]}
        print(f"    robust: full={scen['full']['robust']['MAE']:.3f} (deployed {scen['full']['deployed']['MAE']:.3f}) | "
              f"new_area={scen['new_area']['robust']['MAE']:.3f} (deployed {scen['new_area']['deployed']['MAE']:.3f}) | "
              f"rain_only_4w={scen['rain_only_4w']['robust']['MAE']:.3f} | climatology={res['completeness']['climatology_baseline']['MAE']:.3f}")
        report["horizons"][str(h)] = res

        bundle["models"][h] = {"point": point, "q10": qmodels[0.1], "q90": qmodels[0.9], "cqr_margin": cqr_margin}
        bundle["feature_cols"][h] = cols
        backtests.append(pd.DataFrame({
            "date": te.date.values, "target_date": te.target_date.values, "city": te.city.values,
            "ward_id": te.ward_id.values, "horizon": h, "actual": yte.values, "pred": pte,
            "lo": lo, "hi": hi, "risk_score": tiers["risk_score"].values, "tier_pred": tp, "tier_actual": ta}))
        print(f"h={h}: test MAE={res['LightGBM (deployed)']['MAE']:.3f} RMSE={res['LightGBM (deployed)']['RMSE']:.3f} "
              f"| seasonal-naive MAE={res['Seasonal naive (same week last year)']['MAE']:.3f} "
              f"| PI80 cov={res['PI80_coverage']:.2f} | alert sens={res['alert_sensitivity']:.2f} prec={res['alert_precision']:.2f}")

    risk_model["nb_r"] = float(np.median(r_list))
    report["split_profile"] = split_profile(raw, risk_model)
    bundle["meta"] = {"trained_at": time.strftime("%Y-%m-%d %H:%M"), "train_data_end": C.VAL_END,
                      "python": platform.python_version(), "lightgbm": lgb.__version__,
                      "horizons": list(C.HORIZONS)}
    joblib.dump(bundle, C.MODEL_BUNDLE, compress=3)
    pd.concat(backtests).to_csv(C.BACKTEST_PATH, index=False, float_format="%.4f")
    report["train_seconds"] = round(time.time() - t0, 1)
    C.METRICS_PATH.write_text(json.dumps(report, indent=2))
    print(f"Saved {C.MODEL_BUNDLE.name}, backtest and metrics in {report['train_seconds']}s")


if __name__ == "__main__":
    main()
