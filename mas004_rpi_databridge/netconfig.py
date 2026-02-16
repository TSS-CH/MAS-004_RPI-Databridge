import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List


@dataclass
class IfaceCfg:
    ip: str
    prefix: int
    gw: str = ""
    dns: Optional[List[str]] = None


def _run(cmd: list, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def has_nmcli() -> bool:
    return shutil.which("nmcli") is not None


def _iface_name(kind: str) -> str:
    # in deinem Projekt ist es eth0/eth1 – falls später umbenannt, hier zentral ändern
    kind = (kind or "").lower().strip()
    if kind in ("eth0", "0"):
        return "eth0"
    if kind in ("eth1", "1"):
        return "eth1"
    return kind


def _validate_ipv4(ip: str) -> bool:
    try:
        parts = [int(p) for p in ip.split(".")]
        return len(parts) == 4 and all(0 <= p <= 255 for p in parts)
    except Exception:
        return False


def _validate_prefix(prefix: int) -> bool:
    return isinstance(prefix, int) and 0 <= prefix <= 32


def validate_iface_cfg(cfg: IfaceCfg) -> Tuple[bool, str]:
    if not _validate_ipv4(cfg.ip):
        return False, "Invalid IP"
    if (cfg.gw or "").strip() and not _validate_ipv4(cfg.gw.strip()):
        return False, "Invalid Gateway"
    if not _validate_prefix(cfg.prefix):
        return False, "Invalid Prefix (0..32)"
    for dns_ip in (cfg.dns or []):
        if not _validate_ipv4((dns_ip or "").strip()):
            return False, f"Invalid DNS server '{dns_ip}'"
    return True, "OK"


def get_current_ip_info() -> Dict[str, Any]:
    """
    Best-effort read-only status.
    """
    out: Dict[str, Any] = {"ok": True, "ifaces": {}}
    for iface in ("eth0", "eth1"):
        try:
            ip = _run(["bash", "-lc", f"ip -4 addr show {iface} | grep -oP '(?<=inet\\s)\\d+\\.\\d+\\.\\d+\\.\\d+/\\d+' | head -n1"], check=False).stdout.strip()
            gw = _run(["bash", "-lc", f"ip route | grep '^default' | grep {iface} | awk '{{print $3}}' | head -n1"], check=False).stdout.strip()
            out["ifaces"][iface] = {"cidr": ip or "", "gw": gw or ""}
        except Exception as e:
            out["ifaces"][iface] = {"cidr": "", "gw": "", "err": repr(e)}
    out["nmcli"] = has_nmcli()
    return out


# -----------------------------
# Apply using NetworkManager (nmcli)
# -----------------------------
def _nmcli_find_connection_for_iface(iface: str) -> Optional[str]:
    """
    Finds a NM connection name bound to device.
    """
    try:
        r = _run(["bash", "-lc", f"nmcli -t -f NAME,DEVICE con show | grep ':{iface}$' | head -n1"], check=False).stdout.strip()
        if not r:
            return None
        return r.split(":")[0].strip() or None
    except Exception:
        return None


def apply_static_nmcli(iface: str, cfg: IfaceCfg) -> Dict[str, Any]:
    iface = _iface_name(iface)
    ok, msg = validate_iface_cfg(cfg)
    if not ok:
        return {"ok": False, "msg": msg}

    con = _nmcli_find_connection_for_iface(iface)
    if not con:
        return {"ok": False, "msg": f"nmcli: no connection bound to {iface}. Open NetworkManager and bind first, or use dhcpcd fallback."}

    cidr = f"{cfg.ip}/{cfg.prefix}"
    try:
        gw = (cfg.gw or "").strip()
        dns = [d.strip() for d in (cfg.dns or []) if (d or "").strip()]
        route_metric = 100 if iface == "eth0" else 200
        never_default = "yes" if not gw else "no"

        _run(["bash", "-lc", f"nmcli con mod '{con}' ipv4.method manual ipv4.addresses '{cidr}'"], check=True)
        _run(["bash", "-lc", f"nmcli con mod '{con}' ipv4.gateway '{gw}'"], check=True)
        _run(["bash", "-lc", f"nmcli con mod '{con}' ipv4.never-default '{never_default}'"], check=True)
        _run(["bash", "-lc", f"nmcli con mod '{con}' ipv4.route-metric '{route_metric}'"], check=True)
        if dns:
            _run(
                ["bash", "-lc", f"nmcli con mod '{con}' ipv4.dns '{' '.join(dns)}' ipv4.ignore-auto-dns yes"],
                check=True,
            )
        _run(["bash", "-lc", f"nmcli con down '{con}' || true"], check=False)
        _run(["bash", "-lc", f"nmcli con up '{con}'"], check=True)
        return {
            "ok": True,
            "msg": f"Applied via nmcli on {iface} ({con})",
            "cidr": cidr,
            "gw": gw,
            "dns": dns,
            "route_metric": route_metric,
        }
    except Exception as e:
        return {"ok": False, "msg": f"nmcli apply failed: {repr(e)}"}


# -----------------------------
# Fallback: dhcpcd.conf editing
# -----------------------------
DHCPCD_PATH = "/etc/dhcpcd.conf"


def apply_static_dhcpcd(iface: str, cfg: IfaceCfg) -> Dict[str, Any]:
    iface = _iface_name(iface)
    ok, msg = validate_iface_cfg(cfg)
    if not ok:
        return {"ok": False, "msg": msg}

    cidr = f"{cfg.ip}/{cfg.prefix}"
    gw = (cfg.gw or "").strip()
    dns = [d.strip() for d in (cfg.dns or []) if (d or "").strip()]
    metric = 100 if iface == "eth0" else 200

    if not os.path.exists(DHCPCD_PATH):
        return {"ok": False, "msg": f"{DHCPCD_PATH} not found. Install dhcpcd or use nmcli."}

    try:
        with open(DHCPCD_PATH, "r", encoding="utf-8") as f:
            txt = f.read()

        backup = DHCPCD_PATH + ".bak"
        with open(backup, "w", encoding="utf-8") as f:
            f.write(txt)

        # Remove old block for iface
        pattern = re.compile(rf"(?ms)^\s*#\s*MAS004-BEGIN\s+{re.escape(iface)}\s*$.*?^\s*#\s*MAS004-END\s+{re.escape(iface)}\s*$\s*")
        txt = re.sub(pattern, "", txt)

        router_line = f"static routers={gw}\n" if gw else ""
        dns_line = f"static domain_name_servers={' '.join(dns)}\n" if dns else ""
        block = (
            f"# MAS004-BEGIN {iface}\n"
            f"interface {iface}\n"
            f"static ip_address={cidr}\n"
            f"{router_line}"
            f"{dns_line}"
            f"metric {metric}\n"
            f"# MAS004-END {iface}\n"
        )

        txt = txt.rstrip() + "\n\n" + block + "\n"

        with open(DHCPCD_PATH, "w", encoding="utf-8") as f:
            f.write(txt)

        # restart networking
        _run(["bash", "-lc", "systemctl restart dhcpcd || true"], check=False)
        _run(["bash", "-lc", "systemctl restart networking || true"], check=False)

        return {
            "ok": True,
            "msg": f"Applied via dhcpcd.conf on {iface} (backup: {backup})",
            "cidr": cidr,
            "gw": gw,
            "dns": dns,
            "route_metric": metric,
        }
    except Exception as e:
        return {"ok": False, "msg": f"dhcpcd apply failed: {repr(e)}"}


def apply_static(iface: str, cfg: IfaceCfg) -> Dict[str, Any]:
    """
    Prefers nmcli, falls back to dhcpcd.
    """
    if has_nmcli():
        res = apply_static_nmcli(iface, cfg)
        if res.get("ok"):
            return res
        # if nmcli exists but couldn't apply -> try dhcpcd
        res2 = apply_static_dhcpcd(iface, cfg)
        res2["note"] = "nmcli failed, tried dhcpcd fallback"
        res2["nmcli_error"] = res.get("msg")
        return res2

    return apply_static_dhcpcd(iface, cfg)
