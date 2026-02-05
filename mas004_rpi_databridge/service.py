import time
import threading
import json
import os
import uvicorn

from mas004_rpi_databridge.config import Settings, DEFAULT_CFG_PATH
from mas004_rpi_databridge.db import DB
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.http_client import HttpClient
from mas004_rpi_databridge.watchdog import Watchdog
from mas004_rpi_databridge.webui import build_app

def backoff_s(retry_count: int, base: float, cap: float) -> float:
    n = min(retry_count, 10)
    return min(cap, base * (2 ** n))

def sender_loop(cfg_path: str):
    while True:
        cfg = Settings.load(cfg_path)
        db = DB(cfg.db_path)
        outbox = Outbox(db)

        health_url = None
        if cfg.peer_health_path:
            health_url = cfg.peer_base_url.rstrip("/") + cfg.peer_health_path

        watchdog = Watchdog(
            host=cfg.peer_watchdog_host,
            interval_s=cfg.watchdog_interval_s,
            timeout_s=cfg.watchdog_timeout_s,
            down_after=cfg.watchdog_down_after,
            health_url=health_url,
            tls_verify=cfg.tls_verify
        )

        client = HttpClient(timeout_s=cfg.http_timeout_s, source_ip=cfg.eth0_source_ip, verify_tls=cfg.tls_verify)

        while True:
            up = watchdog.tick()
            if not up:
                time.sleep(cfg.watchdog_interval_s)
                continue

            job = outbox.next_due()
            if not job:
                time.sleep(0.2)
                continue

            try:
                headers = json.loads(job.headers_json)
                body = json.loads(job.body_json) if job.body_json else None

                print(f"[OUTBOX] send id={job.id} rc={job.retry_count} {job.method} {job.url}", flush=True)
                resp = client.request(job.method, job.url, headers, body)
                print(f"[OUTBOX] ok   id={job.id} resp={resp}", flush=True)

                outbox.delete(job.id)

            except Exception as e:
                rc = job.retry_count + 1
                next_ts = time.time() + backoff_s(rc, cfg.retry_base_s, cfg.retry_cap_s)
                print(f"[OUTBOX] FAIL id={job.id} rc={rc} next_in={int(next_ts-time.time())}s err={repr(e)}", flush=True)
                outbox.reschedule(job.id, rc, next_ts)

def main():
    cfg_path = DEFAULT_CFG_PATH
    cfg = Settings.load(cfg_path)

    t = threading.Thread(target=sender_loop, args=(cfg_path,), daemon=True)
    t.start()

    app = build_app(cfg_path)

    ssl_kwargs = {}
    if cfg.webui_https:
        # Nur aktivieren, wenn Dateien existieren – sonst klare Fehlermeldung
        if not (os.path.exists(cfg.webui_ssl_certfile) and os.path.exists(cfg.webui_ssl_keyfile)):
            raise RuntimeError(
                f"HTTPS aktiviert, aber Zertifikat/Key fehlt: cert={cfg.webui_ssl_certfile} key={cfg.webui_ssl_keyfile}"
            )
        ssl_kwargs = {
            "ssl_certfile": cfg.webui_ssl_certfile,
            "ssl_keyfile": cfg.webui_ssl_keyfile,
        }

    uvicorn.run(app, host=cfg.webui_host, port=cfg.webui_port, log_level="info", **ssl_kwargs)
