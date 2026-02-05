import json
from dataclasses import dataclass, asdict
from typing import Optional

DEFAULT_CFG_PATH = "/etc/mas004_rpi_databridge/config.json"

@dataclass
class Settings:
    # Storage
    db_path: str = "/var/lib/mas004_rpi_databridge/databridge.db"

    # Shared Secret
    shared_secret: str = ""

    # Web UI / API
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080

    # Network (informative; IP set via dhcpcd already)
    eth0_ip: str = "192.168.1.100"
    eth1_ip: str = "192.168.2.100"

    # Force outgoing HTTPS via eth0 by binding source IP
    eth0_source_ip: Optional[str] = "192.168.1.100"

    # Peer (remote endpoint)
    peer_base_url: str = "https://192.168.1.10"
    peer_watchdog_host: str = "192.168.1.10"
    peer_health_path: str = "/health"
    watchdog_interval_s: float = 2.0
    watchdog_timeout_s: float = 1.0
    watchdog_down_after: int = 3

    # HTTPS
    http_timeout_s: float = 10.0
    tls_verify: bool = True

    # Retry
    retry_base_s: float = 1.0
    retry_cap_s: float = 60.0

    # Simple UI token (change!)
    ui_token: str = "change-me"

    @classmethod
    def load(cls, path: str = DEFAULT_CFG_PATH) -> "Settings":
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))

    def save(self, path: str = DEFAULT_CFG_PATH):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
