import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys
import types

sys.modules.setdefault("ping3", types.SimpleNamespace(ping=lambda *args, **kwargs: None))

from mas004_rpi_databridge.db import DB, now_ts
from mas004_rpi_databridge.device_bridge import DeviceBridge
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.params import ParamStore


class FakeZbcBridgeClient:
    def __init__(self):
        self.read_calls = []
        self.write_calls = []

    def read_mapped_value(self, mapping: str):
        self.read_calls.append(mapping)
        return "7"

    def write_mapped_value(self, mapping: str, value, verify_readback: bool = True):
        self.write_calls.append((mapping, str(value), verify_readback))
        return 0, str(value)


class DeviceBridgeTtoMappingTests(unittest.TestCase):
    def test_zbc_mapping_read_and_write_use_bridge_client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DB(str(Path(tmpdir) / "db.sqlite3"))
            params = ParamStore(db)
            logs = LogStore(db)

            with db._conn() as c:
                c.execute(
                    """INSERT INTO params(
                        pkey,ptype,pid,min_v,max_v,default_v,unit,rw,dtype,name,format_relevant,
                        message,possible_cause,effects,remedy,updated_ts
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "TTP00071",
                        "TTP",
                        "00071",
                        0,
                        1000,
                        "0",
                        "ms",
                        "R/W",
                        "unsigned int.",
                        "JobUpdateReplyDelay",
                        "NO",
                        None,
                        None,
                        None,
                        None,
                        now_ts(),
                    ),
                )
                c.execute(
                    """INSERT INTO param_device_map(
                        pkey, esp_key, zbc_mapping, zbc_message_id, zbc_command_id, zbc_value_codec,
                        zbc_scale, zbc_offset, ultimate_set_cmd, ultimate_get_cmd, ultimate_var_name, updated_ts
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "TTP00071",
                        None,
                        "FRQ[CURRENT_PARAMETERS]/System/TCPIP/JobUpdateReplyDelay",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        now_ts(),
                    ),
                )

            cfg = SimpleNamespace(
                esp_host="",
                esp_port=0,
                http_timeout_s=2.0,
                esp_watchdog_host="",
                watchdog_timeout_s=1.0,
                watchdog_down_after=3,
                vj6530_host="10.0.0.5",
                vj6530_port=3002,
                vj3350_host="",
                vj3350_port=0,
                esp_simulation=True,
                vj6530_simulation=False,
                vj3350_simulation=True,
            )

            bridge = DeviceBridge(cfg, params, logs)
            bridge._zbc_bridge = FakeZbcBridgeClient()

            read_resp = bridge.execute("vj6530", "TTP00071", "TTP", "read", "?")
            write_resp = bridge.execute("vj6530", "TTP00071", "TTP", "write", "9")

            self.assertEqual("TTP00071=7", read_resp)
            self.assertEqual("ACK_TTP00071=9", write_resp)
            self.assertEqual("9", params.get_effective_value("TTP00071"))


if __name__ == "__main__":
    unittest.main()
