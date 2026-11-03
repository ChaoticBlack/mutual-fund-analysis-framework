"""Tests that need your own files. Nothing in here contains your figures: they check properties that must hold for ANY
valid statement (totals reconcile, our XIRR equals the distributor's printed XIRR, no personal data leaks).

Enable with environment variables (all optional):
  PT_SAMPLE_DIR  folder with TxnStatement*.pdf, PortfolioValuationSummary.pdf, Factsheet*.pdf
  PT_PORTFOLIO   portfolio.xlsx      PT_INDEX_DIR  index folder
"""
import glob, os, re, tempfile, unittest
from tests import _path  # noqa: F401
from ptracker import storage, validation
from ptracker.analytics.xirr import xirr
from ptracker.analytics.engine import investor_flows
from ptracker.parsers import factsheet, txn_statement, valuation_summary

D = os.environ.get("PT_SAMPLE_DIR", "")


def _one(pattern):
    hits = sorted(glob.glob(os.path.join(D, pattern))) if D else []
    return hits[0] if hits else None


@unittest.skipUnless(_one("TxnStatement*.pdf"), "set PT_SAMPLE_DIR to a folder with your statement PDFs")
class RealPdfTests(unittest.TestCase):
    def test_transaction_statement_reconciles(self):
        p = txn_statement.parse_pdf(_one("TxnStatement*.pdf"))
        self.assertGreater(len(p.transactions), 0)
        self.assertEqual(p.warnings, [])
        failed = [c for c in validation.validate_txn(p) if not c.ok and c.severity == "error"]
        self.assertEqual(failed, [])
        self.assertAlmostEqual(p.schemes["market_value"].sum(), p.grand_total_market_value, delta=1.0)

    @unittest.skipUnless(_one("PortfolioValuationSummary*.pdf"), "valuation pdf missing")
    def test_valuation_agrees_and_xirr_matches_the_printed_one(self):
        t = txn_statement.parse_pdf(_one("TxnStatement*.pdf"))
        v = valuation_summary.parse_pdf(_one("PortfolioValuationSummary*.pdf"))
        self.assertGreater(len(v.holdings), 0)
        failed = [c for c in validation.validate_valuation_vs_txn(t, v) if not c.ok and c.severity == "error"]
        self.assertEqual(failed, [])
        flows = investor_flows(t.transactions)
        end = [(r.nav_date, r.market_value) for r in t.schemes.itertuples()]
        ours = xirr(flows + end)
        if "lifetime_xirr_pct" in v.snapshot and ours is not None:
            self.assertAlmostEqual(100 * ours, v.snapshot["lifetime_xirr_pct"], delta=0.15)

    @unittest.skipUnless(_one("Factsheet*.pdf"), "factsheet pdf missing")
    def test_factsheet_is_read_sensibly(self):
        f = factsheet.parse_pdf(_one("Factsheet*.pdf"))
        self.assertTrue(f.info["benchmark_name"])
        self.assertIsNotNone(f.info["expense_ratio_pct"])
        self.assertGreater(len(f.holdings), 5)
        self.assertLessEqual(f.holdings["weight_pct"].sum(), 100.5)
        self.assertGreaterEqual(len(f.trailing), 5)

    def test_generated_workbook_contains_no_personal_data(self):
        from openpyxl import load_workbook
        pdf = _one("TxnStatement*.pdf")
        t = txn_statement.parse_pdf(pdf)
        v = valuation_summary.parse_pdf(_one("PortfolioValuationSummary*.pdf")) if _one("PortfolioValuationSummary*.pdf") else None
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.xlsx")
            storage.save_portfolio(path, t, v, include_name=False, sources={"transactions": pdf})
            wb = load_workbook(path)
            text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row if isinstance(c.value, str)).upper()
        # 1) patterns
        self.assertIsNone(re.search(r"[A-Z]{5}\d{4}[A-Z]", text))                    # PAN
        self.assertIsNone(re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", text))                # mobile number
        self.assertIsNone(re.search(r"@[A-Z0-9-]+\.", text))                          # e-mail
        # 2) every line of the PDF's own header block (name, address, contacts) - read from the file itself
        lines = txn_statement.extract_lines(pdf)
        stop = next((i for i, l in enumerate(lines) if "Transaction Statement" in l), 0)
        for line in lines[:stop]:
            line = " ".join(line.split()).upper()
            if len(line) >= 8:
                self.assertNotIn(line, text, f"header line leaked into the workbook: {line[:20]}...")
        # 3) the PDF's file name usually contains the investor's name
        for tok in re.split(r"[^A-Za-z0-9]+", os.path.basename(pdf)):
            if len(tok) >= 5 and tok.lower() not in ("txnstatement", "statement"):
                self.assertNotIn(tok.upper(), text, f"file-name part leaked: {tok}")


PF, IX = os.environ.get("PT_PORTFOLIO", ""), os.environ.get("PT_INDEX_DIR", "")


@unittest.skipUnless(PF and IX and os.path.exists(PF), "set PT_PORTFOLIO and PT_INDEX_DIR")
class RealAnalysisTests(unittest.TestCase):
    def test_engine_xirr_equals_an_independent_recomputation(self):
        import pandas as pd
        from ptracker.analytics.engine import run_analysis
        res = run_analysis(PF, IX, comparators=("NIFTY 50 TRI",), allow_price_fallback=True)
        pf = storage.load_portfolio(PF)
        flows = investor_flows(pf["Transactions"])
        end = [(pd.to_datetime(r.last_nav_date).date(), float(r.market_value)) for r in pf["Schemes"].itertuples()]
        self.assertAlmostEqual(res.portfolio.iloc[0]["fund_xirr_pct"], round(100 * xirr(flows + end), 2), places=2)
        n = len(pf["Schemes"])
        self.assertEqual(len(res.schemes), n)
        self.assertAlmostEqual(res.schemes["current_value"].sum(), res.portfolio.iloc[0]["fund_value"], delta=n)


@unittest.skipUnless(IX and os.path.isdir(IX), "set PT_INDEX_DIR to your index folder")
class RealIndexTests(unittest.TestCase):
    def test_downloads_stitch_into_a_continuous_series(self):
        from ptracker.indexes.files import IndexStore
        st = IndexStore(IX)
        self.assertGreater(len(st.files), 0)
        for key, s in st._series.items():
            self.assertLessEqual(st.coverage_stats(s)["largest_gap_days"], 7, key)
        self.assertEqual(st.warnings, [])

    def test_public_reference_value(self):
        """Nifty 50 closed at 25,052.35 on 28 Aug 2024 (public data) - skipped if you don't hold that price series."""
        import pandas as pd
        from ptracker.indexes.files import IndexStore
        from ptracker.indexes.spec import parse_benchmark
        st = IndexStore(IX)
        spec = parse_benchmark("Nifty 50")
        if not st.has(spec) or pd.Timestamp("2024-08-28") not in st.series(spec).index:
            self.skipTest("no Nifty 50 price series covering 28 Aug 2024")
        self.assertEqual(float(st.series(spec).loc["2024-08-28"]), 25052.35)


if __name__ == "__main__":
    unittest.main()