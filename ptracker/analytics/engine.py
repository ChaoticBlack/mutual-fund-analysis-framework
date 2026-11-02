"""Performance analytics for one portfolio workbook against benchmark index data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import storage
from ..indexes.files import IndexStore
from ..indexes.plan import DEFAULT_COMPARATORS
from . import costs as costs_mod
from .replicate import Replication, level_on_or_before, replicate
from .resolve import BenchRef, resolve_all
from .xirr import xirr


@dataclass
class AnalysisResult:
    as_of: date | None = None
    schemes: pd.DataFrame = field(default_factory=pd.DataFrame)
    comparisons: pd.DataFrame = field(default_factory=pd.DataFrame)
    portfolio: pd.DataFrame = field(default_factory=pd.DataFrame)
    contribution: pd.DataFrame = field(default_factory=pd.DataFrame)
    timelines: dict = field(default_factory=dict)
    costs: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list = field(default_factory=list)


def investor_flows(txn: pd.DataFrame, warnings: list[str] | None = None) -> list[tuple[date, float]]:
    """Cash flows from the investor's point of view: invested = negative, received = positive."""
    flows = []
    for r in txn.itertuples():
        amt = abs(float(r.total_amount))
        if r.category == "investment":
            flows.append((r.date, -amt))
        elif r.category in ("redemption", "dividend_payout"):
            flows.append((r.date, amt))
        elif r.category == "dividend_reinvest":
            continue                                   # units only, no cash moves
        elif warnings is not None:
            warnings.append(f"Transaction '{r.nature}' on {r.date} has no cash-flow rule; ignored.")
    return flows


def _pct(x):
    return None if x is None else round(100.0 * x, 2)


def _units_at(rep: Replication, d: date) -> float:
    if rep.path.empty:
        return 0.0
    p = rep.path[rep.path["date"] <= d]
    return float(p["units_after"].iloc[-1]) if len(p) else 0.0


def _scheme_timeline(txn: pd.DataFrame, nav_date: date, mv: float, units_end: float, nav: float,
                     flows: list, reps: dict[str, tuple[Replication, pd.Series]]) -> pd.DataFrame:
    g = txn.sort_values("date", kind="stable")
    last = g.groupby("date").agg(units=("balance_units", "last"), nav=("price", "last"))
    rows = []
    for d, rr in last.iterrows():
        row = {"date": d, "units": rr["units"], "nav": rr["nav"], "fund_value": rr["units"] * rr["nav"],
               "net_invested": -sum(a for fd, a in flows if fd <= d)}
        for label, (rep, series) in reps.items():
            hit = series.index.searchsorted(pd.Timestamp(d))
            lvl = float(series.iloc[hit]) if hit < len(series) else np.nan
            row[label] = _units_at(rep, d) * lvl
        rows.append(row)
    fin = {"date": nav_date, "units": units_end, "nav": nav, "fund_value": mv,
           "net_invested": -sum(a for _, a in flows)}
    for label, (rep, _s) in reps.items():
        fin[label] = rep.terminal_value
    rows = [r for r in rows if r["date"] < nav_date] + [fin]
    return pd.DataFrame(rows)


def _combined_row(scope: str, items: list[dict], n_total: int) -> dict:
    """items: [{flows, fund_end:(date,val), bench_end:(date,val)}] -> one summary row."""
    flows = [f for it in items for f in it["flows"]]
    f_end = [it["fund_end"] for it in items]
    b_end = [it["bench_end"] for it in items]
    fx = xirr(flows + f_end)
    bx = xirr(flows + b_end)
    fv, bv = sum(v for _, v in f_end), sum(v for _, v in b_end)
    return {"scope": scope, "funds_covered": f"{len(items)} of {n_total}",
            "invested": round(-sum(a for _, a in flows if a < 0), 0),
            "fund_value": round(fv, 0), "fund_xirr_pct": _pct(fx), "bench_xirr_pct": _pct(bx),
            "edge_pts": None if fx is None or bx is None else round((fx - bx) * 100, 2),
            "bench_value": round(bv, 0), "gap_rs": round(fv - bv, 0)}


def run_analysis(portfolio_path: str, index_dir: str, comparators=DEFAULT_COMPARATORS,
                 allow_price_fallback: bool = False) -> AnalysisResult:
    res = AnalysisResult()
    pf = storage.load_portfolio(portfolio_path)
    sch, txn = pf["Schemes"].copy(), pf["Transactions"]
    if sch.empty or txn.empty:
        raise ValueError("Portfolio workbook has no data. Run `ingest` first.")
    store = IndexStore(index_dir)
    res.warnings += store.errors + store.warnings
    own_refs, comp_refs = resolve_all(portfolio_path, store, comparators, allow_price_fallback)
    for c in ("expense_ratio_regular_pct", "expense_ratio_direct_pct"):
        sch[c] = pd.to_numeric(sch[c], errors="coerce")
    sch["last_nav_date"] = pd.to_datetime(sch["last_nav_date"]).dt.date

    scheme_rows, comp_rows, contrib, timelines = [], [], [], {}
    own_items, comp_items = [], {c.label: [] for c in comp_refs}
    all_items = []
    flows_by_scheme = {}
    for s in sch.itertuples():
        t = txn[txn["scheme_key"] == s.scheme_key]
        flows = investor_flows(t, res.warnings)
        flows_by_scheme[s.scheme_key] = flows
        nav_date, mv = s.last_nav_date, float(s.market_value)
        invested = -sum(a for _, a in flows if a < 0)
        withdrawn = sum(a for _, a in flows if a > 0)
        fx = xirr(flows + [(nav_date, mv)])
        inv_rows = t[t["category"] == "investment"]
        wavg = float(np.average([(nav_date - d).days for d in inv_rows["date"]],
                                weights=inv_rows["total_amount"].abs())) if len(inv_rows) else None
        item_base = {"flows": flows, "fund_end": (nav_date, mv)}
        all_items.append(item_base)
        ref = own_refs.get(s.scheme_key, BenchRef("(none)", None, "unavailable"))
        row = {"scheme_name": s.scheme_name, "plan": s.plan, "invested": round(invested, 0),
               "withdrawn": round(withdrawn, 0), "current_value": round(mv, 0),
               "net_gain": round(mv + withdrawn - invested, 0),
               "abs_return_pct": _pct((mv + withdrawn - invested) / invested) if invested else None,
               "avg_holding_days": None if wavg is None else round(wavg), "n_investments": len(inv_rows),
               "fund_xirr_pct": _pct(fx), "benchmark": ref.label, "benchmark_basis": ref.basis,
               "bench_xirr_pct": None, "edge_pts": None, "bench_value": None, "gap_rs": None, "note": ref.note}
        reps: dict[str, tuple[Replication, pd.Series]] = {}
        if ref.series is not None:
            rep = replicate(flows, ref.series, nav_date)
            if rep is None or rep.xirr is None:
                row["note"] = "; ".join(filter(None, [row["note"], *(rep.issues if rep else ["index data does not cover the flows"])]))
            else:
                row.update(bench_xirr_pct=_pct(rep.xirr), bench_value=round(rep.terminal_value, 0),
                           gap_rs=round(mv - rep.terminal_value, 0),
                           edge_pts=None if fx is None else round((fx - rep.xirr) * 100, 2))
                reps[ref.label] = (rep, ref.series)
                own_items.append({**item_base, "bench_end": (nav_date, rep.terminal_value)})
                contrib.append({"scheme_name": s.scheme_name, "benchmark": ref.label, "gap_rs": row["gap_rs"]})
        scheme_rows.append(row)
        for c in comp_refs:
            crow = {"scheme_name": s.scheme_name, "comparator": c.label, "basis": c.basis,
                    "bench_xirr_pct": None, "edge_pts": None, "bench_value": None, "gap_rs": None, "note": c.note}
            if c.series is not None:
                rep = replicate(flows, c.series, nav_date)
                if rep and rep.xirr is not None:
                    crow.update(bench_xirr_pct=_pct(rep.xirr), bench_value=round(rep.terminal_value, 0),
                                gap_rs=round(mv - rep.terminal_value, 0),
                                edge_pts=None if fx is None else round((fx - rep.xirr) * 100, 2))
                    reps[c.label] = (rep, c.series)
                    comp_items[c.label].append({**item_base, "bench_end": (nav_date, rep.terminal_value)})
                else:
                    crow["note"] = "; ".join(rep.issues) if rep else "index data does not cover the flows"
            comp_rows.append(crow)
        timelines[s.scheme_name] = _scheme_timeline(t, nav_date, mv, float(s.closing_units), float(s.last_nav),
                                                    flows, reps)

    n = len(sch)
    prow = []
    for it in all_items:   # whole-portfolio money-weighted return needs no benchmark
        pass
    fx_all = xirr([f for it in all_items for f in it["flows"]] + [it["fund_end"] for it in all_items])
    prow.append({"scope": "Whole portfolio (money-weighted)", "funds_covered": f"{n} of {n}",
                 "invested": round(-sum(a for it in all_items for _, a in it["flows"] if a < 0), 0),
                 "fund_value": round(sum(v for it in all_items for _, v in [it["fund_end"]]), 0),
                 "fund_xirr_pct": _pct(fx_all), "bench_xirr_pct": None, "edge_pts": None,
                 "bench_value": None, "gap_rs": None})
    if own_items:
        prow.append(_combined_row("vs each fund's OWN benchmark (blended)", own_items, n))
    for c in comp_refs:
        if comp_items[c.label]:
            prow.append(_combined_row(f"vs {c.label}" + ("" if c.basis == "TRI" else f" [{c.basis}]"),
                                      comp_items[c.label], n))
    res.as_of = max(sch["last_nav_date"])
    res.schemes, res.comparisons = pd.DataFrame(scheme_rows), pd.DataFrame(comp_rows)
    res.portfolio = pd.DataFrame(prow)
    if contrib:
        c = pd.DataFrame(contrib)
        tot = c["gap_rs"].sum()
        c["share_of_total_gap_pct"] = [None if tot == 0 else round(100 * g / tot, 1) for g in c["gap_rs"]]
        res.contribution = c
    res.timelines = timelines
    res.costs = costs_mod.estimate(sch, {s.scheme_key: timelines[s.scheme_name] for s in sch.itertuples()},
                                   flows_by_scheme, res.as_of)
    for r in res.schemes.itertuples():
        if r.benchmark_basis == "unavailable":
            res.warnings.append(f"{r.scheme_name}: no usable benchmark ({r.note or 'see `indexes plan`'}).")
        elif r.benchmark_basis.startswith("PRICE"):
            res.warnings.append(f"{r.scheme_name}: benchmark is a PRICE series (excludes dividends).")
    return res


def to_excel(res: AnalysisResult, path: str) -> None:
    sheets = {"Portfolio": res.portfolio, "Schemes": res.schemes, "Comparators": res.comparisons,
              "Contribution": res.contribution, "Costs": res.costs}
    for name, tl in res.timelines.items():
        sheets["TL " + name[:26]] = tl
    sheets["Notes"] = pd.DataFrame({"note": res.warnings + [
        "XIRR uses actual days/365. Benchmark = same cash flows invested in the index on the same days.",
        "Gap_rs = fund value - benchmark-equivalent value (positive = fund ahead).",
        "Timeline fund values use NAVs on transaction dates only (full NAV history arrives in Module 4)."]})
    storage._write(path, sheets)
