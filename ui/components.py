"""Reusable UI building blocks: formatters, cards, chips, tables, charts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import styles

DASH = "—"


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x))


def fmt_pct_frac(frac, signed: bool = True) -> str:
    """Fraction -> '+12.3%' / '(8.1%)'."""
    if _missing(frac):
        return DASH
    pct = float(frac) * 100.0
    if pct < 0:
        return f"({abs(pct):.1f}%)"
    return f"+{pct:.1f}%" if signed else f"{pct:.1f}%"


def fmt_pct_unit(pct, signed: bool = False) -> str:
    """Already-percent value -> '34.2%' / '(8.1%)' (Morningstar columns)."""
    if _missing(pct):
        return DASH
    pct = float(pct)
    if pct < 0:
        return f"({abs(pct):.1f}%)"
    return f"+{pct:.1f}%" if signed else f"{pct:.1f}%"


def fmt_mult(x) -> str:
    if _missing(x):
        return DASH
    x = float(x)
    return f"({abs(x):.1f}x)" if x < 0 else f"{x:.1f}x"


def fmt_ratio(x) -> str:
    if _missing(x):
        return DASH
    return f"{float(x):.2f}"


def fmt_price(x) -> str:
    if _missing(x):
        return DASH
    return f"${float(x):,.2f}"


def fmt_money_mag(x) -> str:
    """$1.23T / $456.7B / $89.0M."""
    if _missing(x):
        return DASH
    x = float(x)
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e12:
        return f"{sign}${a/1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.1f}M"
    return f"{sign}${a:,.0f}"


def fmt_stars(n) -> str:
    if _missing(n):
        return DASH
    n = int(round(float(n)))
    return "★" * n + "☆" * (5 - n)


def fmt_score(x) -> str:
    if _missing(x):
        return DASH
    return f"{float(x):.0f}"


# ---------------------------------------------------------------------------
# Chips / badges (HTML)
# ---------------------------------------------------------------------------
def moat_chip(moat) -> str:
    if _missing(moat):
        return DASH
    color = styles.MOAT_COLORS.get(str(moat), styles.MUTED)
    return f'<span class="chip" style="background:{color}">{moat}</span>'


def grade_badge(grade) -> str:
    if _missing(grade):
        return DASH
    g = str(grade).upper()
    color = styles.GRADE_COLORS.get(g, styles.MUTED)
    return f'<span class="badge-grade" style="background:{color}">{g}</span>'


# ---------------------------------------------------------------------------
# Conditional colour for return / score cells
# ---------------------------------------------------------------------------
def return_bg(frac, full_scale: float = 0.5) -> str:
    if _missing(frac):
        return ""
    frac = float(frac)
    alpha = min(abs(frac) / full_scale, 1.0) * 0.55
    if frac >= 0:
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color:{styles.TEXT};"


def zscore_bg(val, scale: float = 1.5) -> str:
    """Centered z-like value -> green (high) / red (low) gradient around 0."""
    if _missing(val):
        return ""
    v = float(val)
    alpha = min(abs(v) / scale, 1.0) * 0.55
    if v >= 0:
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color:{styles.TEXT};"


def score_bg(val) -> str:
    """0..100 percentile -> green (high) / red (low) gradient."""
    if _missing(val):
        return ""
    v = max(0.0, min(100.0, float(val))) / 100.0
    if v >= 0.5:
        alpha = (v - 0.5) * 2 * 0.55
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    alpha = (0.5 - v) * 2 * 0.55
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color:{styles.TEXT};"


# ---------------------------------------------------------------------------
# Stat card
# ---------------------------------------------------------------------------
def stat_card(label: str, value: str, sub: str = "", sign: Optional[float] = None):
    cls = ""
    if sign is not None and not _missing(sign):
        cls = "pos" if float(sign) >= 0 else "neg"
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="metric-card"><div class="label">{label}</div>'
        f'<div class="value {cls}">{value}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def price_chart(series: pd.Series, title: str = ""):
    if series is None or series.empty:
        st.info("No price history available.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=series.index, y=series.values, mode="lines",
        line=dict(color=styles.PRIMARY, width=2),
        fill="tozeroy", fillcolor="rgba(37,99,235,0.08)",
        hovertemplate="%{x|%b %d, %Y}<br>$%{y:.2f}<extra></extra>", name="Price",
    ))
    lo, hi = float(series.min()), float(series.max())
    pad = (hi - lo) * 0.08 or hi * 0.05
    fig.update_layout(
        template="plotly_white", height=380, title=title,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor=styles.BORDER, tickprefix="$",
                   range=[max(0, lo - pad), hi + pad]),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


def factor_radar(scores: dict, median: Optional[dict] = None):
    """Radar of one stock's factor percentiles vs the universe median."""
    names = list(scores.keys())
    vals = [scores[n] if not _missing(scores[n]) else 0 for n in names]
    fig = go.Figure()
    if median:
        fig.add_trace(go.Scatterpolar(
            r=[median.get(n, 50) for n in names] + [median.get(names[0], 50)],
            theta=names + [names[0]], fill="toself", name="Universe median",
            line=dict(color=styles.MUTED), fillcolor="rgba(100,116,139,0.12)",
        ))
    fig.add_trace(go.Scatterpolar(
        r=vals + [vals[0]], theta=names + [names[0]], fill="toself",
        name="This stock", line=dict(color=styles.PRIMARY),
        fillcolor="rgba(37,99,235,0.18)",
    ))
    fig.update_layout(
        template="plotly_white", height=360, margin=dict(l=30, r=30, t=30, b=30),
        polar=dict(radialaxis=dict(range=[0, 100], showline=False,
                                   gridcolor=styles.BORDER)),
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
    )
    st.plotly_chart(fig, width="stretch")


def heatmap(matrix: pd.DataFrame, cols: List[str], pct_fraction: bool = True):
    if matrix is None or matrix.empty:
        st.info("No data for the heatmap.")
        return
    z = matrix[cols].astype(float)
    text = z.map(lambda v: fmt_pct_frac(v) if pct_fraction else fmt_pct_unit(v))
    fig = go.Figure(data=go.Heatmap(
        z=z.values * (100 if pct_fraction else 1), x=cols, y=list(z.index),
        text=text.values, texttemplate="%{text}", colorscale="RdYlGn", zmid=0,
        colorbar=dict(title="%"),
        hovertemplate="%{y} · %{x}: %{text}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white", height=max(280, 34 * len(z.index) + 120),
        margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# News feed
# ---------------------------------------------------------------------------
def _relative_time(dt_str: str) -> str:
    if not dt_str:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    else:
        return dt_str
    delta = datetime.now(timezone.utc) - dt
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs//60)}m ago"
    if secs < 86400:
        return f"{int(secs//3600)}h ago"
    return f"{int(secs//86400)}d ago"


_SENTIMENT_COLORS = {"positive": styles.POSITIVE, "negative": styles.NEGATIVE,
                     "neutral": styles.MUTED}


def news_feed(items: List[dict], limit: int = 10):
    if not items:
        st.info("No recent news available.")
        return
    for it in items[:limit]:
        title = it.get("title") or "(untitled)"
        url = it.get("url") or "#"
        site = it.get("site") or it.get("publisher") or ""
        when = _relative_time(it.get("publishedDate", ""))
        sentiment = it.get("sentiment")
        chip = ""
        if sentiment:
            c = _SENTIMENT_COLORS.get(str(sentiment).lower(), styles.MUTED)
            chip = f'<span class="chip" style="background:{c};margin-left:8px;">{sentiment}</span>'
        st.markdown(
            f'<div class="news-item"><a href="{url}" target="_blank">{title}</a>{chip}'
            f'<div class="news-meta">{site} · {when}</div></div>',
            unsafe_allow_html=True,
        )
