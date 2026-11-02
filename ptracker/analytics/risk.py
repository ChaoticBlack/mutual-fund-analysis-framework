"""Risk, rolling-return and daily-value analytics built on daily NAV history (Module 4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import storage
from ..indexes.files import IndexStore
from ..indexes.plan import DEFAULT_COMPARATORS
from ..nav.store import NavStore
from .engine import investor_flows
from .replicate import replicate
from .resolve import resolve_all

TD = 252          # trading days per year used to annualise
MIN_OBS = 60      # fewer daily returns than this -> ratios are not reported


# ------------------------------------------------------------------ pure functions
def cagr(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 2:
        return None
    days = (s.index[-1] - s.index[0]).days
    return None if days <= 0 or s.iloc[0] <= 0 else float((s.iloc[-1] / s.iloc[0]) ** (365.0 / days) - 1)


def max_drawdown(s: pd.Series) -> dict:
    s = s.dropna()
    dd = s / s.cummax() - 1.0
    trough = dd.idxmin()
    peak = s.loc[:trough].idxmax()
    after = s.loc[trough:]
    rec = after[after >= s.loc[peak]]
    return {"max_dd": float(dd.min()), "peak": peak.date(), "trough": trough.date(),
            "recovered": rec.index[0].date() if len(rec) else None, "current_dd": float(dd.iloc[-1])}


def risk_metrics(fund: pd.Series, bench: pd.Series | None = None, rf: float = 0.06,
                 min_obs: int = MIN_OBS) -> dict:
    """Metrics from daily NAV/index levels. `rf` is an annual risk-free rate (0.06 = 6%)."""
    f = fund.dropna().sort_index()
    out: dict = {"window_from": f.index[0].date(), "window_to": f.index[-1].date(), "obs": max(len(f) - 1, 0),
                 "cagr": cagr(f)}
    r = f.pct_change().dropna()
    rf_d = (1 + rf) ** (1 / TD) - 1
    if len(r) >= min_obs:
        sd = r.std(ddof=1)
        ex = r - rf_d
        dsd = float(np.sqrt((np.minimum(ex, 0.0) ** 2).mean()))
        out.update(vol=float(sd * np.sqrt(TD)), sharpe=float(ex.mean() / sd * np.sqrt(TD)) if sd > 0 else None,
                   sortino=float(ex.mean() / dsd * np.sqrt(TD)) if dsd > 0 else None)
    out.update({f"dd_{k}": v for k, v in max_drawdown(f).items()})
    if bench is not None:
        idx = f.index.intersection(bench.dropna().index)
        ff, bb = f.loc[idx], bench.loc[idx]
        out["bench_cagr"] = cagr(bb)
        rfu, rbu = ff.pct_change().dropna(), bb.pct_change().dropna()
        if len(rbu) >= min_obs:
            var_b = float(np.var(rbu, ddof=1))
            beta = float(np.cov(rfu, rbu, ddof=1)[0, 1] / var_b) if var_b > 0 else None
            active = rfu - rbu
            te = float(active.std(ddof=1) * np.sqrt(TD))
            up, dn = rbu > 0, rbu < 0
            out.update(
                bench_vol=float(rbu.std(ddof=1) * np.sqrt(TD)), beta=beta,
                corr=float(np.corrcoef(rfu, rbu)[0, 1]),
                alpha=None if beta is None else float((rfu.mean() - rf_d - beta * (rbu.mean() - rf_d)) * TD),
                tracking_error=te, info_ratio=float(active.mean() * TD / te) if te > 0 else None,
                up_capture=float(rfu[up].mean() / rbu[up].mean() * 100) if up.any() else None,
                down_capture=float(rfu[dn].mean() / rbu[dn].mean() * 100) if dn.any() else None)
    return out


def rolling_returns(s: pd.Series, months: int) -> pd.Series:
    """Return over the trailing `months` for every date that has that much history."""
    s = s.dropna().sort_index()
    base_dates = s.index - pd.DateOffset(months=months)
    ok = base_dates >= s.index[0]
    base = s.reindex(base_dates, method="ffill").values
    out = pd.Series(s.values / base - 1.0, index=s.index)
    return out[ok]


def calendar_year_returns(s: pd.Series) -> pd.DataFrame:
    s = s.dropna().sort_index()
    rows, prev = [], None
    for y in sorted(set(s.index.year)):
        last = s[s.index.year == y]
        start_val = prev if prev is not None else s.iloc[0]
        label = "from start" if prev is None else ("YTD" if y == s.index[-1].year and s.index[-1] < pd.Timestamp(y, 12, 24) else "full")
        rows.append({"year": y, "return": float(last.iloc[-1] / start_val - 1), "basis": label})
        prev = float(last.iloc[-1])
    return pd.DataFrame(rows)


def _pct(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(100 * x, nd)


def _r(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


# ------------------------------------------------------------------ result container
@dataclass
class RiskResult:
    risk: pd.DataFrame = field(default_factory=pd.DataFrame)
    vs_comparators: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling: pd.DataFrame = field(default_factory=pd.DataFrame)
    calendar: pd.DataFrame = field(default_factory=pd.DataFrame)
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    portfolio_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list = field(default_factory=list)


def _flat(scope: str, bench_label: str, basis: str, m: dict) -> dict:
    return {"scope": scope, "benchmark": bench_label, "benchmark_basis": basis,
            "window_from": m["window_from"], "window_to": m["window_to"], "daily_obs": m["obs"],
            "cagr_pct": _pct(m.get("cagr")), "bench_cagr_pct": _pct(m.get("bench_cagr")),
            "excess_pts": None if m.get("cagr") is None or m.get("bench_cagr") is None
            else round(100 * (m["cagr"] - m["bench_cagr"]), 2),
            "vol_pct": _pct(m.get("vol")), "bench_vol_pct": _pct(m.get("bench_vol")),
            "max_drawdown_pct": _pct(m["dd_max_dd"]), "dd_peak": m["dd_peak"], "dd_trough": m["dd_trough"],
            "dd_recovered": m["dd_recovered"] or "not yet", "current_drawdown_pct": _pct(m["dd_current_dd"]),
            "beta": _r(m.get("beta")), "alpha_pct": _pct(m.get("alpha")), "correlation": _r(m.get("corr")),
            "tracking_error_pct": _pct(m.get("tracking_error")), "info_ratio": _r(m.get("info_ratio")),
            "up_capture": _r(m.get("up_capture"), 1), "down_capture": _r(m.get("down_capture"), 1),
            "sharpe": _r(m.get("sharpe")), "sortino": _r(m.get("sortino"))}


def _rel_row(scope: str, comp: str, basis: str, m: dict) -> dict:
    return {"scope": scope, "comparator": comp, "basis": basis, "bench_cagr_pct": _pct(m.get("bench_cagr")),
            "beta": _r(m.get("beta")), "alpha_pct": _pct(m.get("alpha")), "correlation": _r(m.get("corr")),
            "tracking_error_pct": _pct(m.get("tracking_error")), "info_ratio": _r(m.get("info_ratio")),
            "up_capture": _r(m.get("up_capture"), 1), "down_capture": _r(m.get("down_capture"), 1)}


def _step(values: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Forward-filled step function on `grid` (0 before the first value)."""
    return values.sort_index().reindex(grid.union(values.index)).ffill().reindex(grid).fillna(0.0)


def _roll_rows(scope, kind, fund, bench, bench_label="(none)"):
    rows, summ = [], []
    for months in (6, 12):
        rf_ = rolling_returns(fund, months)
        if rf_.empty:
            continue
        rb = rolling_returns(bench, months).reindex(rf_.index) if bench is not None else None
        for d, v in rf_.items():
            rows.append({"scope": scope, "benchmark": bench_label, "months": months, "date": d, "fund_pct": _pct(v),
                         "bench_pct": None if rb is None or pd.isna(rb.get(d)) else _pct(rb.get(d))})
        row = {"scope": scope, "benchmark": bench_label, "window_months": months, "n_days": len(rf_),
               "fund_median_pct": _pct(rf_.median()),
               "fund_min_pct": _pct(rf_.min()), "fund_max_pct": _pct(rf_.max())}
        if rb is not None and rb.notna().any():
            both = pd.concat([rf_, rb], axis=1).dropna()
            row.update(bench_median_pct=_pct(both.iloc[:, 1].median()),
                       pct_of_days_ahead=round(100 * float((both.iloc[:, 0] > both.iloc[:, 1]).mean()), 1))
        summ.append(row)
    return rows, summ


def run_risk(portfolio_path: str, index_dir: str, nav_dir: str, comparators=DEFAULT_COMPARATORS,
             allow_price_fallback: bool = False, risk_free_pct: float = 6.0) -> RiskResult:
    res = RiskResult()
    rf = risk_free_pct / 100.0
    pf = storage.load_portfolio(portfolio_path)
    sch, txn = pf["Schemes"].copy(), pf["Transactions"]
    istore, nstore = IndexStore(index_dir), NavStore(nav_dir)
    res.warnings += istore.errors + istore.warnings + nstore.errors + nstore.warnings
    own_refs, comp_refs = resolve_all(portfolio_path, istore, comparators, allow_price_fallback)
    sch["last_nav_date"] = pd.to_datetime(sch["last_nav_date"]).dt.date

    risk_rows, rel_rows, roll_rows, roll_summ, cal_rows = [], [], [], [], []
    per: dict[str, dict] = {}
    for s in sch.itertuples():
        nav = nstore.series_for(s.isin, s.scheme_name, getattr(s, "nav_file", None)
                                if isinstance(getattr(s, "nav_file", None), str) else None)
        if nav is None:
            res.warnings.append(f"{s.scheme_name}: no NAV history found (see `nav plan`); skipped.")
            continue
        t = txn[txn["scheme_key"] == s.scheme_key].sort_values("date", kind="stable")
        first, as_of = t["date"].min(), s.last_nav_date
        navw = nav[(nav.index >= pd.Timestamp(first)) & (nav.index <= pd.Timestamp(as_of))]
        if len(navw) < 5:
            res.warnings.append(f"{s.scheme_name}: NAV history does not cover {first} to {as_of}; skipped.")
            continue
        flows = investor_flows(t)
        grid = navw.index
        units = _step(t.groupby("date")["balance_units"].last().rename(index=pd.Timestamp), grid)
        fl = sorted(flows)
        ni = pd.Series(np.cumsum([-a for _, a in fl]), index=[pd.Timestamp(d) for d, _ in fl]).groupby(level=0).last()
        net_inv = _step(ni, grid)
        daily = pd.DataFrame({"scheme_name": s.scheme_name, "nav": navw, "units": units,
                              "fund_value": units * navw, "net_invested": net_inv})
        ref = own_refs.get(s.scheme_key)
        bench_cols: dict[str, pd.Series] = {}
        own_series = None
        for label, r_ in [("bench_own", ref)] + [(f"bench_{c.label}", c) for c in comp_refs]:
            if r_ is None or r_.series is None:
                continue
            rep = replicate(flows, r_.series, as_of)
            if rep is None or rep.xirr is None:
                continue
            u = _step(pd.Series(rep.path["units_after"].values, index=pd.to_datetime(rep.path["date"])), grid)
            lvl = r_.series.reindex(grid.union(r_.series.index)).ffill().reindex(grid)
            daily[label] = u * lvl
            bench_cols[label] = r_.series
            if label == "bench_own":
                own_series = r_.series
        per[s.scheme_key] = {"nav": navw, "units": units, "own": own_series, "ref": ref, "name": s.scheme_name,
                             "daily": daily, "comps": {c.label: c for c in comp_refs if c.series is not None}}
        # ---- metrics for this fund
        blabel = ref.label if ref and ref.series is not None else "(none)"
        m = risk_metrics(navw, own_series, rf)
        risk_rows.append(_flat(s.scheme_name, blabel, ref.basis if ref else "unavailable", m))
        for c in comp_refs:
            if c.series is not None:
                rel_rows.append(_rel_row(s.scheme_name, c.label, c.basis, risk_metrics(navw, c.series, rf)))
        rr, rs = _roll_rows(s.scheme_name, "fund", navw, own_series, blabel)
        roll_rows += rr
        roll_summ += rs
        cy = calendar_year_returns(navw)
        cyb = calendar_year_returns(own_series[(own_series.index >= navw.index[0]) & (own_series.index <= navw.index[-1])]) \
            if own_series is not None else None
        for _, r_ in cy.iterrows():
            b = None if cyb is None else cyb[cyb["year"] == r_["year"]]
            cal_rows.append({"scope": s.scheme_name, "benchmark": blabel, "year": int(r_["year"]), "basis": r_["basis"],
                             "fund_pct": _pct(r_["return"]),
                             "bench_pct": None if b is None or b.empty else _pct(float(b["return"].iloc[0]))})

    # ---------------- whole portfolio: daily value path and time-weighted return
    if per:
        grid = pd.DatetimeIndex(sorted(set().union(*[set(v["nav"].index) for v in per.values()])))
        tot = pd.DataFrame(index=grid, data={"portfolio_value": 0.0, "net_invested": 0.0})
        num = pd.Series(0.0, index=grid)
        den = pd.Series(0.0, index=grid)
        bench_cols_all: dict[str, pd.Series] = {}
        w_rows = []
        for key, v in per.items():
            nav_ff = v["nav"].reindex(grid).ffill()
            u = v["units"].reindex(grid.union(v["units"].index)).ffill().reindex(grid).fillna(0.0)
            val = (u * nav_ff).fillna(0.0)
            tot["portfolio_value"] += val
            d = v["daily"]
            tot["net_invested"] += d["net_invested"].reindex(grid.union(d.index)).ffill().reindex(grid).fillna(0.0)
            for col in [c for c in d.columns if c.startswith("bench_")]:
                s_ = d[col].reindex(grid.union(d.index)).ffill().reindex(grid).fillna(0.0)
                bench_cols_all[col] = bench_cols_all.get(col, 0.0) + s_
            up, np_ = u.shift(1).fillna(0.0), nav_ff.shift(1)
            num += (up * (nav_ff - np_)).fillna(0.0)
            den += (up * np_).fillna(0.0)
            w_rows.append((key, up * np_, v["own"]))
        for col, ser in bench_cols_all.items():
            tot[col] = ser
        r = (num / den.where(den > 0)).fillna(0.0)
        tot["twr_index"] = (1 + r).cumprod()
        # blended own-benchmark daily return, only if every fund has an own benchmark
        if all(w[2] is not None for w in w_rows):
            br = pd.Series(0.0, index=grid)
            for key, w_val, own in w_rows:
                lv = own.reindex(grid.union(own.index)).ffill().reindex(grid)
                br += (w_val / den.where(den > 0)).fillna(0.0) * lv.pct_change().fillna(0.0)
            tot["blended_own_benchmark_index"] = (1 + br).cumprod()
        else:
            res.warnings.append("Portfolio-level 'blended own benchmark' skipped: not every fund has a benchmark series.")
        res.portfolio_daily = tot.reset_index(names="date")
        twr = tot["twr_index"][tot["portfolio_value"] > 0]
        blend = tot["blended_own_benchmark_index"].reindex(twr.index) if "blended_own_benchmark_index" in tot else None
        m = risk_metrics(twr, blend, rf)
        risk_rows.append(_flat("PORTFOLIO (time-weighted)", "blended own benchmarks" if blend is not None else "(none)",
                               "TRI" if blend is not None else "unavailable", m))
        for c in comp_refs:
            if c.series is not None:
                lv = c.series.reindex(twr.index.union(c.series.index)).ffill().reindex(twr.index)
                rel_rows.append(_rel_row("PORTFOLIO (time-weighted)", c.label, c.basis, risk_metrics(twr, lv, rf)))
        if blend is not None:
            roll_bench, roll_label = blend, "blended own benchmarks"
        else:
            fb = next((c for c in comp_refs if c.series is not None), None)
            roll_bench = None if fb is None else fb.series.reindex(twr.index.union(fb.series.index)).ffill().reindex(twr.index)
            roll_label = "(none)" if fb is None else fb.label
        rr, rs = _roll_rows("PORTFOLIO (time-weighted)", "portfolio", twr, roll_bench, roll_label)
        roll_rows += rr
        roll_summ += rs
        res.daily = pd.concat([v["daily"].reset_index(names="date") for v in per.values()], ignore_index=True)

    res.risk, res.vs_comparators = pd.DataFrame(risk_rows), pd.DataFrame(rel_rows)
    res.rolling, res.rolling_summary, res.calendar = pd.DataFrame(roll_rows), pd.DataFrame(roll_summ), pd.DataFrame(cal_rows)
    if len(sch) and not res.risk.empty:
        short = res.risk["daily_obs"].min()
        res.warnings.append(f"History is short (as little as {int(short)} trading days): ratios are indicative, "
                            f"and no 3-year or 5-year figures are possible yet.")
    return res


def to_excel(res: RiskResult, path: str) -> None:
    def dates(df):
        df = df.copy()
        for c in df.columns:
            if "date" in c.lower() or c in ("window_from", "window_to", "dd_peak", "dd_trough"):
                df[c] = pd.to_datetime(df[c], errors="coerce").dt.date if c == "date" else df[c]
        return df
    notes = pd.DataFrame({"note": res.warnings + [
        "Volatility, Sharpe, Sortino and beta use daily returns, annualised with 252 trading days.",
        "Sharpe = mean excess return / st.dev; Sortino uses downside deviation below the risk-free rate.",
        "Up/down capture = average fund return on benchmark up (down) days divided by the benchmark's, x100.",
        "Alpha is Jensen's alpha (annualised) against the stated benchmark.",
        "Windows start at your first investment in each fund, not at the fund's launch.",
        "PORTFOLIO rows are time-weighted (unit-based), so SIP timing does not distort them."]})
    storage._write(path, {"Risk": res.risk, "Risk_vs_Comparators": res.vs_comparators,
                          "Rolling_Summary": res.rolling_summary, "Calendar_Years": res.calendar,
                          "Portfolio_Daily": dates(res.portfolio_daily), "Daily_Funds": dates(res.daily),
                          "Rolling_Daily": dates(res.rolling), "Notes": notes})