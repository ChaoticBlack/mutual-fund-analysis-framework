# Portfolio tracker

Turns your mutual fund statements into validated Excel workbooks and measures every fund against its own
benchmark, using the same cash flows (your actual SIP dates and amounts). Everything runs locally.

| Module | Status | What it does |
|---|---|---|
| 1. Ingestion | done | Statement / valuation / factsheet PDFs -> validated Excel |
| 2. Index data | done | Works out which benchmark indices a portfolio needs, loads and stitches files, optional download |
| 3. Analytics | done | XIRR, same-cash-flow benchmark comparison, rupee gap, cost drag |
| 4. NAV history + risk | done | Daily NAVs, rolling returns, drawdown, volatility, beta, alpha, capture ratios, Sharpe/Sortino |
| 5. Streamlit app | done | Charts, tables, uploads and downloads on top of modules 1-4 |

## Setup
    python -m venv .venv
    .venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt   # pdfplumber, pandas, openpyxl, scipy, streamlit, plotly, requests

## The app
    streamlit run app.py

Run it from the project folder. In the sidebar set **Data folder** (for example `E:\portfolio_data`) and, under *Other
folders*, the index folder if yours is not called `indexes` (for example `E:\portfolio_data\indices`). The same
settings can be preset with the environment variable `PT_DATA_DIR`.

| Tab | What it shows |
|---|---|
| Overview | Money put in, value today, XIRR, edge vs own benchmarks and vs Nifty 50, daily value chart vs benchmark-equivalent values, lead/lag over time |
| Funds | One fund at a time: value vs benchmarks, 6/12-month rolling returns, calendar years, drawdown, risk numbers |
| Compare | XIRR, rupee gap and contribution by fund; tables against own benchmarks and comparators |
| Risk | Risk table, up/down capture, return vs volatility, rolling-return consistency, comparator metrics |
| Costs | Estimated regular-plan commission so far and next 12 months |
| Holdings | Allocation from your valuation summary and per-fund factsheet holdings and sectors |
| Data & Setup | What is ready, index and NAV plans, optional online downloads, warnings, ingestion log, Excel downloads |

The sidebar's *Add or update data* panel takes the statement, valuation and factsheet PDFs (read in memory, then
discarded) and index / NAV files. Everything the command line does can be done from there.

## Suggested data folder
    data/
      portfolio.xlsx      created by `ingest`
      factsheets.xlsx     created by `factsheet`
      costs.xlsx          YOUR expense-ratio sheet (see "Expense ratios")
      indexes/            index files you download (and optional aliases.csv)
      nav/                daily NAV files per fund (downloaded by `nav fetch` or dropped in by hand)
      analysis.xlsx       created by `analyze`
      risk.xlsx           created by `risk`

## Workflow
    # 1. Statements -> portfolio.xlsx   (transaction statement required, valuation summary optional)
    python -m ptracker ingest --txn TxnStatement.pdf --valuation PortfolioValuationSummary.pdf --out data/portfolio.xlsx -v

    # 2. Factsheets -> factsheets.xlsx, then copy benchmark / expense ratio / exit load into the portfolio
    python -m ptracker factsheet Factsheet_*.pdf --out data/factsheets.xlsx
    python -m ptracker link --portfolio data/portfolio.xlsx --factsheets data/factsheets.xlsx

    # 3. What index data does this portfolio need, and what is missing?
    python -m ptracker indexes plan --portfolio data/portfolio.xlsx --dir data/indexes

    # 4. Put the missing index files in data/indexes (manual download, or `indexes fetch`), then re-run step 3
    #    until every row says `ready`.

    # 5. Expense ratios (regular and direct) -> Schemes sheet
    python -m ptracker costs import --portfolio data/portfolio.xlsx --file data/costs.xlsx

    # 6. Analyse
    python -m ptracker analyze --portfolio data/portfolio.xlsx --dir data/indexes --out data/analysis.xlsx

    # 7. Daily NAV history for each fund (needed for risk metrics), then risk and rolling returns
    python -m ptracker nav plan  --portfolio data/portfolio.xlsx --nav-dir data/nav
    python -m ptracker nav fetch --portfolio data/portfolio.xlsx --nav-dir data/nav     # optional download
    python -m ptracker risk --portfolio data/portfolio.xlsx --dir data/indexes --nav-dir data/nav --out data/risk.xlsx

Re-running `ingest` with a newer statement merges new transactions and never duplicates old ones.

## Command reference
| Command | Purpose | Useful options |
|---|---|---|
| `ingest` | statement (+ valuation) PDF -> `portfolio.xlsx` | `--no-name`, `--password`, `--force`, `-v` |
| `factsheet` | factsheet PDFs -> `factsheets.xlsx` | `--password` |
| `link` | fill benchmark / expense ratio / exit load from factsheets | `--portfolio`, `--factsheets` |
| `indexes scan` | list the index files found and their coverage | `--dir` |
| `indexes plan` | which indices are needed, status of each, and what to do | `--comparators "A,B"` |
| `indexes inspect` | show how one index file is read (columns, type, dates) | `--file` |
| `indexes fetch` | optional automatic TRI download for missing ranges | `--dry-run`, `--yes` |
| `costs import` | read expense ratios from your spreadsheet | `--overwrite` |
| `nav scan` / `nav plan` | NAV files found / which funds still need NAV history (also checks NAVs against your statement prices) | `--nav-dir` |
| `nav fetch` | optional NAV download via AMFI + MFapi.in | `--dry-run`, `--yes` |
| `risk` | risk metrics, rolling returns, daily values | `--out`, `--risk-free 6.0`, `--comparators`, `--allow-price-fallback` |
| `analyze` | performance vs benchmarks, cost drag, Excel output | `--out`, `--comparators`, `--allow-price-fallback` |

## The portfolio workbook
`portfolio.xlsx` sheets: Meta, **Schemes** (one row per fund), **Transactions**, Snapshots, Valuation, Folios,
SIP_Summary, Allocations, Parse_Log. In Schemes you can edit `benchmark_name`, `benchmark_file`, `nav_file`,
`expense_ratio_regular_pct`, `expense_ratio_direct_pct` and `notes`; these survive re-ingestion.
`factsheets.xlsx` sheets: Funds, Holdings, TrailingReturns, CalendarReturns, SectorExposure.

## Benchmark index data
Benchmarks are not hard-coded. Each fund's benchmark comes from its factsheet (or what you type in
`benchmark_name`), plus two standard comparators: Nifty 50 TRI and Nifty 500 TRI.

* **Use Total Returns Index (TRI), not price data.** On niftyindices.com go to Reports -> Historical Data, switch the
  report type to *Total Returns Index* (not the default price view with Open/High/Low/Close) and download the CSV for
  each index. A price series leaves out dividends and understates the benchmark by roughly 1-1.5% a year.
* NSE may cap one download at about a year; several files per index are stitched automatically and overlaps are
  checked. File names do not matter; the index name inside the file (or the file name) is used.
* Fund benchmark names without the word TRI are treated as TRI (SEBI requires TRI benchmarks); the plan notes this.
* `indexes plan` status values: `ready`, `partial` (dates missing), `price_only` (never used silently),
  `missing`, `manual_only` (BSE or other providers), `unsupported_variant` (NTR), `no_benchmark`.
* **Name mismatches:** if a factsheet says `NIFTY CONSUMPTION` but you downloaded `NIFTY INDIA CONSUMPTION`, add a
  file `aliases.csv` to the index folder:

        alias,canonical
        NIFTY CONSUMPTION,NIFTY INDIA CONSUMPTION

  Or put a specific file name in the fund's `benchmark_file` cell to override everything for that fund.
* `indexes fetch` uses niftyindices.com's own undocumented endpoint. It can break or be blocked at any time, requests
  only missing dates (1 request/second) and saves normal CSVs into the same folder. It has been tested against the
  documented request/response format, not the live service. If it fails, download manually.

## Expense ratios
The file for `costs import` is **your own expense-ratio spreadsheet**; the tool never creates it. It needs three columns
(any other columns, and extra header rows above the header, are ignored):

    Fund Name | Regular Expense Ratio | Direct Expense Ratio

Where to get the numbers (use the same month for both plans):
* AMFI website -> "TER of MF Schemes": choose financial year, month, fund type, category and fund house.
* The fund house's scheme page (shows both plans) and the scheme factsheet (usually regular plan only).

SEBI's new expense framework applies from 1 April 2026 and has changed TERs, so figures from older sources may be stale.
`costs import` matches fund names loosely, fills empty cells only, and reports (but keeps) any existing value that
differs from the sheet; use `--overwrite` to replace.

## Daily NAV history
`risk` needs a daily NAV series for every fund. Either route works and they can be mixed:
* **Manual:** put files in `data/nav/`. Accepted: CSV/XLSX with `Date` and `NAV` columns (ISIN in the file name, or an
  `ISIN` column, or the fund name in the file name), or AMFI NAV-history text downloads (AMFI limits each download to a
  short period, so several files per fund are fine; overlaps are merged). To force a file for a fund, put its name in the
  `nav_file` cell of the Schemes sheet.
* **Automatic (optional, needs `requests`):** `nav fetch` looks up each fund's AMFI scheme code from its ISIN using AMFI's
  `NAVAll.txt`, then downloads the missing dates from MFapi.in (a free community API, not run by AMFI). It can change or
  disappear; it was tested against the documented formats, not the live service. Files are saved as `<ISIN>.csv`.
* `nav plan` also compares your statement's prices with the NAV history on each transaction date (a good sign the right
  fund and plan were downloaded: expect all or nearly all to match).

## What `risk` computes
* Per fund (window starts at your first investment in it) and for the whole portfolio (time-weighted, unit-based, so SIP
  timing does not distort it): CAGR vs benchmark, annualised volatility, maximum drawdown with peak, trough and recovery,
  current drawdown, beta, Jensen's alpha, correlation, tracking error, information ratio, up/down capture, Sharpe, Sortino.
* The same relative metrics against each standard comparator, 6- and 12-month rolling returns (with the share of days the
  fund was ahead of its benchmark), calendar-year returns, and daily value / net-invested / benchmark-equivalent value
  paths for charts.
* Conventions: daily returns, 252 trading days a year, risk-free rate 6% (change with `--risk-free`), up/down capture from
  daily returns. With roughly two years of history, treat ratios as indicative; ratios are withheld below 60 daily returns,
  and 3- or 5-year figures are not possible yet.

## What `analyze` computes
* **Fund XIRR** from your transactions (actual days / 365, same convention as the statements).
* **Benchmark XIRR** with the same cash flows invested in the index on the same dates (first index level on or after
  each date), valued on the last NAV date. Gap in rupees = fund value minus benchmark-equivalent value.
* Against each fund's own benchmark, against the comparators, and for the whole portfolio (blended own benchmarks).
* Where the portfolio gap comes from, fund by fund.
* **Cost drag:** estimated regular-plan commission to date and for the next 12 months (needs both expense ratios).
* Timelines per fund (net invested, fund value, benchmark-equivalent value) in the Excel output.

Limits: fund values in timelines use NAVs on transaction dates only until Module 4; XIRR on money held under a year
is noisy; the cost estimate assumes the distributor receives the whole regular-direct gap and flat markets ahead.

## Privacy
Address, phone, e-mail, bank account and nominee are never written; PAN is stored masked (`*****1234*`). A test scans
the generated workbook to enforce this. Keep your data folder out of any shared repository.

## Validation on every ingest
Unit balances are continuous, closing units x NAV = market value, purchases = purchase outstanding, scheme totals =
grand totals, and the transaction statement agrees with the valuation summary. A failed check stops the write.

## Tests
The app is tested with stand-ins for Streamlit and Plotly (`tests/fakes.py`), which proves every page runs on real computed
data but cannot judge appearance. Report any visual or runtime problem you see in the real app.

    python -m unittest discover -s tests -v
    # with your real files (also enables the known-answer tests):
    set PT_SAMPLE_DIR=C:\path\to\pdfs
    set PT_INDEX_DIR=C:\path\to\data\indexes
    set PT_PORTFOLIO=C:\path\to\data\portfolio.xlsx   # also enables the known-answer analytics test
    python -m unittest discover -s tests -v

## Code layout
    ptracker/
      cli.py  __main__.py  util.py  validation.py  storage.py
      ui/         service.py  charts.py  main.py        (app.py in the project root starts it)
      parsers/    txn_statement.py  valuation_summary.py  factsheet.py
      indexes/    spec.py  files.py  plan.py  nse_fetch.py
      nav/        amfi.py  store.py  mfapi.py  nav_plan.py
      analytics/  xirr.py  replicate.py  resolve.py  engine.py  costs.py  risk.py
    tests/

## Parser scope
Tuned to one distributor's statement layout. Redemption, switch and dividend rows are covered by synthetic tests only;
if a real statement contains them and parsing warns, the offending line is reported rather than dropped.