# ParkNova — AI Equities Analyzer

A polished, white/blue Streamlit dashboard for personal investment research over a
curated universe of ~226 AI-related stocks.

## Data sources & the strict division of labor

| Source | Role | Used for |
| --- | --- | --- |
| **Morningstar Excel** (`AI_Equities_Universe_Data from MorningStar.xlsx`) | Primary, static | **All** fundamentals, valuation, profitability, growth, financial health, Morningstar moat/rating/fair-value, and trailing returns (YTD/1Y/3Y/5Y). |
| **Financial Modeling Prep (FMP)** | Live, secondary | **Only** short-window momentum (Today/1W/1M/3M/6M, from daily adjusted-close history) and per-ticker news. |

Fundamentals are never fetched from an API; live prices/returns for the short
windows are never read from the spreadsheet.

## Features

- **Performance & Momentum** — merges Morningstar YTD/1Y/3Y/5Y with live
  Today/1W/1M/3M/6M, color-coded and sortable, plus a blended-momentum ranking
  and a sector × window median-return heatmap.
- **Fundamentals & Factors** — a fundamentals table with toggleable metric
  groups (Valuation / Profitability / Growth / Financial Health / Morningstar
  Verdicts / Statement Absolutes), and a cross-sectional **factor-scoring** table
  (Value, Quality, Growth, Financial Health, Momentum, Morningstar Upside) shown
  as 0–100 percentiles.
- **Screener** — adjust factor weights with sliders to re-rank the universe live
  by the weighted composite.
- **News** — clean FMP news feed per ticker (headline, source, relative time,
  sentiment chip, link).
- **Stock Detail** — header with rating ★, moat chip and fair-value upside; live
  Plotly price chart with a window selector; the 9 return windows as stat cards;
  a factor radar vs the universe median; grouped fundamentals; and a news feed.

## Factor methodology (`core/factors.py`, pure & testable)

Each raw metric is winsorized at ±3σ and z-scored cross-sectionally; sparse
columns (<30% coverage) fall back to a rank-based signal. "Lower-is-better"
metrics (valuation multiples, leverage) are sign-flipped so higher always means
better. A stock's factor score is the **mean of available** sub-signals (missing
values are ignored, never treated as 0), then mapped to a 0–100 percentile. The
weighted composite averages the per-factor z-scores with the slider weights
(renormalised per row over the factors that stock actually has).

## Setup

```bash
pip install -r requirements.txt
```

Set your FMP API key (never commit it). Works with a local `.env` **or** a
platform-set environment variable:

```bash
cp .env.example .env
# edit .env -> FMP_API_KEY=your_real_key
```

> The Morningstar views work without a key; live momentum and news need it.

## Run

```bash
streamlit run app.py
```

> **Note:** `AI_Equities_Universe_Data from MorningStar.xlsx` must be in the
> project root — the app loads the universe from it on startup.

## Project structure

```
app.py                  # entry, nav, layout, CSS injection
data/morningstar.py     # load + clean the Excel (column registry, type coercion, missing handling)
data/fmp_client.py      # FMP wrapper: history, quotes, news (session, retries, batching)
data/service.py         # Streamlit-cached live data access
core/performance.py     # merge Morningstar trailing returns + live momentum windows
core/factors.py         # z-score/winsorize, factor composites, weighted ranking
ui/styles.py            # CSS + theme constants
ui/components.py        # cards, tables, charts, chips/badges, radar, news feed
.streamlit/config.toml  # white/blue theme
```

## Engineering notes

- **Caching:** Morningstar load + factor inputs are cached for the session
  (static). Live quotes/history are cached 15 min; news 30 min. Each ticker's
  history is fetched once and sliced locally for every momentum window and the
  detail chart. A **Refresh live data** button clears only the live caches.
- **Defensive FMP:** timeouts, HTTP errors, empty bodies and a single 429 retry
  with back-off all degrade to "—" rather than crashing.
- **Data-quality reality:** Morningstar coverage is partial (P/E ~151/226, moat
  ~118/226, etc.) and many high-growth names are unprofitable. Percent columns
  are kept in percent units (never ×100); large absolutes are formatted B/M;
  blanks render as "—" everywhere and are never coerced to 0.
- Only the Morningstar file and FMP are used as data sources.
```
