"""Which schemes in a portfolio still need NAV history?"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from .. import storage
from .store import NavStore, check_against_transactions

SLACK_DAYS = 3        # weekend / one holiday; a week-old file is NOT current


def build_nav_plan(portfolio_path: str, store: NavStore) -> pd.DataFrame:
    pf = storage.load_portfolio(portfolio_path)
    sch, txn = pf["Schemes"], pf["Transactions"]
    if sch.empty or txn.empty:
        raise ValueError("Portfolio workbook has no data. Run `ingest` first.")
    first = txn.groupby("scheme_key")["date"].min()
    rows = []
    has_override = "nav_file" in sch.columns
    for s in sch.itertuples():
        override = getattr(s, "nav_file", None) if has_override else None
        override = override if isinstance(override, str) and override.strip() else None
        ser = store.series_for(s.isin, s.scheme_name, override)
        need_from = first[s.scheme_key]
        need_to = pd.to_datetime(s.last_nav_date).date()
        row = {"scheme_name": s.scheme_name, "isin": s.isin, "need_from": need_from, "need_to": need_to,
               "have_from": None, "have_to": None, "status": "missing", "action": ""}
        if ser is not None and len(ser):
            row["have_from"], row["have_to"] = ser.index.min().date(), ser.index.max().date()
            notes = []
            if row["have_from"] > need_from + timedelta(days=SLACK_DAYS):
                notes.append(f"starts {row['have_from']}, need from {need_from}")
            if row["have_to"] < need_to - timedelta(days=SLACK_DAYS):
                notes.append(f"ends {row['have_to']}, need to {need_to}")
            gaps = ser[(ser.index >= pd.Timestamp(need_from)) & (ser.index <= pd.Timestamp(need_to))].index.to_series().diff().dt.days.max()
            if pd.notna(gaps) and gaps > 10:
                notes.append(f"gap of {int(gaps)} days inside the period")
            row["status"] = "ready" if not notes else "partial"
            row["action"] = "-" if not notes else "; ".join(notes)
            chk = check_against_transactions(txn[txn["scheme_key"] == s.scheme_key], ser)
            if chk["checked"]:
                row["txn_price_match"] = f"{chk['matched']}/{chk['checked']}"
        else:
            row["action"] = (f"Download NAV history for ISIN {s.isin} from {need_from} to {need_to} "
                             f"(AMFI NAV History page, or the fund house site) into the NAV folder, "
                             f"or run `python -m ptracker nav fetch`.")
        rows.append(row)
    return pd.DataFrame(rows)