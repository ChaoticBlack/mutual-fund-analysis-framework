"""Everything the app does that is not drawing: paths, uploads, loading and caching inputs, number formatting."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field

import pandas as pd

from .. import storage, validation
from ..analytics.engine import AnalysisResult, run_analysis, to_excel as analysis_to_excel
from ..analytics.risk import RiskResult, run_risk, to_excel as risk_to_excel
from ..indexes.files import IndexStore
from ..indexes.plan import DEFAULT_COMPARATORS, build_plan
from ..nav.nav_plan import build_nav_plan
from ..nav.store import NavStore
from ..parsers import factsheet as fs_parser
from ..parsers import txn_statement, valuation_summary

ALLOWED_UPLOADS = (".csv", ".xlsx", ".xls", ".txt")


# ------------------------------------------------------------------ formatting
def inr(x, decimals: int = 0) -> str:
    """Indian digit grouping: 2120018 -> '₹21,20,018'; negatives as '-₹19,982'."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "-"
    neg = x < 0
    s = f"{abs(round(float(x), decimals)):.{decimals}f}"
    whole, dot, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = head + "," + tail
    return ("-" if neg else "") + "\u20b9" + whole + (dot + frac if decimals else "")


def pct(x, decimals: int = 2, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "-"
    return f"{x:+.{decimals}f}%" if signed else f"{x:.{decimals}f}%"


def pts(x, decimals: int = 2) -> str:
    return "-" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:+.{decimals}f} pts"


# ------------------------------------------------------------------ paths and signatures
@dataclass
class DataPaths:
    root: str
    portfolio: str
    factsheets: str
    indexes: str
    nav: str


def make_paths(root: str, index_dir: str | None = None, nav_dir: str | None = None) -> DataPaths:
    root = os.path.abspath(root)
    return DataPaths(root, os.path.join(root, "portfolio.xlsx"), os.path.join(root, "factsheets.xlsx"),
                     os.path.abspath(index_dir) if index_dir else os.path.join(root, "indexes"),
                     os.path.abspath(nav_dir) if nav_dir else os.path.join(root, "nav"))


def dir_signature(path: str) -> tuple:
    """Changes whenever a file in the folder (or the file itself) is added, removed or modified."""
    if os.path.isfile(path):
        st = os.stat(path)
        return ((os.path.basename(path), st.st_size, int(st.st_mtime)),)
    if not os.path.isdir(path):
        return ()
    out = []
    for fn in sorted(os.listdir(path)):
        fp = os.path.join(path, fn)
        if os.path.isfile(fp):
            st = os.stat(fp)
            out.append((fn, st.st_size, int(st.st_mtime)))
    return tuple(out)


# ------------------------------------------------------------------ the loaded data
@dataclass
class Bundle:
    paths: DataPaths
    has_portfolio: bool = False
    sheets: dict = field(default_factory=dict)
    factsheets: dict = field(default_factory=dict)
    index_plan: pd.DataFrame = field(default_factory=pd.DataFrame)
    index_mapping: pd.DataFrame = field(default_factory=pd.DataFrame)
    nav_plan: pd.DataFrame = field(default_factory=pd.DataFrame)
    analysis: AnalysisResult | None = None
    risk: RiskResult | None = None
    errors: list = field(default_factory=list)

    @property
    def has_nav(self) -> bool:
        return self.risk is not None and not self.risk.risk.empty


def load_bundle(paths: DataPaths, comparators=DEFAULT_COMPARATORS, risk_free_pct: float = 6.0,
                allow_price_fallback: bool = False) -> Bundle:
    b = Bundle(paths)
    if not os.path.exists(paths.portfolio):
        return b
    b.sheets, b.has_portfolio = storage.load_portfolio(paths.portfolio), True
    if os.path.exists(paths.factsheets):
        b.factsheets = storage.load_factsheets(paths.factsheets)
    try:
        b.index_plan, b.index_mapping = build_plan(paths.portfolio, IndexStore(paths.indexes), comparators)
    except Exception as e:
        b.errors.append(f"Index plan: {e}")
    try:
        b.nav_plan = build_nav_plan(paths.portfolio, NavStore(paths.nav))
    except Exception as e:
        b.errors.append(f"NAV plan: {e}")
    try:
        b.analysis = run_analysis(paths.portfolio, paths.indexes, comparators, allow_price_fallback)
    except Exception as e:
        b.errors.append(f"Performance analysis: {e}")
    try:
        b.risk = run_risk(paths.portfolio, paths.indexes, paths.nav, comparators, allow_price_fallback, risk_free_pct)
    except Exception as e:
        b.errors.append(f"Risk analysis: {e}")
    return b


# ------------------------------------------------------------------ uploads
def save_uploads(folder: str, files: list[tuple[str, bytes]]) -> list[str]:
    """Store uploaded index / NAV files. Only plain data files; the path part of any name is discarded."""
    os.makedirs(folder, exist_ok=True)
    msgs = []
    for name, data in files:
        base = os.path.basename(name.replace("\\", "/"))
        if not base or not base.lower().endswith(ALLOWED_UPLOADS):
            msgs.append(f"Skipped {name!r}: only {', '.join(ALLOWED_UPLOADS)} files are accepted.")
            continue
        with open(os.path.join(folder, base), "wb") as fh:
            fh.write(data)
        msgs.append(f"Saved {base} ({len(data):,} bytes).")
    return msgs


def ingest_pdfs(paths: DataPaths, txn: tuple[str, bytes], valuation: tuple[str, bytes] | None = None,
                factsheets: list[tuple[str, bytes]] | None = None, password: str | None = None,
                include_name: bool = False) -> tuple[bool, list[str]]:
    """PDF bytes -> validated workbooks. Nothing is written if a validation check fails."""
    msgs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:         # PDFs (which contain personal data) never persist
        def dump(name, data):
            p = os.path.join(tmp, os.path.basename(name.replace("\\", "/")) or "file.pdf")
            with open(p, "wb") as fh:
                fh.write(data)
            return p
        try:
            t = txn_statement.parse_pdf(dump(*txn), password or None)
            v = valuation_summary.parse_pdf(dump(*valuation), password or None) if valuation else None
        except Exception as e:
            return False, [f"Could not read the statement PDF: {e}"]
        checks = validation.validate_txn(t) + (validation.validate_valuation_vs_txn(t, v) if v else [])
        passed, failed, warned = validation.summarize(checks)
        msgs.append(f"Validation: {passed} passed, {failed} failed, {warned} warnings.")
        msgs += [str(c) for c in checks if not c.ok]
        if failed:
            msgs.append("Nothing was saved because validation failed.")
            return False, msgs
        res = storage.save_portfolio(paths.portfolio, t, v, include_name=include_name,
                                     sources={"transactions": txn[0], **({"valuation": valuation[0]} if valuation else {})},
                                     check_lines=[str(c) for c in checks if not c.ok])
        msgs.append(f"Saved {paths.portfolio}: {res['transactions_added']} new transactions "
                    f"({res['transactions_total']} total).")
        parsed = []
        for name, data in factsheets or []:
            try:
                f = fs_parser.parse_pdf(dump(name, data), password or None)
                parsed.append(f)
                msgs.append(f"Factsheet read: {f.info.get('scheme_name')} - benchmark {f.info.get('benchmark_name')}, "
                            f"expense ratio {f.info.get('expense_ratio_pct')}.")
                msgs += [f"  ! {w}" for w in f.warnings]
            except Exception as e:
                msgs.append(f"Could not read factsheet {name}: {e}")
        if parsed:
            storage.save_factsheets(paths.factsheets, parsed)
            link = storage.link_factsheets(paths.portfolio, paths.factsheets)
            matched = int(link["matched"].sum())
            msgs.append(f"Linked {matched} of {len(link)} funds to their factsheets.")
    return True, msgs


def excel_bytes(kind: str, result) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, f"{kind}.xlsx")
        (analysis_to_excel if kind == "analysis" else risk_to_excel)(result, p)
        with open(p, "rb") as fh:
            return fh.read()


# ------------------------------------------------------------------ small data helpers for the pages
def latest_allocations(sheets: dict) -> pd.DataFrame:
    a = sheets.get("Allocations", pd.DataFrame())
    if a.empty or "as_on" not in a:
        return pd.DataFrame()
    a = a.copy()
    a["as_on"] = pd.to_datetime(a["as_on"], errors="coerce")
    return a[a["as_on"] == a["as_on"].max()]


def fund_daily(risk: RiskResult | None, scope: str) -> pd.DataFrame:
    if risk is None or risk.daily.empty:
        return pd.DataFrame()
    d = risk.daily[risk.daily["scheme_name"] == scope].copy()
    d["date"] = pd.to_datetime(d["date"])
    return d


def bench_columns(df: pd.DataFrame, own_label: str = "Own benchmark") -> dict[str, str]:
    """{legend label: column} for the benchmark-equivalent value columns present in a daily frame."""
    out = {}
    for c in df.columns:
        if c == "bench_own":
            out[own_label] = c
        elif c.startswith("bench_"):
            out[c[6:]] = c
    return out


def fund_values(schemes: pd.DataFrame | None, fund_names: list[str]) -> dict[str, float]:
    """Current rupee value of each factsheet fund, matched to the portfolio's schemes by normalised name."""
    from ..util import norm_name
    if schemes is None or schemes.empty:
        return {}
    by = {norm_name(n): float(v) for n, v in zip(schemes["scheme_name"], schemes["current_value"])}
    return {f: by[norm_name(f)] for f in fund_names if norm_name(f) in by}


def classify_workbook(data: bytes) -> str | None:
    """'portfolio', 'factsheets' or None, judged from the sheet names of a workbook made by this tool."""
    import io
    from openpyxl import load_workbook
    try:
        names = set(load_workbook(io.BytesIO(data), read_only=True).sheetnames)
    except Exception:
        return None
    if {"Schemes", "Transactions"} <= names:
        return "portfolio"
    if {"Funds", "Holdings"} <= names:
        return "factsheets"
    return None


def save_workbooks(paths: DataPaths, files: list[tuple[str, bytes]]) -> list[str]:
    """Use workbooks created earlier (portfolio.xlsx / factsheets.xlsx) without the original PDFs.
    Whatever is replaced is first kept as <name>.previous.xlsx."""
    import shutil
    os.makedirs(paths.root, exist_ok=True)
    msgs = []
    for name, data in files:
        kind = classify_workbook(data)
        if kind is None:
            msgs.append(f"Skipped {name!r}: not a portfolio or factsheets workbook made by this tool.")
            continue
        target = paths.portfolio if kind == "portfolio" else paths.factsheets
        if os.path.exists(target):
            shutil.copyfile(target, target.replace(".xlsx", ".previous.xlsx"))
        with open(target, "wb") as fh:
            fh.write(data)
        msgs.append(f"Saved {name!r} as the {kind} workbook ({os.path.basename(target)}).")
    return msgs