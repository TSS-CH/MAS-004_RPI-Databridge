import re
from dataclasses import dataclass
from typing import Optional

READONLY_NAK = "NAK_ReadOnly"

@dataclass(frozen=True)
class ParamMsg:
    raw: str
    ptype: Optional[str]   # "TTP"
    pid: Optional[str]     # "00002"
    value: Optional[str]   # "50" or "?" or None
    is_ack: bool = False

_RX = re.compile(r"^(?P<ack>ACK_)?(?P<ptype>[A-Z]{3})(?P<pid>\d+)\s*=\s*(?P<val>.*)$")

_WIDTH = {
    "TTP": 5,
    "MAP": 4,
    "MAS": 4,
    "TTE": 4, "TTW": 4,
    "LSE": 4, "LSW": 4,
    "MAE": 4, "MAW": 4,
}

def normalize_pid(ptype: str, pid: str) -> str:
    w = _WIDTH.get(ptype, max(4, len(pid)))
    # pid kann "2" oder "00002" sein → zfill
    try:
        n = int(pid)
        return str(n).zfill(w)
    except Exception:
        return pid.zfill(w)

def parse_param_line(s: str) -> Optional[ParamMsg]:
    s = (s or "").strip()
    if not s:
        return None
    m = _RX.match(s)
    if not m:
        return ParamMsg(raw=s, ptype=None, pid=None, value=s, is_ack=False)  # unknown/raw
    is_ack = bool(m.group("ack"))
    ptype = m.group("ptype")
    pid = normalize_pid(ptype, m.group("pid"))
    val = (m.group("val") or "").strip()
    return ParamMsg(raw=s, ptype=ptype, pid=pid, value=val, is_ack=is_ack)

def build_value(ptype: str, pid: str, value: str) -> str:
    return f"{ptype}{normalize_pid(ptype, pid)}={value}"

def build_ack(ptype: str, pid: str, value: str) -> str:
    return f"ACK_{ptype}{normalize_pid(ptype, pid)}={value}"