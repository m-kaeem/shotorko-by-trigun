"""Requirement 5: the offline kill-switch.

Every test here runs with the network cut at the socket level (only this machine's loopback is allowed)
and with SHOTORKO_OFFLINE=1. Forecasting, supply plans, dispatch notices in every channel, the SMS
outbox and the sample exports must all keep working, and nothing may answer with a 500.
"""
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ("127.0.0.1", "::1", "localhost")


class NetworkBlocked(OSError):
    pass


@pytest.fixture()
def offline(monkeypatch, tmp_path):
    attempts = []
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in LOCAL:
            attempts.append(host)
            raise NetworkBlocked(f"network blocked in offline test: {host}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *a, **k):
        if host not in LOCAL:
            attempts.append(host)
            raise socket.gaierror(f"DNS blocked in offline test: {host}")
        return real_getaddrinfo(host, *a, **k)

    real_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setenv("SHOTORKO_OFFLINE", "1")
    monkeypatch.setenv("SMS_MODE", "dry_run")
    monkeypatch.setenv("SMS_OVERRIDE_TO", "01700000000")   # fake demo number; dry run never sends
    from src import offline as O
    from src import sms_gateway as G
    monkeypatch.setattr(G, "OUTBOX_PATH", tmp_path / "outbox.sqlite")
    O._PROBE.update(at=0.0, ok=None)
    return attempts


@pytest.fixture()
def client(offline):
    from src.api import app
    return TestClient(app)


def test_health_reports_offline(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["offline_forced"] is True and h["internet"] is False
    assert h["llm_calls"] == 0


def test_forecast_and_ward_run_locally(client):
    assert client.get("/api/meta").status_code == 200
    fc = client.get("/api/forecast", params={"date": "2025-07-07"}).json()
    assert len(fc["wards"]) == 60
    w = client.get("/api/ward/DNCC_Ward_01", params={"date": "2025-07-07", "h": 3}).json()
    assert w["drivers"] and w["history"]


def test_supply_plans_run_locally(client):
    p = client.get("/api/plan", params={"date": "2025-07-07", "h": 3, "stock_pct": 50}).json()
    assert len(p["plans"]) == 60
    for x in p["plans"]:
        for k in ("saline_bags", "diagnostic_kits", "hospital_beds"):
            assert 0 <= x["allocated"][k] <= x["required"][k]
            assert x["deficit"][k] == x["required"][k] - x["allocated"][k]
    assert any(x["deficit_level"] != "none" for x in p["plans"])   # 50% stock must create deficits


@pytest.mark.parametrize("lang", ["en", "bn"])
def test_dispatch_every_channel(client, lang):
    q = {"date": "2025-07-07", "h": 3, "stock_pct": 70, "lang": lang}
    d = client.get("/api/dispatch/DSCC_Ward_06", params=q).json()
    assert d["sms"]["text"] and d["whatsapp"]
    if lang == "en":
        assert d["sms"]["chars"] <= 160 and d["sms"]["segments"] == 1
    brief = client.get("/api/dispatch/DSCC_Ward_06", params={**q, "channel": "brief"})
    assert brief.status_code == 200 and "<html" in brief.text
    assert not re.search(r"(src|href)=\"https?://", brief.text), "brief must not load anything from the internet"
    assert client.get("/api/dispatch", params=q).json()["count"] > 0


def test_live_weather_degrades_without_500(client):
    r = client.get("/api/forecast", params={"mode": "live"})
    assert r.status_code in (200, 503)   # cached weather, or a clean "unavailable" that the UI turns into replay
    if r.status_code == 200:              # offline it can only be a saved copy, and the answer must say so and how old it is
        w = r.json()["weather"]
        assert w["source"] in ("offline_cache", "cache") and w["fetched_at"] and w["age_hours"] >= 0


def test_sms_queue_never_touches_the_network(client):
    r = client.post("/api/sms/send", json={"ward_id": "DSCC_Ward_06", "date": "2025-07-07", "h": 3, "lang": "en"}).json()
    assert r["queued"] == 1
    assert client.get("/api/sms/status").json()["mode"] == "dry_run"
    assert client.get("/api/sms/outbox").status_code == 200


def test_export_samples_offline(offline, tmp_path):
    from src import export_samples
    export_samples.main(["--out", str(tmp_path / "exports"), "--min-tier", "Critical"])
    out = tmp_path / "exports"
    assert any((out / "sms").iterdir()) and any((out / "briefs").iterdir())
    assert (out / "README.md").exists()


def test_no_internet_attempts_were_made(client, offline):
    for path in ("/api/health", "/api/forecast", "/api/plan", "/api/dispatch"):
        client.get(path)
    assert offline == [], f"unexpected network attempts: {offline}"


def test_web_pages_load_nothing_from_the_internet():
    for f in ("index.html", "dispatch.html", "app.js", "dispatch.js", "ops.js", "sim.js", "styles.css", "dispatch.css",
              "ops.css", "sim.css", "fonts/fonts.css"):
        text = (ROOT / "web" / f).read_text(encoding="utf-8")
        urls = [u for u in re.findall(r"https?://[^\s\"')]+", text) if "w3.org" not in u]
        assert not urls, f"{f} references the internet: {urls}"


def test_in_app_offline_switch(client):
    from src import offline as O
    try:
        on = client.post("/api/offline", json={"on": True}).json()
        assert on["offline_forced"] is True and on["internet"] is False
        assert client.get("/api/health").json()["offline_forced"] is True
        assert client.get("/api/forecast", params={"date": "2025-07-07"}).status_code == 200
        off = client.post("/api/offline", json={"on": False}).json()
        assert off["offline_forced"] is False
    finally:
        O._RUNTIME["forced"] = None
