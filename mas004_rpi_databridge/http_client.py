# mas004_rpi_databridge/http_client.py
from __future__ import annotations

from typing import Optional, Dict, Any
import httpx


class HttpClient:
    def __init__(self, timeout_s: float = 10.0, source_ip: str = "", verify_tls: bool = True):
        self.timeout_s = float(timeout_s or 10.0)
        self.source_ip = (source_ip or "").strip()
        self.verify_tls = bool(verify_tls)

        self._timeout = httpx.Timeout(self.timeout_s)

        # Optional: an eth0 IP binden (source address)
        self._transport = None
        if self.source_ip:
            # httpx/httpcore erwartet i.d.R. (host, port)
            self._transport = httpx.HTTPTransport(local_address=(self.source_ip, 0))

    def request(self, method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        method = (method or "POST").upper()
        headers = dict(headers or {})

        # WICHTIG: verify_tls=False => verify=False (self-signed ok)
        verify = False if not self.verify_tls else True

        with httpx.Client(timeout=self._timeout, verify=verify, transport=self._transport) as c:
            r = c.request(method, url, headers=headers, json=body)

        # Fehler sauber hochwerfen, damit dein Outbox-Backoff greift
        if not (200 <= r.status_code < 300):
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")

        return {"status_code": r.status_code, "text": r.text}
