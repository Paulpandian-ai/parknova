"""Cached data-access layer over :class:`FMPClient`.

Everything the UI calls goes through here so caching policy lives in one place:

* quotes / performance ........ 15 min  (intraday freshness)
* fundamentals / profiles ..... 1 day
* historical prices ........... 1 day   (fetched once per ticker, sliced locally)

A single :class:`FMPClient` is created via ``st.cache_resource`` (the session is
not hashable, so it must not live inside ``cache_data``). The data functions take
plain strings, which hash cleanly.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from data.fmp_client import FMPClient, FMPError

QUOTE_TTL = 900  # 15 minutes
FUNDAMENTALS_TTL = 86_400  # 1 day
HISTORY_TTL = 86_400  # 1 day


@st.cache_resource
def get_client() -> FMPClient:
    """Return a process-wide FMP client (holds the requests session)."""
    return FMPClient()


def has_api_key() -> bool:
    try:
        get_client()
        return True
    except FMPError:
        return False


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
@st.cache_data(ttl=QUOTE_TTL, show_spinner=False)
def get_quote(ticker: str) -> Optional[dict]:
    return get_client().quote(ticker)


@st.cache_data(ttl=QUOTE_TTL, show_spinner=False)
def get_quotes_batch(tickers: tuple) -> Dict[str, dict]:
    """Batch quote lookup. ``tickers`` is a tuple so it is hashable for caching."""
    return get_client().quotes_batch(list(tickers))


# ---------------------------------------------------------------------------
# Historical prices (fetched once per ticker, sliced locally for all windows)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def get_history(ticker: str) -> pd.Series:
    """Return an adjusted-close price Series indexed by ascending date.

    Empty Series when FMP has no data for the ticker.
    """
    rows = get_client().historical(ticker)
    if not rows:
        return pd.Series(dtype="float64", name=ticker)

    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        return pd.Series(dtype="float64", name=ticker)

    # Prefer adjusted close; fall back to close if the field is absent.
    price_col = "adjClose" if "adjClose" in df.columns else "close"
    df["date"] = pd.to_datetime(df["date"])
    series = (
        df.set_index("date")[price_col]
        .astype("float64")
        .sort_index()
        .dropna()
    )
    series.name = ticker
    return series


# ---------------------------------------------------------------------------
# Fundamentals / profile
# ---------------------------------------------------------------------------
@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def get_profile(ticker: str) -> Optional[dict]:
    return get_client().profile(ticker)


@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def get_key_metrics_ttm(ticker: str) -> Optional[dict]:
    return get_client().key_metrics_ttm(ticker)


@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def get_ratios_ttm(ticker: str) -> Optional[dict]:
    return get_client().ratios_ttm(ticker)


@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def get_income_statement(ticker: str, limit: int = 5) -> List[dict]:
    return get_client().income_statement(ticker, limit=limit)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
def clear_all_caches() -> None:
    """Clear cached data (used by the 'Refresh data' button)."""
    get_quote.clear()
    get_quotes_batch.clear()
    get_history.clear()
    get_profile.clear()
    get_key_metrics_ttm.clear()
    get_ratios_ttm.clear()
    get_income_statement.clear()
