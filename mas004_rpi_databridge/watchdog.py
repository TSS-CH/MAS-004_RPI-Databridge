import time
from typing import Optional

from ping3 import ping
import httpx


class Watchdog:
    def __init__(
        self,
        host: str,
        interval_s: float,
        timeout_s: float,
        down_after: int,
        health_url: Optional[str] = None,
        tls_verify: bool = True,
    ):
        self.host = host
        self.interval_s = float(interval_s)
        self.timeout_s = float(timeout_s)
        self.down_after = int(down_after)
        self.health_url = health_url
        self.tls_verify = bool(tls_verify)

        self._fail = 0
        self.is_up = True
        self._next_check_ts = 0.0

        self._client = httpx.Client(timeout=self.timeout_s, verify=self.tls_verify)

    def _ping_ok(self) -> bool:
        try:
            r = ping(self.host, timeout=self.timeout_s, unit="s")
            return r is not None
        except Exception:
            return False

    def _health_ok(self) -> bool:
        if not self.health_url:
            return True
        try:
            r = self._client.get(self.health_url)
            return 200 <= r.status_code < 300
        except Exception:
            return False

    def tick(self) -> bool:
        now = time.time()
        if now < self._next_check_ts:
            # do NOT spam; return last state
            return self.is_up

        # Wenn der Peer als "down" gilt, schneller erneut pruefen, um schneller zu recovern.
        # Das verhindert mehrsekundige "Loecher" bei kurzzeitigen Netzwerkstoehrungen.
        next_interval = self.interval_s if self.is_up else min(self.interval_s, 0.5)
        self._next_check_ts = now + next_interval

        # Performance/robustness:
        # Wenn ein Health-Endpoint konfiguriert ist, nutze ihn als primaere Quelle.
        # Ping ist dann nur noch Fallback (ICMP ist in manchen Netzen geblockt/instabil).
        if self.health_url:
            ok = self._health_ok()
            if not ok and self.host:
                ok = self._ping_ok()
        else:
            ok = self._ping_ok()

        self._fail = 0 if ok else (self._fail + 1)
        self.is_up = (self._fail < self.down_after)
        return self.is_up
