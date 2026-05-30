"""Cached access layer over the FMP client (live data only).

Caching policy:
  * history / momentum / quotes ... 15 min  (intraday)
  * news .......................... 30 min
The FMP client (holds a requests session, not hashable) lives in cache_resource.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd
import streamlit as st

from data.edgar_client import EDGARClient
from data.fmp_client import FMPClient, FMPError

MOMENTUM_TTL = 900       # 15 min
NEWS_TTL = 1800          # 30 min
DISCLOSURE_TTL = 86_400  # 1 day (institutional / insider)
EDGAR_TTL = 86_400       # 1 day


@st.cache_resource
def get_client() -> FMPClient:
    return FMPClient()


@st.cache_resource
def get_edgar_client() -> EDGARClient:
    return EDGARClient()


def has_api_key() -> bool:
    try:
        get_client()
        return True
    except FMPError:
        return False


@st.cache_data(ttl=MOMENTUM_TTL, show_spinner=False)
def get_history(ticker: str) -> pd.Series:
    """Adjusted-close Series (ascending date index). Empty when no data."""
    rows = get_client().historical(ticker)
    if not rows:
        return pd.Series(dtype="float64", name=ticker)
    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        return pd.Series(dtype="float64", name=ticker)
    price_col = "adjClose" if "adjClose" in df.columns else "close"
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")[price_col].astype("float64").sort_index().dropna()
    s.name = ticker
    return s


@st.cache_data(ttl=MOMENTUM_TTL, show_spinner=False)
def get_quotes_batch(tickers: tuple) -> Dict[str, dict]:
    return get_client().quotes_batch(list(tickers))


@st.cache_data(ttl=NEWS_TTL, show_spinner=False)
def get_stock_news(ticker: str, limit: int = 20) -> List[dict]:
    return get_client().stock_news(ticker, limit=limit)


@st.cache_data(ttl=NEWS_TTL, show_spinner=False)
def get_general_news(limit: int = 30) -> List[dict]:
    return get_client().general_news(limit=limit)


# ---------------------------------------------------------------------------
# Institutional & insider disclosure (FMP) — may be plan-gated -> empty list
# ---------------------------------------------------------------------------
@st.cache_data(ttl=DISCLOSURE_TTL, show_spinner=False)
def get_institutional_holders(ticker: str) -> List[dict]:
    try:
        return get_client().institutional_holders(ticker)
    except Exception:
        return []


@st.cache_data(ttl=DISCLOSURE_TTL, show_spinner=False)
def get_insider_trades(ticker: str, limit: int = 50) -> List[dict]:
    try:
        return get_client().insider_trades(ticker, limit=limit)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# SEC EDGAR (no key; mandatory User-Agent handled in the client)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=EDGAR_TTL, show_spinner=False)
def get_cik_map() -> Dict[str, str]:
    try:
        return get_edgar_client().ticker_cik_map()
    except Exception:
        return {}


@st.cache_data(ttl=EDGAR_TTL, show_spinner=False)
def get_sec_filings(ticker: str, limit: int = 20) -> List[dict]:
    """Recent SEC filings for a ticker. Empty list when CIK unknown / on error."""
    try:
        cik = get_cik_map().get(ticker.upper())
        if not cik:
            return []
        return get_edgar_client().recent_filings(cik, limit=limit)
    except Exception:
        return []


def clear_live_caches() -> None:
    """Clear the live caches (Morningstar load + EDGAR CIK map are untouched)."""
    get_history.clear()
    get_quotes_batch.clear()
    get_stock_news.clear()
    get_general_news.clear()
    get_institutional_holders.clear()
    get_insider_trades.clear()
    get_sec_filings.clear()
