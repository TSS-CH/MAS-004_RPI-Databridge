import os
import re
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional

from mas004_rpi_databridge.config import Settings, DEFAULT_CFG_PATH
from mas004_rpi_databridge.db import DB, now_ts

DEFAULT_LOG_DIR = "/var/lib/mas004_rpi_databridge/logs"

# Channels that should ALWAYS exist in the UI dropdown, even if no logs exist yet.
# "all" is a virtual channel (aggregates all channels).
DEFAULT_LOG_CHANNELS = ["all", "raspi", "esp-plc", "vj3350", "vj6530"]

DAILY_GROUP_ALL = "all"
DAILY_GROUP_ESP = "esp"
DAILY_GROUP_TTO = "tto"
DAILY_GROUP_LASER = "laser"
DAILY_GROUPS = [DAILY_GROUP_ALL, DAILY_GROUP_ESP, DAILY_GROUP_TTO, DAILY_GROUP_LASER]

DAILY_GROUP_LABELS = {
    DAILY_GROUP_ALL: "Raspi All Communications",
    DAILY_GROUP_ESP: "ESP32-PLC",
    DAILY_GROUP_TTO: "TTO 6530",
    DAILY_GROUP_LASER: "Laser 3350",
}

DAILY_GROUP_PREFIX = {
    DAILY_GROUP_ALL: "raspi_all",
    DAILY_GROUP_ESP: "esp32_plc",
    DAILY_GROUP_TTO: "tto_6530",
    DAILY_GROUP_LASER: "laser_3350",
}

DAILY_CHANNEL_GROUP = {
    "esp-plc": DAILY_GROUP_ESP,
    "vj6530": DAILY_GROUP_TTO,
    "vj3350": DAILY_GROUP_LASER,
}


class LogStore:
    def __init__(self, db: DB, log_dir: str = DEFAULT_LOG_DIR, cfg_path: str = DEFAULT_CFG_PATH):
        self.db = db
        self.log_dir = log_dir
        self.cfg_path = cfg_path
        self._next_housekeeping_ts = 0.0
        os.makedirs(self.log_dir, exist_ok=True)

    def log(self, channel: str, direction: str, message: str):
        ts = now_ts()
        channel = (channel or "raspi").strip() or "raspi"
        direction = (direction or "").strip().upper()

        # DB
        with self.db._conn() as c:
            c.execute(
                "INSERT INTO logs(ts, channel, direction, message) VALUES (?,?,?,?)",
                (ts, channel, direction, message),
            )
            # Retention in DB: per channel keep last ~5000 entries.
            c.execute(
                """DELETE FROM logs
                   WHERE channel=?
                     AND id NOT IN (
                       SELECT id FROM logs WHERE channel=? ORDER BY id DESC LIMIT 5000
                     )""",
                (channel, channel),
            )

        self._write_daily_logfiles(ts, channel, direction, message)
        self._maybe_housekeeping(ts)

    def _log_line(self, ts: float, channel: str, direction: str, message: str) -> str:
        dt = datetime.fromtimestamp(ts)
        return f"[{dt:%Y-%m-%d %H:%M:%S}.{int(dt.microsecond/1000):03d}] [{channel}] {direction} {message}\n"

    def _groups_for_channel(self, channel: str) -> List[str]:
        groups = [DAILY_GROUP_ALL]
        g = DAILY_CHANNEL_GROUP.get((channel or "").strip())
        if g:
            groups.append(g)
        return groups

    def _daily_path(self, group: str, d: date) -> str:
        prefix = DAILY_GROUP_PREFIX[group]
        return os.path.join(self.log_dir, f"{prefix}_{d:%Y-%m-%d}.txt")

    def _write_daily_logfiles(self, ts: float, channel: str, direction: str, message: str):
        d = datetime.fromtimestamp(ts).date()
        line = self._log_line(ts, channel, direction, message)
        for group in self._groups_for_channel(channel):
            fn = self._daily_path(group, d)
            try:
                with open(fn, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                # logging errors must never break runtime path
                pass

    def _safe_days(self, value: Any, default_v: int) -> int:
        try:
            n = int(value)
        except Exception:
            n = int(default_v)
        return max(1, min(n, 3650))

    def retention_map_from_settings(self, cfg: Optional[Settings] = None) -> Dict[str, int]:
        cfg2 = cfg if cfg is not None else Settings.load(self.cfg_path)
        return {
            DAILY_GROUP_ALL: self._safe_days(getattr(cfg2, "logs_keep_days_all", 30), 30),
            DAILY_GROUP_ESP: self._safe_days(getattr(cfg2, "logs_keep_days_esp", 30), 30),
            DAILY_GROUP_TTO: self._safe_days(getattr(cfg2, "logs_keep_days_tto", 30), 30),
            DAILY_GROUP_LASER: self._safe_days(getattr(cfg2, "logs_keep_days_laser", 30), 30),
        }

    def apply_retention(self, cfg: Optional[Settings] = None):
        keep_map = self.retention_map_from_settings(cfg)
        today = datetime.now().date()
        items = self.list_daily_files()

        for it in items:
            group = it.get("group")
            d = it.get("_date_obj")
            path = it.get("_path")
            if not group or not d or not path:
                continue

            keep_days = keep_map.get(group, 30)
            cutoff = today - timedelta(days=max(1, keep_days) - 1)
            if d < cutoff:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _maybe_housekeeping(self, ts: float):
        if ts < self._next_housekeeping_ts:
            return
        self._next_housekeeping_ts = ts + 300.0
        try:
            self.apply_retention()
        except Exception:
            pass

    def list_logs(self, channel: str, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Returns logs oldest->newest (UI-friendly).
        For channel='all' it returns aggregated logs including the channel field.
        """
        limit = max(1, min(int(limit), 2000))
        channel = (channel or "").strip()

        with self.db._conn() as c:
            if channel == "all":
                rows = c.execute(
                    "SELECT ts, channel, direction, message FROM logs ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [
                    {"ts": r[0], "channel": r[1], "direction": r[2], "message": r[3]}
                    for r in rows[::-1]
                ]

            rows = c.execute(
                "SELECT ts, channel, direction, message FROM logs WHERE channel=? ORDER BY ts DESC LIMIT ?",
                (channel, limit),
            ).fetchall()

        return [
            {"ts": r[0], "channel": r[1], "direction": r[2], "message": r[3]}
            for r in rows[::-1]
        ]

    def read_logfile(self, channel: str, max_bytes: int = 500_000) -> str:
        """
        Legacy endpoint for test UI download.
        Returns text generated from DB to avoid dependency on legacy single-file logs.
        """
        channel = (channel or "").strip()
        limit = 2500 if channel == "all" else 1500
        items = self.list_logs(channel if channel else "all", limit=limit)
        lines = []
        for it in items:
            ts = float(it.get("ts") or 0.0)
            ch = str(it.get("channel") or "")
            direction = str(it.get("direction") or "").upper()
            msg = str(it.get("message") or "")
            dt = datetime.fromtimestamp(ts)
            if channel == "all":
                lines.append(f"[{dt:%Y-%m-%d %H:%M:%S}.{int(dt.microsecond/1000):03d}] [{ch}] {direction} {msg}")
            else:
                lines.append(f"[{dt:%Y-%m-%d %H:%M:%S}.{int(dt.microsecond/1000):03d}] {direction} {msg}")

        txt = "\n".join(lines) + ("\n" if lines else "")
        data = txt.encode("utf-8", errors="replace")
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")

    def clear_channel(self, channel: str) -> Dict[str, Any]:
        channel = (channel or "").strip()

        if channel == "all":
            with self.db._conn() as c:
                c.execute("DELETE FROM logs")
            return {"ok": True}

        with self.db._conn() as c:
            c.execute("DELETE FROM logs WHERE channel=?", (channel,))

        return {"ok": True}

    def list_channels(self) -> List[str]:
        """
        Returns channels with DEFAULT_LOG_CHANNELS first (stable order),
        then any additional channels sorted alphabetically.
        """
        ch = set(DEFAULT_LOG_CHANNELS)
        with self.db._conn() as c:
            rows = c.execute("SELECT DISTINCT channel FROM logs").fetchall()
            for r in rows:
                if r and r[0]:
                    ch.add(str(r[0]))

        ordered = []
        for d in DEFAULT_LOG_CHANNELS:
            if d in ch:
                ordered.append(d)

        rest = sorted([x for x in ch if x not in set(DEFAULT_LOG_CHANNELS)])
        return ordered + rest

    def list_daily_files(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            names = os.listdir(self.log_dir)
        except Exception:
            return out

        for group, prefix in DAILY_GROUP_PREFIX.items():
            rx = re.compile(rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.txt$")
            for fn in names:
                m = rx.match(fn)
                if not m:
                    continue
                date_s = m.group(1)
                try:
                    d = datetime.strptime(date_s, "%Y-%m-%d").date()
                except Exception:
                    continue
                p = os.path.join(self.log_dir, fn)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                out.append(
                    {
                        "name": fn,
                        "group": group,
                        "group_label": DAILY_GROUP_LABELS.get(group, group),
                        "date": date_s,
                        "size_bytes": int(st.st_size),
                        "mtime_ts": float(st.st_mtime),
                        "_path": p,
                        "_date_obj": d,
                    }
                )

        out.sort(key=lambda x: (x.get("date", ""), x.get("name", "")), reverse=True)
        return out

    def read_daily_file(self, name: str, max_bytes: int = 5_000_000) -> str:
        safe_name = os.path.basename((name or "").strip())
        if safe_name != name:
            raise RuntimeError("invalid file name")
        if not safe_name.endswith(".txt"):
            raise RuntimeError("invalid file type")

        known = {it["name"] for it in self.list_daily_files()}
        if safe_name not in known:
            raise RuntimeError("file not found")

        p = os.path.join(self.log_dir, safe_name)
        with open(p, "rb") as f:
            data = f.read()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")
