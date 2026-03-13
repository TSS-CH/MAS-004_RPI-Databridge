import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from mas004_rpi_databridge.db import DB
from mas004_rpi_databridge.params import ParamStore


class ParamImportZbcMappingTests(unittest.TestCase):
    def test_import_xlsx_reads_zbc_mapping_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workbook_path = Path(tmpdir) / "params.xlsx"
            db_path = Path(tmpdir) / "db.sqlite3"

            wb = Workbook()
            ws = wb.active
            ws.title = "Parameter"
            ws.append(
                [
                    "Params_Type.:",
                    "Param. ID.:",
                    "Min.:",
                    "Max.:",
                    "Default Value:",
                    "Einheit:",
                    "R/W:",
                    "ESP32 R/W:",
                    "Data Type:",
                    "Name:",
                    "ZBC Mapping:",
                ]
            )
            ws.append(
                [
                    "TTP",
                    "00071",
                    0,
                    1000,
                    0,
                    "ms",
                    "R/W",
                    "R",
                    "unsigned int.",
                    "JobUpdateReplyDelay",
                    "FRQ[CURRENT_PARAMETERS]/System/TCPIP/JobUpdateReplyDelay",
                ]
            )
            wb.save(workbook_path)

            store = ParamStore(DB(str(db_path)))
            result = store.import_xlsx(str(workbook_path))

            self.assertTrue(result["ok"])
            mapping = store.get_device_map("TTP00071")
            self.assertEqual(
                "FRQ[CURRENT_PARAMETERS]/System/TCPIP/JobUpdateReplyDelay",
                mapping.get("zbc_mapping"),
            )
            self.assertEqual("R", store.get_meta("TTP00071").get("esp_rw"))


if __name__ == "__main__":
    unittest.main()
