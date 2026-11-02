"""Runs the whole app flow against fake streamlit/plotly. Proves every page executes on real computed data
(no NameError, bad column, empty-frame crash); it cannot judge appearance."""
import importlib, os, sys, tempfile, unittest
from tests import _path  # noqa: F401
from tests import fakes
from tests.helpers import make_workspace

_KEYS = ("streamlit", "plotly", "plotly.graph_objects", "ptracker.ui.main", "ptracker.ui.charts")


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in _KEYS}
        for k in ("ptracker.ui.main", "ptracker.ui.charts"):
            sys.modules.pop(k, None)
        fakes.install_fake_plotly()
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def run_app(self, **kw):
        fake = fakes.install_fake_streamlit(**kw)
        app = importlib.import_module("ptracker.ui.main")
        importlib.reload(app)
        app.main(default_root=self.d.name)
        return fake

    def figures(self, fake):
        return [c[1][0] for c in fake.calls if c[0] == "plotly_chart"]

    def test_first_run_with_no_data_guides_the_user(self):
        fake = self.run_app()
        text = " ".join(str(c) for c in fake.calls if c[0] == "info")
        self.assertIn("No portfolio found", text)
        self.assertIn(("tabs", ("Overview", "Funds", "Compare", "Risk", "Overlap", "Costs", "Holdings", "Data & Setup")), fake.calls)
        self.assertEqual(self.figures(fake), [])

    def test_full_data_renders_charts_and_numbers(self):
        make_workspace(self.d.name)
        fake = self.run_app()
        self.assertGreaterEqual(fake.names().count("metric"), 10)
        figs = self.figures(fake)
        self.assertGreaterEqual(len(figs), 8)
        traces = [t.kw.get("name") for f in figs for t in f.data]
        self.assertIn("Your value", traces)
        self.assertIn("Money put in", traces)
        self.assertIn("Up capture", traces)
        self.assertFalse([c for c in fake.calls if c[0] == "error"])

    def test_portfolio_without_nav_history_still_renders(self):
        make_workspace(self.d.name, with_nav=False)
        fake = self.run_app()
        text = " ".join(str(c) for c in fake.calls if c[0] == "info")
        self.assertIn("NAV history", text)
        self.assertGreaterEqual(fake.names().count("metric"), 5)

    def test_portfolio_without_index_files_does_not_crash(self):
        make_workspace(self.d.name, with_index=False, with_nav=False)
        fake = self.run_app()
        self.assertFalse([c for c in fake.calls if c[0] == "error"])

    def test_saving_uploaded_index_files(self):
        make_workspace(self.d.name, with_index=False)
        class Up:
            name, data = "extra.csv", b"Date,Close\n01-JAN-2024,1\n"
            def getvalue(self): return self.data
        fake = self.run_app(clicks={"Save index files"}, uploads={"Index files (CSV / Excel)": [Up()]})
        self.assertTrue(os.path.exists(os.path.join(self.d.name, "indexes", "extra.csv")))
        self.assertEqual(fake.session_state["messages"][0], "info")


if __name__ == "__main__":
    unittest.main()


class ResilienceTests(SmokeTests):
    def test_every_chart_has_a_unique_key(self):
        make_workspace(self.d.name)
        fake = self.run_app()
        keys = [c[2].get("key") for c in fake.calls if c[0] == "plotly_chart"]
        self.assertTrue(all(keys))
        self.assertEqual(len(keys), len(set(keys)))

    def test_same_chart_on_two_tabs_is_allowed(self):
        make_workspace(self.d.name)
        fake = self.run_app()
        figs = self.figures(fake)
        sigs = [repr([t.kw for t in f.data]) for f in figs]
        self.assertLess(len(set(sigs)), len(sigs))        # the XIRR chart really is drawn twice (Overview + Compare)

    def test_one_failing_tab_does_not_blank_the_others(self):
        make_workspace(self.d.name)
        fake = fakes.install_fake_streamlit()
        app = importlib.import_module("ptracker.ui.main")
        importlib.reload(app)
        def boom(b): raise ValueError("synthetic failure")
        app.render_compare = boom
        # main() looks the page functions up at call time through the module namespace
        app.main(default_root=self.d.name)
        errs = [c for c in fake.calls if c[0] == "error"]
        self.assertEqual(len(errs), 1)
        self.assertIn("synthetic failure", str(errs[0]))
        self.assertGreater(len([c for c in fake.calls if c[0] == "plotly_chart"]), 4)    # other tabs still drew charts

    def test_mixed_type_columns_become_text(self):
        import pandas as pd
        from datetime import date
        fakes.install_fake_streamlit()
        app = importlib.import_module("ptracker.ui.main")
        importlib.reload(app)
        out = app._clean(pd.DataFrame({"x": [date(2025, 1, 1), "not yet", None], "n": [1.0, 2.0, 3.0]}))
        self.assertEqual(list(out["x"]), ["2025-01-01", "not yet", ""])
        self.assertEqual(list(out["n"]), [1.0, 2.0, 3.0])


class OverlapPageTests(SmokeTests):
    def add_factsheets(self):
        import pandas as pd
        from ptracker import storage
        from ptracker.parsers.factsheet import ParsedFactsheet
        def fs(name, rows):
            h = pd.DataFrame([{"instrument": i, "sector_or_rating": s, "weight_pct": w, "section": "Equity", "as_on": None}
                              for i, s, w in rows])
            return ParsedFactsheet(info={"scheme_name": name, "scheme_key_name": name.lower().replace(" ", ""),
                                         "benchmark_name": "X", "expense_ratio_pct": 1.0}, holdings=h)
        a = fs("Demo Equity Fund Reg (G)", [("Mahindra & Mahindra Ltd.", "Auto", 50), ("Infosys Ltd", "IT", 50)])
        b = fs("Other Fund Reg (G)", [("Mahindra and Mahindra Limited", "Auto", 60), ("Zeta Ltd", "Pharma", 40)])
        storage.save_factsheets(os.path.join(self.d.name, "factsheets.xlsx"), [a, b])

    def test_overlap_tab_draws_heatmaps_and_tables(self):
        make_workspace(self.d.name)
        self.add_factsheets()
        fake = self.run_app()
        traces = [type(t).__name__ for f in self.figures(fake) for t in f.data]
        self.assertGreaterEqual(traces.count("Heatmap"), 3)                  # companies, sectors, sector weights
        self.assertIn("Waterfall", traces)                                    # the Compare tab's build-up chart
        self.assertFalse([c for c in fake.calls if c[0] == "error"])
        self.assertIn(("multiselect", "Funds to compare"), fake.calls)

    def test_overlap_tab_without_factsheets_explains_what_to_add(self):
        make_workspace(self.d.name)
        fake = self.run_app()
        self.assertIn("at least two funds", " ".join(str(c) for c in fake.calls if c[0] == "info"))