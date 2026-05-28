"""Theme constants and global CSS injection for a polished white/blue dashboard."""

from __future__ import annotations

import streamlit as st

# --- Palette -------------------------------------------------------------
PRIMARY = "#2563EB"      # primary blue
NAVY = "#1E3A8A"         # deep navy (headers)
TEXT = "#0F172A"         # slate text
MUTED = "#64748B"        # muted text
SURFACE = "#F8FAFC"      # surface
SURFACE_2 = "#F1F5F9"    # secondary surface
BORDER = "#E2E8F0"       # borders
POSITIVE = "#16A34A"     # green
NEGATIVE = "#DC2626"     # red

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
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}

/* Tighten dashboard density */
.block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }}

/* App header bar with subtle navy->blue gradient */
.app-header {{
    background: linear-gradient(90deg, {NAVY} 0%, {PRIMARY} 100%);
    color: #FFFFFF;
    padding: 18px 26px;
    border-radius: 14px;
    margin-bottom: 18px;
    box-shadow: 0 6px 20px rgba(30,58,138,0.18);
}}
.app-header h1 {{ font-size: 1.5rem; font-weight: 700; margin: 0; color: #FFFFFF; letter-spacing: -0.01em; }}
.app-header p {{ margin: 2px 0 0; font-size: 0.9rem; opacity: 0.85; color: #E2E8F0; }}

/* Stat / metric cards */
.metric-card {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(15,23,42,0.06);
    height: 100%;
}}
.metric-card .label {{ font-size: 0.78rem; color: {MUTED}; font-weight: 500; text-transform: uppercase; letter-spacing: 0.04em; }}
.metric-card .value {{ font-size: 1.35rem; font-weight: 700; color: {TEXT}; margin-top: 4px; }}
.metric-card .sub {{ font-size: 0.82rem; color: {MUTED}; margin-top: 2px; }}
.value.pos {{ color: {POSITIVE}; }}
.value.neg {{ color: {NEGATIVE}; }}

/* Detail header */
.detail-head {{ display: flex; align-items: center; gap: 16px; margin-bottom: 6px; }}
.detail-head img {{ width: 52px; height: 52px; border-radius: 10px; border: 1px solid {BORDER}; object-fit: contain; background:#fff; }}
.detail-head .name {{ font-size: 1.4rem; font-weight: 700; color: {TEXT}; }}
.detail-head .meta {{ font-size: 0.9rem; color: {MUTED}; }}
.badge {{
    display: inline-block; background: {SURFACE_2}; color: {NAVY};
    border: 1px solid {BORDER}; border-radius: 999px; padding: 2px 10px;
    font-size: 0.78rem; font-weight: 600; margin-right: 6px;
}}
.relevance {{ color: {MUTED}; font-size: 0.92rem; margin: 6px 0 14px; }}

/* Buttons */
.stButton > button {{
    border-radius: 9px; border: 1px solid {BORDER}; font-weight: 600;
}}
.stButton > button:hover {{ border-color: {PRIMARY}; color: {PRIMARY}; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
.stTabs [data-baseweb="tab"] {{
    font-weight: 600; color: {MUTED}; border-radius: 8px 8px 0 0;
}}
.stTabs [aria-selected="true"] {{ color: {PRIMARY}; }}

/* Dataframe: subtle rounded container */
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; }}

.section-title {{ font-size: 1.05rem; font-weight: 700; color: {NAVY}; margin: 8px 0 4px; }}
</style>
"""


def inject_css() -> None:
    """Inject the global stylesheet. Call once near the top of the app."""
    st.markdown(_CSS, unsafe_allow_html=True)


def app_header(title: str, subtitle: str) -> None:
    st.markdown(
        f"""<div class="app-header"><h1>{title}</h1><p>{subtitle}</p></div>""",
        unsafe_allow_html=True,
    )
