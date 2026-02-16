import json
import os
from dataclasses import dataclass

DEFAULT_CFG_PATH = "/etc/mas004_rpi_databridge/config.json"

@dataclass
class Settings:
    # Storage
    db_path: str = "/var/lib/mas004_rpi_databridge/databridge.db"

    # Web UI
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080
    webui_https: bool = False
    webui_ssl_certfile: str = "/etc/mas004_rpi_databridge/certs/raspi.crt"
    webui_ssl_keyfile: str = "/etc/mas004_rpi_databridge/certs/raspi.key"

    # Interface labels (Info)
    eth0_ip: str = ""
    eth1_ip: str = ""
    eth0_subnet: str = "24"
    eth0_gateway: str = ""
    eth0_dns: str = ""
    eth1_subnet: str = "24"
    eth1_gateway: str = ""
    eth1_dns: str = ""
    eth0_source_ip: str = ""  # outgoing source bind for HttpClient (optional)

    # Peer (Microtom)
    peer_base_url: str = "http://127.0.0.1:9090"
    peer_watchdog_host: str = "127.0.0.1"
    peer_health_path: str = "/health"

    # Watchdog
    watchdog_interval_s: float = 5.0
    watchdog_timeout_s: float = 1.0
    watchdog_down_after: int = 3

    # HTTP client
    http_timeout_s: float = 10.0
    tls_verify: bool = False

    # Retry
    retry_base_s: float = 1.0
    retry_cap_s: float = 60.0

    # Device endpoints (optional; editable in UI)
    # ESP-PLC (HTTP)
    esp_host: str = ""
    esp_port: int = 0
    esp_simulation: bool = True
    esp_watchdog_host: str = ""

    # Printers
    vj3350_host: str = ""
    vj3350_port: int = 0
    vj3350_simulation: bool = True
    vj6530_host: str = ""
    vj6530_port: int = 0
    vj6530_simulation: bool = True

    # Daily log-file retention (days)
    logs_keep_days_all: int = 30
    logs_keep_days_esp: int = 30
    logs_keep_days_tto: int = 30
    logs_keep_days_laser: int = 30

    # UI/API auth
    ui_token: str = "change-me"
    shared_secret: str = ""

    @classmethod
    def load(cls, path: str = DEFAULT_CFG_PATH) -> "Settings":
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cfg = cls()
            cfg.save(path)
            return cfg
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    def save(self, path: str = DEFAULT_CFG_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2, sort_keys=False)
