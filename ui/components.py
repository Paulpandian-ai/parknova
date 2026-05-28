"""Reusable UI building blocks: formatters, styled tables, cards and charts."""

from __future__ import annotations

from typing import List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import styles

# ---------------------------------------------------------------------------
# Formatters  (all return the em-dash placeholder for missing data)
# ---------------------------------------------------------------------------
DASH = "—"


def _is_missing(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x))


def fmt_pct(frac, signed: bool = True) -> str:
    """Fraction -> '+12.3%' / '(8.1%)'. ``signed`` adds the leading + on gains."""
    if _is_missing(frac):
        return DASH
    pct = float(frac) * 100.0
    if pct < 0:
        return f"({abs(pct):.1f}%)"
    return f"+{pct:.1f}%" if signed else f"{pct:.1f}%"


def fmt_mult(x) -> str:
    """24.5x, negatives in parentheses."""
    if _is_missing(x):
        return DASH
    x = float(x)
    if x < 0:
        return f"({abs(x):.1f}x)"
    return f"{x:.1f}x"


def fmt_beta(x) -> str:
    if _is_missing(x):
        return DASH
    return f"{float(x):.2f}"


def fmt_money(x) -> str:
    """Plain price, e.g. $123.45."""
    if _is_missing(x):
        return DASH
    return f"${float(x):,.2f}"


def fmt_marketcap(x) -> str:
    """$1.23T / $456.7B / $89.0M."""
    if _is_missing(x):
        return DASH
    x = float(x)
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:,.0f}"


# ---------------------------------------------------------------------------
# Conditional colour for return cells (green up / red down, scaled to magnitude)
# ---------------------------------------------------------------------------
def _return_bg(frac, full_scale: float = 0.5) -> str:
    """Return a CSS background-color string for a fractional return."""
    if _is_missing(frac):
        return ""
    frac = float(frac)
    alpha = min(abs(frac) / full_scale, 1.0) * 0.55
    if frac >= 0:
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color: {styles.TEXT};"
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color: {styles.TEXT};"


# ---------------------------------------------------------------------------
# Stat / metric cards
# ---------------------------------------------------------------------------
def stat_card(label: str, value: str, sub: str = "", sign: Optional[float] = None):
    cls = ""
    if sign is not None and not _is_missing(sign):
        cls = "pos" if float(sign) >= 0 else "neg"
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    st.markdown(
        f"""<div class="metric-card">
            <div class="label">{label}</div>
            <div class="value {cls}">{value}</div>
            {sub_html}
        </div>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Performance table (Styler with conditional colour)
# ---------------------------------------------------------------------------
def performance_table(perf_df: pd.DataFrame, return_cols: List[str]):
    """Render the colour-coded, sortable performance table."""
    cols = ["ticker", "company", "bucket", "price"] + return_cols
    view = perf_df[cols].rename(
        columns={
            "ticker": "Ticker",
            "company": "Company",
            "bucket": "Bucket",
            "price": "Price",
        }
    )
    fmt = {"Price": fmt_money}
    for c in return_cols:
        fmt[c] = lambda v: fmt_pct(v)

    styler = (
        view.style.format(fmt, na_rep=DASH)
        .map(_return_bg, subset=return_cols)
        .set_properties(**{"font-size": "0.9rem"})
    )
    st.dataframe(styler, use_container_width=True, hide_index=True, height=560)


def heatmap_table(matrix: pd.DataFrame, return_cols: List[str]):
    """Bucket x window median-return heatmap as a Plotly matrix."""
    if matrix.empty:
        st.info("No data available for the heatmap.")
        return
    z = matrix[return_cols].astype(float)
    text = z.map(lambda v: fmt_pct(v))
    fig = go.Figure(
        data=go.Heatmap(
            z=z.values * 100,
            x=return_cols,
            y=list(z.index),
            text=text.values,
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            showscale=True,
            colorbar=dict(title="%"),
            hovertemplate="%{y} · %{x}: %{text}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=max(280, 36 * len(z.index) + 120),
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Fundamentals table
# ---------------------------------------------------------------------------
def fundamentals_table(fund_df: pd.DataFrame, col_map: dict, multiple_cols, percent_cols):
    keys = ["ticker", "company", "bucket"] + list(col_map.keys())
    view = fund_df[keys].rename(
        columns={"ticker": "Ticker", "company": "Company", "bucket": "Bucket", **col_map}
    )
    fmt = {}
    for key, label in col_map.items():
        if key == "market_cap":
            fmt[label] = fmt_marketcap
        elif key == "price":
            fmt[label] = fmt_money
        elif key == "beta":
            fmt[label] = fmt_beta
        elif key in percent_cols:
            fmt[label] = lambda v: fmt_pct(v, signed=False)
        elif key in multiple_cols:
            fmt[label] = fmt_mult
    styler = view.style.format(fmt, na_rep=DASH).set_properties(
        **{"font-size": "0.9rem"}
    )
    st.dataframe(styler, use_container_width=True, hide_index=True, height=560)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def price_chart(series: pd.Series, title: str = ""):
    if series is None or series.empty:
        st.info("No price history available.")
        return
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=series.index,
            y=series.values,
            mode="lines",
            line=dict(color=styles.PRIMARY, width=2),
            fill="tozeroy",
            fillcolor="rgba(37,99,235,0.08)",
            hovertemplate="%{x|%b %d, %Y}<br>$%{y:.2f}<extra></extra>",
            name="Price",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
        title=title,
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor=styles.BORDER, tickprefix="$"),
        hovermode="x unified",
    )
    # Tighten y-range to the data so the area fill doesn't dwarf the line.
    lo, hi = float(series.min()), float(series.max())
    pad = (hi - lo) * 0.08 or hi * 0.05
    fig.update_yaxes(range=[max(0, lo - pad), hi + pad])
    st.plotly_chart(fig, use_container_width=True)


def revenue_income_chart(income: List[dict]):
    """5-year revenue + net income grouped bar chart (oldest -> newest)."""
    if not income:
        st.info("No income-statement data available.")
        return
    rows = list(reversed(income))  # FMP returns newest first
    years, rev, net = [], [], []
    for r in rows:
        years.append(str(r.get("calendarYear") or r.get("date", ""))[:4])
        rev.append(r.get("revenue"))
        net.append(r.get("netIncome"))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=rev, name="Revenue", marker_color=styles.PRIMARY))
    fig.add_trace(go.Bar(x=years, y=net, name="Net Income", marker_color=styles.NAVY))
    fig.update_layout(
        template="plotly_white",
        height=320,
        barmode="group",
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(gridcolor=styles.BORDER, tickprefix="$"),
        xaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig, use_container_width=True)
