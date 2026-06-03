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
from core import portfolios as pfl
from core import signals as sig
from core import store
from core import synthesis as synth
from core import thesis as thx
from core import timing as tm
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
    if st.session_state.get("sides"):
        out = out[out["Side"].isin(st.session_state["sides"])]
    if st.session_state.get("crests"):
        out = out[out["Crest"].isin(st.session_state["crests"])]
    q = (st.session_state.get("search", "") or "").strip().lower()
    if q:
        mask = (out["Ticker"].str.lower().str.contains(q, na=False)
                | out["Name"].str.lower().str.contains(q, na=False))
        out = out[mask]
    return out.reset_index(drop=True)


def filter_toolbar(df: pd.DataFrame, *, bucket=True, sector=True, style=True,
                   side=True, crest=True):
    """Render a compact one-row filter toolbar under the view title.

    All controls write to shared session_state keys (search/buckets/sectors/
    styles/sides/crests) so state persists across views and reruns. Only the
    requested filters are shown; the row wraps gracefully on narrow widths.
    """
    specs = [("search", 1.8)]
    if bucket:
        specs.append(("buckets", 2.0))
    if side:
        specs.append(("sides", 1.5))
    if crest:
        specs.append(("crests", 1.5))
    if sector:
        specs.append(("sectors", 2.0))
    if style:
        specs.append(("styles", 1.8))
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
            elif kind == "sides":
                st.multiselect("Side", ms.side_options(df), key="sides",
                               placeholder="Side", label_visibility="collapsed")
            elif kind == "crests":
                st.multiselect("Crest", ms.crest_options(df), key="crests",
                               placeholder="Crest", label_visibility="collapsed")
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
            # Also drop the session-state universe frame so it rebuilds (Issue 2).
            st.session_state.pop("_perf_full_frame", None)
            st.session_state.pop("_perf_full_ts", None)
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
                st.session_state["_diag_price"] = service.price_basis_report(test_t)
            for r in st.session_state.get("_diag_rows", []):
                _render_diag_row(r)
            pb = st.session_state.get("_diag_price")
            if pb:
                _render_price_basis(pb)

        _render_storage_status()

        src = []
        src.append("Finnhub" if service.has_finnhub() else "Finnhub (no key)")
        src.append("FMP" if service.has_api_key() else "FMP (no key)")
        st.markdown(
            '<div class="muted-note">Fundamentals &amp; trailing returns: '
            'Morningstar (static). Quote: ' + " → ".join(src) +
            ' (15s cache). History: FMP → Finnhub candles (15m cache).'
            '</div>', unsafe_allow_html=True)


def _render_storage_status() -> None:
    """Show which store backend is live (S3 vs local), erroring loudly if S3 is
    configured but unreachable — silent local fallback on the hosted app would
    mean thesis/index data loss on the next reboot."""
    s = store.storage_status()
    if s["backend"] == "local":
        st.markdown(
            '<div class="muted-note">Storage: <b>local</b> '
            '(.cache/ — ephemeral on hosted platforms). Set PARKNOVA_S3_BUCKET '
            'for durable shared storage.</div>', unsafe_allow_html=True)
        return
    if s["ok"]:
        st.markdown(
            f'<div class="muted-note">Storage: '
            f'<b style="color:{styles.POSITIVE};">S3</b> ({cp._esc(s["bucket"])})'
            f'</div>', unsafe_allow_html=True)
    else:
        st.error(f"Storage: S3 configured ({s['bucket']}) but unreachable — "
                 f"{s['error']}. Data would NOT persist. Fix the bucket name / "
                 "AWS credentials before relying on saved theses.")


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


def _render_price_basis(pb: dict) -> None:
    """Show close vs adjClose vs Morningstar Last vs computed 1Y side by side."""
    def _p(v):
        return cp.fmt_price(v) if v is not None else cp.DASH
    st.markdown(f'<div class="muted-note" style="margin-top:6px;"><b>Price basis '
                f'· {pb.get("ticker")}</b></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="muted-note">latest close {_p(pb.get("latest_close"))} · '
        f'latest adjClose {_p(pb.get("latest_adjclose"))} · '
        f'Morningstar Last {_p(pb.get("morningstar_last"))} · '
        f'computed 1Y (adj) {cp.fmt_pct_frac(pb.get("computed_1y"))}</div>',
        unsafe_allow_html=True)
    if pb.get("last_rows"):
        rows = " | ".join(
            f'{r["date"]}: close {_p(r["close"])}/adj {_p(r["adjClose"])}'
            for r in pb["last_rows"])
        st.markdown(f'<div class="muted-note">last rows — {rows}</div>',
                    unsafe_allow_html=True)
    if pb.get("note"):
        st.markdown(f'<div class="muted-note" style="color:{styles.NEGATIVE};">'
                    f'{cp._esc(pb["note"])}</div>', unsafe_allow_html=True)


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
    move = cp.DASH if pct is None else cp.fmt_pct_frac(pct)  # pct is a fraction
    # Honest "live" vs "last close" tag (Bug-1): markets closed -> EOD change.
    if q.get("mode") == "live":
        now = _dt.datetime.now().strftime("%H:%M:%S")
        tag = f"live · {source} · updated {now}"
    elif q.get("mode") == "last close":
        d = q.get("as_of")
        dstr = (pd.Timestamp(d).strftime("%b %d") if d is not None else "")
        tag = f"last close · {source}{(' · ' + dstr) if dstr else ''}"
    else:
        tag = source or ""
    st.markdown(
        f'<div class="metric-card"><div class="label">Today · {source}</div>'
        f'<div class="value">{cp.fmt_price(price)} '
        f'<span class="{cls}" style="font-size:0.9rem;">{move}</span></div>'
        f'<div class="sub">{tag}</div></div>',
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
    """Compact 'Today' strip (live intraday or last-session close) per ticker."""
    import datetime as _dt
    cells = st.columns(len(tickers))
    modes = set()
    for col, t in zip(cells, tickers):
        with col:
            q = service.get_live_quote(t)
            if q["price"] is None:
                cp.stat_card(t, cp.DASH, cp._esc(q["error"] or "")[:24])
            else:
                pct = q["pct"]  # fraction
                modes.add(q.get("mode"))
                cp.stat_card(t, cp.fmt_price(q["price"]),
                             cp.fmt_pct_frac(pct) if pct is not None else cp.DASH,
                             sign=pct)
    now = _dt.datetime.now().strftime("%H:%M:%S")
    if modes == {"last close"}:
        tag = "last close (markets closed)"
    elif "live" in modes:
        tag = f"live · updated {now}"
    else:
        tag = f"updated {now}"
    st.markdown(f'<div class="muted-note">{tag}</div>', unsafe_allow_html=True)


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


# The assembled 226-name momentum frame is expensive to rebuild on every tab
# flip even though its underlying history/quote fetches are @st.cache_data-cached
# (the 226-row assembly + factor pass still runs). Cache the built frame in
# session_state so switching to a universe view is near-instant after the first
# load; rebuild only on (a) manual "Refresh live data", (b) TTL expiry. The live
# *price* refresh (Performance fragment) is separate and far cheaper — it polls
# only the top-N visible quotes, never this whole sweep (momentum windows don't
# need second-by-second freshness; intraday TTL is plenty).
PERF_FRAME_TTL = 900  # 15 min, matches the history/quote cache TTL


def get_perf_full(full: pd.DataFrame) -> pd.DataFrame:
    """Universe performance frame, memoized in session_state across reruns."""
    import time
    cached = st.session_state.get("_perf_full_frame")
    ts = st.session_state.get("_perf_full_ts", 0.0)
    if cached is not None and (time.time() - ts) < PERF_FRAME_TTL:
        return cached
    if service.has_api_key():
        df = build_performance(full)
    else:
        df = perf.build_performance_frame(full, live=False)
    df = df.copy()
    df["Momentum score"] = perf.blended_momentum_score(df)
    st.session_state["_perf_full_frame"] = df
    st.session_state["_perf_full_ts"] = time.time()
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


# Live-quote overlay is scoped to the top-N sorted rows so the Performance
# table's price/Today match Stock Detail (same get_live_quote source) and refresh
# on the interval, WITHOUT polling all 226 names. 15 rows at the 15s default ≈ 60
# Finnhub calls/min — within the free tier. The remaining rows show last close.
PERF_LIVE_TOP_N = 15


def _overlay_live_quotes(df: pd.DataFrame, tickers: list) -> tuple:
    """Overlay get_live_quote onto Last Price/Today for ``tickers`` only.

    Returns (overlaid_df, modes) where modes is the set of quote modes seen
    ('live' / 'last close'). Uses the SAME cached get_live_quote as Stock Detail,
    so the two views show the identical number for a given ticker at a moment.
    """
    out = df.copy()
    if "Today" not in out.columns:
        out["Today"] = np.nan
    lp = dict(zip(out["Ticker"], out["Last Price"]))
    today = dict(zip(out["Ticker"], out["Today"]))
    modes = set()
    for t in tickers:
        q = service.get_live_quote(t)
        if q.get("price") is not None:
            lp[t] = q["price"]
        if q.get("pct") is not None:
            today[t] = q["pct"]
        if q.get("mode"):
            modes.add(q["mode"])
    out["Last Price"] = out["Ticker"].map(lp)
    out["Today"] = out["Ticker"].map(today)
    return out, modes


def _render_perf_styler(view_df: pd.DataFrame, sort_win: str):
    cols = (["Ticker", "Name", "Primary Bucket", "Side", "Crest", "Last Price"]
            + perf.ALL_WINDOWS)
    cols = [c for c in cols if c in view_df.columns]
    view = view_df[cols].sort_values(by=sort_win, ascending=False,
                                     na_position="last")
    fmt = {"Last Price": cp.fmt_price}
    for w in perf.ALL_WINDOWS:
        fmt[w] = lambda v: cp.fmt_pct_frac(v)
    num_cols = ["Last Price"] + perf.ALL_WINDOWS
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.return_bg, subset=perf.ALL_WINDOWS)
              .map(cp.bucket_cell_bg, subset=["Primary Bucket"])
              .map(cp.side_cell_bg, subset=["Side"])
              .map(cp.crest_cell_bg, subset=["Crest"])
              .set_properties(**{"font-size": "0.88rem"})
              .set_properties(subset=num_cols, **{"text-align": "right"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=560)


def _performance_table(df: pd.DataFrame, sort_win: str):
    """Returns table with a live price/Today overlay on the top-N sorted rows.

    The overlay polls get_live_quote (same source as Stock Detail) for only the
    top-N visible rows, refreshing on the Settings interval via st.fragment — the
    same pattern Stock Detail uses. 'Off' renders once (no polling).
    """
    has_live = service.has_finnhub() or service.has_api_key()
    top_live = (df.sort_values(sort_win, ascending=False, na_position="last")
                ["Ticker"].head(PERF_LIVE_TOP_N).tolist()) if has_live else []

    def _render():
        import datetime as _dt
        if top_live:
            view_df, modes = _overlay_live_quotes(df, top_live)
            now = _dt.datetime.now().strftime("%H:%M:%S")
            if "live" in modes:
                tag = (f"live · top {len(top_live)} rows · updated {now} "
                       f"(other rows show last close)")
            elif modes == {"last close"}:
                tag = f"last close · top {len(top_live)} rows"
            else:
                tag = f"updated {now}"
            st.markdown(f'<div class="muted-note">{tag}</div>',
                        unsafe_allow_html=True)
        else:
            view_df = df
        _render_perf_styler(view_df, sort_win)

    interval = _live_interval() if top_live else None
    if interval is None:
        _render()
    else:
        @st.fragment(run_every=interval)
        def _frag():
            _render()
        _frag()


def _momentum_rank(df: pd.DataFrame):
    st.caption("Blended momentum = avg z-score of 1M / 3M / 6M (live) + 1Y "
               "(Morningstar), computed across the full universe.")
    d = df.copy()
    # Momentum score is precomputed universe-wide on perf_full (Bug-2 fix); a
    # filtered view must reuse it, never recompute on the subset. Fall back to a
    # universe-wide compute only if the column is somehow absent.
    if "Momentum score" not in d.columns:
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

    base = ["Ticker", "Name", "Primary Bucket", "Side", "Crest", "Sector"]
    base = [c for c in base if c in df.columns]
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
              .map(cp.side_cell_bg, subset=["Side"])
              .map(cp.crest_cell_bg, subset=["Crest"])
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=560)
    st.caption(f"{len(view)} names · {len(selected_cols)} metrics. "
               "Blanks shown as “—” (never coerced to 0).")


def _factor_scores_table(scores: pd.DataFrame, filtered_tickers: set):
    st.caption("Cross-sectional factor percentiles (0–100, higher = better) "
               "with an equal-weight composite. Tune weights on the Screener tab.")
    # Bug-2 fix: factor percentiles + composite are universe-wide (computed on
    # the full scores frame); filtering only subsets rows for display.
    full_scored = scores.copy()
    full_scored["Composite"] = fc.weighted_composite(scores, fc.default_weights())
    df = full_scored[full_scored["Ticker"].isin(filtered_tickers)].copy()
    if df.empty:
        st.warning("No names match the current filters.")
        return
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
    st.caption("Adjust factor weights to re-rank the universe; use the crest tilt "
               "and 'sort by upside to FV' for timing-aware value — distinguishing "
               "cheap-because-mispriced from cheap-because-the-cycle-is-turning.")

    cols = st.columns(len(fc.FACTOR_NAMES))
    weights = {}
    for col, name in zip(cols, fc.FACTOR_NAMES):
        with col:
            weights[name] = st.slider(name, 0.0, 3.0, 1.0, 0.5, key=f"w_{name}")

    # Timing-aware controls.
    tcol = st.columns([1.4, 1.4, 1.2])
    with tcol[0]:
        tilt_crest = st.selectbox("Crest tilt", ["None"] + tm.CREST_ORDER,
                                  index=0, key="screen_tilt")
    with tcol[1]:
        tilt_str = st.slider("Tilt strength", 0, 30, 10, 5, key="screen_tilt_str",
                             disabled=(tilt_crest == "None"))
    with tcol[2]:
        sort_by = st.selectbox("Sort by", ["Composite", "Upside to FV"],
                               index=0, key="screen_sort")

    # Bug-2 fix: compute the weighted composite on the FULL universe (so its
    # z-scores and percentile are universe-relative), then subset rows for
    # display. Weights/tilt change the blend live, but never the universe.
    full_scored = scores.copy()
    full_scored["Composite"] = fc.weighted_composite(scores, weights)
    if tilt_crest != "None" and "Crest" in full_scored.columns:
        full_scored["Composite"] = (full_scored["Composite"] + (
            (full_scored["Crest"] == tilt_crest).astype(float) * float(tilt_str))
        ).clip(0, 100)

    df = full_scored[full_scored["Ticker"].isin(filtered_tickers)].copy()
    if df.empty:
        st.warning("No names match the current filters.")
        return

    # Merge timing columns (upside_fv / value_trap / tier) from the full frame.
    tcols_keep = ["Ticker", "valuation_tier", "value_trap", "upside_fv"]
    df = df.merge(full[tcols_keep], on="Ticker", how="left")

    if sort_by == "Upside to FV":
        df = df.sort_values("upside_fv", ascending=False, na_position="last")
    else:
        df = df.sort_values("Composite", ascending=False, na_position="last")

    n_trap = int(df["value_trap"].sum())
    if n_trap:
        st.caption(f"{n_trap} name(s) on value-trap watch in this set "
                   "(amber 'watch' in the Trap column).")

    top = df.head(3)
    tcards = st.columns(3)
    for i, (col, (_, r)) in enumerate(zip(tcards, top.iterrows())):
        with col:
            cp.stat_card(f"#{i + 1} {r['Ticker']}", cp.fmt_score(r["Composite"]),
                         r["Name"] or "")

    st.write("")
    df["Trap"] = df["value_trap"].map(lambda b: "watch" if b else "")
    show = (["Ticker", "Name", "Side", "Crest", "valuation_tier"]
            + fc.FACTOR_NAMES + ["Composite", "upside_fv", "Trap"])
    show = [c for c in show if c in df.columns]
    score_cols = fc.FACTOR_NAMES + ["Composite"]
    view = df[show].rename(columns={"valuation_tier": "Tier", "upside_fv": "Upside FV"})
    fmt = {c: cp.fmt_score for c in score_cols}
    fmt["Upside FV"] = lambda v: cp.fmt_pct_frac(v)
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.score_bg, subset=score_cols)
              .map(cp.side_cell_bg, subset=["Side"])
              .map(cp.crest_cell_bg, subset=["Crest"])
              .map(cp.return_bg, subset=["Upside FV"])
              .set_properties(**{"font-size": "0.86rem"}))
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
# View: Capex Cycle (timing dashboard)
# ---------------------------------------------------------------------------
_ROTATION_LIVE = ["1W", "1M", "3M"]      # short windows from the price feed
_ROTATION_MS = ["YTD", "1Y"]              # Morningstar trailing windows


def view_capex_cycle(full: pd.DataFrame, perf_full: pd.DataFrame,
                     scores: pd.DataFrame):
    st.markdown('<div class="view-title">Capex Cycle — Side &times; Crest timing'
                '</div>', unsafe_allow_html=True)
    filter_toolbar(full, sector=False, style=False)
    ft = set(apply_filters(full)["Ticker"])
    fd = full[full["Ticker"].isin(ft)].reset_index(drop=True)
    pf = perf_full[perf_full["Ticker"].isin(ft)].reset_index(drop=True)
    if fd.empty:
        st.warning("No names match the current filters.")
        return

    t_map, t_rot, t_trend, t_board = st.tabs(
        ["Crest x Side matrix", "Rotation tracker", "Crest trends",
         "Layer leaderboard"])
    with t_map:
        _capex_matrix(fd)
    with t_rot:
        _rotation_tracker(pf)
    with t_trend:
        _crest_trends(full)
    with t_board:
        _layer_leaderboard(fd, pf)


_TREND_WINDOWS = {"3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
                  "1Y": pd.DateOffset(years=1), "2Y": pd.DateOffset(years=2),
                  "Max": None}


def _crest_trends(full: pd.DataFrame):
    from core import crest_index as ci

    st.markdown(
        '<div class="news-item" style="border-color:#CA8A04;background:#FEFCE8;">'
        '<b>Backcast caveat.</b> Indices use the <b>current (2026) crest '
        'classification</b> applied to historical prices — i.e. "how would '
        "today's crest groups have moved\", not a contemporaneous read. Names "
        'enter each index only on dates they have real price history (watch the '
        'constituent counts); the recent window (2024→present) is where the '
        'signal is meaningful.</div>', unsafe_allow_html=True)

    ctl = st.columns([1.2, 1.4, 1.4])
    with ctl[0]:
        win = st.selectbox("Window", list(_TREND_WINDOWS.keys()), index=2,
                           key="trend_win")
    with ctl[1]:
        gtype = st.radio("Group", ["Crest (Early/Mid/Late)", "Side (Supply/Demand)"],
                         horizontal=True, key="trend_group")
    with ctl[2]:
        if st.button("Rebuild indices", width="stretch", key="rebuild_idx"):
            with st.spinner("Rebuilding crest/side indices from price history…"):
                bar = st.progress(0.0)
                service.get_crest_indices(
                    full, force=True,
                    progress=lambda f, t: bar.progress(f, text=f"History… {t}"))
                bar.empty()
            st.rerun()

    if not (service.has_api_key() or service.has_finnhub()):
        st.info("Set FMP_API_KEY (or FINNHUB_API_KEY) to build price-based indices.")
        return

    bar = st.progress(0.0, text="Loading crest/side indices…")
    res = service.get_crest_indices(
        full, force=False,
        progress=lambda f, t: bar.progress(f, text=f"History… {t}"))
    bar.empty()
    idx = pd.DataFrame(res["records"])
    if idx.empty:
        st.warning("No index data — price history unavailable for the universe.")
        return
    idx["date"] = pd.to_datetime(idx["date"])

    group_type = "crest" if gtype.startswith("Crest") else "side"
    color_map = styles.CREST_COLORS if group_type == "crest" else styles.SIDE_COLORS
    wide_full = ci.pivot_levels(idx, group_type)
    counts = ci.constituent_counts(idx, group_type)
    if wide_full.empty:
        st.warning("No series for this group.")
        return

    # Re-rebase to 100 at the start of the selected window.
    off = _TREND_WINDOWS[win]
    start = (wide_full.index.max() - off) if off is not None else None
    wide = ci.window_rebased(wide_full, start)
    counts_w = counts[counts.index >= start] if start is not None else counts

    asof = (res.get("as_of") or "")[:10]
    avail = f"{wide_full.index.min():%Y-%m-%d} → {wide_full.index.max():%Y-%m-%d}"
    st.caption(f"Rebased to 100 at window start · available history {avail} · "
               f"as of {asof}{' · rebuilt' if res.get('rebuilt') else ''}")

    cp.index_lines_chart(wide, color_map,
                         title=f"{group_type.title()} indices ({win})")

    # Computed rotation read.
    if group_type == "crest":
        read = ci.rotation_read(wide, "Early", "Late")
        if read:
            st.markdown(f'<div class="section-title">Over {win}: {read}</div>',
                        unsafe_allow_html=True)
        st.markdown('<div class="section-title">Early − Late spread (rotation '
                    'signal)</div>', unsafe_allow_html=True)
        cp.spread_line_chart(ci.spread_series(wide, "Early", "Late"))

    st.markdown('<div class="section-title">Constituents per group over time'
                '</div>', unsafe_allow_html=True)
    st.caption("Thin counts = backcast-heavy / pre-IPO; thicker = more real "
               "history backing the index.")
    cp.constituent_count_chart(counts_w, color_map)


def _capex_matrix(fd: pd.DataFrame):
    st.caption("Where capital is concentrated: count, median 1Y return and "
               "median forward P/E for each crest layer x side.")
    mat = tm.crest_side_matrix(fd)
    # Pivot to a readable grid with a combined cell string per (crest, side).
    sides = tm.SIDE_ORDER
    grid_rows = []
    for crest in tm.CREST_ORDER:
        row = {"Crest": crest}
        for side in sides:
            c = mat[(mat["Crest"] == crest) & (mat["Side"] == side)]
            if len(c) and int(c["n"].iloc[0]) > 0:
                n = int(c["n"].iloc[0])
                m1y = c["median_1y"].iloc[0]
                pe = c["median_fwd_pe"].iloc[0]
                row[side] = (f"{n} names · 1Y "
                             f"{cp.fmt_pct_unit(m1y)} · "
                             f"P/E {cp.fmt_mult(pe)}")
            else:
                row[side] = cp.DASH
        grid_rows.append(row)
    grid = pd.DataFrame(grid_rows)
    styler = (grid.style
              .map(cp.crest_cell_bg, subset=["Crest"])
              .set_properties(**{"font-size": "0.85rem"}))
    st.dataframe(styler, width="stretch", hide_index=True)


def _rotation_tracker(pf: pd.DataFrame):
    st.caption("The timing signal: capital rotating OUT of late-crest INTO "
               "early-crest shows up as early-crest short-window returns "
               "exceeding late-crest.")
    # Prefer live short windows; fall back to Morningstar if they're all empty.
    live_cols = [w for w in _ROTATION_LIVE
                 if w in pf.columns and pf[w].notna().any()]
    use_cols = live_cols + [w for w in _ROTATION_MS if w in pf.columns]
    if not live_cols:
        st.info("Live short-window returns unavailable (price feed empty) — "
                "showing Morningstar YTD/1Y only.")
    mat = tm.rotation_table(pf, use_cols)
    if mat.empty:
        st.info("No return data for the rotation tracker.")
        return
    cp.heatmap(mat, list(mat.columns), pct_fraction=True)

    # Computed direction caption — prefer the shortest live window available.
    cap_window = (live_cols[1] if len(live_cols) > 1 else
                  (live_cols[0] if live_cols else
                   ("1Y" if "1Y" in pf.columns else None)))
    cap = tm.rotation_caption(pf, cap_window) if cap_window else None
    if cap:
        label = "" if cap_window in _ROTATION_LIVE else " (Morningstar)"
        st.markdown(f'<div class="section-title">{cap}{label}</div>',
                    unsafe_allow_html=True)


def _layer_leaderboard(fd: pd.DataFrame, pf: pd.DataFrame):
    layer = st.selectbox("Crest layer", tm.CREST_ORDER,
                         index=0, key="capex_layer")
    sub = fd[fd["Crest"] == layer].copy()
    if sub.empty:
        st.info(f"No names in the {layer} layer for the current filters.")
        return

    # Pullback from 52w high: compute from FMP-stable history when available.
    pulls, src_note = _pullbacks_from_high(sub["Ticker"].tolist())
    sub["Pullback"] = sub["Ticker"].map(pulls)
    sub["Fwd P/E"] = sub.apply(tm.effective_forward_pe, axis=1)
    sub["Upside FV"] = sub["upside_fv"]
    sub["Trap"] = sub["value_trap"].map(lambda b: "watch" if b else "")

    cols = ["Ticker", "Name", "Side", "valuation_tier", "Fwd P/E", "Pullback",
            "Total Return (1Y)", "Upside FV", "Trap"]
    view = sub[cols].rename(columns={"valuation_tier": "Tier",
                                     "Total Return (1Y)": "1Y"})
    view = view.sort_values("Upside FV", ascending=False, na_position="last")
    fmt = {"Fwd P/E": cp.fmt_mult,
           "Pullback": lambda v: cp.fmt_pct_frac(v),
           "1Y": lambda v: cp.fmt_pct_unit(v),
           "Upside FV": lambda v: cp.fmt_pct_frac(v)}
    styler = (view.style.format(fmt, na_rep=cp.DASH)
              .map(cp.side_cell_bg, subset=["Side"])
              .map(cp.return_bg, subset=["Pullback", "Upside FV"])
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=480)
    st.caption(src_note + "  ·  'watch' = value-trap (low fwd P/E + early-crest "
               "+ extended 1Y).")


def _pullbacks_from_high(tickers: list) -> tuple:
    """Map ticker -> (Last - 52wHigh)/52wHigh using FMP-stable history if any.

    Returns ``(mapping, note)``. When no history is available the mapping is
    empty and the note says so (the column then shows '—').
    """
    pulls: dict = {}
    have_hist = False
    for t in tickers[:60]:  # cap the per-render history fetches
        h = service.get_history_result(t)
        s = h["series"]
        if s is None or s.empty:
            continue
        have_hist = True
        last = float(s.iloc[-1])
        hi = float(s.tail(252).max()) if len(s) >= 5 else float(s.max())
        if hi:
            pulls[t] = last / hi - 1.0
    note = ("Pullback = (last - 52w high) / 52w high from price history."
            if have_hist else
            "Pullback needs price history (feed empty) — showing 1Y instead.")
    return pulls, note


# ---------------------------------------------------------------------------
# View: News & Filings (Feature B)
# ---------------------------------------------------------------------------
def view_news(full: pd.DataFrame):
    st.markdown('<div class="view-title">News &amp; Filings</div>',
                unsafe_allow_html=True)
    options = full["Ticker"].tolist()
    labels = dict(zip(full["Ticker"], full["Name"]))
    ticker = st.selectbox(
        "Ticker", options, index=None,
        placeholder="Select a ticker…",
        format_func=lambda t: f"{t} · {labels.get(t, '')}",
        key="selected_ticker")

    if ticker is None:
        st.info("Select a ticker above to view news and filings.")
        return
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


def view_detail(full: pd.DataFrame, scores: pd.DataFrame):
    st.markdown('<div class="view-title">Stock Detail</div>',
                unsafe_allow_html=True)
    options = full["Ticker"].tolist()
    labels = dict(zip(full["Ticker"], full["Name"]))
    ticker = st.selectbox(
        "Select a ticker", options, index=None,
        placeholder="Select a ticker…",
        format_func=lambda t: f"{t} · {labels.get(t, '')}",
        key="selected_ticker")

    if ticker is None:
        st.info("Select a ticker above to view its detail.")
        return

    row = full[full["Ticker"] == ticker].iloc[0]

    # --- Header ---
    rating = row.get("Morningstar Rating for Stocks")
    moat = row.get("Economic Moat")
    upside = row.get("upside_pct")
    trap_chip = (cp.value_trap_chip(tm.value_trap_reason(row))
                 if row.get("value_trap") else "")
    head = (
        f'<div class="detail-head"><div class="name">{row.get("Name") or ticker} '
        f'<span class="tk">{ticker}</span></div>'
        f'<div class="meta">{cp.bucket_chip(row.get("Primary Bucket"))} '
        f'{cp.side_chip(row.get("Side"))} {cp.crest_chip(row.get("Crest"))} '
        f'{trap_chip} &nbsp; '
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

    # --- Capex cycle position card ---
    st.markdown('<div class="section-title">Capex cycle position</div>',
                unsafe_allow_html=True)
    cc = st.columns(4)
    with cc[0]:
        cp.stat_card("Side", row.get("Side") or cp.DASH)
    with cc[1]:
        cp.stat_card("Crest", f'{row.get("Crest") or cp.DASH}-crest'
                     if row.get("Crest") else cp.DASH)
    with cc[2]:
        cp.stat_card("Valuation tier", row.get("valuation_tier") or cp.DASH)
    with cc[3]:
        cp.stat_card("Upside to FV", cp.fmt_pct_frac(row.get("upside_fv")),
                     sign=row.get("upside_fv"))
    if row.get("value_trap"):
        st.markdown(
            f'<div class="muted-note" style="color:{styles.TRAP_AMBER};">'
            f'Value-trap watch — {cp._esc(tm.value_trap_reason(row))}</div>',
            unsafe_allow_html=True)
    if row.get("Crest Rationale"):
        st.markdown(f'<div class="muted-note">{cp._esc(row["Crest Rationale"])}'
                    f'</div>', unsafe_allow_html=True)

    # --- Price chart with window selector ---
    st.markdown('<div class="section-title">Price</div>', unsafe_allow_html=True)
    win = st.radio("Window", list(_DETAIL_WINDOWS.keys()) + ["Max"],
                   index=4, horizontal=True, key="detail_win")
    series = pd.Series(dtype="float64")
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

    # --- Return cards (9 windows) — computed from the already-fetched series ---
    st.markdown('<div class="section-title">Returns</div>', unsafe_allow_html=True)
    _detail_return_cards(ticker, row, series)

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


def _detail_return_cards(ticker: str, row: pd.Series, series: pd.Series) -> None:
    """Render 9 return-window stat cards from the already-fetched history series.

    Live windows (Today/1W/1M/3M/6M) come from the adjusted series.
    Trailing windows (YTD/1Y/3Y/5Y): computed from adjusted series, falling
    back to the Morningstar columns when there isn't enough history — same
    logic as build_performance_frame, but for a single ticker only.
    """
    live_vals = perf.live_windows_from_series(series, None, ticker=ticker)
    ms_vals: dict = {}
    for w in perf.MS_WINDOWS:
        computed = (perf.ytd_return(series) if w == "YTD"
                    else perf.trailing_return(series, perf._MS_OFFSETS[w]))
        ms_raw = row.get(perf._MS_SOURCE[w])
        if computed is not None:
            ms_vals[w] = computed
        elif ms_raw is not None and pd.notna(ms_raw):
            ms_vals[w] = float(ms_raw) / 100.0
    all_vals = {**live_vals, **ms_vals}
    rcols = st.columns(len(perf.ALL_WINDOWS))
    for col, w in zip(rcols, perf.ALL_WINDOWS):
        with col:
            v = all_vals.get(w)
            cp.stat_card(w, cp.fmt_pct_frac(v), sign=v)


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
# View: Thesis (structured, conviction-weighted decision log per ticker)
# ---------------------------------------------------------------------------
def _imported_for_ticker(ticker: str) -> list:
    """Imported filing analyses whose ticker matches (for the evidence snapshot)."""
    out = []
    for obj in service.get_imported_analyses().values():
        if str(obj.get("ticker", "")).upper() == ticker.upper():
            out.append(obj)
    return out


def _capture_for(ticker: str, full: pd.DataFrame, scores: pd.DataFrame) -> dict:
    """Build a frozen evidence snapshot from static frames + one ticker's history."""
    def _row(df):
        sub = df[df["Ticker"] == ticker]
        return sub.iloc[0] if not sub.empty else None

    # Compute return windows from a single history fetch (not a 226-ticker sweep).
    perf_row = None
    if service.has_api_key() or service.has_finnhub():
        hist = service.get_history_result(ticker)
        series = hist.get("series", pd.Series(dtype="float64"))
        if not series.empty:
            live_vals = perf.live_windows_from_series(series, None, ticker=ticker)
            ms_vals: dict = {}
            fr = _row(full)
            for w in perf.MS_WINDOWS:
                computed = (perf.ytd_return(series) if w == "YTD"
                            else perf.trailing_return(series, perf._MS_OFFSETS[w]))
                ms_raw = fr.get(perf._MS_SOURCE[w]) if fr is not None else None
                if computed is not None:
                    ms_vals[w] = computed
                elif ms_raw is not None and pd.notna(ms_raw):
                    ms_vals[w] = float(ms_raw) / 100.0
            perf_row = pd.Series({**live_vals, **ms_vals})

    return thx.capture_evidence(ticker, _row(full), _row(scores),
                                perf_row, _imported_for_ticker(ticker))


def view_thesis(full: pd.DataFrame, scores: pd.DataFrame):
    st.markdown('<div class="view-title">Thesis — conviction-weighted decision '
                'log</div>', unsafe_allow_html=True)
    t_edit, t_roll = st.tabs(["Editor & journal", "Portfolio rollup"])
    with t_edit:
        _thesis_editor(full, scores)
    with t_roll:
        _thesis_rollup(full)


def _thesis_editor(full: pd.DataFrame, scores: pd.DataFrame):
    # "Theses on file" chips — clickable shortcuts to jump to a saved ticker.
    saved = thx.list_theses()
    if saved:
        st.caption("Theses on file — click to load:")
        chip_cols = st.columns(min(len(saved), 8))
        for col, rec in zip(chip_cols, saved):
            with col:
                if st.button(rec["ticker"], key=f"chip_{rec['ticker']}",
                             use_container_width=True):
                    st.session_state["selected_ticker"] = rec["ticker"]
                    st.session_state.pop(f"_th_seeded_{rec['ticker']}", None)
                    st.rerun()

    options = full["Ticker"].tolist()
    labels = dict(zip(full["Ticker"], full["Name"]))
    ticker = st.selectbox(
        "Ticker", options, index=None,
        placeholder="Select a ticker…",
        format_func=lambda t: f"{t} · {labels.get(t, '')}",
        key="selected_ticker")

    if ticker is None:
        st.info("Select a ticker above to begin or edit a thesis.")
        return

    evidence = _capture_for(ticker, full, scores)
    row = full[full["Ticker"] == ticker].iloc[0]

    # --- Evidence panel (read-only, auto-pulled) ---
    st.markdown('<div class="section-title">Evidence (auto-captured)</div>',
                unsafe_allow_html=True)
    chips = (cp.bucket_chip(row.get("Primary Bucket")) + " "
             + cp.side_chip(row.get("Side")) + " " + cp.crest_chip(row.get("Crest"))
             + " " + (cp.value_trap_chip(tm.value_trap_reason(row))
                      if row.get("value_trap") else ""))
    st.markdown(f'<div class="detail-head"><div class="meta">{chips}</div></div>',
                unsafe_allow_html=True)
    val = evidence["valuation"]
    ec = st.columns(4)
    with ec[0]:
        cp.stat_card("Last / Fair", f'{cp.fmt_price(val["last_price"])} / '
                     f'{cp.fmt_price(val["fair_value"])}')
    with ec[1]:
        cp.stat_card("Upside to FV", cp.fmt_pct_frac(val["upside_fv"]),
                     sign=val["upside_fv"])
    with ec[2]:
        cp.stat_card("Fwd P/E · Tier", f'{cp.fmt_mult(val["forward_pe"])} · '
                     f'{val["valuation_tier"] or cp.DASH}')
    with ec[3]:
        comp = evidence["factors"].get("Composite")
        cp.stat_card("Factor composite", cp.fmt_score(comp))

    ecol1, ecol2 = st.columns([1, 1])
    with ecol1:
        fdict = {n: evidence["factors"].get(n) for n in fc.FACTOR_NAMES}
        med = {n: scores[n].median() for n in fc.FACTOR_NAMES if n in scores}
        if any(v is not None for v in fdict.values()):
            cp.factor_radar(fdict, med)
    with ecol2:
        ret = evidence["returns"]
        rc = st.columns(3)
        for i, w in enumerate(["1M", "6M", "1Y", "3Y", "5Y", "Today"]):
            with rc[i % 3]:
                cp.stat_card(w, cp.fmt_pct_frac(ret.get(w)), sign=ret.get(w))
        f = evidence["filings"]
        if f["imported_count"]:
            st.caption(f"{f['imported_count']} imported filing analysis(es)."
                       + (f" Latest net read: {f['latest_net_read']}"
                          if f["latest_net_read"] else ""))

    # --- Seed editor state from the saved record (or import) ---
    record = thx.read_thesis(ticker)
    _seed_thesis_state(ticker, record)

    # --- Import drafted thesis (zero-cost AI path) ---
    with st.expander("Draft with Claude (export / import — no paid API)"):
        st.caption("Export the evidence brief, draft the thesis in Claude (Max "
                   "plan) with a thesis-drafter skill, then paste the JSON back.")
        st.download_button("Export thesis brief (JSON)",
                           data=thx.export_brief(ticker, evidence,
                                                 _current_from_state(ticker)),
                           file_name=f"{ticker}_thesis_brief.json",
                           mime="application/json", key=f"exp_{ticker}")
        drafted = st.text_area("Paste drafted thesis JSON to import",
                               key=f"imp_text_{ticker}", height=80)
        if st.button("Import drafted thesis", key=f"imp_btn_{ticker}"):
            parsed = thx.parse_imported_thesis(drafted)
            if parsed is None:
                st.warning("Could not parse a thesis from that JSON.")
            else:
                _apply_current_to_state(ticker, parsed)
                st.success("Imported — review and Save to record it.")
                st.rerun()

    # --- Thesis editor ---
    st.markdown('<div class="section-title">Thesis</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.selectbox("Stance", thx.STANCES, key=f"th_stance_{ticker}")
    with c2:
        st.slider("Conviction (1-10)", 1, 10, key=f"th_conviction_{ticker}")
    with c3:
        st.number_input("Position size (% of portfolio)", 0.0, 100.0, step=0.5,
                        key=f"th_size_{ticker}")
    st.text_input("Time horizon", key=f"th_horizon_{ticker}",
                  placeholder="e.g. 18-24 months")
    bc1, bc2 = st.columns(2)
    with bc1:
        st.text_area("Bull case", key=f"th_bull_{ticker}", height=110)
        st.text_area("Catalysts (one per line)", key=f"th_cat_{ticker}", height=90)
        st.text_area("Valuation view", key=f"th_val_{ticker}", height=90)
    with bc2:
        st.text_area("Bear case", key=f"th_bear_{ticker}", height=110)
        st.text_area("Risks (one per line)", key=f"th_risk_{ticker}", height=90)
        st.text_area("Crest note (capex-cycle timing call)",
                     key=f"th_crest_{ticker}", height=90)
    st.text_area("Defined exit (the condition that proves you wrong)",
                 key=f"th_exit_{ticker}", height=70)
    note = st.text_input("Journal note for this save", key=f"th_note_{ticker}",
                         placeholder="What changed since last entry?")

    if st.button("Save thesis + journal entry", type="primary",
                 key=f"th_save_{ticker}"):
        cur = _current_from_state(ticker)
        fresh = _capture_for(ticker, full, scores)  # freeze at save
        thx.save_thesis(ticker, cur, fresh, note=note)
        st.success(f"Saved {ticker} thesis and appended a journal entry.")
        st.rerun()

    # --- Journal timeline + conviction chart ---
    record = thx.read_thesis(ticker)
    if record and record.get("journal"):
        st.markdown('<div class="section-title">Conviction over time</div>',
                    unsafe_allow_html=True)
        cp.conviction_chart(thx.conviction_history(record))
        st.markdown('<div class="section-title">Journal</div>',
                    unsafe_allow_html=True)
        _render_journal(record)
    else:
        st.caption("No journal yet — save a thesis to begin the record.")


def _seed_thesis_state(ticker: str, record) -> None:
    """Initialise editor widget state from a saved record (once per ticker)."""
    cur = (record or {}).get("current") or thx.empty_current()
    sentinel = f"_th_seeded_{ticker}"
    if st.session_state.get(sentinel):
        return
    _apply_current_to_state(ticker, cur)
    st.session_state[sentinel] = True


def _apply_current_to_state(ticker: str, cur: dict) -> None:
    base = thx.empty_current()
    base.update({k: v for k, v in (cur or {}).items() if v is not None})
    st.session_state[f"th_stance_{ticker}"] = (
        base["stance"] if base["stance"] in thx.STANCES else "Watch")
    st.session_state[f"th_conviction_{ticker}"] = int(base.get("conviction") or 5)
    st.session_state[f"th_size_{ticker}"] = float(base.get("position_size_pct") or 0.0)
    st.session_state[f"th_horizon_{ticker}"] = base.get("time_horizon") or ""
    st.session_state[f"th_bull_{ticker}"] = base.get("bull_case") or ""
    st.session_state[f"th_bear_{ticker}"] = base.get("bear_case") or ""
    st.session_state[f"th_val_{ticker}"] = base.get("valuation_view") or ""
    st.session_state[f"th_crest_{ticker}"] = base.get("crest_note") or ""
    st.session_state[f"th_exit_{ticker}"] = base.get("defined_exit") or ""
    st.session_state[f"th_cat_{ticker}"] = "\n".join(base.get("catalysts") or [])
    st.session_state[f"th_risk_{ticker}"] = "\n".join(base.get("risks") or [])


def _current_from_state(ticker: str) -> dict:
    def g(field, default=""):
        return st.session_state.get(f"th_{field}_{ticker}", default)

    def lines(field):
        return [ln.strip() for ln in (g(field) or "").splitlines() if ln.strip()]
    return {
        "stance": g("stance", "Watch"),
        "conviction": int(g("conviction", 5) or 5),
        "time_horizon": g("horizon"),
        "position_size_pct": float(g("size", 0.0) or 0.0),
        "bull_case": g("bull"), "bear_case": g("bear"),
        "valuation_view": g("val"), "catalysts": lines("cat"),
        "risks": lines("risk"), "crest_note": g("crest"),
        "defined_exit": g("exit"),
    }


def _render_journal(record: dict) -> None:
    for e in reversed(record.get("journal", [])):
        ts = str(e.get("ts", ""))[:16].replace("T", " ")
        snap = e.get("evidence_snapshot") or {}
        up = (snap.get("valuation") or {}).get("upside_fv")
        comp = (snap.get("factors") or {}).get("Composite")
        meta = (f"upside {cp.fmt_pct_frac(up)} · composite {cp.fmt_score(comp)}")
        st.markdown(
            f'<div class="news-item"><b>{e.get("stance") or ""}</b> · conviction '
            f'{e.get("conviction")} · size {e.get("position_size_pct")}%'
            f'<div class="news-meta">{ts} · {cp._esc(e.get("note") or "")}</div>'
            f'<div class="news-meta">{meta}</div></div>', unsafe_allow_html=True)


def _thesis_rollup(full: pd.DataFrame) -> None:
    records = thx.list_theses()
    if not records:
        st.info("No theses saved yet. Write one in the Editor tab.")
        return
    live = full.set_index("Ticker")
    rows = []
    for r in records:
        tk = r["ticker"]
        cur = r.get("current") or {}
        lr = live.loc[tk] if tk in live.index else None
        rows.append({
            "Ticker": tk,
            "Stance": cur.get("stance"),
            "Conviction": cur.get("conviction"),
            "Size %": cur.get("position_size_pct"),
            "Crest": (lr.get("Crest") if lr is not None else None),
            "Upside FV": (lr.get("upside_fv") if lr is not None else None),
            "Value-trap": ("watch" if (lr is not None and lr.get("value_trap"))
                           else ""),
            "Updated": str(r.get("updated_at", ""))[:10],
        })
    df = pd.DataFrame(rows)
    total = pd.to_numeric(df["Size %"], errors="coerce").sum()

    sc = st.columns(3)
    with sc[0]:
        cp.stat_card("Theses", str(len(df)))
    with sc[1]:
        cp.stat_card("Total intended size", f"{total:.1f}%",
                     sign=(1 if total <= 100 else -1))
    with sc[2]:
        cp.stat_card("Avg conviction",
                     cp.fmt_ratio(pd.to_numeric(df["Conviction"],
                                                errors="coerce").mean()))
    if total > 100:
        st.warning(f"Intended position sizes sum to {total:.1f}% (> 100%).")

    fmt = {"Upside FV": lambda v: cp.fmt_pct_frac(v),
           "Size %": lambda v: f"{v:.1f}%" if pd.notna(v) else cp.DASH,
           "Conviction": lambda v: f"{int(v)}" if pd.notna(v) else cp.DASH}
    styler = (df.style.format(fmt, na_rep=cp.DASH)
              .map(cp.crest_cell_bg, subset=["Crest"])
              .map(cp.return_bg, subset=["Upside FV"])
              .set_properties(**{"font-size": "0.88rem"}))
    st.dataframe(styler, width="stretch", hide_index=True)

    # Group by crest layer — thesis exposure across the capex cycle.
    st.markdown('<div class="section-title">Intended exposure by crest layer'
                '</div>', unsafe_allow_html=True)
    grp = (df.assign(_s=pd.to_numeric(df["Size %"], errors="coerce"))
           .groupby(df["Crest"].fillna("Unknown"))["_s"].sum())
    gcols = st.columns(max(len(grp), 1))
    for col, (layer, sz) in zip(gcols, grp.items()):
        with col:
            cp.stat_card(str(layer), f"{sz:.1f}%")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# View: Signals
# ---------------------------------------------------------------------------
_DISCLAIMER = (
    "Conditions flagged from current data for your review. "
    "Not investment advice or predictions."
)


def _get_crest_wide(full: pd.DataFrame):
    """Fetch the crest index and return the wide Early/Mid/Late frame (or empty)."""
    from core import crest_index as ci

    if not (service.has_api_key() or service.has_finnhub()):
        return pd.DataFrame()
    bar = st.progress(0.0, text="Loading crest index for signal computation…")
    res = service.get_crest_indices(
        full, force=False,
        progress=lambda f, t: bar.progress(f, text=f"History… {t}"))
    bar.empty()
    records = res.get("records") or []
    if not records:
        return pd.DataFrame()
    idx = pd.DataFrame(records)
    idx["date"] = pd.to_datetime(idx["date"])
    return ci.pivot_levels(idx, "crest")


def _render_signal_1(rot: dict) -> None:
    """Rotation inflection advisory with marked spread chart and key numbers."""
    regime = rot["regime"]
    is_inflection = regime in ("inflection_late", "inflection_early")

    if is_inflection:
        direction = "toward Late-crest" if regime == "inflection_late" else "toward Early-crest"
        st.markdown(
            f'<div class="news-item" style="border-color:{styles.SIGNAL_AMBER}; '
            f'background:#FEFCE8;">'
            f'<b>Rotation inflection — {direction}</b> (both conditions met)</div>',
            unsafe_allow_html=True)
    else:
        regime_label = {
            "early_leading": "Early-crest leading",
            "late_leading": "Late-crest leading",
            "neutral": "Regime unclear",
        }.get(regime, regime)
        st.markdown(
            f'<div class="news-item" style="border-color:{styles.BORDER};">'
            f'<b>Current regime: {regime_label}</b></div>',
            unsafe_allow_html=True)

    st.markdown(
        f'<div class="muted-note" style="margin:6px 0 10px 0;">'
        f'{cp._esc(rot["advisory"])}</div>',
        unsafe_allow_html=True)

    # Key numbers row
    kc = st.columns(4)
    def _fmts(v):
        return f"{v:+.3f}/day" if v is not None else cp.DASH
    def _fmtp(v):
        return cp.fmt_pct_frac(v) if v is not None else cp.DASH

    with kc[0]:
        cp.stat_card("Spread (E−L)",
                     f"{rot['spread_current']:.1f} pts" if rot["spread_current"] is not None else cp.DASH)
    with kc[1]:
        cp.stat_card("Slope (recent)", _fmts(rot["slope_recent"]),
                     f"{sig.SLOPE_RECENT_DAYS}-day window")
    with kc[2]:
        cp.stat_card("Early 1M (median)", _fmtp(rot["early_1m"]))
    with kc[3]:
        cp.stat_card("Late 1M (median)", _fmtp(rot["late_1m"]))

    # Spread chart with inflection marker
    cp.spread_with_inflection_chart(
        rot["spread_series"],
        inflection_date=rot["inflection_date"],
        title="Early − Late spread (full history)")


def _render_stretch_warnings(warnings: list) -> None:
    if not warnings:
        st.markdown(
            '<div class="muted-note">No stretch conditions detected in the '
            'current universe.</div>', unsafe_allow_html=True)
        return
    for w in warnings[:15]:
        n_conds = w["conditions"]
        cond_label = f"{n_conds} condition{'s' if n_conds != 1 else ''} stacked"
        chip = (
            f'<span style="font-size:0.75rem;font-weight:600;padding:2px 8px;'
            f'border-radius:4px;background:#FEF3C7;color:{styles.SIGNAL_AMBER};">'
            f'{cond_label}</span>'
        )
        crest_chip = cp.crest_chip(w["crest"]) if w["crest"] else ""
        st.markdown(
            f'<div class="news-item" style="border-color:{styles.SIGNAL_AMBER};">'
            f'<b>{cp._esc(w["ticker"])}</b> {cp._esc(w["name"])[:48]} '
            f'{crest_chip} {chip}</div>',
            unsafe_allow_html=True)
        for reason in w["reasons"]:
            st.markdown(
                f'<div class="muted-note" style="margin:1px 0 0 8px;">'
                f'&bull; {cp._esc(reason)}</div>',
                unsafe_allow_html=True)
        if w.get("ma_note") and not any("MA" in r for r in w["reasons"]):
            st.markdown(
                f'<div class="muted-note" style="margin:1px 0 0 8px;'
                f'font-style:italic;">&bull; {cp._esc(w["ma_note"])}</div>',
                unsafe_allow_html=True)


def _render_opportunity_hints(hints: list, had_prior: bool) -> None:
    if not had_prior:
        st.markdown(
            '<div class="muted-note" style="font-style:italic;">Fair-value '
            'crossing signals require a prior reading (first run). Revisit '
            'after the app has run at least twice.</div>',
            unsafe_allow_html=True)
    if not hints:
        st.markdown(
            '<div class="muted-note">No opportunity conditions detected in the '
            'current universe.</div>', unsafe_allow_html=True)
        return
    type_labels = {
        "fv_crossing": "FV crossing",
        "fundamental_divergence": "Fundamental divergence",
        "late_crest_leader": "Late-crest leader",
        "other": "Signal",
    }
    for h in hints[:15]:
        label = type_labels.get(h["hint_type"], "Signal")
        chip = (
            f'<span style="font-size:0.75rem;font-weight:600;padding:2px 8px;'
            f'border-radius:4px;background:#CCFBF1;color:{styles.SIGNAL_TEAL};">'
            f'{label}</span>'
        )
        crest_chip = cp.crest_chip(h["crest"]) if h["crest"] else ""
        st.markdown(
            f'<div class="news-item" style="border-color:{styles.SIGNAL_TEAL};">'
            f'<b>{cp._esc(h["ticker"])}</b> {cp._esc(h["name"])[:48]} '
            f'{crest_chip} {chip}</div>',
            unsafe_allow_html=True)
        for reason in h["reasons"]:
            st.markdown(
                f'<div class="muted-note" style="margin:1px 0 0 8px;">'
                f'&bull; {cp._esc(reason)}</div>',
                unsafe_allow_html=True)


def view_signals(full: pd.DataFrame, perf_full: pd.DataFrame,
                 scores: pd.DataFrame) -> None:
    st.markdown('<div class="view-title">Signals</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="muted-note" style="margin-bottom:12px;">'
        f'{_DISCLAIMER}</div>',
        unsafe_allow_html=True)

    # ---- Signal 1: crest rotation ----------------------------------------
    st.markdown('<div class="section-title">Rotation signal — Early vs Late-crest'
                '</div>', unsafe_allow_html=True)
    if not (service.has_api_key() or service.has_finnhub()):
        st.info("Set FMP_API_KEY or FINNHUB_API_KEY to enable the rotation signal "
                "(needs crest index history).")
        rot = sig.rotation_inflection(pd.DataFrame(), perf_full)
    else:
        wide_full = _get_crest_wide(full)
        rot = sig.rotation_inflection(wide_full, perf_full)
    _render_signal_1(rot)

    # ---- Signal 2 + 3 (first pass without MA, then enrich top hits) ------
    stretch_no_ma = sig.stretch_warnings(full, perf_full, histories=None)

    # Fetch histories for the top flagged names only (at most 12 API calls, cached).
    histories: dict = {}
    if service.has_api_key() or service.has_finnhub():
        top_tickers = [w["ticker"] for w in stretch_no_ma[:12]]
        for t in top_tickers:
            h = service.get_history_result(t)
            s = h.get("series")
            if s is not None and not s.empty:
                histories[t] = s

    stretch = sig.stretch_warnings(full, perf_full, histories=histories)
    hints, had_prior = sig.opportunity_hints(
        full, perf_full, scores, rot["regime"])

    # Persist current FV readings for next-session crossing detection.
    sig.save_prior_fv(full)

    # ---- Summary strip ---------------------------------------------------
    n_rot_adv = 1 if rot["regime"] in ("inflection_late", "inflection_early") else 0
    st.markdown(
        f'<div class="muted-note" style="margin:10px 0;">'
        f'<b>{n_rot_adv}</b> rotation advisor{"y" if n_rot_adv == 1 else "ies"} &nbsp;·&nbsp; '
        f'<b>{len(stretch)}</b> stretch warning{"" if len(stretch) == 1 else "s"} &nbsp;·&nbsp; '
        f'<b>{len(hints)}</b> opportunity hint{"" if len(hints) == 1 else "s"}'
        f'</div>',
        unsafe_allow_html=True)

    # ---- Two-column layout: warnings | hints -----------------------------
    st.write("")
    col_warn, col_opp = st.columns(2)
    with col_warn:
        st.markdown(
            f'<div class="section-title" style="color:{styles.SIGNAL_AMBER};">'
            f'Stretch warnings</div>',
            unsafe_allow_html=True)
        st.caption(
            "Names at notable extremes — mean-reversion risk elevated. "
            "Not a sell call; review whether your conviction still holds.")
        _render_stretch_warnings(stretch)
    with col_opp:
        st.markdown(
            f'<div class="section-title" style="color:{styles.SIGNAL_TEAL};">'
            f'Opportunity hints</div>',
            unsafe_allow_html=True)
        st.caption(
            "Conditions a value or timing investor might find worth reviewing. "
            "Not a buy recommendation.")
        _render_opportunity_hints(hints, had_prior)


# ---------------------------------------------------------------------------
# View: Portfolios (paper — your own decisions, no execution, no advice)
# ---------------------------------------------------------------------------
_PAPER_LABEL = ("Paper portfolio — your decisions, no execution. These are your "
                "own paper trades recorded for review; nothing here is advice and "
                "no orders are placed.")


def _now_evidence(ticker: str, full: pd.DataFrame, scores: pd.DataFrame) -> dict:
    """Current evidence for a ticker, cheaply (no per-position history fetch).

    Reuses ``thesis.capture_evidence`` on the already-computed frames; passing
    ``perf_row=None`` keeps it to factors/valuation/position — exactly the
    fields the entry-vs-now comparison reads — without a price-history call.
    """
    def _row(df):
        sub = df[df["Ticker"] == ticker]
        return sub.iloc[0] if not sub.empty else None
    return thx.capture_evidence(ticker, _row(full), _row(scores), None,
                                _imported_for_ticker(ticker))


def _active_signals_for(ticker: str, full: pd.DataFrame,
                        perf_full: pd.DataFrame, scores: pd.DataFrame) -> list:
    """Signals active for one ticker right now (reuses core.signals)."""
    out: list = []
    try:
        for w in sig.stretch_warnings(full, perf_full, histories=None):
            if w.get("ticker") == ticker:
                out.append({"kind": "stretch", "reasons": w.get("reasons", []),
                            "conditions": w.get("conditions")})
        hints, _ = sig.opportunity_hints(full, perf_full, scores, "neutral")
        for h in hints:
            if h.get("ticker") == ticker:
                out.append({"kind": h.get("hint_type"),
                            "reasons": h.get("reasons", [])})
    except Exception:
        pass
    return out


def _portfolio_prices(tickers) -> dict:
    """One live quote per unique ticker (cached). Empty when no API key."""
    prices: dict = {}
    if not (service.has_api_key() or service.has_finnhub()):
        return prices
    for t in sorted({t for t in tickers if t}):
        try:
            prices[t] = service.get_live_quote(t).get("price")
        except Exception:
            prices[t] = None
    return prices


def _fmt_delta_val(d: dict) -> str:
    fmt = d.get("fmt")
    if fmt == "pct":
        return cp.fmt_pct_frac(d.get("now"))
    if fmt == "bool":
        return "yes" if d.get("now") else "no"
    return cp.fmt_score(d.get("now"))


def _fmt_delta_entry(d: dict) -> str:
    fmt = d.get("fmt")
    if fmt == "pct":
        return cp.fmt_pct_frac(d.get("entry"))
    if fmt == "bool":
        return "yes" if d.get("entry") else "no"
    return cp.fmt_score(d.get("entry"))


def _render_position_detail(pos: dict, full: pd.DataFrame,
                            scores: pd.DataFrame) -> None:
    """Entry-vs-now thesis-held comparison + frozen evidence for one position."""
    entry_ev = (pos.get("thesis_at_entry") or {}).get("evidence", {})
    now_ev = _now_evidence(pos["ticker"], full, scores)
    held = pfl.thesis_held(entry_ev, now_ev)

    st.markdown(
        f'Thesis-held: {cp.thesis_held_chip(held["status"])}',
        unsafe_allow_html=True)
    rationale = (pos.get("thesis_at_entry") or {}).get("rationale")
    if rationale:
        st.caption(f"Rationale at entry: {cp._esc(rationale)}")

    if held["deltas"]:
        rows = [{"Metric": d["label"], "At entry": _fmt_delta_entry(d),
                 "Now": _fmt_delta_val(d),
                 "Direction": ("improved" if d["dir"] > 0 else
                               "weakened" if d["dir"] < 0 else "flat")}
                for d in held["deltas"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
    else:
        st.caption("No comparable entry-vs-now metrics available.")

    sigs = (pos.get("thesis_at_entry") or {}).get("signals", [])
    if sigs:
        labels = []
        for s in sigs:
            labels.append(f"{s.get('kind')}: " + "; ".join(s.get("reasons", [])))
        st.caption("Signals active at entry — " + " · ".join(labels))


def _render_positions(record: dict, full: pd.DataFrame, scores: pd.DataFrame,
                      prices: dict) -> None:
    positions = record.get("positions", [])
    open_pos = [p for p in positions if p.get("status") == "open"]
    closed_pos = [p for p in positions if p.get("status") == "closed"]

    if not positions:
        st.info("No positions yet — add one below to start this paper portfolio.")
        return

    st.markdown('<div class="section-title">Open positions</div>',
                unsafe_allow_html=True)
    if not open_pos:
        st.caption("No open positions.")
    for pos in open_pos:
        price = prices.get(pos["ticker"])
        pnl = pfl.position_pnl(pos, price)
        ret = pnl["return_pct"]
        ret_txt = cp.fmt_pct_frac(ret) if ret is not None else cp.DASH
        dol = (cp.fmt_price(pnl["dollar_pnl"]) if pnl["dollar_pnl"] is not None
               else cp.DASH)
        title = (f'{pos["ticker"]} · {pos["action"]} {pos["shares"]:g} @ '
                 f'{cp.fmt_price(pos["entry_price"])}  →  '
                 f'{cp.fmt_price(price) if price is not None else "no price"}  '
                 f'({ret_txt}, {dol})')
        with st.expander(title, expanded=False):
            _render_position_detail(pos, full, scores)
            cc = st.columns([2, 1])
            with cc[0]:
                exit_px = st.number_input(
                    "Exit price", min_value=0.0,
                    value=float(price) if price else float(pos["entry_price"]),
                    step=0.01, key=f"exit_px_{pos['id']}")
            with cc[1]:
                st.write("")
                st.write("")
                if st.button("Close position", key=f"close_{pos['id']}",
                             use_container_width=True):
                    pfl.close_position(record["portfolio_id"], pos["id"], exit_px)
                    st.rerun()

    if closed_pos:
        st.markdown('<div class="section-title">Closed positions (realized)</div>',
                    unsafe_allow_html=True)
        rows = []
        for pos in closed_pos:
            pnl = pfl.position_pnl(pos, None)
            rows.append({
                "Ticker": pos["ticker"], "Action": pos["action"],
                "Shares": pos["shares"],
                "Entry": cp.fmt_price(pos["entry_price"]),
                "Exit": cp.fmt_price(pos.get("exit_price")),
                "Realized %": cp.fmt_pct_frac(pnl["return_pct"])
                if pnl["return_pct"] is not None else cp.DASH,
                "Realized $": cp.fmt_price(pnl["dollar_pnl"])
                if pnl["dollar_pnl"] is not None else cp.DASH,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)


def _render_add_position(record: dict, full: pd.DataFrame,
                         perf_full: pd.DataFrame, scores: pd.DataFrame) -> None:
    st.markdown('<div class="section-title">Add a paper position</div>',
                unsafe_allow_html=True)
    options = full["Ticker"].tolist()
    labels = dict(zip(full["Ticker"], full["Name"]))
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        ticker = st.selectbox(
            "Ticker", options, index=None, placeholder="Select a ticker…",
            format_func=lambda t: f"{t} · {labels.get(t, '')}",
            key=f"add_tk_{record['portfolio_id']}")
    with c2:
        action = st.radio("Action", ["BUY", "SELL"], horizontal=True,
                          key=f"add_act_{record['portfolio_id']}")
    with c3:
        shares = st.number_input("Shares", min_value=0.0, value=100.0, step=1.0,
                                 key=f"add_sh_{record['portfolio_id']}")

    # Default entry price to the current live price (editable for backdating).
    default_px = 0.0
    if ticker is not None:
        live = service.get_live_quote(ticker).get("price") \
            if (service.has_api_key() or service.has_finnhub()) else None
        if live is None:
            sub = full[full["Ticker"] == ticker]
            live = float(sub.iloc[0].get("Last Price")) if not sub.empty else None
        default_px = float(live) if live else 0.0

    c4, c5 = st.columns([1, 1])
    with c4:
        entry_px = st.number_input(
            "Entry price (defaults to live; edit to backdate)", min_value=0.0,
            value=default_px, step=0.01, key=f"add_px_{record['portfolio_id']}")
    with c5:
        conviction = st.slider("Conviction", 1, 10, 6,
                               key=f"add_cv_{record['portfolio_id']}")
    stance = st.selectbox("Stance", thx.STANCES, index=0,
                          key=f"add_st_{record['portfolio_id']}")
    rationale = st.text_area("Rationale (optional)", height=68,
                             key=f"add_rt_{record['portfolio_id']}")

    if st.button("Record paper position", type="primary",
                 disabled=(ticker is None or entry_px <= 0),
                 key=f"add_go_{record['portfolio_id']}"):
        # Freeze the evidence snapshot (one history fetch) + active signals.
        evidence = _capture_for(ticker, full, scores)
        signals = _active_signals_for(ticker, full, perf_full, scores)
        pfl.open_position(
            record["portfolio_id"], ticker, action, shares, entry_px,
            thesis_at_entry={"evidence": evidence, "signals": signals},
            rationale=rationale, stance=stance, conviction=conviction)
        st.success(f"Recorded paper {action} {shares:g} {ticker} "
                   f"@ {cp.fmt_price(entry_px)} — evidence frozen at entry.")
        st.rerun()


def _render_versions(record: dict) -> None:
    st.markdown('<div class="section-title">Version history</div>',
                unsafe_allow_html=True)
    st.caption("Save a labeled snapshot of the whole portfolio, then diff any "
               "past version against the current state.")
    vc1, vc2 = st.columns([2, 1])
    with vc1:
        label = st.text_input("Version label", placeholder="e.g. Initial build",
                              key=f"ver_lbl_{record['portfolio_id']}")
    with vc2:
        st.write("")
        st.write("")
        if st.button("Save version", key=f"ver_save_{record['portfolio_id']}",
                     use_container_width=True):
            pfl.save_version(record["portfolio_id"], label)
            st.rerun()

    versions = record.get("versions", [])
    if not versions:
        st.info("No versions saved yet.")
        return

    opts = {f'v{v["version"]} — {v["label"]} ({v["ts"][:10]})': v
            for v in versions}
    pick = st.selectbox("View / diff a version", list(opts.keys()),
                        key=f"ver_pick_{record['portfolio_id']}")
    chosen = opts[pick]
    snap_positions = chosen["snapshot"].get("positions", [])
    diff = pfl.diff_versions(snap_positions, record.get("positions", []))

    st.markdown("**Changes since this version**")
    dc = st.columns(3)
    with dc[0]:
        cp.stat_card("Added since", str(len(diff["added"])))
    with dc[1]:
        cp.stat_card("Closed since", str(len(diff["closed"])))
    with dc[2]:
        cp.stat_card("Resized since", str(len(diff["resized"])))
    if diff["added"]:
        st.caption("Added: " + ", ".join(p["ticker"] for p in diff["added"]))
    if diff["closed"]:
        st.caption("Closed: " + ", ".join(p["ticker"] for p in diff["closed"]))
    if diff["resized"]:
        st.caption("Resized: " + ", ".join(
            f'{r["ticker"]} {r["from"]:g}→{r["to"]:g}' for r in diff["resized"]))

    st.markdown("**Positions in this version**")
    if snap_positions:
        rows = [{"Ticker": p["ticker"], "Action": p["action"],
                 "Shares": p["shares"], "Entry": cp.fmt_price(p["entry_price"]),
                 "Status": p["status"]} for p in snap_positions]
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
    else:
        st.caption("This version had no positions.")


def _render_compare(prices_all: dict) -> None:
    st.markdown('<div class="section-title">Compare portfolios</div>',
                unsafe_allow_html=True)
    idx = pfl.list_portfolios()
    if len(idx) < 2:
        st.info("Create at least two portfolios to compare strategies.")
        return
    name_by_id = {e["portfolio_id"]: e["name"] for e in idx}
    picked = st.multiselect(
        "Select portfolios to compare", list(name_by_id.keys()),
        default=list(name_by_id.keys())[:2],
        format_func=lambda pid: name_by_id.get(pid, pid), key="cmp_pick")
    if len(picked) < 2:
        st.caption("Pick two or more.")
        return
    records = [pfl.read_portfolio(pid) for pid in picked]
    records = [r for r in records if r]
    rows = pfl.compare_portfolios(records, prices_all)
    table = [{
        "Portfolio": r["name"],
        "Total return": cp.fmt_pct_frac(r["total_pct"])
        if r["total_pct"] is not None else cp.DASH,
        "Total P&L": cp.fmt_price(r["total_pnl"]),
        "Win rate": cp.fmt_pct_frac(r["win_rate"], signed=False)
        if r["win_rate"] is not None else cp.DASH,
        "Conviction-wtd return": cp.fmt_pct_frac(r["conviction_weighted_return"])
        if r["conviction_weighted_return"] is not None else cp.DASH,
        "Open": r["open_n"], "Closed": r["closed_n"],
        "Top crest": r["top_crest"] or cp.DASH,
        "Top side": r["top_side"] or cp.DASH,
    } for r in rows]
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    st.caption("Factual side-by-side only — returns and hit rates. Which "
               "strategy fits your goals is your call.")


def view_portfolios(full: pd.DataFrame, perf_full: pd.DataFrame,
                    scores: pd.DataFrame) -> None:
    st.markdown('<div class="view-title">Portfolios</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="muted-note" style="margin-bottom:10px;">'
                f'{_PAPER_LABEL}</div>', unsafe_allow_html=True)

    idx = pfl.list_portfolios()

    # --- Selector + create + delete -------------------------------------
    sel_col, new_col = st.columns([3, 2])
    with sel_col:
        if idx:
            ids = [e["portfolio_id"] for e in idx]
            names = {e["portfolio_id"]: e["name"] for e in idx}
            active_id = st.selectbox(
                "Portfolio", ids, format_func=lambda p: names.get(p, p),
                key="pf_active")
        else:
            active_id = None
            st.info("No portfolios yet — create one to begin.")
    with new_col:
        with st.popover("New portfolio", use_container_width=True):
            nm = st.text_input("Name", key="pf_new_name",
                               placeholder="e.g. Value Tilt")
            note = st.text_area("Strategy note", key="pf_new_note", height=68,
                                placeholder="e.g. Buy below fair value, "
                                            "ignore momentum")
            if st.button("Create", type="primary", key="pf_new_go",
                         disabled=not nm.strip()):
                rec = pfl.create_portfolio(nm, note)
                st.session_state["pf_active"] = rec["portfolio_id"]
                st.rerun()

    if active_id is None:
        # Still offer the compare tab framing (will show its own empty state).
        _render_compare({})
        return

    record = pfl.read_portfolio(active_id)
    if record is None:
        st.warning("Could not load that portfolio.")
        return

    # Prices: one live quote per unique ticker in this portfolio (cached).
    tickers = [p["ticker"] for p in record.get("positions", [])]
    prices = _portfolio_prices(tickers)
    summary = pfl.portfolio_summary(record, prices)

    # --- Header strip (cash + holdings composition) ----------------------
    if record.get("strategy_note"):
        st.caption(cp._esc(record["strategy_note"]))
    h = st.columns(6)
    with h[0]:
        cp.stat_card("Total value", cp.fmt_price(summary["total_value"]),
                     "cash + positions")
    with h[1]:
        cp.stat_card("Total P&L", cp.fmt_price(summary["total_pnl"]),
                     cp.fmt_pct_frac(summary["total_pnl_pct"])
                     if summary["total_pnl_pct"] is not None else "",
                     sign=summary["total_pnl"])
    with h[2]:
        cp.stat_card("Cash remaining", cp.fmt_price(summary["cash_remaining"]),
                     "over-allocated" if summary["over_allocated"] else "")
    with h[3]:
        cp.stat_card("Invested", cp.fmt_price(summary["invested_cost"]),
                     f'{summary["open_n"]} open position'
                     f'{"s" if summary["open_n"] != 1 else ""}')
    with h[4]:
        cp.stat_card("Open", str(summary["open_n"]))
    with h[5]:
        cp.stat_card("Closed", str(summary["closed_n"]),
                     f'win rate {cp.fmt_pct_frac(summary["win_rate"], signed=False)}'
                     if summary["win_rate"] is not None else "")

    if not prices and tickers:
        st.caption("Live prices unavailable (no API key) — open positions show "
                   "at cost, so P&L is $0 until a price source is configured. "
                   "Closed positions settle on their recorded exit price.")

    tab_pos, tab_ver, tab_cmp = st.tabs(
        ["Positions", "Versions", "Compare"])
    with tab_pos:
        _render_positions(record, full, scores, prices)
        st.divider()
        _render_add_position(record, full, perf_full, scores)
        st.divider()
        with st.popover("Delete this portfolio"):
            st.caption("This permanently removes the portfolio and its history.")
            if st.button("Confirm delete", key=f"del_{active_id}"):
                pfl.delete_portfolio(active_id)
                st.session_state.pop("pf_active", None)
                st.rerun()
    with tab_ver:
        _render_versions(record)
    with tab_cmp:
        _render_compare(prices)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
NAV_ITEMS = ["Performance", "Fundamentals & Factors", "Screener", "Buckets",
             "Capex Cycle", "Signals", "Portfolios", "News & Filings",
             "Stock Detail", "Thesis"]


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

    # Universe-wide views need the full performance sweep (226 tickers).
    # Single-ticker views (Stock Detail, Thesis, News & Filings) skip it and
    # call service.get_history_result for only the one selected ticker.
    UNIVERSE_VIEWS = {"Performance", "Fundamentals & Factors", "Screener",
                      "Buckets", "Capex Cycle", "Signals", "Portfolios"}

    full = full.copy()
    if selected in UNIVERSE_VIEWS:
        # Memoized in session_state (Issue 2): near-instant tab flips; rebuilds
        # only on manual refresh or TTL expiry. Momentum score is already on it.
        perf_full = get_perf_full(full)

        # ONE authoritative last price flows from perf_full into `full` so the
        # displayed price and upside-to-FV are live-quote-consistent.
        auth_price = perf_full.set_index("Ticker")["Last Price"]
        full["Last Price"] = full["Ticker"].map(auth_price).fillna(full["Last Price"])
        full = tm.add_timing_columns(full)

        merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                            on="Ticker", how="left")
        scores = fc.compute_factor_scores(merged)
        meta_src = merged
    else:
        # Single-ticker views: static Morningstar prices; Momentum factor = N/A.
        perf_full = pd.DataFrame()
        full = tm.add_timing_columns(full)
        scores = fc.compute_factor_scores(full)
        meta_src = full

    # Attach metadata so the scores frame carries Ticker/Name/Bucket/Sector/Side/Crest.
    for i, col in enumerate(["Ticker", "Name", "Primary Bucket",
                              "Sector", "Side", "Crest"]):
        scores.insert(i, col, meta_src[col].values)

    # Render only the active view (option_menu, unlike st.tabs, renders one).
    if selected == "Performance":
        view_performance(full, perf_full)
    elif selected == "Fundamentals & Factors":
        view_fundamentals(full, scores)
    elif selected == "Screener":
        view_screener(full, scores)
    elif selected == "Buckets":
        view_buckets(full, perf_full, scores)
    elif selected == "Capex Cycle":
        view_capex_cycle(full, perf_full, scores)
    elif selected == "Signals":
        view_signals(full, perf_full, scores)
    elif selected == "Portfolios":
        view_portfolios(full, perf_full, scores)
    elif selected == "News & Filings":
        view_news(full)
    elif selected == "Stock Detail":
        view_detail(full, scores)
    elif selected == "Thesis":
        view_thesis(full, scores)


if __name__ == "__main__":
    main()
