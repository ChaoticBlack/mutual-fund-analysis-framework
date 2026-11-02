import os, tempfile, unittest
from tests import _path  # noqa: F401
import pandas as pd
from ptracker.indexes.files import IndexStore, read_index_file
from ptracker.indexes.spec import parse_benchmark

PRICE_A = "\ufeffDate ,Open ,High ,Low ,Close ,Shares Traded ,Turnover (\u20b9 Cr)\n" \
          "05-SEP-2024,1,1,1,100.5,1,1\n04-SEP-2024,1,1,1,100.0,1,1\n03-SEP-2024,1,1,1,99.0,1,1\n"
PRICE_B = "\ufeffDate ,Open ,High ,Low ,Close ,Shares Traded ,Turnover (\u20b9 Cr)\n" \
          "10-SEP-2024,1,1,1,103.0,1,1\n09-SEP-2024,1,1,1,102.0,1,1\n05-SEP-2024,1,1,1,100.5,1,1\n"
TRI = "Index Name,Date,TotalReturnsIndex,NTR_Value\n" \
      "Nifty 50,04 Sep 2024,35000.5,31000.1\nNifty 50,05 Sep 2024,35100.0,31050.0\n"


class IndexFileTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.w = lambda name, text: open(os.path.join(self.d.name, name), "w", encoding="utf-8").write(text)

    def tearDown(self):
        self.d.cleanup()

    def test_nse_price_file_with_bom_and_trailing_spaces(self):
        self.w("NIFTY_50-03-09-2024-to-05-09-2024.csv", PRICE_A)
        f = read_index_file(os.path.join(self.d.name, "NIFTY_50-03-09-2024-to-05-09-2024.csv"))
        self.assertEqual(f.spec.key, "NIFTY 50|PRICE")
        self.assertEqual(len(f.series), 3)
        self.assertEqual(f.series.loc["2024-09-05"], 100.5)

    def test_overlapping_downloads_are_stitched_without_duplicates(self):
        self.w("NIFTY_50-03-09-2024-to-05-09-2024.csv", PRICE_A)
        self.w("NIFTY_50-05-09-2024-to-10-09-2024.csv", PRICE_B)
        st = IndexStore(self.d.name)
        s = st.series(parse_benchmark("Nifty 50"))
        self.assertEqual(len(s), 5)
        self.assertTrue(s.index.is_monotonic_increasing)
        self.assertEqual(st.warnings, [])

    def test_conflicting_overlap_is_warned(self):
        self.w("NIFTY_50-03-09-2024-to-05-09-2024.csv", PRICE_A)
        self.w("NIFTY_50-05-09-2024-to-10-09-2024.csv", PRICE_B.replace("100.5", "150.0"))
        self.assertTrue(any("disagree" in w for w in IndexStore(self.d.name).warnings))

    def test_tri_and_price_are_kept_separate(self):
        self.w("NIFTY_50-03-09-2024-to-05-09-2024.csv", PRICE_A)
        self.w("nifty50_tri.csv", TRI)
        st = IndexStore(self.d.name)
        self.assertTrue(st.has(parse_benchmark("Nifty 50 TRI")))
        self.assertTrue(st.has(parse_benchmark("Nifty 50")))
        self.assertEqual(st.series(parse_benchmark("Nifty 50 TRI")).iloc[0], 35000.5)

    def test_bad_file_is_reported_not_fatal(self):
        self.w("broken.csv", "foo,bar\n1,2\n")
        self.w("NIFTY_50-03-09-2024-to-05-09-2024.csv", PRICE_A)
        st = IndexStore(self.d.name)
        self.assertEqual(len(st.files), 1)
        self.assertTrue(any("broken.csv" in e for e in st.errors))

    def test_excel_files_work(self):
        pd.DataFrame({"Date": ["03-SEP-2024", "04-SEP-2024"], "Close": [1.0, 2.0]}).to_excel(
            os.path.join(self.d.name, "NIFTY_NEXT_50-01-09-2024-to-30-09-2024.xlsx"), index=False)
        st = IndexStore(self.d.name)
        self.assertTrue(st.has(parse_benchmark("Nifty Next 50")))


if __name__ == "__main__":
    unittest.main()


class NameWinsOverGenericColumnTest(unittest.TestCase):
    def test_tri_in_file_name_with_generic_close_column_is_treated_as_tri(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "NIFTY 50 TRI-01-09-2024-to-30-09-2024.csv"), "w").write(
                "Date,Close\n03-SEP-2024,35000\n04-SEP-2024,35100\n")
            f = read_index_file(os.path.join(d, "NIFTY 50 TRI-01-09-2024-to-30-09-2024.csv"))
            self.assertEqual(f.spec.variant, "TRI")
            self.assertTrue(f.warnings)

    def test_fuzzy_total_return_column(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "NIFTY ENERGY-01-09-2024-to-30-09-2024.csv"), "w").write(
                "Date,Total Return Index Value\n03-SEP-2024,100\n04-SEP-2024,101\n")
            self.assertEqual(read_index_file(os.path.join(d, "NIFTY ENERGY-01-09-2024-to-30-09-2024.csv")).spec.variant, "TRI")