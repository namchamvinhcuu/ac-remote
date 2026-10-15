"""Load A/C units from YAML (path in UNITS_FILE)."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.ir import PROTOCOLS


@dataclass(frozen=True)
class Unit:
    id: str
    name: str
    protocol: str
    rm4_host: str


def load_units(path: str | None = None) -> dict[str, Unit]:
    path = Path(path or os.environ.get("UNITS_FILE", "config/units.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_host = (raw.get("rm4") or {}).get("host")

    units: dict[str, Unit] = {}
    for item in raw.get("units") or []:
        host = item.get("rm4_host") or default_host
        if not host:
            raise ValueError(f"unit {item.get('id')!r}: no rm4_host and no rm4.host default")
        if item["protocol"] not in PROTOCOLS:
            raise ValueError(f"unit {item['id']!r}: unknown protocol {item['protocol']!r}")
        unit_id = str(item["id"])  # YAML `id: 123` is an int; URL path params are always str
        if unit_id in units:
            raise ValueError(f"duplicate unit id {unit_id!r}")
        units[unit_id] = Unit(id=unit_id, name=str(item.get("name", unit_id)), protocol=item["protocol"], rm4_host=host)
    return units
