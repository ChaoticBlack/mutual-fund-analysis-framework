"""How much do the funds hold the same things? (stock, sector and look-through views)

Input is the Holdings sheet of factsheets.xlsx: one row per instrument per fund with its % of the fund.
Only equity rows are compared; cash, repo and other current assets are reported separately as 'coverage'.
"""
from __future__ import annotations

import re

import pandas as pd

_DROP = re.compile(r"\b(limited|ltd|the|india|company|co|corporation|corp)\b")


def norm_instrument(name: str) -> str:
    """'Mahindra & Mahindra Ltd.' and 'Mahindra and Mahindra Limited' -> 'mahindra and mahindra'."""
    n = str(name).lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = _DROP.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def short(name: str) -> str:
    return re.sub(r"\s+Fund\s+Reg\s*\(G\)$", "", re.sub(r"\s+Fund\s*$", "", str(name))).strip()


def prepare(holdings: pd.DataFrame, funds: list[str] | None = None) -> pd.DataFrame:
    """Equity rows only, one row per (fund, company): columns fund, key, instrument, sector, weight (% of the fund)."""
    if holdings is None or holdings.empty:
        return pd.DataFrame(columns=["fund", "key", "instrument", "sector", "weight"])
    h = holdings[holdings["section"] == "Equity"].copy()
    if funds is not None:
        h = h[h["scheme_name"].isin(funds)]
    h["key"] = h["instrument"].map(norm_instrument)
    first = h.drop_duplicates(["scheme_name", "key"]).set_index(["scheme_name", "key"])
    h["weight_pct"] = h["weight_pct"].astype(float)
    g = h.groupby(["scheme_name", "key"], as_index=False)["weight_pct"].sum()
    g["instrument"] = [first.loc[(a, b), "instrument"] for a, b in zip(g["scheme_name"], g["key"])]
    g["sector"] = [first.loc[(a, b), "sector_or_rating"] for a, b in zip(g["scheme_name"], g["key"])]
    return g.rename(columns={"scheme_name": "fund", "weight_pct": "weight"})[["fund", "key", "instrument", "sector", "weight"]]


def coverage(holdings: pd.DataFrame, funds: list[str] | None = None,
             declared_equity: dict[str, float] | None = None) -> pd.DataFrame:
    """Share of each fund's NAV that is equity vs everything else, and the total itemised.
    `declared_equity` (fund -> % equity the factsheet's own composition table states) shows when a factsheet
    itemises less equity than it claims (e.g. derivative positions that are not listed)."""
    h = holdings if funds is None else holdings[holdings["scheme_name"].isin(funds)]
    rows = []
    for f, g in h.groupby("scheme_name", sort=False):
        eq = float(g.loc[g["section"] == "Equity", "weight_pct"].sum())
        tot = float(g["weight_pct"].sum())
        decl = (declared_equity or {}).get(f)
        rows.append({"fund": f, "equity_pct": round(eq, 2), "cash_and_other_pct": round(tot - eq, 2),
                     "total_read_pct": round(tot, 2), "declared_equity_pct": decl,
                     "equity_not_itemised_pct": None if decl is None or pd.isna(decl) else round(float(decl) - eq, 2),
                     "complete": tot >= 98.0})
    return pd.DataFrame(rows)


def _pivot(eq: pd.DataFrame, index: str) -> pd.DataFrame:
    return eq.pivot_table(index=index, columns="fund", values="weight", aggfunc="sum", fill_value=0.0)


def overlap_matrix(eq: pd.DataFrame, by: str = "key") -> pd.DataFrame:
    """Pairwise overlap in % of NAV = sum over shared items of the smaller of the two weights.
    The diagonal is each fund's own total equity weight (its overlap with itself)."""
    w = _pivot(eq, by)
    funds = list(w.columns)
    m = pd.DataFrame(0.0, index=funds, columns=funds)
    for a in funds:
        for b in funds:
            m.loc[a, b] = float(pd.concat([w[a], w[b]], axis=1).min(axis=1).sum())
    return m


def sector_matrix(eq: pd.DataFrame) -> pd.DataFrame:
    return _pivot(eq, "sector").sort_values(by=list(_pivot(eq, "sector").columns), ascending=False)


def shared_share(eq: pd.DataFrame) -> pd.DataFrame:
    """Per fund: how much of its equity sits in companies that at least one other selected fund also holds."""
    n_funds = eq.groupby("key")["fund"].nunique()
    shared = eq[eq["key"].map(n_funds) > 1]
    rows = []
    for f in eq["fund"].unique():
        own = eq[eq["fund"] == f]
        s = shared[shared["fund"] == f]
        rows.append({"fund": f, "equity_pct": round(float(own["weight"].sum()), 2),
                     "in_shared_stocks_pct": round(float(s["weight"].sum()), 2),
                     "share_of_equity_pct": round(100 * float(s["weight"].sum()) / float(own["weight"].sum()), 1)
                     if own["weight"].sum() else None, "n_shared_stocks": int(len(s))})
    return pd.DataFrame(rows)


def stock_table(eq: pd.DataFrame, fund_values: dict[str, float]) -> pd.DataFrame:
    """One row per company: weight in each fund, how many funds hold it, and your total rupee exposure.
    `fund_values` maps fund name -> rupees you hold; exposure = sum(value x weight / 100)."""
    w = _pivot(eq, "key")
    funds = list(w.columns)
    label = eq.drop_duplicates("key").set_index("key")
    vals = pd.Series({f: fund_values.get(f, 0.0) for f in funds})
    out = w.copy()
    out.insert(0, "instrument", [label.loc[k, "instrument"] for k in out.index])
    out.insert(1, "sector", [label.loc[k, "sector"] for k in out.index])
    out.insert(2, "n_funds", (w > 0).sum(axis=1))
    out["exposure_rs"] = (w * vals / 100.0).sum(axis=1)
    total = float(vals.sum())
    out["share_of_your_money_pct"] = (100 * out["exposure_rs"] / total) if total else None
    return out.reset_index(drop=True).sort_values("exposure_rs", ascending=False)


def sector_lookthrough(eq: pd.DataFrame, fund_values: dict[str, float]) -> pd.DataFrame:
    w = _pivot(eq, "sector")
    vals = pd.Series({f: fund_values.get(f, 0.0) for f in w.columns})
    total = float(vals.sum())
    out = pd.DataFrame({"exposure_rs": (w * vals / 100.0).sum(axis=1)})
    out["share_of_your_money_pct"] = 100 * out["exposure_rs"] / total if total else None
    return out.sort_values("exposure_rs", ascending=False).reset_index().rename(columns={"sector": "sector"})


def stock_exposure_by_fund(eq: pd.DataFrame, fund_values: dict[str, float], top: int = 15) -> pd.DataFrame:
    """Rupees you hold in each of the top companies, split by which fund holds them (for a stacked bar)."""
    w = _pivot(eq, "key")
    vals = pd.Series({f: fund_values.get(f, 0.0) for f in w.columns})
    rs = w * vals / 100.0
    label = eq.drop_duplicates("key").set_index("key")["instrument"]
    rs.index = [label[k] for k in rs.index]
    return rs.loc[rs.sum(axis=1).sort_values(ascending=False).head(top).index]