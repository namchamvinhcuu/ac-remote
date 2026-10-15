"""Last commanded state per unit, persisted to JSON (IR is one-way, the A/C never reports back)."""

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.exception("state file unreadable, starting empty path=%s", path)
            else:
                if isinstance(data, dict):
                    self._data = data
                else:
                    log.error("state file is not a JSON object, starting empty path=%s type=%s", path, type(data).__name__)

    def get(self, unit_id: str) -> dict | None:
        return self._data.get(unit_id)

    def set(self, unit_id: str, state: dict) -> None:
        with self._lock:
            self._data[unit_id] = state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
