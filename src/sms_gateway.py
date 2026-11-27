"""Automatic SMS sending for dispatch notices, through an Android phone used as the SMS gateway.

How it works
    Shotorko --HTTP over Wi-Fi--> Android phone running "SMS Gateway for Android" (local server mode)
             --SIM--> ward officers' phones
The phone app is free and open source (github.com/capcom6/android-sms-gateway). The laptop and the phone
only need the same Wi-Fi or the phone's hotspot; no internet and no paid SMS account.

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
        "override_to": os.environ.get("SMS_OVERRIDE_TO", "").strip(),   # demo: send every message to this one number
        "pause_s": float(os.environ.get("SMS_PAUSE_SECONDS", "2")),
    }


def public_settings() -> dict:
    s = settings()
    return {"mode": s["mode"], "gateway": s["url"] or None, "override_to": _mask(normalise(s["override_to"])) if s["override_to"] else None,
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


def recipient_for(ward_id: str) -> tuple[str, str]:
    """(phone, why) for a ward: the demo override, the ward officer, or ('', reason)."""
    s = settings()
    if s["override_to"]:
        return normalise(s["override_to"]), "override"
    c = contacts().get(ward_id)
    if c and c["phone"]:
        return c["phone"], "contact"
    return "", "no phone number for this ward in data/ward_contacts.csv"


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


# ------------------------------------------------------------------ the Android gateway
def _session():
    import requests
    s = settings()
    sess = requests.Session()
    sess.auth = (s["user"], s["password"])
    return sess, s["url"]


def gateway_status() -> dict:
    """Is the phone reachable? Never raises."""
    s = settings()
    if s["mode"] != "android":
        return {"mode": s["mode"], "reachable": None, "detail": "dry run: nothing is sent"}
    if not s["url"]:
        return {"mode": "android", "reachable": False, "detail": "SMS_GATEWAY_URL is not set in .env"}
    try:
        sess, url = _session()
        r = sess.get(url + "/health", timeout=4)
        ok = r.status_code < 500
        return {"mode": "android", "reachable": ok, "detail": f"phone answered ({r.status_code})"}
    except Exception as exc:   # phone off, different Wi-Fi, app closed...
        return {"mode": "android", "reachable": False, "detail": f"phone not reachable: {type(exc).__name__}"}


def _send_android(phone: str, text: str) -> str:
    """POST one message to the phone; returns the gateway's message id. Raises on failure."""
    sess, url = _session()
    body = {"textMessage": {"text": text}, "phoneNumbers": [phone]}
    r = sess.post(url + "/message", json=body, timeout=10)
    if r.status_code == 400:   # older app versions take {"message": "..."} instead of textMessage
        r = sess.post(url + "/message", json={"message": text, "phoneNumbers": [phone]}, timeout=10)
    if r.status_code >= 400:
        raise RuntimeError(f"gateway HTTP {r.status_code}: {r.text[:160]}")
    return str(r.json().get("id", ""))


def _state_android(provider_id: str) -> tuple[str, str | None]:
    """Map the phone's state to ours: Pending/Processed -> sending, Sent -> sent, Delivered -> delivered, Failed -> failed."""
    sess, url = _session()
    r = sess.get(f"{url}/message/{provider_id}", timeout=6)
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
            pid = _send_android(m["phone"], m["text"])
            _update(m["id"], status="sending", provider_id=pid, mode="android", attempts=m["attempts"] + 1, error=None)
        except Exception as exc:
            n = m["attempts"] + 1
            _update(m["id"], status="failed" if n >= MAX_ATTEMPTS else "queued", attempts=n, error=str(exc)[:200])
        time.sleep(max(0.0, s["pause_s"]))
    for m in inflight:
        try:
            st, err = _state_android(m["provider_id"])
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
