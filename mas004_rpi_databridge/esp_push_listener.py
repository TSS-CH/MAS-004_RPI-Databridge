from __future__ import annotations

import socket
import threading
from typing import Callable

from mas004_rpi_databridge.config import Settings
from mas004_rpi_databridge.db import DB
from mas004_rpi_databridge.device_bridge import DeviceBridge
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.params import ParamStore
from mas004_rpi_databridge.peers import peer_urls
from mas004_rpi_databridge.protocol import parse_operation_line, parse_param_line


def _is_ipv4(s: str) -> bool:
    try:
        parts = [int(p) for p in (s or "").split(".")]
        return len(parts) == 4 and all(0 <= p <= 255 for p in parts)
    except Exception:
        return False


def _configure_socket(sock: socket.socket, *, set_timeout: bool = True):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    if set_timeout:
        sock.settimeout(1.0)


def _channel_for_ptype(ptype: str) -> str:
    ptype = (ptype or "").upper()
    if ptype.startswith("TT"):
        return "vj6530"
    if ptype.startswith("LS"):
        return "vj3350"
    if ptype.startswith("MA"):
        return "esp-plc"
    return "raspi"


class EspPushListener:
    def __init__(self, cfg: Settings, log: Callable[[str], None]):
        self.cfg = cfg
        self.log = log
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None

    def start(self) -> bool:
        bind_ip = (self.cfg.eth1_ip or "").strip()
        bind_port = int(self.cfg.esp_port or 0)
        if bool(self.cfg.esp_simulation) or not _is_ipv4(bind_ip) or bind_port <= 0:
            self.log(
                f"[ESP-PUSH] disabled sim={self.cfg.esp_simulation} bind_ip={bind_ip!r} bind_port={bind_port}"
            )
            return False

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _configure_socket(s, set_timeout=False)
            s.bind((bind_ip, bind_port))
            s.listen(32)
            self._sock = s
        except Exception as exc:
            self.log(f"[ESP-PUSH] FAIL bind {bind_ip}:{bind_port} err={repr(exc)}")
            return False

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self.log(f"[ESP-PUSH] listen {bind_ip}:{bind_port}")
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
            _configure_socket(client)
            threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()

    def _handle_client(self, client: socket.socket, addr):
        peer = f"{addr[0]}:{addr[1]}"
        buffer = b""
        self.log(f"[ESP-PUSH] open {peer}")
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    resp = self._process_line(line)
                    client.sendall((resp + "\n").encode("utf-8"))
        except Exception as exc:
            self.log(f"[ESP-PUSH] client error {peer} err={repr(exc)}")
        finally:
            try:
                client.close()
            except Exception:
                pass
            self.log(f"[ESP-PUSH] close {peer}")

    def _process_line(self, line: str) -> str:
        db = DB(self.cfg.db_path)
        params = ParamStore(db)
        outbox = Outbox(db)
        logs = LogStore(db)
        bridge = DeviceBridge(self.cfg, params, logs)

        logs.log("esp-plc", "in", f"esp->raspi: {line}")
        logs.log("raspi", "in", f"esp-plc push: {line}")

        parsed = parse_operation_line(line)
        if not parsed:
            logs.log("esp-plc", "out", "raspi->esp: NAK_Syntax")
            return "NAK_Syntax"

        ptype, pid, op, value = parsed
        pkey = f"{ptype}{pid}"
        if not params.get_meta(pkey):
            resp = f"{pkey}=NAK_UnknownParam"
            logs.log("esp-plc", "out", f"raspi->esp: {resp}")
            return resp

        dev = _channel_for_ptype(ptype)
        if dev == "raspi":
            resp = f"{pkey}=NAK_UnsupportedParamType"
            logs.log("esp-plc", "out", f"raspi->esp: {resp}")
            return resp

        if op == "read":
            resp = bridge.execute(device=dev, pkey=pkey, ptype=ptype, op="read", value="?", actor="esp32")
            logs.log("esp-plc", "out", f"raspi->esp: {resp}")
            return resp

        if ptype.startswith("MA"):
            ok, msg = params.apply_device_value(pkey, value)
            if not ok:
                resp = f"{pkey}={msg}"
                logs.log("esp-plc", "out", f"raspi->esp: {resp}")
                return resp
            forwarded_line = line
        else:
            resp = bridge.execute(device=dev, pkey=pkey, ptype=ptype, op="write", value=value, actor="esp32")
            logs.log(dev, "out", f"{dev}->raspi: {resp}")
            if "NAK" in resp.upper():
                logs.log("esp-plc", "out", f"raspi->esp: {resp}")
                return resp
            parsed_resp = parse_param_line(resp)
            if not parsed_resp or parsed_resp.ptype is None or parsed_resp.value is None:
                logs.log("esp-plc", "out", f"raspi->esp: {resp}")
                return resp
            forwarded_line = f"{parsed_resp.ptype}{parsed_resp.pid}={parsed_resp.value}"

        targets = peer_urls(self.cfg, "/api/inbox")
        if not targets:
            logs.log("raspi", "error", f"no peer_base_url configured; cannot forward ESP push {line}")
        else:
            for url in targets:
                outbox.enqueue("POST", url, {}, {"msg": forwarded_line, "source": "raspi", "origin": "esp-plc"}, None)
            logs.log("raspi", "out", f"forward to microtom: {forwarded_line}")

        resp = f"ACK_{pkey}={value}"
        logs.log("esp-plc", "out", f"raspi->esp: {resp}")
        return resp


class EspPushListenerManager:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.listener: EspPushListener | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _sig(cfg: Settings):
        return ((cfg.eth1_ip or "").strip(), int(cfg.esp_port or 0), bool(cfg.esp_simulation))

    def start(self):
        with self._lock:
            self._apply(self.cfg)

    def reconcile(self, cfg: Settings):
        with self._lock:
            if self._sig(cfg) == self._sig(self.cfg):
                return
            self.cfg = cfg
            print("[ESP-PUSH] reconcile listener", flush=True)
            self._apply(cfg)

    def stop(self):
        with self._lock:
            if self.listener:
                self.listener.stop()
                self.listener = None

    def _apply(self, cfg: Settings):
        if self.listener:
            self.listener.stop()
            self.listener = None
        listener = EspPushListener(cfg, print)
        if listener.start():
            self.listener = listener
