"""Cross-checks that prove the PDF was read correctly before any analysis is built on it."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .parsers.txn_statement import ParsedTxnStatement
from .parsers.valuation_summary import ParsedValuation
from .util import norm_name


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "error"  # 'error' fails ingestion, 'warn' is informational

    def __str__(self) -> str:
        flag = "PASS" if self.ok else ("FAIL" if self.severity == "error" else "WARN")
        return f"[{flag}] {self.name}" + (f" - {self.detail}" if self.detail else "")


def validate_txn(p: ParsedTxnStatement, money_tol: float = 1.0, unit_tol: float = 0.002) -> list[Check]:
    checks: list[Check] = []
    t, s = p.transactions, p.schemes
    checks.append(Check("Transactions were found", not t.empty, f"{len(t)} rows, {len(s)} schemes"))
    checks.append(Check("No unparsed transaction-looking lines", not p.warnings,
                        "; ".join(p.warnings[:3])))
    if t.empty:
        return checks

    dup = t.duplicated(["scheme_key", "date", "nature", "units", "total_amount"], keep=False)
    checks.append(Check("No duplicate transaction rows", not dup.any(), f"{int(dup.sum())} duplicated rows"))

    for _, sc in s.iterrows():
        g = t[t["scheme_key"] == sc["scheme_key"]].sort_values("date", kind="stable")
        label = sc["scheme_name"]
        # 1) running unit balance
        bal, worst = sc["opening_units"] or 0.0, 0.0
        for _, r in g.iterrows():
            bal += r["units"]
            worst = max(worst, abs(bal - r["balance_units"]))
            bal = r["balance_units"]
        checks.append(Check(f"Unit balance is continuous: {label}", worst <= unit_tol,
                            f"max drift {worst:.4f} units"))
        closing = g["balance_units"].iloc[-1] if len(g) else sc["opening_units"]
        # 2) closing units x NAV = market value
        if sc["nav"] and sc["market_value"] is not None:
            diff = abs(closing * sc["nav"] - sc["market_value"])
            checks.append(Check(f"Closing units x NAV = market value: {label}", diff <= money_tol,
                                f"difference Rs {diff:.2f}"))
        # 3) purchases add up to 'Purchase Outstanding' (only meaningful with no redemptions)
        if (g["category"] == "redemption").any():
            checks.append(Check(f"Purchase outstanding check skipped (has redemptions): {label}",
                                True, "cost of remaining units needs lot-level accounting", "warn"))
        elif sc["purchase_outstanding"] is not None:
            invested = g.loc[g["category"] == "investment", "total_amount"].sum() + (sc["opening_cost"] or 0)
            diff = abs(invested - sc["purchase_outstanding"])
            checks.append(Check(f"Purchases add up to purchase outstanding: {label}", diff <= money_tol,
                                f"difference Rs {diff:.2f}"))
    if p.grand_total_market_value is not None:
        diff = abs(s["market_value"].sum() - p.grand_total_market_value)
        checks.append(Check("Scheme market values add up to grand total", diff <= money_tol,
                            f"difference Rs {diff:.2f}"))
    if p.grand_total_purchase_outstanding is not None:
        diff = abs(s["purchase_outstanding"].sum() - p.grand_total_purchase_outstanding)
        checks.append(Check("Scheme purchase outstanding adds up to grand total", diff <= money_tol,
                            f"difference Rs {diff:.2f}"))
    return checks


def validate_valuation_vs_txn(txn: ParsedTxnStatement, val: ParsedValuation,
                              money_tol: float = 1.0) -> list[Check]:
    checks: list[Check] = []
    if val.holdings.empty:
        return [Check("Valuation holdings table was parsed", False, "table empty")]
    closing = (txn.transactions.sort_values("date", kind="stable")
               .groupby("scheme_key")["balance_units"].last())
    by_name = {(norm_name(r.scheme_name), r.folio): r for r in txn.schemes.itertuples()}
    for h in val.holdings.itertuples():
        sc = by_name.get((norm_name(h.scheme_name), h.folio))
        if sc is None:
            checks.append(Check(f"Valuation scheme found in transactions: {h.scheme_name}", False,
                                "no matching name+folio"))
            continue
        du = abs(closing.get(sc.scheme_key, 0.0) - h.balance_units)
        checks.append(Check(f"Units agree (statement vs valuation): {h.scheme_name}", du <= 0.002,
                            f"difference {du:.4f}"))
        dv = abs(sc.market_value - h.current_value)
        checks.append(Check(f"Current value agrees: {h.scheme_name}", dv <= money_tol,
                            f"difference Rs {dv:.2f}"))
        dp = abs(sc.purchase_outstanding - h.purchase_value)
        checks.append(Check(f"Purchase value agrees: {h.scheme_name}", dp <= money_tol,
                            f"difference Rs {dp:.2f}"))
    snap = val.snapshot
    if "current_value" in snap and "market_value" in txn.schemes:
        d = abs(txn.schemes["market_value"].sum() - snap["current_value"])
        checks.append(Check("Valuation snapshot current value matches statement total", d <= 5.0,
                            f"difference Rs {d:.2f}"))
    if txn.schemes["nav_date"].notna().any() and val.as_on:
        nd = txn.schemes["nav_date"].max()
        checks.append(Check("Statement NAV date vs valuation 'as on' date", True,
                            f"NAVs dated {nd}, valuation as on {val.as_on} (informational)", "warn"))
    return checks


def summarize(checks: list[Check]) -> tuple[int, int, int]:
    passed = sum(c.ok for c in checks)
    failed = sum((not c.ok) and c.severity == "error" for c in checks)
    return passed, failed, len(checks) - passed - failed
