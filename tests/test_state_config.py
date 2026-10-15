"""StateStore persistence edge cases and load_units config errors."""

import logging
from pathlib import Path

import pytest
import yaml

from app.config import load_units
from app.state import StateStore


# --- StateStore ---

def test_missing_file_starts_empty_without_creating_it(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    assert store.get("midea") is None
    assert not path.exists()


def test_set_creates_parent_dirs_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "nested" / "data" / "state.json"
    store = StateStore(path)
    store.set("midea", {"power": True, "temp": 24})
    store.set("reetech", {"power": False})
    assert sorted(p.name for p in path.parent.iterdir()) == ["state.json"]
    reloaded = StateStore(path)
    assert reloaded.get("midea") == {"power": True, "temp": 24}
    assert reloaded.get("reetech") == {"power": False}


def test_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    StateStore(path).set("midea", {"temp": 25})
    before = path.read_bytes()

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        StateStore(path).set("midea", {"temp": 18})
    monkeypatch.undo()
    assert path.read_bytes() == before  # atomic: never half-written
    assert StateStore(path).get("midea") == {"temp": 25}


def test_non_ascii_round_trip(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path).set("phòng-ngủ", {"name": "Điều hòa"})
    assert StateStore(path).get("phòng-ngủ") == {"name": "Điều hòa"}


@pytest.mark.parametrize("content", ["", "{not json", "\xff\xfe garbage"])
def test_unreadable_state_file_starts_empty(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="latin-1")
    assert StateStore(path).get("midea") is None


@pytest.mark.parametrize("content", ["[1, 2]", "null", "\"text\"", "42"])
def test_valid_json_that_is_not_an_object_starts_empty(tmp_path, content, caplog):
    # A parseable-but-wrong-shape file is as corrupt as unparseable JSON: the store
    # must start empty instead of crashing every later get() (GET /api/units -> 500).
    path = tmp_path / "state.json"
    path.write_text(content)
    with caplog.at_level(logging.ERROR, logger="app.state"):
        store = StateStore(path)
    assert any(r.name == "app.state" and r.levelno == logging.ERROR for r in caplog.records)
    assert store.get("midea") is None
    store.set("midea", {"power": True})
    assert StateStore(path).get("midea") == {"power": True}


# --- load_units ---

def _write(tmp_path, body: str) -> str:
    cfg = tmp_path / "units.yaml"
    cfg.write_text(body)
    return str(cfg)


def test_load_units_reads_units_file_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITS_FILE", _write(tmp_path, "rm4: {host: h}\nunits:\n  - {id: a, protocol: coolix}\n"))
    assert list(load_units()) == ["a"]


def test_load_units_empty_file_means_no_units(tmp_path):
    assert load_units(_write(tmp_path, "")) == {}


def test_load_units_invalid_yaml_raises(tmp_path):
    with pytest.raises(yaml.YAMLError):
        load_units(_write(tmp_path, "units: [ {id: a, protocol: coolix\n"))


def test_load_units_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_units(str(tmp_path / "nope.yaml"))


@pytest.mark.parametrize("body", [
    pytest.param("rm4: {host: h}\nunits:\n  - {id: a}\n", id="protocol-missing"),
    pytest.param("rm4: {host: h}\nunits:\n  - {protocol: coolix}\n", id="id-missing"),
])
def test_load_units_missing_required_key_fails_loudly(tmp_path, body):
    with pytest.raises(KeyError):
        load_units(_write(tmp_path, body))


def test_load_units_python_tags_are_not_executed(tmp_path):
    # safe_load: a config file must never be able to construct arbitrary objects.
    with pytest.raises(yaml.YAMLError):
        load_units(_write(tmp_path, "units: !!python/object/apply:os.system ['true']\n"))
