from __future__ import annotations

import threading
import time


class Vj6530RuntimeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_async_ok_ts = 0.0
        self._last_async_event_ts = 0.0
        self._last_async_error = ""

    def mark_async_ok(self):
        now = time.monotonic()
        with self._lock:
            self._last_async_ok_ts = now
            self._last_async_error = ""

    def mark_async_event(self):
        now = time.monotonic()
        with self._lock:
            self._last_async_ok_ts = now
            self._last_async_event_ts = now
            self._last_async_error = ""

    def mark_async_error(self, detail: str):
        with self._lock:
            self._last_async_error = str(detail or "")

    def async_recent(self, max_age_s: float) -> bool:
        with self._lock:
            last_ok = self._last_async_ok_ts
        return (time.monotonic() - last_ok) <= max(0.0, float(max_age_s or 0.0))

    def snapshot(self) -> dict[str, float | str]:
        with self._lock:
            return {
                "last_async_ok_ts": self._last_async_ok_ts,
                "last_async_event_ts": self._last_async_event_ts,
                "last_async_error": self._last_async_error,
            }


RUNTIME = Vj6530RuntimeState()
