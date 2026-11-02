import unittest
from tests import _path  # noqa: F401
from ptracker.parsers.factsheet import _left_panel_fields

PANEL = """Demo Fund
Investment Objective
Capital appreciation.
Launch Date
14 Aug 2024
Scheme Profile
Corpus (Cr.) 732.00
52 Week High 12.01 (20 Aug 2026)
Current NAV 11.3600 (29 Oct 2026)
52 Week Low 9.08 (31 Mar 2026)
0.5% for redemption within 3
Exit Load
months
Expense Ratio 2.04
Taxation Equity
Category
Equity: Thematic
Benchmark
NSE - Nifty India Manufacturing TRI
Composition (%)
Equity 93.07
Debt 6.93"""


class FactsheetHelperTests(unittest.TestCase):
    def test_exit_load_value_sits_above_label(self):
        f = _left_panel_fields(PANEL)
        self.assertEqual(f["Exit Load"], "0.5% for redemption within 3 months")

    def test_repeated_word_equity_maps_to_right_label(self):
        f = _left_panel_fields(PANEL)
        self.assertEqual(f["Taxation"], "Equity")
        self.assertEqual(f["Equity"], "93.07")
        self.assertEqual(f["Benchmark"], "NSE - Nifty India Manufacturing TRI")


if __name__ == "__main__":
    unittest.main()
