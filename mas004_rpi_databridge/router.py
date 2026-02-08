import json
import re
from typing import Optional, Tuple

from mas004_rpi_databridge.config import Settings
from mas004_rpi_databridge.device_bridge import DeviceBridge
from mas004_rpi_databridge.inbox import Inbox
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.params import ParamStore
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.protocol import normalize_pid


def _channel_for_ptype(ptype: str) -> str:
    ptype = (ptype or "").upper()
    if ptype.startswith("TT"):  # TTP/TTE/TTW
        return "vj6530"
    if ptype.startswith("LS"):  # LSE/LSW
        return "vj3350"
    if ptype.startswith("MA"):  # MAP/MAS/MAE/MAW
        return "esp-plc"
    return "raspi"


def _extract_msg_line(body_json: Optional[str]) -> Optional[str]:
    if body_json is None:
        return None
    try:
        obj = json.loads(body_json)
    except Exception:
        s = str(body_json).strip()
        return s if s else None

    if isinstance(obj, str):
        return obj.strip() if obj.strip() else None
    if isinstance(obj, dict):
        for k in ("msg", "line", "text", "cmd"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    return None


def _parse_line(line: str) -> Optional[Tuple[str, str, str, str]]:
    """
    Returns (ptype, pid, op, value)
    op: 'read' or 'write'
    """
    s = (line or "").strip()
    if not s:
        return None

    m = re.match(r"^\s*([A-Za-z]{3})([0-9A-Za-z_]+)\s*=\s*(\?|-?[0-9A-Za-z_.]+)\s*$", s)
    if not m:
        return None

    ptype = m.group(1).upper()
    pid = m.group(2)
    if pid.isdigit():
        pid = normalize_pid(ptype, pid)
    rhs = m.group(3)
    if rhs == "?":
        return (ptype, pid, "read", "?")
    return (ptype, pid, "write", rhs)


class Router:
    def __init__(self, cfg: Settings, inbox: Inbox, outbox: Outbox, params: ParamStore, logs: LogStore):
        self.cfg = cfg
        self.inbox = inbox
        self.outbox = outbox
        self.params = params
        self.logs = logs
        self.device_bridge = DeviceBridge(cfg, params, logs)

    def _enqueue_to_mikrotom(self, line: str, correlation: Optional[str] = None):
        url = self.cfg.peer_base_url.rstrip("/") + "/api/inbox"
        headers = {}
        if correlation:
            headers["X-Correlation-Id"] = correlation
        self.outbox.enqueue("POST", url, headers, {"msg": line, "source": "raspi"}, None)

    def handle_mikrotom_line(self, line: str, correlation: Optional[str]) -> Optional[str]:
        parsed = _parse_line(line)
        if not parsed:
            return None

        ptype, pid, op, value = parsed
        pkey = f"{ptype}{pid}"
        dev = _channel_for_ptype(ptype)

        self.logs.log("raspi", "in", f"mikrotom: {line}")
        self.logs.log(dev, "in", f"raspi-> {dev}: {line}")

        resp = self.device_bridge.execute(device=dev, pkey=pkey, ptype=ptype, op=op, value=value)
        self.logs.log(dev, "out", f"{dev}->raspi: {resp}")
        self.logs.log("raspi", "out", f"to mikrotom: {resp}")
        self._enqueue_to_mikrotom(resp, correlation=correlation)
        return resp

    def tick_once(self) -> bool:
        msg = self.inbox.claim_next_pending()
        if not msg:
            return False

        line = _extract_msg_line(msg.body_json)
        if not line:
            self.logs.log("raspi", "info", f"mikrotom msg id={msg.id} ohne 'msg/line/text/cmd' -> ignoriert")
            self.inbox.ack(msg.id)
            return True

        try:
            self.handle_mikrotom_line(line, correlation=msg.idempotency_key)
        except Exception as e:
            self.logs.log("raspi", "error", f"router error for inbox id={msg.id}: {repr(e)}")
        finally:
            self.inbox.ack(msg.id)

        return True
