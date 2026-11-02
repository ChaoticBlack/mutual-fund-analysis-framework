"""Estimate what the regular-plan expense ratio costs, and import expense ratios from a spreadsheet."""
from __future__ import annotations

import difflib
import re
from datetime import date

import pandas as pd

from .. import storage
from ..util import norm_name

_NOISE = re.compile(r"(regular|reg|direct|growth|plan|option|idcw|g)$")


def _core(name: str) -> str:
    n = norm_name(name)
    n = re.sub(r"(regplan|regulargrowth|regg|regularplan|reg)$", "", n)
    n = n.replace("assets", "asset")
    return re.sub(r"(growth|g)$", "", n)


def estimate(schemes: pd.DataFrame, timelines: dict[str, pd.DataFrame], flows_by_scheme: dict[str, list],
             as_of: date) -> pd.DataFrame:
    """Rough commission estimate = time-integral of fund value x (regular - direct expense ratio).

    The integral uses the value on each transaction date (linear in between), so it improves
    once daily NAV history is available. It assumes the distributor receives the whole gap.
    """
    rows = []
    for r in schemes.itertuples():
        tl = timelines.get(r.scheme_key)
        reg, dr = r.expense_ratio_regular_pct, r.expense_ratio_direct_pct
        row = {"scheme_name": r.scheme_name, "plan": r.plan, "er_regular_pct": reg, "er_direct_pct": dr}
        if tl is None or tl.empty or pd.isna(reg) or pd.isna(dr):
            row.update({"drag_pct": None, "est_commission_to_date": None, "est_next_12m": None,
                        "note": "needs regular AND direct expense ratios (Schemes sheet or `costs import`)"})
            rows.append(row)
            continue
        drag = (float(reg) - float(dr)) if r.plan != "Direct" else 0.0
        t = pd.to_datetime(tl["date"])
        years = t.diff().dt.days.fillna(0).values / 365.0
        v = tl["fund_value"].values
        integral = float(sum((v[i] + v[i - 1]) / 2 * years[i] for i in range(1, len(v))))
        recent = [-a for d, a in flows_by_scheme.get(r.scheme_key, []) if a < 0 and (as_of - d).days <= 183]
        monthly_sip = sum(recent) / 6.0
        nxt = (float(v[-1]) + 6 * monthly_sip) * drag / 100.0   # average balance if markets stay flat
        row.update({"drag_pct": round(drag, 2), "est_commission_to_date": round(integral * drag / 100.0, 0),
                    "est_next_12m": round(nxt, 0), "note": ""})
        rows.append(row)
    return pd.DataFrame(rows)


def import_costs(portfolio_path: str, file_path: str, overwrite: bool = False) -> pd.DataFrame:
    """Read a sheet with 'Fund Name', 'Regular Expense Ratio', 'Direct Expense Ratio' columns
    (extra columns and extra header rows are ignored) and fill the Schemes sheet."""
    raw = pd.read_excel(file_path, header=None) if file_path.lower().endswith(("xlsx", "xls", "xlsm")) \
        else pd.read_csv(file_path, header=None)
    hdr = next((i for i in range(min(len(raw), 15))
                if {"fundname", "regularexpenseratio", "directexpenseratio"} <=
                {re.sub(r"[^a-z]", "", str(c).lower()) for c in raw.iloc[i]}), None)
    if hdr is None:
        raise ValueError("Could not find a header row containing Fund Name, Regular Expense Ratio and "
                         "Direct Expense Ratio.")
    names = [re.sub(r"[^a-z]", "", str(c).lower()) for c in raw.iloc[hdr]]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = names
    df = df[["fundname", "regularexpenseratio", "directexpenseratio"]].dropna(subset=["fundname"])
    df = df[pd.to_numeric(df["regularexpenseratio"], errors="coerce").notna()]

    pf = storage.load_portfolio(portfolio_path)
    sch = pf["Schemes"].copy()
    for c in ("expense_ratio_regular_pct", "expense_ratio_direct_pct"):
        sch[c] = pd.to_numeric(sch[c], errors="coerce")
    report = []
    for i, r in sch.iterrows():
        target = _core(r["scheme_name"])
        best, score = None, 0.0
        for row in df.itertuples():
            s = difflib.SequenceMatcher(None, target, _core(str(row.fundname))).ratio()
            if s > score:
                best, score = row, s
        if best is None or score < 0.85:
            report.append({"scheme_name": r["scheme_name"], "matched": "", "result": "no confident match"})
            continue
        reg, dr = float(best.regularexpenseratio), float(best.directexpenseratio)
        msgs = []
        for col, val in (("expense_ratio_regular_pct", reg), ("expense_ratio_direct_pct", dr)):
            cur = sch.at[i, col]
            if pd.isna(cur) or overwrite:
                sch.at[i, col] = val
                msgs.append(f"{col}={val}")
            elif abs(cur - val) > 1e-9:
                msgs.append(f"KEPT existing {col}={cur} (sheet says {val}; use --overwrite to replace)")
        report.append({"scheme_name": r["scheme_name"], "matched": str(best.fundname),
                       "result": "; ".join(msgs) or "already up to date"})
    pf["Schemes"] = sch
    storage._write(portfolio_path, {k: v for k, v in pf.items()})
    return pd.DataFrame(report)
