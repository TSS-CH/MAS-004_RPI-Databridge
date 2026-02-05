import json
import os
from dataclasses import dataclass, asdict
from typing import Optional

DEFAULT_CFG_PATH = "/etc/mas004_rpi_databridge/config.json"


@dataclass
class Settings:
    # Storage
    db_path: str = "/var/lib/mas004_rpi_databridge/databridge.db"

    # Web UI / API
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080

    # Network (display + optional apply via UI)
    eth0_ip: str = ""
    eth0_subnet: str = ""     # CIDR prefix as string or "255.255.255.0" (UI will normalize)
    eth0_gateway: str = ""

    eth1_ip: str = ""
    eth1_subnet: str = ""
    eth1_gateway: str = ""

    # Outgoing source binding (requests)
    eth0_source_ip: str = ""

    # Mikrotom peer
    peer_base_url: str = "http://127.0.0.1:9090"
    peer_watchdog_host: str = "127.0.0.1"
    peer_health_path: str = "/health"

    # Watchdog
    watchdog_interval_s: float = 2.0
    watchdog_timeout_s: float = 1.0
    watchdog_down_after: int = 3

    # HTTP client
    http_timeout_s: float = 10.0
    tls_verify: bool = False

    # Retry
    retry_base_s: float = 1.0
    retry_cap_s: float = 60.0

    # Auth
    ui_token: str = "change-me"
    shared_secret: str = ""

    # Device endpoints (future real integration; now used by UI + routing stubs)
    esp_host: str = "192.168.2.10"
    esp_port: int = 5000

    vj3350_host: str = "192.168.2.20"
    vj3350_port: int = 20000

    vj6530_host: str = "192.168.2.30"
    vj6530_port: int = 3007

    @staticmethod
    def load(path: str = DEFAULT_CFG_PATH) -> "Settings":
        if not os.path.exists(path):
            # Ensure dir exists
            os.makedirs(os.path.dirname(path), exist_ok=True)
            s = Settings()
            s.save(path)
            return s

        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        s = Settings()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def save(self, path: str = DEFAULT_CFG_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, sort_keys=False)
