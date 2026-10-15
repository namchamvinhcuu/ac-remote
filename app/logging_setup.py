"""Logging: stdout + rotating file under LOG_DIR."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging() -> None:
    log_dir = Path(os.environ.get("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)
    file_handler = RotatingFileHandler(log_dir / "ac-remote.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
