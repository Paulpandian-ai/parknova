"""ParkNova — AI Equities Analyzer.

A personal investment-research dashboard over a curated ~226-stock AI universe.
Morningstar export = primary fundamentals/returns; FMP = live momentum + news.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from core import factors as fc
from core import performance as perf
from data import morningstar as ms
from data import service
from ui import components as cp
from ui import styles

load_dotenv()

st.set_page_config(page_title="ParkNova — AI Equities Analyzer",
                   page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")
styles.inject_css()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    with st.sidebar:
        st.markdown("### 🔎 Filters")
        st.text_input("Search ticker / name", key="search", placeholder="e.g. NVDA")
        st.multiselect("Sector", ms.sector_options(df), key="sectors")
        st.multiselect("Style box", ms.style_options(df), key="styles")
        st.divider()
        if st.button("🔄 Refresh live data", width="stretch"):
            service.clear_live_caches()
            st.success("Live caches cleared.")
            st.rerun()
        st.caption("Fundamentals & trailing returns: Morningstar (static). "
                   "Today/1W/1M/3M/6M + news: FMP live (quotes cached 15m, "
                   "news 30m).")

    out = df
    sectors = st.session_state.get("sectors", [])
    if sectors:
        out = out[out["Sector"].isin(sectors)]
    stylebox = st.session_state.get("styles", [])
    if stylebox:
        out = out[out["Stock Style Box"].isin(stylebox)]
    q = (st.session_state.get("search", "") or "").strip().lower()
    if q:
        mask = (out["Ticker"].str.lower().str.contains(q, na=False)
                | out["Name"].str.lower().str.contains(q, na=False))
        out = out[mask]
    return out.reset_index(drop=True)


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
def view_performance(perf_full: pd.DataFrame, filtered_tickers: set):
    st.markdown('<div class="section-title">Performance & Momentum</div>',
                unsafe_allow_html=True)
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

    st.write("")
    t_table, t_mom, t_heat = st.tabs(["📋 Returns table", "🚀 Momentum rank",
                                      "🔥 Sector heatmap"])
    with t_table:
        _performance_table(df, sel)
    with t_mom:
        _momentum_rank(df)
    with t_heat:
        st.caption("Median total return per sector × window.")
        cp.heatmap(perf.sector_heatmap(df), perf.ALL_WINDOWS, pct_fraction=True)


def _performance_table(df: pd.DataFrame, sort_win: str):
    cols = ["Ticker", "Name", "Sector", "Last Price"] + perf.ALL_WINDOWS
    view = df[cols].sort_values(by=sort_win, ascending=False, na_position="last")
    fmt = {"Last Price": cp.fmt_price}
    for w in perf.ALL_WINDOWS:
        fmt[w] = lambda v: cp.fmt_pct_frac(v)
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.return_bg, subset=perf.ALL_WINDOWS)
              .set_properties(**{"font-size": "0.88rem"}))
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
def view_fundamentals(full: pd.DataFrame, scores: pd.DataFrame,
                      filtered_tickers: set):
    st.markdown('<div class="section-title">Fundamentals & Factor Analysis</div>',
                unsafe_allow_html=True)
    sub_fund, sub_fac = st.tabs(["📑 Fundamentals", "🧮 Factor scores"])
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

    base = ["Ticker", "Name", "Sector"]
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

    styler = view.style.format(fmt, na_rep=cp.DASH).set_properties(
        **{"font-size": "0.86rem"})
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
def view_screener(scores: pd.DataFrame, filtered_tickers: set):
    st.markdown('<div class="section-title">Screener — weighted factor ranking</div>',
                unsafe_allow_html=True)
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
# View: News
# ---------------------------------------------------------------------------
def view_news(full: pd.DataFrame, filtered_tickers: set):
    st.markdown('<div class="section-title">News</div>', unsafe_allow_html=True)
    df = full[full["Ticker"].isin(filtered_tickers)]
    options = df["Ticker"].tolist()
    if not options:
        st.warning("No names match the current filters.")
        return
    labels = dict(zip(df["Ticker"], df["Name"]))
    ticker = st.selectbox("Ticker", options,
                          format_func=lambda t: f"{t} · {labels.get(t, '')}",
                          key="news_ticker")
    if not service.has_api_key():
        st.info("Set FMP_API_KEY to load live news.")
        return
    items = service.get_stock_news(ticker, limit=20)
    cp.news_feed(items, limit=15)


# ---------------------------------------------------------------------------
# View: Stock Detail
# ---------------------------------------------------------------------------
_DETAIL_WINDOWS = {"1W": pd.DateOffset(weeks=1), "1M": pd.DateOffset(months=1),
                   "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
                   "1Y": pd.DateOffset(years=1), "5Y": pd.DateOffset(years=5)}


def view_detail(full: pd.DataFrame, perf_full: pd.DataFrame,
                scores: pd.DataFrame, filtered_tickers: set):
    st.markdown('<div class="section-title">Stock Detail</div>',
                unsafe_allow_html=True)
    pool = full[full["Ticker"].isin(filtered_tickers)]
    if pool.empty:
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
        f'<div class="meta">{row.get("Sector") or ""} · '
        f'{row.get("Stock Style Box") or ""} &nbsp; '
        f'<span class="stars">{cp.fmt_stars(rating)}</span> &nbsp; '
        f'{cp.moat_chip(moat)}</div></div>'
    )
    st.markdown(head, unsafe_allow_html=True)

    fv = row.get("Fair Value")
    lp = row.get("Last Price")
    cinfo = st.columns(3)
    with cinfo[0]:
        cp.stat_card("Last Price", cp.fmt_price(lp))
    with cinfo[1]:
        cp.stat_card("Fair Value", cp.fmt_price(fv))
    with cinfo[2]:
        cp.stat_card("Implied upside", cp.fmt_pct_frac(upside), sign=upside)

    # --- Price chart with window selector ---
    st.markdown('<div class="section-title">Price</div>', unsafe_allow_html=True)
    win = st.radio("Window", list(_DETAIL_WINDOWS.keys()) + ["Max"],
                   index=4, horizontal=True, key="detail_win")
    if service.has_api_key():
        series = service.get_history(ticker)
        chart_s = series if (win == "Max" or series.empty) else \
            series[series.index >= series.index[-1] - _DETAIL_WINDOWS[win]]
        cp.price_chart(chart_s)
    else:
        st.info("Set FMP_API_KEY to load the live price chart.")

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

    # --- News ---
    st.markdown('<div class="section-title">Latest news</div>',
                unsafe_allow_html=True)
    if service.has_api_key():
        cp.news_feed(service.get_stock_news(ticker, limit=12), limit=10)
    else:
        st.info("Set FMP_API_KEY to load live news.")


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
def main():
    styles.app_header(
        "ParkNova — AI Equities Analyzer",
        "Morningstar fundamentals + live FMP momentum & news across the AI universe")

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

    filtered = sidebar_filters(full)
    filtered_tickers = set(filtered["Ticker"])

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
    scores.insert(2, "Sector", merged["Sector"].values)

    tabs = st.tabs(["📈 Performance", "📊 Fundamentals & Factors", "🧪 Screener",
                    "📰 News", "🔍 Stock Detail"])
    with tabs[0]:
        view_performance(perf_full, filtered_tickers)
    with tabs[1]:
        view_fundamentals(full, scores, filtered_tickers)
    with tabs[2]:
        view_screener(scores, filtered_tickers)
    with tabs[3]:
        view_news(full, filtered_tickers)
    with tabs[4]:
        view_detail(full, perf_full, scores, filtered_tickers)


if __name__ == "__main__":
    main()
