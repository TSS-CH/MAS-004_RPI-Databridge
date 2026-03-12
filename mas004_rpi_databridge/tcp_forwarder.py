from __future__ import annotations

import select
import socket
import threading
from dataclasses import dataclass
from typing import Callable, List

from mas004_rpi_databridge.config import Settings


def _is_ipv4(s: str) -> bool:
    try:
        parts = [int(p) for p in (s or "").split(".")]
        return len(parts) == 4 and all(0 <= p <= 255 for p in parts)
    except Exception:
        return False


def parse_port_list(raw: str) -> List[int]:
    txt = (raw or "").strip()
    if not txt:
        return []
    out: List[int] = []
    cur = ""
    for ch in txt:
        if ch.isdigit():
            cur += ch
            continue
        if cur:
            p = int(cur)
            if 1 <= p <= 65535 and p not in out:
                out.append(p)
            cur = ""
    if cur:
        p = int(cur)
        if 1 <= p <= 65535 and p not in out:
            out.append(p)
    return out


@dataclass
class ForwardRule:
    label: str
    listen_ip: str
    listen_port: int
    target_ip: str
    target_port: int


def build_rules(cfg: Settings, log: Callable[[str], None]) -> List[ForwardRule]:
    listen_ip = (cfg.eth0_ip or "").strip()
    if not _is_ipv4(listen_ip):
        log(f"[FWD] eth0_ip invalid/empty ('{listen_ip}'), fallback to 0.0.0.0")
        listen_ip = "0.0.0.0"

    device_defs = [
        ("VJ6530", (cfg.vj6530_host or "").strip(), 3007, getattr(cfg, "vj6530_forward_ports", "")),
        ("VJ3350", (cfg.vj3350_host or "").strip(), 3008, getattr(cfg, "vj3350_forward_ports", "")),
        ("ESP32", (cfg.esp_host or "").strip(), 3009, getattr(cfg, "esp_forward_ports", "")),
    ]

    rules: List[ForwardRule] = []
    used_ports: set[int] = set()
    for label, target_ip, main_port, extra_raw in device_defs:
        if not _is_ipv4(target_ip):
            log(f"[FWD] skip {label}: target host missing/invalid ('{target_ip}')")
            continue

        ports = [main_port] + parse_port_list(extra_raw)
        uniq_ports: List[int] = []
        for p in ports:
            if p not in uniq_ports:
                uniq_ports.append(p)

        for p in uniq_ports:
            if p in used_ports:
                log(f"[FWD] skip duplicate listen port {p} for {label}")
                continue
            used_ports.add(p)
            rules.append(
                ForwardRule(
                    label=label,
                    listen_ip=listen_ip,
                    listen_port=p,
                    target_ip=target_ip,
                    target_port=p,
                )
            )
    return rules


class TcpPortForwarder:
    def __init__(self, rule: ForwardRule, log: Callable[[str], None]):
        self.rule = rule
        self.log = log
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.rule.listen_ip, self.rule.listen_port))
            s.listen(128)
            self._sock = s
        except Exception as e:
            self.log(
                f"[FWD] FAIL bind {self.rule.listen_ip}:{self.rule.listen_port} "
                f"->{self.rule.target_ip}:{self.rule.target_port} err={repr(e)}"
            )
            return False

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self.log(
            f"[FWD] listen {self.rule.listen_ip}:{self.rule.listen_port} "
            f"-> {self.rule.target_ip}:{self.rule.target_port} ({self.rule.label})"
        )
        return True

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _accept_loop(self):
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            except Exception:
                continue
            t = threading.Thread(target=self._handle_client, args=(client, addr), daemon=True)
            t.start()

    def _handle_client(self, client: socket.socket, addr):
        upstream = None
        try:
            upstream = socket.create_connection((self.rule.target_ip, self.rule.target_port), timeout=3.0)
        except Exception as e:
            self.log(
                f"[FWD] connect fail {self.rule.listen_port} -> {self.rule.target_ip}:{self.rule.target_port} "
                f"from {addr[0]}:{addr[1]} err={repr(e)}"
            )
            try:
                client.close()
            except Exception:
                pass
            return

        try:
            client.setblocking(False)
            upstream.setblocking(False)
            sockets = [client, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 1.0)
                if not readable:
                    continue
                for src in readable:
                    dst = upstream if src is client else client
                    try:
                        data = src.recv(65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        return
                    dst.sendall(data)
        except Exception:
            return
        finally:
            for s in (client, upstream):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass


class TcpForwarderManager:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.forwarders: List[TcpPortForwarder] = []
        self._lock = threading.Lock()

    @staticmethod
    def _rule_sig(rules: List[ForwardRule]) -> List[tuple[str, str, int, str, int]]:
        return [(r.label, r.listen_ip, r.listen_port, r.target_ip, r.target_port) for r in rules]

    def _apply_rules(self, rules: List[ForwardRule]):
        self._stop_unlocked()
        if not rules:
            print("[FWD] no forwarding rules active", flush=True)
            return

        for rule in rules:
            fwd = TcpPortForwarder(rule, print)
            if fwd.start():
                self.forwarders.append(fwd)
        print(f"[FWD] active listeners={len(self.forwarders)}", flush=True)

    def start(self):
        with self._lock:
            self._apply_rules(build_rules(self.cfg, print))

    def reconcile(self, cfg: Settings):
        with self._lock:
            self.cfg = cfg
            desired_rules = build_rules(cfg, print)
            active_rules = [fwd.rule for fwd in self.forwarders]
            if self._rule_sig(desired_rules) == self._rule_sig(active_rules):
                return
            print("[FWD] reconcile forwarding listeners", flush=True)
            self._apply_rules(desired_rules)

    def stop(self):
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self):
        for fwd in self.forwarders:
            fwd.stop()
        self.forwarders.clear()
