"""Automatic SMS sending for dispatch notices, through an Android phone used as the SMS gateway.

How it works
    Shotorko --HTTPS--> SMS Gateway cloud relay (api.sms-gate.app) --> the phone   (cloud route: any network)
    Shotorko --HTTP over Wi-Fi--> the phone's local server                          (local route: same Wi-Fi)
    the phone --SIM--> ward officers' phones
The phone app "SMS Gateway for Android" is free and open source (github.com/capcom6/android-sms-gateway).
Cloud route: the phone keeps a connection to the free relay, so Shotorko can run anywhere (a hosted URL, a
laptop on another network) and still reach it; both sides just need internet. Local route: same Wi-Fi or the
phone's hotspot, no internet at all. When both are configured, cloud is tried first and local is the backup.

Modes (set in .env, see .env.example)
    SMS_MODE=dry_run   default: every message is logged to the outbox as "dry-run" and nothing is sent
    SMS_MODE=android   real sending through the phone at SMS_GATEWAY_URL

Every message goes through a local outbox (data/outbox.sqlite): queued -> sending -> sent -> delivered,
or failed with the reason. One background worker sends the queue with a short pause between messages
(Android limits how fast an app may send SMS), retries failures, and simply waits while the phone is
unreachable, so the web app never crashes or blocks because of SMS.
"""
from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import threading
import time
from datetime import datetime

from . import config as C

ENV_PATH = C.ROOT / ".env"
OUTBOX_PATH = C.ROOT / "data" / "outbox.sqlite"
CONTACTS_PATH = C.ROOT / "data" / "ward_contacts.csv"
CONTACTS_EXAMPLE = C.ROOT / "data" / "ward_contacts.example.csv"
MAX_ATTEMPTS = 3


# ------------------------------------------------------------------ settings
from .offline import load_env as _load_env   # noqa: E402  (shared .env reader)

_load_env()


def settings() -> dict:
    return {
        "mode": os.environ.get("SMS_MODE", "dry_run").strip().lower(),
        "url": os.environ.get("SMS_GATEWAY_URL", "").rstrip("/"),
        "user": os.environ.get("SMS_GATEWAY_USER", ""),
        "password": os.environ.get("SMS_GATEWAY_PASS", ""),
        "cloud_url": os.environ.get("SMS_CLOUD_URL", "https://api.sms-gate.app/3rdparty/v1").rstrip("/"),
        "cloud_user": os.environ.get("SMS_CLOUD_USER", ""),
        "cloud_password": os.environ.get("SMS_CLOUD_PASS", ""),
        "override_to": os.environ.get("SMS_OVERRIDE_TO", "").strip(),   # demo: send every message to this one number
        "pause_s": float(os.environ.get("SMS_PAUSE_SECONDS", "2")),
    }


def public_settings() -> dict:
    s = settings()
    return {"mode": s["mode"], "gateway": s["url"] or None, "routes": [r["name"] for r in routes()], "override_to": _mask(normalise(s["override_to"])) if s["override_to"] else None,
            "contacts_with_phone": sum(1 for c in contacts().values() if c["phone"])}


def _mask(num: str) -> str:
    return num[:5] + "•" * max(0, len(num) - 8) + num[-3:] if len(num) > 8 else "•" * len(num)


# ------------------------------------------------------------------ contacts
def contacts() -> dict:
    """{ward_id: {"name":..., "phone": "+8801..."}}. data/ward_contacts.csv holds real numbers and is not committed."""
    path = CONTACTS_PATH if CONTACTS_PATH.exists() else CONTACTS_EXAMPLE
    out = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[r["ward_id"].strip()] = {"name": (r.get("officer_name") or "").strip(), "phone": normalise((r.get("phone") or "").strip())}
    return out


def normalise(num: str) -> str:
    """Bangladeshi mobile numbers to +8801XXXXXXXXX; anything else is returned trimmed (or '' if empty)."""
    d = "".join(ch for ch in num if ch.isdigit() or ch == "+")
    if not d:
        return ""
    if d.startswith("+"):
        return d
    if d.startswith("880"):
        return "+" + d
    if d.startswith("01") and len(d) == 11:
        return "+88" + d
    return d


def valid_phone(num: str) -> bool:
    """A Bangladeshi mobile (+8801[3-9] and 8 digits) or another international number (+ and 8 to 15 digits)."""
    if num.startswith("+880"):
        return len(num) == 14 and num[4] == "1" and num[5] in "3456789" and num[1:].isdigit()
    return num.startswith("+") and num[1:].isdigit() and 8 <= len(num) - 1 <= 15


_CONTACTS_LOCK = threading.Lock()


def save_contact(ward_id: str, name: str, phone: str) -> dict:
    """Store (or, with an empty phone, remove) one ward's officer number in data/ward_contacts.csv.
    The file is created from the example on first save; it holds real numbers and is never committed."""
    phone = normalise(phone)
    if phone and not valid_phone(phone):
        raise ValueError("not a valid mobile number (example: 01712345678 or +8801712345678)")
    name = " ".join(name.split())[:60]
    with _CONTACTS_LOCK:
        src = CONTACTS_PATH if CONTACTS_PATH.exists() else CONTACTS_EXAMPLE
        rows = []
        if src.exists():
            with open(src, newline="", encoding="utf-8-sig") as f:
                rows = [dict(r) for r in csv.DictReader(f)]
        hit = next((r for r in rows if r.get("ward_id", "").strip() == ward_id), None)
        if hit is None:
            raise KeyError(ward_id)
        hit["officer_name"], hit["phone"] = name, phone
        tmp = CONTACTS_PATH.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ward_id", "city", "officer_name", "phone"], extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(CONTACTS_PATH)
    return {"ward_id": ward_id, "name": name, "phone": _mask(phone) if phone else "", "has_phone": bool(phone)}


def contacts_public() -> dict:
    """What the dispatch page may show: names and masked numbers only."""
    s = settings()
    fb = normalise(s["override_to"]) if s["override_to"] else ""
    return {"fallback": _mask(fb) if fb else None,
            "wards": {k: {"name": v["name"], "phone": _mask(v["phone"]) if v["phone"] else "", "has_phone": bool(v["phone"])}
                      for k, v in contacts().items()}}


def recipient_for(ward_id: str) -> tuple[str, str]:
    """(phone, why) for a ward: its own officer's number, else the fallback (demo) number, else ('', reason)."""
    c = contacts().get(ward_id)
    if c and c["phone"]:
        return c["phone"], "contact"
    s = settings()
    if s["override_to"]:
        return normalise(s["override_to"]), "fallback"
    return "", "no phone number saved for this ward"


# ------------------------------------------------------------------ outbox
_DB_LOCK = threading.Lock()


def _db() -> sqlite3.Connection:
    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OUTBOX_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, ward_id TEXT, phone TEXT, text TEXT,
        week TEXT, lang TEXT, status TEXT, mode TEXT, provider_id TEXT, error TEXT, attempts INTEGER DEFAULT 0,
        dedupe_key TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_dedupe ON messages(dedupe_key)")
    if "route" not in {r[1] for r in con.execute("PRAGMA table_info(messages)")}:   # older outboxes: add the column
        con.execute("ALTER TABLE messages ADD COLUMN route TEXT")
    return con


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def enqueue(ward_id: str, text: str, week: str, lang: str, force: bool = False) -> dict:
    """Add one SMS to the outbox. The same text to the same ward for the same week is not queued twice unless force."""
    phone, why = recipient_for(ward_id)
    mode = settings()["mode"]
    key = hashlib.sha1(f"{ward_id}|{week}|{phone}|{text}".encode("utf-8")).hexdigest()[:16]
    with _DB_LOCK, _db() as con:
        if not force:
            dup = con.execute("SELECT * FROM messages WHERE dedupe_key=? AND status NOT IN ('failed','skipped') ORDER BY id DESC LIMIT 1",
                              (key,)).fetchone()
            if dup:
                return {**dict(dup), "duplicate": True}
        status, err = ("queued", None) if phone else ("skipped", why)
        cur = con.execute("INSERT INTO messages (created, updated, ward_id, phone, text, week, lang, status, mode, error, dedupe_key) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (_now(), _now(), ward_id, phone, text, week, lang, status, mode, err, key))
        row = con.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
    _wake()
    return {**dict(row), "duplicate": False}


def outbox(limit: int = 200, week: str | None = None) -> list[dict]:
    with _DB_LOCK, _db() as con:
        q, args = "SELECT * FROM messages", []
        if week:
            q, args = q + " WHERE week=?", [week]
        rows = con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [{**dict(r), "phone": _mask(r["phone"]) if r["phone"] else ""} for r in rows]


def _update(mid: int, **f) -> None:
    f["updated"] = _now()
    cols = ", ".join(f"{k}=?" for k in f)
    with _DB_LOCK, _db() as con:
        con.execute(f"UPDATE messages SET {cols} WHERE id=?", (*f.values(), mid))


# ------------------------------------------------------------------ the Android gateway (cloud relay + local Wi-Fi)
def routes() -> list[dict]:
    """Configured ways to reach the phone, in the order they are tried: cloud relay first, then local Wi-Fi."""
    s = settings()
    out = []
    if s["cloud_user"] and s["cloud_password"]:
        out.append({"name": "cloud", "url": s["cloud_url"], "user": s["cloud_user"], "password": s["cloud_password"]})
    if s["url"]:
        out.append({"name": "local", "url": s["url"], "user": s["user"], "password": s["password"]})
    return out


def _route(name: str) -> dict | None:
    return next((r for r in routes() if r["name"] == name), None)


def _session(route: dict):
    import requests
    sess = requests.Session()
    sess.auth = (route["user"], route["password"])
    return sess


def _check(route: dict) -> tuple[bool, str]:
    """(reachable, detail) for one route. Never raises."""
    try:
        sess = _session(route)
        if route["name"] == "cloud":   # the relay lists the phones registered to this account
            r = sess.get(route["url"] + "/devices", timeout=6)
            if r.status_code in (401, 403):
                return False, "cloud login rejected: check SMS_CLOUD_USER / SMS_CLOUD_PASS"
            if r.status_code == 200:
                try:
                    n = len(r.json())
                except ValueError:
                    n = 0
                return (n > 0), (f"cloud relay: {n} phone(s) registered" if n else "cloud relay: no phone registered yet")
            return r.status_code < 500, f"cloud relay answered ({r.status_code})"
        r = sess.get(route["url"] + "/health", timeout=4)
        return r.status_code < 500, f"phone answered on local Wi-Fi ({r.status_code})"
    except Exception as exc:   # phone off, different Wi-Fi, no internet...
        return False, f"{route['name']} not reachable: {type(exc).__name__}"


def gateway_status() -> dict:
    """Can at least one route reach the phone? Never raises."""
    s = settings()
    if s["mode"] != "android":
        return {"mode": s["mode"], "reachable": None, "detail": "dry run: nothing is sent", "route": None}
    rs = routes()
    if not rs:
        return {"mode": "android", "reachable": False, "route": None,
                "detail": "no route set: add SMS_CLOUD_USER/PASS (any network) or SMS_GATEWAY_URL (local Wi-Fi) to .env"}
    details = []
    for r in rs:
        ok, d = _check(r)
        details.append(d)
        if ok:
            return {"mode": "android", "reachable": True, "route": r["name"], "detail": d}
    return {"mode": "android", "reachable": False, "route": None, "detail": "; ".join(details)}


def _post(route: dict, phone: str, text: str) -> str:
    sess = _session(route)
    for path in ("/message", "/messages"):   # app versions differ in the path and the body shape
        r = sess.post(route["url"] + path, json={"textMessage": {"text": text}, "phoneNumbers": [phone]}, timeout=15)
        if r.status_code == 400:
            r = sess.post(route["url"] + path, json={"message": text, "phoneNumbers": [phone]}, timeout=15)
        if r.status_code != 404:
            break
    if r.status_code >= 400:
        raise RuntimeError(f"{route['name']} gateway HTTP {r.status_code}: {r.text[:160]}")
    return str(r.json().get("id", ""))


def _send_android(phone: str, text: str) -> tuple[str, str]:
    """Send through the first route that works; returns (route name, gateway message id). Raises if all fail."""
    errors = []
    for route in routes():
        try:
            return route["name"], _post(route, phone, text)
        except Exception as exc:
            errors.append(str(exc)[:120])
    raise RuntimeError(" | ".join(errors) or "no SMS route configured")


def _state_android(route_name: str, provider_id: str) -> tuple[str, str | None]:
    """Map the phone's state to ours: Pending/Processed -> sending, Sent -> sent, Delivered -> delivered, Failed -> failed."""
    route = _route(route_name or "local") or (routes() or [None])[0]
    if route is None:
        return "sending", None
    sess = _session(route)
    for path in ("/message/", "/messages/"):
        r = sess.get(f"{route['url']}{path}{provider_id}", timeout=8)
        if r.status_code != 404:
            break
    if r.status_code >= 400:
        return "sending", None
    j = r.json()
    st = str(j.get("state", "")).lower()
    err = None
    for rec in j.get("recipients") or []:
        err = rec.get("error") or err
    return {"sent": "sent", "delivered": "delivered", "failed": "failed"}.get(st, "sending"), err


# ------------------------------------------------------------------ background worker
_EVENT = threading.Event()
_WORKER: threading.Thread | None = None


def _wake() -> None:
    global _WORKER
    if _WORKER is None or not _WORKER.is_alive():
        _WORKER = threading.Thread(target=_loop, name="sms-outbox", daemon=True)
        _WORKER.start()
    _EVENT.set()


def _loop() -> None:
    while True:
        try:
            busy = _tick()
        except Exception:   # the worker must never die
            busy = False
        _EVENT.wait(timeout=2 if busy else 15)
        _EVENT.clear()


def _tick() -> bool:
    """Send queued messages and refresh in-flight ones. Returns True while there is still work."""
    s = settings()
    with _DB_LOCK, _db() as con:
        queued = con.execute("SELECT * FROM messages WHERE status='queued' ORDER BY id LIMIT 20").fetchall()
        inflight = con.execute("SELECT * FROM messages WHERE status IN ('sending','sent') AND provider_id IS NOT NULL "
                               "AND created > datetime('now','-2 days','localtime') ORDER BY id LIMIT 50").fetchall()
    if s["mode"] != "android":
        for m in queued:
            _update(m["id"], status="dry-run", mode=s["mode"], attempts=m["attempts"] + 1)
        return False
    if (queued or inflight) and not gateway_status()["reachable"]:
        return True   # phone away: keep everything queued and try again shortly
    for m in queued:
        try:
            route, pid = _send_android(m["phone"], m["text"])
            _update(m["id"], status="sending", provider_id=pid, mode="android", route=route, attempts=m["attempts"] + 1, error=None)
        except Exception as exc:
            n = m["attempts"] + 1
            _update(m["id"], status="failed" if n >= MAX_ATTEMPTS else "queued", attempts=n, error=str(exc)[:200])
        time.sleep(max(0.0, s["pause_s"]))
    for m in inflight:
        try:
            st, err = _state_android(m["route"], m["provider_id"])
            if st != m["status"]:
                _update(m["id"], status=st, error=err)
        except Exception:
            pass
    return bool(queued or inflight)


def retry_failed() -> int:
    with _DB_LOCK, _db() as con:
        n = con.execute("UPDATE messages SET status='queued', attempts=0, error=NULL, updated=? WHERE status='failed'", (_now(),)).rowcount
    _wake()
    return n
