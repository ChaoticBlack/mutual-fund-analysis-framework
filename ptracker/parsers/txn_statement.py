"""Parser for the distributor 'Transaction Statement' PDF.

Produces one row per transaction plus scheme-level closing figures. Anything that
looks like a transaction line but does not match the expected layout is reported
in `warnings` instead of being silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd
import pdfplumber

from ..util import infer_plan_option, mask_pan, num, parse_date

_AMT = r"\(?-?[\d,]+\.\d{2}\)?"
_UNITS = r"\(?-?[\d,]+\.\d{2,4}\)?"

FUND_HDR = re.compile(
    r"^(?P<name>.+?)\s+(?P<isin>INF[A-Z0-9]{9})\s+\[(?P<folio>[^\]]+)\]\s*$")
OPENING = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+Opening Cost and Unit Balance\s+"
    r"(?P<cost>[\d,]+\.\d{2})\s+(?P<units>[\d,]+\.\d{2,4})\s*$")
TXN = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<nature>.+?)\s+(?P<amount>" + _AMT + r")\s+"
    r"(?P<price>[\d,]+\.\d{2,6})\s+(?P<units>" + _UNITS + r")\s+(?P<balance>[\d,]+\.\d{2,4})\s+"
    r"(?P<stamp>[\d,]+\.\d{2})\s+(?P<tds>[\d,]+\.\d{2})\s+(?P<stt>[\d,]+\.\d{2})\s+"
    r"(?P<total>" + _AMT + r")\s*$")
FOOTER = re.compile(
    r"^NAV\s*:\s*(?P<nav>[\d,]+\.\d+)\s+as on\s+(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"Purchase Outstanding\s*:\s*(?P<po>[\d,]+\.\d{2})\s+Market Value\s*:\s*(?P<mv>[\d,]+\.\d{2})")
GRAND = re.compile(
    r"^Grand Total\s*:\s*Purchase Outstanding\s*:\s*(?P<po>[\d,]+\.\d{2})\s+"
    r"Market Value\s*:\s*(?P<mv>[\d,]+\.\d{2})")
INVESTOR = re.compile(r"^(?P<name>[A-Z][A-Z .'-]+?)\s+\(PAN\s*:\s*(?P<pan>[A-Z]{5}\d{4}[A-Z])\)")
PERIOD = re.compile(r"From\s+(?P<a>\d{2}/\d{2}/\d{4})\s+to\s+(?P<b>\d{2}/\d{2}/\d{4})")
STARTS_WITH_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}\s")

# Order matters: first match wins.
_CATEGORY_RULES = [
    ("redemption", ("SWITCH OUT", "STP OUT", "SWP", "REDEMPTION", "REDEEM")),
    ("investment", ("SWITCH IN", "STP IN", "SIP", "PURCHASE", "NFO", "ADDITIONAL", "LUMPSUM", "INVESTMENT")),
    ("dividend_payout", ("DIVIDEND PAYOUT", "IDCW PAYOUT")),
    ("dividend_reinvest", ("DIVIDEND REINVEST", "IDCW REINVEST", "REINVEST")),
]


def classify(nature: str) -> str:
    n = nature.upper()
    for cat, keys in _CATEGORY_RULES:
        if any(k in n for k in keys):
            return cat
    return "other"


@dataclass
class ParsedTxnStatement:
    investor_name: str | None = None
    pan_masked: str | None = None
    period_from: object = None
    period_to: object = None
    schemes: pd.DataFrame = field(default_factory=pd.DataFrame)
    transactions: pd.DataFrame = field(default_factory=pd.DataFrame)
    grand_total_purchase_outstanding: float | None = None
    grand_total_market_value: float | None = None
    warnings: list[str] = field(default_factory=list)


def extract_lines(pdf_path: str, password: str | None = None) -> list[str]:
    lines: list[str] = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
    return lines


def parse_lines(lines: list[str]) -> ParsedTxnStatement:
    out = ParsedTxnStatement()
    schemes: list[dict] = []
    txns: list[dict] = []
    cur: dict | None = None

    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue

        if (m := PERIOD.search(line)) and out.period_from is None:
            out.period_from, out.period_to = parse_date(m["a"]), parse_date(m["b"])
            continue
        if (m := INVESTOR.match(line)) and out.pan_masked is None:
            out.investor_name, out.pan_masked = m["name"].strip(), mask_pan(m["pan"])
            continue
        if (m := FUND_HDR.match(line)):
            plan, option = infer_plan_option(m["name"])
            cur = {"scheme_key": f"{m['isin']}|{m['folio']}", "scheme_name": m["name"].strip(),
                   "isin": m["isin"], "folio": m["folio"], "plan": plan, "option": option,
                   "opening_units": 0.0, "opening_cost": 0.0, "nav": None, "nav_date": None,
                   "purchase_outstanding": None, "market_value": None}
            schemes.append(cur)
            continue
        if cur is None:
            continue
        if (m := OPENING.match(line)):
            cur["opening_cost"], cur["opening_units"] = num(m["cost"]), num(m["units"])
            continue
        if (m := TXN.match(line)):
            nature = m["nature"].strip()
            cat = classify(nature)
            units = num(m["units"])
            amount, total = num(m["amount"]), num(m["total"])
            # Normalise sign: units/amount are positive for investments, negative for redemptions.
            sign = -1 if cat == "redemption" else 1
            if cat in ("investment", "redemption", "dividend_reinvest"):
                units = sign * abs(units)
            txns.append({
                "scheme_key": cur["scheme_key"], "scheme_name": cur["scheme_name"],
                "isin": cur["isin"], "folio": cur["folio"], "date": parse_date(m["date"]),
                "nature": nature, "category": cat, "amount": sign * abs(amount),
                "price": num(m["price"]), "units": units, "balance_units": num(m["balance"]),
                "stamp_duty": num(m["stamp"]), "tds": num(m["tds"]), "stt": num(m["stt"]),
                "total_amount": sign * abs(total)})
            if cat == "other":
                out.warnings.append(f"Unrecognised transaction type '{nature}' on {m['date']} "
                                    f"({cur['scheme_name']}); kept with category 'other'.")
            continue
        if (m := FOOTER.match(line)):
            cur["nav"], cur["nav_date"] = num(m["nav"]), parse_date(m["date"])
            cur["purchase_outstanding"], cur["market_value"] = num(m["po"]), num(m["mv"])
            continue
        if (m := GRAND.match(line)):
            out.grand_total_purchase_outstanding, out.grand_total_market_value = num(m["po"]), num(m["mv"])
            cur = None
            continue
        if STARTS_WITH_DATE.match(line):
            out.warnings.append(f"Line looks like a transaction but could not be parsed: {line!r}")

    out.schemes = pd.DataFrame(schemes)
    out.transactions = pd.DataFrame(txns)
    if not out.transactions.empty:
        out.transactions = out.transactions.sort_values(
            ["scheme_key", "date"], kind="stable").reset_index(drop=True)
    return out


def parse_pdf(pdf_path: str, password: str | None = None) -> ParsedTxnStatement:
    return parse_lines(extract_lines(pdf_path, password))
