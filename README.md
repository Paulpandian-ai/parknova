# ParkNova — AI Equities Analyzer

A polished, white/blue Streamlit dashboard for personal investment research over a
curated universe of ~226 AI-related stocks.

## Data sources & the strict division of labor

| Source | Role | Used for |
| --- | --- | --- |
| **Morningstar Excel** (`AI_Equities_Universe_Data from MorningStar.xlsx`) | Primary, static | **All** fundamentals, valuation, profitability, growth, financial health, Morningstar moat/rating/fair-value, and trailing returns (YTD/1Y/3Y/5Y). |
| **`bucket_mapping.csv`** | Static taxonomy | Curated **Primary Bucket** per ticker (the 10 AI sub-themes) + secondary bucket weights. Left-joined on `Ticker`. |
| **Financial Modeling Prep (FMP)** | Live, secondary | Short-window momentum (Today/1W/1M/3M/6M from daily adjusted-close), news, and (if your plan includes them) institutional 13F + insider Form-4 data. |
| **SEC EDGAR** | Live, authoritative | Recent SEC filings (10-K/Q, 8-K, S-1/424B, SC 13D/G). Free; requires a descriptive User-Agent. |
| **Claude Max plan** via the `sec-filing-analyzer` skill | Primary (filing analysis) | You analyze a filing inside Claude and **import the JSON** — zero marginal cost. This is the default filing-analysis path. |
| **Anthropic API** *(optional fallback)* | Opt-in, off by default | The News & Filings narrative summary, plus a paid per-filing "Analyze" path — only when `ANTHROPIC_API_KEY` is set **and** the sidebar fallback toggle is on; never auto-run. |

Fundamentals are never fetched from an API; live prices/returns for the short
windows are never read from the spreadsheet.

## The Primary Bucket taxonomy (`bucket_mapping.csv`)

Morningstar's `Sector` is too coarse for AI analysis (NVDA/MU/ASML all land in
"Technology"; CEG/VST/OKLO in "Utilities"/"Energy"). A curated taxonomy of 10
buckets is used instead, joined onto the Morningstar frame on `Ticker`:

`1 Compute Semi` · `2 Memory` · `3 Foundry/Semicap` · `4 Networking` ·
`5 Power/Cooling` · `6 AI Software` · `7 Hyperscaler` · `R Robotics/Autonomy` ·
`X Edge AI/Vision` · `Q Quantum`

Every ticker gets a bucket; any unmapped ticker falls back to `Unclassified`
(logged, never a crash). Secondary bucket weights (e.g. `"1:0.70/4:0.20/6:0.10"`)
are parsed into a dict for future exposure-splitting. Buckets are a first-class
filter and grouping dimension across the app, rendered as colored chips.

## Features

- **Performance & Momentum** — merges Morningstar YTD/1Y/3Y/5Y with live
  Today/1W/1M/3M/6M, color-coded and sortable, with a Primary-Bucket column +
  filter, a blended-momentum ranking, and a median-return heatmap with a
  **Bucket / Sector toggle**.
- **Fundamentals & Factors** — a fundamentals table with toggleable metric
  groups, and a cross-sectional **factor-scoring** table (Value, Quality, Growth,
  Financial Health, Momentum, Morningstar Upside) shown as 0–100 percentiles.
- **Screener** — adjust factor weights with sliders to re-rank the universe live.
- **Buckets** (slice-and-dice) — a per-bucket summary table (counts, returns,
  median P/E/ROE/Rev-growth, median factor scores, avg ★), a return leaderboard
  bar chart, a constituent drill-down, and a bucket-vs-universe factor profile.
- **News & Filings** — for a selected ticker: a deterministic "At a glance"
  digest (news sentiment tally, filing counts 30/90d, net 90-day insider $, top
  institutional changes, implied upside), an optional AI summary, then tabs for
  News (with a "leading sources only" toggle), SEC Filings (badged), Institutional
  holders, and Insider transactions.
- **Filing analysis — import-first, zero cost** (SEC Filings tab). The primary
  path uses your **Claude Max plan**, not the paid API: each filing shows its
  accession + a copyable EDGAR doc URL + a one-line "filing reference". You copy
  that into Claude, run the `sec-filing-analyzer` skill to get a JSON object back,
  and **import it** (drag onto the uploader, or drop the file into
  `.cache/filings/imported/`). The app indexes imports by accession (dashes
  ignored) and renders the structured result inline under the matching filing —
  what/why, material facts, a key-figures table, guidance changes, risks/flags, a
  sentiment chip and net read — tagged **"Imported · analyzed in Claude"**, with
  no API call. Imports survive restarts (they're files on disk) and take
  precedence over everything.
  - *Optional paid fallback:* with `ANTHROPIC_API_KEY` set **and** the sidebar
    "Enable paid API analysis (fallback)" toggle on (off by default), filings
    without an import get an "Analyze (paid API)" button. On click only, the app
    fetches the document from EDGAR, converts HTML → clean text, trims for cost
    (full text for 8-K/6-K/13D-G; MD&A + Risk Factors + Results section-extraction
    for 10-K/10-Q, first-40k fallback, hard 60k cap), and calls Anthropic
    (Haiku default; Sonnet selectable). These results are disk-cached by
    accession + model. Nothing ever runs on page load.
- **Stock Detail** — header with bucket chip, rating ★, moat chip and fair-value
  upside; live Plotly price chart with a window selector; the 9 return windows as
  stat cards; a factor radar vs the universe median; grouped fundamentals; and a
  compact News & Filings pane.

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
# optional: ANTHROPIC_API_KEY=...   (enables the opt-in AI summary)
```

> The Morningstar views (fundamentals, factors, buckets) work without any key.
> Live momentum, news, and insider/institutional data need `FMP_API_KEY`. SEC
> filings need no key. The AI summary appears only when `ANTHROPIC_API_KEY` is
> set and the toggle is on.

### SEC EDGAR User-Agent

SEC requires a descriptive `User-Agent` on every request. The default in
`data/edgar_client.py` (`ParkNova research contact@parknova.app`) works; edit it
to your own contact string if you prefer.

## Run

```bash
streamlit run app.py
```

> **Note:** both `AI_Equities_Universe_Data from MorningStar.xlsx` and
> `bucket_mapping.csv` must be in the project root.

## Project structure

```
app.py                  # entry, nav, layout, CSS injection
data/morningstar.py     # load + clean the Excel + join bucket_mapping.csv taxonomy
data/fmp_client.py      # FMP wrapper: history, quotes, news, institutional, insider
data/edgar_client.py    # SEC EDGAR: CIK resolution, filings, document fetch + HTML→text + trim
data/anthropic_client.py# opt-in LLM: narrative summary + per-filing analysis (configurable model)
data/service.py         # Streamlit-cached live data access (FMP + EDGAR) + filing analysis
core/performance.py     # merge Morningstar trailing returns + live momentum; heatmaps
core/factors.py         # z-score/winsorize, factor composites, weighted ranking
core/synthesis.py       # deterministic "At a glance" digest (pure functions)
core/filing_cache.py    # on-disk cache for LLM filing analyses (.cache/filings/)
ui/styles.py            # CSS + theme constants + bucket palette
ui/components.py        # cards, tables, charts, chips/badges, radar, news/filings feeds
.streamlit/config.toml  # white/blue theme
bucket_mapping.csv      # curated Primary Bucket taxonomy
.cache/                 # disk-persisted filing analyses (gitignored)
```

## Engineering notes

- **Caching:** Morningstar load + bucket join + factor inputs are cached for the
  session (static). Live quotes/history 15 min; news 30 min; institutional /
  insider / EDGAR / filing-document text 1 day. Each ticker's history is fetched
  once and sliced locally for every momentum window and the detail chart. LLM
  filing analyses are cached **to disk** (`.cache/filings/`, keyed by accession
  number + model) so a filing is analyzed at most once ever — this cache survives
  restarts and is deliberately *not* cleared by the **Refresh live data** button
  (a filing's content never changes).
- **Cost discipline (filing analysis):** nothing runs on page load or tab switch;
  every analysis is click-triggered. Long filings are section-extracted (10-K/Q)
  or truncated with disclosure, then hard-capped at 60k chars before the call.
  Default model is Haiku; Sonnet is opt-in per analysis. A cache hit makes no API
  call and is tagged "cached".
- **Defensive network:** timeouts, HTTP errors, empty bodies and a single 429
  retry with back-off all degrade to "—"/"no data" rather than crashing. If an
  FMP plan doesn't include the institutional/insider endpoints, the view shows a
  clear "not available on current data plan" note.
- **Data-quality reality:** Morningstar coverage is partial (P/E ~151/226, moat
  ~118/226, etc.) and many high-growth names are unprofitable. Percent columns
  are kept in percent units (never ×100); large absolutes are formatted B/M;
  blanks render as "—" everywhere and are never coerced to 0.
- Data sources are limited to the Morningstar file, `bucket_mapping.csv`, FMP,
  SEC EDGAR, and the optional Anthropic API.
```
