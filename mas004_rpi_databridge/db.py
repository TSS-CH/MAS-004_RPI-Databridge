import sqlite3
import time
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts REAL NOT NULL,
  method TEXT NOT NULL,
  url TEXT NOT NULL,
  headers_json TEXT NOT NULL,
  body_json TEXT,
  idempotency_key TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_next ON outbox(next_attempt_ts, created_ts);

CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_ts REAL NOT NULL,
  source TEXT,
  headers_json TEXT NOT NULL,
  body_json TEXT,
  idempotency_key TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_dedupe ON inbox(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_inbox_state ON inbox(state, received_ts);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  channel TEXT NOT NULL,
  direction TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_ch_ts ON logs(channel, ts);

-- ===== Parameter-Tabellen =====
CREATE TABLE IF NOT EXISTS params (
  pkey TEXT PRIMARY KEY,          -- z.B. TTP00002
  ptype TEXT NOT NULL,            -- z.B. TTP
  pid TEXT NOT NULL,              -- z.B. 00002
  min_v REAL,
  max_v REAL,
  default_v TEXT,
  unit TEXT,
  rw TEXT,                        -- R / W / R/W
  dtype TEXT,
  name TEXT,
  format_relevant TEXT,
  message TEXT,
  possible_cause TEXT,
  effects TEXT,
  remedy TEXT,
  updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_params_type_id ON params(ptype, pid);

CREATE TABLE IF NOT EXISTS param_values (
  pkey TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_ts REAL NOT NULL,
  FOREIGN KEY(pkey) REFERENCES params(pkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS param_device_map (
  pkey TEXT PRIMARY KEY,
  esp_key TEXT,
  zbc_message_id INTEGER,
  zbc_command_id INTEGER,
  zbc_value_codec TEXT,
  zbc_scale REAL,
  zbc_offset REAL,
  ultimate_set_cmd TEXT,
  ultimate_get_cmd TEXT,
  ultimate_var_name TEXT,
  updated_ts REAL NOT NULL,
  FOREIGN KEY(pkey) REFERENCES params(pkey) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_param_device_map_zbc ON param_device_map(zbc_message_id, zbc_command_id);
"""

_init_lock = threading.Lock()
_initialized_paths = set()


class DB:
    def __init__(self, path: str):
        self.path = path
        self._init_once()

    def _conn(self):
        # isolation_level=None => autocommit
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.execute("PRAGMA busy_timeout=5000;")
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        return c

    def _init_once(self):
        global _initialized_paths
        with _init_lock:
            if self.path in _initialized_paths:
                return

            # Retry a few times if another thread/process is initializing
            for i in range(10):
                try:
                    with self._conn() as c:
                        c.executescript(SCHEMA)
                    _initialized_paths.add(self.path)
                    return
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower():
                        time.sleep(0.2 * (i + 1))
                        continue
                    raise


def now_ts() -> float:
    return time.time()
