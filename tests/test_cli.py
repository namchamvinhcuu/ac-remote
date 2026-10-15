"""app.cli end to end: `python -m app.cli` in a subprocess against the real app served by uvicorn on an ephemeral port.

The CLI talks urllib, so TestClient cannot be injected; a real server keeps the whole request path (auth header,
JSON body, HTTP error mapping) under test. The RM4 is a fake — nothing reaches real hardware.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import uvicorn

from app.config import Unit
from app.main import create_app
from app.state import StateStore

REPO = Path(__file__).resolve().parent.parent
USER, PASSWORD = "admin", "secret-password"


class FakeRM4:
    def __init__(self, fail: bool = False):
        self.sent: list[bytes] = []
        self.fail = fail

    def send(self, packet: bytes) -> None:
        if self.fail:
            raise TimeoutError("no reply")
        self.sent.append(packet)


def _serve(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.01)
    return server, thread, f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.fixture
def api(tmp_path):
    """Yields a factory: api(fail=False) -> (url, senders, store)."""
    running = []

    def start(fail: bool = False):
        units = {
            "reetech": Unit("reetech", "Reetech", "mitsubishi112", "10.0.0.1"),
            "midea": Unit("midea", "Midea", "coolix", "10.0.0.2"),
        }
        senders = {"10.0.0.1": FakeRM4(fail), "10.0.0.2": FakeRM4(fail)}
        store = StateStore(tmp_path / "state.json")
        server, thread, url = _serve(create_app(units, store, senders.__getitem__, USER, PASSWORD))
        running.append((server, thread))
        return url, senders, store

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=10)


def cli(url: str, *args: str, password: str = PASSWORD) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("APP_USER", "APP_PASSWORD", "AC_REMOTE_URL", "LOG_LEVEL")}
    env.update(AC_REMOTE_URL=url, APP_USER=USER, APP_PASSWORD=password)
    return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)


def sent_count(senders) -> int:
    return sum(len(s.sent) for s in senders.values())


# --- happy -------------------------------------------------------------------------------------------------


def test_list_prints_units_json(api):
    url, senders, store = api()
    store.set("midea", {"power": True, "mode": "cool", "temp": 24, "fan": "low"})

    res = cli(url, "list")

    assert res.returncode == 0, res.stderr
    units = json.loads(res.stdout)
    assert [u["id"] for u in units] == ["reetech", "midea"]
    assert units[1]["state"] == {"power": True, "mode": "cool", "temp": 24, "fan": "low"}
    assert units[0]["state"] is None
    assert sent_count(senders) == 0


def test_first_command_without_prior_state_uses_server_defaults(api):
    url, senders, store = api()

    res = cli(url, "midea", "on")

    assert res.returncode == 0, res.stderr
    expected = {"power": True, "mode": "cool", "temp": 25, "fan": "auto"}
    assert json.loads(res.stdout) == {"id": "midea", "state": expected}
    assert len(senders["10.0.0.2"].sent) == 1 and not senders["10.0.0.1"].sent
    assert store.get("midea") == expected


def test_on_reuses_last_commanded_state(api):
    url, senders, store = api()
    store.set("reetech", {"power": False, "mode": "heat", "temp": 28, "fan": "max"})

    res = cli(url, "reetech", "on")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["state"] == {"power": True, "mode": "heat", "temp": 28, "fan": "max"}
    assert len(senders["10.0.0.1"].sent) == 1


def test_flags_override_last_state_and_others_are_kept(api):
    url, _, store = api()
    store.set("reetech", {"power": True, "mode": "heat", "temp": 28, "fan": "max"})

    res = cli(url, "reetech", "on", "--temp", "22", "--mode", "cool")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["state"] == {"power": True, "mode": "cool", "temp": 22, "fan": "max"}

    res = cli(url, "reetech", "on", "--fan", "low")
    assert res.returncode == 0, res.stderr
    assert store.get("reetech") == {"power": True, "mode": "cool", "temp": 22, "fan": "low"}


def test_off_powers_down_and_keeps_last_settings(api):
    url, senders, store = api()
    store.set("midea", {"power": True, "mode": "heat", "temp": 27, "fan": "min"})

    res = cli(url, "midea", "off")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["state"] == {"power": False, "mode": "heat", "temp": 27, "fan": "min"}
    assert store.get("midea") == {"power": False, "mode": "heat", "temp": 27, "fan": "min"}
    assert len(senders["10.0.0.2"].sent) == 1


# --- errors ------------------------------------------------------------------------------------------------


def test_wrong_password_exits_1_with_401_and_sends_nothing(api):
    url, senders, _ = api()

    res = cli(url, "midea", "on", password="wrong-password")  # test fixture, not a real secret (secret-allow)

    assert res.returncode == 1
    assert "HTTP 401" in res.stderr
    assert res.stdout == ""
    assert sent_count(senders) == 0


def test_invalid_temp_exits_1_with_422_detail(api):
    url, senders, store = api()

    res = cli(url, "midea", "on", "--temp", "40")

    assert res.returncode == 1
    assert "HTTP 422 POST /api/units/midea: temp 40 out of range 17-30" in res.stderr  # server detail relayed
    assert res.stdout == ""
    assert sent_count(senders) == 0 and store.get("midea") is None


def test_unknown_unit_exits_1_listing_known_ids_and_sends_nothing(api):
    url, senders, _ = api()

    res = cli(url, "bedroom", "on")

    assert res.returncode == 1
    assert "unknown unit 'bedroom'" in res.stderr
    assert "known: reetech, midea" in res.stderr
    assert res.stdout == ""
    assert sent_count(senders) == 0


def _dead_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens on this port any more
    return f"http://127.0.0.1:{port}"


def test_unreachable_url_exits_1(api):
    res = cli(_dead_url(), "list")

    assert res.returncode == 1
    assert "cannot reach" in res.stderr
    assert "Traceback" not in res.stderr
    assert res.stdout == ""


def test_rm4_failure_exits_1_with_502_detail_without_host(api):
    url, _, store = api(fail=True)

    res = cli(url, "midea", "on")

    assert res.returncode == 1
    assert "HTTP 502 POST /api/units/midea: RM4 unreachable for unit 'midea'; see server log" in res.stderr
    assert "10.0.0.2" not in res.stderr
    assert res.stdout == ""
    assert store.get("midea") is None


@pytest.mark.parametrize("args", [["midea"], ["midea", "toggle"], ["midea", "on", "--temp", "warm"]])
def test_usage_errors_exit_2_and_send_nothing(api, args):
    url, senders, _ = api()

    res = cli(url, *args)

    assert res.returncode == 2
    assert "usage:" in res.stderr
    assert res.stdout == ""
    assert sent_count(senders) == 0


def test_list_with_action_is_a_usage_error(api):
    url, _, _ = api()
    res = cli(url, "list", "off")
    assert res.returncode == 2
    assert "'list' takes no action" in res.stderr


@pytest.mark.parametrize("args", [["midea"], ["list", "on"]])
def test_usage_errors_are_checked_before_any_request(rogue, args):
    # Regression: arg checks used to run after GET /api/units, so a down server turned a usage error into rc 1.
    url, requests = rogue("close")
    assert cli(_dead_url(), *args).returncode == 2
    res = cli(url, *args)
    assert res.returncode == 2
    assert requests == []


# --- rogue server: answers GET /api/units, misbehaves as told --------------------------------------------------

_UNITS = [{"id": "midea", "name": "Midea", "protocol": "coolix", "state": None}]


def _http(body: bytes, content_type: str) -> bytes:
    head = f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    return head.encode() + body


def _read_request(conn) -> bytes:
    """Read headers plus the Content-Length body. http.client sends a POST body in a separate send(), and closing
    with it still unread makes Linux answer with RST instead of FIN — the client then sees ConnectionResetError."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return data
        data += chunk
    head, body = data.split(b"\r\n\r\n", 1)
    length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")[1:] if line.lower().startswith(b"content-length:")), 0)
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk
    return data


@pytest.fixture
def rogue():
    """rogue(behaviour, on="any"|"POST", units=None) -> (url, requests). Behaviour: close | html | hang | truncated.
    With on="POST", GET /api/units is answered with `units` and only the POST misbehaves.
    truncated: a 502 status line whose body stops after a few of the announced Content-Length bytes."""
    listeners, held = [], []

    def start(behaviour: str, on: str = "any", units: list | None = None):
        units_json = json.dumps(_UNITS if units is None else units).encode()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        srv.settimeout(0.2)
        listeners.append(srv)
        requests: list[str] = []

        def loop():
            while srv.fileno() != -1:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    continue
                conn.settimeout(5)
                request_line = _read_request(conn).split(b"\r\n", 1)[0].decode()
                requests.append(request_line)
                if on == "POST" and not request_line.startswith("POST"):
                    conn.sendall(_http(units_json, "application/json"))
                    conn.close()
                elif behaviour == "close":
                    conn.close()
                elif behaviour == "html":
                    conn.sendall(_http(b"<html>proxy login</html>", "text/html"))
                    conn.close()
                elif behaviour == "truncated":
                    head = "HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n"
                    conn.sendall(head.encode() + b'{"detail": "RM4')
                    conn.close()
                else:  # hang: keep the connection open, never answer
                    held.append(conn)

        threading.Thread(target=loop, daemon=True).start()
        return f"http://127.0.0.1:{srv.getsockname()[1]}", requests

    yield start
    for sock in listeners + held:
        sock.close()


def _assert_clean_error(res, *fragments):
    assert res.returncode == 1
    assert res.stdout == ""
    assert "Traceback" not in res.stderr
    error_lines = [line for line in res.stderr.splitlines() if " ERROR app.cli " in line]
    assert len(error_lines) == 1, res.stderr
    for fragment in fragments:
        assert fragment in error_lines[0], error_lines[0]
    return error_lines[0]


@pytest.mark.parametrize("behaviour,fragment", [("close", "RemoteDisconnected"), ("html", "JSONDecodeError")])
def test_bad_response_to_list_is_a_clean_error(rogue, behaviour, fragment):
    # Regression: dropped connection / non-JSON 200 (e.g. a proxy page) crashed with a traceback.
    url, _ = rogue(behaviour)
    line = _assert_clean_error(cli(url, "list"), "no valid response from", "GET /api/units", fragment)
    assert "may still have been sent" not in line  # GET never switches anything


def test_bad_url_scheme_is_a_clean_error():
    res = cli("ftp//nohost", "list")
    _assert_clean_error(res, "bad AC_REMOTE_URL", "ftp//nohost")


def test_post_without_response_warns_the_command_may_have_been_sent(rogue):
    url, requests = rogue("close", on="POST")
    _assert_clean_error(cli(url, "midea", "on"), "POST /api/units/midea", "may still have been sent")
    assert [r.split(" ", 2)[:2] for r in requests] == [["GET", "/api/units"], ["POST", "/api/units/midea"]]


@pytest.mark.parametrize("args,on,hint", [(["list"], "any", False), (["midea", "off"], "POST", True)])
def test_read_timeout_raises_api_error_not_a_traceback(rogue, monkeypatch, args, on, hint):
    # Regression: a read timeout escaped urllib as a bare TimeoutError. In-process so _TIMEOUT can be shortened.
    from app import cli as cli_module

    url, _ = rogue("hang", on=on)
    monkeypatch.setenv("AC_REMOTE_URL", url)
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setattr(cli_module, "_TIMEOUT", 0.5)

    started = time.monotonic()
    with pytest.raises(cli_module.ApiError) as err:
        cli_module.run(args)

    assert time.monotonic() - started < 10
    assert "no valid response from" in str(err.value) and "timed out" in str(err.value)
    assert ("may still have been sent" in str(err.value)) is hint


def test_unit_id_is_url_quoted_in_the_post_path(rogue):
    # A unit id with '/' or '?' must stay one path segment.
    url, requests = rogue("close", on="POST", units=[{"id": "a/b?c", "name": "x", "protocol": "coolix", "state": None}])
    cli(url, "a/b?c", "on")
    assert requests[-1].split(" ")[1] == "/api/units/a%2Fb%3Fc"


# --- HTTP error whose body cannot be read (python-reviewer finding, 2026-10-15) ----------------------------------


@pytest.mark.parametrize("args,on,request_part", [(["list"], "any", "GET /api/units"), (["midea", "on"], "POST", "POST /api/units/midea")])
def test_http_error_with_truncated_body_is_a_clean_error(rogue, args, on, request_part):
    # Regression: exc.read() in the HTTPError handler raised IncompleteRead on a body cut short, escaping as a traceback.
    url, _ = rogue("truncated", on=on)
    line = _assert_clean_error(cli(url, *args), f"HTTP 502 {request_part}", "error body unreadable", "IncompleteRead")
    assert "no valid response from" not in line  # the status code arrived; it is reported as an HTTP error


class _BrokenBody:
    def __init__(self, exc: BaseException):
        self.exc = exc

    def read(self, *args):
        raise self.exc

    def close(self):
        pass


@pytest.mark.parametrize(
    "read_exc,name",
    [(http.client.IncompleteRead(b'{"det', 40), "IncompleteRead"), (ConnectionResetError(104, "Connection reset by peer"), "ConnectionResetError")],
)
def test_http_error_body_read_failure_raises_api_error(monkeypatch, read_exc, name):
    # Regression (same finding): any OSError / HTTPException while reading the error body still maps to ApiError.
    from app import cli as cli_module

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", http.client.HTTPMessage(), _BrokenBody(read_exc))

    monkeypatch.setenv("AC_REMOTE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(cli_module.ApiError) as err:
        cli_module.run(["list"])

    assert str(err.value).startswith("HTTP 503 GET /api/units: <error body unreadable: ")
    assert name in str(err.value)
    assert isinstance(err.value.__cause__, urllib.error.HTTPError)
    assert cli_module.main(["list"]) == 1
