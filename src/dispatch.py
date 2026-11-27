"""Requirement 4: ward-specific dispatch notices (SMS, WhatsApp, printable commissioner brief).

Every notice is built from one ward plan (docs/PHASE2_PLAN_CONTRACT.md) with fixed templates in
English and Bangla. No LLM and no network: the same plan always gives the same text, so notices
keep working when the internet is down during judging (Requirement 5).
"""
from __future__ import annotations

import html
from datetime import datetime

import pandas as pd

from .hospitals import HOSPITALS, HOTLINE

BN_DIG = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
CITY_CODE = {"Dhaka North": "DNCC", "Dhaka South": "DSCC", "Chattogram": "CCC", "Gazipur": "GCC", "Narayanganj": "NCC"}
T = {
    "en": {
        "tiers": {"Low": "Low", "Medium": "Medium", "High": "High", "Critical": "Critical"},
        "cities": {c: c for c in CITY_CODE},
        "items": {"saline_bags": "Saline bags", "diagnostic_kits": "Test kits", "hospital_beds": "Hospital beds"},
        "short": {"saline_bags": "saline", "diagnostic_kits": "kits", "hospital_beds": "beds"},
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "ward": "Ward",
        "action": {
            "Critical": "Send spray teams now, clear standing water, alert clinics and ready beds.",
            "High": "Send spray teams this week and clear drains before the peak.",
            "Medium": "Inspect breeding sites and keep supplies on standby.",
            "Low": "Routine surveillance; no extra deployment needed.",
        },
        "sms_action": {"Critical": "Act now.", "High": "Act this week.", "Medium": "Inspect sites.", "Low": "Routine watch."},
    },
    "bn": {
        "tiers": {"Low": "কম", "Medium": "মাঝারি", "High": "উচ্চ", "Critical": "সংকটজনক"},
        "cities": {"Dhaka North": "ঢাকা উত্তর", "Dhaka South": "ঢাকা দক্ষিণ", "Chattogram": "চট্টগ্রাম",
                   "Gazipur": "গাজীপুর", "Narayanganj": "নারায়ণগঞ্জ"},
        "items": {"saline_bags": "স্যালাইন ব্যাগ", "diagnostic_kits": "পরীক্ষা কিট", "hospital_beds": "হাসপাতাল শয্যা"},
        "short": {"saline_bags": "স্যালাইন", "diagnostic_kits": "কিট", "hospital_beds": "শয্যা"},
        "months": ["জানু", "ফেব্রু", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টে", "অক্টো", "নভে", "ডিসে"],
        "ward": "ওয়ার্ড",
        "action": {
            "Critical": "এখনই স্প্রে দল পাঠান, জমে থাকা পানি সরান, ক্লিনিক সতর্ক করুন ও শয্যা প্রস্তুত রাখুন।",
            "High": "এই সপ্তাহেই স্প্রে দল পাঠান এবং চূড়ার আগে ড্রেন পরিষ্কার করুন।",
            "Medium": "প্রজননস্থল পরিদর্শন করুন, সরবরাহ প্রস্তুত রাখুন।",
            "Low": "নিয়মিত নজরদারি; বাড়তি দল দরকার নেই।",
        },
        "sms_action": {"Critical": "এখনই ব্যবস্থা নিন।", "High": "এই সপ্তাহে ব্যবস্থা নিন।", "Medium": "স্থান পরিদর্শন করুন।",
                       "Low": "নিয়মিত নজরদারি।"},
    },
}
ITEMS = ("saline_bags", "diagnostic_kits", "hospital_beds")


def _n(x, lang, nd=0) -> str:
    s = f"{x:.{nd}f}" if isinstance(x, float) else str(x)
    return s.translate(BN_DIG) if lang == "bn" else s


def _date(iso: str, lang: str, year=False) -> str:
    d = pd.Timestamp(iso)
    s = f"{d.day} {T[lang]['months'][d.month - 1]}" + (f" {d.year}" if year else "")
    return s.translate(BN_DIG) if lang == "bn" else s


def _ward_no(ward_id: str) -> str:
    return ward_id.split("_")[-1]


def notice(plan: dict, lang: str = "en") -> dict:
    """Structured notice: everything a channel renderer needs, already localised."""
    L = T[lang]
    deficits = {k: v for k, v in plan["deficit"].items() if v > 0}
    hosp = [h for h in HOSPITALS if h["city"] == plan["city"]][:2]
    return {
        "ward_id": plan["ward_id"], "lang": lang,
        "ward": f"{L['ward']} {_n(_ward_no(plan['ward_id']), lang)}",
        "city": L["cities"].get(plan["city"], plan["city"]), "city_code": CITY_CODE.get(plan["city"], ""),
        "tier": plan["tier"], "tier_label": L["tiers"][plan["tier"]], "risk_score": plan["risk_score"],
        "week": _date(plan["target_week"], lang, year=True), "week_short": _date(plan["target_week"], lang),
        "issued": _date(plan["date"], lang, year=True), "h": plan["h"],
        "cases": _n(round(plan["predicted_cases"]), lang),
        "range": f"{_n(round(plan['cases_low']), lang)}–{_n(round(plan['cases_high']), lang)}",
        "supplies": [{"key": k, "label": L["items"][k], "short": L["short"][k],
                      "required": plan["required"][k], "allocated": plan["allocated"][k],
                      "deficit": plan["deficit"][k], "coverage": plan["coverage_pct"][k]} for k in ITEMS],
        "deficits": deficits, "deficit_level": plan["deficit_level"],
        "spray_teams": plan["spray_teams"], "priority_rank": plan["priority_rank"],
        "action": L["action"][plan["tier"]] + (
            "" if plan["spray_teams"] or plan["tier"] in ("Low", "Medium") else
            (" No spray crew is free in this round: request one from the city pool." if lang == "en"
             else " এই দফায় কোনো স্প্রে দল খালি নেই: সিটি পুল থেকে দল চান।")),
        "sms_action": L["sms_action"][plan["tier"]],
        "hospitals": [h["name"] for h in hosp], "hotline": HOTLINE["number"],
        "overflow": plan.get("overflow"), "simulation": plan.get("simulation"),
        "stock_pct": plan["stock_pct"], "source": plan["source"],
    }


# ------------------------------------------------------------------ SMS
def sms_segments(text: str) -> int:
    """GSM-7 fits 160 chars (153 per part); Bangla needs UCS-2: 70 chars (67 per part)."""
    ucs2 = any(ord(ch) > 127 for ch in text)
    single, multi = (70, 67) if ucs2 else (160, 153)
    return 1 if len(text) <= single else -(-len(text) // multi)


def sms(plan: dict, lang: str = "en") -> str:
    """One SMS segment in English (<=160 chars); Bangla is kept as short as possible."""
    N = notice(plan, lang)
    tag = "SHOTORKO" if lang == "en" else "সতর্ক"
    head = f"{tag} {N['tier_label'].upper() if lang == 'en' else N['tier_label']} {N['city_code']}-W{_ward_no(plan['ward_id'])}"
    if lang == "en":
        head = f"SHOTORKO: Ward {_ward_no(plan['ward_id'])} {N['city_code']} is {N['tier_label'].upper()}."
        body = f" ~{N['cases']} dengue cases expected wk of {N['week_short']} ({N['range']})."
        act = f" {N['sms_action']}"
        short = (" Short: " + ", ".join(f"{T[lang]['short'][k]} {v}" for k, v in N["deficits"].items()) + ".") if N["deficits"] else ""
        crews = f" {N['spray_teams']} spray team{'s' if N['spray_teams'] != 1 else ''} coming." if N["spray_teams"] else ""
        sup = " You have " + ", ".join(f"{s['short']} {s['allocated']}/{s['required']}" for s in N["supplies"]) + "."
        tail = f" Hotline {N['hotline']}"
        parts = [head, body, act, short, crews, sup, tail]
        parts = [x.replace("–", "-") for x in parts]   # keep the English SMS in the GSM-7 alphabet (160 chars)
        text = "".join(parts)
        for drop in (5, 6, 4):     # supplies detail first, then the hotline, then the crew line
            if len(text) <= 160:
                break
            parts[drop] = ""
            text = "".join(parts)
    else:
        body = f" {N['week_short']}: ~{N['cases']} রোগী।"
        sup = " পেয়েছে/দরকার " + ", ".join(f"{s['short']} {_n(s['allocated'], lang)}/{_n(s['required'], lang)}" for s in N["supplies"]) + "।"
        short = (" ঘাটতি " + ", ".join(f"{T[lang]['short'][k]} {_n(v, lang)}" for k, v in N["deficits"].items()) + "।") if N["deficits"] else ""
        crews = f" স্প্রে দল {_n(N['spray_teams'], lang)}।" if N["spray_teams"] else ""
        tail = f" {N['sms_action']}"
        text = "".join([head, body, short, crews, sup, tail])
    return text.strip()


# ------------------------------------------------------------------ WhatsApp
def whatsapp(plan: dict, lang: str = "en") -> str:
    N = notice(plan, lang)
    en = lang == "en"
    lines = [
        f"*{'Shotorko dengue alert' if en else 'সতর্ক ডেঙ্গু সতর্কতা'}: {N['city']}, {N['ward']}*",
        f"{'Risk' if en else 'ঝুঁকি'}: *{N['tier_label']}* ({_n(N['risk_score'], lang)}/{_n(100, lang)})",
        f"{'Week of' if en else 'সপ্তাহ'} {N['week']}: ~*{N['cases']}* {'cases expected' if en else 'জন রোগী প্রত্যাশিত'} ({N['range']})",
        "",
        f"*{'Supplies (allocated / required)' if en else 'সরবরাহ (বরাদ্দ / প্রয়োজন)'}*",
    ]
    for s in N["supplies"]:
        mark = ("  ⚠ " + ("short" if en else "ঘাটতি") + f" {_n(s['deficit'], lang)}") if s["deficit"] else ""
        lines.append(f"• {s['label']}: {_n(s['allocated'], lang)} / {_n(s['required'], lang)}{mark}")
    if N["deficit_level"] != "none":
        lvl = {"partial": ("Partial supply deficit", "আংশিক সরবরাহ ঘাটতি"), "critical": ("CRITICAL supply deficit", "গুরুতর সরবরাহ ঘাটতি")}
        lines += ["", f"*{lvl[N['deficit_level']][0 if en else 1]}*: " + ("escalate to the city store." if en else "সিটি স্টোরে জানান।")]
    lines += ["", f"{'Spray teams assigned' if en else 'নির্ধারিত স্প্রে দল'}: *{_n(N['spray_teams'], lang)}*"]
    if N["overflow"] and N["overflow"].get("send_to"):
        dest = ", ".join(f"{d['ward_id']} ({_n(d['beds'], lang)})" for d in N["overflow"]["send_to"])
        lines.append(f"{'Bed overflow to' if en else 'অতিরিক্ত রোগী পাঠান'}: {dest}")
    lines += ["", f"{'Action' if en else 'করণীয়'}: {N['action']}",
              f"{'Dengue hospitals' if en else 'ডেঙ্গু হাসপাতাল'}: {', '.join(N['hospitals'])}",
              f"{'Health line' if en else 'স্বাস্থ্য বাতায়ন'}: {_n(N['hotline'], lang)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------ printable commissioner brief
BRIEF_CSS = """
:root{--ink:#111827;--ink2:#374151;--mut:#6B7280;--rule:#E5E7EB;--indigo:#0E7490;--paper:#FFFFFF;--cloth:#F3F4F6}
*{box-sizing:border-box}html,body{margin:0;background:var(--cloth);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Nirmala UI","Noto Sans Bengali","Hind Siliguri",sans-serif;font-variant-numeric:tabular-nums}
.sheet{max-width:780px;margin:24px auto;background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:34px 40px 30px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding-bottom:14px;border-bottom:2px solid var(--indigo)}
.brand{color:var(--indigo);font-weight:700;font-size:15px}.meta{font-size:13px;color:var(--mut);text-align:right;line-height:1.5}
h1{font-size:28px;margin:18px 0 4px;line-height:1.1}.sub{color:var(--ink2);font-size:15px;margin:0}
.tier{display:inline-block;margin-top:10px;padding:3px 11px;border-radius:6px;color:#fff;font-weight:700;font-size:14px}
.facts{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--rule);border-radius:6px;margin:20px 0}
.facts div{padding:12px 14px}.facts div+div{border-left:1px solid var(--rule)}
.facts b{display:block;font-size:26px;line-height:1.1}.facts span{font-size:12.5px;color:var(--mut)}
h2{font-size:16px;margin:22px 0 8px}
table{width:100%;border-collapse:collapse;font-size:14.5px}th,td{padding:8px 10px;border-bottom:1px solid var(--rule);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:500;font-size:13px}
td.short{color:#BE123C;font-weight:700}
.alert{margin:16px 0;padding:11px 14px;border-radius:6px;font-size:14.5px;line-height:1.5}
.alert.critical{background:#F6E6E4;border:1px solid #DDA9A6;color:#5E1215}.alert.partial{background:#FBF1DE;border:1px solid #E5C784;color:#5A3D00}
.alert.none{background:#E7EEE1;border:1px solid #B7CBA9;color:#26401A}
.action{font-size:15px;line-height:1.55}
ul{margin:6px 0 0;padding-left:18px;font-size:14px;line-height:1.6}
.sign{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:34px;font-size:13px;color:var(--mut)}
.sign div{border-top:1px solid var(--ink2);padding-top:6px}
footer{margin-top:22px;font-size:11.5px;color:var(--mut);border-top:1px dashed var(--rule);padding-top:8px}
.print{position:fixed;right:18px;top:18px;background:var(--indigo);color:#fff;border:0;border-radius:6px;padding:9px 14px;font:inherit;font-size:14px;cursor:pointer}
@media print{html,body{background:#fff}.sheet{border:0;margin:0;max-width:none;padding:0}.print{display:none}@page{size:A4;margin:16mm}}
"""
TIER_HEX = {"Low": "#15803D", "Medium": "#A16207", "High": "#C2410C", "Critical": "#BE123C"}   # print-dark versions of the app tier colours


def brief_html(plan: dict, lang: str = "en", standalone_fonts: bool = True) -> str:
    N = notice(plan, lang)
    en = lang == "en"
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(s['label'])}</td><td>{_n(s['required'], lang)}</td><td>{_n(s['allocated'], lang)}</td>"
        f"<td class=\"{'short' if s['deficit'] else ''}\">{_n(s['deficit'], lang)}</td><td>{_n(s['coverage'], lang, 1)}%</td></tr>"
        for s in N["supplies"])
    lvl = {"none": ("Supplies fully covered at the current regional stock.", "বর্তমান আঞ্চলিক মজুদে সরবরাহ পুরোপুরি মেটানো যাচ্ছে।"),
           "partial": ("Partial supply deficit: request top-up from the city store.", "আংশিক ঘাটতি: সিটি স্টোর থেকে বাড়তি সরবরাহ চান।"),
           "critical": ("CRITICAL supply deficit: less than half of at least one item can be supplied. Escalate today.",
                        "গুরুতর ঘাটতি: অন্তত একটি সামগ্রীর অর্ধেকও দেওয়া যাচ্ছে না। আজই ঊর্ধ্বতনকে জানান।")}
    overflow = ""
    if N["overflow"] and N["overflow"].get("send_to"):
        overflow = ("<h2>" + ("Bed overflow routing" if en else "অতিরিক্ত রোগী স্থানান্তর") + "</h2><ul>" +
                    "".join(f"<li>{e(d['ward_id'])}: {_n(d['beds'], lang)}</li>" for d in N["overflow"]["send_to"]) + "</ul>")
    issued = datetime.now().strftime("%Y-%m-%d %H:%M")
    lab = (lambda a, b: a if en else b)
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(N['city'])} {e(N['ward'])}: {lab('Commissioner brief', 'কমিশনার ব্রিফ')}</title><style>{BRIEF_CSS}</style></head><body>
<button class="print" onclick="window.print()">{lab('Print / save PDF', 'প্রিন্ট / PDF')}</button>
<main class="sheet">
<header><div class="brand">Shotorko সতর্ক · {lab('Dengue dispatch brief', 'ডেঙ্গু ডিসপ্যাচ ব্রিফ')}</div>
<div class="meta">{lab('Forecast issued for week of', 'পূর্বাভাসের সপ্তাহ')} {e(N['issued'])}<br>{lab('Horizon', 'সময়সীমা')}: {_n(N['h'], lang)} {lab('weeks', 'সপ্তাহ')} · {lab('Priority', 'অগ্রাধিকার')} #{_n(N['priority_rank'], lang)} · {lab('Stock', 'মজুদ')} {_n(int(N['stock_pct']), lang)}%</div></header>
<h1>{e(N['city'])} · {e(N['ward'])}</h1>
<p class="sub">{lab('Expected peak week', 'প্রত্যাশিত সপ্তাহ')}: {e(N['week'])}</p>
<span class="tier" style="background:{TIER_HEX[N['tier']]}">{e(N['tier_label'])} {lab('risk', 'ঝুঁকি')}</span>
<div class="facts"><div><b>{N['cases']}</b><span>{lab('expected cases', 'প্রত্যাশিত রোগী')}</span></div>
<div><b>{N['range']}</b><span>{lab('80% range', '৮০% পরিসর')}</span></div>
<div><b>{_n(N['spray_teams'], lang)}</b><span>{lab('spray teams assigned', 'নির্ধারিত স্প্রে দল')}</span></div></div>
<h2>{lab('Supplies', 'সরবরাহ')}</h2>
<table><thead><tr><th>{lab('Item', 'সামগ্রী')}</th><th>{lab('Required', 'প্রয়োজন')}</th><th>{lab('Allocated', 'বরাদ্দ')}</th><th>{lab('Deficit', 'ঘাটতি')}</th><th>{lab('Covered', 'পূরণ')}</th></tr></thead><tbody>{rows}</tbody></table>
<div class="alert {N['deficit_level']}">{lvl[N['deficit_level']][0 if en else 1]}</div>
{overflow}
<h2>{lab('Action for the ward team', 'ওয়ার্ড দলের করণীয়')}</h2><p class="action">{e(N['action'])}</p>
<h2>{lab('Refer patients to', 'রোগী পাঠান')}</h2><ul>{''.join(f'<li>{e(h)}</li>' for h in N['hospitals'])}<li>{lab('Health line', 'স্বাস্থ্য বাতায়ন')}: {_n(N['hotline'], lang)}</li></ul>
<div class="sign"><div>{lab('Ward commissioner', 'ওয়ার্ড কমিশনার')}</div><div>{lab('Health officer', 'স্বাস্থ্য কর্মকর্তা')}</div></div>
<footer>{lab('Generated offline by Shotorko from the local forecast model; no internet or AI service used.', 'সতর্ক স্থানীয় মডেল থেকে অফলাইনে তৈরি; ইন্টারনেট বা এআই সেবা ব্যবহার হয়নি।')} {lab('Supply plan source', 'সরবরাহ পরিকল্পনার উৎস')}: {e(N['source'])} · {issued}</footer>
</main></body></html>"""
