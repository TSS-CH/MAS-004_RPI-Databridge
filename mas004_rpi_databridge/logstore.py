import os
from typing import List, Dict, Any
from mas004_rpi_databridge.db import DB, now_ts

DEFAULT_LOG_DIR = "/var/lib/mas004_rpi_databridge/logs"

# Channels that should ALWAYS exist in the UI dropdown, even if no logs exist yet.
# "all" is a virtual channel (aggregates all channels).
DEFAULT_LOG_CHANNELS = ["all", "raspi", "esp-plc", "vj3350", "vj6530"]


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
                (ts, channel, direction, message),
            )
            # Retention: pro Channel nur die letzten ~5000 Einträge
            c.execute(
                """DELETE FROM logs
                   WHERE channel=?
                     AND id NOT IN (
                       SELECT id FROM logs WHERE channel=? ORDER BY id DESC LIMIT 5000
                     )""",
                (channel, channel),
            )

        # Datei
        fn = os.path.join(self.log_dir, f"{channel}.log")
        line = f"{ts:.3f}\t{direction.upper()}\t{message}\n"
        try:
            with open(fn, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def list_logs(self, channel: str, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))

        with self.db._conn() as c:
            if channel == "all":
                rows = c.execute(
                    "SELECT ts, channel, direction, message FROM logs ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                # newest first -> return oldest first
                return [
                    {"ts": r[0], "channel": r[1], "direction": r[2], "message": r[3]}
                    for r in rows[::-1]
                ]

            rows = c.execute(
                "SELECT ts, direction, message FROM logs WHERE channel=? ORDER BY ts DESC LIMIT ?",
                (channel, limit),
            ).fetchall()

        return [{"ts": r[0], "direction": r[1], "message": r[2]} for r in rows[::-1]]

    def read_logfile(self, channel: str, max_bytes: int = 500_000) -> str:
        if channel == "all":
            # Aggregated view from DB (unabhängig von Files)
            items = self.list_logs("all", limit=2000)
            lines = []
            for it in items:
                ts = float(it.get("ts") or 0.0)
                ch = str(it.get("channel", ""))
                direction = str(it.get("direction", "")).upper()
                msg = str(it.get("message", ""))
                lines.append(f"{ts:.3f}\t{ch}\t{direction}\t{msg}")
            txt = "\n".join(lines) + ("\n" if lines else "")

            b = txt.encode("utf-8", errors="replace")
            if len(b) > max_bytes:
                b = b[-max_bytes:]
            return b.decode("utf-8", errors="replace")

        fn = os.path.join(self.log_dir, f"{channel}.log")
        if not os.path.exists(fn):
            return ""
        with open(fn, "rb") as f:
            data = f.read()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")

    def clear_channel(self, channel: str) -> Dict[str, Any]:
        if channel == "all":
            # DB clear all
            with self.db._conn() as c:
                c.execute("DELETE FROM logs")

            # file clear all (*.log)
            try:
                for fn in os.listdir(self.log_dir):
                    if fn.endswith(".log"):
                        try:
                            os.remove(os.path.join(self.log_dir, fn))
                        except Exception:
                            pass
            except Exception:
                pass

            return {"ok": True}

        # DB clear
        with self.db._conn() as c:
            c.execute("DELETE FROM logs WHERE channel=?", (channel,))

        # file clear
        fn = os.path.join(self.log_dir, f"{channel}.log")
        try:
            if os.path.exists(fn):
                os.remove(fn)
        except Exception:
            pass
        return {"ok": True}

    def list_channels(self) -> List[str]:
        # Always include defaults
        ch = set(DEFAULT_LOG_CHANNELS)

        # channels from DB + files
        with self.db._conn() as c:
            rows = c.execute("SELECT DISTINCT channel FROM logs").fetchall()
            for r in rows:
                ch.add(str(r[0]))

        try:
            for fn in os.listdir(self.log_dir):
                if fn.endswith(".log"):
                    ch.add(fn[:-4])
        except Exception:
            pass

        # Keep default order first, then extras sorted
        out = []
        for d in DEFAULT_LOG_CHANNELS:
            if d in ch:
                out.append(d)
        extras = sorted([x for x in ch if x not in DEFAULT_LOG_CHANNELS])
        return out + extras
