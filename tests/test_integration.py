"""Runs only when real sample PDFs are available:  PT_SAMPLE_DIR=/path/to/pdfs python -m unittest"""
import glob, os, unittest
from tests import _path  # noqa: F401
from ptracker.parsers import txn_statement, valuation_summary, factsheet
from ptracker import validation

D = os.environ.get("PT_SAMPLE_DIR", "")
def _one(pattern):
    hits = glob.glob(os.path.join(D, pattern)) if D else []
    return hits[0] if hits else None


@unittest.skipUnless(_one("TxnStatement*.pdf"), "set PT_SAMPLE_DIR to a folder with the sample PDFs")
class RealPdfTests(unittest.TestCase):
    def test_transaction_statement(self):
        p = txn_statement.parse_pdf(_one("TxnStatement*.pdf"))
        self.assertEqual(len(p.transactions), 107)
        self.assertEqual(len(p.schemes), 5)
        self.assertEqual(p.warnings, [])
        self.assertAlmostEqual(p.grand_total_market_value, 2120017.73, places=2)
        failed = [c for c in validation.validate_txn(p) if not c.ok and c.severity == "error"]
        self.assertEqual(failed, [])

    @unittest.skipUnless(_one("PortfolioValuationSummary.pdf"), "valuation pdf missing")
    def test_valuation_matches_transactions(self):
        t = txn_statement.parse_pdf(_one("TxnStatement*.pdf"))
        v = valuation_summary.parse_pdf(_one("PortfolioValuationSummary.pdf"))
        self.assertEqual(len(v.holdings), 5)
        self.assertEqual(v.snapshot["lifetime_xirr_pct"], -0.83)
        failed = [c for c in validation.validate_valuation_vs_txn(t, v) if not c.ok and c.severity == "error"]
        self.assertEqual(failed, [])

    @unittest.skipUnless(_one("Factsheet_Invesco*.pdf"), "factsheet pdf missing")
    def test_invesco_factsheet(self):
        f = factsheet.parse_pdf(_one("Factsheet_Invesco*.pdf"))
        self.assertEqual(f.info["benchmark_name"], "Nifty India Manufacturing TRI")
        self.assertEqual(f.info["expense_ratio_pct"], 2.04)
        self.assertEqual(len(f.holdings), 47)
        self.assertAlmostEqual(f.holdings["weight_pct"].sum(), 100.0, places=1)
        self.assertEqual(len(f.trailing), 8)

    def test_generated_workbook_contains_no_personal_data(self):
        import re, tempfile
        from openpyxl import load_workbook
        from ptracker import storage
        t = txn_statement.parse_pdf(_one("TxnStatement*.pdf"))
        v = valuation_summary.parse_pdf(_one("PortfolioValuationSummary.pdf")) \
            if _one("PortfolioValuationSummary.pdf") else None
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.xlsx")
            storage.save_portfolio(path, t, v, include_name=False)
            wb = load_workbook(path)
            text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row
                            if isinstance(c.value, str))
        self.assertIsNone(re.search(r"[A-Z]{5}\d{4}[A-Z]", text))          # full PAN
        self.assertIsNone(re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", text))      # mobile number
        self.assertIsNone(re.search(r"@[a-z0-9-]+\.", text))                # e-mail
        for word in ("DESHMUKH", "CIVIL HOSPITAL", "AXIS BANK", "NEAR CIVIL"):
            self.assertNotIn(word, text.upper())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.environ.get("PT_INDEX_DIR"), "set PT_INDEX_DIR to a folder with your NIFTY_50 csv files")
class RealIndexTests(unittest.TestCase):
    def test_three_nse_downloads_stitch_into_one_continuous_series(self):
        from ptracker.indexes.files import IndexStore
        from ptracker.indexes.spec import parse_benchmark
        st = IndexStore(os.environ["PT_INDEX_DIR"])
        spec = parse_benchmark("Nifty 50")
        s = st.series(spec)
        self.assertGreaterEqual(len(st.files), 3)
        self.assertLessEqual(st.coverage_stats(s)["largest_gap_days"], 5)
        self.assertEqual(st.value_on_or_after(spec, "2024-08-28")[1], 25052.35)
        self.assertEqual(st.warnings, [])


@unittest.skipUnless(os.environ.get("PT_PORTFOLIO") and os.environ.get("PT_INDEX_DIR"),
                     "set PT_PORTFOLIO (portfolio.xlsx) and PT_INDEX_DIR")
class RealAnalysisTests(unittest.TestCase):
    def test_portfolio_xirr_reproduces_the_statement_and_nifty_price_known_answers(self):
        from ptracker.analytics.engine import run_analysis
        res = run_analysis(os.environ["PT_PORTFOLIO"], os.environ["PT_INDEX_DIR"],
                           comparators=("NIFTY 50 TRI",), allow_price_fallback=True)
        self.assertEqual(res.schemes.set_index("scheme_name")["fund_xirr_pct"].round(2).to_dict(),
                         {"ICICI Pru Energy Opportunities Fund Reg (G)": 1.87,
                          "ICICI Pru Equity Minimum Variance Fund Reg (G)": -3.71,
                          "Invesco India Manufacturing Fund Reg (G)": 6.46,
                          "Mirae Asset Great Consumer Fund Reg (G)": -5.05,
                          "Sundaram Business Cycle Fund Reg (G)": -4.82})
        whole = res.portfolio.iloc[0]
        self.assertEqual(whole["fund_xirr_pct"], -0.83)
        n50 = res.portfolio[res.portfolio["scope"].str.startswith("vs NIFTY 50")].iloc[0]
        self.assertAlmostEqual(n50["bench_xirr_pct"], -5.81, delta=0.05)     # independent calc from earlier
        self.assertAlmostEqual(n50["gap_rs"], 117860, delta=500)