"""Small parsing helpers shared by all parsers."""
from __future__ import annotations

import re
from datetime import date, datetime

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def num(text) -> float | None:
    """'4,40,000.00' -> 440000.0 ; '(1,234.50)' -> -1234.5 ; '-' / '' -> None."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("%", "").replace("\u20b9", "").strip()
    if s in ("", "-", "--", "NA", "N/A"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_date(text) -> date | None:
    """Accepts dd/mm/yyyy, dd-mm-yyyy, 'dd Mon yyyy', 'dd-Mon-yyyy'."""
    if text is None:
        return None
    s = str(text).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def mask_pan(pan: str | None) -> str | None:
    """Keep only the 4 digits of a PAN: ABCDE1234F -> *****1234*."""
    if not pan or len(pan) != 10:
        return None
    return "*****" + pan[5:9] + "*"


def norm_name(name: str) -> str:
    """Lower-case alphanumerics only; used to match a scheme name across documents."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def infer_plan_option(scheme_name: str) -> tuple[str, str]:
    n = scheme_name.lower()
    plan = "Direct" if "direct" in n else ("Regular" if re.search(r"\breg(ular)?\b", n) else "Unknown")
    if "(g)" in n or "growth" in n:
        option = "Growth"
    elif "idcw" in n or "dividend" in n or "(d)" in n:
        option = "IDCW"
    else:
        option = "Unknown"
    return plan, option
