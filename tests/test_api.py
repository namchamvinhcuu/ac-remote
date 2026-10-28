import json

import pytest
from fastapi.testclient import TestClient

from app.config import Unit, load_units
from app.main import create_app
from app.state import StateStore

AUTH = ("admin", "secret")


class FakeRM4:
    def __init__(self, fail: bool = False):
        self.sent: list[bytes] = []
        self.fail = fail

    def send(self, packet: bytes) -> None:
        if self.fail:
            raise TimeoutError("no reply")
        self.sent.append(packet)


@pytest.fixture
def setup(tmp_path):
    units = {
        "reetech": Unit("reetech", "Reetech", "mitsubishi112", "10.0.0.1"),
        "midea": Unit("midea", "Midea", "coolix", "10.0.0.2"),
    }
    senders = {"10.0.0.1": FakeRM4(), "10.0.0.2": FakeRM4()}
    store = StateStore(tmp_path / "state.json")
    client = TestClient(create_app(units, store, senders.__getitem__, *AUTH))
    return client, senders, store, tmp_path


def test_requires_auth(setup):
    client, *_ = setup
    assert client.get("/api/units").status_code == 401
    assert client.get("/api/units", auth=("admin", "wrong")).status_code == 401
    assert client.post("/api/units/midea", json={"power": True}).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_command_sends_to_the_unit_rm4_and_persists_state(setup):
    client, senders, _, tmp_path = setup
    res = client.post("/api/units/midea", json={"power": True, "temp": 26}, auth=AUTH)
    assert res.status_code == 200
    assert res.json()["state"] == {"power": True, "mode": "cool", "temp": 26, "fan": "auto"}
    assert len(senders["10.0.0.2"].sent) == 1 and not senders["10.0.0.1"].sent

    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["midea"]["temp"] == 26
    units = client.get("/api/units", auth=AUTH).json()
    assert next(u for u in units if u["id"] == "midea")["state"]["temp"] == 26


def test_protocol_default_fan(setup):
    client, *_ = setup
    res = client.post("/api/units/reetech", json={"power": False}, auth=AUTH)
    assert res.json()["state"]["fan"] == "med"


def test_unknown_unit_and_invalid_values(setup):
    client, senders, *_ = setup
    assert client.post("/api/units/nope", json={"power": True}, auth=AUTH).status_code == 404
    assert client.post("/api/units/midea", json={"power": True, "temp": 40}, auth=AUTH).status_code == 422
    assert client.post("/api/units/midea", json={"power": True, "mode": "turbo"}, auth=AUTH).status_code == 422
    assert not any(s.sent for s in senders.values())


def test_rm4_failure_returns_502_and_keeps_old_state(tmp_path):
    units = {"midea": Unit("midea", "Midea", "coolix", "10.0.0.2")}
    store = StateStore(tmp_path / "state.json")
    client = TestClient(create_app(units, store, lambda _h: FakeRM4(fail=True), *AUTH))
    assert client.post("/api/units/midea", json={"power": True}, auth=AUTH).status_code == 502
    assert store.get("midea") is None


def test_state_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    store = StateStore(path)
    assert store.get("x") is None
    store.set("x", {"power": True})
    assert StateStore(path).get("x") == {"power": True}


def test_load_units(tmp_path):
    cfg = tmp_path / "units.yaml"
    cfg.write_text(
        "rm4: {host: 192.168.5.48}\n"
        "units:\n"
        "  - {id: reetech, protocol: mitsubishi112}\n"
        "  - {id: midea, name: Midea, protocol: coolix, rm4_host: 192.168.5.49}\n"
    )
    units = load_units(str(cfg))
    assert units["reetech"].rm4_host == "192.168.5.48" and units["reetech"].name == "reetech"
    assert units["midea"].rm4_host == "192.168.5.49"


def test_load_units_accepts_daikin152(tmp_path):
    cfg = tmp_path / "units.yaml"
    cfg.write_text("rm4: {host: 10.0.0.1}\nunits:\n  - {id: daikin, name: Daikin, protocol: daikin152, rm4_host: 10.0.0.3}\n")
    units = load_units(str(cfg))
    assert units["daikin"].protocol == "daikin152" and units["daikin"].rm4_host == "10.0.0.3"


@pytest.mark.parametrize("body", [
    "units:\n  - {id: a, protocol: coolix}\n",
    "rm4: {host: h}\nunits:\n  - {id: a, protocol: nec}\n",
    "rm4: {host: h}\nunits:\n  - {id: a, protocol: coolix}\n  - {id: a, protocol: coolix}\n",
])
def test_load_units_rejects_bad_config(tmp_path, body):
    cfg = tmp_path / "units.yaml"
    cfg.write_text(body)
    with pytest.raises(ValueError):
        load_units(str(cfg))
