import unittest
from tests import _path  # noqa: F401
from ptracker.indexes.spec import parse_benchmark, nse_name_candidates


class SpecTests(unittest.TestCase):
    def test_factsheet_style_names(self):
        s = parse_benchmark("NSE - Nifty India Manufacturing TRI")
        self.assertEqual((s.base, s.variant, s.source), ("NIFTY INDIA MANUFACTURING", "TRI", "NSE"))
        self.assertEqual(parse_benchmark("Nifty 50 TRI").key, "NIFTY 50|TRI")
        self.assertEqual(parse_benchmark("Nifty 50 Index").variant, "PRICE")
        self.assertEqual(parse_benchmark("Nifty 50 - Net Total Return").variant, "NTR")

    def test_non_nse_sources_are_flagged(self):
        self.assertEqual(parse_benchmark("S&P BSE 500 TRI").source, "BSE")
        self.assertEqual(parse_benchmark("CRISIL Hybrid 35+65 Aggressive TRI").source, "OTHER")

    def test_slug_and_candidates(self):
        self.assertEqual(parse_benchmark("Nifty 50 TRI").slug, "NIFTY_50_TRI")
        self.assertEqual(nse_name_candidates("NIFTY 100 LOW VOLATILITY 30"),
                         ["NIFTY 100 LOW VOLATILITY 30", "NIFTY100 LOW VOLATILITY 30"])
        self.assertEqual(nse_name_candidates("NIFTY 50"), ["NIFTY 50"])


if __name__ == "__main__":
    unittest.main()
