from __future__ import annotations

import math
import re
import struct
from typing import Optional

from mas004_rpi_databridge.config import Settings
from mas004_rpi_databridge.device_clients import DeviceWatchdog, EspPlcClient, UltimateClient, ZipherClient
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.params import ParamStore

READONLY_TYPES = {"TTE", "TTW", "LSE", "LSW", "MAE", "MAW"}  # push-only from device perspective
ZBC_ERR_MESSAGE_ID = 0x500D


class DeviceBridge:
    def __init__(self, cfg: Settings, params: ParamStore, logs: LogStore):
        self.cfg = cfg
        self.params = params
        self.logs = logs

        self._esp = EspPlcClient(cfg.esp_host, cfg.esp_port, timeout_s=cfg.http_timeout_s)
        self._esp_watchdog = DeviceWatchdog(
            host=(cfg.esp_watchdog_host or cfg.esp_host),
            timeout_s=cfg.watchdog_timeout_s,
            down_after=cfg.watchdog_down_after,
        )
        self._zbc = ZipherClient(cfg.vj6530_host, cfg.vj6530_port, timeout_s=cfg.http_timeout_s)
        self._ultimate = UltimateClient(cfg.vj3350_host, cfg.vj3350_port, timeout_s=cfg.http_timeout_s)

    def execute(self, device: str, pkey: str, ptype: str, op: str, value: str) -> str:
        ptype = (ptype or "").upper()
        op = (op or "").lower()

        if ptype in READONLY_TYPES:
            return f"{pkey}=NAK_ReadOnly"

        if device == "raspi" or self._is_simulation(device):
            return self._simulate(pkey, op, value)

        if op == "write":
            ok, msg = self.params.validate_write(pkey, value)
            if not ok:
                return f"{pkey}={msg}"

        try:
            if device == "esp-plc":
                return self._esp_live(pkey, op, value)
            if device == "vj6530":
                return self._zbc_live(pkey, op, value)
            if device == "vj3350":
                return self._ultimate_live(pkey, op, value)
            return f"{pkey}=NAK_UnknownDevice"
        except Exception as exc:
            self.logs.log(device, "error", f"live communication failed for {pkey}: {repr(exc)}")
            return f"{pkey}=NAK_DeviceComm"

    def _is_simulation(self, device: str) -> bool:
        if device == "esp-plc":
            return bool(self.cfg.esp_simulation)
        if device == "vj6530":
            return bool(self.cfg.vj6530_simulation)
        if device == "vj3350":
            return bool(self.cfg.vj3350_simulation)
        return True

    def _simulate(self, pkey: str, op: str, value: str) -> str:
        if op == "read":
            meta = self.params.get_meta(pkey)
            if not meta:
                return f"{pkey}=NAK_UnknownParam"
            return f"{pkey}={self.params.get_effective_value(pkey)}"

        ok, err = self.params.set_value(pkey, value)
        if ok:
            return f"ACK_{pkey}={value}"
        return f"{pkey}={err}"

    def _esp_live(self, pkey: str, op: str, value: str) -> str:
        if not self._esp_watchdog.check():
            return f"{pkey}=NAK_DeviceDown"

        mapping = self.params.get_device_map(pkey)
        esp_key = (mapping.get("esp_key") or pkey).strip()
        line = f"{esp_key}={'?' if op == 'read' else value}"

        response = ""
        last_exc: Optional[Exception] = None
        for _ in range(2):
            try:
                response = self._esp.exchange_line(line, read_timeout_s=self.cfg.http_timeout_s)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc

        if op == "read":
            rhs = _extract_rhs(response)
            if rhs is None:
                return f"{pkey}=NAK_DeviceBadResponse"
            ok, msg = self.params.apply_device_value(pkey, rhs)
            if not ok:
                return f"{pkey}={msg}"
            return f"{pkey}={rhs}"

        # write
        if response and "NAK" in response.upper():
            return f"{pkey}=NAK_DeviceRejected"
        ok, msg = self.params.set_value(pkey, value)
        if not ok:
            return f"{pkey}={msg}"
        return f"ACK_{pkey}={value}"

    def _zbc_live(self, pkey: str, op: str, value: str) -> str:
        mapping = self.params.get_device_map(pkey)
        command_id = mapping.get("zbc_command_id")
        if command_id is None:
            return f"{pkey}=NAK_MappingMissing"

        message_id = int(mapping.get("zbc_message_id") or 0x500A)
        codec = str(mapping.get("zbc_value_codec") or "u16le").strip().lower()
        scale = float(mapping.get("zbc_scale") or 1.0)
        offset = float(mapping.get("zbc_offset") or 0.0)
        if abs(scale) < 1e-12:
            scale = 1.0

        command_id = int(command_id) & 0xFFFF
        body = struct.pack("<H", command_id)
        if op == "write":
            body += _encode_codec(value, codec, scale, offset)

        resp_id, resp_body = self._zbc.transact(message_id=message_id, body=body)
        if resp_id == ZBC_ERR_MESSAGE_ID:
            err_code = struct.unpack("<H", resp_body[:2])[0] if len(resp_body) >= 2 else 0xFFFF
            return f"{pkey}=NAK_ZBC_{err_code:04X}"

        if op == "write":
            ok, msg = self.params.set_value(pkey, value)
            if not ok:
                return f"{pkey}={msg}"
            return f"ACK_{pkey}={value}"

        raw = resp_body
        if len(raw) >= 2 and struct.unpack("<H", raw[:2])[0] == command_id:
            raw = raw[2:]
        if not raw:
            return f"{pkey}=NAK_DeviceBadResponse"

        decoded = _decode_codec(raw, codec, scale, offset)
        ok, msg = self.params.apply_device_value(pkey, decoded)
        if not ok:
            return f"{pkey}={msg}"
        return f"{pkey}={decoded}"

    def _ultimate_live(self, pkey: str, op: str, value: str) -> str:
        mapping = self.params.get_device_map(pkey)
        var_name = (mapping.get("ultimate_var_name") or pkey).strip()
        set_cmd = (mapping.get("ultimate_set_cmd") or "SetVars").strip()
        get_cmd = (mapping.get("ultimate_get_cmd") or "GetVars").strip()

        if op == "write":
            ack, code, _args = self._ultimate.command(set_cmd, [var_name, str(value)])
            if not ack:
                return f"{pkey}=NAK_Ultimate_{code or 'FAIL'}"
            ok, msg = self.params.set_value(pkey, value)
            if not ok:
                return f"{pkey}={msg}"
            return f"ACK_{pkey}={value}"

        ack, code, args = self._ultimate.command(get_cmd, [var_name])
        if not ack:
            return f"{pkey}=NAK_Ultimate_{code or 'FAIL'}"

        parsed = _extract_ultimate_value(var_name, args)
        if parsed is None:
            return f"{pkey}=NAK_DeviceBadResponse"

        ok, msg = self.params.apply_device_value(pkey, parsed)
        if not ok:
            return f"{pkey}={msg}"
        return f"{pkey}={parsed}"


def _extract_rhs(line: str) -> Optional[str]:
    s = (line or "").strip()
    if not s:
        return None
    m = re.match(r"^\s*[A-Za-z0-9_]+\s*=\s*(.+?)\s*$", s)
    if m:
        return m.group(1).strip()
    return s


def _extract_ultimate_value(var_name: str, args: list[str]) -> Optional[str]:
    vname = (var_name or "").strip()
    if not args:
        return None

    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            if k.strip() == vname:
                return v.strip()

    if len(args) >= 2:
        for idx in range(len(args) - 1):
            if args[idx].strip() == vname:
                return args[idx + 1].strip()

    if len(args) == 1:
        return args[0].strip()

    return None


def _encode_codec(value: str, codec: str, scale: float, offset: float) -> bytes:
    codec = (codec or "u16le").strip().lower()
    if codec == "ascii":
        return (str(value) + "\x00").encode("utf-8")

    f = float(value)
    scaled = (f - offset) / scale
    if codec in ("u8", "uint8"):
        return struct.pack("<B", int(round(scaled)))
    if codec in ("u16", "u16le", "uint16"):
        return struct.pack("<H", int(round(scaled)))
    if codec in ("u32", "u32le", "uint32"):
        return struct.pack("<I", int(round(scaled)))
    if codec in ("i16", "i16le", "int16"):
        return struct.pack("<h", int(round(scaled)))
    if codec in ("i32", "i32le", "int32"):
        return struct.pack("<i", int(round(scaled)))
    if codec in ("f32", "f32le", "float32", "float"):
        return struct.pack("<f", float(scaled))
    raise ValueError(f"unsupported codec: {codec}")


def _decode_codec(data: bytes, codec: str, scale: float, offset: float) -> str:
    codec = (codec or "u16le").strip().lower()
    if codec == "ascii":
        txt = data.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        return txt

    if codec in ("u8", "uint8"):
        raw = struct.unpack_from("<B", data, 0)[0]
    elif codec in ("u16", "u16le", "uint16"):
        raw = struct.unpack_from("<H", data, 0)[0]
    elif codec in ("u32", "u32le", "uint32"):
        raw = struct.unpack_from("<I", data, 0)[0]
    elif codec in ("i16", "i16le", "int16"):
        raw = struct.unpack_from("<h", data, 0)[0]
    elif codec in ("i32", "i32le", "int32"):
        raw = struct.unpack_from("<i", data, 0)[0]
    elif codec in ("f32", "f32le", "float32", "float"):
        raw = struct.unpack_from("<f", data, 0)[0]
    else:
        raise ValueError(f"unsupported codec: {codec}")

    value = (float(raw) * scale) + offset
    if math.isfinite(value) and abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")
