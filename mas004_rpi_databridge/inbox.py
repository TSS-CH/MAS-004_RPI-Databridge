import json
from dataclasses import dataclass
from typing import Optional
from mas004_rpi_databridge.db import DB, now_ts

@dataclass
class InboxMsg:
    id: int
    received_ts: float
    source: Optional[str]
    headers_json: str
    body_json: Optional[str]
    idempotency_key: str
    state: str

class Inbox:
    def __init__(self, db: DB):
        self.db = db

    def store(self, source: Optional[str], headers: dict, body: Optional[dict], idempotency_key: str) -> bool:
        headers = dict(headers or {})
        with self.db._conn() as c:
            try:
                c.execute(
                    "INSERT INTO inbox(received_ts,source,headers_json,body_json,idempotency_key,state) VALUES(?,?,?,?,?, 'pending')",
                    (now_ts(), source, json.dumps(headers), json.dumps(body) if body is not None else None, idempotency_key)
                )
                return True
            except Exception:
                # likely UNIQUE constraint -> duplicate idempotency key
                return False

    def next_pending(self) -> Optional[InboxMsg]:
        with self.db._conn() as c:
            row = c.execute(
                """SELECT id,received_ts,source,headers_json,body_json,idempotency_key,state
                   FROM inbox
                   WHERE state='pending'
                   ORDER BY received_ts ASC
                   LIMIT 1"""
            ).fetchone()
        return InboxMsg(*row) if row else None

    def claim_next_pending(self) -> Optional[InboxMsg]:
        """
        Atomar: nimmt die älteste pending Nachricht und setzt sie auf 'processing',
        damit parallel laufende Worker sie nicht doppelt ziehen.
        """
        with self.db._conn() as c:
            c.execute("BEGIN IMMEDIATE;")
            row = c.execute(
                """SELECT id,received_ts,source,headers_json,body_json,idempotency_key,state
                   FROM inbox
                   WHERE state='pending'
                   ORDER BY received_ts ASC
                   LIMIT 1"""
            ).fetchone()
            if not row:
                c.execute("COMMIT;")
                return None

            msg_id = row[0]
            c.execute("UPDATE inbox SET state='processing' WHERE id=? AND state='pending'", (msg_id,))
            c.execute("COMMIT;")

        return InboxMsg(*row)

    def ack(self, msg_id: int):
        with self.db._conn() as c:
            c.execute("UPDATE inbox SET state='done' WHERE id=?", (msg_id,))

    def nack(self, msg_id: int):
        # falls du mal retry willst
        with self.db._conn() as c:
            c.execute("UPDATE inbox SET state='pending' WHERE id=?", (msg_id,))

    def count_pending(self) -> int:
        with self.db._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM inbox WHERE state='pending'").fetchone()[0])
