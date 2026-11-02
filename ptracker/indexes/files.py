"""Read index history files from a folder (CSV / XLSX), stitch overlapping downloads, report coverage.

NSE limits each manual download to about a year, so one index usually arrives as several files.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pandas as pd

from .spec import IndexSpec, parse_benchmark

DATE_FORMATS = ["%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%b %d, %Y"]
TRI_COLS = ["totalreturnsindex", "totalreturnindex", "tri", "totalreturnsindexvalue", "totalreturnindexvalue",
            "trivalue"]
NTR_COLS = ["ntrvalue", "ntr", "nettotalreturnsindex"]
PRICE_COLS = ["close", "closingindexvalue", "indexvalue", "value"]
_RANGE_SUFFIX = re.compile(r"[-_ ]*\d{2}[-_]\d{2}[-_]\d{4}[-_ ]*to[-_ ]*\d{2}[-_]\d{2}[-_]\d{4}.*$", re.I)


def _norm_col(c) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower().replace("\ufeff", ""))


def _parse_dates(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    best, best_n = None, -1
    for fmt in DATE_FORMATS:
        p = pd.to_datetime(s, format=fmt, errors="coerce")
        if p.notna().sum() > best_n:
            best, best_n = p, p.notna().sum()
    return best.dt.normalize()


def _read_any(path: str) -> pd.DataFrame:
    if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)


def _name_from_filename(stem: str) -> str:
    s = _RANGE_SUFFIX.sub("", stem)
    return re.sub(r"[_]+", " ", s).strip()


@dataclass
class IndexFile:
    path: str
    spec: IndexSpec
    series: pd.Series
    value_column: str
    warnings: list[str] = field(default_factory=list)


def read_index_file(path: str) -> IndexFile:
    df = _read_any(path)
    cols = {_norm_col(c): c for c in df.columns}
    if "date" not in cols:
        raise ValueError(f"{os.path.basename(path)}: no Date column (found {list(df.columns)})")
    warns: list[str] = []
    variant, vcol = None, None
    for names, var in ((TRI_COLS, "TRI"), (PRICE_COLS, "PRICE")):
        hit = next((cols[n] for n in names if n in cols), None)
        if hit:
            variant, vcol = var, hit
            break
    if vcol is None:  # fuzzy: any column that mentions 'total return'
        hit = next((orig for norm, orig in cols.items() if "totalreturn" in norm), None)
        if hit:
            variant, vcol = "TRI", hit
    if vcol is None:
        raise ValueError(f"{os.path.basename(path)}: no recognisable value column (found {list(df.columns)})")
    # name: prefer an 'Index Name' column, else the file name
    name = None
    if "indexname" in cols and df[cols["indexname"]].notna().any():
        name = str(df[cols["indexname"]].dropna().iloc[0])
    stem = os.path.splitext(os.path.basename(path))[0]
    spec = parse_benchmark(name or _name_from_filename(stem))
    # an explicit TRI/NTR token in the file name wins over a missing one in the contents
    if spec.variant == "TRI" and variant == "PRICE":
        # the file name says TRI but its only value column is generic ('Close'); trust the explicit name
        variant = "TRI"
        warns.append(f"{os.path.basename(path)}: file name says TRI, value column '{vcol}' is generic - "
                     f"treated as TRI. Check it with `indexes inspect`.")
    spec = spec.as_variant(variant)
    dates = _parse_dates(df[cols["date"]])
    vals = pd.to_numeric(df[vcol].astype(str).str.replace(",", ""), errors="coerce")
    s = pd.Series(vals.values, index=dates.values, name=spec.key).dropna()
    s = s[~s.index.isna()]
    if s.empty:
        raise ValueError(f"{os.path.basename(path)}: no usable rows after parsing")
    return IndexFile(path, spec, s.sort_index(), vcol, warns)


class IndexStore:
    """All index files in a folder, grouped by (index, variant) and stitched."""

    def __init__(self, folder: str):
        self.folder = folder
        self.files: list[IndexFile] = []
        self.errors: list[str] = []
        self._series: dict[str, pd.Series] = {}
        self.warnings: list[str] = []
        self.scan()

    def scan(self) -> None:
        self.files, self.errors, self.warnings, self._series = [], [], [], {}
        if not os.path.isdir(self.folder):
            return
        for fn in sorted(os.listdir(self.folder)):
            if fn.startswith(("~", ".", "_")) or not fn.lower().endswith((".csv", ".xlsx", ".xls", ".xlsm")):
                continue
            try:
                f = read_index_file(os.path.join(self.folder, fn))
                self.files.append(f)
                self.warnings += f.warnings
            except Exception as e:  # keep going; report at the end
                self.errors.append(f"{fn}: {e}")
        groups: dict[str, list[IndexFile]] = {}
        for f in self.files:
            groups.setdefault(f.spec.key, []).append(f)
        for key, fl in groups.items():
            merged = pd.concat([f.series for f in fl])
            dup = merged.index.duplicated(keep=False)
            if dup.any():
                g = merged[dup].groupby(level=0)
                spread = (g.max() - g.min()) / g.min()
                bad = spread[spread > 1e-4]
                if len(bad):
                    self.warnings.append(f"{key}: {len(bad)} overlapping dates disagree between files "
                                         f"(first: {bad.index[0].date()}); later file used.")
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            self._series[key] = merged

    # -------------------------------------------------------------- queries
    def has(self, spec: IndexSpec) -> bool:
        return spec.key in self._series

    def series(self, spec: IndexSpec) -> pd.Series:
        return self._series[spec.key]

    def catalog(self) -> pd.DataFrame:
        rows = []
        for key, s in self._series.items():
            base, variant = key.split("|")
            rows.append({"index": base, "variant": variant, "from": s.index.min().date(),
                         "to": s.index.max().date(), "rows": len(s),
                         "files": sum(1 for f in self.files if f.spec.key == key),
                         "largest_gap_days": self.coverage_stats(s)["largest_gap_days"]})
        return pd.DataFrame(rows)

    @staticmethod
    def coverage_stats(s: pd.Series) -> dict:
        gaps = s.index.to_series().diff().dt.days.dropna()
        return {"largest_gap_days": int(gaps.max()) if len(gaps) else 0}

    def value_on_or_after(self, spec: IndexSpec, day) -> tuple[pd.Timestamp, float]:
        s = self.series(spec)
        s2 = s[s.index >= pd.Timestamp(day)]
        return s2.index[0], float(s2.iloc[0])