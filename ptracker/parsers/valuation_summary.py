"""Parser for the distributor 'Portfolio Valuation Summary' PDF.

Extracts the scheme-level valuation table, allocation tables (AMC, category, cap, sector,
top holdings, debt papers and ratings), folio details and the SIP summary. Personal data
(address, phone, e-mail, bank, nominee) is deliberately NOT captured.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd
import pdfplumber

from ..util import mask_pan, num, parse_date

N = r"-?[\d,]+(?:\.\d+)?"
AS_ON = re.compile(r"^As on\s+(?P<d>\d{2}-\d{2}-\d{4})")
SNAP_KEYS = {
    "Investment (A)": "investment", "Switch In (B)": "switch_in", "Switch Out (C)": "switch_out",
    "Redemption (D)": "redemption", "Div. Payout/Interest (E)": "dividend_payout",
    "Net Investment (F = A+B-C-D-E)": "net_investment", "Current Value (G)": "current_value",
    "Overall Gain (H = G-F)": "overall_gain",
}
ROW3 = re.compile(rf"^(?P<label>.+?)\s+(?P<val>{N})\s+(?P<pct>{N})$")
ROW_PERF = re.compile(
    rf"^(?P<label>.+?)\s+(?P<purchase>{N})\s+(?P<current>{N})\s+(?P<abs>{N})\s+(?P<cagr>{N})\s+(?P<alloc>{N})$")
HOLD_NUMS = re.compile(
    rf"^(?P<units>[\d,]+\.\d{{3,4}})\s+(?P<avg_nav>[\d.]+)\s+(?P<pv>[\d,]+\.\d{{2}})\s+(?P<nav>[\d.]+)\s+"
    rf"(?P<cv>[\d,]+\.\d{{2}})\s+(?P<div>[\d,]+\.\d{{2}})\s+(?P<gain>{N})\s+(?P<abs>{N})\s+"
    rf"(?P<days>\d+)\s+(?P<cagr>{N})$")
FOLIO_ROW = re.compile(
    r"^(?P<name>.+?)\s+(?P<folio>[\dA-Za-z/]+)\s+(?P<ftype>Physical|Demat|Electronic)\s+(?P<aum>[\d,]+)")
SIP_ROW = re.compile(r"^(?P<name>.+?)\s+(?P<folio>[\dA-Za-z/]+)\s+(?P<vals>(?:[\d,]+\s*){2,})$")


@dataclass
class ParsedValuation:
    as_on: object = None
    pan_masked: str | None = None
    snapshot: dict = field(default_factory=dict)
    by_fund_house: pd.DataFrame = field(default_factory=pd.DataFrame)
    by_scheme: pd.DataFrame = field(default_factory=pd.DataFrame)
    by_subcategory: pd.DataFrame = field(default_factory=pd.DataFrame)
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    folios: pd.DataFrame = field(default_factory=pd.DataFrame)
    sip_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    allocations: pd.DataFrame = field(default_factory=pd.DataFrame)  # long form
    warnings: list[str] = field(default_factory=list)


def _page_texts(pdf_path: str, password=None):
    pages = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for p in pdf.pages:
            full = p.extract_text() or ""
            # Split the "Holding Analysis" page into left/right halves at the 2nd table header.
            halves = None
            words = p.extract_words()
            debt_hdr = [w for w in words if w["text"] == "Debt"]
            if "Holding Analysis" in full and debt_hdr:
                x = min(w["x0"] for w in debt_hdr) - 3
                left = p.crop((0, 0, x, p.height)).extract_text() or ""
                right = p.crop((x, 0, p.width, p.height)).extract_text() or ""
                halves = (left, right)
            pages.append((full, halves))
    return pages


def _perf_table(lines, start_hdr) -> pd.DataFrame:
    rows, on = [], False
    for ln in lines:
        if ln.startswith(start_hdr):
            on = True
            continue
        if not on:
            continue
        if ln.startswith("Total"):
            break
        if (m := ROW_PERF.match(ln)):
            rows.append({"name": m["label"], "purchase_value": num(m["purchase"]),
                         "current_value": num(m["current"]), "abs_return_pct": num(m["abs"]),
                         "cagr_pct": num(m["cagr"]), "allocation_pct": num(m["alloc"])})
    return pd.DataFrame(rows)


def _simple_table(text, header_prefix, section) -> list[dict]:
    rows, on = [], False
    for raw in text.splitlines():
        ln = " ".join(raw.split())
        if ln.startswith(header_prefix):
            on = True
            continue
        if not on:
            continue
        if ln.startswith("Total") or ln.startswith("Page "):
            break
        if (m := ROW3.match(ln)):
            rows.append({"section": section, "label": m["label"], "value": num(m["val"]),
                         "pct": num(m["pct"])})
        elif ln and not re.match(rf".*{N}$", ln):
            break  # next table heading
    return rows


def _half_tables(half_text: str) -> list[dict]:
    """Parse the stacked tables of one half of the Holding Analysis page."""
    rows, section = [], None
    for raw in half_text.splitlines():
        ln = " ".join(raw.split())
        if re.search(r"Allocation by .*\(Top 10\)", ln):
            section = ln.split("Current Value")[0].strip()
            continue
        if section and (m := ROW3.match(ln)):
            rows.append({"section": section, "label": m["label"], "value": num(m["val"]),
                         "pct": num(m["pct"])})
    return rows


def parse_pdf(pdf_path: str, password: str | None = None) -> ParsedValuation:
    out = ParsedValuation()
    pages = _page_texts(pdf_path, password)
    all_lines = [" ".join(l.split()) for full, _ in pages for l in full.splitlines()]

    # --- header facts -------------------------------------------------------
    for ln in all_lines:
        if (m := AS_ON.match(ln)) and out.as_on is None:
            out.as_on = parse_date(m["d"])
        if (m := re.search(r"PAN\s*:\s*([A-Z]{5}\d{4}[A-Z])", ln)) and out.pan_masked is None:
            out.pan_masked = mask_pan(m.group(1))
        if (m := re.match(r"^Lifetime XIRR\s+(" + N + r")\s*%", ln)):
            out.snapshot["lifetime_xirr_pct"] = num(m.group(1))
        for key, field_name in SNAP_KEYS.items():
            if ln.startswith(key):
                out.snapshot[field_name] = num(ln[len(key):])
        if (m := re.match(r"^Investment snapshot since (\d{2}-\d{2}-\d{4})", ln)):
            out.snapshot["since"] = parse_date(m.group(1))
        if (m := re.match(r"^(S&P BSE SENSEX|NSE NIFTY) \((\d{2}-\d{2}-\d{4})\)\s+([\d,.]+)", ln)):
            out.snapshot[("sensex" if "SENSEX" in m.group(1) else "nifty50") + "_level"] = num(m.group(3))
            out.snapshot["index_level_date"] = parse_date(m.group(2))

    # --- performance tables ---------------------------------------------------
    out.by_fund_house = _perf_table(all_lines, "Fund Purchase Value").rename(columns={"name": "fund_house"})
    out.by_scheme = _perf_table(all_lines, "Scheme Purchase Value").rename(columns={"name": "scheme_name"})
    out.by_subcategory = _perf_table(all_lines, "Sub Category Purchase Value").rename(
        columns={"name": "sub_category"})

    # --- allocations (long form) ---------------------------------------------
    alloc: list[dict] = []
    full_text = "\n".join(full for full, _ in pages)
    alloc += _simple_table(full_text, "Cap Current Value", "Equity cap allocation")
    for full, halves in pages:
        if halves:
            for half in halves:
                alloc += _half_tables(half)
    out.allocations = pd.DataFrame(alloc)

    # --- scheme holdings table (names wrap onto several lines) ---------------
    holdings, folios, sip = [], [], []
    category, pending, in_hold, in_folio, in_sip, sip_months = None, [], False, False, False, []
    for ln in all_lines:
        if ln.startswith("Folio Details"):
            in_hold, in_folio, in_sip = False, True, False
            continue
        if ln.startswith("SIP Summary"):
            in_hold, in_folio, in_sip = False, False, True
            continue
        if ln.startswith("Scheme Balance Units") or ln == "Mutual Fund":
            in_hold = in_hold or ln == "Mutual Fund"
        if ln.startswith("Scheme Folio Jul") or re.match(r"^Scheme Folio [A-Z][a-z]{2}'", ln):
            sip_months = re.findall(r"[A-Z][a-z]{2}'\s?\d{2}", ln)
            continue
        if in_folio and (m := FOLIO_ROW.match(ln)):
            rest = ln[m.end():]
            holding = "Single" if "Single" in rest else ("Joint" if "Joint" in rest else None)
            tax = "Individual" if "Individual" in rest else None
            folios.append({"scheme_name": m["name"], "folio": m["folio"], "folio_type": m["ftype"],
                           "aum": num(m["aum"]), "tax_status": tax, "holding_mode": holding})
            continue
        if in_sip and sip_months and (m := SIP_ROW.match(ln)) and "Total" not in ln:
            vals = [num(v) for v in m["vals"].split()]
            if len(vals) == len(sip_months):
                for mo, v in zip(sip_months, vals):
                    sip.append({"scheme_name": m["name"], "folio": m["folio"],
                                "month": mo.replace(" ", ""), "amount": v})
            continue
        if in_hold:
            if ln.startswith("Equity:") and "Total" not in ln or re.match(r"^(Debt|Hybrid|Other|Gold)[A-Za-z: ]*$", ln):
                category = ln
                pending = []
                continue
            if "Total" in ln or ln.startswith("Grand Total"):
                pending = []
                continue
            m = re.match(r"^(?P<name>.+?)\s*\[(?P<folio>[^\]]+)\]\s*(?P<rest>.*)$", ln)
            if m and m["rest"] and HOLD_NUMS.match(m["rest"]):  # all on one line
                nums, name, folio = HOLD_NUMS.match(m["rest"]), " ".join(pending + [m["name"]]), m["folio"]
            elif HOLD_NUMS.match(ln):  # numbers only; name before, folio on next line
                nums, name, folio = HOLD_NUMS.match(ln), " ".join(pending), None
            elif folio_only := re.match(r"^\[(?P<f>[^\]]+)\]$", ln):
                if holdings and holdings[-1]["folio"] is None:
                    holdings[-1]["folio"] = folio_only["f"]
                continue
            else:
                if not ln.startswith(("SHIVAM", "Avg", "Scheme", "Purchase", "NAV", "Days", "Abs", "(%)")) \
                        and "(PAN" not in ln and ln not in ("Mutual Fund", "Holding"):
                    pending.append(ln)
                continue
            holdings.append({
                "scheme_name": name.strip(), "folio": folio, "category": category,
                "balance_units": num(nums["units"]), "avg_purchase_nav": num(nums["avg_nav"]),
                "purchase_value": num(nums["pv"]), "current_nav": num(nums["nav"]),
                "current_value": num(nums["cv"]), "dividend": num(nums["div"]),
                "gain": num(nums["gain"]), "abs_return_pct": num(nums["abs"]),
                "avg_holding_days": int(nums["days"]), "cagr_pct": num(nums["cagr"])})
            pending = []

    out.holdings = pd.DataFrame(holdings)
    out.folios = pd.DataFrame(folios)
    out.sip_summary = pd.DataFrame(sip)
    if out.holdings.empty:
        out.warnings.append("No scheme holdings table could be parsed from the valuation summary.")
    return out
