"""Work out, for ANY portfolio workbook, which benchmark index series are needed and whether we have them."""
from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd

from .. import storage
from .files import IndexStore, read_index_file
from .spec import IndexSpec, load_aliases, parse_benchmark, similar_bases

DEFAULT_COMPARATORS = ("NIFTY 50 TRI", "NIFTY 500 TRI")
MAX_INTERNAL_GAP_DAYS = 10          # holidays + weekends never exceed ~5; more means missing data


def _coverage_status(s: pd.Series, need_from, need_to, slack: int) -> tuple[str, str]:
    have_from, have_to = s.index.min().date(), s.index.max().date()
    notes = []
    if have_from > need_from + timedelta(days=slack):
        notes.append(f"starts {have_from}, need from {need_from}")
    if have_to < need_to - timedelta(days=slack):
        notes.append(f"ends {have_to}, need to {need_to}")
    gap = IndexStore.coverage_stats(s[(s.index >= pd.Timestamp(need_from)) & (s.index <= pd.Timestamp(need_to))])
    if gap["largest_gap_days"] > MAX_INTERNAL_GAP_DAYS:
        notes.append(f"gap of {gap['largest_gap_days']} days inside the period")
    return ("ready" if not notes else "partial"), "; ".join(notes)


def build_plan(portfolio_path: str, store: IndexStore, comparators=DEFAULT_COMPARATORS,
               slack_days: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (plan, mapping).

    plan:    one row per distinct index needed (benchmarks of the schemes + standard comparators)
    mapping: one row per scheme -> which stored series will be used as its benchmark
    """
    pf = storage.load_portfolio(portfolio_path)
    sch, txn = pf["Schemes"], pf["Transactions"]
    if sch.empty or txn.empty:
        raise ValueError("Portfolio workbook has no Schemes/Transactions. Run `ingest` first.")
    first_txn = txn.groupby("scheme_key")["date"].min()
    last_nav = pd.to_datetime(sch.set_index("scheme_key")["last_nav_date"], errors="coerce").dt.date
    p_from = min(first_txn)
    p_to = max(d for d in last_nav if pd.notna(d))

    needs: dict[str, dict] = {}
    mapping_rows = []
    aliases = load_aliases(store.folder)

    def resolve(name: str, default_variant: str) -> IndexSpec:
        sp = parse_benchmark(name, default_variant)
        canon = aliases.get(sp.base)
        return IndexSpec(canon, sp.variant, sp.source, sp.raw, sp.assumed_tri) if canon else sp

    def need(spec: IndexSpec, used_by: str, n_from, n_to, file_override: str | None = None):
        e = needs.setdefault(spec.key, {"spec": spec, "used_by": [], "from": n_from, "to": n_to,
                                        "override": file_override})
        e["used_by"].append(used_by)
        e["from"], e["to"] = min(e["from"], n_from), max(e["to"], n_to)

    for r in sch.itertuples():
        n_from, n_to = first_txn.get(r.scheme_key, p_from), last_nav.get(r.scheme_key, p_to)
        override = r.benchmark_file if isinstance(r.benchmark_file, str) and r.benchmark_file.strip() else None
        name = r.benchmark_name if isinstance(r.benchmark_name, str) and r.benchmark_name.strip() else None
        if override:
            path = override if os.path.isabs(override) else os.path.join(store.folder, override)
            try:
                f = read_index_file(path)
                spec = f.spec
                need(spec, r.scheme_name, n_from, n_to, file_override=path)
                mapping_rows.append({"scheme_key": r.scheme_key, "scheme_name": r.scheme_name,
                                     "benchmark": spec.label, "series_key": f"FILE:{path}", "source": "file override",
                                     "_status_key": spec.key})
            except Exception as e:
                mapping_rows.append({"scheme_key": r.scheme_key, "scheme_name": r.scheme_name,
                                     "benchmark": override, "series_key": None,
                                     "source": f"override file unreadable: {e}", "_status_key": None})
        elif name:
            spec = resolve(name, "TRI")      # SEBI requires fund benchmarks to be TRI
            need(spec, r.scheme_name, n_from, n_to)
            mapping_rows.append({"scheme_key": r.scheme_key, "scheme_name": r.scheme_name,
                                 "benchmark": spec.label, "series_key": spec.key, "source": "factsheet/sheet",
                                 "_status_key": spec.key})
        else:
            mapping_rows.append({"scheme_key": r.scheme_key, "scheme_name": r.scheme_name, "benchmark": None,
                                 "series_key": None, "source": "no benchmark set", "_status_key": None})
    for c in comparators:
        spec = resolve(c, "TRI")
        need(spec, "(standard comparator)", p_from, p_to)

    rows = []
    for key, e in needs.items():
        spec: IndexSpec = e["spec"]
        series = None
        if e["override"]:
            series = read_index_file(e["override"]).series
        elif store.has(spec):
            series = store.series(spec)
        have_from = series.index.min().date() if series is not None else None
        have_to = series.index.max().date() if series is not None else None
        note = ""
        if series is not None:
            status, note = _coverage_status(series, e["from"], e["to"], slack_days)
        elif spec.variant == "NTR":
            status = "unsupported_variant"
        elif spec.variant == "TRI" and store.has(spec.as_variant("PRICE")):
            status = "price_only"
            ps = store.series(spec.as_variant("PRICE"))
            have_from, have_to = ps.index.min().date(), ps.index.max().date()
        elif spec.source != "NSE":
            status = "manual_only"
        else:
            status = "missing"
        fetchable = spec.source == "NSE" and spec.variant == "TRI" and status in ("missing", "partial", "price_only")
        notes = []
        if spec.assumed_tri:
            notes.append("TRI assumed: the benchmark name did not say TRI (SEBI requires TRI benchmarks)")
        if status in ("missing", "partial", "price_only") or series is None:
            known = sorted({k.split("|")[0] for k in store._series})
            sim = similar_bases(spec.base, known)
            if sim and not store.has(spec):
                notes.append(f"files exist for similar name(s) {sim}; if the same index, add a line "
                             f"'{spec.base},{sim[0]}' to aliases.csv in the index folder")
        rows.append({"benchmark": spec.label, "source": spec.source, "status": status, "note": "; ".join(notes),
                     "have_from": have_from, "have_to": have_to, "need_from": e["from"], "need_to": e["to"],
                     "used_by": "; ".join(dict.fromkeys(e["used_by"])), "fetchable": fetchable,
                     "action": _action(spec, status, note, e, have_to), "_key": key})
    plan = pd.DataFrame(rows, columns=["benchmark", "source", "status", "note", "have_from", "have_to", "need_from",
                                       "need_to", "used_by", "fetchable", "action", "_key"])

    # attach the resolved status to the per-scheme mapping
    status_by_key = dict(zip(plan["_key"], plan["status"]))
    mapping = pd.DataFrame(mapping_rows)
    mapping["status"] = [status_by_key.get(k) if isinstance(k, str) else "no_benchmark"
                         for k in mapping["_status_key"]]
    mapping = mapping.drop(columns="_status_key")
    return plan.drop(columns="_key"), mapping


def _action(spec: IndexSpec, status: str, note: str, e: dict, have_to) -> str:
    rng = f"{e['from']} to {e['to']}"
    if status == "ready":
        return "-"
    if status == "partial":
        return f"Add more {spec.variant} data for {spec.base}: {note}."
    if status == "price_only":
        return (f"Only the PRICE series of {spec.base} is available (no dividends, so it understates the "
                f"benchmark by roughly 1-1.5% a year). Get the Total Returns Index for {rng}.")
    if status == "missing":
        return (f"Download '{spec.base}' as Total Returns Index for {rng} from niftyindices.com "
                f"(Historical data), or run `python -m ptracker indexes fetch`.")
    if status == "manual_only":
        return f"{spec.source} index: download its TRI for {rng} from the provider and place it in the index folder."
    if status == "unsupported_variant":
        return "Net Total Return (NTR) series are not supported yet; use the TRI of the same index."
    return ""