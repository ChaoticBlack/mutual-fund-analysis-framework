"""The Streamlit app. Run from the project folder with:  streamlit run app.py   (app.py just calls main() below)"""
from __future__ import annotations

import itertools
import os
import traceback

import pandas as pd
import streamlit as st

from ..analytics import overlap as ov
from ..indexes.plan import DEFAULT_COMPARATORS
from . import charts, service
from .service import inr, pct, pts

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@st.cache_data(show_spinner="Crunching the numbers...")
def _cached_bundle(root, index_dir, nav_dir, comparators, risk_free, allow_price, signature):
    return service.load_bundle(service.make_paths(root, index_dir, nav_dir), comparators, risk_free, allow_price)


_keys = itertools.count(1)       # reset at the start of every run, so keys are stable between reruns


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Text-like columns that mix dates, numbers and words (e.g. 'not yet') are shown as plain text."""
    d = df.copy()
    for c in d.columns:
        if d[c].dtype == object:
            d[c] = d[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return d


def _show(df: pd.DataFrame, **kw):
    if df is None or df.empty:
        st.caption("Nothing to show yet.")
    else:
        st.dataframe(_clean(df), hide_index=True, **kw)


def _fig(fig, note: str = "Not enough data for this chart yet."):
    """Every chart gets its own key: Streamlit rejects two identical charts without one (e.g. the same chart
    shown on two tabs)."""
    if fig is None:
        if note:
            st.caption(note)
    else:
        st.plotly_chart(fig, key=f"chart_{next(_keys)}")


# ================================================================== sidebar
def _sidebar_settings(default_root):
    with st.sidebar:
        st.header("Settings")
        root = st.text_input("Data folder", value=default_root or os.environ.get("PT_DATA_DIR", "data"),
                             help="Folder holding portfolio.xlsx, factsheets.xlsx and the index / NAV folders.")
        with st.expander("Other folders (optional)"):
            index_dir = st.text_input("Index files folder", value="", help="Empty = <data folder>/indexes")
            nav_dir = st.text_input("NAV files folder", value="", help="Empty = <data folder>/nav")
        rf = st.number_input("Risk-free rate (% a year)", min_value=0.0, max_value=15.0, value=6.0, step=0.25,
                             help="Used for Sharpe, Sortino and alpha.")
        allow = st.checkbox("Use price indices when a TRI file is missing", value=False,
                            help="Price indices leave out dividends, so they understate the benchmark by roughly 1-1.5% a year.")
        comps_text = st.text_input("Standard comparators", value=", ".join(DEFAULT_COMPARATORS))
    comps = tuple(c.strip() for c in comps_text.split(",") if c.strip()) or DEFAULT_COMPARATORS
    return root, index_dir.strip() or None, nav_dir.strip() or None, comps, float(rf), bool(allow)


def _sidebar_uploads(paths: service.DataPaths):
    with st.sidebar:
        with st.expander("Add or update data"):
            st.caption("PDFs are read in memory and discarded. Only the cleaned Excel workbook is saved, "
                       "without address, phone, e-mail, bank or nominee details.")
            txn = st.file_uploader("Transaction statement (PDF)", type=["pdf"], key="up_txn")
            val = st.file_uploader("Portfolio valuation summary (PDF, optional)", type=["pdf"], key="up_val")
            fsf = st.file_uploader("Fund factsheets (PDF, optional)", type=["pdf"], accept_multiple_files=True, key="up_fs")
            pw = st.text_input("PDF password (if any)", type="password", key="pdf_pw")
            keep_name = st.checkbox("Store my name in the workbook", value=False, key="keep_name")
            if st.button("Process statements", type="primary", disabled=txn is None):
                ok, msgs = service.ingest_pdfs(
                    paths, (txn.name, txn.getvalue()), (val.name, val.getvalue()) if val else None,
                    [(f.name, f.getvalue()) for f in (fsf or [])], pw, keep_name)
                st.session_state["messages"] = ("success" if ok else "error", msgs)
                if ok:
                    st.cache_data.clear()
            st.divider()
            wbs = st.file_uploader("Workbooks made earlier (portfolio.xlsx / factsheets.xlsx)", type=["xlsx"],
                                   accept_multiple_files=True, key="up_wb")
            if st.button("Use these workbooks", disabled=not wbs):
                st.session_state["messages"] = ("info", service.save_workbooks(paths, [(f.name, f.getvalue()) for f in wbs]))
                st.cache_data.clear()
            st.divider()
            idx = st.file_uploader("Index files (CSV / Excel)", type=["csv", "xlsx", "xls"],
                                   accept_multiple_files=True, key="up_idx")
            if st.button("Save index files", disabled=not idx):
                st.session_state["messages"] = ("info", service.save_uploads(paths.indexes, [(f.name, f.getvalue()) for f in idx]))
                st.cache_data.clear()
            navf = st.file_uploader("NAV history files (CSV / Excel / AMFI text)", type=["csv", "xlsx", "xls", "txt"],
                                    accept_multiple_files=True, key="up_nav")
            if st.button("Save NAV files", disabled=not navf):
                st.session_state["messages"] = ("info", service.save_uploads(paths.nav, [(f.name, f.getvalue()) for f in navf]))
                st.cache_data.clear()
        kind_msgs = st.session_state.get("messages")
        if kind_msgs:
            kind, msgs = kind_msgs
            (st.success if kind == "success" else st.error if kind == "error" else st.info)("\n\n".join(msgs))


# ================================================================== pages
def freshness_warning(b: service.Bundle) -> str | None:
    """The Overview numbers use the statement's valuation date, while charts and risk need daily NAVs.
    If the NAV history stops earlier, say so instead of silently drawing shorter charts."""
    if b.analysis is None or b.analysis.as_of is None or b.risk is None or b.risk.portfolio_daily.empty:
        return None
    last = pd.to_datetime(b.risk.portfolio_daily["date"]).max().date()
    gap = (b.analysis.as_of - last).days
    if gap <= 3:
        return None
    return (f"Your latest valuation date is {b.analysis.as_of}, but the daily NAV history ends on {last} ({gap} days "
            f"earlier). The Overview figures use {b.analysis.as_of}; the charts, Funds and Risk tabs stop at {last} and "
            "leave out the latest SIPs and price moves. Add the missing NAVs (Data & Setup > fetch missing NAV history, or "
            "the sidebar) and reload.")


def render_overview(b: service.Bundle):
    a, r = b.analysis, b.risk
    if a is None:
        st.warning("The performance analysis is not available yet. See 'Data & Setup' for what is missing.")
        return
    p = a.portfolio
    whole = p.iloc[0]
    own = p[p["scope"].str.startswith("vs each fund")]
    nifty = p[p["scope"].str.startswith("vs NIFTY 50")]
    gain = float(a.schemes["net_gain"].sum())
    c = st.columns(5)
    c[0].metric("Money put in", inr(whole["invested"]))
    c[1].metric("Worth today", inr(whole["fund_value"]), delta=inr(gain))
    c[2].metric("Your annual return (XIRR)", pct(whole["fund_xirr_pct"]))
    if not own.empty:
        o = own.iloc[0]
        c[3].metric("Vs your funds' own benchmarks", pts(o["edge_pts"]), delta=inr(o["gap_rs"]))
    else:
        c[3].metric("Vs own benchmarks", "n/a")
    if not nifty.empty:
        n = nifty.iloc[0]
        c[4].metric("Vs Nifty 50 index fund", pts(n["edge_pts"]), delta=inr(n["gap_rs"]))
    else:
        c[4].metric("Vs Nifty 50", "n/a")
    st.caption(f"As of {a.as_of}. 'Same cash flows' means every SIP you made is "
               "mirrored into the index on the same day, so the comparison is fair for SIP money.")

    if r is not None and not r.portfolio_daily.empty:
        pdaily = r.portfolio_daily.copy()
        pdaily["date"] = pd.to_datetime(pdaily["date"])
        bench = service.bench_columns(pdaily, "Each fund's own benchmark, combined")
        _fig(charts.value_vs_benchmarks(pdaily, "portfolio_value", "net_invested", bench,
                                        "Portfolio value vs what the same money would be in each benchmark"))
        with st.expander("How to read this chart"):
            st.markdown(
                "- **Your value** (blue) is what your units are worth each day. **Money put in** (grey dashed) is the cash you invested.\n"
                "- Every other line answers: *if each deposit had gone into this index on the same day instead, what would it be worth?*\n"
                "- **Each fund's own benchmark, combined**: your funds do have different benchmarks. For this line, every fund's "
                "deposits are mirrored into that fund's own benchmark, and the results are added together, so each benchmark counts "
                "in proportion to the money you put into the fund that uses it.\n"
                "- Where blue is above a line you are ahead of that alternative.")
        _fig(charts.gap_over_time(pdaily, "portfolio_value", bench, "Your lead (+) or lag (-) over time"))
        st.caption("This is the blue line minus each benchmark line, in rupees. Above zero you are ahead of that alternative; "
                   "a rising line means your funds were beating it during that stretch.")
    else:
        st.info("Add daily NAV history under 'Data & Setup' to see the day-by-day chart.")
    _fig(charts.xirr_bars(a.schemes))
    st.subheader("Portfolio summary")
    _show(_format_portfolio(p))


def _format_portfolio(p: pd.DataFrame) -> pd.DataFrame:
    d = p.copy()
    for c in ("invested", "fund_value", "bench_value", "gap_rs"):
        d[c] = d[c].map(inr)
    for c in ("fund_xirr_pct", "bench_xirr_pct"):
        d[c] = d[c].map(pct)
    d["edge_pts"] = d["edge_pts"].map(pts)
    return d.rename(columns={"scope": "Comparison", "funds_covered": "Funds", "invested": "Put in", "fund_value": "Your value",
                             "fund_xirr_pct": "Your XIRR", "bench_xirr_pct": "Benchmark XIRR", "edge_pts": "Edge",
                             "bench_value": "Benchmark value", "gap_rs": "Gap"})


def render_funds(b: service.Bundle):
    a, r = b.analysis, b.risk
    if a is None or a.schemes.empty:
        st.info("Nothing to show yet.")
        return
    sel = st.selectbox("Fund", list(a.schemes["scheme_name"]))
    row = a.schemes[a.schemes["scheme_name"] == sel].iloc[0]
    c = st.columns(5)
    c[0].metric("Put in", inr(row["invested"]))
    c[1].metric("Worth today", inr(row["current_value"]), delta=inr(row["net_gain"]))
    c[2].metric("Your XIRR", pct(row["fund_xirr_pct"]))
    c[3].metric("Benchmark XIRR", pct(row["bench_xirr_pct"]))
    c[4].metric("Edge", pts(row["edge_pts"]), delta=inr(row["gap_rs"]))
    st.caption(f"Benchmark: {row['benchmark']} ({row['benchmark_basis']}). {row['note'] or ''}")
    if r is None or r.risk.empty or sel not in set(r.risk["scope"]):
        st.info("Add NAV history for this fund under 'Data & Setup' to see its charts.")
        return
    d = service.fund_daily(r, sel)
    bench = service.bench_columns(d, str(row["benchmark"]))
    _fig(charts.value_vs_benchmarks(d, "fund_value", "net_invested", bench, "Value vs benchmark-equivalent values"))
    c1, c2 = st.columns(2)
    with c1:
        months = st.radio("Rolling window (months)", [6, 12], index=1, horizontal=True)
        _fig(charts.rolling_chart(r.rolling, sel, months), "Not enough history for this window yet.")
        st.caption("Each point is the return over the previous N months. Where blue is above red, the fund beat its "
                   "benchmark over that window.")
    with c2:
        _fig(charts.calendar_chart(r.calendar, sel))
        st.caption("Return in each calendar year. The first and last bars can be part-years (labelled).")
    _fig(charts.drawdown_chart(d.set_index("date")["nav"]))
    st.caption("0% means the fund's price is at its highest so far. A reading of -20% means the price is 20% below its "
               "previous peak on that day. The deepest dip is the maximum drawdown; how long the line stays below zero "
               "is how long recovery took (or is taking). It follows the fund's price as if bought on day one, not your "
               "SIP-averaged position.")
    rk = r.risk[r.risk["scope"] == sel]
    if not rk.empty:
        t = rk.iloc[0].drop(labels=["scope"]).astype(str).rename("value").reset_index().rename(columns={"index": "metric"})
        st.subheader("Risk and return numbers")
        _show(t)


def render_compare(b: service.Bundle):
    a = b.analysis
    if a is None:
        st.info("Nothing to show yet.")
        return
    s = a.schemes.copy()
    _fig(charts.xirr_bars(a.schemes))
    c1, c2 = st.columns(2)
    with c1:
        _fig(charts.gap_bars(a.schemes))
        st.caption("One bar per fund: rupees ahead of (+) or behind (-) what the same deposits would be worth in that "
                   "fund's own benchmark.")
    with c2:
        _fig(charts.gap_waterfall(a.contribution))
        st.caption("The same numbers stacked left to right, so you can see the portfolio's total gap build up. The last "
                   "bar is the total.")
    if not a.contribution.empty:
        ct = a.contribution.copy()
        ct["gap_rs"] = ct["gap_rs"].map(inr)
        ct["share_of_total_gap_pct"] = ct["share_of_total_gap_pct"].map(lambda v: "-" if v is None or pd.isna(v) else f"{v:.1f}%")
        _show(ct.rename(columns={"gap_rs": "Gap", "share_of_total_gap_pct": "Share of the total gap"}))
        st.caption("A share above 100% or below 0% is normal: one fund can explain more than the whole gap if others offset it.")
    for col in ("invested", "current_value", "net_gain", "bench_value", "gap_rs"):
        s[col] = s[col].map(inr)
    for col in ("abs_return_pct", "fund_xirr_pct", "bench_xirr_pct"):
        s[col] = s[col].map(pct)
    s["edge_pts"] = s["edge_pts"].map(pts)
    st.subheader("Funds against their own benchmarks")
    _show(s.drop(columns=["plan", "withdrawn"], errors="ignore"))
    st.subheader("Funds against the standard comparators")
    cmp_ = a.comparisons.copy()
    if not cmp_.empty:
        cmp_["bench_value"] = cmp_["bench_value"].map(inr)
        cmp_["gap_rs"] = cmp_["gap_rs"].map(inr)
        cmp_["edge_pts"] = cmp_["edge_pts"].map(pts)
        cmp_["bench_xirr_pct"] = cmp_["bench_xirr_pct"].map(pct)
    _show(cmp_.drop(columns=["note"], errors="ignore"))


def render_risk(b: service.Bundle):
    r = b.risk
    if r is None or r.risk.empty:
        st.info("Risk numbers need daily NAV history. Add it under 'Data & Setup'.")
        return
    st.caption("Each window starts at your first investment in the fund, so figures describe the period you actually "
               "held it. With about two years of data, treat ratios as indicative.")
    c1, c2 = st.columns(2)
    with c1:
        _fig(charts.capture_bars(r.risk))
    with c2:
        _fig(charts.risk_return_scatter(r.risk))
    st.subheader("Risk and return by fund")
    _show(r.risk.drop(columns=["window_from", "window_to", "benchmark_basis"], errors="ignore"))
    st.subheader("Rolling returns: how often was the fund ahead?")
    _show(r.rolling_summary)
    st.subheader("Against the standard comparators")
    _show(r.vs_comparators)
    st.subheader("Calendar years")
    _show(r.calendar)
    with st.expander("How to read these numbers"):
        st.markdown(
            "- **Beta**: 1.0 moves with the benchmark; above 1.0 swings more.\n"
            "- **Max drawdown**: worst fall from a previous high; 'recovered' is the date it got back to that high.\n"
            "- **Up / down capture**: share of the benchmark's good (bad) days the fund captured. Below 100 on down days is good.\n"
            "- **Tracking error / information ratio**: how far the fund strays from the benchmark, and the extra return per unit of that.\n"
            "- **Sharpe / Sortino**: return above the risk-free rate per unit of risk; negative when returns are below it.\n"
            "- **Alpha**: annual return not explained by the benchmark's moves.")


def render_overlap(b: service.Bundle):
    h = b.factsheets.get("Holdings", pd.DataFrame()) if b.factsheets else pd.DataFrame()
    if h.empty or h["scheme_name"].nunique() < 2:
        found = "found" if os.path.exists(b.paths.factsheets) else "NOT found"
        n = 0 if h.empty else h["scheme_name"].nunique()
        st.info(f"Overlap needs the holdings of at least two funds. It reads them from {b.paths.factsheets} "
                f"({found}; {n} fund(s) with holdings). That workbook is built from the factsheet PDFs once; if you already "
                "have it, point 'Data folder' in the sidebar at the folder that contains it, or load it with "
                "'Add or update data' > 'Workbooks made earlier'.")
        return
    funds_all = list(h["scheme_name"].drop_duplicates())
    sel = st.multiselect("Funds to compare", funds_all, default=funds_all)
    if len(sel) < 2:
        st.info("Pick at least two funds.")
        return
    declared = {}
    fdf = b.factsheets.get("Funds", pd.DataFrame())
    if not fdf.empty and "equity_pct" in fdf:
        declared = dict(zip(fdf["scheme_name"], fdf["equity_pct"]))
    cov = ov.coverage(h, sel, declared)
    bad = cov[~cov["complete"]]
    if not bad.empty:
        parts = []
        for r in bad.itertuples():
            extra = ""
            if r.equity_not_itemised_pct is not None and not pd.isna(r.equity_not_itemised_pct) and r.equity_not_itemised_pct > 1:
                extra = f"; it says {r.declared_equity_pct:.1f}% is equity but lists only {r.equity_pct:.1f}%"
            parts.append(f"{ov.short(r.fund)}: the factsheet itemises {r.total_read_pct:.1f}% of the fund{extra}")
        st.warning("Some factsheets list only part of the fund (often because derivative positions are not itemised): "
                   + " | ".join(parts) + ". Overlap involving these funds may be understated.")
    eq = ov.prepare(h, sel)
    values = service.fund_values(b.analysis.schemes if b.analysis else None, sel)
    st.caption("Overlap between two funds = for every company both hold, the smaller of the two weights, added up "
               "(as % of each fund's assets). 0% = nothing in common; 100% = identical. Holdings are as of the factsheet date.")
    c1, c2 = st.columns(2)
    with c1:
        _fig(charts.overlap_heatmap(ov.overlap_matrix(eq), "Same companies (overlap %)"))
    with c2:
        _fig(charts.overlap_heatmap(ov.overlap_matrix(eq, by="sector"), "Same sectors (overlap %)"))
    st.caption("The diagonal is each fund's total equity weight. Funds can hold different companies yet the same sectors, "
               "so compare the two charts: a low left chart with a high right chart means different stocks, same bets.")
    st.subheader("How much of each fund sits in companies another selected fund also holds")
    _show(ov.shared_share(eq).assign(fund=lambda d: d["fund"].map(ov.short)).rename(columns={
        "fund": "Fund", "equity_pct": "Equity %", "in_shared_stocks_pct": "In shared companies %",
        "share_of_equity_pct": "Share of its equity %", "n_shared_stocks": "Shared companies"}))
    if values:
        st.subheader("What you really own, looking through the funds")
        expo = ov.stock_exposure_by_fund(eq, values, 15)
        _fig(charts.lookthrough_bars(expo))
        st.caption("Rupees you hold in each company, adding up every fund that holds it and coloured by fund. "
                   "These use the funds' current values and the factsheet weights.")
        t = ov.stock_table(eq, values)
        multi = t[t["n_funds"] > 1].copy()
        st.subheader(f"Companies held by more than one fund ({len(multi)}; "
                     f"{multi['share_of_your_money_pct'].sum():.1f}% of your money in these funds)")
        wcols = [c for c in multi.columns if c in set(eq["fund"])]
        multi["exposure_rs"] = multi["exposure_rs"].map(inr)
        multi["share_of_your_money_pct"] = multi["share_of_your_money_pct"].map(lambda v: f"{v:.2f}%")
        multi = multi.rename(columns={**{c: ov.short(c) for c in wcols}, "instrument": "Company", "sector": "Sector",
                                      "n_funds": "Funds", "exposure_rs": "Your rupees", "share_of_your_money_pct": "Share"})
        _show(multi)
        st.subheader("Sector exposure, looking through the funds")
        sl = ov.sector_lookthrough(eq, values)
        sl["exposure_rs"] = sl["exposure_rs"].map(inr)
        sl["share_of_your_money_pct"] = sl["share_of_your_money_pct"].map(lambda v: f"{v:.2f}%")
        _show(sl.rename(columns={"sector": "Sector", "exposure_rs": "Your rupees", "share_of_your_money_pct": "Share"}))
    else:
        st.info("Process your portfolio so fund values are known to see rupee look-through numbers.")
    _fig(charts.sector_heatmap(ov.sector_matrix(eq)))


def render_costs(b: service.Bundle):
    a = b.analysis
    if a is None or a.costs.empty:
        st.info("Nothing to show yet.")
        return
    c = a.costs.copy()
    if c["est_commission_to_date"].notna().any():
        tot, nxt = c["est_commission_to_date"].sum(), c["est_next_12m"].sum()
        m = st.columns(2)
        m[0].metric("Estimated commission paid so far", inr(tot))
        m[1].metric("Estimated over the next 12 months", inr(nxt))
        _fig(charts.commission_bars(a.costs))
    else:
        st.warning("Fill both expense-ratio columns (regular and direct) in the Schemes sheet to see the cost estimate.")
    for col in ("est_commission_to_date", "est_next_12m"):
        c[col] = c[col].map(inr)
    _show(c)
    st.caption("Estimate = value held over time x (regular - direct expense ratio). It assumes the distributor receives "
               "the whole gap and flat markets ahead. Use expense ratios from the same month for both plans.")


def render_holdings(b: service.Bundle):
    alloc = service.latest_allocations(b.sheets)
    if alloc.empty:
        st.info("Process the portfolio valuation summary PDF to see allocation.")
    else:
        st.caption(f"From your valuation summary dated {alloc['as_on'].iloc[0].date()}.")
        for section, g in alloc.groupby("section", sort=False):
            d = g.rename(columns={"label": "label", "pct": "pct"})
            _fig(charts.allocation_bars(d, str(section)), "")
    fs = b.factsheets
    if fs and not fs.get("Funds", pd.DataFrame()).empty:
        st.subheader("Fund factsheets")
        names = list(fs["Funds"]["scheme_name"])
        sel = st.selectbox("Factsheet", names, key="fs_sel")
        info = fs["Funds"][fs["Funds"]["scheme_name"] == sel].iloc[0]
        st.caption(f"Benchmark {info.get('benchmark_name')}; expense ratio {info.get('expense_ratio_pct')}%; "
                   f"exit load {info.get('exit_load')}; fund manager {info.get('fund_manager')}.")
        sec = fs.get("SectorExposure", pd.DataFrame())
        if not sec.empty:
            g = sec[sec["scheme_name"] == sel].rename(columns={"sector_or_rating": "label", "weight_pct": "pct"})
            _fig(charts.allocation_bars(g, "Sector exposure (from the fund's holdings)"), "")
        h = fs.get("Holdings", pd.DataFrame())
        if not h.empty:
            st.dataframe(h[h["scheme_name"] == sel][["instrument", "sector_or_rating", "weight_pct"]], hide_index=True)


def render_setup(b: service.Bundle):
    st.subheader("What is ready")
    rows = [("Portfolio workbook", "yes" if b.has_portfolio else "no - upload the statement in the sidebar")]
    if b.has_portfolio:
        sch = b.sheets.get("Schemes", pd.DataFrame())
        have_b = int(sch["benchmark_name"].notna().sum()) if "benchmark_name" in sch else 0
        rows.append(("Funds with a benchmark", f"{have_b} of {len(sch)}"))
        if not b.index_plan.empty:
            rows.append(("Index series ready", f"{int((b.index_plan['status'] == 'ready').sum())} of {len(b.index_plan)}"))
        if not b.nav_plan.empty:
            rows.append(("Funds with NAV history", f"{int((b.nav_plan['status'] == 'ready').sum())} of {len(b.nav_plan)}"))
    _show(pd.DataFrame(rows, columns=["item", "status"]))
    for e in b.errors:
        st.error(e)
    if not b.has_portfolio:
        return
    st.subheader("Benchmark index files")
    _show(b.index_plan.drop(columns=["fetchable"], errors="ignore"))
    st.subheader("Fund to benchmark mapping")
    _show(b.index_mapping.drop(columns=["scheme_key", "series_key"], errors="ignore"))
    st.subheader("NAV history")
    _show(b.nav_plan)
    st.subheader("Optional online downloads")
    st.caption("These use unofficial public endpoints (niftyindices.com, AMFI and MFapi.in). They can fail or change; "
               "if they do, download the files by hand and add them from the sidebar.")
    agree = st.checkbox("I understand and want to try", key="agree_fetch")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Fetch missing NAV history", disabled=not agree):
            try:
                from ..nav import mfapi
                _show(mfapi.fetch_missing(b.nav_plan, b.paths.nav))
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Fetch failed: {e}")
    with c2:
        if st.button("Fetch missing TRI index files", disabled=not agree):
            try:
                from ..indexes import nse_fetch
                _show(nse_fetch.fetch_missing(b.index_plan, b.paths.indexes))
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Fetch failed: {e}")
    st.subheader("Notes and warnings")
    notes = list(dict.fromkeys((b.analysis.warnings if b.analysis else []) + (b.risk.warnings if b.risk else [])))
    if notes:
        for n in notes:
            st.caption("- " + n)
    else:
        st.caption("None.")
    log = b.sheets.get("Parse_Log", pd.DataFrame())
    if not log.empty:
        with st.expander("Ingestion log"):
            _show(log.tail(30))
    st.subheader("Download results")
    if st.checkbox("Prepare Excel downloads", key="prep_dl"):
        if b.analysis is not None:
            st.download_button("Download analysis.xlsx", service.excel_bytes("analysis", b.analysis), "analysis.xlsx", XLSX)
        if b.risk is not None and not b.risk.risk.empty:
            st.download_button("Download risk.xlsx", service.excel_bytes("risk", b.risk), "risk.xlsx", XLSX)


# ================================================================== entry point
def main(default_root: str | None = None):
    global _keys
    _keys = itertools.count(1)
    st.set_page_config(page_title="Portfolio tracker", layout="wide")
    st.title("Portfolio tracker")
    st.caption("Runs on your computer; your data is not sent anywhere. Not investment advice.")
    root, index_dir, nav_dir, comps, rf, allow = _sidebar_settings(default_root)
    paths = service.make_paths(root, index_dir, nav_dir)
    _sidebar_uploads(paths)
    sig = tuple(service.dir_signature(p) for p in (paths.portfolio, paths.factsheets, paths.indexes, paths.nav))
    b = _cached_bundle(root, paths.indexes, paths.nav, comps, rf, allow, sig)
    if not b.has_portfolio:
        st.info(f"No portfolio found in {paths.root}. Open 'Add or update data' in the sidebar, upload your transaction "
                "statement PDF and press 'Process statements'.")
    stale = freshness_warning(b)
    if stale:
        st.warning(stale)
    tabs = st.tabs(["Overview", "Funds", "Compare", "Risk", "Overlap", "Costs", "Holdings", "Data & Setup"])
    pages = [render_overview, render_funds, render_compare, render_risk, render_overlap, render_costs, render_holdings,
             render_setup]
    for tab, page in zip(tabs, pages):
        with tab:
            if not b.has_portfolio and page is not render_setup:
                st.caption("Waiting for data.")
                continue
            try:                      # one failing tab must not blank out the others
                page(b)
            except Exception as e:
                st.error(f"This tab hit a problem: {type(e).__name__}: {e}")
                with st.expander("Technical details (please send these if you report it)"):
                    st.code(traceback.format_exc())