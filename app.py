"""ParkNova — AI Equities Analyzer.

A personal investment-research dashboard over a curated ~226-stock AI universe.
Morningstar export = primary fundamentals/returns; FMP = live momentum + news.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit_option_menu import option_menu

from core import factors as fc
from core import performance as perf
from core import synthesis as synth
from data import anthropic_client as anth
from data import morningstar as ms
from data import service
from ui import components as cp
from ui import styles

load_dotenv()

st.set_page_config(page_title="ParkNova — AI Equities Analyzer",
                   layout="wide", initial_sidebar_state="collapsed")
styles.inject_css()


# ---------------------------------------------------------------------------
# Filters — applied from persistent session_state; rendered as a top toolbar
# ---------------------------------------------------------------------------
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Filter ``df`` using the shared, persisted toolbar state."""
    out = df
    if st.session_state.get("buckets"):
        out = out[out["Primary Bucket"].isin(st.session_state["buckets"])]
    if st.session_state.get("sectors"):
        out = out[out["Sector"].isin(st.session_state["sectors"])]
    if st.session_state.get("styles"):
        out = out[out["Stock Style Box"].isin(st.session_state["styles"])]
    q = (st.session_state.get("search", "") or "").strip().lower()
    if q:
        mask = (out["Ticker"].str.lower().str.contains(q, na=False)
                | out["Name"].str.lower().str.contains(q, na=False))
        out = out[mask]
    return out.reset_index(drop=True)


def filter_toolbar(df: pd.DataFrame, *, bucket=True, sector=True, style=True):
    """Render a compact one-row filter toolbar under the view title.

    All controls write to shared session_state keys (search/buckets/sectors/
    styles) so state persists across views and reruns. Only the requested
    filters are shown; the row wraps gracefully on narrow widths.
    """
    specs = [("search", 2.0)]
    if bucket:
        specs.append(("buckets", 2.2))
    if sector:
        specs.append(("sectors", 2.2))
    if style:
        specs.append(("styles", 2.0))
    cols = st.columns([w for _, w in specs])
    for col, (kind, _) in zip(cols, specs):
        with col:
            if kind == "search":
                st.text_input("Search", key="search",
                              placeholder="Ticker or name",
                              label_visibility="collapsed")
            elif kind == "buckets":
                st.multiselect("Primary bucket", ms.bucket_options(df),
                               key="buckets", placeholder="Primary bucket",
                               label_visibility="collapsed")
            elif kind == "sectors":
                st.multiselect("Sector", ms.sector_options(df), key="sectors",
                               placeholder="Sector", label_visibility="collapsed")
            elif kind == "styles":
                st.multiselect("Style box", ms.style_options(df), key="styles",
                               placeholder="Style box",
                               label_visibility="collapsed")


def settings_popover() -> None:
    """Top-right Settings popover: refresh, live-interval, diagnostics, toggles."""
    with st.popover("Settings", use_container_width=True):
        if st.button("Refresh live data", width="stretch"):
            service.clear_live_caches()
            st.success("Live caches cleared.")
            st.rerun()

        # Live-quote refresh interval (drives the @st.fragment polling).
        st.selectbox("Live quote refresh", ["Off", "15s", "30s", "60s"],
                     index=1, key="live_interval",
                     help="How often the selected ticker's quote polls Finnhub. "
                          "'Off' keeps quotes static to conserve API calls.")

        if anth.has_anthropic_key():
            st.toggle("Enable paid API analysis (fallback)", value=False,
                      key="paid_api_fallback",
                      help="Off by default. The primary path is importing JSON "
                           "from the sec-filing-analyzer skill (free on your "
                           "Claude Max plan). Turn on to also allow the paid "
                           "Anthropic API 'Analyze' buttons.")

        with st.expander("Data diagnostics"):
            st.caption("Probe each live endpoint for a test ticker and show the "
                       "real reason a call fails.")
            test_t = st.text_input("Test ticker", value="NVDA",
                                   key="diag_ticker").strip().upper() or "NVDA"
            if st.button("Run diagnostics", width="stretch", key="run_diag"):
                st.session_state["_diag_rows"] = service.run_diagnostics(test_t)
            for r in st.session_state.get("_diag_rows", []):
                _render_diag_row(r)

        src = []
        src.append("Finnhub" if service.has_finnhub() else "Finnhub (no key)")
        src.append("FMP" if service.has_api_key() else "FMP (no key)")
        st.markdown(
            '<div class="muted-note">Fundamentals &amp; trailing returns: '
            'Morningstar (static). Quote: ' + " → ".join(src) +
            ' (15s cache). History: FMP → Finnhub candles (15m cache).'
            '</div>', unsafe_allow_html=True)


def _render_diag_row(r: dict) -> None:
    """One diagnostics line: green ok / red reason, verbatim error text."""
    ok = r.get("ok")
    if ok:
        detail = f"keys={r.get('keys')}" if r.get("keys") else "ok"
        body = f'<span style="color:{styles.POSITIVE};">OK</span> · {detail}'
    elif r.get("empty"):
        body = f'<span style="color:{styles.MUTED};">empty response</span>'
    else:
        reason = r.get("error") or f"status {r.get('status')}"
        body = (f'<span style="color:{styles.NEGATIVE};">FAIL</span> · '
                f'{cp._esc(reason)}')
    status = f" (HTTP {r['status']})" if r.get("status") else ""
    st.markdown(
        f'<div class="muted-note" style="margin:2px 0;"><b>{r["endpoint"]}</b>'
        f'{status}: {body}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Live quote (near-real-time) — refreshes in place via st.fragment
# ---------------------------------------------------------------------------
_INTERVAL_SECS = {"Off": None, "15s": 15, "30s": 30, "60s": 60}


def _live_interval():
    """Seconds for the fragment poll, or None when polling is Off."""
    return _INTERVAL_SECS.get(st.session_state.get("live_interval", "15s"), 15)


def _render_quote_inline(ticker: str, fallback_price=None) -> None:
    """Render the live price + today's-move chip + source/timestamp line."""
    import datetime as _dt
    q = service.get_live_quote(ticker)
    price, pct, source = q["price"], q["pct"], q["source"]
    if price is None:
        if fallback_price is not None and pd.notna(fallback_price):
            st.markdown(
                f'<div class="metric-card"><div class="label">Live price</div>'
                f'<div class="value">{cp.fmt_price(fallback_price)}</div>'
                f'<div class="sub">{cp._esc(q["error"] or "live quote unavailable")}'
                f'</div></div>', unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="metric-card"><div class="label">Live price</div>'
                f'<div class="value">{cp.DASH}</div><div class="sub">'
                f'{cp._esc(q["error"] or "no quote")}</div></div>',
                unsafe_allow_html=True)
        return
    cls = "" if pct is None else ("pos" if float(pct) >= 0 else "neg")
    move = cp.DASH if pct is None else cp.fmt_pct_frac(float(pct) / 100.0)
    now = _dt.datetime.now().strftime("%H:%M:%S")
    st.markdown(
        f'<div class="metric-card"><div class="label">Live price · {source}</div>'
        f'<div class="value">{cp.fmt_price(price)} '
        f'<span class="{cls}" style="font-size:0.9rem;">{move}</span></div>'
        f'<div class="sub">live · updated {now}</div></div>',
        unsafe_allow_html=True)


def live_quote_card(ticker: str, fallback_price=None) -> None:
    """Live quote card; polls every N seconds via a fragment unless Off."""
    every = _live_interval()
    if every is None:
        _render_quote_inline(ticker, fallback_price)
        return

    @st.fragment(run_every=every)
    def _frag():
        _render_quote_inline(ticker, fallback_price)

    _frag()


def _render_ticker_strip(tickers: list) -> None:
    """Compact live 'Today' strip for a small set of tickers."""
    import datetime as _dt
    cells = st.columns(len(tickers))
    for col, t in zip(cells, tickers):
        with col:
            q = service.get_live_quote(t)
            if q["price"] is None:
                cp.stat_card(t, cp.DASH, cp._esc(q["error"] or "")[:24])
            else:
                pct = q["pct"]
                cp.stat_card(t, cp.fmt_price(q["price"]),
                             cp.fmt_pct_frac(float(pct) / 100.0) if pct is not None
                             else cp.DASH,
                             sign=pct)
    now = _dt.datetime.now().strftime("%H:%M:%S")
    src = "Finnhub" if service.has_finnhub() else "FMP"
    st.markdown(f'<div class="muted-note">live · {src} · updated {now}</div>',
                unsafe_allow_html=True)


def live_ticker_strip(tickers: list, max_n: int = 8) -> None:
    """Live 'Today' strip for up to ``max_n`` tickers; fragment-polls unless Off.

    Only these visible tickers poll live — never all 226.
    """
    tickers = [t for t in tickers if t][:max_n]
    if not tickers:
        return
    every = _live_interval()
    if every is None:
        _render_ticker_strip(tickers)
        return

    @st.fragment(run_every=every)
    def _frag():
        _render_ticker_strip(tickers)

    _frag()


# ---------------------------------------------------------------------------
# Cached heavy builders
# ---------------------------------------------------------------------------
def build_performance(full: pd.DataFrame) -> pd.DataFrame:
    """Build the full-universe performance frame (cached per live TTL)."""
    bar = st.progress(0.0, text="Loading live momentum…")
    df = perf.build_performance_frame(
        full, progress=lambda f, t: bar.progress(f, text=f"Loading momentum… {t}"))
    bar.empty()
    return df


# ---------------------------------------------------------------------------
# View: Performance
# ---------------------------------------------------------------------------
def view_performance(full: pd.DataFrame, perf_full: pd.DataFrame):
    st.markdown('<div class="view-title">Performance &amp; Momentum</div>',
                unsafe_allow_html=True)
    filter_toolbar(full)
    filtered_tickers = set(apply_filters(full)["Ticker"])
    df = perf_full[perf_full["Ticker"].isin(filtered_tickers)].reset_index(drop=True)
    if df.empty:
        st.warning("No names match the current filters.")
        return

    sel = st.selectbox("Summary / sort window", perf.ALL_WINDOWS,
                       index=perf.ALL_WINDOWS.index("1Y"), key="perf_win")

    n = len(df)
    today = df["Today"].dropna()
    pct_up = (today > 0).mean() * 100 if len(today) else np.nan
    wcol = df[sel].dropna()
    best = df.loc[df[sel].idxmax()] if len(wcol) else None
    worst = df.loc[df[sel].idxmin()] if len(wcol) else None
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cp.stat_card("Names shown", str(n))
    with c2:
        cp.stat_card("Up today", cp.fmt_pct_frac(pct_up/100, signed=False)
                     if pd.notna(pct_up) else cp.DASH)
    with c3:
        if best is not None:
            cp.stat_card(f"Best ({sel})", best["Ticker"],
                         cp.fmt_pct_frac(best[sel]), sign=best[sel])
        else:
            cp.stat_card(f"Best ({sel})", cp.DASH)
    with c4:
        if worst is not None:
            cp.stat_card(f"Worst ({sel})", worst["Ticker"],
                         cp.fmt_pct_frac(worst[sel]), sign=worst[sel])
        else:
            cp.stat_card(f"Worst ({sel})", cp.DASH)

    # Live "Today" strip for the top visible names (only these poll live).
    if service.has_finnhub() or service.has_api_key():
        top_live = (df.sort_values(sel, ascending=False, na_position="last")
                    ["Ticker"].head(8).tolist())
        with st.expander("Live quotes — top names", expanded=False):
            live_ticker_strip(top_live)

    st.write("")
    t_table, t_mom, t_heat = st.tabs(["Returns table", "Momentum rank",
                                      "Heatmap"])
    with t_table:
        _performance_table(df, sel)
    with t_mom:
        _momentum_rank(df)
    with t_heat:
        mode = st.radio("Group by", ["Bucket", "Sector"], horizontal=True,
                        key="heat_mode")
        st.caption(f"Median total return per {mode.lower()} × window.")
        if mode == "Bucket":
            mat = perf.group_heatmap(df, "Primary Bucket", ms.BUCKET_ORDER)
        else:
            mat = perf.group_heatmap(df, "Sector")
        cp.heatmap(mat, perf.ALL_WINDOWS, pct_fraction=True)


def _performance_table(df: pd.DataFrame, sort_win: str):
    cols = ["Ticker", "Name", "Primary Bucket", "Sector", "Last Price"] + perf.ALL_WINDOWS
    view = df[cols].sort_values(by=sort_win, ascending=False, na_position="last")
    fmt = {"Last Price": cp.fmt_price}
    for w in perf.ALL_WINDOWS:
        fmt[w] = lambda v: cp.fmt_pct_frac(v)
    num_cols = ["Last Price"] + perf.ALL_WINDOWS
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.return_bg, subset=perf.ALL_WINDOWS)
              .map(cp.bucket_cell_bg, subset=["Primary Bucket"])
              .set_properties(**{"font-size": "0.88rem"})
              .set_properties(subset=num_cols, **{"text-align": "right"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=560)


def _momentum_rank(df: pd.DataFrame):
    st.caption("Blended momentum = avg z-score of 1M / 3M / 6M (live) + 1Y "
               "(Morningstar).")
    d = df.copy()
    d["Momentum score"] = perf.blended_momentum_score(d)
    cols = ["Ticker", "Name", "Sector", "1M", "3M", "6M", "1Y", "Momentum score"]
    view = d[cols].sort_values("Momentum score", ascending=False, na_position="last")
    fmt = {w: (lambda v: cp.fmt_pct_frac(v)) for w in ["1M", "3M", "6M", "1Y"]}
    fmt["Momentum score"] = lambda v: cp.fmt_ratio(v)
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.return_bg, subset=["1M", "3M", "6M", "1Y"])
              .map(cp.zscore_bg, subset=["Momentum score"])
              .set_properties(**{"font-size": "0.88rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=520)


# ---------------------------------------------------------------------------
# View: Fundamentals & Factors
# ---------------------------------------------------------------------------
def view_fundamentals(full: pd.DataFrame, scores: pd.DataFrame):
    st.markdown('<div class="view-title">Fundamentals &amp; Factor Analysis</div>',
                unsafe_allow_html=True)
    filter_toolbar(full)
    filtered_tickers = set(apply_filters(full)["Ticker"])
    sub_fund, sub_fac = st.tabs(["Fundamentals", "Factor scores"])
    with sub_fund:
        _fundamentals_table(full, filtered_tickers)
    with sub_fac:
        _factor_scores_table(scores, filtered_tickers)


def _fundamentals_table(full: pd.DataFrame, filtered_tickers: set):
    groups = st.multiselect(
        "Metric groups", list(ms.COLUMN_GROUPS.keys()),
        default=["Valuation", "Profitability", "Morningstar Verdicts"],
        key="fund_groups")
    df = full[full["Ticker"].isin(filtered_tickers)].reset_index(drop=True)
    if df.empty:
        st.warning("No names match the current filters.")
        return

    selected_cols: list = []
    for g in groups:
        for c in ms.COLUMN_GROUPS[g]:
            if c in df.columns and c not in selected_cols:
                selected_cols.append(c)

    base = ["Ticker", "Name", "Primary Bucket", "Sector"]
    view = df[base + selected_cols].copy()

    # Verdict columns rendered as HTML chips/badges/stars -> use st.markdown table
    # only when small; otherwise format text. Keep st.dataframe for sortability,
    # converting verdicts to compact text (chips shown on Detail page).
    fmt = {}
    for c in selected_cols:
        if c in ms.MULTIPLE_COLS:
            fmt[c] = cp.fmt_mult
        elif c in ms.PERCENT_COLS:
            fmt[c] = lambda v: cp.fmt_pct_unit(v)
        elif c in ms.RATIO_COLS:
            fmt[c] = cp.fmt_ratio
        elif c in ms.MONEY_COLS:
            fmt[c] = cp.fmt_money_mag
        elif c == "Fair Value":
            fmt[c] = cp.fmt_price
        elif c == "Morningstar Rating for Stocks":
            fmt[c] = cp.fmt_stars

    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.bucket_cell_bg, subset=["Primary Bucket"])
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=560)
    st.caption(f"{len(view)} names · {len(selected_cols)} metrics. "
               "Blanks shown as “—” (never coerced to 0).")


def _factor_scores_table(scores: pd.DataFrame, filtered_tickers: set):
    st.caption("Cross-sectional factor percentiles (0–100, higher = better) "
               "with an equal-weight composite. Tune weights on the Screener tab.")
    df = scores[scores["Ticker"].isin(filtered_tickers)].copy()
    if df.empty:
        st.warning("No names match the current filters.")
        return
    df["Composite"] = fc.weighted_composite(df, fc.default_weights())
    cols = ["Ticker", "Name", "Sector"] + fc.FACTOR_NAMES + ["Composite"]
    view = df[cols].sort_values("Composite", ascending=False, na_position="last")
    score_cols = fc.FACTOR_NAMES + ["Composite"]
    fmt = {c: cp.fmt_score for c in score_cols}
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.score_bg, subset=score_cols)
              .set_properties(**{"font-size": "0.88rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=560)


# ---------------------------------------------------------------------------
# View: Screener
# ---------------------------------------------------------------------------
def view_screener(full: pd.DataFrame, scores: pd.DataFrame):
    st.markdown('<div class="view-title">Screener — weighted factor ranking</div>',
                unsafe_allow_html=True)
    filter_toolbar(full)
    filtered_tickers = set(apply_filters(full)["Ticker"])
    st.caption("Adjust factor weights to re-rank the universe live by the "
               "weighted composite.")

    cols = st.columns(len(fc.FACTOR_NAMES))
    weights = {}
    for col, name in zip(cols, fc.FACTOR_NAMES):
        with col:
            weights[name] = st.slider(name, 0.0, 3.0, 1.0, 0.5, key=f"w_{name}")

    df = scores[scores["Ticker"].isin(filtered_tickers)].copy()
    if df.empty:
        st.warning("No names match the current filters.")
        return
    df["Composite"] = fc.weighted_composite(df, weights)
    df = df.sort_values("Composite", ascending=False, na_position="last")

    top = df.head(3)
    tcols = st.columns(3)
    for col, (_, r) in zip(tcols, top.iterrows()):
        with col:
            cp.stat_card(f"#{list(top['Ticker']).index(r['Ticker'])+1} {r['Ticker']}",
                         cp.fmt_score(r["Composite"]), r["Name"] or "")

    st.write("")
    show = ["Ticker", "Name", "Sector"] + fc.FACTOR_NAMES + ["Composite"]
    score_cols = fc.FACTOR_NAMES + ["Composite"]
    styler = (df[show].style.format({c: cp.fmt_score for c in score_cols}, na_rep=cp.DASH)
              .map(cp.score_bg, subset=score_cols)
              .set_properties(**{"font-size": "0.88rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=520)


# ---------------------------------------------------------------------------
# View: Buckets (slice-and-dice)
# ---------------------------------------------------------------------------
def view_buckets(full: pd.DataFrame, perf_full: pd.DataFrame,
                 scores: pd.DataFrame):
    st.markdown('<div class="view-title">Buckets — AI sub-theme slice &amp; dice'
                '</div>', unsafe_allow_html=True)
    filter_toolbar(full)
    filtered_tickers = set(apply_filters(full)["Ticker"])

    # Respect the active filters for the cohort being analysed.
    pf = perf_full[perf_full["Ticker"].isin(filtered_tickers)].reset_index(drop=True)
    fd = full[full["Ticker"].isin(filtered_tickers)].reset_index(drop=True)
    sc = scores[scores["Ticker"].isin(filtered_tickers)].reset_index(drop=True)
    if pf.empty:
        st.warning("No names match the current filters.")
        return

    win = st.selectbox("Window", perf.ALL_WINDOWS,
                       index=perf.ALL_WINDOWS.index("1Y"), key="bucket_win")

    summary = _bucket_summary(pf, fd, sc, win)

    t_sum, t_board, t_drill = st.tabs(
        ["Summary", "Leaderboard", "Drill-down"])

    with t_sum:
        st.caption("One row per bucket — counts, returns, valuation, quality and "
                   "factor medians. Sortable.")
        _bucket_summary_table(summary, win)

    with t_board:
        st.caption(f"Median {win} return per bucket — which AI sub-theme is winning.")
        cp.leaderboard_bar(summary["Primary Bucket"].tolist(),
                           summary[f"Median {win}"].tolist(),
                           title=f"Median {win} return by bucket")

    with t_drill:
        _bucket_drilldown(pf, sc, summary)


def _bucket_summary(pf, fd, sc, win):
    """Build the per-bucket summary frame (one row per bucket present)."""
    fd_idx = fd.set_index("Ticker")
    sc_idx = sc.set_index("Ticker")
    rows = []
    for bucket in ms.BUCKET_ORDER:
        members = pf[pf["Primary Bucket"] == bucket]
        if members.empty:
            continue
        tickers = members["Ticker"].tolist()
        fsub = fd_idx.reindex(tickers)
        ssub = sc_idx.reindex(tickers)
        rows.append({
            "Primary Bucket": bucket,
            "Names": len(members),
            f"Median {win}": members[win].median(skipna=True),
            f"Mean {win}": members[win].mean(skipna=True),
            "Median P/E": fsub["Price/Earnings"].median(skipna=True),
            "Median ROE": fsub["Return on Equity"].median(skipna=True),
            "Median Rev Gr (1Y)": fsub["Revenue Growth (1Y)"].median(skipna=True),
            "Avg ★": fsub["Morningstar Rating for Stocks"].mean(skipna=True),
            "Value": ssub["Value"].median(skipna=True),
            "Quality": ssub["Quality"].median(skipna=True),
            "Growth": ssub["Growth"].median(skipna=True),
            "Momentum": ssub["Momentum"].median(skipna=True),
        })
    return pd.DataFrame(rows)


def _bucket_summary_table(summary: pd.DataFrame, win: str):
    if summary.empty:
        st.info("No buckets to summarise.")
        return
    ret_cols = [f"Median {win}", f"Mean {win}"]
    factor_cols = ["Value", "Quality", "Growth", "Momentum"]
    fmt = {c: (lambda v: cp.fmt_pct_frac(v)) for c in ret_cols}
    fmt["Median P/E"] = cp.fmt_mult
    fmt["Median ROE"] = lambda v: cp.fmt_pct_unit(v)
    fmt["Median Rev Gr (1Y)"] = lambda v: cp.fmt_pct_unit(v)
    fmt["Avg ★"] = lambda v: cp.fmt_ratio(v)
    for c in factor_cols:
        fmt[c] = cp.fmt_score
    styler = (summary.style.format(fmt, na_rep=cp.DASH)
              .map(cp.bucket_cell_bg, subset=["Primary Bucket"])
              .map(cp.return_bg, subset=ret_cols)
              .map(cp.score_bg, subset=factor_cols)
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=440)


def _bucket_drilldown(pf, sc, summary):
    buckets = summary["Primary Bucket"].tolist()
    bucket = st.selectbox("Bucket", buckets, key="drill_bucket")

    # Bucket factor profile vs universe median.
    factor_cols = fc.FACTOR_NAMES
    bsub = sc[sc["Primary Bucket"] == bucket]
    bvals = [bsub[n].median(skipna=True) for n in factor_cols]
    uvals = [sc[n].median(skipna=True) for n in factor_cols]
    st.markdown('<div class="section-title">Bucket factor profile vs universe</div>',
                unsafe_allow_html=True)
    cp.grouped_factor_bars(factor_cols, bvals, uvals, group_label=bucket)

    # Constituents with returns + factor scores.
    st.markdown('<div class="section-title">Constituents</div>',
                unsafe_allow_html=True)
    members = pf[pf["Primary Bucket"] == bucket].copy()
    merged = members.merge(
        sc[["Ticker"] + factor_cols], on="Ticker", how="left")
    ret_cols = ["1M", "3M", "6M", "1Y"]
    cols = ["Ticker", "Name", "Last Price"] + ret_cols + factor_cols
    view = merged[cols].sort_values("1Y", ascending=False, na_position="last")
    fmt = {"Last Price": cp.fmt_price}
    for c in ret_cols:
        fmt[c] = lambda v: cp.fmt_pct_frac(v)
    for c in factor_cols:
        fmt[c] = cp.fmt_score
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.return_bg, subset=ret_cols)
              .map(cp.score_bg, subset=factor_cols)
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=440)


# ---------------------------------------------------------------------------
# View: News & Filings (Feature B)
# ---------------------------------------------------------------------------
def view_news(full: pd.DataFrame, perf_full: pd.DataFrame,
              scores: pd.DataFrame):
    st.markdown('<div class="view-title">News &amp; Filings</div>',
                unsafe_allow_html=True)
    options = full["Ticker"].tolist()
    labels = dict(zip(full["Ticker"], full["Name"]))
    ticker = st.selectbox("Ticker", options,
                          format_func=lambda t: f"{t} · {labels.get(t, '')}",
                          key="news_ticker")
    row = full[full["Ticker"] == ticker].iloc[0]

    # --- Header ---
    rating = row.get("Morningstar Rating for Stocks")
    head = (
        f'<div class="detail-head"><div class="name">{row.get("Name") or ticker} '
        f'<span class="tk">{ticker}</span></div>'
        f'<div class="meta">{cp.bucket_chip(row.get("Primary Bucket"))} &nbsp; '
        f'<span class="stars">{cp.fmt_stars(rating)}</span> &nbsp; '
        f'upside {cp.fmt_pct_frac(row.get("upside_pct"))}</div></div>')
    st.markdown(head, unsafe_allow_html=True)

    # --- Fetch all sources (graceful empties) ---
    news = service.get_stock_news(ticker, limit=30) if service.has_api_key() else []
    filings = service.get_sec_filings(ticker, limit=20)
    insider = service.get_insider_trades(ticker) if service.has_api_key() else []
    holders = service.get_institutional_holders(ticker) if service.has_api_key() else []

    glance = synth.build_at_a_glance(row, news, filings, insider, holders)
    _at_a_glance_cards(glance)
    _ai_summary_card(ticker, row.get("Name") or ticker, news, filings, glance, holders)

    st.write("")
    t_news, t_sec, t_inst, t_ins = st.tabs(
        ["News", "SEC Filings", "Institutional", "Insider"])
    with t_news:
        if not service.has_api_key():
            st.info("Set FMP_API_KEY to load live news.")
        else:
            leading = st.toggle("Leading sources only", value=False, key="lead_only")
            cp.news_feed(news, limit=20, leading_only=leading)
    with t_sec:
        st.caption("Source: SEC EDGAR. 8-K = material events · 10-Q/10-K = "
                   "financials · S-1/424B = offerings · SC 13D/G = >5% stakes.")
        _sec_filings_panel(ticker, filings)
    with t_inst:
        cp.holders_table(holders)
    with t_ins:
        ins = glance["insider"]
        cp.stat_card(
            f"Net insider ({ins['days']}d)",
            cp.fmt_money_mag(ins["net"]),
            f"{ins['n_buy']} buys / {ins['n_sell']} sells · {ins['direction']}",
            sign=ins["net"] if (ins["n_buy"] or ins["n_sell"]) else None)
        st.write("")
        cp.insider_table(insider)


def _at_a_glance_cards(g: dict):
    st.markdown('<div class="section-title">At a glance</div>',
                unsafe_allow_html=True)
    s = g["sentiment"]
    f = g["filings"]
    ins = g["insider"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        net_sent = s["positive"] - s["negative"]
        cp.stat_card("News sentiment",
                     f"{s['positive']}+ / {s['negative']}−",
                     f"{s['total']} headlines", sign=net_sent)
    with c2:
        cp.stat_card("Filings 30d / 90d", f"{f['last30']} / {f['last90']}",
                     "SEC EDGAR")
    with c3:
        has_ins = ins["n_buy"] or ins["n_sell"]
        cp.stat_card("Net insider 90d", cp.fmt_money_mag(ins["net"]),
                     ins["direction"], sign=ins["net"] if has_ins else None)
    with c4:
        cp.stat_card("Upside to FV", cp.fmt_pct_frac(g["upside_pct"]),
                     f"{cp.fmt_stars(g['rating'])}", sign=g["upside_pct"])

    changes = g["institutional"]
    if changes and any(c["change"] is not None for c in changes):
        bits = []
        for c in changes:
            if c["change"] is None:
                continue
            arrow = "▲" if c["change"] >= 0 else "▼"
            bits.append(f"{c['holder']} {arrow}{abs(c['change']):,.0f}")
        if bits:
            st.caption("Top institutional changes: " + " · ".join(bits))


def _ai_summary_card(ticker, name, news, filings, glance, holders):
    """Opt-in Anthropic narrative; only shown when a key is present."""
    if not anth.has_anthropic_key():
        return
    enabled = st.toggle("AI summary (generated)", value=False, key="ai_sum")
    if not enabled:
        return

    @st.cache_data(show_spinner="Generating AI summary…")
    def _gen(tk: str) -> str:
        return anth.generate_summary(
            tk, name, news, filings, glance["insider"], holders) or ""

    text = _gen(ticker)
    if text:
        st.markdown(
            f'<div class="news-item" style="border-color:{styles.PRIMARY};">'
            f'<b>AI Summary (generated)</b><div class="news-meta" '
            f'style="color:{styles.TEXT};margin-top:6px;font-size:0.92rem;">'
            f'{text}</div></div>', unsafe_allow_html=True)
    else:
        st.info("AI summary unavailable right now (showing deterministic summary "
                "above).")


# ---------------------------------------------------------------------------
# SEC Filings panel — import-first (zero cost), paid API as optional fallback
# ---------------------------------------------------------------------------
def _run_filing_analysis(ticker: str, f: dict, model: str) -> dict:
    """Fetch -> trim -> analyze one filing (uses disk cache; spinner on miss)."""
    accn = f.get("accessionNumber", "")
    cached = service.filing_analysis_cached(accn, model) is not None
    if cached:
        return service.analyze_filing(
            accn, model, f.get("cik"), f.get("primaryDocument", ""),
            f.get("form", ""), f.get("filingDate", ""), ticker)
    with st.spinner("Fetching filing & running AI analysis…"):
        return service.analyze_filing(
            accn, model, f.get("cik"), f.get("primaryDocument", ""),
            f.get("form", ""), f.get("filingDate", ""), ticker)


def _filing_reference(ticker: str, f: dict) -> str:
    """The copyable reference string the user pastes into Claude + the skill."""
    return (f"{ticker} {f.get('form', '?')} filed {f.get('filingDate', '?')} — "
            f"{f.get('url', '')} (accession {f.get('accessionNumber', '')})")


def _handle_import_uploader(uploaded) -> None:
    """Validate + persist uploaded skill JSON file(s); report results."""
    import json
    ok_n, bad = 0, []
    for uf in uploaded:
        try:
            obj = json.loads(uf.getvalue().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            bad.append(f"{uf.name}: not valid JSON ({exc})")
            continue
        # A file may contain one analysis object or a list of them.
        objs = obj if isinstance(obj, list) else [obj]
        for o in objs:
            good, msg = service.save_imported_analysis(o)
            if good:
                ok_n += 1
            else:
                bad.append(f"{uf.name}: {msg}")
    if ok_n:
        st.success(f"Imported {ok_n} analysis(es).")
    for b in bad:
        st.warning(f"Skipped — {b}")
    if ok_n:
        st.rerun()


def _sec_filings_panel(ticker: str, filings: list):
    st.caption("Analyze filings **free under your Claude Max plan**: copy a "
               "filing reference, run the `sec-filing-analyzer` skill in Claude, "
               "then import the JSON here.")

    # Uploader (accepts one or many skill-produced JSON files).
    uploaded = st.file_uploader(
        "Import filing analysis (JSON)", type=["json"],
        accept_multiple_files=True, key=f"import_{ticker}",
        help="Drop the JSON produced by the sec-filing-analyzer skill. Files are "
             "saved to .cache/filings/imported/ and matched by accession number.")
    if uploaded:
        _handle_import_uploader(uploaded)

    if not filings:
        cp.filings_feed(filings, limit=20)  # graceful empty message
        return

    # Imported analyses (primary path) indexed by normalized accession.
    imported = service.get_imported_analyses()

    # Paid API path is demoted: only when a key is present AND the sidebar
    # fallback toggle is on.
    paid_enabled = (anth.has_anthropic_key()
                    and st.session_state.get("paid_api_fallback", False))
    model = None
    if paid_enabled:
        model = anth.MODEL_CHOICES[st.selectbox(
            "Analysis model (paid fallback)", list(anth.MODEL_CHOICES.keys()),
            index=0, key="filing_model",
            help="Haiku is cheapest/fastest. Sonnet gives a deeper read.")]

    show: dict = st.session_state.setdefault("_filing_show", {})

    # Optional paid multi-filing synthesis (only under the fallback toggle).
    if paid_enabled:
        if st.button("Analyze recent filing activity (paid API)",
                     key="analyze_activity"):
            _run_activity_synthesis(ticker, filings[:5], model)
        syn = st.session_state.get("_filing_activity")
        if syn:
            with st.container(border=True):
                st.markdown('<div class="section-title">Recent filing activity'
                            '</div>', unsafe_allow_html=True)
                cp.filing_analysis_result(syn)

    st.write("")
    for f in filings[:20]:
        accn = f.get("accessionNumber", "")
        accn_key = service.normalize_accession(accn)
        imported_obj = imported.get(accn_key)
        has_doc = bool(f.get("primaryDocument"))

        with st.container(border=True):
            cp.filing_row_header(
                f.get("form", "?"), f.get("filingDate", ""), f.get("url", "#"),
                cached=(imported_obj is None
                        and paid_enabled
                        and service.filing_analysis_cached(accn, model) is not None))

            # Accession + copyable EDGAR reference (Change 3).
            st.code(_filing_reference(ticker, f), language=None)

            if imported_obj is not None:
                # Imported analysis wins — no API path offered.
                cp.imported_analysis_result(imported_obj)
                st.caption("Imported from Claude. Re-import an updated JSON to "
                           "replace this.")
            elif paid_enabled:
                on_disk = service.filing_analysis_cached(accn, model) is not None
                label = "View cached analysis" if on_disk else "Analyze (paid API)"
                if st.button(label, key=f"an_{accn_key}", disabled=not has_doc):
                    show[accn] = _run_filing_analysis(ticker, f, model)
                if not has_doc:
                    st.caption("No primary document to analyze for this filing.")
                if accn in show:
                    cp.filing_analysis_result(show[accn])
            else:
                st.caption("No imported analysis yet — copy the reference above "
                           "and run the skill in Claude.")


def _run_activity_synthesis(ticker: str, filings: list, model: str):
    """Build the multi-filing synthesis, reusing per-filing cached analyses."""
    items = []
    for f in filings:
        accn = f.get("accessionNumber", "")
        prior = service.filing_analysis_cached(accn, model)
        items.append({
            "form": f.get("form", "?"),
            "filingDate": f.get("filingDate", ""),
            "accessionNumber": accn,
            "analysis": (prior or {}).get("text", "") if prior else "",
        })
    with st.spinner("Synthesizing recent filing activity…"):
        st.session_state["_filing_activity"] = service.analyze_filing_activity(
            ticker, model, items)


# ---------------------------------------------------------------------------
# View: Stock Detail
# ---------------------------------------------------------------------------
_DETAIL_WINDOWS = {"1W": pd.DateOffset(weeks=1), "1M": pd.DateOffset(months=1),
                   "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
                   "1Y": pd.DateOffset(years=1), "5Y": pd.DateOffset(years=5)}


def view_detail(full: pd.DataFrame, perf_full: pd.DataFrame,
                scores: pd.DataFrame):
    st.markdown('<div class="view-title">Stock Detail</div>',
                unsafe_allow_html=True)
    pool = full
    options = pool["Ticker"].tolist()
    labels = dict(zip(pool["Ticker"], pool["Name"]))
    ticker = st.selectbox("Select a ticker", options,
                          format_func=lambda t: f"{t} · {labels.get(t, '')}",
                          key="detail_ticker")
    row = full[full["Ticker"] == ticker].iloc[0]

    # --- Header ---
    rating = row.get("Morningstar Rating for Stocks")
    moat = row.get("Economic Moat")
    upside = row.get("upside_pct")
    head = (
        f'<div class="detail-head"><div class="name">{row.get("Name") or ticker} '
        f'<span class="tk">{ticker}</span></div>'
        f'<div class="meta">{cp.bucket_chip(row.get("Primary Bucket"))} &nbsp; '
        f'{row.get("Sector") or ""} · {row.get("Stock Style Box") or ""} &nbsp; '
        f'<span class="stars">{cp.fmt_stars(rating)}</span> &nbsp; '
        f'{cp.moat_chip(moat)}</div></div>'
    )
    st.markdown(head, unsafe_allow_html=True)

    fv = row.get("Fair Value")
    lp = row.get("Last Price")
    cinfo = st.columns(3)
    with cinfo[0]:
        live_quote_card(ticker, fallback_price=lp)
    with cinfo[1]:
        cp.stat_card("Fair Value", cp.fmt_price(fv))
    with cinfo[2]:
        cp.stat_card("Implied upside", cp.fmt_pct_frac(upside), sign=upside)

    # --- Price chart with window selector ---
    st.markdown('<div class="section-title">Price</div>', unsafe_allow_html=True)
    win = st.radio("Window", list(_DETAIL_WINDOWS.keys()) + ["Max"],
                   index=4, horizontal=True, key="detail_win")
    if service.has_api_key() or service.has_finnhub():
        hist = service.get_history_result(ticker)
        series = hist["series"]
        if series.empty:
            st.warning(f"No price history: {hist['error'] or 'unavailable'}")
        else:
            if hist["source"] and hist["error"]:
                # Fallback succeeded — show source + why FMP was bypassed.
                st.caption(f"Source: {hist['source']} — {hist['error']}")
            else:
                st.caption(f"Source: {hist['source']}")
            chart_s = series if (win == "Max") else \
                series[series.index >= series.index[-1] - _DETAIL_WINDOWS[win]]
            cp.price_chart(chart_s)
    else:
        st.info("Set FMP_API_KEY or FINNHUB_API_KEY to load the price chart.")

    # --- Return cards (9 windows) ---
    st.markdown('<div class="section-title">Returns</div>', unsafe_allow_html=True)
    prow = perf_full[perf_full["Ticker"] == ticker]
    prow = prow.iloc[0] if not prow.empty else None
    rcols = st.columns(len(perf.ALL_WINDOWS))
    for col, w in zip(rcols, perf.ALL_WINDOWS):
        with col:
            v = prow[w] if prow is not None else None
            cp.stat_card(w, cp.fmt_pct_frac(v), sign=v)

    # --- Factor radar ---
    st.markdown('<div class="section-title">Factor profile</div>',
                unsafe_allow_html=True)
    srow = scores[scores["Ticker"] == ticker]
    if not srow.empty:
        s = srow.iloc[0]
        this = {n: s[n] for n in fc.FACTOR_NAMES}
        med = {n: scores[n].median() for n in fc.FACTOR_NAMES}
        rc1, rc2 = st.columns([1, 1])
        with rc1:
            cp.factor_radar(this, med)
        with rc2:
            for n in fc.FACTOR_NAMES:
                cp.stat_card(n, cp.fmt_score(this[n]),
                             f"universe median {cp.fmt_score(med[n])}")

    # --- Grouped fundamentals ---
    st.markdown('<div class="section-title">Fundamentals</div>',
                unsafe_allow_html=True)
    _detail_fundamentals(row)

    # --- Compact News & Filings ---
    st.markdown('<div class="section-title">News & filings</div>',
                unsafe_allow_html=True)
    nf_news, nf_sec = st.columns(2)
    with nf_news:
        st.caption("Latest news")
        if service.has_api_key():
            cp.news_feed(service.get_stock_news(ticker, limit=12), limit=6)
        else:
            st.info("Set FMP_API_KEY to load live news.")
    with nf_sec:
        st.caption("Recent SEC filings")
        cp.filings_feed(service.get_sec_filings(ticker, limit=10), limit=6)


def _detail_fundamentals(row: pd.Series):
    for group, cols in ms.COLUMN_GROUPS.items():
        present = [c for c in cols if c in row.index]
        if not present:
            continue
        with st.expander(group, expanded=(group in ("Valuation", "Profitability"))):
            cells = st.columns(4)
            for i, c in enumerate(present):
                val = row.get(c)
                if c in ms.MULTIPLE_COLS:
                    txt = cp.fmt_mult(val)
                elif c in ms.PERCENT_COLS:
                    txt = cp.fmt_pct_unit(val)
                elif c in ms.RATIO_COLS:
                    txt = cp.fmt_ratio(val)
                elif c in ms.MONEY_COLS:
                    txt = cp.fmt_money_mag(val)
                elif c == "Fair Value":
                    txt = cp.fmt_price(val)
                elif c == "Morningstar Rating for Stocks":
                    txt = cp.fmt_stars(val)
                elif c in ("Growth Grade", "Profitability Grade"):
                    txt = str(val) if val is not None and pd.notna(val) else cp.DASH
                elif c == "Economic Moat":
                    txt = str(val) if val is not None and pd.notna(val) else cp.DASH
                else:
                    txt = (cp.fmt_ratio(val) if isinstance(val, (int, float))
                           and pd.notna(val) else cp.DASH)
                with cells[i % 4]:
                    cp.stat_card(c, txt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
NAV_ITEMS = ["Performance", "Fundamentals & Factors", "Screener", "Buckets",
             "News & Filings", "Stock Detail"]


def main():
    # Slim header bar + Settings popover (top-right), no sidebar.
    head_col, set_col = st.columns([6, 1])
    with head_col:
        styles.app_header(
            "ParkNova — AI Equities Analyzer",
            "Morningstar fundamentals · live FMP momentum & news across the "
            "AI universe")
    with set_col:
        settings_popover()

    if not service.has_api_key():
        st.error("**FMP_API_KEY not found.** Copy `.env.example` to `.env` and add "
                 "your Financial Modeling Prep key (or set it in the platform "
                 "environment), then restart. Morningstar views work without it; "
                 "live momentum & news need the key.")

    try:
        full = ms.load_morningstar()
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()

    # Horizontal text-only nav (no emoji), enterprise styling, active underline.
    selected = option_menu(
        menu_title=None, options=NAV_ITEMS, orientation="horizontal",
        default_index=0, key="nav", styles=styles.nav_styles())

    # Build live performance once (handles missing key gracefully -> NaNs).
    if service.has_api_key():
        perf_full = build_performance(full)
    else:
        perf_full = perf.build_performance_frame(full, live=False)

    # Stash for the cached factor builder + compute scores.
    st.session_state["_ms_full"] = full
    st.session_state["_perf_full"] = perf_full
    merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                        on="Ticker", how="left")
    scores = fc.compute_factor_scores(merged)
    scores.insert(0, "Ticker", merged["Ticker"].values)
    scores.insert(1, "Name", merged["Name"].values)
    scores.insert(2, "Primary Bucket", merged["Primary Bucket"].values)
    scores.insert(3, "Sector", merged["Sector"].values)

    # Render only the active view (option_menu, unlike st.tabs, renders one).
    if selected == "Performance":
        view_performance(full, perf_full)
    elif selected == "Fundamentals & Factors":
        view_fundamentals(full, scores)
    elif selected == "Screener":
        view_screener(full, scores)
    elif selected == "Buckets":
        view_buckets(full, perf_full, scores)
    elif selected == "News & Filings":
        view_news(full, perf_full, scores)
    elif selected == "Stock Detail":
        view_detail(full, perf_full, scores)


if __name__ == "__main__":
    main()
