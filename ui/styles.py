"""Theme constants + global CSS injection for the white/blue ParkNova UI."""

from __future__ import annotations

import streamlit as st

# --- Palette ---------------------------------------------------------------
PRIMARY = "#2563EB"
NAVY = "#1E3A8A"
TEXT = "#0F172A"
MUTED = "#64748B"
SURFACE = "#F8FAFC"
SURFACE_2 = "#F1F5F9"
BORDER = "#E2E8F0"
POSITIVE = "#16A34A"
NEGATIVE = "#DC2626"

MOAT_COLORS = {"Wide": "#1E3A8A", "Narrow": "#2563EB", "None": "#94A3B8"}
# Grade A..F on green -> red.
GRADE_COLORS = {
    "A": "#16A34A", "B": "#65A30D", "C": "#CA8A04",
    "D": "#EA580C", "F": "#DC2626",
}

# Primary Bucket palette (Feature A). Keyed by the full bucket label.
BUCKET_COLORS = {
    "1 Compute Semi": "#2563EB",
    "2 Memory": "#7C3AED",
    "3 Foundry/Semicap": "#0891B2",
    "4 Networking": "#0D9488",
    "5 Power/Cooling": "#CA8A04",
    "6 AI Software": "#DC2626",
    "7 Hyperscaler": "#1E3A8A",
    "R Robotics/Autonomy": "#475569",
    "X Edge AI/Vision": "#9333EA",
    "Q Quantum": "#EA580C",
    "Unclassified": "#94A3B8",
}

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: {TEXT};
}}
.stApp {{ background-color: #FFFFFF; }}

#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}

.block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px; }}

/* Header bar */
.app-header {{
    background: linear-gradient(90deg, {NAVY} 0%, {PRIMARY} 100%);
    color: #FFFFFF; padding: 18px 26px; border-radius: 14px;
    margin-bottom: 16px; box-shadow: 0 6px 20px rgba(30,58,138,0.18);
}}
.app-header h1 {{ font-size: 1.55rem; font-weight: 700; margin: 0; letter-spacing: -0.01em; color:#fff; }}
.app-header p {{ margin: 3px 0 0; font-size: 0.9rem; opacity: 0.88; color: #E2E8F0; }}

/* Stat cards */
.metric-card {{
    background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 14px 16px; box-shadow: 0 1px 3px rgba(15,23,42,0.06); height: 100%;
}}
.metric-card .label {{ font-size: 0.74rem; color: {MUTED}; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em; }}
.metric-card .value {{ font-size: 1.3rem; font-weight: 700; color: {TEXT}; margin-top: 3px; }}
.metric-card .sub {{ font-size: 0.8rem; color: {MUTED}; margin-top: 1px; }}
.value.pos {{ color: {POSITIVE}; }}
.value.neg {{ color: {NEGATIVE}; }}

/* Chips & badges */
.chip {{ display:inline-block; padding:2px 10px; border-radius:999px;
    font-size:0.76rem; font-weight:600; color:#fff; }}
.badge-grade {{ display:inline-block; width:22px; height:22px; line-height:22px;
    text-align:center; border-radius:6px; font-size:0.8rem; font-weight:700; color:#fff; }}
.stars {{ color:#F59E0B; font-size:1.0rem; letter-spacing:1px; }}

/* Detail header */
.detail-head .name {{ font-size:1.5rem; font-weight:700; color:{TEXT}; }}
.detail-head .tk {{ color:{MUTED}; font-weight:600; }}
.detail-head .meta {{ font-size:0.9rem; color:{MUTED}; margin-top:2px; }}
.relevance {{ color:{MUTED}; font-size:0.9rem; margin:6px 0 12px; }}

/* News */
.news-item {{ border:1px solid {BORDER}; border-radius:10px; padding:12px 14px;
    margin-bottom:10px; background:#fff; }}
.news-item a {{ color:{NAVY}; font-weight:600; text-decoration:none; font-size:0.96rem; }}
.news-item a:hover {{ color:{PRIMARY}; text-decoration:underline; }}
.news-meta {{ font-size:0.78rem; color:{MUTED}; margin-top:4px; }}

.stButton > button {{ border-radius:9px; border:1px solid {BORDER}; font-weight:600; }}
.stButton > button:hover {{ border-color:{PRIMARY}; color:{PRIMARY}; }}

.stTabs [data-baseweb="tab-list"] {{ gap:4px; }}
.stTabs [data-baseweb="tab"] {{ font-weight:600; color:{MUTED}; border-radius:8px 8px 0 0; }}
.stTabs [aria-selected="true"] {{ color:{PRIMARY}; }}

[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:10px; }}
.section-title {{ font-size:1.05rem; font-weight:700; color:{NAVY}; margin:10px 0 4px; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def app_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="app-header"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )
