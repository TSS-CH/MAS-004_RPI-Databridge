from __future__ import annotations

import time

from mas004_rpi_databridge._vj6530_bridge import (
    AsyncSubscriptionId,
    MessageId,
    ZbcClient,
    resolve_summary_mappings,
)
from mas004_rpi_databridge.config import Settings
from mas004_rpi_databridge.device_bridge import DeviceBridge
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.params import ParamStore
from mas004_rpi_databridge.peers import peer_urls
from mas004_rpi_databridge.vj6530_runtime import RUNTIME as VJ6530_RUNTIME

_ASYNC_SUBSCRIPTIONS = [
    (int(AsyncSubscriptionId.PRINTER_IS_ONLINE), 0),
    (int(AsyncSubscriptionId.PRINTER_IS_OFFLINE), 0),
    (int(AsyncSubscriptionId.PRINTER_ENTERS_WARNING), 0),
    (int(AsyncSubscriptionId.PRINTER_LEAVES_WARNING), 0),
    (int(AsyncSubscriptionId.PRINTER_ENTERS_FAULT), 0),
    (int(AsyncSubscriptionId.PRINTER_LEAVES_FAULT), 0),
    (int(AsyncSubscriptionId.PRINTER_IS_BUSY), 0),
    (int(AsyncSubscriptionId.PRINTER_IS_NOT_BUSY), 0),
    (int(AsyncSubscriptionId.PRINTER_STARTS_PRINTING), 0),
    (int(AsyncSubscriptionId.PRINTER_FINISHES_PRINTING), 0),
    (int(AsyncSubscriptionId.PRINT_FAILED), 0),
]


class Vj6530AsyncListener:
    def __init__(self, cfg: Settings, params: ParamStore, logs: LogStore, outbox: Outbox):
        self.cfg = cfg
        self.params = params
        self.logs = logs
        self.outbox = outbox
        self.device_bridge = DeviceBridge(cfg, params, logs)
        self._status_snapshot: dict[str, bool | str] = {}

    def run_session(self, session_s: float = 30.0):
        client = ZbcClient(self.cfg.vj6530_host, self.cfg.vj6530_port, timeout_s=max(2.0, float(self.cfg.http_timeout_s or 5.0)))
        with client:
            client.detect_profile()
            msg_id, _ = client.subscribe_async(_ASYNC_SUBSCRIPTIONS)
            if int(msg_id) != int(MessageId.NUL):
                raise RuntimeError(f"6530 async subscribe failed with 0x{int(msg_id):04X}")

            VJ6530_RUNTIME.mark_async_ok()
            self.logs.log("vj6530", "info", "async subscription active")
            self._sync_from_summary(client.request_summary_info(force_refresh=True))

            deadline = time.monotonic() + max(5.0, float(session_s or 30.0))
            while time.monotonic() < deadline:
                try:
                    msg_id, response = client.receive_unsolicited()
                    VJ6530_RUNTIME.mark_async_ok()
                except TimeoutError:
                    VJ6530_RUNTIME.mark_async_ok()
                    continue
                except OSError:
                    raise

                if int(msg_id) != int(MessageId.AIR):
                    continue

                tag_ids = [int(getattr(tag, "tag_id", 0) or 0) for tag in getattr(response, "tags", [])]
                if not tag_ids:
                    continue

                self.logs.log("vj6530", "in", f"async tags={','.join(f'0x{tag_id:04X}' for tag_id in tag_ids)}")
                self._status_snapshot.update(_status_updates_from_async(tag_ids))
                VJ6530_RUNTIME.mark_async_event()

                if _needs_summary_sync(tag_ids):
                    try:
                        self._sync_from_summary(client.request_summary_info(force_refresh=True))
                    except Exception as exc:
                        self.logs.log("vj6530", "error", f"async summary refresh failed: {repr(exc)}")

    def _sync_from_summary(self, summary):
        rows = []
        rows.extend(self.params.list_params(ptype="TTP", limit=5000, offset=0))
        rows.extend(self.params.list_params(ptype="TTE", limit=5000, offset=0))
        rows.extend(self.params.list_params(ptype="TTW", limit=5000, offset=0))

        mapping_by_key: dict[str, str] = {}
        current_by_key: dict[str, str] = {}
        for row in rows:
            pkey = str(row.get("pkey") or "").strip()
            mapping = str(row.get("zbc_mapping") or "").strip()
            upper = mapping.upper()
            if not pkey or not mapping:
                continue
            if not (upper.startswith("STATUS[") or upper.startswith("STS[") or upper.startswith("IRQ{")):
                continue
            mapping_by_key[pkey] = mapping
            current_by_key[pkey] = str(row.get("effective_v") if row.get("effective_v") is not None else "0")

        if not mapping_by_key:
            return

        resolved = resolve_summary_mappings(mapping_by_key, summary, snapshot=self._status_snapshot)
        targets = peer_urls(self.cfg, "/api/inbox")

        for pkey, new_value in resolved.items():
            if new_value is None:
                continue
            new_text = str(new_value)
            if current_by_key.get(pkey, "0") == new_text:
                continue

            ok, msg = self.params.apply_device_value(pkey, new_text, promote_default=True)
            if not ok:
                self.logs.log("raspi", "error", f"vj6530 async persist failed for {pkey}: {msg}")
                continue

            line = f"{pkey}={new_text}"
            self.logs.log("vj6530", "in", f"async: {line}")
            self.logs.log("raspi", "in", f"vj6530 async: {line}")

            for url in targets:
                self.outbox.enqueue("POST", url, {}, {"msg": line, "source": "raspi", "origin": "vj6530"}, None)
            if targets:
                self.logs.log("raspi", "out", f"forward to microtom: {line}")

            if self.params.can_actor_read(pkey, actor="esp32"):
                ok, detail = self.device_bridge.mirror_to_esp(pkey, new_text)
                if ok:
                    self.logs.log("raspi", "out", f"forward to esp-plc: {line}")
                else:
                    self.logs.log("raspi", "info", f"skip esp mirror for {pkey}: {detail}")


def _needs_summary_sync(tag_ids: list[int]) -> bool:
    return any(
        tag_id
        in {
            int(AsyncSubscriptionId.PRINTER_IS_ONLINE),
            int(AsyncSubscriptionId.PRINTER_IS_OFFLINE),
            int(AsyncSubscriptionId.PRINTER_ENTERS_WARNING),
            int(AsyncSubscriptionId.PRINTER_LEAVES_WARNING),
            int(AsyncSubscriptionId.PRINTER_ENTERS_FAULT),
            int(AsyncSubscriptionId.PRINTER_LEAVES_FAULT),
            int(AsyncSubscriptionId.PRINTER_IS_BUSY),
            int(AsyncSubscriptionId.PRINTER_IS_NOT_BUSY),
            int(AsyncSubscriptionId.PRINTER_STARTS_PRINTING),
            int(AsyncSubscriptionId.PRINTER_FINISHES_PRINTING),
            int(AsyncSubscriptionId.PRINT_FAILED),
        }
        for tag_id in tag_ids
    )


def _status_updates_from_async(tag_ids: list[int]) -> dict[str, bool]:
    updates: dict[str, bool] = {}
    for tag_id in tag_ids:
        if tag_id == int(AsyncSubscriptionId.PRINTER_IS_ONLINE):
            updates["printer_online"] = True
        elif tag_id == int(AsyncSubscriptionId.PRINTER_IS_OFFLINE):
            updates["printer_online"] = False
        elif tag_id == int(AsyncSubscriptionId.PRINTER_ENTERS_WARNING):
            updates["printer_warning"] = True
        elif tag_id == int(AsyncSubscriptionId.PRINTER_LEAVES_WARNING):
            updates["printer_warning"] = False
        elif tag_id == int(AsyncSubscriptionId.PRINTER_ENTERS_FAULT):
            updates["printer_fault"] = True
        elif tag_id == int(AsyncSubscriptionId.PRINTER_LEAVES_FAULT):
            updates["printer_fault"] = False
        elif tag_id == int(AsyncSubscriptionId.PRINTER_IS_BUSY):
            updates["printer_busy"] = True
        elif tag_id == int(AsyncSubscriptionId.PRINTER_IS_NOT_BUSY):
            updates["printer_busy"] = False
        elif tag_id == int(AsyncSubscriptionId.PRINTER_STARTS_PRINTING):
            updates["printer_printing"] = True
            updates["printer_busy"] = True
        elif tag_id in (int(AsyncSubscriptionId.PRINTER_FINISHES_PRINTING), int(AsyncSubscriptionId.PRINT_FAILED)):
            updates["printer_printing"] = False
    return updates
