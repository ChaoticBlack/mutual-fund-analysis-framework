"""OPTIONAL downloader for NSE Total Returns Index history from niftyindices.com.

Important caveats (be honest about them in the UI as well):
  * This uses the website's own *undocumented* XHR endpoint. It can change or start blocking without notice.
  * It has NOT been exercised against the live service from the development sandbox (no internet there);
    the parsing and request logic are unit-tested against recorded-format responses only.
  * It is polite by design: only missing date ranges are requested, 1 call per second, retries with backoff.
  * Anything it downloads is saved as ordinary CSV in the index folder, so the manual route always works.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd

from .spec import IndexSpec, nse_name_candidates

URL = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
PAGE = "https://www.niftyindices.com/reports/historical-data"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 ptracker/0.2"
# NSE ignores non-browser user agents by stalling, so the UA must start with 'Mozilla/5.0'.


class IndexNotFound(Exception):
    pass


def _default_post(url, headers, data, timeout):
    import requests  # imported lazily so the rest of the package works without it
    return requests.post(url, headers=headers, data=data, timeout=timeout)


class NseFetcher:
    def __init__(self, post=None, sleep: float = 1.0, max_retries: int = 4, timeout: float = 60.0,
                 chunk_days: int = 365, log=print):
        self._post = post or _default_post
        self.sleep, self.max_retries, self.timeout, self.chunk_days, self.log = sleep, max_retries, timeout, chunk_days, log

    # ------------------------------------------------------------------ one request
    def _request(self, name: str, start: date, end: date) -> list[dict]:
        fmt = lambda d: d.strftime("%d-%b-%Y")
        body = {"cinfo": "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
                         % (name, fmt(start), fmt(end), name)}
        headers = {"Content-Type": "application/json; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
                   "Referer": PAGE, "User-Agent": UA}
        delay, last = 2.0, None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._post(URL, headers, json.dumps(body), self.timeout)
                resp.raise_for_status()
                return json.loads(resp.json()["d"])
            except Exception as e:                      # network, 403, malformed JSON...
                last = e
                if attempt < self.max_retries:
                    self.log(f"  request failed ({e}); retrying in {delay:.0f}s")
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(f"NSE request failed after {self.max_retries + 1} attempts: {last}")

    # ------------------------------------------------------------------ public API
    def fetch(self, spec: IndexSpec, start: date, end: date) -> pd.DataFrame:
        """Return ascending daily TRI rows [Index Name, Date, TotalReturnsIndex, NTR_Value]."""
        if spec.variant != "TRI" or spec.source != "NSE":
            raise ValueError(f"Only NSE TRI series can be fetched (asked for {spec.label}, {spec.source}).")
        tried = []
        for name in nse_name_candidates(spec.base):
            tried.append(name)
            rows: list[dict] = []
            cur = start
            while cur <= end:
                chunk_end = min(cur + timedelta(days=self.chunk_days - 1), end)
                rows += self._request(name, cur, chunk_end)
                cur = chunk_end + timedelta(days=1)
                if cur <= end:
                    time.sleep(self.sleep)
            if rows:
                df = pd.DataFrame(rows)
                out = pd.DataFrame({
                    "Index Name": name,
                    "Date": pd.to_datetime(df["Date"], format="%d %b %Y", errors="coerce"),
                    "TotalReturnsIndex": pd.to_numeric(df["TotalReturnsIndex"], errors="coerce"),
                    "NTR_Value": pd.to_numeric(df.get("NTR_Value"), errors="coerce")})
                return out.dropna(subset=["Date", "TotalReturnsIndex"]).drop_duplicates("Date") \
                          .sort_values("Date").reset_index(drop=True)
            time.sleep(self.sleep)
        raise IndexNotFound(f"NSE returned no data for any of these spellings: {tried}. "
                            f"Check the exact name on niftyindices.com or download the file manually.")


def save_csv(df: pd.DataFrame, spec: IndexSpec, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    a, b = df["Date"].min().date(), df["Date"].max().date()
    path = os.path.join(folder, f"{spec.slug}-{a:%d-%m-%Y}-to-{b:%d-%m-%Y}.csv")
    out = df.copy()
    out["Date"] = out["Date"].dt.strftime("%d-%b-%Y")
    out.to_csv(path, index=False)
    return path


def fetch_missing(plan: pd.DataFrame, folder: str, fetcher: NseFetcher | None = None,
                  dry_run: bool = False, today: date | None = None) -> pd.DataFrame:
    """Download only what the plan says is missing/partial. Returns a report."""
    from .spec import parse_benchmark
    fetcher = fetcher or NseFetcher()
    today = today or date.today()
    report = []
    for r in plan[plan["fetchable"]].itertuples():
        spec = parse_benchmark(r.benchmark)
        # only ask for the part we do not already hold, plus a small buffer for holidays
        if r.status == "partial" and pd.notna(r.have_to):
            start = max(r.have_to + timedelta(days=1), r.need_from)
        else:
            start = r.need_from - timedelta(days=10)
        end = min(r.need_to + timedelta(days=3), today)
        entry = {"benchmark": r.benchmark, "from": start, "to": end, "result": ""}
        if dry_run:
            entry["result"] = "dry run - nothing requested"
        else:
            try:
                df = fetcher.fetch(spec, start, end)
                entry["result"] = f"saved {len(df)} rows -> {os.path.basename(save_csv(df, spec, folder))}"
            except Exception as e:
                entry["result"] = f"FAILED: {e}"
        report.append(entry)
    return pd.DataFrame(report)
