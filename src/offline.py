"""Offline resilience (Phase 2, Requirement 5).

Everything that matters runs on this machine: the trained model bundle, the dataset, the supply plans,
the dispatch templates and the web assets (deck.gl, map outlines and fonts are in the repo). The only
optional network uses are the live Open-Meteo weather mode (falls back to cache, then to replay) and the
SMS phone gateway (on the local Wi-Fi/hotspot, not the internet; unsent messages wait in the outbox).

    SHOTORKO_OFFLINE=1    force offline mode: never attempt any internet request (judging kill-switch)

Without the flag the app still notices a dead connection quickly (a 1.5 s probe, cached for 30 s) instead
of waiting on long HTTP timeouts.
"""
from __future__ import annotations

import os
import socket
import time

from . import config as C

ENV_PATH = C.ROOT / ".env"
_PROBE = {"at": 0.0, "ok": None}
PROBE_HOSTS = (("api.open-meteo.com", 443), ("1.1.1.1", 53))


def load_env() -> None:
    """Read KEY=VALUE lines from .env into the environment (no extra dependency; real env vars win)."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


_RUNTIME = {"forced": None}   # set from the UI's offline switch; None = follow SHOTORKO_OFFLINE


def forced_offline() -> bool:
    if _RUNTIME["forced"] is not None:
        return _RUNTIME["forced"]
    return os.environ.get("SHOTORKO_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def set_forced(on: bool) -> None:
    """The in-app switch: go offline (or back online) without restarting. Resets the connectivity probe."""
    _RUNTIME["forced"] = bool(on)
    _PROBE.update(at=0.0, ok=None)


def internet_ok(max_age_s: float = 30.0) -> bool:
    """Quick reachability check, cached. Always False when offline mode is forced."""
    if forced_offline():
        return False
    if _PROBE["ok"] is not None and time.time() - _PROBE["at"] < max_age_s:
        return _PROBE["ok"]
    ok = False
    for host, port in PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                ok = True
                break
        except OSError:
            continue
    _PROBE.update(at=time.time(), ok=ok)
    return ok


def status() -> dict:
    forced = forced_offline()
    online = internet_ok()
    return {"offline_forced": forced, "internet": online,
            "mode": "offline (forced)" if forced else ("online" if online else "offline (no internet)")}
