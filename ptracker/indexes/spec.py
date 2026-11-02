"""Turn a benchmark name as printed on a factsheet into a normalised index specification."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexSpec:
    base: str        # 'NIFTY INDIA MANUFACTURING'
    variant: str     # 'TRI' | 'NTR' | 'PRICE'
    source: str      # 'NSE' | 'BSE' | 'OTHER'
    raw: str = ""
    assumed_tri: bool = False   # factsheet name did not say TRI; we assume it (SEBI requires TRI benchmarks)

    @property
    def key(self) -> str:
        return f"{self.base}|{self.variant}"

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", self.base).strip("_") + f"_{self.variant}"

    @property
    def label(self) -> str:
        return f"{self.base} {self.variant}".strip()

    def as_variant(self, variant: str) -> "IndexSpec":
        return IndexSpec(self.base, variant, self.source, self.raw, self.assumed_tri)


_NTR = re.compile(r"\b(NTR|NET TOTAL RETURNS?( INDEX)?)\b")
_TRI = re.compile(r"\b(TRI|TR|TOTAL RETURNS?( INDEX)?)\b")


def parse_benchmark(name: str, default_variant: str = "PRICE") -> IndexSpec:
    """'NSE - Nifty India Manufacturing TRI' -> IndexSpec('NIFTY INDIA MANUFACTURING','TRI','NSE')."""
    raw = (name or "").strip()
    s = re.sub(r"\s+", " ", raw.upper().replace("\u2013", "-"))
    source = "NSE"
    if re.match(r"^(S&P\s*)?BSE\b", s) or s.startswith("S&P BSE"):
        source = "BSE"
    elif re.match(r"^(NSE\s*-\s*)", s):
        s = re.sub(r"^NSE\s*-\s*", "", s)
    if not s.startswith(("NIFTY", "S&P BSE", "BSE")) and source == "NSE":
        source = "OTHER"          # e.g. CRISIL / MSCI / Nifty-less names
    variant, assumed = "PRICE", False
    if _NTR.search(s):
        variant = "NTR"
    elif _TRI.search(s):
        variant = "TRI"
    elif default_variant != "PRICE":
        variant, assumed = default_variant, True
    s = _NTR.sub("", s)
    s = _TRI.sub("", s)
    s = re.sub(r"[()\-]+", " ", s)
    s = re.sub(r"\bINDEX\b$", "", s.strip())
    s = re.sub(r"\s+", " ", s).strip()
    return IndexSpec(base=s, variant=variant, source=source, raw=raw, assumed_tri=assumed)


def nse_name_candidates(base: str) -> list[str]:
    """NSE spells some names 'NIFTY 100 ...' and others 'NIFTY100 ...'; a wrong spelling returns [].

    Returns the spellings worth trying, most likely first.
    """
    out = [base]
    m = re.match(r"^NIFTY (\d+) (.+)$", base)
    if m:                                    # strategy indices are compact: NIFTY100 LOW VOLATILITY 30
        out.append(f"NIFTY{m.group(1)} {m.group(2)}")
    m = re.match(r"^NIFTY(\d+) (.+)$", base)
    if m:
        out.append(f"NIFTY {m.group(1)} {m.group(2)}")
    return list(dict.fromkeys(out))


def load_aliases(folder: str) -> dict[str, str]:
    """Optional `aliases.csv` (columns alias,canonical) in the index folder: lets you say that a
    benchmark printed as 'NIFTY CONSUMPTION' is the index you downloaded as 'NIFTY INDIA CONSUMPTION'."""
    import os
    import pandas as pd
    path = os.path.join(folder, "aliases.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    return {str(a).strip().upper(): str(c).strip().upper()
            for a, c in zip(df.get("alias", []), df.get("canonical", [])) if str(a).strip()}


def similar_bases(base: str, available: list[str]) -> list[str]:
    """Names in `available` that probably refer to the same index (token subset or close spelling)."""
    import difflib
    want = set(base.split())
    out = [b for b in available if b != base and (want <= set(b.split()) or set(b.split()) <= want)]
    out += [b for b in difflib.get_close_matches(base, available, n=3, cutoff=0.8) if b not in out and b != base]
    return out