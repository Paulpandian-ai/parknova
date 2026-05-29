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

from data.fmp_client import FMPClient, FMPError

MOMENTUM_TTL = 900       # 15 min
NEWS_TTL = 1800          # 30 min


@st.cache_resource
def get_client() -> FMPClient:
    return FMPClient()


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


def clear_live_caches() -> None:
    """Clear only the live FMP caches (Morningstar load is untouched)."""
    get_history.clear()
    get_quotes_batch.clear()
    get_stock_news.clear()
    get_general_news.clear()
