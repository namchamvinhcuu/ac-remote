"""HTTP API + web UI to control A/C units through BroadLink RM4 mini."""

import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from app.config import Unit, load_units
from app.ir import build_packet
from app.logging_setup import setup_logging
from app.rm4 import RM4
from app.state import StateStore

log = logging.getLogger(__name__)
_STATIC = Path(__file__).parent / "static"


class Command(BaseModel):
    power: bool
    mode: Literal["cool", "heat", "dry", "auto"] = "cool"
    temp: int = 25
    fan: str | None = None  # protocol default when omitted


_DEFAULT_FAN = {"mitsubishi112": "med", "coolix": "auto", "daikin152": "auto"}
_PLACEHOLDER_PASSWORD = "change-me"
_MIN_PASSWORD_LEN = 12


def create_app(units: dict[str, Unit], store: StateStore, sender_for: Callable[[str], RM4], user: str, password: str) -> FastAPI:
    # No docs/openapi routes: they would be served without auth.
    app = FastAPI(title="ac-remote", docs_url=None, redoc_url=None, openapi_url=None)
    security = HTTPBasic()

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/healthz":
            response.headers["Cache-Control"] = "no-store"
            # Anti-clickjacking: no other site may frame the control page.
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    def require_auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
        ok_user = secrets.compare_digest(creds.username.encode(), user.encode())
        ok_pass = secrets.compare_digest(creds.password.encode(), password.encode())
        if not (ok_user and ok_pass):
            log.warning("auth failed user=%r", creds.username[:64])
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
        return creds.username

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", dependencies=[Depends(require_auth)])
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/units", dependencies=[Depends(require_auth)])
    def list_units():
        return [{"id": u.id, "name": u.name, "protocol": u.protocol, "state": store.get(u.id)} for u in units.values()]

    @app.post("/api/units/{unit_id}")
    async def command(unit_id: str, cmd: Command, who: str = Depends(require_auth)):
        unit = units.get(unit_id)
        if unit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown unit {unit_id!r}")
        fan = cmd.fan or _DEFAULT_FAN[unit.protocol]
        try:
            packet = build_packet(unit.protocol, cmd.power, cmd.mode, cmd.temp, fan)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        try:
            await run_in_threadpool(sender_for(unit.rm4_host).send, packet)
        except Exception as exc:
            log.exception("command failed unit=%s user=%s host=%s", unit_id, who, unit.rm4_host)
            # Host and exception text stay in the server log only (path-rule: no RM4 host in the page).
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"RM4 unreachable for unit {unit_id!r}; see server log") from exc

        state = {"power": cmd.power, "mode": cmd.mode, "temp": cmd.temp, "fan": fan}
        try:
            store.set(unit_id, state)
        except OSError:
            # IR already sent, so the A/C did switch: report success, only persistence failed.
            log.exception("state write failed unit=%s path=%s", unit_id, store.path)
        log.info("command sent unit=%s user=%s state=%s", unit_id, who, state)
        return {"id": unit_id, "state": state}

    return app


def build_from_env() -> FastAPI:
    setup_logging()
    password = os.environ.get("APP_PASSWORD")
    if not password:
        raise RuntimeError("APP_PASSWORD is required")
    if password == _PLACEHOLDER_PASSWORD or len(password) < _MIN_PASSWORD_LEN:
        raise RuntimeError(f"APP_PASSWORD must be a non-placeholder value of at least {_MIN_PASSWORD_LEN} characters")
    units = load_units()
    store = StateStore(Path(os.environ.get("DATA_DIR", "data")) / "state.json")
    senders: dict[str, RM4] = {}

    def sender_for(host: str) -> RM4:
        if host not in senders:
            senders[host] = RM4(host)
        return senders[host]

    log.info("starting units=%s", list(units))
    return create_app(units, store, sender_for, os.environ.get("APP_USER", "admin"), password)
