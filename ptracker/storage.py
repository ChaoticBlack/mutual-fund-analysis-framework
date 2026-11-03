"""Excel persistence. One workbook per portfolio, one workbook for factsheets.

Design rules
  * Re-ingesting is idempotent: transactions are de-duplicated, never duplicated.
  * Columns a person edits by hand (benchmark_file, expense ratios, notes) survive re-ingestion.
  * No address / phone / e-mail / bank / nominee data is ever written.
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import __version__
from .parsers.factsheet import ParsedFactsheet
from .parsers.txn_statement import ParsedTxnStatement
from .parsers.valuation_summary import ParsedValuation
from .util import norm_name

TXN_KEY = ["scheme_key", "date", "nature", "units", "total_amount"]
USER_COLS = ["benchmark_name", "benchmark_file", "nav_file", "expense_ratio_regular_pct",
             "expense_ratio_direct_pct", "notes"]
SCHEME_COLS = ["scheme_key", "scheme_name", "isin", "folio", "plan", "option", "category",
               "benchmark_name", "benchmark_file", "nav_file", "expense_ratio_regular_pct",
               "expense_ratio_direct_pct", "exit_load", "last_nav", "last_nav_date",
               "closing_units", "purchase_outstanding", "market_value", "notes"]


# ---------------------------------------------------------------- low level helpers
def _read(path: str, sheet: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except ValueError:  # sheet missing
        return pd.DataFrame()


def _as_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def _style(path: str) -> None:
    wb = load_workbook(path)
    head_fill = PatternFill("solid", fgColor="DDE6F0")
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), head_fill
        for i, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
    wb.save(path)


def _write(path: str, sheets: dict[str, pd.DataFrame]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame({"(empty)": []})).to_excel(xw, sheet_name=name, index=False)
    _style(path)


def _log_rows(source: str, messages: list[tuple[str, str]]) -> pd.DataFrame:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return pd.DataFrame([{"timestamp": now, "source": source, "level": lv, "message": m}
                         for lv, m in messages])


# ---------------------------------------------------------------- portfolio workbook
def load_portfolio(path: str) -> dict[str, pd.DataFrame]:
    names = ["Meta", "Schemes", "Transactions", "Snapshots", "Valuation", "Folios",
             "SIP_Summary", "Allocations", "Parse_Log"]
    data = {n: _read(path, n) for n in names}
    if not data["Transactions"].empty:
        data["Transactions"]["date"] = _as_date(data["Transactions"]["date"])
    return data


def save_portfolio(path: str, txn: ParsedTxnStatement, val: ParsedValuation | None = None,
                   include_name: bool = True, sources: dict[str, str] | None = None,
                   check_lines: list[str] | None = None) -> dict:
    old = load_portfolio(path)
    summary: dict = {}

    # --- transactions: merge + de-duplicate -----------------------------------
    new_t = txn.transactions.copy()
    before = len(old["Transactions"])
    merged = pd.concat([old["Transactions"], new_t], ignore_index=True) if before else new_t
    merged = merged.drop_duplicates(TXN_KEY, keep="first").sort_values(
        ["scheme_key", "date"], kind="stable").reset_index(drop=True)
    summary["transactions_total"], summary["transactions_added"] = len(merged), len(merged) - before

    # --- schemes: refresh numbers, preserve hand-edited columns ----------------
    closing = merged.groupby("scheme_key")["balance_units"].last()
    sch = txn.schemes.copy()
    sch["closing_units"] = sch["scheme_key"].map(closing)
    sch = sch.rename(columns={"nav": "last_nav", "nav_date": "last_nav_date"})
    cat = {}
    if val is not None and not val.holdings.empty:
        cat = {(norm_name(r.scheme_name), r.folio): r.category for r in val.holdings.itertuples()}
    sch["category"] = [cat.get((norm_name(n), f)) for n, f in zip(sch["scheme_name"], sch["folio"])]
    for c in ["benchmark_name", "benchmark_file", "nav_file", "expense_ratio_regular_pct",
              "expense_ratio_direct_pct", "exit_load", "notes"]:
        sch[c] = None
    if not old["Schemes"].empty:
        prev = old["Schemes"].set_index("scheme_key")
        prev = prev.astype({c: object for c in USER_COLS + ["exit_load", "category"] if c in prev})
        for c in USER_COLS + ["exit_load"]:
            if c in prev.columns:
                sch[c] = sch["scheme_key"].map(prev[c]).where(lambda s: s.notna(), sch[c])
        if "category" in prev.columns:  # keep earlier category if the new run had none
            sch["category"] = sch["category"].where(sch["category"].notna(),
                                                    sch["scheme_key"].map(prev["category"]))
    sch = sch[SCHEME_COLS]

    # --- valuation snapshot data (history kept, same as_on replaced) -----------
    def append_snapshot(old_df: pd.DataFrame, new_df: pd.DataFrame, as_on) -> pd.DataFrame:
        if new_df is None or new_df.empty:
            return old_df
        new_df = new_df.copy()
        new_df.insert(0, "as_on", as_on)
        if not old_df.empty and "as_on" in old_df:
            old_df = old_df[_as_date(old_df["as_on"]) != as_on]
        return pd.concat([old_df, new_df], ignore_index=True)

    snaps, valu, folios, sips, allocs = (old["Snapshots"], old["Valuation"], old["Folios"],
                                         old["SIP_Summary"], old["Allocations"])
    if val is not None:
        row = pd.DataFrame([{**{k: v for k, v in val.snapshot.items()}}])
        snaps = append_snapshot(snaps, row, val.as_on)
        valu = append_snapshot(valu, val.holdings, val.as_on)
        folios = append_snapshot(folios, val.folios, val.as_on)
        sips = append_snapshot(sips, val.sip_summary, val.as_on)
        allocs = append_snapshot(allocs, val.allocations, val.as_on)

    # --- meta + log ----------------------------------------------------------
    meta = {"investor_name": txn.investor_name if include_name else "(omitted)",
            "pan_masked": txn.pan_masked,
            "statement_period_from": txn.period_from, "statement_period_to": txn.period_to,
            "valuation_as_on": val.as_on if val else None,
            "last_ingested": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ptracker_version": __version__}
    for k, v in (sources or {}).items():
        meta[f"source_{k}"] = os.path.basename(v) if include_name else "(omitted)"
    meta_df = pd.DataFrame({"key": list(meta), "value": [str(v) if v is not None else "" for v in meta.values()]})

    msgs = [("warn", w) for w in txn.warnings] + ([("warn", w) for w in val.warnings] if val else [])
    msgs += [("info", c) for c in (check_lines or [])]
    msgs.append(("info", f"Ingested {summary['transactions_added']} new transactions "
                         f"({summary['transactions_total']} total)."))
    log = pd.concat([old["Parse_Log"], _log_rows("ingest", msgs)], ignore_index=True)

    _write(path, {"Meta": meta_df, "Schemes": sch, "Transactions": merged, "Snapshots": snaps,
                  "Valuation": valu, "Folios": folios, "SIP_Summary": sips,
                  "Allocations": allocs, "Parse_Log": log})
    return summary


# ---------------------------------------------------------------- factsheet workbook
def load_factsheets(path: str) -> dict[str, pd.DataFrame]:
    return {n: _read(path, n) for n in ["Funds", "Holdings", "TrailingReturns",
                                        "CalendarReturns", "SectorExposure"]}


def save_factsheets(path: str, parsed: list[ParsedFactsheet]) -> dict:
    data = load_factsheets(path)
    for fs in parsed:
        name, key = fs.info["scheme_name"], fs.info["scheme_key_name"]

        def put(sheet: str, df: pd.DataFrame, keep_key_col="scheme_key_name"):
            cur = data[sheet]
            if not cur.empty and keep_key_col in cur:
                cur = cur[cur[keep_key_col] != key]
            if df is None or df.empty:
                data[sheet] = cur
                return
            d = df.copy()
            d.insert(0, "scheme_name", name)
            d.insert(1, "scheme_key_name", key)
            data[sheet] = pd.concat([cur, d], ignore_index=True)

        put("Funds", pd.DataFrame([{k: v for k, v in fs.info.items()
                                    if k not in ("scheme_name", "scheme_key_name")}]))
        put("Holdings", fs.holdings)
        put("TrailingReturns", fs.trailing)
        put("CalendarReturns", fs.calendar)
        eq = fs.holdings[fs.holdings["section"] == "Equity"] if not fs.holdings.empty else fs.holdings
        sector = (eq.groupby("sector_or_rating", as_index=False)["weight_pct"].sum()
                  .sort_values("weight_pct", ascending=False)) if not eq.empty else pd.DataFrame()
        put("SectorExposure", sector)
    _write(path, data)
    return {"funds": len(data["Funds"])}


def link_factsheets(portfolio_path: str, factsheets_path: str) -> pd.DataFrame:
    """Copy benchmark / expense ratio / exit load from factsheets into the Schemes sheet.

    Existing non-empty hand-entered values are never overwritten.
    Returns a small report (scheme, matched, filled columns).
    """
    pf, fs = load_portfolio(portfolio_path), load_factsheets(factsheets_path)
    sch, funds = pf["Schemes"].copy(), fs["Funds"]
    for col in ("benchmark_name", "benchmark_file", "nav_file", "exit_load", "notes", "category"):
        if col in sch:  # all-empty columns are read back as float; make them text-capable
            sch[col] = sch[col].astype(object)
    if sch.empty or funds.empty:
        return pd.DataFrame([{"scheme_name": "-", "matched": False, "filled": "nothing to link"}])
    by_key = funds.set_index("scheme_key_name")
    report = []
    for i, r in sch.iterrows():
        key = norm_name(r["scheme_name"])
        if key not in by_key.index:
            report.append({"scheme_name": r["scheme_name"], "matched": False, "filled": ""})
            continue
        f, filled = by_key.loc[key], []
        er_col = "expense_ratio_direct_pct" if r["plan"] == "Direct" else "expense_ratio_regular_pct"
        for col, val in (("benchmark_name", f.get("benchmark_name")), (er_col, f.get("expense_ratio_pct")),
                         ("exit_load", f.get("exit_load"))):
            if pd.notna(val) and (pd.isna(sch.at[i, col]) or col == "exit_load"):
                sch.at[i, col] = val
                filled.append(col)
        if pd.isna(sch.at[i, "category"]) and pd.notna(f.get("category")):
            sch.at[i, "category"] = f["category"]
            filled.append("category")
        report.append({"scheme_name": r["scheme_name"], "matched": True, "filled": ", ".join(filled)})
    pf["Schemes"] = sch
    log = pd.concat([pf["Parse_Log"], _log_rows("link", [("info", f"{x['scheme_name']}: "
                      f"{'linked ' + x['filled'] if x['matched'] else 'no factsheet found'}") for x in report])],
                    ignore_index=True)
    pf["Parse_Log"] = log
    _write(portfolio_path, {k: v for k, v in pf.items()})
    return pd.DataFrame(report)