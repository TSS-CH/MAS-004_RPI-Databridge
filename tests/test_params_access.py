import tempfile
import unittest
from pathlib import Path

from mas004_rpi_databridge.db import DB, now_ts
from mas004_rpi_databridge.params import ParamStore


class ParamAccessTests(unittest.TestCase):
    def test_microtom_and_esp_access_are_evaluated_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DB(str(Path(tmpdir) / "db.sqlite3"))
            store = ParamStore(db)

            with db._conn() as c:
                c.execute(
                    """INSERT INTO params(
                        pkey,ptype,pid,min_v,max_v,default_v,unit,rw,esp_rw,dtype,name,format_relevant,
                        message,possible_cause,effects,remedy,updated_ts
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "TTP00002",
                        "TTP",
                        "00002",
                        0,
                        100,
                        "55",
                        None,
                        "W",
                        "R",
                        "unsigned int.",
                        "SomeParam",
                        "NO",
                        None,
                        None,
                        None,
                        None,
                        now_ts(),
                    ),
                )

            self.assertTrue(store.can_actor_write("TTP00002", actor="microtom"))
            self.assertTrue(store.can_actor_read("TTP00002", actor="esp32"))
            self.assertFalse(store.can_actor_write("TTP00002", actor="esp32"))
            self.assertEqual((False, "NAK_ReadOnly"), store.validate_write("TTP00002", "10", actor="esp32"))


if __name__ == "__main__":
    unittest.main()
