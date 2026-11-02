"""XIRR with the same convention as Excel / the AMC statements (actual days / 365, first flow = t0)."""
from __future__ import annotations

from datetime import date

from scipy.optimize import brentq


def xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    return sum(a / (1.0 + rate) ** ((d - t0).days / 365.0) for d, a in flows)


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised internal rate of return as a fraction (0.12 = 12%).

    Negative amounts are money invested, positive amounts money received. Returns None when no
    meaningful rate exists (all flows one sign, a single date, or no root found) instead of guessing.
    """
    fl = sorted(((d, float(a)) for d, a in flows if a), key=lambda x: x[0])
    if len(fl) < 2 or fl[0][0] == fl[-1][0]:
        return None
    if not (any(a < 0 for _, a in fl) and any(a > 0 for _, a in fl)):
        return None
    f = lambda r: xnpv(r, fl)
    lo, hi = -0.9999, 1.0
    while f(lo) * f(hi) > 0 and hi < 1e5:
        hi *= 2
    if f(lo) * f(hi) > 0:
        return None
    try:
        return float(brentq(f, lo, hi, xtol=1e-12, maxiter=500))
    except (ValueError, RuntimeError):
        return None
