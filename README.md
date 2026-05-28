# AI Equities Tracker

A clean, professional Streamlit dashboard to track **live performance and
fundamentals** for a curated universe of AI-related stocks. Phase 1 of a
personal investment-research platform.

All live numbers come from [Financial Modeling Prep (FMP)](https://financialmodelingprep.com/).
The Excel workbook is used only for static metadata (tickers, company names,
thematic buckets, AI-relevance notes) — its price/cap/multiple values are
treated as stale placeholders.

## Features

- **Performance view** — total return over Today / 1W / 1M / 3M / 6M / 1Y / 5Y /
  Max for every name, color-coded (green up / red down, intensity scaled to
  magnitude), sortable, with a summary strip and a **bucket heatmap** (median
  return per sub-theme × window).
- **Fundamentals view** — market cap, P/E, Fwd P/E, P/S, EV/EBITDA, margins,
  revenue growth, ROE, debt/equity, FCF margin and beta, consistently formatted,
  with graceful `—` for the unprofitable / data-sparse names.
- **Stock Detail view** — company header with logo, an interactive Plotly price
  chart with a window selector, window-return stat cards, key fundamentals, and
  a 5-year revenue / net-income bar chart.
- **Filters** — by primary bucket, by group (single names / ETFs / include-OTC
  toggle) and a free-text ticker/name search.
- **Caching** — quotes 15 min, fundamentals & profiles 1 day, the universe for
  the whole session; each ticker's price history is fetched once and sliced
  locally for all windows. A **Refresh data** button clears the caches.

## Setup

```bash
pip install -r requirements.txt
```

Set your FMP API key (never commit it):

```bash
cp .env.example .env
# edit .env and set FMP_API_KEY=your_real_key
```

The key is read from the `FMP_API_KEY` environment variable via `python-dotenv`.

## Run

```bash
streamlit run app.py
```

> **Note:** `AI_Equities_Universe.xlsx` must be present in the project root — the
> app loads the universe from it on startup.

## Project structure

```
app.py                 # Streamlit entry, nav, layout
data/fmp_client.py     # FMP API wrapper (session, retries, batching)
data/service.py        # Streamlit-cached data-access layer
data/universe.py       # load + parse the Excel universe
core/performance.py    # window-return computation + frame assembly
core/fundamentals.py   # assemble the fundamentals frame
ui/styles.py           # CSS injection + theme constants
ui/components.py       # reusable cards, tables, charts
.streamlit/config.toml # white/blue theme
```

## Notes

- The first load of the performance table fetches history for ~250 tickers; it
  shows a progress bar and is fast on subsequent loads thanks to caching.
- FMP failures (timeouts, HTTP errors, empty bodies, a single 429 retry with
  back-off) degrade gracefully: a ticker with no data is shown with `—` rather
  than crashing the table.
- FMP is the single data provider. No other data source is used.
