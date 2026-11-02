"""Daily NAV history per scheme, read from a folder of files.

Accepted files (any mix):
  * `<ISIN>.csv` written by `nav fetch`                      columns: date, nav
  * CSV/XLSX with Date + NAV columns (optional ISIN column; or an ISIN / the fund name in the file name)
  * AMFI NAV-history text downloads (.txt), which may contain many schemes
A scheme is matched by ISIN first, then by the `nav_file` cell in the Schemes sheet, then by fund name.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pandas as pd

from ..util import norm_name
from .amfi import looks_like_history, parse_history

ISIN_RE = re.compile(r"INF[A-Z0-9]{9}")
NAV_COLS = ["nav", "netassetvalue", "close", "value", "price"]
DATE_FORMATS = ["%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%b %d, %Y"]


def _norm_col(c) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower().replace("\ufeff", ""))


def _parse_dates(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    best, n = None, -1
    for fmt in DATE_FORMATS:
        p = pd.to_datetime(s, format=fmt, errors="coerce")
        if p.notna().sum() > n:
            best, n = p, p.notna().sum()
    return best.dt.normalize()


@dataclass
class NavFile:
    path: str
    isin: str | None
    name_hint: str
    series: pd.Series


@dataclass
class NavStore:
    folder: str
    files: list[NavFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.scan()

    # ---------------------------------------------------------------- reading
    def scan(self) -> None:
        self.files, self.errors, self.warnings = [], [], []
        if not os.path.isdir(self.folder):
            return
        for fn in sorted(os.listdir(self.folder)):
            path = os.path.join(self.folder, fn)
            if fn.startswith(("~", ".", "_")) or not fn.lower().endswith((".csv", ".xlsx", ".xls", ".txt")):
                continue
            try:
                self.files += self._read(path)
            except Exception as e:
                self.errors.append(f"{fn}: {e}")

    def _read(self, path: str) -> list[NavFile]:
        stem = os.path.splitext(os.path.basename(path))[0]
        if path.lower().endswith(".txt"):
            text = open(path, encoding="utf-8-sig", errors="replace").read()
            if not looks_like_history(text):
                raise ValueError("text file is not an AMFI NAV history download")
            df = parse_history(text)
            out = []
            for isin, g in df.groupby("isin_growth", dropna=True):
                s = pd.Series(g["nav"].values, index=g["date"].values).sort_index()
                out.append(NavFile(path, isin, g["scheme_name"].iloc[0], s))
            if not out:
                raise ValueError("no schemes with a growth ISIN found")
            return out
        raw = pd.read_excel(path) if path.lower().endswith((".xlsx", ".xls")) else \
            pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
        cols = {_norm_col(c): c for c in raw.columns}
        if "date" not in cols:
            raise ValueError(f"no Date column (found {list(raw.columns)})")
        vcol = next((cols[n] for n in NAV_COLS if n in cols), None)
        if vcol is None:
            raise ValueError(f"no NAV column (found {list(raw.columns)})")
        dates = _parse_dates(raw[cols["date"]])
        vals = pd.to_numeric(raw[vcol].astype(str).str.replace(",", ""), errors="coerce")
        base = pd.Series(vals.values, index=dates.values).dropna()
        base = base[~base.index.isna()]
        if base.empty:
            raise ValueError("no usable rows")
        if "isin" in cols and raw[cols["isin"]].notna().any():
            out = []
            for isin, idx in raw.groupby(raw[cols["isin"]].astype(str).str.strip()).groups.items():
                s = pd.Series(vals.loc[idx].values, index=dates.loc[idx].values).dropna()
                out.append(NavFile(path, isin if ISIN_RE.fullmatch(isin) else None, stem, s.sort_index()))
            return out
        m = ISIN_RE.search(stem.upper())
        return [NavFile(path, m.group(0) if m else None, stem, base.sort_index())]

    # ---------------------------------------------------------------- queries
    def series_for(self, isin: str | None, scheme_name: str = "", nav_file: str | None = None) -> pd.Series | None:
        parts = []
        if nav_file:
            p = nav_file if os.path.isabs(nav_file) else os.path.join(self.folder, nav_file)
            parts = [f.series for f in self.files if os.path.abspath(f.path) == os.path.abspath(p)]
        if not parts and isin:
            parts = [f.series for f in self.files if f.isin == isin]
        if not parts and scheme_name:
            want = norm_name(scheme_name)
            parts = [f.series for f in self.files if f.isin is None and norm_name(f.name_hint)
                     and (norm_name(f.name_hint) in want or want in norm_name(f.name_hint))]
        if not parts:
            return None
        merged = pd.concat(parts)
        dup = merged.index.duplicated(keep=False)
        if dup.any():
            g = merged[dup].groupby(level=0)
            bad = ((g.max() - g.min()) / g.min() > 1e-4).sum()
            if bad:
                self.warnings.append(f"{isin or scheme_name}: {int(bad)} overlapping NAV dates disagree between files.")
        return merged[~merged.index.duplicated(keep="last")].sort_index()

    def catalog(self) -> pd.DataFrame:
        rows = []
        for f in self.files:
            rows.append({"file": os.path.basename(f.path), "isin": f.isin, "name_hint": f.name_hint,
                         "from": f.series.index.min().date(), "to": f.series.index.max().date(),
                         "rows": len(f.series)})
        return pd.DataFrame(rows)


def check_against_transactions(txn: pd.DataFrame, nav: pd.Series, rel_tol: float = 6e-4) -> dict:
    """Does the NAV history reproduce the prices in the statement on the transaction dates?"""
    t = txn[txn["category"].isin(["investment", "redemption"])]
    n_ok, bad = 0, []
    for r in t.itertuples():
        v = nav.get(pd.Timestamp(r.date))
        if v is None or pd.isna(v):
            bad.append((r.date, r.price, None))
        elif abs(v - r.price) / r.price <= rel_tol:
            n_ok += 1
        else:
            bad.append((r.date, r.price, float(v)))
    return {"checked": len(t), "matched": n_ok, "mismatches": bad}
