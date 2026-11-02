"""OPTIONAL NAV downloader (MFapi.in, a free community API built on AMFI data) + ISIN -> scheme code lookup.

Caveats (the same honesty as the NSE index fetcher):
  * MFapi.in is not run by AMFI; it can change, rate-limit or disappear. The ISIN -> scheme-code lookup uses
    AMFI's own NAVAll.txt, because MFapi cannot be queried by ISIN.
  * Not exercised against the live services from the development sandbox (no internet); the request and response
    handling is unit-tested against the documented formats.
  * Polite by design: one request per scheme, only the missing date range, retries with back-off.
Anything downloaded is saved as plain CSV (`<ISIN>.csv`), so the manual route always works.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pandas as pd

from .amfi import NAVALL_URLS, isin_to_scheme_code, parse_navall

BASE = "https://api.mfapi.in"
UA = "Mozilla/5.0 (X11; Linux x86_64) ptracker/0.4"


def _default_get(url, params, timeout):
    import requests
    return requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)


class MfApi:
    def __init__(self, get=None, sleep: float = 0.5, max_retries: int = 3, timeout: float = 60.0, log=print):
        self._get, self.sleep, self.max_retries, self.timeout, self.log = get or _default_get, sleep, max_retries, timeout, log

    def _call(self, url, params=None):
        delay, last = 2.0, None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._get(url, params, self.timeout)
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
                if attempt < self.max_retries:
                    self.log(f"  request failed ({e}); retrying in {delay:.0f}s")
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(f"request failed after {self.max_retries + 1} attempts: {last}")

    def navall(self) -> pd.DataFrame:
        """Latest NAV list. Tries each known AMFI address; fails loudly if none can be parsed."""
        problems = []
        for url in NAVALL_URLS:
            try:
                text = self._call(url).text
            except Exception as e:
                problems.append(f"{url}: {e}")
                continue
            df = parse_navall(text)
            if not df.empty:
                return df
            first = next((l for l in text.splitlines() if l.strip()), "")[:100]
            problems.append(f"{url}: downloaded {len(text)} characters but no scheme rows could be read "
                            f"(first line: {first!r})")
        raise LookupError("could not read AMFI NAVAll.txt - " + " | ".join(problems))

    def history(self, scheme_code: int, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        params = {}
        if start:
            params["startDate"] = start.isoformat()
        if end:
            params["endDate"] = end.isoformat()
        body = self._call(f"{BASE}/mf/{scheme_code}", params or None).json()
        rows = body.get("data") or []
        df = pd.DataFrame(rows, columns=["date", "nav"])
        df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        return df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)


def save_csv(df: pd.DataFrame, isin: str, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{isin}.csv")
    if os.path.exists(path):                       # merge with what an earlier fetch already saved
        old = pd.read_csv(path, parse_dates=["date"])
        df = pd.concat([old, df]).drop_duplicates("date", keep="last").sort_values("date")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False)
    return path


def fetch_missing(plan: pd.DataFrame, folder: str, api: MfApi | None = None, dry_run: bool = False,
                  today: date | None = None) -> pd.DataFrame:
    api = api or MfApi()
    today = today or date.today()
    todo = plan[plan["status"].isin(["missing", "partial"])]
    report, navall = [], None
    for r in todo.itertuples():
        start = (r.have_to + timedelta(days=1)) if r.status == "partial" and pd.notna(r.have_to) \
            else r.need_from - timedelta(days=10)
        end = min(r.need_to + timedelta(days=3), today)
        entry = {"scheme_name": r.scheme_name, "isin": r.isin, "from": start, "to": end, "result": ""}
        if dry_run:
            entry["result"] = "dry run - nothing requested"
        else:
            try:
                if navall is None:
                    navall = api.navall()
                code = isin_to_scheme_code(navall, r.isin)
                if code is None:
                    raise LookupError("ISIN not found in AMFI NAVAll.txt")
                df = api.history(code, start, end)
                if df.empty:
                    raise LookupError(f"MFapi returned no NAVs for scheme code {code}")
                entry["result"] = f"saved {len(df)} NAVs (scheme code {code}) -> {os.path.basename(save_csv(df, r.isin, folder))}"
                time.sleep(api.sleep)
            except Exception as e:
                entry["result"] = f"FAILED: {e}"
        report.append(entry)
    return pd.DataFrame(report)