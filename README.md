# Shotorko (সতর্ক) by TRIGUN: dengue early warning, ward by ward

**Shotorko** forecasts dengue risk for 60 city wards in Dhaka North, Dhaka South, Chattogram, Gazipur and Narayanganj, **2, 3 and 4 weeks ahead**. It turns lagged rainfall, temperature and humidity, plus each ward's drainage, crowding and sanitation, into expected case counts, 80% prediction intervals and **Low / Medium / High / Critical** risk tiers. Public-health officers can use these to send larviciding and drain-clearing teams to wards before an outbreak peaks.

*Shotorko* (সতর্ক) means "alert, on guard" in Bangla.

**Live demo:** https://shotorko-by-trigun.onrender.com/ · **Demo video:** [watch on Google Drive](https://drive.google.com/file/d/1qEMGLf551k-3XB1SZB4KWHsLtJW90DJH/view?usp=sharing)

## Quick start (≈ 1 minute)

Requires **Python 3.10–3.13** and git.

```bash
git clone https://github.com/m-kaeem/shotorko-by-trigun.git shotorko && cd shotorko
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

The app opens at **http://localhost:8000**. No API keys, no Node.js and no training run are needed: the trained model bundle, the dataset and the front-end libraries are all in the repo.

**Timing:** the first `pip install` downloads about 80 MB of wheels: roughly 10 s on a fast connection and 1–2 min on a 10 Mbps line. The server starts in about 3 s. After a `git pull`, reuse the same `.venv`; `pip install -r requirements.txt` then finishes in seconds ("Requirement already satisfied"), and you only need it when `requirements.txt` changes.

Optional commands:

```bash
python -m pytest -q      # verify the lag pipeline (no leakage, correct ward-level lags), the model and the API
python -m src.train      # retrain all models from the CSV (~2.5 min), rewrites models/ and artifacts/
```

## What you can do in the app

| Area | What it shows |
|---|---|
| **3D risk map** | All 60 wards as 3D columns on a map of Bangladesh. Column height is expected cases and colour is the risk tier. You can rotate, tilt and zoom the map. Clicking a column flies the camera to that ward and opens its forecast. You can filter by city or search for a ward. |
| **Ward forecast panel** | Risk score, expected cases for 2, 3 and 4 weeks with 80% ranges, and a plain-language summary. A chart compares the last 12 weeks with the forecast and the seasonal normal. **Driver breakdown** (TreeSHAP grouped into rainfall, temperature, humidity, recent cases, ward vulnerability and season), a weather strip and a recommended action. |
| **Monsoon Simulator** (বর্ষা সিমুলেটর) | Change rainfall (×0–3), temperature (±4 °C) and humidity (±20 points) over the last 4 weeks, in replay **or live** mode. The model re-runs for every ward, and the map regrows and rain streaks thicken. |
| **Hospital readiness** | Dengue-treating public hospitals for each city appear on the map and in the ward panel. The panel adds the city's expected dengue load vs normal (summed ward forecasts), preparedness actions when risk is High or Critical, and the DGHS health line 16263. Real-time bed occupancy is not publicly available, so pressure is forecast-based. |
| **Week picker & season replay** | A calendar picker (year → months shaded by reported cases → weeks) with previous/next week and month buttons chooses any week from 2017 to 2025; test months are marked. The ▶ Play button steps week by week through 2024–2025 (out-of-sample). A **"What really happened"** toggle shows the reported counts on the same map so you can check the model. |
| **Live weather** | Feeds this season's real Open-Meteo weather for all five cities (one cached request) into the model for a weather-driven outlook. |
| **Model trust** | Chronological split, MAE vs baselines by horizon, backtest chart with intervals, a **ward × week risk heatmap** for 2024–25 (forecast vs what happened), the **data distribution of each split**, accuracy on train/validation/test (MAE, RMSE, WAPE, R²), missing-data robustness, tier confusion matrix, error by month, interval coverage, alert sensitivity and precision. |
| **Your data** | Upload a short or incomplete CSV (weekly or daily; only a date plus rainfall or temperature is required). Forecasts come from a robust model trained for missing data, with the typical error measured for that data shape. A sample CSV is included. |
| **How it works** | Animated "breeding clock" (rain → standing water → mosquitoes → patients) and live system stats: inference time, API calls, LLM tokens (0). |
| **For everyone** | Full **English / বাংলা** switch (including numerals), a 4-step guided tour on first visit, keyboard-accessible controls, CSV field plan download, and reduced-motion support. |

## Results (test years 2024–2025, unseen during development)

| Horizon | MAE | RMSE | vs seasonal-naive MAE | 80% interval coverage | Outbreak weeks caught |
|---|---|---|---|---|---|
| 2 weeks | 0.99 | 1.97 | −41% | 85% | 71% |
| 3 weeks | 1.04 | 2.06 | −38% | 85% | 86% |
| 4 weeks | 1.16 | 2.30 | −31% | 85% | 84% |

### Data distribution per split

| Split | Weeks | Ward-weeks | Total cases | Mean / ward-week | Weeks with 0 cases | Weeks above alert threshold |
|---|---|---|---|---|---|---|
| Train (2016–2021) | 313 | 18,780 | 48,419 | 2.58 | 58.1% | 9.0% |
| Validation (2022–2023) | 104 | 6,240 | 19,968 | 3.20 | 56.5% | 13.6% |
| Test (2024–2025) | 105 | 6,300 | 14,185 | 2.25 | 59.5% | 6.8% |

The test years were quieter than validation. That is why test MAE is lower than validation MAE but alert precision is lower too: High weeks are rarer.

### Accuracy on each split (3-week horizon)

| Model | Split | MAE | RMSE | WAPE | R² |
|---|---|---|---|---|---|
| Fit on train only | Train (seen) | 0.84 | 1.60 | 33% | 0.87 |
| | Validation | 1.34 | 2.78 | 42% | 0.73 |
| | Test (unseen) | 1.07 | 2.13 | 47% | 0.70 |
| **Deployed (train + validation)** | **Test (unseen)** | **1.04** | **2.06** | **46%** | **0.72** |

### Incomplete data (3-week horizon, MAE on test weeks)

| Data available | Standard model | Robust model |
|---|---|---|
| Complete | 1.04 | 1.01 |
| No temperature | 1.67 | 1.00 |
| Only last 4 weeks | 1.15 | 1.00 |
| New area: rain + temperature, 8 weeks, no cases, no ward profile | 1.83 | 1.11 |
| Rainfall only, 4 weeks | 2.01 | 1.10 |
| *No data at all (seasonal average)* | *1.30* | |

The robust model is trained with **feature-group dropout**: copies of the training rows with whole column groups and long history randomly blanked. It is used automatically whenever input data is incomplete.

Design, data flow, validation split, model rationale and cost controls are in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Repository layout

```
run.py                     starts the app (FastAPI + static web client) on localhost:8000
src/api.py                 REST API: meta, forecast, ward/{id}, validation, backtest/{id}, heatmap, hospitals, upload, sample.csv, usage
src/robust.py              feature-group dropout + completeness scenarios (missing-data robustness)
src/upload.py              parse a user's CSV (flexible columns, daily→weekly) and forecast with the robust model
src/hospitals.py           dengue-treating public hospitals per city (sources in the file)
src/config.py              paths, horizons, split dates, lag settings
src/features.py            data loading + ward-level multi-week lag pipeline
src/risk.py                endemic channel, outbreak probability, risk tiers
src/train.py               chronological training, baselines, calibration, serialization
src/predict.py             inference engine, TreeSHAP drivers, what-if scenarios
src/weather_client.py      optional Open-Meteo client (single request, disk cache, call counter)
web/                       single-page app: index.html, app.js, styles.css, i18n.js (EN/BN)
web/vendor/, web/data/     vendored deck.gl and Natural Earth map outlines (see THIRD_PARTY.md)
data/                      hackathon dataset (CSV export of the provided Google Sheet)
models/                    serialized joblib model bundle
artifacts/                 metrics.json + backtest predictions used by the Model trust page
tests/                     pipeline, model and API tests
```

## AI disclosure

| Tool | Used for |
|---|---|
| **Claude (Anthropic)** | Planning, research, and drafting code, tests and docs. The team reviewed, ran and tested everything. |
| **Open-Meteo API** | Optional live-weather mode only; not used for training. |

No AI model runs inside the app, and the model is trained only on the organisers' dataset. Bundled open-source assets: deck.gl (MIT) and Natural Earth outlines (public domain).

## Team TRIGUN

| Member | Role |
|---|---|
| Mahbubul Alam | Data ingestion, inference engine, live-weather client, web app & 3D map |
| Mohammad Mahiuddin | Lag feature pipeline, robust (missing-data) model, risk tiers, training & chronological validation, REST API |
| Mohammad Maharul Islam | Hospital readiness layer, "Your data" CSV upload, tests, architecture & model documentation |

Demo video: https://drive.google.com/file/d/1qEMGLf551k-3XB1SZB4KWHsLtJW90DJH/view?usp=sharing

Live deployment: https://shotorko-by-trigun.onrender.com/

Weather data in live mode: [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0.
