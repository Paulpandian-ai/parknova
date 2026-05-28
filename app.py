"""AI Equities Tracker — Streamlit entry point.

Phase 1 of a personal investment-research platform: a clean white/blue dashboard
to track performance and fundamentals for a curated AI-stock universe, all live
from Financial Modeling Prep.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from core import fundamentals as fnd
from core import performance as perf
from data import service
from data import universe as uni
from ui import components as cp
from ui import styles

load_dotenv()

st.set_page_config(
    page_title="AI Equities Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
styles.inject_css()


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------
def filter_universe(df: pd.DataFrame) -> pd.DataFrame:
    groups = []
    if st.session_state.get("inc_single", True):
        groups.append(uni.GROUP_SINGLE)
    if st.session_state.get("inc_etf", True):
        groups.append(uni.GROUP_ETF)
    if st.session_state.get("inc_otc", False):
        groups.append(uni.GROUP_OTC)

    out = df[df["group"].isin(groups)].copy()

    buckets = st.session_state.get("buckets", [])
    if buckets:
        out = out[out["bucket"].isin(buckets)]

    query = (st.session_state.get("search", "") or "").strip().lower()
    if query:
        mask = out["ticker"].str.lower().str.contains(query, na=False) | out[
            "company"
        ].str.lower().str.contains(query, na=False)
        out = out[mask]

    return out.reset_index(drop=True)


def sidebar_controls(full: pd.DataFrame):
    with st.sidebar:
        st.markdown("### 🔎 Filters")
        st.text_input("Search ticker / name", key="search", placeholder="e.g. NVDA")
        st.multiselect("Primary bucket", uni.bucket_options(full), key="buckets")

        st.markdown("#### Groups")
        st.checkbox("Single names", value=True, key="inc_single")
        st.checkbox("ETFs", value=True, key="inc_etf")
        st.checkbox("Include OTC / lower-liquidity", value=False, key="inc_otc")

        st.divider()
        if st.button("🔄 Refresh data", use_container_width=True):
            service.clear_all_caches()
            st.success("Caches cleared — reloading live data.")
            st.rerun()
        st.caption(
            "Quotes cached 15 min · fundamentals 1 day. Live data from Financial "
            "Modeling Prep."
        )


# ---------------------------------------------------------------------------
# Cached frame builders (wrap the core builders so heavy work is reused)
# ---------------------------------------------------------------------------
def build_performance(filtered: pd.DataFrame) -> pd.DataFrame:
    bar = st.progress(0.0, text="Loading performance…")

    def cb(frac, label):
        bar.progress(frac, text=f"Loading performance… {label}")

    df = perf.build_performance_frame(filtered, progress=cb)
    bar.empty()
    return df


def build_fundamentals(filtered: pd.DataFrame) -> pd.DataFrame:
    bar = st.progress(0.0, text="Loading fundamentals…")

    def cb(frac, label):
        bar.progress(frac, text=f"Loading fundamentals… {label}")

    df = fnd.build_fundamentals_frame(filtered, progress=cb)
    bar.empty()
    return df


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def view_performance(filtered: pd.DataFrame):
    st.markdown('<div class="section-title">Performance</div>', unsafe_allow_html=True)
    sel_window = st.selectbox(
        "Summary / sort window",
        perf.RETURN_COLS,
        index=perf.RETURN_COLS.index("1M"),
        key="perf_window",
    )

    if filtered.empty:
        st.warning("No names match the current filters.")
        return

    df = build_performance(filtered)

    # --- Summary strip ---
    n = len(df)
    up_today = df["Today"].dropna()
    pct_up = (up_today > 0).mean() * 100 if len(up_today) else float("nan")
    wcol = df[sel_window].dropna()
    best = df.loc[df[sel_window].idxmax()] if len(wcol) else None
    worst = df.loc[df[sel_window].idxmin()] if len(wcol) else None

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cp.stat_card("Names shown", str(n))
    with c2:
        cp.stat_card("Up today", cp.fmt_pct(pct_up / 100, signed=False) if pd.notna(pct_up) else cp.DASH)
    with c3:
        if best is not None:
            cp.stat_card(f"Best ({sel_window})", best["ticker"],
                         cp.fmt_pct(best[sel_window]), sign=best[sel_window])
        else:
            cp.stat_card(f"Best ({sel_window})", cp.DASH)
    with c4:
        if worst is not None:
            cp.stat_card(f"Worst ({sel_window})", worst["ticker"],
                         cp.fmt_pct(worst[sel_window]), sign=worst[sel_window])
        else:
            cp.stat_card(f"Worst ({sel_window})", cp.DASH)

    st.write("")
    tab_table, tab_heat = st.tabs(["📋 Table", "🔥 Bucket heatmap"])
    with tab_table:
        df_sorted = df.sort_values(by=sel_window, ascending=False, na_position="last")
        cp.performance_table(df_sorted, perf.RETURN_COLS)
    with tab_heat:
        st.caption("Median return per bucket × window.")
        cp.heatmap_table(perf.bucket_heatmap(df), perf.RETURN_COLS)


def view_fundamentals(filtered: pd.DataFrame):
    st.markdown('<div class="section-title">Fundamentals</div>', unsafe_allow_html=True)
    if filtered.empty:
        st.warning("No names match the current filters.")
        return
    df = build_fundamentals(filtered)
    missing = (~df["has_data"]).sum()
    if missing:
        st.caption(f"{missing} name(s) returned no data and show “—”.")
    cp.fundamentals_table(
        df, fnd.FUNDAMENTAL_COLS, fnd.MULTIPLE_COLS, fnd.PERCENT_COLS
    )


def view_detail(full: pd.DataFrame, filtered: pd.DataFrame):
    st.markdown('<div class="section-title">Stock Detail</div>', unsafe_allow_html=True)
    pool = filtered if not filtered.empty else full
    options = pool["ticker"].tolist()
    labels = {r["ticker"]: f'{r["ticker"]} · {r["company"]}' for _, r in pool.iterrows()}
    ticker = st.selectbox(
        "Select a ticker", options, format_func=lambda t: labels.get(t, t), key="detail_ticker"
    )
    if not ticker:
        return
    meta = pool[pool["ticker"] == ticker].iloc[0]

    profile = service.get_profile(ticker) or {}
    quote = service.get_quote(ticker) or {}
    series = service.get_history(ticker)

    # --- Header ---
    logo = profile.get("image") or ""
    name = profile.get("companyName") or meta["company"]
    exch = profile.get("exchangeShortName") or meta.get("exchange") or ""
    img_html = f'<img src="{logo}"/>' if logo else ""
    relevance = meta.get("ai_relevance") or ""
    st.markdown(
        f"""<div class="detail-head">{img_html}
        <div><div class="name">{name} <span style="color:{styles.MUTED};font-weight:600;">{ticker}</span></div>
        <div class="meta"><span class="badge">{meta['bucket']}</span>{exch}</div></div></div>
        <div class="relevance">{relevance}</div>""",
        unsafe_allow_html=True,
    )

    # --- Price chart with window selector ---
    win = st.radio(
        "Window", ["1W", "1M", "3M", "6M", "1Y", "5Y", "Max"],
        index=4, horizontal=True, key="detail_window",
    )
    chart_series = _slice_for_window(series, win)
    cp.price_chart(chart_series)

    # --- Window-return stat cards ---
    returns = perf.compute_window_returns(series, today_pct=quote.get("changesPercentage"))
    st.markdown('<div class="section-title">Returns</div>', unsafe_allow_html=True)
    cols = st.columns(len(perf.RETURN_COLS))
    for col, w in zip(cols, perf.RETURN_COLS):
        with col:
            cp.stat_card(w, cp.fmt_pct(returns[w]), sign=returns[w])

    # --- Key fundamentals ---
    st.markdown('<div class="section-title">Key fundamentals</div>', unsafe_allow_html=True)
    row = fnd.build_row(ticker, name, meta["bucket"])
    items = [
        ("Market Cap", cp.fmt_marketcap(row["market_cap"])),
        ("Price", cp.fmt_money(row["price"])),
        ("P/E", cp.fmt_mult(row["pe"])),
        ("Fwd P/E", cp.fmt_mult(row["fwd_pe"])),
        ("P/S", cp.fmt_mult(row["ps"])),
        ("EV/EBITDA", cp.fmt_mult(row["ev_ebitda"])),
        ("Gross Mgn", cp.fmt_pct(row["gross_margin"], signed=False)),
        ("Op Mgn", cp.fmt_pct(row["operating_margin"], signed=False)),
        ("Net Mgn", cp.fmt_pct(row["net_margin"], signed=False)),
        ("Rev Growth", cp.fmt_pct(row["rev_growth"], signed=False)),
        ("ROE", cp.fmt_pct(row["roe"], signed=False)),
        ("Beta", cp.fmt_beta(row["beta"])),
    ]
    fcols = st.columns(4)
    for i, (label, val) in enumerate(items):
        with fcols[i % 4]:
            cp.stat_card(label, val)

    # --- 5yr revenue / net income ---
    st.markdown('<div class="section-title">Revenue & net income (5y)</div>', unsafe_allow_html=True)
    cp.revenue_income_chart(service.get_income_statement(ticker, limit=5))


def _slice_for_window(series: pd.Series, win: str) -> pd.Series:
    if series is None or series.empty or win == "Max":
        return series
    offsets = {
        "1W": pd.DateOffset(weeks=1), "1M": pd.DateOffset(months=1),
        "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
        "1Y": pd.DateOffset(years=1), "5Y": pd.DateOffset(years=5),
    }
    start = series.index[-1] - offsets[win]
    return series[series.index >= start]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    styles.app_header(
        "AI Equities Tracker",
        "Live performance & fundamentals across the AI investment universe",
    )

    if not service.has_api_key():
        st.error(
            "**FMP_API_KEY not found.** Copy `.env.example` to `.env` and add your "
            "Financial Modeling Prep API key, then restart the app."
        )
        st.stop()

    try:
        full = uni.load_universe()
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()

    sidebar_controls(full)
    filtered = filter_universe(full)

    tab_perf, tab_fund, tab_detail = st.tabs(
        ["📈 Performance", "📊 Fundamentals", "🔍 Stock Detail"]
    )
    with tab_perf:
        view_performance(filtered)
    with tab_fund:
        view_fundamentals(filtered)
    with tab_detail:
        view_detail(full, filtered)


if __name__ == "__main__":
    main()
