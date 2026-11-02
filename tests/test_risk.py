import os, tempfile, unittest
from datetime import date
from tests import _path  # noqa: F401
import numpy as np
import pandas as pd
from ptracker import storage
from ptracker.analytics import risk
from ptracker.analytics.risk import cagr, max_drawdown, risk_metrics, rolling_returns, calendar_year_returns, run_risk
from ptracker.parsers.txn_statement import parse_lines


def idx(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


class PureFunctionTests(unittest.TestCase):
    def test_max_drawdown_known_series(self):
        s = pd.Series([100, 120, 90, 110, 130.0], index=idx(5))
        d = max_drawdown(s)
        self.assertAlmostEqual(d["max_dd"], -0.25)
        self.assertEqual((d["peak"], d["trough"]), (s.index[1].date(), s.index[2].date()))
        self.assertEqual(d["recovered"], s.index[4].date())
        self.assertAlmostEqual(d["current_dd"], 0.0)

    def test_unrecovered_drawdown(self):
        s = pd.Series([100, 120, 90, 100.0], index=idx(4))
        d = max_drawdown(s)
        self.assertIsNone(d["recovered"])
        self.assertAlmostEqual(d["current_dd"], 100 / 120 - 1)

    def test_cagr(self):
        s = pd.Series([100.0, 110.0], index=pd.to_datetime(["2023-01-01", "2024-01-01"]))
        self.assertAlmostEqual(cagr(s), 0.10, places=6)

    def test_beta_two_and_perfect_correlation_and_capture(self):
        rng = np.random.default_rng(0)
        rb = rng.normal(0.0004, 0.01, 400)
        bench = pd.Series(1000 * np.cumprod(1 + rb), index=idx(400))
        fund = pd.Series(10 * np.cumprod(1 + 2 * rb), index=idx(400))          # exactly 2x the benchmark's daily move
        m = risk_metrics(fund, bench, rf=0.0)
        self.assertAlmostEqual(m["beta"], 2.0, places=6)
        self.assertAlmostEqual(m["corr"], 1.0, places=6)
        self.assertAlmostEqual(m["up_capture"], 200.0, places=4)
        self.assertAlmostEqual(m["down_capture"], 200.0, places=4)
        self.assertAlmostEqual(m["vol"] / m["bench_vol"], 2.0, places=4)
        self.assertAlmostEqual(m["alpha"], 0.0, places=6)

    def test_ratios_withheld_when_history_is_too_short(self):
        s = pd.Series(np.linspace(10, 11, 30), index=idx(30))
        m = risk_metrics(s, s)
        self.assertNotIn("vol", m)
        self.assertNotIn("beta", m)

    def test_sharpe_zero_vol_does_not_crash(self):
        s = pd.Series(10 * (1.0002 ** np.arange(200)), index=idx(200))
        self.assertIsNotNone(risk_metrics(s, rf=0.0).get("vol"))

    def test_rolling_returns_use_calendar_months(self):
        days = pd.date_range("2024-01-01", "2025-06-30", freq="D")
        s = pd.Series(100 * (1.001 ** np.arange(len(days))), index=days)
        r = rolling_returns(s, 12)
        self.assertEqual(r.index[0], pd.Timestamp("2025-01-01"))
        self.assertAlmostEqual(r.iloc[0], 1.001 ** 366 - 1, places=9)       # 2024 is a leap year

    def test_calendar_years(self):
        s = pd.Series([100.0, 110.0, 99.0, 121.0], index=pd.to_datetime(["2024-03-01", "2024-12-31", "2025-06-30", "2025-12-31"]))
        c = calendar_year_returns(s)
        self.assertAlmostEqual(c.iloc[0]["return"], 0.10)
        self.assertEqual(c.iloc[0]["basis"], "from start")
        self.assertAlmostEqual(c.iloc[1]["return"], 121 / 110 - 1)


SAMPLE = """
Transaction Statement
From 01/01/2024 to 26/10/2026
Demo Equity Fund Reg (G) INF000A01234 [111/22]
01/01/2024 Opening Cost and Unit Balance 0.00 0.0000
02/02/2024 SIP 19,999.00 10.0000 1,999.9000 1,999.9000 1.00 0.00 0.00 20,000.00
04/03/2024 SIP 19,999.00 10.0000 1,999.9000 3,999.8000 1.00 0.00 0.00 20,000.00
NAV : 11.0000 as on 29/12/2024 Purchase Outstanding : 40,000.00 Market Value : 43,998.00
Grand Total : Purchase Outstanding : 40,000.00 Market Value : 43,998.00
""".strip().splitlines()


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.ix, self.nv = (os.path.join(self.d.name, n) for n in ("idx", "nav"))
        os.makedirs(self.ix); os.makedirs(self.nv)
        self.pf = os.path.join(self.d.name, "p.xlsx")
        storage.save_portfolio(self.pf, parse_lines(SAMPLE))
        from openpyxl import load_workbook
        wb = load_workbook(self.pf); ws = wb["Schemes"]; h = [c.value for c in ws[1]]
        ws.cell(row=2, column=h.index("benchmark_name") + 1, value="Nifty Test TRI"); wb.save(self.pf)
        days = pd.bdate_range("2023-12-01", "2025-01-31")
        rng = np.random.default_rng(1)
        r = rng.normal(0.0004, 0.01, len(days))
        index = pd.Series(1000 * np.cumprod(1 + r), index=days)
        fund = pd.Series(10 * np.cumprod(1 + 1.5 * r), index=days)
        # make the NAV on the two SIP dates and the valuation date equal the statement's prices
        self.fund = fund / fund.loc["2024-02-02"] * 10.0
        pd.DataFrame({"Index Name": "Nifty Test", "Date": days.strftime("%d %b %Y"),
                      "TotalReturnsIndex": index.values}).to_csv(os.path.join(self.ix, "t.csv"), index=False)
        pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "nav": self.fund.values}).to_csv(
            os.path.join(self.nv, "INF000A01234.csv"), index=False)
        self.index = index

    def test_pipeline_runs_and_values_reconcile(self):
        res = run_risk(self.pf, self.ix, self.nv, comparators=(), risk_free_pct=0.0)
        row = res.risk.iloc[0]
        self.assertEqual(row["benchmark_basis"], "TRI")
        self.assertAlmostEqual(row["beta"], 1.5, places=1)
        d = res.daily
        # daily fund value = units x NAV; before the 2nd SIP only the first SIP's units are held
        first = d[d["date"] == pd.Timestamp("2024-02-02")].iloc[0]
        self.assertAlmostEqual(first["fund_value"], 1999.9 * 10.0, places=2)
        self.assertAlmostEqual(first["net_invested"], 20000.0)
        last = d.iloc[-1]
        self.assertAlmostEqual(last["fund_value"], 3999.8 * self.fund.loc[last["date"]], places=2)

    def test_single_fund_portfolio_twr_equals_the_fund_return(self):
        res = run_risk(self.pf, self.ix, self.nv, comparators=(), risk_free_pct=0.0)
        pdly = res.portfolio_daily
        a, b = pd.Timestamp("2024-02-05"), pd.Timestamp("2024-12-27")
        ratio_twr = pdly.set_index("date").loc[b, "twr_index"] / pdly.set_index("date").loc[a, "twr_index"]
        self.assertAlmostEqual(ratio_twr, self.fund.loc[b] / self.fund.loc[a], places=9)

    def test_missing_nav_is_reported_not_guessed(self):
        os.remove(os.path.join(self.nv, "INF000A01234.csv"))
        res = run_risk(self.pf, self.ix, self.nv, comparators=())
        self.assertTrue(res.risk.empty)
        self.assertTrue(any("no NAV history" in w for w in res.warnings))


if __name__ == "__main__":
    unittest.main()
