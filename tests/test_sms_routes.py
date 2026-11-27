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
