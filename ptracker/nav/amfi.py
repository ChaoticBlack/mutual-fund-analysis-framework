"""Parsers for AMFI's text formats.

NAVAll.txt (latest NAV of every scheme) and the NAV history report are semicolon-separated, with blank lines and
fund-house / category heading lines mixed in between the data rows.

AMFI has changed the column layout over time (for example the scheme name is now followed by separate 'Plan' and
'Option' fields), so rows are read by *content*, not by a fixed number of columns:
  * first field  = scheme code (digits)
  * any field that looks like an ISIN (INF + 9 characters) = ISIN; the first is the payout/growth ISIN, the second
    the reinvestment ISIN
  * the last field that looks like dd-Mon-yyyy = date
  * the NAV = the first number after the ISIN / name fields
"""
from __future__ import annotations

import re

import pandas as pd

NAVALL_URLS = ["https://portal.amfiindia.com/spages/NAVAll.txt", "https://www.amfiindia.com/spages/NAVAll.txt"]
NAVALL_URL = NAVALL_URLS[0]
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"   # ?tp=1&frmdt=1-Apr-2020&todt=15-May-2020

_ISIN = re.compile(r"^INF[A-Z0-9]{9}$")
_DATE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_row(parts: list[str]):
    """Returns dict or None if the line is not a scheme row."""
    if len(parts) < 6 or not parts[0].isdigit() or not _DATE.match(parts[-1]):
        return None
    isins = [p for p in parts[1:] if _ISIN.match(p)]
    # position of the last ISIN-or-placeholder column among the first few fields
    cut = 1
    for i, p in enumerate(parts[1:5], start=1):
        if _ISIN.match(p) or p in ("-", ""):
            cut = i
    name_parts, nav = [], None
    if not _ISIN.match(parts[1]) and parts[1] not in ("-", ""):      # history layout: name comes before the ISINs
        name_parts.append(parts[1])
    for p in parts[cut + 1:-1]:
        if nav is None and _NUM.match(p):
            nav = float(p)
        elif nav is None and p and p != "-":
            name_parts.append(p)
    if nav is None:
        return None
    return {"scheme_code": int(parts[0]), "isin_growth": isins[0] if isins else None,
            "isin_reinv": isins[1] if len(isins) > 1 else None,
            "scheme_name": " - ".join(name_parts), "nav": nav,
            "date": pd.to_datetime(parts[-1], format="%d-%b-%Y", errors="coerce")}


def _parse(text: str) -> pd.DataFrame:
    recs = []
    for raw in text.splitlines():
        if ";" not in raw:
            continue
        rec = _parse_row([p.strip() for p in raw.split(";")])
        if rec:
            recs.append(rec)
    return pd.DataFrame(recs, columns=["scheme_code", "isin_growth", "isin_reinv", "scheme_name", "nav", "date"])


def parse_navall(text: str) -> pd.DataFrame:
    """Latest NAV of every scheme. Handles both the older 6-field and the newer 8-field row layouts."""
    return _parse(text).dropna(subset=["nav"])


def parse_history(text: str) -> pd.DataFrame:
    """NAV history download (one row per scheme per day)."""
    return _parse(text).dropna(subset=["nav", "date"])


def looks_like_history(text: str) -> bool:
    head = text[:5000]
    return ";" in head and not _parse(head).empty


def isin_to_scheme_code(navall: pd.DataFrame, isin: str) -> int | None:
    if navall.empty:
        return None
    hit = navall[(navall["isin_growth"] == isin) | (navall["isin_reinv"] == isin)]
    return int(hit["scheme_code"].iloc[0]) if len(hit) else None