"""build_from_env: fail-closed without APP_PASSWORD, env wiring, one cached RM4 per host."""

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import build_from_env

PW = "test-pass-0001"  # secret-allow: test fixture, 14 chars (>= the 12-char minimum)


class FakeRM4:
    instances: list["FakeRM4"] = []

    def __init__(self, host: str):
        self.host = host
        self.sent: list[bytes] = []
        FakeRM4.instances.append(self)

    def send(self, packet: bytes) -> None:
        self.sent.append(packet)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    units = tmp_path / "units.yaml"
    units.write_text(
        "rm4: {host: 10.0.0.1}\n"
        "units:\n"
        "  - {id: reetech, protocol: mitsubishi112}\n"
        "  - {id: midea, protocol: coolix}\n"
        "  - {id: other, protocol: coolix, rm4_host: 10.0.0.2}\n"
    )
    for key in ("APP_PASSWORD", "APP_USER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UNITS_FILE", str(units))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    FakeRM4.instances = []
    monkeypatch.setattr(main_mod, "RM4", FakeRM4)  # patched where build_from_env uses it
    yield tmp_path, monkeypatch
    for h in root.handlers[:]:  # setup_logging adds root handlers; undo them
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


@pytest.mark.parametrize("value", [None, ""])
def test_refuses_to_start_without_app_password(env, value):
    _, mp = env
    if value is not None:
        mp.setenv("APP_PASSWORD", value)
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        build_from_env()


@pytest.mark.parametrize("value", [
    pytest.param("change-me", id="placeholder-from-env-example"),
    pytest.param("a" * 11, id="11-chars"),
])
def test_refuses_weak_app_password(env, value):
    # Regression (audit round 2): placeholder / too-short passwords must not start the app.
    _, mp = env
    mp.setenv("APP_PASSWORD", value)
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        build_from_env()


def test_accepts_12_char_password_boundary(env):
    _, mp = env
    mp.setenv("APP_PASSWORD", "a" * 12)
    client = TestClient(build_from_env())
    assert client.get("/api/units", auth=("admin", "a" * 12)).status_code == 200


def test_default_user_is_admin_and_password_from_env(env):
    tmp_path, mp = env
    mp.setenv("APP_PASSWORD", PW)
    client = TestClient(build_from_env())
    assert client.get("/api/units", auth=("admin", PW)).status_code == 200
    assert client.get("/api/units", auth=("admin", PW + "x")).status_code == 401
    assert (tmp_path / "logs" / "ac-remote.log").exists()


def test_app_user_from_env(env):
    _, mp = env
    mp.setenv("APP_PASSWORD", PW)
    mp.setenv("APP_USER", "nam")
    client = TestClient(build_from_env())
    assert client.get("/api/units", auth=("nam", PW)).status_code == 200
    assert client.get("/api/units", auth=("admin", PW)).status_code == 401


def test_one_cached_rm4_per_host_and_state_in_data_dir(env):
    tmp_path, mp = env
    mp.setenv("APP_PASSWORD", PW)
    client = TestClient(build_from_env())
    assert FakeRM4.instances == []  # lazy: no RM4 before the first command
    for unit in ("reetech", "midea", "reetech", "other"):
        assert client.post(f"/api/units/{unit}", json={"power": False}, auth=("admin", PW)).status_code == 200
    hosts = sorted(r.host for r in FakeRM4.instances)
    assert hosts == ["10.0.0.1", "10.0.0.2"]
    by_host = {r.host: r for r in FakeRM4.instances}
    assert len(by_host["10.0.0.1"].sent) == 3 and len(by_host["10.0.0.2"].sent) == 1
    assert (tmp_path / "data" / "state.json").exists()
