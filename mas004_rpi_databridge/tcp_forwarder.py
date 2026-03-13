from __future__ import annotations

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
    primary: bool = True


def _normalize_port(raw: int | str | None, fallback: int) -> int:
    try:
        port = int(raw or 0)
    except Exception:
        port = 0
    return port if 1 <= port <= 65535 else fallback


def _configure_socket(sock: socket.socket, *, set_timeout: bool = True):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    if set_timeout:
        sock.settimeout(1.0)


def build_rules(cfg: Settings, log: Callable[[str], None]) -> List[ForwardRule]:
    listen_ip = (cfg.eth0_ip or "").strip()
    if not _is_ipv4(listen_ip):
        log(f"[FWD] eth0_ip invalid/empty ('{listen_ip}'), fallback to 0.0.0.0")
        listen_ip = "0.0.0.0"

    device_defs = [
        (
            "VJ6530",
            (cfg.vj6530_host or "").strip(),
            _normalize_port(getattr(cfg, "vj6530_port", 0), 3007),
            getattr(cfg, "vj6530_forward_ports", ""),
        ),
        (
            "VJ3350",
            (cfg.vj3350_host or "").strip(),
            _normalize_port(getattr(cfg, "vj3350_port", 0), 3008),
            getattr(cfg, "vj3350_forward_ports", ""),
        ),
        (
            "ESP32",
            (cfg.esp_host or "").strip(),
            _normalize_port(getattr(cfg, "esp_port", 0), 3010),
            getattr(cfg, "esp_forward_ports", ""),
        ),
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
                    primary=(p == main_port),
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
        self._conn_lock = threading.Lock()
        self._tracked_sockets: set[socket.socket] = set()
        self._active_connections = 0

    def start(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _configure_socket(s, set_timeout=False)
            s.bind((self.rule.listen_ip, self.rule.listen_port))
            s.listen(256)
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
        with self._conn_lock:
            for sock in list(self._tracked_sockets):
                try:
                    sock.close()
                except Exception:
                    pass
            self._tracked_sockets.clear()

    def _accept_loop(self):
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            except Exception:
                continue
            _configure_socket(client)
            t = threading.Thread(target=self._handle_client, args=(client, addr), daemon=True)
            t.start()

    def _handle_client(self, client: socket.socket, addr):
        upstream = None
        conn_id = f"{addr[0]}:{addr[1]}"
        try:
            upstream = socket.create_connection((self.rule.target_ip, self.rule.target_port), timeout=1.5)
            _configure_socket(upstream)
        except Exception as e:
            self.log(
                f"[FWD] connect fail {self.rule.listen_port} -> {self.rule.target_ip}:{self.rule.target_port} "
                f"from {conn_id} err={repr(e)}"
            )
            try:
                client.close()
            except Exception:
                pass
            return

        stop_evt = threading.Event()
        try:
            active = self._track_socket_pair(client, upstream, delta=1)
            self.log(
                f"[FWD] open {self.rule.label} {conn_id} "
                f"{self.rule.listen_port}->{self.rule.target_ip}:{self.rule.target_port} active={active}"
            )

            pumps = [
                threading.Thread(target=self._pump, args=(client, upstream, stop_evt), daemon=True),
                threading.Thread(target=self._pump, args=(upstream, client, stop_evt), daemon=True),
            ]
            for pump in pumps:
                pump.start()

            while not self._stop.is_set() and not stop_evt.is_set():
                for pump in pumps:
                    pump.join(0.2)
                if not any(pump.is_alive() for pump in pumps):
                    break
            stop_evt.set()
        except Exception as e:
            self.log(f"[FWD] bridge fail {self.rule.label} {conn_id} err={repr(e)}")
        finally:
            self._close_socket(client)
            self._close_socket(upstream)
            active = self._track_socket_pair(client, upstream, delta=-1)
            self.log(f"[FWD] close {self.rule.label} {conn_id} active={active}")

    def _pump(self, src: socket.socket, dst: socket.socket, stop_evt: threading.Event):
        while not self._stop.is_set() and not stop_evt.is_set():
            try:
                data = src.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                stop_evt.set()
                break

            if not data:
                stop_evt.set()
                break

            view = memoryview(data)
            while view and not self._stop.is_set() and not stop_evt.is_set():
                try:
                    sent = dst.send(view)
                except socket.timeout:
                    continue
                except Exception:
                    stop_evt.set()
                    break
                if sent <= 0:
                    stop_evt.set()
                    break
                view = view[sent:]

        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass

    def _track_socket_pair(self, client: socket.socket | None, upstream: socket.socket | None, delta: int) -> int:
        with self._conn_lock:
            for sock in (client, upstream):
                if not sock:
                    continue
                if delta > 0:
                    self._tracked_sockets.add(sock)
                else:
                    self._tracked_sockets.discard(sock)
            self._active_connections = max(0, self._active_connections + delta)
            return self._active_connections

    @staticmethod
    def _close_socket(sock: socket.socket | None):
        if not sock:
            return
        try:
            sock.close()
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
