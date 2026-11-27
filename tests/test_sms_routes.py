"""SMS routes: cloud relay first (phone reachable from any network), local Wi-Fi as the backup.
No real gateway is contacted: the per-route sender is replaced by a fake."""
import pytest


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    from src import sms_gateway as G
    monkeypatch.setattr(G, "OUTBOX_PATH", tmp_path / "outbox.sqlite")
    for k in ("SMS_CLOUD_USER", "SMS_CLOUD_PASS", "SMS_GATEWAY_URL", "SMS_GATEWAY_USER", "SMS_GATEWAY_PASS"):
        monkeypatch.delenv(k, raising=False)
    return G


def test_routes_order_cloud_then_local(gw, monkeypatch):
    monkeypatch.setenv("SMS_CLOUD_USER", "u"); monkeypatch.setenv("SMS_CLOUD_PASS", "p")
    monkeypatch.setenv("SMS_GATEWAY_URL", "http://192.168.0.10:8080")
    assert [r["name"] for r in gw.routes()] == ["cloud", "local"]
    assert gw.routes()[0]["url"].startswith("https://api.sms-gate.app")


def test_only_local_when_no_cloud_login(gw, monkeypatch):
    monkeypatch.setenv("SMS_GATEWAY_URL", "http://192.168.0.10:8080")
    assert [r["name"] for r in gw.routes()] == ["local"]


def test_cloud_failure_falls_back_to_local(gw, monkeypatch):
    monkeypatch.setenv("SMS_CLOUD_USER", "u"); monkeypatch.setenv("SMS_CLOUD_PASS", "p")
    monkeypatch.setenv("SMS_GATEWAY_URL", "http://192.168.0.10:8080")
    tried = []

    def fake_post(route, phone, text):
        tried.append(route["name"])
        if route["name"] == "cloud":
            raise RuntimeError("cloud gateway HTTP 503")
        return "msg-42"

    monkeypatch.setattr(gw, "_post", fake_post)
    assert gw._send_android("+8801700000000", "test") == ("local", "msg-42")
    assert tried == ["cloud", "local"]


def test_all_routes_failing_raises_with_reasons(gw, monkeypatch):
    monkeypatch.setenv("SMS_CLOUD_USER", "u"); monkeypatch.setenv("SMS_CLOUD_PASS", "p")
    monkeypatch.setattr(gw, "_post", lambda route, phone, text: (_ for _ in ()).throw(RuntimeError(f"{route['name']} down")))
    with pytest.raises(RuntimeError, match="cloud down"):
        gw._send_android("+8801700000000", "test")


def test_outbox_records_the_route(gw, monkeypatch):
    monkeypatch.setenv("SMS_MODE", "android"); monkeypatch.setenv("SMS_OVERRIDE_TO", "01700000000")
    monkeypatch.setenv("SMS_CLOUD_USER", "u"); monkeypatch.setenv("SMS_CLOUD_PASS", "p")
    monkeypatch.setenv("SMS_PAUSE_SECONDS", "0")
    monkeypatch.setattr(gw, "_wake", lambda: None)                       # drive the worker by hand
    monkeypatch.setattr(gw, "gateway_status", lambda: {"reachable": True})
    monkeypatch.setattr(gw, "_post", lambda route, phone, text: "abc")
    monkeypatch.setattr(gw, "_state_android", lambda route, pid: ("delivered", None))
    gw.enqueue("DSCC_Ward_06", "hello", "2025-07-28", "en")
    gw._tick(); gw._tick()
    m = gw.outbox(5)[0]
    assert m["route"] == "cloud" and m["status"] == "delivered"


def test_no_route_configured_reports_how_to_fix(gw, monkeypatch):
    monkeypatch.setenv("SMS_MODE", "android")
    st = gw.gateway_status()
    assert st["reachable"] is False and "SMS_CLOUD_USER" in st["detail"]


# ------------------------------------------------------------------ per-ward numbers
@pytest.fixture()
def book(gw, monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "CONTACTS_PATH", tmp_path / "ward_contacts.csv")
    monkeypatch.delenv("SMS_OVERRIDE_TO", raising=False)
    return gw


def test_save_contact_normalises_and_masks(book):
    r = book.save_contact("DSCC_Ward_04", "  Rahima   Khatun ", "01712-345678")
    assert r == {"ward_id": "DSCC_Ward_04", "name": "Rahima Khatun", "phone": "+8801••••••678", "has_phone": True}
    assert book.contacts()["DSCC_Ward_04"]["phone"] == "+8801712345678"
    assert len(book.contacts()) == 60                       # the other wards are kept


def test_save_contact_rejects_bad_numbers_and_unknown_wards(book):
    for bad in ("12345", "01212345678", "+88017123"):
        with pytest.raises(ValueError):
            book.save_contact("DSCC_Ward_04", "", bad)
    with pytest.raises(KeyError):
        book.save_contact("NOPE_Ward_99", "", "01712345678")


def test_ward_number_beats_fallback_number(book, monkeypatch):
    monkeypatch.setenv("SMS_OVERRIDE_TO", "01900000000")
    book.save_contact("DSCC_Ward_04", "", "01712345678")
    assert book.recipient_for("DSCC_Ward_04") == ("+8801712345678", "contact")
    assert book.recipient_for("DSCC_Ward_05") == ("+8801900000000", "fallback")
    book.save_contact("DSCC_Ward_04", "", "")              # removing the number falls back again
    assert book.recipient_for("DSCC_Ward_04")[1] == "fallback"
    monkeypatch.delenv("SMS_OVERRIDE_TO")
    assert book.recipient_for("DSCC_Ward_04")[0] == ""


def test_contact_api_never_returns_full_numbers(book):
    from fastapi.testclient import TestClient
    from src.api import app
    c = TestClient(app)
    assert c.post("/api/sms/contact", json={"ward_id": "DSCC_Ward_04", "name": "A", "phone": "01712345678"}).status_code == 200
    assert c.post("/api/sms/contact", json={"ward_id": "DSCC_Ward_04", "phone": "123"}).status_code == 400
    w = c.get("/api/sms/contacts").json()["wards"]["DSCC_Ward_04"]
    assert w["has_phone"] and "345" not in w["phone"]


def test_last_seen_picks_newest_phone_in_utc(gw):
    devices = [{"lastSeen": "2026-11-27T15:12:04+03:00"}, {"lastSeen": "2026-11-27T09:00:00Z"}, {"lastSeen": "garbage"}]
    assert gw._last_seen(devices) == "2026-11-27T12:12:04Z"
    assert gw._last_seen([]) is None and gw._last_seen(None) is None


def test_gateway_status_reports_last_seen_and_is_cached(gw, monkeypatch):
    monkeypatch.setenv("SMS_MODE", "android")
    monkeypatch.setenv("SMS_CLOUD_USER", "u"); monkeypatch.setenv("SMS_CLOUD_PASS", "p")
    monkeypatch.setattr(gw, "_STATUS_CACHE", {"at": 0.0, "key": None, "value": None})
    calls = []

    def fake_check(route):
        calls.append(route["name"])
        return True, "cloud relay: 1 phone(s) registered", "2026-11-27T12:12:04Z"

    monkeypatch.setattr(gw, "_check", fake_check)
    first = gw.gateway_status()
    assert first["reachable"] and first["route"] == "cloud" and first["last_seen"] == "2026-11-27T12:12:04Z"
    assert gw.gateway_status() is first and calls == ["cloud"]        # reused, no second network call
    gw.gateway_status(fresh=True)
    assert calls == ["cloud", "cloud"]                                 # fresh=True asks again
