"""Thread-safe sender for one BroadLink RM4 mini, reconnecting on failure."""

import logging
import threading

import broadlink

log = logging.getLogger(__name__)


class RM4:
    def __init__(self, host: str, timeout: float = 5.0):
        self.host = host
        self.timeout = timeout
        self._device = None
        self._lock = threading.Lock()

    def _connect(self):
        device = broadlink.hello(self.host, timeout=self.timeout)
        if device.is_locked:
            raise RuntimeError(f"RM4 {self.host} is locked; disable 'Lock device' in the BroadLink app")
        device.timeout = self.timeout  # hello() only applies it to discovery; auth/send_data default to 10 s
        device.auth()
        log.info("rm4 connected host=%s model=%s", self.host, device.model)
        return device

    def send(self, packet: bytes) -> None:
        """Send one IR packet; reconnect and retry once if the session went stale."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._device is None:
                        self._device = self._connect()
                    self._device.send_data(packet)
                    return
                except Exception:
                    self._device = None
                    log.warning("rm4 send failed host=%s attempt=%d", self.host, attempt, exc_info=True)
                    if attempt == 2:
                        raise
