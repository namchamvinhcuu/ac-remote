"""API gaps: auth on every route, input validation before any IR send, RM4 failure path."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Unit, load_units
from app.main import create_app
from app.state import StateStore

USER, PASSWORD = "admin", "s3cret-pw"
AUTH = (USER, PASSWORD)


class FakeRM4:
    def __init__(self, fail: bool = False):
        self.sent: list[bytes] = []
        self.fail = fail

    def send(self, packet: bytes) -> None:
        if self.fail:
            raise TimeoutError("no reply")
        self.sent.append(packet)


@pytest.fixture
def env(tmp_path):
    units = {
        "reetech": Unit("reetech", "Reetech", "mitsubishi112", "10.0.0.1"),
        "midea": Unit("midea", "Midea", "coolix", "10.0.0.2"),
        "daikin": Unit("daikin", "Daikin", "daikin152", "10.0.0.3"),
    }
    senders = {"10.0.0.1": FakeRM4(), "10.0.0.2": FakeRM4(), "10.0.0.3": FakeRM4()}
    state_path = tmp_path / "state.json"
    app = create_app(units, StateStore(state_path), senders.__getitem__, USER, PASSWORD)
    return TestClient(app), app, senders, state_path


def _nothing_sent(senders) -> bool:
    return not any(s.sent for s in senders.values())


# --- auth ---

PROTECTED = [
    ("GET", "/", None),
    ("GET", "/api/units", None),
    ("POST", "/api/units/midea", {"power": True}),
    ("POST", "/api/units/reetech", {"power": False}),
    ("POST", "/api/units/does-not-exist", {"power": True}),  # 401 before 404: no unit enumeration
]
BAD_CREDS = [
    pytest.param(None, id="no-credentials"),
    pytest.param((USER, "wrong"), id="wrong-password"),
    pytest.param(("root", PASSWORD), id="wrong-user"),
    pytest.param(("root", "wrong"), id="both-wrong"),
    pytest.param(("", ""), id="empty"),
    pytest.param((USER, PASSWORD[:-1]), id="password-prefix"),  # secret-allow: test fixture
    pytest.param((USER, PASSWORD + "x"), id="password-suffix"),  # secret-allow: test fixture
    pytest.param((USER.upper(), PASSWORD), id="user-case"),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
@pytest.mark.parametrize("creds", BAD_CREDS)
def test_protected_routes_reject_bad_credentials_without_side_effects(env, method, path, body, creds):
    client, _, senders, state_path = env
    res = client.request(method, path, json=body, auth=creds)
    assert res.status_code == 401
    assert res.headers.get("www-authenticate", "").startswith("Basic")
    assert _nothing_sent(senders)
    assert not state_path.exists()


def test_malformed_authorization_header_is_401(env):
    client, _, senders, _ = env
    for header in ("Basic not-base64!!", "Bearer abc", "Basic "):
        res = client.post("/api/units/midea", json={"power": True}, headers={"Authorization": header})
        assert res.status_code == 401, header
    assert _nothing_sent(senders)


def test_index_served_with_valid_credentials(env):
    client, *_ = env
    res = client.get("/", auth=AUTH)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_healthz_is_the_only_public_route(env):
    # Project invariant: auth on every route except /healthz. Enumerate the real
    # route table so a new route (or a framework default one) without auth is caught.
    client, app, *_ = env
    public = []
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            if method == "HEAD":
                continue
            path = route.path.replace("{unit_id}", "midea")
            if client.request(method, path).status_code != 401:
                public.append(f"{method} {route.path}")
    assert public == ["GET /healthz"]


# --- command validation: 4xx and NO IR send ---

INVALID = [
    pytest.param("midea", {"power": True, "temp": 16}, id="coolix-temp-below-17"),
    pytest.param("midea", {"power": True, "temp": 31}, id="coolix-temp-above-30"),
    pytest.param("reetech", {"power": True, "temp": 15}, id="mitsu-temp-below-16"),
    pytest.param("reetech", {"power": True, "temp": 32}, id="mitsu-temp-above-31"),
    pytest.param("midea", {"power": True, "mode": "fan"}, id="mode-not-in-literal"),
    pytest.param("midea", {"power": True, "fan": "quiet"}, id="coolix-fan-unknown"),
    pytest.param("reetech", {"power": True, "fan": "auto"}, id="mitsu-fan-unknown"),
    pytest.param("daikin", {"power": True, "temp": 17}, id="daikin-temp-below-18"),
    pytest.param("daikin", {"power": True, "temp": 33}, id="daikin-temp-above-32"),
    pytest.param("daikin", {"power": True, "fan": "turbo"}, id="daikin-fan-unknown"),
    pytest.param("daikin", {"power": True, "mode": "fan"}, id="daikin-mode-not-in-literal"),
    pytest.param("midea", {"temp": 25}, id="power-missing"),
    pytest.param("midea", {"power": True, "temp": 25.5}, id="temp-fractional"),
    pytest.param("midea", {"power": True, "temp": "hot"}, id="temp-string"),
    pytest.param("midea", {"power": "maybe"}, id="power-not-bool"),
]


@pytest.mark.parametrize("unit,body", INVALID)
def test_invalid_command_is_422_and_sends_nothing(env, unit, body):
    client, _, senders, state_path = env
    res = client.post(f"/api/units/{unit}", json=body, auth=AUTH)
    assert res.status_code == 422, res.text
    assert _nothing_sent(senders)
    assert not state_path.exists()


@pytest.mark.parametrize("invalid", [
    pytest.param({"temp": 99}, id="temp-only"),
    pytest.param({"fan": "bogus"}, id="fan-only"),
])
@pytest.mark.parametrize("unit", ["midea", "reetech", "daikin"])
def test_power_off_with_invalid_settings_is_rejected_for_every_protocol(env, unit, invalid):
    # Power-off must not persist garbage (temp 99 / fan "bogus") as the unit's
    # last commanded state; mitsubishi112 rejects it, coolix must too.
    # One invalid field per case, so each check must run before the power-off return.
    client, _, senders, state_path = env
    res = client.post(f"/api/units/{unit}", json={"power": False} | invalid, auth=AUTH)
    assert res.status_code == 422, res.text
    assert _nothing_sent(senders)
    assert not state_path.exists()


def test_non_json_body_is_422(env):
    client, _, senders, _ = env
    res = client.post("/api/units/midea", content=b"power=1", auth=AUTH, headers={"content-type": "application/json"})
    assert res.status_code == 422
    assert _nothing_sent(senders)


def test_unknown_unit_is_404_and_sends_nothing(env):
    client, _, senders, state_path = env
    res = client.post("/api/units/../midea", json={"power": True}, auth=AUTH)
    assert res.status_code == 404
    res = client.post("/api/units/MIDEA", json={"power": True}, auth=AUTH)
    assert res.status_code == 404
    assert _nothing_sent(senders)
    assert not state_path.exists()


@pytest.mark.parametrize("unit,temp", [
    ("midea", 17), ("midea", 30), ("reetech", 16), ("reetech", 31), ("daikin", 18), ("daikin", 32),
])
def test_temp_range_boundaries_are_accepted(env, unit, temp):
    client, _, senders, _ = env
    res = client.post(f"/api/units/{unit}", json={"power": True, "temp": temp}, auth=AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["state"]["temp"] == temp
    assert sum(len(s.sent) for s in senders.values()) == 1


def test_explicit_fan_overrides_protocol_default(env):
    client, *_ = env
    res = client.post("/api/units/reetech", json={"power": True, "fan": "quiet"}, auth=AUTH)
    assert res.json()["state"]["fan"] == "quiet"


def test_sent_packet_is_the_built_ir_packet(env):
    from app.ir import build_packet

    client, _, senders, _ = env
    client.post("/api/units/reetech", json={"power": True, "mode": "heat", "temp": 22, "fan": "low"}, auth=AUTH)
    assert senders["10.0.0.1"].sent == [build_packet("mitsubishi112", True, "heat", 22, "low")]


def test_daikin_default_fan_is_auto_and_packet_goes_only_to_its_rm4(env):
    from app.ir import build_packet

    client, _, senders, state_path = env
    res = client.post("/api/units/daikin", json={"power": True, "temp": 27}, auth=AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["state"] == {"power": True, "mode": "cool", "temp": 27, "fan": "auto"}
    assert senders["10.0.0.3"].sent == [build_packet("daikin152", True, "cool", 27, "auto")]
    assert not senders["10.0.0.1"].sent and not senders["10.0.0.2"].sent
    assert state_path.exists()


def test_daikin_verified_off_command_sends_the_hardware_verified_off_state(env):
    from broadlink.remote import data_to_pulses

    client, _, senders, _ = env
    res = client.post("/api/units/daikin", json={"power": False, "temp": 27, "fan": "quiet"}, auth=AUTH)
    assert res.status_code == 200, res.text
    [packet] = senders["10.0.0.3"].sent
    pulses = data_to_pulses(packet)
    bits = [1 if pulses[14 + 2 * i + 1] > 900 else 0 for i in range(152)]  # 12 leader pulses + header
    state = bytes(sum(b << i for i, b in enumerate(bits[n:n + 8])) for n in range(0, 152, 8))
    assert state.hex() == "11da270000303600b0000000000000c50008f5"  # turned the real unit off (2026-10-28)


# --- RM4 failure ---

def test_rm4_failure_is_502_without_host_in_detail_logs_host_and_keeps_previous_state(tmp_path, caplog):
    # Regression (round-2 review): the RM4 host / exception text must not reach the HTTP client, only the server log.
    units = {"midea": Unit("midea", "Midea", "coolix", "10.0.0.2")}
    store = StateStore(tmp_path / "state.json")
    store.set("midea", {"power": False, "mode": "cool", "temp": 25, "fan": "auto"})
    rm4 = FakeRM4()
    client = TestClient(create_app(units, store, lambda _h: rm4, *AUTH))

    rm4.fail = True
    with caplog.at_level(logging.ERROR, logger="app.main"):
        res = client.post("/api/units/midea", json={"power": True, "temp": 20}, auth=AUTH)
    assert res.status_code == 502
    assert res.json()["detail"] == "RM4 unreachable for unit 'midea'; see server log"
    assert "10.0.0.2" not in res.text and "no reply" not in res.text
    errors = [r for r in caplog.records if r.name == "app.main" and r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "10.0.0.2" in errors[0].getMessage() and "unit=midea" in errors[0].getMessage()
    assert errors[0].exc_info is not None and "no reply" in str(errors[0].exc_info[1])
    assert store.get("midea")["power"] is False
    assert StateStore(tmp_path / "state.json").get("midea")["power"] is False

    rm4.fail = False  # RM4 back: the next command goes through
    assert client.post("/api/units/midea", json={"power": True, "temp": 20}, auth=AUTH).status_code == 200
    assert store.get("midea")["temp"] == 20


# --- regression guards for audit round-2 fixes ---

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
@pytest.mark.parametrize("creds", [None, AUTH], ids=["no-auth", "auth"])
def test_framework_docs_routes_do_not_exist(env, path, creds):
    # Regression (audit bug 1): FastAPI's default docs/openapi were served without auth.
    client, *_ = env
    assert client.get(path, auth=creds).status_code == 404


@pytest.mark.parametrize("method,path,body,creds,status", [
    ("GET", "/", None, AUTH, 200),
    ("GET", "/api/units", None, AUTH, 200),
    ("POST", "/api/units/midea", {"power": True}, AUTH, 200),
    ("GET", "/api/units", None, None, 401),
    ("POST", "/api/units/midea", {"power": True}, (USER, "wrong"), 401),
    ("POST", "/api/units/nope", {"power": True}, AUTH, 404),
    ("POST", "/api/units/midea", {"power": True, "temp": 99}, AUTH, 422),
])
def test_cache_control_no_store_on_every_non_health_response(env, method, path, body, creds, status):
    client, *_ = env
    res = client.request(method, path, json=body, auth=creds)
    assert res.status_code == status
    assert res.headers.get("cache-control") == "no-store"


def test_cache_control_no_store_on_502(tmp_path):
    units = {"midea": Unit("midea", "Midea", "coolix", "10.0.0.2")}
    client = TestClient(create_app(units, StateStore(tmp_path / "s.json"), lambda _h: FakeRM4(fail=True), *AUTH))
    res = client.post("/api/units/midea", json={"power": True}, auth=AUTH)
    assert res.status_code == 502
    assert res.headers.get("cache-control") == "no-store"


def test_healthz_has_no_cache_control_override(env):
    client, *_ = env
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "cache-control" not in res.headers


# --- regression guards for the OpenClaw round-2 fixes ---

@pytest.mark.parametrize("method,path,body,creds,status", [
    ("GET", "/", None, AUTH, 200),
    ("GET", "/api/units", None, AUTH, 200),
    ("POST", "/api/units/midea", {"power": True}, AUTH, 200),
    ("GET", "/", None, None, 401),
    ("POST", "/api/units/midea", {"power": True}, (USER, "wrong"), 401),
    ("POST", "/api/units/midea", {"power": True, "temp": 99}, AUTH, 422),
])
def test_anti_framing_headers_on_every_non_health_response(env, method, path, body, creds, status):
    # Regression (clickjacking): no other site may frame the control page or the API.
    client, *_ = env
    res = client.request(method, path, json=body, auth=creds)
    assert res.status_code == status
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("content-security-policy") == "frame-ancestors 'none'"


def test_healthz_has_no_anti_framing_headers(env):
    client, *_ = env
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "x-frame-options" not in res.headers and "content-security-policy" not in res.headers


@pytest.mark.parametrize("content_type", [
    None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
])
def test_command_with_non_json_content_type_is_422_and_sends_nothing(env, content_type):
    # CSRF guard: a cross-site <form> or a "simple" fetch cannot set application/json, so such a POST must not
    # switch the A/C even when the browser attaches cached Basic credentials. Relies on FastAPI strict_content_type.
    client, _, senders, state_path = env
    headers = {} if content_type is None else {"content-type": content_type}
    res = client.post("/api/units/midea", content=b'{"power": true}', headers=headers, auth=AUTH)
    assert res.status_code == 422, res.text
    assert _nothing_sent(senders)
    assert not state_path.exists()


def test_command_with_json_content_type_still_works(env):
    # Control for the CSRF guard above: the same body with application/json is accepted.
    client, _, senders, _ = env
    res = client.post("/api/units/midea", content=b'{"power": true}', headers={"content-type": "application/json"}, auth=AUTH)
    assert res.status_code == 200, res.text
    assert len(senders["10.0.0.2"].sent) == 1


def test_static_html_never_builds_markup_from_strings():
    # XSS guard: unit names/ids come from config and API errors; the UI must use textContent/createElement only.
    from pathlib import Path

    pages = sorted((Path(__file__).resolve().parent.parent / "app" / "static").glob("*.html"))
    assert pages, "no static page found"
    offenders = [f"{p.name}:{n}" for p in pages for n, line in enumerate(p.read_text().splitlines(), 1)
                 if any(sink in line for sink in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"))]
    assert offenders == []


def test_state_write_failure_still_reports_success_and_logs(tmp_path, caplog):
    # Regression: IR already went out, so a failed state write must not turn into a 500.
    blocker = tmp_path / "blocker"
    blocker.write_text("a regular file, so mkdir of the state dir fails")
    units = {"midea": Unit("midea", "Midea", "coolix", "10.0.0.2")}
    rm4 = FakeRM4()
    client = TestClient(create_app(units, StateStore(blocker / "data" / "state.json"), lambda _h: rm4, *AUTH))

    with caplog.at_level(logging.ERROR, logger="app.main"):
        res = client.post("/api/units/midea", json={"power": True, "temp": 22}, auth=AUTH)
    assert res.status_code == 200
    assert res.json()["state"]["temp"] == 22
    assert len(rm4.sent) == 1
    errors = [r for r in caplog.records if r.name == "app.main" and r.levelno >= logging.ERROR]
    assert errors and "state write failed" in errors[0].getMessage()
    assert errors[0].exc_info is not None


def test_auth_failure_log_cannot_be_forged_with_newlines(env, caplog):
    # Regression: a username with a newline must not inject a fake log line.
    client, *_ = env
    with caplog.at_level(logging.WARNING, logger="app.main"):
        client.get("/api/units", auth=("evil\n2026-01-01 INFO app.main command sent", "x"))
    msgs = [r.getMessage() for r in caplog.records if "auth failed" in r.getMessage()]
    assert len(msgs) == 1
    assert "\n" not in msgs[0] and "\r" not in msgs[0]
    assert "evil\\n2026" in msgs[0]


def test_auth_failure_log_truncates_long_username(env, caplog):
    client, *_ = env
    with caplog.at_level(logging.WARNING, logger="app.main"):
        client.get("/api/units", auth=("A" * 5000, "x"))
    msg = next(r.getMessage() for r in caplog.records if "auth failed" in r.getMessage())
    assert "A" * 64 in msg and "A" * 65 not in msg


def test_numeric_yaml_unit_id_is_reachable(tmp_path):
    # Regression: `id: 123` loaded as int was unreachable from the str path param.
    cfg = tmp_path / "units.yaml"
    cfg.write_text("rm4: {host: 10.0.0.2}\nunits:\n  - {id: 123, name: 456, protocol: coolix}\n")
    units = load_units(str(cfg))
    assert list(units) == ["123"] and units["123"].name == "456"
    rm4 = FakeRM4()
    client = TestClient(create_app(units, StateStore(tmp_path / "s.json"), lambda _h: rm4, *AUTH))
    res = client.post("/api/units/123", json={"power": True}, auth=AUTH)
    assert res.status_code == 200, res.text
    assert len(rm4.sent) == 1
    listed = client.get("/api/units", auth=AUTH).json()
    assert listed[0]["id"] == "123" and listed[0]["state"]["power"] is True
