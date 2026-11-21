"""Central configuration: paths, horizons, split dates, feature settings."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "dengue_weather_wards.csv"
MODEL_DIR = ROOT / "models"
ARTIFACT_DIR = ROOT / "artifacts"
CACHE_DIR = ROOT / ".cache"

MODEL_BUNDLE = MODEL_DIR / "dengue_ews_bundle.joblib"
BACKTEST_PATH = ARTIFACT_DIR / "backtest_predictions.csv.gz"  # CSV keeps the install light (no pyarrow)
METRICS_PATH = ARTIFACT_DIR / "metrics.json"

# Forecast horizons in weeks (the 2-to-4-week early-warning window)
HORIZONS = (2, 3, 4)

# Chronological split, applied on the *target* week (t + h) so that
# no training label ever falls inside the validation/test period.
TRAIN_END = "2021-12-31"   # fit
VAL_END = "2023-12-31"     # early stopping / tuning; deployed model is refit on train+val
# test = 2024-01-01 .. 2025-12-29 (never seen during fitting or tuning)

# Lag design (weeks). Effective lag relative to the predicted week = k + h.
RAIN_LAGS = range(0, 13)          # 0..12 weeks
TEMP_LAGS = range(0, 9)           # 0..8 weeks
HUM_LAGS = range(0, 7)            # 0..6 weeks
CASE_LAGS = range(0, 5)           # 0..4 weeks (autoregressive signal)
ROLL_WINDOWS = (2, 4, 8, 12)

AEDES_OPT_TEMP_C = 29.0           # Mordecai et al. 2017 thermal optimum for transmission

QUANTILES = (0.1, 0.9)            # 80% prediction interval

TIERS = ["Low", "Medium", "High", "Critical"]
TIER_COLORS = {"Low": "#2e7d32", "Medium": "#f9a825", "High": "#ef6c00", "Critical": "#c62828"}

CITY_CENTROIDS = {   # used only by the optional live-weather mode
    "Dhaka North": (23.8103, 90.4125),
    "Dhaka South": (23.7104, 90.4074),
    "Chattogram": (22.3569, 91.7832),
    "Gazipur": (23.9999, 90.4203),
    "Narayanganj": (23.6238, 90.5000),
}
