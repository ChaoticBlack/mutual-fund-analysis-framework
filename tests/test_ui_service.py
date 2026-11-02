import glob, os, tempfile, unittest
from tests import _path  # noqa: F401
import pandas as pd
from ptracker.ui import service
from ptracker.ui.service import inr, pct, pts
from tests.helpers import make_workspace


class FormatTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(inr(2120018), "\u20b921,20,018")
        self.assertEqual(inr(-19982), "-\u20b919,982")
        self.assertEqual(inr(100), "\u20b9100")
        self.assertEqual(inr(1234567.891, 2), "\u20b912,34,567.89")
        self.assertEqual(inr(None), "-")
        self.assertEqual(inr(float("nan")), "-")
        self.assertEqual(inr(0), "\u20b90")

    def test_percent_and_points(self):
        self.assertEqual(pct(-0.83), "-0.83%")
        self.assertEqual(pct(None), "-")
        self.assertEqual(pts(3.844), "+3.84 pts")
        self.assertEqual(pts(-1.2), "-1.20 pts")


class FolderTests(unittest.TestCase):
    def test_signature_changes_when_a_file_is_added_or_changed(self):
        with tempfile.TemporaryDirectory() as d:
            s0 = service.dir_signature(d)
            with open(os.path.join(d, "a.csv"), "w") as fh:
                fh.write("x")
            s1 = service.dir_signature(d)
            with open(os.path.join(d, "a.csv"), "w") as fh:
                fh.write("xyz")
            s2 = service.dir_signature(d)
            self.assertEqual(len({s0, s1, s2}), 3)
            self.assertEqual(service.dir_signature(os.path.join(d, "missing")), ())

    def test_uploads_are_sanitised(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = service.save_uploads(d, [("..\\..\\evil.csv", b"a,b"), ("good.csv", b"1"), ("run.exe", b"MZ"), ("", b"")])
            self.assertEqual(sorted(os.listdir(d)), ["evil.csv", "good.csv"])      # path part dropped, exe refused
            self.assertTrue(any("Skipped" in m for m in msgs))
            self.assertFalse(os.path.exists(os.path.join(d, "..", "evil.csv")))


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)

    def test_empty_folder_gives_an_empty_bundle(self):
        b = service.load_bundle(service.make_paths(self.d.name))
        self.assertFalse(b.has_portfolio)
        self.assertIsNone(b.analysis)

    def test_full_workspace_loads_everything(self):
        make_workspace(self.d.name)
        b = service.load_bundle(service.make_paths(self.d.name), comparators=(), risk_free_pct=0.0)
        self.assertTrue(b.has_portfolio and b.has_nav)
        self.assertEqual(b.errors, [])
        self.assertFalse(b.analysis.schemes.empty)
        self.assertEqual(list(b.nav_plan["status"]), ["ready"])
        d = service.fund_daily(b.risk, "Demo Equity Fund Reg (G)")
        self.assertIn("Own benchmark (same cash flows)", service.bench_columns(d))
        self.assertIn("Each fund's own benchmark, combined (same cash flows)", service.bench_columns(d, "Each fund's own benchmark, combined"))

    def test_missing_nav_still_gives_performance_numbers(self):
        make_workspace(self.d.name, with_nav=False)
        b = service.load_bundle(service.make_paths(self.d.name), comparators=())
        self.assertFalse(b.has_nav)
        self.assertIsNotNone(b.analysis)
        self.assertFalse(b.analysis.schemes.empty)

    def test_failed_validation_saves_nothing(self):
        paths = service.make_paths(self.d.name)
        ok, msgs = service.ingest_pdfs(paths, ("bad.pdf", b"this is not a pdf"))
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(paths.portfolio))
        self.assertTrue(any("Could not read" in m for m in msgs))


D = os.environ.get("PT_SAMPLE_DIR", "")


@unittest.skipUnless(D and glob.glob(os.path.join(D, "TxnStatement*.pdf")), "set PT_SAMPLE_DIR to run with real PDFs")
class RealPdfIngestTests(unittest.TestCase):
    def test_upload_flow_end_to_end(self):
        def rd(pattern):
            p = glob.glob(os.path.join(D, pattern))
            return (os.path.basename(p[0]), open(p[0], "rb").read()) if p else None
        with tempfile.TemporaryDirectory() as d:
            paths = service.make_paths(d)
            ok, msgs = service.ingest_pdfs(paths, rd("TxnStatement*.pdf"), rd("PortfolioValuationSummary.pdf"),
                                           [x for x in [rd("Factsheet_Invesco*.pdf")] if x])
            self.assertTrue(ok, msgs)
            self.assertTrue(os.path.exists(paths.portfolio) and os.path.exists(paths.factsheets))
            self.assertTrue(any("0 failed" in m for m in msgs))
            self.assertFalse(any(f.lower().endswith(".pdf") for f in os.listdir(d)))      # PDFs are not kept


if __name__ == "__main__":
    unittest.main()