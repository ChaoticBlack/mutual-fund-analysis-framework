import os, tempfile, unittest
from datetime import date
from tests import _path  # noqa: F401
import pandas as pd
from ptracker import storage
from ptracker.analytics.xirr import xirr
from ptracker.analytics.replicate import replicate
from ptracker.analytics.engine import run_analysis, investor_flows
from ptracker.analytics import costs
from ptracker.parsers.txn_statement import parse_lines


class XirrTests(unittest.TestCase):
    def test_one_year_ten_percent(self):
        r = xirr([(date(2024, 1, 1), -100.0), (date(2025, 1, 1), 110.0)])   # 366 days -> slightly < 10%
        self.assertAlmostEqual(r, 0.10, delta=0.002)

    def test_exactly_365_days(self):
        self.assertAlmostEqual(xirr([(date(2023, 1, 1), -100.0), (date(2024, 1, 1), 110.0)]), 0.10, places=6)

    def test_no_answer_when_flows_are_one_sided_or_single_date(self):
        self.assertIsNone(xirr([(date(2024, 1, 1), -100.0), (date(2024, 6, 1), -50.0)]))
        self.assertIsNone(xirr([(date(2024, 1, 1), -100.0), (date(2024, 1, 1), 110.0)]))
        self.assertIsNone(xirr([]))

    def test_negative_return(self):
        self.assertAlmostEqual(xirr([(date(2023, 1, 1), -100.0), (date(2024, 1, 1), 90.0)]), -0.10, places=6)


def index_series(start, end, daily=0.0004):
    days = pd.bdate_range(start, end)
    vals = [1000.0 * (1 + daily) ** i for i in range(len(days))]
    return pd.Series(vals, index=days)


class ReplicationTests(unittest.TestCase):
    def test_constant_growth_index_gives_its_own_rate(self):
        s = index_series("2024-01-01", "2026-01-31", 0.0004)
        flows = [(date(2024, 1, 1), -1000.0), (date(2025, 1, 1), -1000.0)]
        r = replicate(flows, s, date(2026, 1, 30))
        self.assertIsNotNone(r.xirr)
        self.assertGreater(r.terminal_value, 2000)

    def test_buy_then_sell_everything_returns_index_move(self):
        s = index_series("2024-01-01", "2025-12-31", 0.001)
        buy, sell = date(2024, 1, 2), date(2025, 1, 2)
        lvl = lambda d: float(s[s.index >= pd.Timestamp(d)].iloc[0])
        proceeds = 1000.0 * lvl(sell) / lvl(buy)
        r = replicate([(buy, -1000.0), (sell, proceeds)], s, date(2025, 6, 2))
        self.assertAlmostEqual(r.units, 0.0, places=9)
        self.assertAlmostEqual(r.terminal_value, 0.0, places=6)

    def test_holiday_uses_next_trading_day_and_gaps_are_reported(self):
        s = index_series("2024-01-01", "2024-03-01")
        self.assertIsNotNone(replicate([(date(2024, 1, 6), -100.0)], s, date(2024, 2, 20)))     # Saturday
        bad = replicate([(date(2024, 1, 1), -100.0)], s, date(2024, 6, 1))
        self.assertTrue(bad.issues)                                                          # data ends early
        self.assertIsNone(bad.xirr)

    def test_oversell_is_flagged(self):
        s = index_series("2024-01-01", "2024-12-31")
        r = replicate([(date(2024, 1, 2), -100.0), (date(2024, 3, 1), 500.0)], s, date(2024, 6, 1))
        self.assertTrue(any("more units" in i for i in r.issues))


SAMPLE = """
Transaction Statement
From 01/01/2024 to 26/10/2026
Demo Equity Fund Reg (G) INF000A01234 [111/22]
01/01/2024 Opening Cost and Unit Balance 0.00 0.0000
02/02/2024 SIP 19,999.00 10.0000 1,999.9000 1,999.9000 1.00 0.00 0.00 20,000.00
04/03/2024 SIP 19,999.00 10.0000 1,999.9000 3,999.8000 1.00 0.00 0.00 20,000.00
NAV : 11.0000 as on 10/05/2024 Purchase Outstanding : 40,000.00 Market Value : 43,998.00
Grand Total : Purchase Outstanding : 40,000.00 Market Value : 43,998.00
""".strip().splitlines()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.idx = os.path.join(self.d.name, "idx"); os.makedirs(self.idx)
        self.pf = os.path.join(self.d.name, "p.xlsx")
        storage.save_portfolio(self.pf, parse_lines(SAMPLE))
        from openpyxl import load_workbook
        wb = load_workbook(self.pf); ws = wb["Schemes"]; h = [c.value for c in ws[1]]
        ws.cell(row=2, column=h.index("benchmark_name") + 1, value="Nifty Test TRI"); wb.save(self.pf)
        s = index_series("2024-01-01", "2024-06-30", 0.0005)
        pd.DataFrame({"Index Name": "Nifty Test", "Date": s.index.strftime("%d %b %Y"),
                      "TotalReturnsIndex": s.values}).to_csv(os.path.join(self.idx, "test_tri.csv"), index=False)

    def tearDown(self):
        self.d.cleanup()

    def test_fund_xirr_matches_direct_calculation(self):
        res = run_analysis(self.pf, self.idx, comparators=())
        flows = [(date(2024, 2, 2), -20000.0), (date(2024, 3, 4), -20000.0), (date(2024, 5, 10), 43998.0)]
        self.assertAlmostEqual(res.schemes.iloc[0]["fund_xirr_pct"], round(xirr(flows) * 100, 2), places=2)

    def test_gap_equals_value_difference_and_benchmark_is_used(self):
        res = run_analysis(self.pf, self.idx, comparators=())
        r = res.schemes.iloc[0]
        self.assertEqual(r["benchmark_basis"], "TRI")
        self.assertAlmostEqual(r["gap_rs"], round(r["current_value"] - r["bench_value"], 0), delta=1)
        self.assertEqual(len(res.timelines), 1)
        self.assertAlmostEqual(res.timelines["Demo Equity Fund Reg (G)"].iloc[-1]["fund_value"], 43998.0, places=1)

    def test_missing_benchmark_data_never_produces_a_number(self):
        for f in os.listdir(self.idx):
            os.remove(os.path.join(self.idx, f))
        res = run_analysis(self.pf, self.idx, comparators=())
        self.assertIsNone(res.schemes.iloc[0]["bench_xirr_pct"] if pd.notna(res.schemes.iloc[0]["bench_xirr_pct"]) else None)
        self.assertEqual(res.schemes.iloc[0]["benchmark_basis"], "unavailable")

    def test_price_series_only_used_with_explicit_fallback(self):
        for f in os.listdir(self.idx):
            os.remove(os.path.join(self.idx, f))
        s = index_series("2024-01-01", "2024-06-30")
        pd.DataFrame({"Date": s.index.strftime("%d-%b-%Y").str.upper(), "Close": s.values}).to_csv(
            os.path.join(self.idx, "NIFTY_TEST-01-01-2024-to-30-06-2024.csv"), index=False)
        strict = run_analysis(self.pf, self.idx, comparators=())
        self.assertEqual(strict.schemes.iloc[0]["benchmark_basis"], "unavailable")
        loose = run_analysis(self.pf, self.idx, comparators=(), allow_price_fallback=True)
        self.assertTrue(loose.schemes.iloc[0]["benchmark_basis"].startswith("PRICE"))

    def test_investor_flow_signs(self):
        t = parse_lines(SAMPLE).transactions
        fl = investor_flows(t)
        self.assertTrue(all(a < 0 for _, a in fl))


class CostTests(unittest.TestCase):
    def test_import_handles_two_row_header_and_name_spelling_differences(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "p.xlsx")
            storage.save_portfolio(pf, parse_lines(SAMPLE.copy() if hasattr(SAMPLE, "copy") else SAMPLE))
            from openpyxl import Workbook
            wb = Workbook(); ws = wb.active
            ws.append(["", "No Expense Ratio", "", "Regular Expense Ratio"])
            ws.append(["Fund Name", "Regular Expense Ratio", "Direct Expense Ratio", "", "Duration (years)"])
            ws.append(["Demo Equity Fund", 1.9, 0.7, "", 10])
            ws.append(["Some Other Fund", 2.0, 0.5, "", 10])
            sheet = os.path.join(d, "costs.xlsx"); wb.save(sheet)
            rep = costs.import_costs(pf, sheet)
            self.assertIn("expense_ratio_regular_pct=1.9", rep.iloc[0]["result"])
            sch = storage.load_portfolio(pf)["Schemes"]
            self.assertEqual(float(sch.iloc[0]["expense_ratio_direct_pct"]), 0.7)
            rep2 = costs.import_costs(pf, sheet)
            self.assertEqual(rep2.iloc[0]["result"], "already up to date")

    def test_estimate_scales_with_value_and_time(self):
        sch = pd.DataFrame([{"scheme_key": "a", "scheme_name": "A", "plan": "Regular",
                             "expense_ratio_regular_pct": 2.0, "expense_ratio_direct_pct": 1.0}])
        tl = pd.DataFrame({"date": [date(2024, 1, 1), date(2025, 1, 1)], "fund_value": [100000.0, 100000.0]})
        out = costs.estimate(sch, {"a": tl}, {"a": []}, date(2025, 1, 1))
        self.assertAlmostEqual(out.iloc[0]["est_commission_to_date"], 1000.0 * 366 / 365, delta=2)


if __name__ == "__main__":
    unittest.main()
