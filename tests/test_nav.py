import json, os, tempfile, unittest
from datetime import date
from unittest import mock
from tests import _path  # noqa: F401
import pandas as pd
from ptracker import storage
from ptracker.nav import amfi, mfapi
from ptracker.nav.nav_plan import build_nav_plan
from ptracker.nav.store import NavStore, check_against_transactions
from ptracker.parsers.txn_statement import parse_lines
from tests.test_txn_parser import SAMPLE

ISIN = "INF000A01234"            # the demo fund in SAMPLE
NAVALL = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n\n"
          "Open Ended Schemes(Equity Scheme - Thematic Fund)\n\nDemo Mutual Fund\n"
          f"900001;{ISIN};-;Demo Equity Fund - Regular Plan - Growth;11.00;23-Oct-2026\n")
HIST = ("Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;"
        "Repurchase Price;Sale Price;Date\n\nDemo Mutual Fund\n"
        f"900001;Demo Equity Fund;{ISIN};-;10.5000;;;28-Aug-2024\n"
        f"900001;Demo Equity Fund;{ISIN};-;10.6000;;;29-Aug-2024\n")


# the layout AMFI serves today: name, plan and option are separate fields (sample row from a real download)
NAVALL_NEW = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan Type;Option;Net Asset Value;Date\n\n"
              "Open Ended Schemes ( Equity Scheme - Thematic Fund )\n\nICICI Prudential Mutual Fund\n"
              "152726;INF109KC15W9;-;ICICI PRUDENTIAL ENERGY OPPORTUNITIES FUND;Regular Plan;Growth;10.53;30-Oct-2026\n"
              "152727;INF109KC17X3;INF109KC18X1;ICICI PRUDENTIAL EQUITY MINIMUM VARIANCE FUND;Regular Plan;IDCW;9.89;30-Oct-2026\n"
              "999999;-;-;SOME OLD SCHEME;Regular Plan;Growth;N.A.;30-Oct-2026\n")


class AmfiTests(unittest.TestCase):
    def test_current_eight_field_layout(self):
        d = amfi.parse_navall(NAVALL_NEW)
        self.assertEqual(len(d), 2)                                   # the N.A. row has no NAV and is dropped
        self.assertEqual(amfi.isin_to_scheme_code(d, "INF109KC15W9"), 152726)
        self.assertEqual(amfi.isin_to_scheme_code(d, "INF109KC18X1"), 152727)   # reinvestment ISIN also resolves
        self.assertEqual(d.iloc[0]["nav"], 10.53)
        self.assertIn("ENERGY OPPORTUNITIES", d.iloc[0]["scheme_name"])
        self.assertEqual(d.iloc[0]["date"], pd.Timestamp("2026-10-30"))

    def test_empty_or_html_text_gives_empty_frame_not_an_exception(self):
        self.assertTrue(amfi.parse_navall("<html>blocked</html>").empty)
        self.assertIsNone(amfi.isin_to_scheme_code(amfi.parse_navall(""), "INF109KC15W9"))


    def test_navall_and_isin_lookup(self):
        d = amfi.parse_navall(NAVALL)
        self.assertEqual(len(d), 1)
        self.assertEqual(amfi.isin_to_scheme_code(d, ISIN), 900001)
        self.assertIsNone(amfi.isin_to_scheme_code(d, "INF999Z99999"))

    def test_history_parser_skips_headings_and_blank_lines(self):
        h = amfi.parse_history(HIST)
        self.assertEqual(list(h["nav"]), [10.5, 10.6])
        self.assertEqual(h["date"].iloc[0], pd.Timestamp("2024-08-28"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        def w(n, t):
            with open(os.path.join(self.d.name, n), "w", encoding="utf-8") as fh:
                fh.write(t)
        self.w = w

    def tearDown(self):
        self.d.cleanup()

    def test_isin_in_file_name_and_overlapping_files_merge(self):
        self.w(f"{ISIN}.csv", "date,nav\n2024-08-28,10.0\n2024-08-29,10.1\n")
        self.w(f"{ISIN}_more.csv", "Date,NAV\n29-08-2024,10.1\n30-08-2024,10.2\n")
        s = NavStore(self.d.name).series_for(ISIN)
        self.assertEqual(len(s), 3)
        self.assertEqual(NavStore(self.d.name).warnings, [])

    def test_isin_column_splits_one_file_into_schemes(self):
        self.w("multi.csv", f"ISIN,Date,NAV\n{ISIN},2024-08-28,10\nINF111B02222,2024-08-28,50\n")
        st = NavStore(self.d.name)
        self.assertEqual(float(st.series_for(ISIN).iloc[0]), 10.0)
        self.assertEqual(float(st.series_for("INF111B02222").iloc[0]), 50.0)

    def test_amfi_history_text_file(self):
        self.w("amfi_download.txt", HIST)
        self.assertEqual(len(NavStore(self.d.name).series_for(ISIN)), 2)

    def test_name_hint_and_bad_files(self):
        self.w("Demo Equity Fund Reg G.csv", "Date,NAV\n2024-08-28,10\n")
        self.w("junk.csv", "a,b\n1,2\n")
        st = NavStore(self.d.name)
        self.assertIsNotNone(st.series_for(None, "Demo Equity Fund Reg (G)"))
        self.assertTrue(any("junk.csv" in e for e in st.errors))

    def test_transaction_price_check(self):
        t = parse_lines(SAMPLE).transactions
        nav = pd.Series([10.0, 10.5, 10.9], index=pd.to_datetime(["2024-08-28", "2024-09-28", "2024-10-28"]))
        r = check_against_transactions(t, nav)
        self.assertEqual(r["checked"], 3)
        self.assertEqual(r["matched"], 2)             # statement prices are 10.0, 10.5, 11.0 -> 10.9 is off
        self.assertEqual(len(r["mismatches"]), 1)
        self.assertEqual(r["mismatches"][0][2], 10.9)


class FakeResp:
    def __init__(self, payload=None, text=""):
        self._p, self.text = payload, text
    def raise_for_status(self): pass
    def json(self): return self._p


class MfApiTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("time.sleep"); p.start(); self.addCleanup(p.stop)
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.pf = os.path.join(self.d.name, "p.xlsx")
        storage.save_portfolio(self.pf, parse_lines(SAMPLE))

    def fake_get(self, calls):
        def get(url, params, timeout):
            calls.append((url, params))
            if url.endswith("NAVAll.txt"):
                return FakeResp(text=NAVALL)
            return FakeResp({"meta": {}, "data": [{"date": "29-08-2024", "nav": "10.60000"},
                                                  {"date": "28-08-2024", "nav": "10.50000"}], "status": "SUCCESS"})
        return get

    def test_fetch_uses_isin_lookup_then_scheme_code_and_saves_csv(self):
        calls, folder = [], os.path.join(self.d.name, "nav")
        plan = build_nav_plan(self.pf, NavStore(folder))
        self.assertEqual(plan.iloc[0]["status"], "missing")
        rep = mfapi.fetch_missing(plan, folder, api=mfapi.MfApi(get=self.fake_get(calls)), today=date(2026, 11, 1))
        self.assertIn("saved 2 NAVs (scheme code 900001)", rep.iloc[0]["result"])
        self.assertTrue(calls[0][0].endswith("NAVAll.txt"))
        self.assertTrue(calls[1][0].endswith("/mf/900001"))
        self.assertIn("startDate", calls[1][1])
        s = NavStore(folder).series_for(ISIN)
        self.assertEqual(list(s.values), [10.5, 10.6])                        # ascending, parsed
        self.assertEqual(build_nav_plan(self.pf, NavStore(folder)).iloc[0]["status"], "partial")   # ends 29 Aug

    def test_unreadable_navall_gives_a_diagnostic_message(self):
        api = mfapi.MfApi(get=lambda url, params, timeout: FakeResp(text="<html>nope</html>"), log=lambda *_: None)
        with self.assertRaises(LookupError) as cm:
            api.navall()
        self.assertIn("no scheme rows could be read", str(cm.exception))
        self.assertIn("portal.amfiindia.com", str(cm.exception))

    def test_falls_back_to_second_address(self):
        calls = []
        def get(url, params, timeout):
            calls.append(url)
            if "portal" in url:
                raise ConnectionError("blocked")
            return FakeResp(text=NAVALL_NEW)
        api = mfapi.MfApi(get=get, max_retries=0, log=lambda *_: None)
        self.assertEqual(len(api.navall()), 2)
        self.assertEqual(len(calls), 2)

    def test_unknown_isin_and_dry_run(self):
        plan = pd.DataFrame([{"scheme_name": "X", "isin": "INF999Z99999", "need_from": date(2024, 8, 28),
                              "need_to": date(2024, 9, 30), "have_from": None, "have_to": None, "status": "missing"}])
        rep = mfapi.fetch_missing(plan, self.d.name, api=mfapi.MfApi(get=self.fake_get([]), log=lambda *_: None))
        self.assertIn("ISIN not found", rep.iloc[0]["result"])
        def never(*a): raise AssertionError("network used")
        rep = mfapi.fetch_missing(plan, self.d.name, api=mfapi.MfApi(get=never), dry_run=True)
        self.assertIn("dry run", rep.iloc[0]["result"])

    def test_failures_are_retried_then_raised(self):
        def boom(*a): raise ConnectionError("blocked")
        with self.assertRaises(RuntimeError):
            mfapi.MfApi(get=boom, max_retries=1, log=lambda *_: None).history(900001)


if __name__ == "__main__":
    unittest.main()