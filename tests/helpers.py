import os
import numpy as np
import pandas as pd
from tests import _path  # noqa: F401
from ptracker import storage
from ptracker.parsers.txn_statement import parse_lines

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


def make_workspace(root: str, with_index=True, with_nav=True, with_portfolio=True, benchmark="Nifty Test TRI") -> dict:
    """A complete synthetic data folder: portfolio.xlsx, indexes/, nav/."""
    ix, nv = os.path.join(root, "indexes"), os.path.join(root, "nav")
    os.makedirs(ix, exist_ok=True)
    os.makedirs(nv, exist_ok=True)
    pf = os.path.join(root, "portfolio.xlsx")
    if with_portfolio:
        storage.save_portfolio(pf, parse_lines(SAMPLE))
        from openpyxl import load_workbook
        wb = load_workbook(pf)
        ws = wb["Schemes"]
        h = [c.value for c in ws[1]]
        ws.cell(row=2, column=h.index("benchmark_name") + 1, value=benchmark)
        ws.cell(row=2, column=h.index("expense_ratio_regular_pct") + 1, value=2.0)
        ws.cell(row=2, column=h.index("expense_ratio_direct_pct") + 1, value=0.8)
        wb.save(pf)
    days = pd.bdate_range("2023-12-01", "2025-01-31")
    r = np.random.default_rng(1).normal(0.0004, 0.01, len(days))
    index = pd.Series(1000 * np.cumprod(1 + r), index=days)
    fund = pd.Series(10 * np.cumprod(1 + 1.5 * r), index=days)
    fund = fund / fund.loc["2024-02-02"] * 10.0
    if with_index:
        pd.DataFrame({"Index Name": "Nifty Test", "Date": days.strftime("%d %b %Y"),
                      "TotalReturnsIndex": index.values}).to_csv(os.path.join(ix, "t.csv"), index=False)
    if with_nav:
        pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "nav": fund.values}).to_csv(
            os.path.join(nv, "INF000A01234.csv"), index=False)
    return {"root": root, "portfolio": pf, "indexes": ix, "nav": nv}
