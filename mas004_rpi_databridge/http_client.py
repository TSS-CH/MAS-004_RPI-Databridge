import httpx
import json
from typing import Optional

class HttpClient:
    def __init__(self, timeout_s: float, source_ip: Optional[str], verify_tls: bool):
        self.timeout_s = timeout_s
        self.source_ip = source_ip
        self.verify_tls = verify_tls

    def _transport(self) -> httpx.BaseTransport:
        if not self.source_ip:
            return httpx.HTTPTransport(retries=0)
        return httpx.HTTPTransport(retries=0, local_address=(self.source_ip, 0))

    def request(self, method: str, url: str, headers: dict, body: Optional[dict]):
        method = method.upper()
        with httpx.Client(timeout=self.timeout_s, verify=self.verify_tls, transport=self._transport()) as c:
            if method == "GET":
                r = c.get(url, headers=headers)
            elif method == "POST":
                r = c.post(url, headers=headers, content=json.dumps(body or {}))
            else:
                raise ValueError(f"Unsupported method: {method}")
            r.raise_for_status()
            return r.text
