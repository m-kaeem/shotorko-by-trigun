"""Write sample dispatch notices to exports/ (Phase 2 deliverable: "/exports sample SMS/WhatsApp/printable briefs").

    python -m src.export_samples                       # default demo week, 3-week horizon, 70% stock
    python -m src.export_samples --date 2025-07-07 --h 3 --stock 70 --crews 20

Runs fully offline: the local model, the dataset and fixed templates. Writes, for every High+ ward:
exports/sms/<ward>_<lang>.txt, exports/whatsapp/<ward>_<lang>.txt, exports/briefs/<ward>_<lang>.html,
plus exports/dispatch_round_<date>.csv (one row per ward) and exports/README.md.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from . import config as C
from . import dispatch as D
from .plan_stub import get_build_plans
from .predict import Engine


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default="2025-07-07")
    ap.add_argument("--h", type=int, default=3, choices=C.HORIZONS)
    ap.add_argument("--stock", type=float, default=70.0)
    ap.add_argument("--crews", type=int, default=20)
    ap.add_argument("--min-tier", default="High", choices=C.TIERS)
    ap.add_argument("--out", default=str(C.ROOT / "exports"), help="output folder (default: exports/)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    for sub in ("sms", "whatsapp", "briefs"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    build, source = get_build_plans()
    plans = build(Engine(), a.date, a.h, stock_pct=a.stock, total_crews=a.crews)
    keep = C.TIERS[C.TIERS.index(a.min_tier):]
    plans = sorted((p for p in plans if p["tier"] in keep), key=lambda p: p["priority_rank"])
    rows = []
    for p in plans:
        for lang in ("en", "bn"):
            stem = f"{p['ward_id']}_{lang}"
            (out / "sms" / f"{stem}.txt").write_text(D.sms(p, lang) + "\n", encoding="utf-8")
            (out / "whatsapp" / f"{stem}.txt").write_text(D.whatsapp(p, lang) + "\n", encoding="utf-8")
            (out / "briefs" / f"{stem}.html").write_text(D.brief_html(p, lang), encoding="utf-8")
        s = D.sms(p, "en")
        rows.append({"priority": p["priority_rank"], "ward_id": p["ward_id"], "city": p["city"], "tier": p["tier"],
                     "predicted_cases": p["predicted_cases"], "deficit_level": p["deficit_level"],
                     **{f"{k}_required": p["required"][k] for k in D.ITEMS},
                     **{f"{k}_allocated": p["allocated"][k] for k in D.ITEMS},
                     "spray_teams": p["spray_teams"], "sms_en": s, "sms_en_segments": D.sms_segments(s)})
    with open(out / f"dispatch_round_{a.date}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ward_id"])
        w.writeheader()
        w.writerows(rows)
    (out / "README.md").write_text(
        f"# Sample dispatch notices\n\nGenerated offline by `python -m src.export_samples` for the week of {a.date}, "
        f"{a.h}-week horizon, regional stock {a.stock:g}%, {a.crews} spray crews, wards at {a.min_tier} risk or above "
        f"({len(plans)} wards). Supply plan source: `{source}`.\n\n"
        "- `sms/`: one SMS per ward (English fits one 160-character message; Bangla is Unicode, 70 characters per part)\n"
        "- `whatsapp/`: WhatsApp alerts with bold labels\n"
        "- `briefs/`: printable office orders (open in a browser, then Print or Save as PDF)\n"
        f"- `dispatch_round_{a.date}.csv`: the whole round in one table\n\n"
        "Each ward has an English (`_en`) and a Bangla (`_bn`) version.\n", encoding="utf-8")
    print(f"Wrote {len(plans)} wards x 2 languages x 3 channels to {out} (plan source: {source})")


if __name__ == "__main__":
    main()
