# Methodology

What every number and chart in the tracker means, how it is calculated, and what it can and cannot tell you.
This describes what the code actually does. Nothing here is investment advice.

Contents: 1 Data and checks | 2 Money-based performance | 3 Risk and return from NAV history | 4 Overlap and
look-through | 5 Reading the charts | 6 Limitations | 7 Glossary

---

## 1. Data and how it is checked

| Input | Used for |
|---|---|
| Transaction statement (PDF) | Every purchase, redemption and dividend: date, amount, price, units. The basis of all money-based results. |
| Portfolio valuation summary (PDF) | Current values, sector / market-cap / top-holding allocation, SIP summary. Cross-checked against the statement. |
| Fund factsheets (PDF) | Benchmark name, expense ratio, exit load, fund manager, holdings and sector weights, factsheet returns. |
| Index files (CSV / Excel) | Daily levels of each benchmark (ideally Total Returns Index) and the standard comparators. |
| NAV files (CSV / Excel / AMFI text, or optional download) | Daily NAV of each fund, for risk and the day-by-day charts. |

**Cash-flow rules.** Seen from the investor: money going into a fund is negative; money coming back is positive.
A purchase or SIP counts the *total* amount debited (including the Rs 1 stamp duty, which buys no units). Redemptions
and dividend payouts are positive. Dividend reinvestment adds units but moves no cash.

**Checks run on every import** (a failure stops anything being saved):
unit balance continues correctly after every transaction; closing units x NAV = market value; purchases add up to
"purchase outstanding"; fund totals add up to the grand total; the valuation summary agrees with the statement on
units, cost and value for every fund. For NAV files, the app also compares your statement's price on each
transaction date with the NAV file (a mismatch usually means the wrong plan or option was downloaded).

**Privacy.** Address, phone, e-mail, bank and nominee details are never written to any file. PAN is stored masked.

---

## 2. Money-based performance (what happened to *your* money)

### 2.1 Basic figures
* **Money put in** = sum of all amounts invested. **Worth today** = closing units x last NAV.
* **Net gain** = worth today + money withdrawn - money put in.
* **Absolute return** = net gain / money put in. It ignores *when* money went in, so it is not comparable between
  funds with different SIP histories; use XIRR for that.
* **Average holding days** = average age of each rupee invested, weighted by amount (up to the last NAV date).

### 2.2 XIRR (your annualised return)
The single yearly rate r that makes the present value of all cash flows zero:

    sum over flows of  amount_i / (1 + r) ^ ((date_i - date_first) / 365)  = 0

with the final value of your holding counted as a positive flow on the valuation date. It is the same convention as
Excel's XIRR and as the figures in your statements (the tool reproduces your statement's XIRR to the second decimal).
It is the right measure when money goes in at different times, because it weights each rupee by how long it was invested.

* It is a **money-weighted** return: it reflects both the fund's performance *and* your timing. Buying more after a fall
  raises it; buying before a fall lowers it.
* No answer is shown when flows are all one sign or fall on a single date.
* Over holding periods well under a year, annualising magnifies noise. Treat very short histories with caution.

### 2.3 Benchmarks
* **Own benchmark**: the index named in the fund's factsheet (for example Nifty India Manufacturing TRI).
* **Standard comparators**: Nifty 50 TRI and Nifty 500 TRI by default. They answer "would a plain index fund have done
  better?" which is a different question from "did the manager beat their own benchmark?".
* **TRI (Total Returns Index)** assumes dividends are reinvested. A **price index** ignores dividends and understates
  returns by roughly the dividend yield (about 1-1.5% a year for broad indices). Fund benchmarks are required to be TRI,
  so a factsheet name without the word TRI is treated as TRI (and the plan says so). A price series is used only if you
  explicitly allow the fallback, and is labelled when it is.

### 2.4 Same-cash-flow replication (the benchmark's XIRR and value)
Comparing a SIP's XIRR with an index's point-to-point return is unfair, because SIP money is invested at many different
index levels. Instead the tool replays your cash flows into the index:

1. For each of your flows, find the index level on that date (or the next trading day, up to 7 days later).
2. A deposit of Rs X buys X / level units of the index; a withdrawal sells units at that day's level.
3. On the valuation date, the units are valued at the last index level on or before that date.
4. The benchmark's XIRR is computed from the same flows and that final value.

Result: **benchmark value** (what the same deposits would be worth in the index) and **benchmark XIRR**.
Notes: the Rs 1 stamp duty on each SIP buys no fund units but the index copy invests the full amount, a tiny advantage to
the index (about Rs 100 over 100 SIPs). If the index data does not cover your dates, no number is produced.

### 2.5 Edge and rupee gap
* **Edge (points)** = fund XIRR - benchmark XIRR, in percentage points a year.
* **Gap (Rs)** = worth today - benchmark value. Positive = you are ahead of the benchmark in rupees.

They tell the same story in different units. Points describe the rate of out- or under-performance; rupees say what it
came to. A big edge on a small, recent investment can be a tiny rupee gap.

### 2.6 Whole portfolio
* **Whole-portfolio XIRR**: all funds' flows and values pooled.
* **Against each fund's own benchmark (blended)**: each fund's deposits are replayed into that fund's own benchmark, the
  resulting benchmark values are added up, and one XIRR is computed. Funds with different benchmarks are therefore blended
  in proportion to the money you put into each. "k of n funds" tells you if any fund lacked a benchmark.
* **Against a comparator**: all deposits of all funds replayed into the same index (for example Nifty 50 TRI).

### 2.7 Contribution to the gap
Because gaps are measured in rupees they add: portfolio gap = sum of fund gaps.
"Share of the total gap" = a fund's gap / total gap. Shares can exceed 100% or be negative when funds offset each other
(a fund 150% of the total just means others gave some back).

### 2.8 Cost drag (estimated distributor commission)
Regular plans carry a higher expense ratio than direct plans; the difference largely pays the distributor.

    estimated commission to date = integral over time of (value held) x (regular ER - direct ER) / 100

The value path is linear between transaction dates (so it improves once daily NAVs are used in later versions).
Next 12 months = (today's value + 6 x average monthly SIP of the last six months) x gap / 100, i.e. flat markets and
unchanged SIPs. Both need current regular **and** direct expense ratios from the same month; stale or mixed figures
distort the result. It does not include exit loads or taxes.

---

## 3. Risk and return from NAV history

All figures in this section use **daily NAV** and are computed over the period *you held each fund* (from your first
purchase to the valuation date). Returns are simple daily returns. A fund's series and its benchmark's are aligned on
dates both have. Ratios are not reported with fewer than 60 daily returns.

### 3.1 CAGR (lump-sum basis)
CAGR = (last NAV / first NAV) ^ (365 / days) - 1, as if everything had been invested on the first day. It describes the
fund's price path, not your SIP experience, so it can differ a lot from your XIRR (for example, if your SIPs bought cheaply
after a dip). *Excess* = fund CAGR - benchmark CAGR over the same window.

### 3.2 Volatility
Standard deviation of daily returns x sqrt(252). How bumpy the ride was. Higher is not "worse"; compare it with the
benchmark's and with the return earned.

### 3.3 Drawdown
For each day: drawdown = NAV / highest NAV so far - 1. **Maximum drawdown** is the worst value; the table gives the peak
date, trough date and the date the previous peak was regained (or "not yet"). **Current drawdown** is today's distance below
the highest NAV so far. It shows the pain of a bad stretch and how long recovery took, which volatility does not.

### 3.4 Beta and correlation
Beta = covariance(fund, benchmark) / variance(benchmark), on daily returns. 1.0 moves with the benchmark; 1.2 tends to
move 20% more in either direction; 0.8 moves 20% less. Correlation (-1 to 1) says how tightly the fund follows it.
Beta is only meaningful when correlation is reasonably high.

### 3.5 Alpha (Jensen's)
Annualised return not explained by the benchmark's movement:

    alpha = ( mean(fund) - rf_d - beta x (mean(benchmark) - rf_d) ) x 252      rf_d = (1 + risk-free)^(1/252) - 1

Positive alpha = the fund earned more than its beta to the benchmark would predict. Over short periods it is very noisy.

### 3.6 Tracking error and information ratio
Tracking error = standard deviation of (fund return - benchmark return) x sqrt(252): how far the fund strays from the
benchmark. Information ratio = (mean active return x 252) / tracking error: extra return per unit of straying. Above about
0.5 over a long period is considered good; over two years it is only indicative.

### 3.7 Up and down capture
Up capture = average fund return on days the benchmark rose / average benchmark return on those days x 100. Down capture is
the same for days it fell. Ideal: up above 100, down below 100. Example: up 90 / down 70 means the fund took 90% of the gains
but only 70% of the losses.

### 3.8 Sharpe and Sortino
Sharpe = average daily excess return over the risk-free rate / standard deviation of daily returns x sqrt(252).
Sortino replaces the standard deviation with **downside deviation** (root-mean-square of the days below the risk-free
rate), so only bad volatility counts. The risk-free rate is a setting (default 6% a year). When a fund returned less than
the risk-free rate, both are negative; that is arithmetic, not an error.

### 3.9 Rolling returns
For every day with enough history, the return over the previous N calendar months (6 or 12), measured from the last NAV
on or before the date N months earlier. They show *consistency*: a single point-to-point number depends on the start and end
dates, while rolling returns show the range you could have experienced. "% of days ahead" is the share of rolling windows in
which the fund beat its benchmark; windows overlap heavily, so these are not independent observations.

### 3.10 Calendar-year returns
Last NAV of the year / last NAV of the previous year - 1. The first year starts from the first NAV in your window
("from start") and the current year is labelled YTD. Part-years are not comparable with full years.

### 3.11 Portfolio-level (time-weighted) figures
Your portfolio's NAV-like index is built from the units you held each day, so SIP timing does not distort it:

    daily return = sum over funds of ( units held yesterday x change in NAV ) / sum over funds of ( units yesterday x NAV yesterday )

The **blended own benchmark** index uses each fund's own benchmark daily return, weighted by each fund's share of
yesterday's portfolio value (index levels are carried forward over non-trading days). Risk figures labelled PORTFOLIO use
these. They answer "how did the strategy behave?", whereas XIRR answers "what did my money earn?". Both are valid; they
differ whenever flows are large relative to the portfolio.

### 3.12 Daily value paths
For charts: units held x NAV each day (your value); cumulative money put in; and units of the index bought with each deposit
x that day's index level (each benchmark-equivalent value). The last point reconciles with section 2.

---

## 4. Overlap and look-through

Built from factsheet holdings (equity rows only; cash and repo are reported separately). Company names are matched after
removing case, punctuation, "&" vs "and", and words like Ltd / Limited / India, so "Mahindra & Mahindra Ltd." and
"Mahindra and Mahindra Limited" are one company.

* **Pairwise overlap (%)** = sum over companies held by both funds of the **smaller** of the two weights (weights as % of
  each fund). 0% = nothing in common; 100% = identical portfolios. The diagonal is the fund's own equity weight. Same formula
  for sectors.
* **Same companies vs same sectors:** funds can own different companies yet make the same sector bet. A low company overlap
  with a high sector overlap means diversification by name, not by theme.
* **Shared share**: the part of a fund's equity that sits in companies at least one other selected fund also holds.
* **Look-through exposure (Rs)** = sum over funds of (your rupees in the fund x the company's weight in the fund / 100).
  **Share of your money** = exposure / total rupees in the selected funds. This is what you effectively own, counting every
  fund. (Your valuation summary's top-10 list is the same calculation across all funds; the two agree.)
* **Coverage** warns when a factsheet itemises less than about 98% of the fund; overlap is then understated. The check
  also compares the equity listed with the equity the factsheet's own composition table declares: a gap usually means
  positions (for example derivatives) are not itemised in the factsheet itself, so it cannot be fixed by re-reading the PDF.

Limits: holdings are a snapshot at the factsheet date (usually the previous month-end); some factsheets list only the top
holdings; derivatives and unlisted instruments are not unwound; name matching is best-effort.

---

## 5. Reading the charts

| Chart | How to read it |
|---|---|
| Overview: value vs benchmarks | Blue = your value; grey dashed = money put in; each other line = the same deposits made into that index. "Each fund's own benchmark, combined" mirrors each fund's deposits into that fund's own benchmark and adds them up. Blue above a line = you are ahead of it. |
| Lead / lag over time | Blue minus each benchmark line, in rupees. Above zero = ahead; rising = outperforming at that time. |
| XIRR bars | Your annualised return on each fund next to its own benchmark's, for the same cash flows. |
| Rupees ahead / behind | One bar per fund: its rupee gap to its own benchmark. |
| Gap waterfall | The same gaps stacked so the portfolio total builds up; the last bar is the total. |
| Rolling return | Return over the previous 6 or 12 months at each date; blue above red = ahead of the benchmark over that window. |
| Calendar-year bars | Fund vs benchmark by year; first and last may be part-years. |
| Fall from the previous peak | 0% = at a new high; -20% = 20% below the highest price so far. Depth = size of the fall; time below zero = time to recover. |
| Up / down capture | Bars vs the 100 line (see 3.7). |
| Return vs volatility | Each dot a fund: higher = more return, further right = bumpier ride. Lump-sum basis. |
| Overlap heatmaps | Darker = more in common (see section 4). |
| Look-through bars | Rupees you effectively hold in each company, coloured by fund. |

---

## 6. Limitations

* **Short history.** About two years of data is one market regime. Ratios, rolling returns and fund comparisons over so
  short a period are indicative, not predictive. 3- and 5-year measures are not possible yet.
* **Benchmark choice drives conclusions.** A fund can look good against a narrow benchmark and poor against a broad one.
  Both views are shown on purpose.
* **Money-weighted vs time-weighted vs lump-sum.** XIRR, portfolio time-weighted returns and CAGR measure different things
  and rarely agree; none is "the" answer.
* **Not included:** taxes (capital-gains tax on switching or selling), exit loads, inflation, and any money flows outside
  your statement.
* **Costs are estimates.** Expense ratios change; the commission estimate assumes the full regular-direct gap goes to the
  distributor.
* **Data quality.** The PDF parsers are tuned to one distributor's layout and are validated against your own totals, but a
  new layout can break them (the tool reports rather than guesses). NAV and index files should come from reliable sources.
* **Past performance does not predict future returns.** The tracker describes what happened; it does not tell you what
  to do.

---

## 7. Glossary

**NAV** price of one fund unit. **SIP** regular fixed investment. **XIRR** annualised return for irregular cash flows.
**CAGR** compound annual growth rate between two dates. **TRI** total returns index (dividends reinvested). **TER** total
expense ratio, the yearly fund cost. **Regular / Direct plan** same fund; regular pays a distributor commission and has a
higher TER. **Drawdown** fall from a previous peak. **Beta** sensitivity to the benchmark. **Alpha** return beyond what beta
explains. **Tracking error** variability of the difference from the benchmark. **Look-through** counting what the funds
themselves hold.