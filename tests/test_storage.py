import os, tempfile, unittest
from tests import _path  # noqa: F401
import pandas as pd
from openpyxl import load_workbook
from ptracker import storage
from ptracker.parsers.txn_statement import parse_lines
from tests.test_txn_parser import SAMPLE


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "p.xlsx")
        self.txn = parse_lines(SAMPLE)

    def tearDown(self):
        self.dir.cleanup()

    def test_reingest_is_idempotent(self):
        a = storage.save_portfolio(self.path, self.txn)
        b = storage.save_portfolio(self.path, self.txn)
        self.assertEqual(a["transactions_added"], 3)
        self.assertEqual(b["transactions_added"], 0)
        self.assertEqual(len(storage.load_portfolio(self.path)["Transactions"]), 3)

    def test_hand_edited_columns_survive_reingest(self):
        storage.save_portfolio(self.path, self.txn)
        wb = load_workbook(self.path); ws = wb["Schemes"]
        col = [c.value for c in ws[1]].index("benchmark_file") + 1
        ws.cell(row=2, column=col, value="my_index.csv"); wb.save(self.path)
        storage.save_portfolio(self.path, self.txn)
        self.assertEqual(storage.load_portfolio(self.path)["Schemes"].iloc[0]["benchmark_file"], "my_index.csv")

    def test_no_personal_data_in_workbook(self):
        storage.save_portfolio(self.path, self.txn, include_name=False)
        meta = storage.load_portfolio(self.path)["Meta"].astype(str)
        blob = " ".join(str(x) for x in meta.values.ravel())
        self.assertNotIn("ABCDE1234F", blob)
        self.assertIn("(omitted)", blob)


if __name__ == "__main__":
    unittest.main()
