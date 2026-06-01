# ParkNova — AI Equities Analyzer

A polished, white/blue Streamlit dashboard for personal investment research over a
curated universe of ~226 AI-related stocks.

## Data sources & the strict division of labor

| Source | Role | Used for |
| --- | --- | --- |
| **Morningstar Excel** (`AI_Equities_Universe_Data from MorningStar.xlsx`) | Primary, static | **All** fundamentals, valuation, profitability, growth, financial health, Morningstar moat/rating/fair-value, and trailing returns (YTD/1Y/3Y/5Y). |
| **`bucket_mapping.csv`** | Static taxonomy | Curated **Primary Bucket** per ticker (the 10 AI sub-themes) + secondary bucket weights. Left-joined on `Ticker`. |
| **`crest_timing_mapping.csv`** | Static taxonomy | **Side** (Supplier / Demand-Neocloud / Hyperscaler) and **Crest** (Early / Mid / Late capex-wave layer) per ticker + a rationale line. Left-joined on `Ticker`; missing → Side=Unknown, Crest=Mid (logged). |
| **Financial Modeling Prep (FMP)** — **stable API** | Live, secondary | Short-window momentum (Today/1W/1M/3M/6M from daily close), news, and (if your plan includes them) institutional / insider data. `data/fmp_client.py` targets `https://financialmodelingprep.com/stable/...` (the legacy `/api/v3` paths now return HTTP 403 "Legacy Endpoint"). Each method has an inline comment with its exact stable URL + params; responses are normalised back to the field names the app uses. |
| **Finnhub** (free tier) | Live quotes + history fallback | Near-real-time US quotes (price + today's %), and daily candles used as a **history fallback** when FMP history is unavailable. Set `FINNHUB_API_KEY` (free at [finnhub.io](https://finnhub.io)). Note: free-tier candles are currently gated for many symbols, so FMP-stable is the primary history source. |
| **SEC EDGAR** | Live, authoritative | Recent SEC filings (10-K/Q, 8-K, S-1/424B, SC 13D/G). Free; requires a descriptive User-Agent. |
| **Claude Max plan** via the `sec-filing-analyzer` skill | Primary (filing analysis) | You analyze a filing inside Claude and **import the JSON** — zero marginal cost. This is the default filing-analysis path. |
| **Anthropic API** *(optional fallback)* | Opt-in, off by default | The News & Filings narrative summary, plus a paid per-filing "Analyze" path — only when `ANTHROPIC_API_KEY` is set **and** the Settings-popover fallback toggle is on; never auto-run. |

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
- **Capex Cycle** (timing dashboard) — a **Crest × Side matrix** (where capital
  is concentrated: count + median 1Y + median forward P/E per cell), a **rotation
  tracker** (median return per crest layer × window, with a *computed* caption like
  "Last 1M: Early-crest +X% vs Late-crest −Y% → capital rotating toward
  early-crest layers" — derived from the data, falling back to Morningstar YTD/1Y
  if the live feed is empty), and a **layer leaderboard** (constituents of a crest
  layer with pullback-from-52w-high, forward P/E, valuation tier, and the
  value-trap chip). The framework: **Side** = sells into the buildout (Supplier) /
  buys the compute (Demand-Neocloud) / Hyperscaler counterparty; **Crest** = where
  the layer sits in the capex wave (Early chips/memory/optics → Mid equipment/
  software/networking → Late power/cooling/grid); plus a **valuation tier** and a
  **value-trap flag** (low forward P/E + Early-crest cyclical + extended 1Y run —
  the "single-digit memory at the cycle top" signature). Side/Crest are filters
  and chips across every data view and Stock Detail; the Screener supports
  timing-aware value (crest tilt + sort by upside-to-fair-value, value-trap
  flagged). A **Crest trends** tab charts rebased-to-100 equal-weighted return
  **indices** per crest layer (Early/Mid/Late) and per side (Supplier/Demand/
  Hyperscaler) over a selectable window, with a computed rotation caption, the
  **Early − Late spread** as a single tracked signal, a constituent-count strip,
  and a prominent **backcast caveat** (indices apply the *current* 2026 crest
  labels to historical prices; names enter only on dates they have real history).
  Indices are built once from FMP-stable history (`core/crest_index.py`),
  persisted via `core/store.py` (local, or S3 when `PARKNOVA_S3_BUCKET` is set),
  refreshed daily, with a manual **Rebuild indices** button.
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
  - *Optional paid fallback:* with `ANTHROPIC_API_KEY` set **and** the Settings-popover
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
- **Thesis** (conviction-weighted decision log) — pick a ticker, see an
  **auto-captured evidence snapshot** (factor radar + composite, valuation /
  value-trap, crest/side chips, returns — reusing `core/factors.py` and
  `core/timing.py`, never recomputed), then write a structured thesis (stance,
  conviction 1-10, horizon, intended position size %, bull/bear, valuation view,
  catalysts, risks, crest note, defined exit). Saving appends a **timestamped
  journal entry that freezes the evidence snapshot**, so a **conviction-over-time
  chart** renders and you can see months later whether the call was right.
  Records persist to `.cache/theses/{ticker}.json` via `core/store.py` (local, or
  S3 when `PARKNOVA_S3_BUCKET` is set). A **Portfolio rollup** lists every saved
  thesis (stance / conviction / size % / live upside-to-FV / crest / value-trap),
  sums intended sizes (flags > 100%), and groups exposure by crest layer. An
  **Export thesis brief** button produces a paste-ready JSON for drafting in
  Claude (Max plan) via the `skills/thesis-drafter` skill — import the result
  back; no paid API call.

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
#              FINNHUB_API_KEY=your_real_key   (free at finnhub.io; live quotes + history fallback)
# optional:    ANTHROPIC_API_KEY=...           (enables the opt-in AI summary)
```

> The Morningstar views (fundamentals, factors, buckets) work without any key.
> Live momentum, news, and insider/institutional data need `FMP_API_KEY`.
> `FINNHUB_API_KEY` adds near-real-time quotes and a daily-candle history fallback
> (useful when FMP's plan doesn't include `historical-price-full`). SEC filings
> need no key. The AI summary appears only when `ANTHROPIC_API_KEY` is set.

### Live data: quotes, history, diagnostics

- **"Today" precedence (meaningful 24/7):** live intraday — Finnhub `dp` when
  non-null/non-zero (market open, tagged "live") → **EOD fallback** — the last
  completed session's close-to-close change from the already-cached daily history
  (market closed / Finnhub flat, tagged "last close" + session date) → "—" only
  when no history exists. Computed via `last_session_change()` in
  `core/performance.py`, reusing the single cached per-ticker history (no extra
  calls). The Performance "Today" column never shows a bare blank when history
  exists.
- **History precedence** (chart + 1W/1M/3M/6M windows): FMP stable EOD → Finnhub
  daily candles → "—" with the reason shown. Each ticker's history is fetched
  once and all windows (and the EOD "Today" fallback) are sliced from it.
- **Cross-sectional scores are universe-wide:** momentum and all factor/composite
  z-scores are computed across the full 226-name universe *before* any filter;
  filtering only subsets rows for display. Filtering to a single ticker shows its
  true universe-relative score (e.g. MU momentum), never a degenerate 0.00.
- **Near-real-time refresh:** Stock Detail's quote and a Performance "Live quotes"
  strip poll via `st.fragment(run_every=...)`; the interval (Off / 15s / 30s /
  60s) is set in **Settings**. Only the selected/visible tickers poll — never all
  226. Quotes are cached 15s, history 15m.
- **Settings → Data diagnostics** probes each live endpoint for a test ticker and
  shows the real reason a call fails — including FMP's verbatim plan-gated /
  bad-key / usage message (returned as HTTP 200 JSON, not an HTTP error).

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
app.py                  # entry, horizontal top-nav (option-menu), toolbar filters, CSS
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
