"""Plotly figures. Every function takes plain DataFrames and returns a figure (or None when there is no data)."""
from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go

PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b"]
GREY = "#7f7f7f"


_AMC = re.compile(r"^(ICICI Pru(?:dential)?|Invesco India|Mirae Asset|Sundaram|Nippon India|HDFC|SBI|Axis|Kotak|"
                  r"Aditya Birla Sun Life|UTI|DSP|Franklin India|Tata|Bandhan|Edelweiss|Motilal Oswal|PGIM India|"
                  r"Canara Robeco|Parag Parikh|Quant|HSBC|Mahindra Manulife|Bajaj Finserv|Baroda BNP Paribas)\s+")
_TAIL = re.compile(r"\s+(Fund|Reg(?:ular)?\s*\(G\)|\(G\)|Growth|Plan)\s*$")


def labels(names) -> list[str]:
    """Short, readable chart labels ('ICICI Pru Energy Opportunities Fund Reg (G)' -> 'Energy Opportunities').
    Falls back to longer names if shortening would make two labels identical."""
    def trim(n, drop_amc):
        n = str(n).replace(" (time-weighted)", "")
        for _ in range(3):
            n = _TAIL.sub("", n)
        return _AMC.sub("", n) if drop_amc else n
    short = [trim(n, True) for n in names]
    if len(set(short)) == len(short):
        return short
    return [trim(n, False) for n in names]


def _base(fig, title: str, height: int = 420, yaxis_title: str | None = None):
    # legend above the plot (under the title) so it can never collide with axis labels at the bottom
    fig.update_layout(title=title, height=height + 40, margin=dict(l=10, r=10, t=95, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                      hovermode="x unified")
    if yaxis_title:
        fig.update_layout(yaxis_title=yaxis_title)
    return fig


def value_vs_benchmarks(daily: pd.DataFrame, value_col: str, invested_col: str, bench: dict[str, str], title: str):
    """Your value, the money put in, and what the same deposits would be worth in each benchmark."""
    if daily is None or daily.empty or value_col not in daily:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["date"], y=daily[value_col], name="Your value", mode="lines",
                             line=dict(color=PALETTE[0], width=3)))
    for i, (label, col) in enumerate(bench.items(), start=1):
        if col in daily and daily[col].notna().any():
            fig.add_trace(go.Scatter(x=daily["date"], y=daily[col], name=label, mode="lines",
                                     line=dict(color=PALETTE[i % len(PALETTE)], width=1.6)))
    fig.add_trace(go.Scatter(x=daily["date"], y=daily[invested_col], name="Money put in", mode="lines",
                             line=dict(color=GREY, width=1.5, dash="dash")))
    return _base(fig, f"{title} (Rs)")


def gap_over_time(daily: pd.DataFrame, value_col: str, bench: dict[str, str], title: str):
    if daily is None or daily.empty:
        return None
    fig = go.Figure()
    for i, (label, col) in enumerate(bench.items()):
        if col in daily and daily[col].notna().any():
            fig.add_trace(go.Scatter(x=daily["date"], y=daily[value_col] - daily[col], name=label.replace(" (same cash flows)", ""),
                                     mode="lines", line=dict(color=PALETTE[(i + 1) % len(PALETTE)], width=1.8)))
    fig.add_hline(y=0, line_dash="dot", line_color=GREY)
    return _base(fig, f"{title} (Rs)", 340)


def rolling_chart(rolling: pd.DataFrame, scope: str, months: int):
    d = rolling[(rolling["scope"] == scope) & (rolling["months"] == months)] if not rolling.empty else rolling
    if d is None or d.empty:
        return None
    label = d["benchmark"].iloc[0] if "benchmark" in d else "benchmark"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["date"], y=d["fund_pct"], name="Fund", mode="lines", line=dict(color=PALETTE[0], width=2.4)))
    if d["bench_pct"].notna().any():
        fig.add_trace(go.Scatter(x=d["date"], y=d["bench_pct"], name=str(label), mode="lines",
                                 line=dict(color=PALETTE[1], width=1.6)))
    fig.add_hline(y=0, line_dash="dot", line_color=GREY)
    fig = _base(fig, f"Rolling {months}-month return", 340)
    fig.update_layout(yaxis_ticksuffix="%")
    return fig


def drawdown_chart(nav: pd.Series, title: str = "Fall from the previous peak"):
    nav = nav.dropna()
    if len(nav) < 5:
        return None
    dd = (nav / nav.cummax() - 1) * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="Drawdown", mode="lines", fill="tozeroy",
                             line=dict(color=PALETTE[1], width=1.5)))
    fig = _base(fig, title, 300)
    fig.update_layout(yaxis_ticksuffix="%")
    return fig


def calendar_chart(cal: pd.DataFrame, scope: str):
    d = cal[cal["scope"] == scope] if not cal.empty else cal
    if d is None or d.empty:
        return None
    labels = [f"{r.year} ({r.basis})" for r in d.itertuples()]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=d["fund_pct"], name="Fund", marker=dict(color=PALETTE[0])))
    if d["bench_pct"].notna().any():
        fig.add_trace(go.Bar(x=labels, y=d["bench_pct"], name=str(d["benchmark"].iloc[0]), marker=dict(color=PALETTE[1])))
    fig = _base(fig, "Calendar-year returns", 340)
    fig.update_layout(barmode="group", yaxis_ticksuffix="%")
    return fig


def xirr_bars(schemes: pd.DataFrame):
    if schemes is None or schemes.empty:
        return None
    names = labels(schemes["scheme_name"])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=schemes["fund_xirr_pct"], name="Your fund (XIRR)", marker=dict(color=PALETTE[0])))
    fig.add_trace(go.Bar(x=names, y=schemes["bench_xirr_pct"], name="Own benchmark, same cash flows", marker=dict(color=PALETTE[1])))
    fig = _base(fig, "Annualised return on your money: fund vs its benchmark", 380)
    fig.update_layout(barmode="group", yaxis_ticksuffix="%")
    return fig


def gap_bars(schemes: pd.DataFrame):
    if schemes is None or schemes.empty or schemes["gap_rs"].notna().sum() == 0:
        return None
    d = schemes.dropna(subset=["gap_rs"])
    names = labels(d["scheme_name"])
    colors = [PALETTE[2] if v >= 0 else PALETTE[1] for v in d["gap_rs"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=d["gap_rs"], name="Rupee gap", marker=dict(color=colors)))
    fig = _base(fig, "Each fund: rupees ahead (+) or behind (-) its own benchmark", 340)
    fig.update_layout(showlegend=False)
    return fig


def capture_bars(risk: pd.DataFrame):
    d = risk.dropna(subset=["up_capture", "down_capture"]) if risk is not None and not risk.empty else None
    if d is None or d.empty:
        return None
    names = labels(d["scope"])[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=names, x=list(d["down_capture"])[::-1], name="Down capture", orientation="h",
                         marker=dict(color=PALETTE[1])))
    fig.add_trace(go.Bar(y=names, x=list(d["up_capture"])[::-1], name="Up capture", orientation="h",
                         marker=dict(color=PALETTE[2])))
    fig.add_vline(x=100, line_dash="dot", line_color=GREY)
    fig = _base(fig, "Up / down capture vs own benchmark (100 = same as the benchmark)", 120 + 60 * len(names))
    fig.update_layout(barmode="group", hovermode="closest", legend_traceorder="reversed")
    return fig


def risk_return_scatter(risk: pd.DataFrame):
    d = risk.dropna(subset=["vol_pct", "cagr_pct"]) if risk is not None and not risk.empty else None
    if d is None or d.empty:
        return None
    names = labels(d["scope"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["vol_pct"], y=d["cagr_pct"], mode="markers+text", text=names, textposition="top center",
                             cliponaxis=False, marker=dict(size=12, color=PALETTE[0]), name="Funds"))
    fig = _base(fig, "Return vs volatility (buy-on-day-one basis)", 400)
    xs, ys = list(d["vol_pct"]), list(d["cagr_pct"])
    px = max((max(xs) - min(xs)) * 0.25, 1.0)
    py = max((max(ys) - min(ys)) * 0.15, 0.8)
    fig.update_xaxes(range=[min(xs) - px, max(xs) + px])
    fig.update_yaxes(range=[min(ys) - py, max(ys) + py])
    fig.update_layout(xaxis_title="Volatility (annualised %)", yaxis_title="CAGR %", hovermode="closest", showlegend=False)
    return fig


def gap_waterfall(contrib: pd.DataFrame):
    """The per-fund rupee gaps stacked left to right so the portfolio's total gap builds up; last bar = total."""
    if contrib is None or contrib.empty:
        return None
    names = labels(contrib["scheme_name"])
    vals = [float(v) for v in contrib["gap_rs"]]
    fig = go.Figure()
    fig.add_trace(go.Waterfall(x=names + ["Portfolio total"], y=vals + [0], measure=["relative"] * len(vals) + ["total"],
                               text=[f"{v:,.0f}" for v in vals + [sum(vals)]], textposition="outside",
                               increasing=dict(marker=dict(color=PALETTE[2])), decreasing=dict(marker=dict(color=PALETTE[1])),
                               totals=dict(marker=dict(color=PALETTE[0]))))
    fig = _base(fig, "How the portfolio's total gap builds up, fund by fund (Rs)", 360)
    fig.update_layout(showlegend=False, hovermode="closest")
    return fig


def commission_bars(costs: pd.DataFrame):
    d = costs.dropna(subset=["est_commission_to_date"]) if costs is not None and not costs.empty else None
    if d is None or d.empty:
        return None
    names = labels(d["scheme_name"])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=d["est_commission_to_date"], name="Estimated to date", marker=dict(color=PALETTE[3])))
    fig.add_trace(go.Bar(x=names, y=d["est_next_12m"], name="Estimated next 12 months", marker=dict(color=PALETTE[4])))
    fig = _base(fig, "Estimated distributor commission inside the regular plans (Rs)", 340)
    fig.update_layout(barmode="group")
    return fig


def allocation_bars(df: pd.DataFrame, title: str, top: int = 12):
    if df is None or df.empty:
        return None
    d = df.sort_values("pct", ascending=False).head(top).iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d["pct"], y=d["label"], orientation="h", marker=dict(color=PALETTE[0]), name="Share"))
    fig = _base(fig, title, 120 + 28 * len(d))
    fig.update_layout(showlegend=False, xaxis_ticksuffix="%", hovermode="closest")
    return fig


def overlap_heatmap(matrix: pd.DataFrame, title: str):
    if matrix is None or matrix.empty:
        return None
    labs = labels(list(matrix.index))
    z = matrix.values.astype(float)
    text = [[f"{v:.1f}%" for v in row] for row in z]
    fig = go.Figure()
    fig.add_trace(go.Heatmap(z=z, x=labs, y=labs, text=text, texttemplate="%{text}", colorscale="Blues",
                             zmin=0, zmax=100, showscale=False))
    fig = _base(fig, title, 150 + 70 * len(labs))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(hovermode="closest")
    return fig


def sector_heatmap(matrix: pd.DataFrame, title: str = "Sector weight in each fund (% of the fund)"):
    if matrix is None or matrix.empty:
        return None
    m = matrix.head(15)
    cols = labels(list(m.columns))
    text = [[f"{v:.1f}" if v > 0 else "" for v in row] for row in m.values]
    fig = go.Figure()
    fig.add_trace(go.Heatmap(z=m.values.astype(float), x=cols, y=list(m.index), text=text, texttemplate="%{text}",
                             colorscale="Blues", showscale=False))
    fig = _base(fig, title, 140 + 30 * len(m))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(hovermode="closest")
    return fig


def lookthrough_bars(exposure: pd.DataFrame, title: str = "Your biggest underlying holdings (Rs), by which fund holds them"):
    if exposure is None or exposure.empty:
        return None
    d = exposure.iloc[::-1]
    fig = go.Figure()
    for i, (f, lbl) in enumerate(zip(d.columns, labels(list(d.columns)))):
        fig.add_trace(go.Bar(y=list(d.index), x=d[f], orientation="h", name=lbl,
                             marker=dict(color=PALETTE[i % len(PALETTE)])))
    fig = _base(fig, title, 140 + 26 * len(d))
    fig.update_layout(barmode="stack", hovermode="closest")
    return fig