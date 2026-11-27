# Phase 2: the "ward plan" contract (requirements 1, 3 → 4, 5)

**Who reads this:** whoever builds **Requirement 1 (resource allocation engine)** and **Requirement 3 (vector-control simulator)**, and the Claude session helping them.
**Why:** Requirement 4 (dispatch notices) and Requirement 5 (offline mode) are being built now. They don't compute supplies or spraying teams themselves. They read one object per ward called a **ward plan**. As long as 1 and 3 produce exactly this shape, everything plugs together with no rewrite.

> **Note for Claude:** treat this file as a fixed interface. Do not rename, remove or change the type of any field below. You may add new fields. If you think the contract needs to change, stop and tell the human instead of changing it silently.

---

## 1. Where the code lives

| Piece | Owner | File | What it does |
|---|---|---|---|
| Plan builder | Req 1 (+ Req 3) | `src/plan.py` | `build_plans(...)` returns the list of ward plans below |
| Stand-in (temporary) | Req 4/5 | `src/plan_stub.py` | Same signature and output; used until `src/plan.py` exists |
| Dispatch notices | Req 4 | `src/dispatch.py` | Reads ward plans and writes SMS, WhatsApp and printable briefs |
| Contract check | Req 4/5 | `tests/test_plan_contract.py` | Fails if a plan doesn't match this file. Run it on your code. |

Dispatch calls `src.plan.build_plans` if it exists, otherwise `src.plan_stub.build_plans`. When your version lands, delete nothing; it takes over automatically.

## 2. The function you must provide

```python
# src/plan.py
def build_plans(
    engine,                    # the loaded model engine (src/predict.py Engine), already used by src/api.py
    date: str,                 # forecast reference week, ISO "YYYY-MM-DD" (a dataset Monday), same as /api/forecast
    h: int,                    # horizon in weeks: 2, 3 or 4
    stock_pct: float = 100.0,  # Available Regional Stock slider, 30–100 (percent of total required stock)
    total_crews: int = 20,     # Total Spraying Crews slider
    deployments: dict | None = None,  # Req 3: {ward_id: crews_sent}; None = use Req 1's own crew assignment
    scenario=None,             # optional weather what-if, same object src/api.py already passes to the engine
) -> list[dict]:               # exactly one ward plan per ward, all 60 wards, any order
    ...
```

Rules:
- **All 60 wards**, every call, including Low-risk wards (they just get small or zero numbers).
- **Deterministic:** same inputs give the same output. No randomness without a fixed seed.
- **Offline:** no internet, no LLM or API calls. Only the local model and the dataset.
- **Fast:** under about 1 second for all 60 wards (the UI sliders call it live).

## 3. The ward plan object (exact shape)

```json
{
  "ward_id": "DNCC_Ward_01",
  "city": "Dhaka North",
  "date": "2025-07-07",
  "h": 3,
  "target_week": "2025-07-28",

  "predicted_cases": 11.3,
  "cases_low": 5.3,
  "cases_high": 16.0,
  "tier": "High",
  "risk_score": 48,
  "population_density_sqkm": 64024,

  "required":  { "saline_bags": 27, "diagnostic_kits": 13, "hospital_beds": 4 },
  "allocated": { "saline_bags": 19, "diagnostic_kits": 13, "hospital_beds": 4 },
  "deficit":   { "saline_bags": 8,  "diagnostic_kits": 0,  "hospital_beds": 0 },
  "coverage_pct": { "saline_bags": 70.4, "diagnostic_kits": 100.0, "hospital_beds": 100.0 },
  "deficit_level": "partial",

  "priority_rank": 4,
  "spray_teams": 2,

  "simulation": {
    "crews_deployed": 2,
    "cases_prevented": 3.1,
    "beds_saved": 0.9,
    "saline_saved": 7.4,
    "roi_cases_per_crew": 1.55
  },

  "stock_pct": 70,
  "total_crews": 20,
  "source": "engine"
}
```

## 4. What each field means

**Identity and forecast** (copy these from the existing forecast; don't recompute them):

| Field | Type | Meaning |
|---|---|---|
| `ward_id` | str | e.g. `DNCC_Ward_01`, same IDs as the dataset |
| `city` | str | One of `Dhaka North`, `Dhaka South`, `Chattogram`, `Gazipur`, `Narayanganj` |
| `date` | str | Reference week passed in |
| `h` | int | Horizon passed in (2, 3 or 4) |
| `target_week` | str | `date` + `h` weeks: the week the cases are expected |
| `predicted_cases` | float | Model's expected cases for the target week (`pred` in `/api/forecast`) |
| `cases_low`, `cases_high` | float | 80% range (`lo`, `hi`) |
| `tier` | str | `Low`, `Medium`, `High` or `Critical` (same tiers as now) |
| `risk_score` | int | 0–100 (`score` in `/api/forecast`) |
| `population_density_sqkm` | int | From the dataset; used for prioritising |

**Supplies** (Requirement 1). Each block always has exactly these three keys: `saline_bags`, `diagnostic_kits`, `hospital_beds`.

| Field | Type | Rule |
|---|---|---|
| `required` | int per item | What the ward needs if stock were unlimited. Round **up** (ceil). Base it on `predicted_cases` and the per-case ratios in the Phase 2 dataset columns `saline_bags_needed`, `diagnostic_kits_needed` and `hospital_beds_required`. |
| `allocated` | int per item | What the ward actually gets under `stock_pct` and your prioritisation. Must satisfy `0 ≤ allocated ≤ required`. |
| `deficit` | int per item | Always `required − allocated` (never negative) |
| `coverage_pct` | float per item | `100 × allocated / required`, or `100.0` when `required` is 0. One decimal. |
| `deficit_level` | str | `"none"` if every deficit is 0. `"critical"` if any `coverage_pct` < 50. Otherwise `"partial"`. |
| `priority_rank` | int | 1 = served first. Unique across all 60 wards. Higher risk and higher density should rank first. |

The total allocated across all 60 wards per item must not exceed `stock_pct %` of the total required.

**Spraying teams** (Requirement 1 assigns, Requirement 3 overrides):

| Field | Type | Rule |
|---|---|---|
| `spray_teams` | int ≥ 0 | Crews assigned to this ward. If `deployments` is given, use those numbers. The sum over all wards must be ≤ `total_crews`. |
| `simulation` | object or `null` | Requirement 3 only. `null` when no simulation was run. |
| `simulation.crews_deployed` | int | Crews this ward received in the simulation |
| `simulation.cases_prevented` | float | Predicted cases without crews minus with crews, over the 2–4 week window. Must come from the model, not a fixed %. |
| `simulation.beds_saved`, `simulation.saline_saved` | float | The same reduction expressed in beds and saline bags |
| `simulation.roi_cases_per_crew` | float | `cases_prevented / crews_deployed` (0 if no crews) |

**Run settings:**

| Field | Type | Meaning |
|---|---|---|
| `stock_pct`, `total_crews` | number | Echo the inputs so a notice can say "at 70% stock" |
| `source` | str | `"engine"` from your code, `"stub"` from the stand-in |

## 5. API endpoint (so the UI and notices share one path)

```
GET /api/plan?date=2025-07-07&h=3&stock_pct=70&total_crews=20
POST /api/plan/simulate   body: {"date": "...", "h": 3, "stock_pct": 70, "total_crews": 20, "deployments": {"DNCC_Ward_01": 2}}
```

Both return:

```json
{ "plans": [ ...60 ward plans... ], "totals": { "required": {...}, "allocated": {...}, "deficit": {...} } }
```

Add them in `src/api.py` next to `/api/forecast`, and keep the existing endpoints unchanged.

## 6. Requirement 2 (overflow routing), optional field

If you build bed overflow routing, add this to each plan. Dispatch will show it when present and ignore it when missing:

```json
"overflow": { "beds_short": 3, "send_to": [{"ward_id": "DNCC_Ward_07", "beds": 3}] }
```

Ward bed capacity is **not** in the dataset. Whatever rule you use to estimate it, write it down in `ARCHITECTURE.md`.

## 7. Before you commit

1. Run `python -m pytest -q`. All tests must pass, including `tests/test_plan_contract.py`.
2. Pull the network plug, or set `SHOTORKO_OFFLINE=1`, and call `/api/plan`. It must still answer.
3. Don't edit `src/dispatch.py` or `src/plan_stub.py`; message the Req 4/5 owner instead.
4. Use the team's commit naming for Phase 2: `version-onsite-final` (confirm the exact format with the team).
