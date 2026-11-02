"""'What if the same money had gone into the benchmark on the same days?'"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .xirr import xirr


@dataclass
class Replication:
    xirr: float | None
    terminal_value: float | None
    terminal_date: date
    units: float
    path: pd.DataFrame                       # date, level, units_after (one row per flow date)
    issues: list[str] = field(default_factory=list)


def _on_or_after(series: pd.Series, day: date):
    i = series.index.searchsorted(pd.Timestamp(day))
    return None if i >= len(series) else (series.index[i], float(series.iloc[i]))


def _on_or_before(series: pd.Series, day: date):
    i = series.index.searchsorted(pd.Timestamp(day), side="right") - 1
    return None if i < 0 else (series.index[i], float(series.iloc[i]))


def level_on_or_before(series: pd.Series, day: date):
    return _on_or_before(series, day)


def replicate(flows: list[tuple[date, float]], series: pd.Series, as_of: date,
              max_gap_days: int = 7) -> Replication | None:
    """Mirror investor flows (negative = invested, positive = withdrawn) into an index series.

    Buys/sells happen at the first index level on or after the flow date; the position is valued at
    the last level on or before `as_of`. Returns None if the series cannot cover the flows at all.
    """
    issues: list[str] = []
    by_day: dict[date, float] = {}
    for d, a in flows:
        by_day[d] = by_day.get(d, 0.0) + a
    units, rows = 0.0, []
    for d in sorted(by_day):
        hit = _on_or_after(series, d)
        if hit is None or (hit[0].date() - d).days > max_gap_days:
            issues.append(f"index has no level within {max_gap_days} days after {d}")
            return None if not rows else Replication(None, None, as_of, units, pd.DataFrame(rows), issues)
        ts, lvl = hit
        units += -by_day[d] / lvl
        if units < -1e-9:
            issues.append(f"benchmark replication sold more units than held on {d}")
        rows.append({"date": d, "level": lvl, "units_after": units})
    end = _on_or_before(series, as_of)
    if end is None or (as_of - end[0].date()).days > max_gap_days:
        issues.append(f"index data ends before the valuation date {as_of}")
        return Replication(None, None, as_of, units, pd.DataFrame(rows), issues)
    tv = units * end[1]
    rate = xirr(list(by_day.items()) + [(as_of, tv)])
    return Replication(rate, tv, as_of, units, pd.DataFrame(rows), issues)
