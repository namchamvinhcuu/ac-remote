"""Command-line client for the ac-remote API, meant to run inside the container.

    python -m app.cli list
    python -m app.cli <unit> on|off [--mode M] [--temp T] [--fan F]

Settings left out reuse the unit's last commanded state. Credentials come from APP_USER/APP_PASSWORD
(already in the container env), so callers such as a chat bot over SSH never hold the password.
Prints the API's JSON on success; on failure logs the error to stderr and exits non-zero.
"""

import argparse
import base64
import http.client
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("app.cli")

_DEFAULT_URL = "http://127.0.0.1:8000"
_TIMEOUT = 90  # RM4 worst case ~30 s per command, plus one queued command behind the per-host lock
_SETTINGS = ("mode", "temp", "fan")


class ApiError(Exception):
    pass


def _request(method: str, path: str, body: dict | None = None):
    base = os.environ.get("AC_REMOTE_URL", _DEFAULT_URL).rstrip("/")
    token = base64.b64encode(f"{os.environ.get('APP_USER', 'admin')}:{os.environ.get('APP_PASSWORD', '')}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Authorization": f"Basic {token}"})
    except ValueError as exc:
        raise ApiError(f"bad AC_REMOTE_URL {base!r}: {exc}") from exc
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise ApiError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach {base}: {exc.reason}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # The request may have reached the server: timeout, dropped connection, non-JSON reply, bad URL.
        hint = "; the command may still have been sent, check the unit" if method == "POST" else ""
        raise ApiError(f"no valid response from {base} for {method} {path}: {exc!r}{hint}") from exc


def run(argv: list[str]) -> dict | list:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Control A/C units through ac-remote.")
    parser.add_argument("unit", help="unit id, or 'list' to show units and their last commanded state")
    parser.add_argument("action", nargs="?", choices=("on", "off"))
    parser.add_argument("--mode")
    parser.add_argument("--temp", type=int)
    parser.add_argument("--fan")
    args = parser.parse_args(argv)
    if args.unit == "list":
        if args.action is not None:
            parser.error("'list' takes no action")
    elif args.action is None:
        parser.error("action on|off is required")

    units = _request("GET", "/api/units")
    if args.unit == "list":
        return units

    unit = next((u for u in units if u["id"] == args.unit), None)
    if unit is None:
        raise ApiError(f"unknown unit {args.unit!r}; known: {', '.join(u['id'] for u in units)}")

    body = {key: value for key, value in (unit.get("state") or {}).items() if key in _SETTINGS}
    body.update({key: getattr(args, key) for key in _SETTINGS if getattr(args, key) is not None})
    body["power"] = args.action == "on"
    log.info("sending unit=%s command=%s", args.unit, body)
    return _request("POST", f"/api/units/{urllib.parse.quote(args.unit, safe='')}", body)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except ApiError as exc:
        log.error("%s", exc)
        return 1
    print(json.dumps(result, ensure_ascii=False))  # the CLI's result on stdout, not a log line (log-allow)
    return 0


if __name__ == "__main__":
    sys.exit(main())
