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


def bucket_chip(bucket) -> str:
    if _missing(bucket):
        return DASH
    color = styles.BUCKET_COLORS.get(str(bucket), styles.MUTED)
    return f'<span class="chip" style="background:{color}">{bucket}</span>'


def crest_chip(crest) -> str:
    if _missing(crest):
        return ""
    color = styles.CREST_COLORS.get(str(crest), styles.MUTED)
    return (f'<span class="chip" style="background:{color}">{crest}-crest</span>')


def side_chip(side) -> str:
    if _missing(side) or str(side) == "Unknown":
        return ""
    color = styles.SIDE_COLORS.get(str(side), styles.MUTED)
    return f'<span class="chip" style="background:{color}">{side}</span>'


def value_trap_chip(reason: str = "") -> str:
    """Amber-outline 'Value-trap watch' chip with a warning tooltip."""
    tip = (reason or "Low multiple on an extended early-crest cyclical.").replace(
        '"', "&quot;")
    return (f'<span class="chip" title="{tip}" style="background:#FEF3C7;'
            f'color:{styles.TRAP_AMBER};border:1px solid {styles.TRAP_AMBER};">'
            f'Value-trap watch</span>')


# Form-group -> (color, label) for SEC filing badges.
_FORM_BADGE = {
    "financials": ("#2563EB", "Financials"),
    "material": ("#CA8A04", "Material 8-K"),
    "offering": ("#DC2626", "Offering"),
    "stake": ("#7C3AED", ">5% Stake"),
    "insider": ("#475569", "Insider"),
}


def form_badge(group: Optional[str]) -> str:
    if not group or group not in _FORM_BADGE:
        return ""
    color, label = _FORM_BADGE[group]
    return f'<span class="chip" style="background:{color};margin-left:6px;">{label}</span>'


def leading_badge() -> str:
    return (f'<span class="chip" style="background:{styles.NAVY};'
            f'margin-left:6px;">Leading</span>')


# ---------------------------------------------------------------------------
# Conditional colour for return / score cells
# ---------------------------------------------------------------------------
def return_bg(frac, full_scale: float = 0.5) -> str:
    if _missing(frac):
        return ""
    frac = float(frac)
    alpha = min(abs(frac) / full_scale, 1.0) * 0.35
    if frac >= 0:
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color:{styles.TEXT};"


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def bucket_cell_bg(bucket) -> str:
    """Tint a Bucket cell with its palette colour (chip-like, for st.dataframe)."""
    if _missing(bucket):
        return ""
    color = styles.BUCKET_COLORS.get(str(bucket))
    if not color:
        return ""
    r, g, b = _hex_to_rgb(color)
    return (f"background-color: rgba({r},{g},{b},0.16); "
            f"color:{color}; font-weight:600;")


def _palette_cell_bg(value, palette: dict) -> str:
    if _missing(value):
        return ""
    color = palette.get(str(value))
    if not color:
        return ""
    r, g, b = _hex_to_rgb(color)
    return (f"background-color: rgba({r},{g},{b},0.16); "
            f"color:{color}; font-weight:600;")


def crest_cell_bg(crest) -> str:
    return _palette_cell_bg(crest, styles.CREST_COLORS)


def side_cell_bg(side) -> str:
    return _palette_cell_bg(side, styles.SIDE_COLORS)


def zscore_bg(val, scale: float = 1.5) -> str:
    """Centered z-like value -> green (high) / red (low) gradient around 0."""
    if _missing(val):
        return ""
    v = float(val)
    alpha = min(abs(v) / scale, 1.0) * 0.35
    if v >= 0:
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    return f"background-color: rgba(220,38,38,{alpha:.3f}); color:{styles.TEXT};"


def score_bg(val) -> str:
    """0..100 percentile -> green (high) / red (low) gradient."""
    if _missing(val):
        return ""
    v = max(0.0, min(100.0, float(val))) / 100.0
    if v >= 0.5:
        alpha = (v - 0.5) * 2 * 0.35
        return f"background-color: rgba(22,163,74,{alpha:.3f}); color:{styles.TEXT};"
    alpha = (0.5 - v) * 2 * 0.35
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


def leaderboard_bar(labels: List[str], values: List[float], title: str = "",
                    use_bucket_colors: bool = True):
    """Horizontal bar chart of a return per group, sorted, colored by sign.

    Values are fractions; bars use the bucket palette when available, else the
    green/red sign colors.
    """
    pairs = [(l, v) for l, v in zip(labels, values) if v is not None and not (
        isinstance(v, float) and pd.isna(v))]
    if not pairs:
        st.info("No data for the leaderboard.")
        return
    pairs.sort(key=lambda p: p[1])  # ascending -> largest at top after reversed axis
    labs = [p[0] for p in pairs]
    vals = [p[1] * 100 for p in pairs]
    if use_bucket_colors and all(l in styles.BUCKET_COLORS for l in labs):
        colors = [styles.BUCKET_COLORS[l] for l in labs]
    else:
        colors = [styles.POSITIVE if v >= 0 else styles.NEGATIVE for v in vals]
    fig = go.Figure(go.Bar(
        x=vals, y=labs, orientation="h", marker_color=colors,
        text=[fmt_pct_frac(v / 100) for v in vals], textposition="auto",
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white", height=max(260, 34 * len(labs) + 100), title=title,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(ticksuffix="%", gridcolor=styles.BORDER, zerolinecolor=styles.MUTED),
        yaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig, width="stretch")


def grouped_factor_bars(names: List[str], group_vals: List[float],
                        universe_vals: List[float], group_label: str = "Bucket"):
    """Grouped bars comparing a group's median factor scores vs universe median."""
    fig = go.Figure()
    fig.add_trace(go.Bar(name=group_label, x=names, y=group_vals,
                         marker_color=styles.PRIMARY))
    fig.add_trace(go.Bar(name="Universe median", x=names, y=universe_vals,
                         marker_color=styles.MUTED))
    fig.update_layout(
        template="plotly_white", height=340, barmode="group",
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(range=[0, 100], gridcolor=styles.BORDER, title="Percentile"),
        xaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
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


def news_feed(items: List[dict], limit: int = 10, leading_only: bool = False):
    from core.synthesis import is_leading_source

    if not items:
        st.info("No recent news available.")
        return
    shown = 0
    for it in items:
        site = it.get("site") or it.get("publisher") or ""
        leading = is_leading_source(site)
        if leading_only and not leading:
            continue
        title = it.get("title") or "(untitled)"
        url = it.get("url") or "#"
        when = _relative_time(it.get("publishedDate", ""))
        sentiment = it.get("sentiment")
        chip = ""
        if sentiment:
            c = _SENTIMENT_COLORS.get(str(sentiment).lower(), styles.MUTED)
            chip = f'<span class="chip" style="background:{c};margin-left:8px;">{sentiment}</span>'
        badge = leading_badge() if leading else ""
        st.markdown(
            f'<div class="news-item"><a href="{url}" target="_blank">{title}</a>'
            f'{chip}{badge}'
            f'<div class="news-meta">{site} · {when}</div></div>',
            unsafe_allow_html=True,
        )
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        st.info("No items from leading sources in this batch.")


def filings_feed(filings: List[dict], limit: int = 20):
    """Render SEC filings as labeled rows with form badges."""
    from data.edgar_client import classify_form

    if not filings:
        st.info("No SEC filings available (CIK not found or none on file).")
        return
    for f in filings[:limit]:
        form = f.get("form", "?")
        date = f.get("filingDate", "")
        url = f.get("url", "#")
        badge = form_badge(classify_form(form))
        st.markdown(
            f'<div class="news-item">'
            f'<a href="{url}" target="_blank">{form}</a>{badge}'
            f'<div class="news-meta">Filed {date}</div></div>',
            unsafe_allow_html=True,
        )


def filing_row_header(form: str, date: str, url: str, cached: bool = False) -> None:
    """Compact header line for one filing in the analysis panel."""
    from data.edgar_client import classify_form
    badge = form_badge(classify_form(form))
    cached_tag = (f'<span class="chip" style="background:{styles.MUTED};'
                  f'margin-left:6px;">cached</span>' if cached else "")
    st.markdown(
        f'<div style="margin:2px 0 -6px;">'
        f'<a href="{url}" target="_blank" style="color:{styles.NAVY};'
        f'font-weight:600;text-decoration:none;">{form}</a>{badge}{cached_tag}'
        f'<span class="news-meta" style="margin-left:8px;">Filed {date}</span>'
        f'</div>', unsafe_allow_html=True)


def filing_analysis_result(result: dict) -> None:
    """Render a free-text filing-analysis result card (paid-API path)."""
    if not result:
        return
    if result.get("error"):
        st.warning(result["error"])
        return
    text = result.get("text")
    if not text:
        st.warning("Analysis unavailable (the model returned no text).")
        return
    meta_bits = []
    model = result.get("model")
    if model:
        meta_bits.append(model)
    if result.get("cached"):
        meta_bits.append("cached")
    if result.get("method") in ("sections", "truncated"):
        meta_bits.append(f"input: {result['method']}")
    if result.get("truncated"):
        meta_bits.append("truncated")
    usage = result.get("usage") or {}
    if usage.get("input_tokens") is not None:
        meta_bits.append(f"{usage.get('input_tokens')}→"
                         f"{usage.get('output_tokens')} tok")
    meta = " · ".join(str(m) for m in meta_bits)
    safe = _esc(text).replace("\n", "<br>")
    st.markdown(
        f'<div class="news-item" style="border-color:{styles.PRIMARY};">'
        f'<div class="news-meta" style="margin-bottom:6px;">{meta}</div>'
        f'<div style="font-size:0.92rem;color:{styles.TEXT};">{safe}</div></div>',
        unsafe_allow_html=True)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


_SENTIMENT_CHIP = {"positive": styles.POSITIVE, "negative": styles.NEGATIVE,
                   "neutral": styles.MUTED}


def imported_analysis_result(obj: dict) -> None:
    """Render a structured analysis imported from the sec-filing-analyzer skill.

    Shows: an 'Imported · analyzed in Claude' tag, what_and_why, material_facts,
    key_figures (small table), guidance_changes, risks_or_flags, a sentiment
    chip, and net_read. No API call ever.
    """
    a = (obj or {}).get("analysis") or {}
    parts = []

    by = obj.get("analyzed_by") or "Claude (Max plan) via sec-filing-analyzer skill"
    when = obj.get("analyzed_at") or ""
    trunc = " · truncated source" if obj.get("truncated") else ""
    tag = (f'<span class="chip" style="background:{styles.POSITIVE};">'
           f'Imported · analyzed in Claude</span>')
    meta = _esc(f"{by}{(' · ' + when) if when else ''}{trunc}")
    parts.append(f'<div style="margin-bottom:6px;">{tag}'
                 f'<span class="news-meta" style="margin-left:8px;">{meta}'
                 f'</span></div>')

    sentiment = str(a.get("sentiment") or "").lower()
    if sentiment in _SENTIMENT_CHIP:
        parts.append(f'<span class="chip" style="background:'
                     f'{_SENTIMENT_CHIP[sentiment]};margin-bottom:8px;">'
                     f'{_esc(a.get("sentiment"))}</span>')

    def _section(title, body_html):
        return (f'<div style="margin-top:8px;"><b style="color:{styles.NAVY};">'
                f'{title}</b><div style="font-size:0.92rem;color:{styles.TEXT};'
                f'margin-top:2px;">{body_html}</div></div>')

    if a.get("what_and_why"):
        parts.append(_section("What &amp; why", _esc(a["what_and_why"])))

    facts = a.get("material_facts") or []
    if isinstance(facts, list) and facts:
        items = "".join(f"<li>{_esc(x)}</li>" for x in facts)
        parts.append(_section("Material facts", f"<ul style='margin:4px 0 0 "
                                                 f"18px;'>{items}</ul>"))

    figs = a.get("key_figures") or []
    if isinstance(figs, list) and figs:
        rows = ""
        for kf in figs:
            if not isinstance(kf, dict):
                continue
            rows += (f"<tr><td style='padding:2px 10px 2px 0;color:{styles.MUTED};'>"
                     f"{_esc(kf.get('label',''))}</td>"
                     f"<td style='padding:2px 10px 2px 0;font-weight:600;'>"
                     f"{_esc(kf.get('value',''))}</td>"
                     f"<td style='padding:2px 0;color:{styles.MUTED};'>"
                     f"{_esc(kf.get('context',''))}</td></tr>")
        if rows:
            parts.append(_section("Key figures",
                                  f"<table style='border-collapse:collapse;'>"
                                  f"{rows}</table>"))

    guidance = a.get("guidance_changes")
    if guidance:
        parts.append(_section("Guidance changes", _esc(guidance)))

    risks = a.get("risks_or_flags") or []
    if isinstance(risks, list) and risks:
        items = "".join(f"<li>{_esc(x)}</li>" for x in risks)
        parts.append(_section("Risks / flags", f"<ul style='margin:4px 0 0 "
                                                f"18px;'>{items}</ul>"))

    if a.get("net_read"):
        parts.append(_section("Net read", f"<i>{_esc(a['net_read'])}</i>"))

    st.markdown(
        f'<div class="news-item" style="border-color:{styles.POSITIVE};">'
        + "".join(parts) + "</div>", unsafe_allow_html=True)


def holders_table(holders: List[dict], top: int = 15):
    if not holders:
        st.info("Institutional ownership not available (endpoint may not be on "
                "your FMP plan).")
        return
    rows = []
    for h in holders[:top]:
        rows.append({
            "Holder": h.get("holder") or h.get("investorName") or "?",
            "Shares": h.get("shares"),
            "Change": h.get("change"),
            "Date": h.get("dateReported") or h.get("date") or "",
        })
    df = pd.DataFrame(rows)

    def _chg(v):
        if _missing(v):
            return DASH
        v = float(v)
        return f"+{v:,.0f}" if v >= 0 else f"({abs(v):,.0f})"

    def _shares(v):
        return DASH if _missing(v) else f"{float(v):,.0f}"

    styler = (df.style.format({"Shares": _shares, "Change": _chg}, na_rep=DASH)
              .map(lambda v: return_bg(v, full_scale=5e6) if not _missing(v) else "",
                   subset=["Change"])
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=420)


def insider_table(trades: List[dict], limit: int = 25):
    if not trades:
        st.info("Insider transactions not available (endpoint may not be on your "
                "FMP plan).")
        return
    rows = []
    for t in trades[:limit]:
        shares = t.get("securitiesTransacted")
        price = t.get("price")
        try:
            value = abs(float(shares) * float(price)) if shares and price else None
        except (TypeError, ValueError):
            value = None
        ad = str(t.get("acquisitionOrDisposition") or "").upper()
        side = "Buy" if ad == "A" else ("Sell" if ad == "D" else
                                        t.get("transactionType", ""))
        rows.append({
            "Date": t.get("transactionDate") or t.get("filingDate") or "",
            "Insider": t.get("reportingName") or t.get("name") or "?",
            "Role": t.get("typeOfOwner") or t.get("relationship") or "",
            "Side": side,
            "Value": value,
        })
    df = pd.DataFrame(rows)

    def _v(x):
        return DASH if _missing(x) else fmt_money_mag(x)

    def _side_bg(s):
        if s == "Buy":
            return f"background-color: rgba(22,163,74,0.16); color:{styles.POSITIVE}; font-weight:600;"
        if s == "Sell":
            return f"background-color: rgba(220,38,38,0.16); color:{styles.NEGATIVE}; font-weight:600;"
        return ""

    styler = (df.style.format({"Value": _v}, na_rep=DASH)
              .map(_side_bg, subset=["Side"])
              .set_properties(**{"font-size": "0.86rem"}))
    st.dataframe(styler, width="stretch", hide_index=True, height=420)
