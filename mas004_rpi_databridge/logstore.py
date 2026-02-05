import os
from typing import Optional
from mas004_rpi_databridge.db import DB, now_ts

DEFAULT_LOG_DIR = "/var/lib/mas004_rpi_databridge/logs"

class LogStore:
    def __init__(self, db: DB, log_dir: str = DEFAULT_LOG_DIR):
        self.db = db
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def log(self, channel: str, direction: str, message: str):
        ts = now_ts()
        # DB
        with self.db._conn() as c:
            c.execute(
                "INSERT INTO logs(ts, channel, direction, message) VALUES (?,?,?,?)",
                (ts, channel, direction, message)
            )
            # einfache Retention: pro Channel nur die letzten ~5000 Einträge
            c.execute(
                """DELETE FROM logs
                   WHERE channel=?
                     AND id NOT IN (
                       SELECT id FROM logs WHERE channel=? ORDER BY id DESC LIMIT 5000
                     )""",
                (channel, channel)
            )

        # Datei (append)
        fn = os.path.join(self.log_dir, f"{channel}.log")
        line = f"{ts:.3f}\t{direction.upper()}\t{message}\n"
        try:
            with open(fn, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def list_logs(self, channel: str, limit: int = 200):
        limit = max(1, min(int(limit), 2000))
        with self.db._conn() as c:
            rows = c.execute(
                "SELECT ts, direction, message FROM logs WHERE channel=? ORDER BY ts DESC LIMIT ?",
                (channel, limit)
            ).fetchall()
        # newest first -> UI mag oft oldest first
        return [{"ts": r[0], "direction": r[1], "message": r[2]} for r in rows[::-1]]

    def read_logfile(self, channel: str, max_bytes: int = 200_000) -> str:
        fn = os.path.join(self.log_dir, f"{channel}.log")
        if not os.path.exists(fn):
            return ""
        with open(fn, "rb") as f:
            data = f.read()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")
