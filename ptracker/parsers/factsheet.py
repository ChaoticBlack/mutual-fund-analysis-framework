"""Parser for single-scheme factsheet PDFs (distributor-branded, one scheme per file).

Layout assumptions (verified on the Invesco India Manufacturing Fund factsheet):
  * Left panel: scheme profile (launch date, NAV, exit load, expense ratio, benchmark, ...).
  * Centre: a two-column holdings table.
  * Right: trailing and calendar-year returns, scheme vs benchmark.
Anything missing is left as None and reported in `warnings`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd
import pdfplumber

from ..util import norm_name, num, parse_date

WEIGHT = re.compile(r"^-?\d+(?:\.\d+)?$")
SECTION_LABELS = {"current asset", "equity", "trep", "debt", "gold", "others", "other", "derivatives"}
SECTION_TYPE = {"current asset": "Current Asset", "equity": "Equity", "trep": "TREPS"}

LEFT_LABELS = [  # in order of appearance in the left panel
    "Investment Objective", "Launch Date", "Scheme Profile", "Corpus (Cr.)", "52 Week High",
    "Current NAV", "52 Week Low", "Exit Load", "Expense Ratio", "Taxation", "Category",
    "Benchmark", "Scheme Type", "Fund Manager(s)", "Volatility Measures", "Alpha", "Sharpe Ratio",
    "Std. Deviation", "Mean", "Beta", "Composition (%)", "Equity", "Debt", "Gold", "Global Equity",
    "Other", "Equity Cap Allocation (%)", "Large", "Mid", "Small", "Scheme Riskometer"]


@dataclass
class ParsedFactsheet:
    info: dict = field(default_factory=dict)
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    trailing: pd.DataFrame = field(default_factory=pd.DataFrame)
    calendar: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)


def _left_panel_fields(text: str) -> dict[str, str]:
    """Walk the panel's lines in order, assigning text to the most recent label."""
    fields: dict[str, str] = {}
    idx, current = 0, None
    lines = [" ".join(r.split()) for r in text.splitlines() if r.strip()]
    for pos, ln in enumerate(lines):
        # 'Exit Load' value is vertically centred, so its first line sits ABOVE the label.
        if ln == "Exit Load" and pos > 0 and re.match(r"^(\d+(\.\d+)?\s*%|Nil)", lines[pos - 1], re.I):
            fields["Exit Load"] = lines[pos - 1]
        if pos + 1 < len(lines) and re.match(r"^(\d+(\.\d+)?\s*%|Nil)", ln, re.I) \
                and lines[pos + 1].startswith("Exit Load"):
            continue  # consumed above
        matched = False
        if ln in fields.values() and current is None:
            continue
        # look forward only, so repeated words ('Equity') map to the right label
        for j in range(idx, len(LEFT_LABELS)):
            lab = LEFT_LABELS[j]
            if ln == lab or ln.startswith(lab + " "):
                current, idx, matched = lab, j + 1, True
                rest = ln[len(lab):].strip()
                if rest:
                    fields[current] = (fields.get(current, "") + " " + rest).strip()
                break
        if not matched and current:
            fields[current] = (fields.get(current, "") + " " + ln).strip()
    return fields


def _holdings(page, left_x1: float, section_y0: float, section_y1: float, as_on) -> pd.DataFrame:
    words = page.extract_words(keep_blank_chars=False)
    hdr = sorted([w for w in words if w["text"] == "Instrument"], key=lambda w: w["x0"])
    sect = sorted([w for w in words if w["text"] == "Sector/Rating"], key=lambda w: w["x0"])
    if len(hdr) < 2 or len(sect) < 2:
        return pd.DataFrame()
    rows: list[dict] = []
    for (ix, sx), x_lo, x_hi in zip(
            [(hdr[0]["x0"], sect[0]["x0"]), (hdr[1]["x0"], sect[1]["x0"])],
            [hdr[0]["x0"] - 2, hdr[1]["x0"] - 2], [hdr[1]["x0"] - 2, page.width]):
        col = [w for w in words if x_lo <= w["x0"] < x_hi and section_y0 <= w["top"] < section_y1]
        lines: dict[int, list] = {}
        for w in col:
            lines.setdefault(round(w["top"] / 3), []).append(w)  # tolerance ~3pt
        section, last = None, None
        for key in sorted(lines):
            ws = sorted(lines[key], key=lambda w: w["x0"])
            texts = [w["text"] for w in ws]
            if texts[:1] == ["Instrument"] or texts[:2] == ["Net", "Net"] or texts[:1] == ["Assets"]:
                continue
            if len(texts) >= 2 and texts[-1] == "%" and WEIGHT.match(texts[-2]):
                weight = float(texts[-2])
                body = ws[:-2]
                name = " ".join(w["text"] for w in body if w["x0"] < sx - 2)
                sector = " ".join(w["text"] for w in body if w["x0"] >= sx - 2)
                last = {"instrument": name, "sector_or_rating": sector, "weight_pct": weight,
                        "section": SECTION_TYPE.get((section or "").lower(), section)}
                rows.append(last)
            else:
                label = " ".join(texts)
                if label.lower() in SECTION_LABELS and ws[0]["x0"] < sx - 2:
                    section = label
                elif last is not None:  # wrapped continuation of previous row
                    name_part = " ".join(w["text"] for w in ws if w["x0"] < sx - 2)
                    sec_part = " ".join(w["text"] for w in ws if w["x0"] >= sx - 2)
                    if name_part:
                        last["instrument"] += " " + name_part
                    if sec_part:
                        last["sector_or_rating"] += " " + sec_part
    df = pd.DataFrame(rows)
    if not df.empty:
        df["as_on"] = as_on
    return df


def parse_pdf(pdf_path: str, password: str | None = None) -> ParsedFactsheet:
    out = ParsedFactsheet()
    with pdfplumber.open(pdf_path, password=password) as pdf:
        page = pdf.pages[0]
        full = page.extract_text() or ""
        words = page.extract_words()
        name = full.splitlines()[0].strip() if full else None

        inst_x = min((w["x0"] for w in words if w["text"] == "Instrument"), default=None)
        left_x1 = (inst_x - 3) if inst_x else page.width * 0.2
        left = page.crop((0, 0, left_x1, page.height)).extract_text() or ""
        f = _left_panel_fields(left)

        m = re.search(r"Portfolio Holdings \(as on (\d{1,2} \w{3} \d{4})\)", full)
        holdings_as_on = parse_date(m.group(1)) if m else None

        # vertical bands for the right-hand tables
        top_of = {w["text"]: w["top"] for w in words if w["text"] in ("Trailing", "Calender", "Cumulative")}
        trail_x0 = min((w["x0"] for w in words if w["text"] == "Period"), default=page.width * 0.65) - 6
        trailing_txt = calendar_txt = ""
        if "Trailing" in top_of and "Calender" in top_of:
            trailing_txt = page.crop((trail_x0, top_of["Trailing"] - 2, page.width,
                                      top_of["Calender"] - 2)).extract_text() or ""
        if "Calender" in top_of:
            end = top_of.get("Cumulative", page.height)
            calendar_txt = page.crop((trail_x0, top_of["Calender"] - 2, page.width, end - 2)).extract_text() or ""

        sect_y0 = min((w["top"] for w in words if w["text"] == "Instrument"), default=0) + 12
        sect_y1 = top_of.get("Trailing", page.height) - 5
        # holdings stop where the 'Sector Allocation' heading begins
        sa = [w["top"] for w in words if w["text"] == "Sector" and w["x0"] < page.width * 0.5
              and w["top"] > sect_y0 + 100]
        if sa:
            sect_y1 = min(sect_y1, min(sa) - 2)
        out.holdings = _holdings(page, left_x1, sect_y0, sect_y1, holdings_as_on)

    def first_num(s):
        mm = re.search(r"-?[\d,]+(?:\.\d+)?", s or "")
        return num(mm.group(0)) if mm else None

    nav_m = re.match(r"([\d.,]+)\s*\((\d{1,2} \w{3} \d{4})\)", f.get("Current NAV", ""))
    bench = f.get("Benchmark")
    out.info = {
        "scheme_name": name, "scheme_key_name": norm_name(name or ""),
        "launch_date": parse_date(f.get("Launch Date")), "corpus_cr": first_num(f.get("Corpus (Cr.)")),
        "factsheet_nav": num(nav_m.group(1)) if nav_m else None,
        "factsheet_nav_date": parse_date(nav_m.group(2)) if nav_m else None,
        "exit_load": f.get("Exit Load"), "expense_ratio_pct": first_num(f.get("Expense Ratio")),
        "taxation": f.get("Taxation"), "category": f.get("Category"),
        "benchmark_raw": bench,
        "benchmark_name": re.sub(r"^(NSE|BSE)\s*-\s*", "", bench).strip() if bench else None,
        "benchmark_key": norm_name(re.sub(r"^(NSE|BSE)\s*-\s*", "", bench)) if bench else None,
        "scheme_type": f.get("Scheme Type"), "fund_manager": f.get("Fund Manager(s)"),
        "equity_pct": first_num(f.get("Equity")), "debt_pct": first_num(f.get("Debt")),
        "large_cap_pct": first_num(f.get("Large")), "mid_cap_pct": first_num(f.get("Mid")),
        "small_cap_pct": first_num(f.get("Small")), "holdings_as_on": holdings_as_on,
    }
    for key in ("benchmark_name", "expense_ratio_pct", "launch_date", "factsheet_nav"):
        if out.info.get(key) is None:
            out.warnings.append(f"Could not read '{key}' from factsheet {name!r}.")

    tr = []
    for raw in trailing_txt.splitlines():
        mm = re.match(r"^(\d+)\s+(Month|Months|Year|Years)\s+(-|-?[\d.]+)\s+(-|-?[\d.]+)$", " ".join(raw.split()))
        if mm:
            months = int(mm.group(1)) * (12 if mm.group(2).startswith("Year") else 1)
            tr.append({"period": f"{mm.group(1)} {mm.group(2)}", "months": months,
                       "return_type": "absolute" if months < 12 else "cagr",
                       "scheme_pct": num(mm.group(3)), "benchmark_pct": num(mm.group(4))})
    out.trailing = pd.DataFrame(tr)
    cy = []
    for raw in calendar_txt.splitlines():
        mm = re.match(r"^(\d{4})\s+(-|-?[\d.]+)\s+(-|-?[\d.]+)$", " ".join(raw.split()))
        if mm:
            cy.append({"year": int(mm.group(1)), "scheme_pct": num(mm.group(2)),
                       "benchmark_pct": num(mm.group(3))})
    out.calendar = pd.DataFrame(cy)
    if out.holdings.empty:
        out.warnings.append("No holdings table could be parsed from the factsheet.")
    return out
