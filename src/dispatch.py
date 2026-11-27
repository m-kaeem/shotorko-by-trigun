"""Requirement 4: ward-specific dispatch notices (SMS, WhatsApp, printable office order).

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


# ------------------------------------------------------------------ printable brief (Bangladesh government office-order format)
CORP = {   # issuing office for each city: (English, Bangla)
    "Dhaka North": ("Dhaka North City Corporation", "ঢাকা উত্তর সিটি কর্পোরেশন", "Dhaka", "ঢাকা"),
    "Dhaka South": ("Dhaka South City Corporation", "ঢাকা দক্ষিণ সিটি কর্পোরেশন", "Dhaka", "ঢাকা"),
    "Chattogram": ("Chattogram City Corporation", "চট্টগ্রাম সিটি কর্পোরেশন", "Chattogram", "চট্টগ্রাম"),
    "Gazipur": ("Gazipur City Corporation", "গাজীপুর সিটি কর্পোরেশন", "Gazipur", "গাজীপুর"),
    "Narayanganj": ("Narayanganj City Corporation", "নারায়ণগঞ্জ সিটি কর্পোরেশন", "Narayanganj", "নারায়ণগঞ্জ"),
}
BRIEF_CSS = """
:root{--ink:#111;--mut:#555;--rule:#222;--paper:#fff;--cloth:#E9E9E6}
*{box-sizing:border-box}html,body{margin:0;background:var(--cloth);color:var(--ink)}
body{font-family:"Book Antiqua","Palatino Linotype",Palatino,Georgia,"Nirmala UI","Noto Serif Bengali","Hind Siliguri",serif;font-variant-numeric:tabular-nums}
.bn{font-family:"Nirmala UI","Noto Sans Bengali","Hind Siliguri",sans-serif}
.sheet{width:210mm;min-height:297mm;margin:24px auto;background:var(--paper);box-shadow:0 1px 6px rgba(0,0,0,.18);padding:16mm 18mm 12mm;display:flex;flex-direction:column}
.lh{display:grid;grid-template-columns:1fr 100px 1fr;align-items:center;gap:14px;text-align:center}
.lh .bn{font-size:17px;line-height:1.45;font-weight:600}
.lh .en{font-size:14px;line-height:1.45;font-weight:700;letter-spacing:.02em;text-transform:uppercase;font-family:"Times New Roman",Times,serif}
.seal{width:98px;height:98px;padding:16px;border-radius:50%;border:2px solid var(--ink);display:grid;place-items:center;text-align:center;position:relative}
.seal::before{content:"";position:absolute;inset:5px;border-radius:50%;border:1px solid var(--ink)}
.seal b{font-size:15px;letter-spacing:.06em}.lh .seal small{display:block;font-size:9px;font-weight:600;line-height:1.25;margin-top:3px;white-space:nowrap}
.ref{display:flex;justify-content:space-between;gap:16px;margin-top:26px;font-size:15px}
h1{text-align:center;font-size:21px;margin:26px 0 4px;text-decoration:underline;text-underline-offset:4px}
.subj{margin:14px 0 4px;font-size:15.5px;line-height:1.55}
.body{font-size:15.5px;line-height:1.65;text-align:justify}
.body p.lead{text-indent:48px;margin:12px 0}
ol.paras{list-style:none;margin:0;padding:0}
ol.paras>li{display:grid;grid-template-columns:44px 1fr;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:14.5px;margin:8px 0 2px}
th,td{border:1px solid var(--rule);padding:5px 9px;text-align:right}
th:first-child,td:first-child{text-align:left}th{font-weight:700;background:#F4F4F2}
td.short{font-weight:700;text-decoration:underline}
ul.plain{margin:4px 0 0;padding-left:20px}
.sign{align-self:flex-end;text-align:center;margin-top:44px;min-width:250px;font-size:15px;line-height:1.5}
.sign .line{border-top:1px dotted var(--ink);margin-bottom:4px}
.dist{margin-top:26px;font-size:15px;line-height:1.55}
.dist h2{font-size:15.5px;margin:0 0 4px;text-decoration:underline;text-underline-offset:3px}
.dist ol{margin:0;padding:0;list-style:none}
.grow{flex:1}
footer{margin-top:24px;border-top:1.5px solid var(--ink);padding-top:6px;text-align:center;font-size:12px;color:var(--mut);line-height:1.5}
.print{position:fixed;right:18px;top:18px;background:#1F2937;color:#fff;border:0;border-radius:6px;padding:9px 14px;font:14px system-ui,sans-serif;cursor:pointer}
@media print{html,body{background:#fff}.sheet{width:auto;min-height:calc(297mm - 24mm);margin:0;box-shadow:none;padding:0}.print{display:none}@page{size:A4;margin:12mm 16mm}}
"""


def _memo_no(plan: dict, lang: str) -> str:
    """Reference number in the dotted style of government memos, e.g. DSCC.HLTH.DNG.2025.07.07.W06.H2."""
    d = pd.Timestamp(plan["date"])
    s = f"{CITY_CODE.get(plan['city'], 'CC')}.HLTH.DNG.{d.year}.{d.month:02d}.{d.day:02d}.W{_ward_no(plan['ward_id'])}.H{plan['h']}"
    return s.translate(BN_DIG) if lang == "bn" else s


def brief_html(plan: dict, lang: str = "en", standalone_fonts: bool = True) -> str:
    """One A4 page in the Bangladesh government office-order layout: bilingual letterhead, memo number
    and date, underlined title, subject, numbered paragraphs, signature block and distribution list."""
    N = notice(plan, lang)
    en = lang == "en"
    e = html.escape
    lab = (lambda a, b: a if en else b)
    num = (lambda i: _n(f"{i:02d}", lang) + ".")
    corp_en, corp_bn, town_en, town_bn = CORP.get(plan["city"], (plan["city"], N["city"], plan["city"], N["city"]))
    corp = corp_en if en else corp_bn

    rows = "".join(
        f"<tr><td>{e(s['label'])}</td><td>{_n(s['required'], lang)}</td><td>{_n(s['allocated'], lang)}</td>"
        f"<td class=\"{'short' if s['deficit'] else ''}\">{_n(s['deficit'], lang)}</td><td>{_n(s['coverage'], lang, 1)}%</td></tr>"
        for s in N["supplies"])
    lvl = {"none": ("Supplies are fully covered at the current regional stock.", "বর্তমান আঞ্চলিক মজুদে সরবরাহ পুরোপুরি মেটানো যাচ্ছে।"),
           "partial": ("There is a partial supply deficit. The ward team shall request a top-up from the city store.",
                       "আংশিক সরবরাহ ঘাটতি রয়েছে। ওয়ার্ড দল সিটি স্টোর থেকে বাড়তি সরবরাহ চাইবে।"),
           "critical": ("There is a CRITICAL supply deficit: less than half of at least one item can be supplied. This shall be escalated today.",
                        "গুরুতর সরবরাহ ঘাটতি রয়েছে: অন্তত একটি সামগ্রীর অর্ধেকও দেওয়া যাচ্ছে না। আজই ঊর্ধ্বতন কর্তৃপক্ষকে জানাতে হবে।")}

    paras = [
        f"{lab('Action', 'করণীয়')}: {e(N['action'])} "
        + lab(f"{N['spray_teams']} spray team{' has' if N['spray_teams'] == 1 else 's have'} been assigned to the ward for this round.",
              f"এই দফায় ওয়ার্ডে {_n(N['spray_teams'], lang)}টি স্প্রে দল নির্ধারণ করা হয়েছে।"),
        lab("Supplies for the ward (required against allocated) are as follows:", "ওয়ার্ডের সরবরাহ (প্রয়োজন ও বরাদ্দ) নিম্নরূপ:")
        + f"<table><thead><tr><th>{lab('Item', 'সামগ্রী')}</th><th>{lab('Required', 'প্রয়োজন')}</th><th>{lab('Allocated', 'বরাদ্দ')}</th>"
          f"<th>{lab('Deficit', 'ঘাটতি')}</th><th>{lab('Covered', 'পূরণ')}</th></tr></thead><tbody>{rows}</tbody></table>"
        + lvl[N["deficit_level"]][0 if en else 1],
    ]
    if N["overflow"] and N["overflow"].get("send_to"):
        dest = ", ".join(f"{e(d['ward_id'])} ({_n(d['beds'], lang)} {lab('beds', 'শয্যা')})" for d in N["overflow"]["send_to"])
        paras.append(lab("Patients beyond the ward's bed capacity shall be referred to: ", "ওয়ার্ডের শয্যার অতিরিক্ত রোগী পাঠাতে হবে: ") + dest + ".")
    paras.append(lab("Suspected dengue patients shall be referred to ", "সন্দেহভাজন ডেঙ্গু রোগীদের পাঠাতে হবে: ")
                 + lab(" or ", " অথবা ").join(e(h) for h in N["hospitals"])
                 + lab(f". Health line: {N['hotline']}.", f"। স্বাস্থ্য বাতায়ন: {_n(N['hotline'], lang)}।"))
    paras.append(lab("This order takes effect once it is signed by the competent authority.",
                     "যথাযথ কর্তৃপক্ষের স্বাক্ষরের পর থেকে এই আদেশ কার্যকর হবে।"))
    body = "".join(f"<li><span>{num(i)}</span><div>{p}</div></li>" for i, p in enumerate(paras, start=2))

    dist = [lab(f"Ward Councillor, {N['ward']}, {corp}", f"ওয়ার্ড কাউন্সিলর, {N['ward']}, {corp}"),
            lab("Ward health and vector-control supervisor", "ওয়ার্ড স্বাস্থ্য ও মশক নিয়ন্ত্রণ সুপারভাইজার"),
            lab("City store (medical supplies)", "সিটি স্টোর (চিকিৎসা সামগ্রী)"),
            lab("Notice board", "নোটিশ বোর্ড"),
            lab("Office file", "অফিস নথি")]
    issued = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(N['city'])} {e(N['ward'])}: {lab('Office order', 'অফিস আদেশ')}</title><style>{BRIEF_CSS}</style></head><body>
<button class="print" onclick="window.print()">{lab('Print / save PDF', 'প্রিন্ট / PDF')}</button>
<main class="sheet">
<header class="lh">
  <div class="bn">{e(corp_bn)}<br>স্বাস্থ্য বিভাগ<br>{e(town_bn)}, বাংলাদেশ</div>
  <div class="seal" aria-label="{lab('Office seal', 'দপ্তরের সিল')}"><div><b>{e(N['city_code'])}</b><small class="bn">স্বাস্থ্য বিভাগ</small></div></div>
  <div class="en">{e(corp_en)}<br>Health Department<br>{e(town_en)}, Bangladesh</div>
</header>
<div class="ref"><span>{lab('No.', 'স্মারক নং')} {_memo_no(plan, lang)}</span><span>{lab('Date', 'তারিখ')}: {e(N['issued'])}</span></div>
<h1>{lab('Office Order', 'অফিস আদেশ')}</h1>
<p class="subj"><b>{lab('Subject', 'বিষয়')}:</b> {lab(f"Dengue response for {e(N['ward'])}, {e(N['city'])}, for the week of {e(N['week'])}.",
                                                       f"{e(N['city'])} {e(N['ward'])}-এ {e(N['week'])} সপ্তাহের ডেঙ্গু প্রতিরোধ কার্যক্রম।")}</p>
<div class="body">
<p class="lead">{lab(f"According to the Shotorko dengue forecast, {e(N['ward'])} of {e(corp)} is expected to have about <b>{N['cases']}</b> dengue cases in the week of {e(N['week'])} (likely range {N['range']}), {_n(N['h'], lang)} weeks from now. The ward's risk level is <b>{e(N['tier_label'])}</b> (score {_n(N['risk_score'], lang)}/100) and it is priority no. {_n(N['priority_rank'], lang)} in this round, with regional stock at {_n(int(N['stock_pct']), lang)}%.",
                    f"সতর্ক ডেঙ্গু পূর্বাভাস অনুযায়ী {e(corp)}-এর {e(N['ward'])}-এ {e(N['week'])} সপ্তাহে প্রায় <b>{N['cases']}</b> জন ডেঙ্গু রোগী হতে পারে (সম্ভাব্য পরিসর {N['range']}), যা এখন থেকে {_n(N['h'], lang)} সপ্তাহ পরে। ওয়ার্ডের ঝুঁকির মাত্রা <b>{e(N['tier_label'])}</b> (স্কোর {_n(N['risk_score'], lang)}/{_n(100, lang)}); এই দফায় অগ্রাধিকার ক্রম {_n(N['priority_rank'], lang)}, আঞ্চলিক মজুদ {_n(int(N['stock_pct']), lang)}%।")}</p>
<ol class="paras">{body}</ol>
</div>
<div class="sign"><div class="line"></div>({lab('Name', 'নাম')})<br>{lab('Chief Health Officer', 'প্রধান স্বাস্থ্য কর্মকর্তা')}<br>{e(corp)}</div>
<section class="dist"><h2>{lab('Distribution', 'বিতরণ')}:</h2><ol>{''.join(f'<li>{num(i)} {e(d)}</li>' for i, d in enumerate(dist, start=1))}</ol></section>
<div class="grow"></div>
<footer>{lab('Generated offline by Shotorko from the local forecast model; no internet or AI service used.', 'সতর্ক স্থানীয় মডেল থেকে অফলাইনে তৈরি; ইন্টারনেট বা এআই সেবা ব্যবহার হয়নি।')}<br>{lab('Supply plan source', 'সরবরাহ পরিকল্পনার উৎস')}: {e(N['source'])} · {lab('Printed', 'মুদ্রণ')} {issued}</footer>
</main></body></html>"""
