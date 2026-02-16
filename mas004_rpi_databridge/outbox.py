import json
import uuid
from dataclasses import dataclass
from typing import Optional
from mas004_rpi_databridge.db import DB, now_ts

@dataclass
class OutboxJob:
    id: int
    created_ts: float
    method: str
    url: str
    headers_json: str
    body_json: Optional[str]
    idempotency_key: str
    retry_count: int
    next_attempt_ts: float

class Outbox:
    def __init__(self, db: DB):
        self.db = db

    def enqueue(self, method: str, url: str, headers: dict, body: Optional[dict], idempotency_key: Optional[str]=None):
        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        headers = dict(headers or {})
        headers.setdefault("X-Idempotency-Key", idempotency_key)
        headers.setdefault("Content-Type", "application/json")

        with self.db._conn() as c:
            c.execute(
                "INSERT INTO outbox(created_ts,method,url,headers_json,body_json,idempotency_key) VALUES(?,?,?,?,?,?)",
                (now_ts(), method.upper(), url, json.dumps(headers), json.dumps(body) if body is not None else None, idempotency_key)
            )
        return idempotency_key

    def next_due(self) -> Optional[OutboxJob]:
        with self.db._conn() as c:
            row = c.execute(
                """SELECT id,created_ts,method,url,headers_json,body_json,idempotency_key,retry_count,next_attempt_ts
                   FROM outbox
                   WHERE next_attempt_ts <= ?
                   ORDER BY next_attempt_ts ASC, retry_count ASC, created_ts ASC
                   LIMIT 1""",
                (now_ts(),)
            ).fetchone()
        return OutboxJob(*row) if row else None

    def delete(self, job_id: int):
        with self.db._conn() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (job_id,))

    def reschedule(self, job_id: int, retry_count: int, next_attempt_ts: float):
        with self.db._conn() as c:
            c.execute("UPDATE outbox SET retry_count=?, next_attempt_ts=? WHERE id=?",
                      (retry_count, next_attempt_ts, job_id))

    def count(self) -> int:
        with self.db._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
