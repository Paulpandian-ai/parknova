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

# Capex-cycle palettes (Crest layer + Side).
CREST_COLORS = {"Early": "#0D9488", "Mid": "#64748B", "Late": "#CA8A04"}
SIDE_COLORS = {"Supplier": "#2563EB", "Demand": "#7C3AED",
               "Hyperscaler": "#1E3A8A", "Unknown": "#94A3B8"}
TRAP_AMBER = "#CA8A04"
SIGNAL_AMBER = "#CA8A04"   # stretch / warning signals (same as trap amber)
SIGNAL_TEAL = "#0D9488"    # opportunity hints (same as Early-crest teal)

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: {TEXT};
}}
.stApp {{ background-color: #FFFFFF; }}

/* Hide default Streamlit chrome */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
[data-testid="stToolbar"] {{ visibility: hidden; height: 0; }}
[data-testid="stDecoration"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
section[data-testid="stSidebar"] {{ display: none; }}

/* Dense, centered, constrained content */
.block-container {{
    padding-top: 0.8rem; padding-bottom: 2rem;
    max-width: 1400px; margin: 0 auto;
}}

/* Slim flat header bar */
.app-header {{
    background: {NAVY};
    color: #FFFFFF; padding: 12px 20px; border-radius: 8px;
    margin-bottom: 8px;
}}
.app-header h1 {{ font-size: 1.15rem; font-weight: 700; margin: 0;
    letter-spacing: -0.01em; color:#fff; }}
.app-header p {{ margin: 1px 0 0; font-size: 0.8rem; opacity: 0.82; color: #E2E8F0; }}

/* Stat cards — consistent height, hairline border, 12px radius */
.metric-card {{
    background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 12px 14px; min-height: 78px; box-sizing: border-box;
}}
.metric-card .label {{ font-size: 0.7rem; color: {MUTED}; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em; }}
.metric-card .value {{ font-size: 1.25rem; font-weight: 700; color: {TEXT};
    margin-top: 4px; }}
.metric-card .sub {{ font-size: 0.78rem; color: {MUTED}; margin-top: 2px; }}
.value.pos {{ color: {POSITIVE}; }}
.value.neg {{ color: {NEGATIVE}; }}

/* Chips & badges — flat, solid subtle fills, consistent pill radius */
.chip {{ display:inline-block; padding:2px 10px; border-radius:6px;
    font-size:0.74rem; font-weight:600; color:#fff; }}
.badge-grade {{ display:inline-block; width:22px; height:22px; line-height:22px;
    text-align:center; border-radius:6px; font-size:0.8rem; font-weight:700; color:#fff; }}
.stars {{ color:#F59E0B; font-size:1.0rem; letter-spacing:1px; }}

/* Detail header */
.detail-head .name {{ font-size:1.4rem; font-weight:700; color:{TEXT}; }}
.detail-head .tk {{ color:{MUTED}; font-weight:600; }}
.detail-head .meta {{ font-size:0.9rem; color:{MUTED}; margin-top:2px; }}
.relevance {{ color:{MUTED}; font-size:0.9rem; margin:6px 0 12px; }}

/* News / filing items */
.news-item {{ border:1px solid {BORDER}; border-radius:8px; padding:12px 14px;
    margin-bottom:8px; background:#fff; }}
.news-item a {{ color:{NAVY}; font-weight:600; text-decoration:none; font-size:0.94rem; }}
.news-item a:hover {{ color:{PRIMARY}; text-decoration:underline; }}
.news-meta {{ font-size:0.78rem; color:{MUTED}; margin-top:4px; }}

/* Buttons — hairline, slate, blue on hover */
.stButton > button {{ border-radius:8px; border:1px solid {BORDER};
    font-weight:600; color:{TEXT}; background:#fff; }}
.stButton > button:hover {{ border-color:{PRIMARY}; color:{PRIMARY}; }}

/* Tables — hairline border, light header, comfortable rows */
[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:8px; }}
[data-testid="stDataFrame"] thead tr th {{
    background:{SURFACE}; color:{MUTED}; font-weight:600; }}

/* Titles */
.section-title {{ font-size:1.0rem; font-weight:700; color:{NAVY};
    margin:8px 0 4px; }}
.view-title {{ font-size:1.25rem; font-weight:700; color:{NAVY};
    margin:4px 0 8px; letter-spacing:-0.01em; }}
.muted-note {{ font-size:0.78rem; color:{MUTED}; }}

/* Tighten multiselect / inputs in the filter toolbar */
[data-testid="stHorizontalBlock"] {{ gap: 0.6rem; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def app_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="app-header"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


# Enterprise styling for the streamlit-option-menu horizontal nav.
def nav_styles() -> dict:
    return {
        "container": {"padding": "0", "background-color": "transparent",
                      "margin-bottom": "10px"},
        "nav-link": {
            "font-size": "0.9rem", "font-weight": "600", "color": MUTED,
            "background-color": "transparent", "padding": "8px 14px",
            "margin": "0 2px", "border-radius": "0",
            "border-bottom": "2px solid transparent", "--hover-color": SURFACE_2,
        },
        "nav-link-selected": {
            "color": PRIMARY, "background-color": "transparent",
            "font-weight": "700", "border-bottom": f"2px solid {PRIMARY}",
            "border-radius": "0",
        },
        "icon": {"display": "none"},
    }
