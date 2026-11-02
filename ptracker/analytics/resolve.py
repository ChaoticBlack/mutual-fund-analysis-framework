"""Decide which stored index series stands in for each fund's benchmark and for the comparators."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..indexes.files import IndexStore, read_index_file
from ..indexes.plan import DEFAULT_COMPARATORS, build_plan
from ..indexes.spec import IndexSpec, load_aliases, parse_benchmark


@dataclass
class BenchRef:
    label: str                    # e.g. 'NIFTY INDIA MANUFACTURING TRI'
    series: pd.Series | None
    basis: str                    # 'TRI' | 'PRICE (no dividends)' | 'unavailable'
    note: str = ""


def _ref_for_spec(store: IndexStore, spec: IndexSpec, allow_price: bool) -> BenchRef:
    if store.has(spec):
        return BenchRef(spec.label, store.series(spec), spec.variant)
    price = spec.as_variant("PRICE")
    if spec.variant == "TRI" and store.has(price):
        if allow_price:
            return BenchRef(price.label, store.series(price), "PRICE (no dividends)",
                            "TRI not found; PRICE series used - benchmark return understated by the dividend yield")
        return BenchRef(spec.label, None, "unavailable", "only a PRICE series exists (use --allow-price-fallback)")
    return BenchRef(spec.label, None, "unavailable", "no data for this index in the folder")


def resolve_all(portfolio_path: str, store: IndexStore, comparators=DEFAULT_COMPARATORS,
                allow_price: bool = False) -> tuple[dict[str, BenchRef], list[BenchRef]]:
    """Returns ({scheme_key: BenchRef}, [comparator BenchRef...])."""
    _plan, mapping = build_plan(portfolio_path, store, comparators)
    aliases = load_aliases(store.folder)
    per_scheme: dict[str, BenchRef] = {}
    for r in mapping.itertuples():
        key = r.series_key
        if not isinstance(key, str):
            per_scheme[r.scheme_key] = BenchRef("(none)", None, "unavailable", str(r.source))
        elif key.startswith("FILE:"):
            try:
                f = read_index_file(key[5:])
                per_scheme[r.scheme_key] = BenchRef(f.spec.label + " (file)", f.series, f.spec.variant)
            except Exception as e:
                per_scheme[r.scheme_key] = BenchRef(key, None, "unavailable", str(e))
        else:
            base, variant = key.split("|")
            src = parse_benchmark(r.benchmark).source
            per_scheme[r.scheme_key] = _ref_for_spec(store, IndexSpec(base, variant, src), allow_price)
    comps = []
    for c in comparators:
        sp = parse_benchmark(c, "TRI")
        if sp.base in aliases:
            sp = IndexSpec(aliases[sp.base], sp.variant, sp.source, sp.raw)
        comps.append(_ref_for_spec(store, sp, allow_price))
    return per_scheme, comps
