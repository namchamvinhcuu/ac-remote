"""RM4.send: lazy connect, session reuse, reconnect + retry once, locked device — fake broadlink only."""

import threading

import pytest

from app import rm4 as rm4_mod
from app.rm4 import RM4


class FakeDevice:
    def __init__(self, fail_sends: int = 0, locked: bool = False):
        self.fail_sends = fail_sends
        self.is_locked = locked
        self.model = "RM4 mini (fake)"
        self.authed = 0
        self.sent: list[bytes] = []

    def auth(self):
        self.authed += 1

    def send_data(self, packet: bytes):
        if self.fail_sends:
            self.fail_sends -= 1
            raise OSError("stale session")
        self.sent.append(packet)


class FakeBroadlink:
    """Replaces broadlink.hello; hands out the queued devices (or exceptions) in order."""

    def __init__(self, *devices):
        self.queue = list(devices)
        self.calls: list[tuple[str, float]] = []

    def hello(self, host, timeout):
        self.calls.append((host, timeout))
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_bl(monkeypatch):
    def install(*devices):
        fake = FakeBroadlink(*devices)
        monkeypatch.setattr(rm4_mod.broadlink, "hello", fake.hello)  # patched where RM4 uses it
        return fake
    return install


def test_connects_lazily_and_reuses_the_session(fake_bl):
    dev = FakeDevice()
    bl = fake_bl(dev)
    rm4 = RM4("10.0.0.9", timeout=2.5)
    assert bl.calls == []  # no network on construction

    rm4.send(b"a")
    rm4.send(b"b")
    assert dev.sent == [b"a", b"b"]
    assert bl.calls == [("10.0.0.9", 2.5)]  # one hello for both sends
    assert dev.authed == 1


def test_session_uses_the_configured_timeout_not_broadlink_default(fake_bl):
    # Regression: hello(timeout=) only covers discovery; auth/send_data use device.timeout
    # (broadlink default 10 s), so RM4 must copy its own timeout onto the device.
    dev = FakeDevice()
    dev.timeout = 10
    seen = {}
    dev.auth = lambda: seen.setdefault("auth_timeout", dev.timeout)
    fake_bl(dev)
    RM4("10.0.0.9", timeout=2.5).send(b"pkt")
    assert dev.timeout == 2.5
    assert seen["auth_timeout"] == 2.5  # already set before auth()


def test_stale_session_reconnects_and_retries_once(fake_bl):
    stale, fresh = FakeDevice(fail_sends=1), FakeDevice()
    bl = fake_bl(stale, fresh)
    rm4 = RM4("10.0.0.9")
    rm4.send(b"pkt")
    assert stale.sent == [] and fresh.sent == [b"pkt"]
    assert len(bl.calls) == 2


def test_two_failures_raise_and_drop_the_session(fake_bl):
    first, second, third = FakeDevice(fail_sends=1), FakeDevice(fail_sends=1), FakeDevice()
    bl = fake_bl(first, second, third)
    rm4 = RM4("10.0.0.9")
    with pytest.raises(OSError, match="stale session"):
        rm4.send(b"pkt")
    assert len(bl.calls) == 2  # exactly one retry, no third attempt
    assert rm4._device is None

    rm4.send(b"pkt")  # next call starts a fresh session
    assert third.sent == [b"pkt"] and len(bl.calls) == 3


def test_unreachable_host_raises_after_retry(fake_bl):
    bl = fake_bl(TimeoutError("no reply"), TimeoutError("no reply"))
    with pytest.raises(TimeoutError):
        RM4("10.0.0.9").send(b"pkt")
    assert len(bl.calls) == 2


def test_hello_fails_once_then_succeeds(fake_bl):
    dev = FakeDevice()
    fake_bl(TimeoutError("no reply"), dev)
    RM4("10.0.0.9").send(b"pkt")
    assert dev.sent == [b"pkt"]


def test_concurrent_sends_are_serialized(fake_bl):
    # The RM4 session is not thread-safe: two requests (threadpool) must never
    # be inside send_data at the same time.
    release = threading.Event()
    entered = threading.Event()
    second = threading.Event()
    state = {"active": 0, "peak": 0, "calls": 0}
    guard = threading.Lock()

    class BlockingDevice(FakeDevice):
        def send_data(self, packet):
            with guard:
                state["active"] += 1
                state["calls"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] >= 2:
                    second.set()
            entered.set()
            release.wait(timeout=5)
            with guard:
                state["active"] -= 1
            self.sent.append(packet)

    dev = BlockingDevice()
    fake_bl(dev)
    rm4 = RM4("10.0.0.9")
    rm4._device = dev  # skip connect so both threads go straight to send_data
    threads = [threading.Thread(target=rm4.send, args=(p,), daemon=True) for p in (b"a", b"b")]
    threads[0].start()
    assert entered.wait(timeout=5)  # thread 1 is now inside send_data
    threads[1].start()
    # With a working lock thread 2 can never get in, so this wait always times out
    # (passing cannot be flaky); without the lock it returns as soon as it enters.
    overlapped = second.wait(timeout=0.5)
    peak_while_blocked = state["peak"]
    release.set()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert not overlapped
    assert peak_while_blocked == 1
    assert state["peak"] == 1 and state["calls"] == 2
    assert sorted(dev.sent) == [b"a", b"b"]


def test_locked_device_is_refused_and_never_sent_to(fake_bl):
    locked1, locked2 = FakeDevice(locked=True), FakeDevice(locked=True)
    fake_bl(locked1, locked2)
    with pytest.raises(RuntimeError, match="locked"):
        RM4("10.0.0.9").send(b"pkt")
    assert locked1.sent == [] and locked2.sent == []
    assert locked1.authed == 0 and locked2.authed == 0
