import os, tempfile, unittest
from tests import _path  # noqa: F401
import pandas as pd
from openpyxl import load_workbook
from ptracker import storage
from ptracker.indexes.files import IndexStore
from ptracker.indexes.plan import build_plan
from ptracker.parsers.txn_statement import parse_lines
from tests.test_txn_parser import SAMPLE


def csv_price(name, start, end):
    days = pd.bdate_range(start, end)
    df = pd.DataFrame({"Date ": days.strftime("%d-%b-%Y").str.upper(), "Close ": range(100, 100 + len(days))})
    df.to_csv(os.path.join(name), index=False, encoding="utf-8-sig")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.idx = os.path.join(self.d.name, "idx"); os.makedirs(self.idx)
        self.pf = os.path.join(self.d.name, "p.xlsx")
        storage.save_portfolio(self.pf, parse_lines(SAMPLE))   # one scheme, 2024-08-28 .. 2026-10-23

    def tearDown(self):
        self.d.cleanup()

    def _set(self, **cols):
        wb = load_workbook(self.pf); ws = wb["Schemes"]; hdr = [c.value for c in ws[1]]
        for k, v in cols.items():
            ws.cell(row=2, column=hdr.index(k) + 1, value=v)
        wb.save(self.pf)

    def _status(self, bench):
        plan, mapping = build_plan(self.pf, IndexStore(self.idx), comparators=())
        return plan, mapping

    def test_no_benchmark_is_flagged(self):
        plan, mapping = self._status(None)
        self.assertEqual(mapping.iloc[0]["status"], "no_benchmark")

    def test_missing_then_ready_when_file_dropped_in(self):
        self._set(benchmark_name="Nifty Energy TRI")
        plan, mapping = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "missing")
        self.assertTrue(plan.iloc[0]["fetchable"])
        pd.DataFrame({"Index Name": "Nifty Energy", "Date": pd.bdate_range("2024-08-20", "2026-10-30")
                      .strftime("%d %b %Y"), "TotalReturnsIndex": 1.0}).assign(
            TotalReturnsIndex=lambda d: range(1, len(d) + 1)).to_csv(os.path.join(self.idx, "energy_tri.csv"), index=False)
        plan, mapping = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "ready")
        self.assertEqual(mapping.iloc[0]["status"], "ready")

    def test_partial_coverage_is_detected(self):
        self._set(benchmark_name="Nifty Energy TRI")
        pd.DataFrame({"Index Name": "Nifty Energy", "Date": pd.bdate_range("2024-08-20", "2025-12-31")
                      .strftime("%d %b %Y"), "TotalReturnsIndex": 5.0}).to_csv(
            os.path.join(self.idx, "energy_tri.csv"), index=False)
        plan, _ = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "partial")
        self.assertIn("ends", plan.iloc[0]["action"])

    def test_price_series_is_never_silently_used_for_tri(self):
        self._set(benchmark_name="Nifty 50 TRI")
        csv_price(os.path.join(self.idx, "NIFTY_50-20-08-2024-to-30-10-2026.csv"), "2024-08-20", "2026-10-30")
        plan, mapping = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "price_only")
        self.assertEqual(mapping.iloc[0]["status"], "price_only")

    def test_bse_benchmark_is_manual_only(self):
        self._set(benchmark_name="S&P BSE 500 TRI")
        plan, _ = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "manual_only")
        self.assertFalse(plan.iloc[0]["fetchable"])

    def test_file_override_wins_over_benchmark_name(self):
        self._set(benchmark_name="Nifty Energy TRI", benchmark_file="my_custom_index.csv")
        pd.DataFrame({"Date": pd.bdate_range("2024-08-20", "2026-10-30").strftime("%d-%m-%Y"),
                      "Total Returns Index": 1.0}).to_csv(os.path.join(self.idx, "my_custom_index.csv"), index=False)
        plan, mapping = self._status("x")
        self.assertEqual(mapping.iloc[0]["source"], "file override")
        self.assertEqual(mapping.iloc[0]["status"], "ready")

    def test_standard_comparators_are_always_requested(self):
        plan, _ = build_plan(self.pf, IndexStore(self.idx))
        self.assertIn("NIFTY 50 TRI", set(plan["benchmark"]))
        self.assertIn("NIFTY 500 TRI", set(plan["benchmark"]))


if __name__ == "__main__":
    unittest.main()


class AssumedTriAndAliasTests(PlanTests):
    def test_benchmark_without_tri_word_is_assumed_tri_and_noted(self):
        self._set(benchmark_name="Nifty Energy")
        plan, _ = self._status("x")
        self.assertEqual(plan.iloc[0]["benchmark"], "NIFTY ENERGY TRI")
        self.assertIn("TRI assumed", plan.iloc[0]["note"])

    def test_similar_files_are_suggested_and_alias_resolves_them(self):
        self._set(benchmark_name="Nifty Consumption TRI")
        pd.DataFrame({"Index Name": "Nifty India Consumption", "Date": pd.bdate_range("2024-08-20", "2026-10-30")
                      .strftime("%d %b %Y"), "TotalReturnsIndex": 1.0}).to_csv(
            os.path.join(self.idx, "consumption.csv"), index=False)
        plan, _ = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "missing")
        self.assertIn("NIFTY INDIA CONSUMPTION", plan.iloc[0]["note"])
        open(os.path.join(self.idx, "aliases.csv"), "w").write("alias,canonical\nNIFTY CONSUMPTION,NIFTY INDIA CONSUMPTION\n")
        plan, mapping = self._status("x")
        self.assertEqual(plan.iloc[0]["status"], "ready")