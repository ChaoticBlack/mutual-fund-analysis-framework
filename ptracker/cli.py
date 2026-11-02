"""Command line entry point:  python -m ptracker <command> ..."""
from __future__ import annotations

import argparse
import sys

from . import storage, validation
from .parsers import factsheet as fs_parser
from .parsers import txn_statement, valuation_summary


def cmd_ingest(a) -> int:
    txn = txn_statement.parse_pdf(a.txn, a.password)
    val = valuation_summary.parse_pdf(a.valuation, a.password) if a.valuation else None
    checks = validation.validate_txn(txn)
    if val:
        checks += validation.validate_valuation_vs_txn(txn, val)
    p, f, w = validation.summarize(checks)
    for c in checks:
        if a.verbose or not c.ok:
            print(c)
    print(f"\nValidation: {p} passed, {f} failed, {w} warnings.")
    if f and not a.force:
        print("Not writing the workbook because checks failed (use --force to override).")
        return 1
    res = storage.save_portfolio(a.out, txn, val, include_name=not a.no_name,
                                 sources={"transactions": a.txn, **({"valuation": a.valuation} if a.valuation else {})},
                                 check_lines=[str(c) for c in checks if not c.ok])
    print(f"Wrote {a.out}: {res['transactions_added']} new transactions "
          f"({res['transactions_total']} total).")
    return 0


def expand_paths(patterns: list[str]) -> list[str]:
    """Windows shells do not expand wildcards for programs, so do it here: 'Factsheet_*.pdf' -> every match."""
    import glob
    out: list[str] = []
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        out += hits if hits else [pat]          # no match: keep it, so the error message names the file
    return out


def cmd_factsheet(a) -> int:
    parsed = []
    for path in expand_paths(a.files):
        r = fs_parser.parse_pdf(path, a.password)
        parsed.append(r)
        print(f"{r.info.get('scheme_name')}: benchmark={r.info.get('benchmark_name')!r}, "
              f"expense ratio={r.info.get('expense_ratio_pct')}, holdings={len(r.holdings)}"
              + (f"  WARN: {'; '.join(r.warnings)}" if r.warnings else ""))
    storage.save_factsheets(a.out, parsed)
    print(f"Wrote {a.out}")
    return 0


def cmd_link(a) -> int:
    print(storage.link_factsheets(a.portfolio, a.factsheets).to_string(index=False))
    return 0


def cmd_analyze(a) -> int:
    import pandas as pd
    from .analytics.engine import run_analysis, to_excel
    from .indexes.plan import DEFAULT_COMPARATORS
    comps = tuple(x.strip() for x in a.comparators.split(",") if x.strip()) if a.comparators else DEFAULT_COMPARATORS
    res = run_analysis(a.portfolio, a.dir, comps, a.allow_price_fallback)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 60)
    print(f"\nValuation date: {res.as_of}\n\n== Portfolio ==")
    print(res.portfolio.to_string(index=False))
    print("\n== Funds vs their own benchmark ==")
    print(res.schemes[["scheme_name", "fund_xirr_pct", "benchmark", "benchmark_basis", "bench_xirr_pct",
                       "edge_pts", "gap_rs"]].to_string(index=False))
    print("\n== Funds vs standard comparators ==")
    print(res.comparisons.drop(columns=["note"]).to_string(index=False))
    if not res.contribution.empty:
        print("\n== Where the gap vs own benchmarks comes from ==")
        print(res.contribution.to_string(index=False))
    print("\n== Cost drag (estimate) ==")
    print(res.costs.to_string(index=False))
    if res.warnings:
        print("\nNotes:")
        for w in dict.fromkeys(res.warnings):
            print("  !", w)
    if a.out:
        to_excel(res, a.out)
        print(f"\nWrote {a.out}")
    return 0


def cmd_nav(a) -> int:
    from .nav.store import NavStore
    from .nav.nav_plan import build_nav_plan
    import pandas as pd
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    st = NavStore(a.nav_dir)
    if a.action == "scan":
        print(st.catalog().to_string(index=False) if st.files else f"No NAV files found in {a.nav_dir}")
        for m in st.errors + st.warnings:
            print("  !", m)
        return 0
    if a.action == "check":
        from .nav.store import check_against_transactions
        pf = storage.load_portfolio(a.portfolio)
        for sc in pf["Schemes"].itertuples():
            nf = getattr(sc, "nav_file", None)
            ser = st.series_for(sc.isin, sc.scheme_name, nf if isinstance(nf, str) else None)
            if ser is None:
                print(f"{sc.scheme_name}: no NAV file found")
                continue
            tx = pf["Transactions"][pf["Transactions"]["scheme_key"] == sc.scheme_key]
            r = check_against_transactions(tx, ser)
            print(f"{sc.scheme_name}: {r['matched']}/{r['checked']} statement prices match the NAV file "
                  f"(file runs {ser.index.min().date()} to {ser.index.max().date()})")
            for d, price, nav in r["mismatches"]:
                print(f"    {d}: statement price {price} vs " + (f"NAV file {nav}" if nav is not None
                                                                 else "no NAV in the file for that date"))
        return 0
    plan = build_nav_plan(a.portfolio, st)
    if a.action == "plan":
        print(plan.to_string(index=False))
        return 0
    if a.action == "fetch":
        from .nav import mfapi
        todo = plan[plan["status"].isin(["missing", "partial"])]
        if todo.empty:
            print("All schemes already have NAV history covering the period.")
            return 0
        print("Will request from AMFI (NAVAll.txt) and MFapi.in (community API):")
        print(todo[["scheme_name", "isin", "need_from", "need_to"]].to_string(index=False))
        if not (a.yes or a.dry_run) and input("Proceed? [y/N] ").strip().lower() != "y":
            return 1
        print(mfapi.fetch_missing(plan, a.nav_dir, dry_run=a.dry_run).to_string(index=False))
        return 0
    return 2


def cmd_risk(a) -> int:
    import pandas as pd
    from .analytics.risk import run_risk, to_excel
    from .indexes.plan import DEFAULT_COMPARATORS
    comps = tuple(x.strip() for x in a.comparators.split(",") if x.strip()) if a.comparators else DEFAULT_COMPARATORS
    res = run_risk(a.portfolio, a.dir, a.nav_dir, comps, a.allow_price_fallback, a.risk_free)
    if res.risk.empty:
        print("No fund has usable NAV history yet, so there is nothing to analyse.")
        for w in dict.fromkeys(res.warnings):
            print("  !", w)
        print(f"\nNext: python -m ptracker nav plan --portfolio {a.portfolio} --nav-dir {a.nav_dir}"
              f"\n      python -m ptracker nav fetch --portfolio {a.portfolio} --nav-dir {a.nav_dir}   (or add NAV files by hand)")
        return 1
    pd.set_option("display.width", 280); pd.set_option("display.max_columns", 40); pd.set_option("display.max_colwidth", 38)
    print("\n== Risk and return (window starts at your first investment in each fund) ==")
    print(res.risk.drop(columns=["window_from", "window_to", "daily_obs", "benchmark_basis"], errors="ignore").to_string(index=False))
    print("\n== Against the standard comparators ==")
    print(res.vs_comparators.to_string(index=False))
    print("\n== Rolling returns summary ==")
    print(res.rolling_summary.to_string(index=False))
    print("\n== Calendar years ==")
    print(res.calendar.to_string(index=False))
    if res.warnings:
        print("\nNotes:")
        for w in dict.fromkeys(res.warnings):
            print("  !", w)
    if a.out:
        to_excel(res, a.out)
        print(f"\nWrote {a.out}")
    return 0


def cmd_costs(a) -> int:
    from .analytics.costs import import_costs
    print(import_costs(a.portfolio, a.file, a.overwrite).to_string(index=False))
    return 0


def _store_and_plan(a):
    from .indexes.files import IndexStore
    from .indexes.plan import DEFAULT_COMPARATORS, build_plan
    comps = tuple(x.strip() for x in a.comparators.split(",") if x.strip()) if a.comparators else DEFAULT_COMPARATORS
    store = IndexStore(a.dir)
    return store, build_plan(a.portfolio, store, comps)


def _show(df):
    import pandas as pd
    with pd.option_context("display.width", 250, "display.max_colwidth", 90, "display.max_columns", 30):
        print(df.to_string(index=False))


def cmd_indexes(a) -> int:
    from .indexes.files import IndexStore
    if a.action == "inspect":
        import pandas as pd
        from .indexes.files import _read_any, read_index_file
        raw = _read_any(a.file)
        print(f"File: {a.file}\nColumns: {list(raw.columns)}\nRows: {len(raw)}")
        print(raw.head(3).to_string(index=False))
        f = read_index_file(a.file)
        print(f"\nDetected index : {f.spec.base}\nDetected type  : {f.spec.variant}  (value column '{f.value_column}')"
              f"\nDate range     : {f.series.index.min().date()} -> {f.series.index.max().date()}"
              f"\nFirst/last val : {f.series.iloc[0]:,.2f} / {f.series.iloc[-1]:,.2f}")
        for w in f.warnings:
            print("  !", w)
        return 0
    if a.action == "scan":
        st = IndexStore(a.dir)
        _show(st.catalog() if len(st.files) else __import__("pandas").DataFrame({"index folder": [a.dir], "files": [0]}))
        for m in st.errors + st.warnings:
            print("  !", m)
        return 0
    store, (plan, mapping) = _store_and_plan(a)
    if a.action == "plan":
        print("\nIndex series needed by this portfolio:\n")
        _show(plan.drop(columns=["fetchable"]))
        print("\nScheme -> benchmark mapping:\n")
        _show(mapping.drop(columns=["scheme_key", "series_key"]))
        return 0
    if a.action == "fetch":
        from .indexes.nse_fetch import fetch_missing
        todo = plan[plan["fetchable"]]
        if todo.empty:
            print("Nothing to fetch - every NSE TRI series needed is already in the folder.")
            return 0
        print("Will request from niftyindices.com (undocumented endpoint, 1 request/second):")
        _show(todo[["benchmark", "status", "need_from", "need_to"]])
        if not (a.yes or a.dry_run) and input("Proceed? [y/N] ").strip().lower() != "y":
            return 1
        _show(fetch_missing(plan, a.dir, dry_run=a.dry_run))
        return 0
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ptracker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest", help="transaction statement (+ valuation summary) PDF -> Excel")
    i.add_argument("--txn", required=True); i.add_argument("--valuation")
    i.add_argument("--out", default="data/portfolio.xlsx"); i.add_argument("--password")
    i.add_argument("--no-name", action="store_true", help="do not store the investor name")
    i.add_argument("--force", action="store_true"); i.add_argument("-v", "--verbose", action="store_true")
    i.set_defaults(fn=cmd_ingest)
    f = sub.add_parser("factsheet", help="scheme factsheet PDFs -> factsheets.xlsx")
    f.add_argument("files", nargs="+"); f.add_argument("--out", default="data/factsheets.xlsx")
    f.add_argument("--password"); f.set_defaults(fn=cmd_factsheet)
    l = sub.add_parser("link", help="fill benchmark / expense ratio in the portfolio from factsheets")
    l.add_argument("--portfolio", default="data/portfolio.xlsx")
    l.add_argument("--factsheets", default="data/factsheets.xlsx"); l.set_defaults(fn=cmd_link)
    x = sub.add_parser("indexes", help="benchmark index data: scan | plan | fetch")
    x.add_argument("action", choices=["scan", "plan", "fetch", "inspect"])
    x.add_argument("--dir", default="data/indexes"); x.add_argument("--portfolio", default="data/portfolio.xlsx")
    x.add_argument("--comparators", help='comma list, default "NIFTY 50 TRI,NIFTY 500 TRI"')
    x.add_argument("--file", help="for 'inspect': a single index file")
    x.add_argument("--yes", action="store_true"); x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_indexes)
    z = sub.add_parser("analyze", help="fund vs benchmark performance (needs the index files)")
    z.add_argument("--portfolio", default="data/portfolio.xlsx"); z.add_argument("--dir", default="data/indexes")
    z.add_argument("--out", help="write the results to this .xlsx"); z.add_argument("--comparators")
    z.add_argument("--allow-price-fallback", action="store_true",
                   help="use PRICE series when a TRI is missing (understates the benchmark)")
    z.set_defaults(fn=cmd_analyze)
    n = sub.add_parser("nav", help="daily NAV history: scan | plan | check | fetch")
    n.add_argument("action", choices=["scan", "plan", "check", "fetch"])
    n.add_argument("--portfolio", default="data/portfolio.xlsx"); n.add_argument("--nav-dir", default="data/nav")
    n.add_argument("--yes", action="store_true"); n.add_argument("--dry-run", action="store_true")
    n.set_defaults(fn=cmd_nav)
    k = sub.add_parser("risk", help="risk metrics, rolling returns, daily values (needs NAV history)")
    k.add_argument("--portfolio", default="data/portfolio.xlsx"); k.add_argument("--dir", default="data/indexes")
    k.add_argument("--nav-dir", default="data/nav"); k.add_argument("--out"); k.add_argument("--comparators")
    k.add_argument("--risk-free", type=float, default=6.0, help="annual risk-free rate in percent (default 6.0)")
    k.add_argument("--allow-price-fallback", action="store_true")
    k.set_defaults(fn=cmd_risk)
    c = sub.add_parser("costs", help="import expense ratios from a spreadsheet")
    c.add_argument("action", choices=["import"]); c.add_argument("--portfolio", default="data/portfolio.xlsx")
    c.add_argument("--file", required=True); c.add_argument("--overwrite", action="store_true")
    c.set_defaults(fn=cmd_costs)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())