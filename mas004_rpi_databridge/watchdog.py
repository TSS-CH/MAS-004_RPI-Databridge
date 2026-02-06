# mas004_rpi_databridge/watchdog.py
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

        # Rate limit: nur alle interval_s wirklich prüfen
        self._next_check_ts = 0.0  # 0 => beim ersten tick() sofort prüfen

    def tick(self) -> bool:
        now = time.time()

        # Wenn wir zu früh dran sind: alten Status zurückgeben, keine neuen Requests
        if now < self._next_check_ts:
            return self.is_up

        # Nächster erlaubter Check-Zeitpunkt
        self._next_check_ts = now + self.interval_s

        ok = self._ping_ok()
        if ok and self.health_url:
            ok = self._health_ok()

        self._fail = 0 if ok else (self._fail + 1)
        self.is_up = (self._fail < self.down_after)
        return self.is_up

    def _ping_ok(self) -> bool:
        try:
            # ping() gibt None bei Timeout zurück
            return ping(self.host, timeout=self.timeout_s, unit="ms") is not None
        except Exception:
            return False

    def _health_ok(self) -> bool:
        if not self.health_url:
            return True
        try:
            with httpx.Client(timeout=self.timeout_s, verify=self.tls_verify) as c:
                r = c.get(self.health_url)
                return 200 <= r.status_code < 300
        except Exception:
            return False
