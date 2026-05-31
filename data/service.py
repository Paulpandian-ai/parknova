"""Cached access layer over the FMP client (live data only).

Caching policy:
  * history / momentum / quotes ... 15 min  (intraday)
  * news .......................... 30 min
The FMP client (holds a requests session, not hashable) lives in cache_resource.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from core import filing_cache
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


# ---------------------------------------------------------------------------
# On-demand filing analysis (document fetch + LLM), persisted to disk so a
# filing is analyzed at most once ever (survives restarts).
# ---------------------------------------------------------------------------
@st.cache_data(ttl=EDGAR_TTL, show_spinner=False)
def get_filing_document_text(cik: int, accession_number: str,
                             primary_document: str) -> str:
    """Cleaned plain text of a filing's primary document ("" on failure)."""
    try:
        return get_edgar_client().fetch_document_text(
            cik, accession_number, primary_document)
    except Exception:
        return ""


def filing_analysis_cached(accession_number: str, model: str) -> Optional[dict]:
    """Return a previously-saved on-disk (paid-API) analysis, or None."""
    return filing_cache.load(accession_number, model)


# ---------------------------------------------------------------------------
# Imported skill analyses (primary, zero-cost path)
# ---------------------------------------------------------------------------
def get_imported_analyses() -> Dict[str, dict]:
    """Index of imported skill analyses keyed by normalized accession."""
    return filing_cache.load_imported_index()


def save_imported_analysis(obj: dict) -> tuple:
    """Validate + persist one imported analysis. Returns (ok, message)."""
    return filing_cache.save_imported(obj)


def normalize_accession(accn) -> str:
    return filing_cache.normalize_accession(accn)


def analyze_filing(accession_number: str, model: str, cik: int,
                   primary_document: str, form: str,
                   filing_date: str, ticker: str) -> dict:
    """Fetch -> trim -> LLM-analyze a single filing, persisting to disk.

    If an analysis for ``(accession_number, model)`` already exists on disk it is
    returned instantly with ``cached=True`` and NO network/API call is made.
    Returns ``{text, usage, model, method, truncated, cached, error?}``.
    """
    existing = filing_cache.load(accession_number, model)
    if existing is not None:
        existing["cached"] = True
        return existing

    from data import anthropic_client as anth
    from data.edgar_client import trim_for_analysis

    raw = get_filing_document_text(cik, accession_number, primary_document)
    if not raw:
        return {"text": None, "usage": None, "model": model, "method": "none",
                "truncated": False, "cached": False,
                "error": "Could not fetch filing document from EDGAR."}
    trimmed = trim_for_analysis(form, raw)
    result = anth.analyze_filing(form, filing_date, ticker, trimmed["text"],
                                 model=model, truncated=trimmed["truncated"])
    result["method"] = trimmed["method"]
    result["truncated"] = trimmed["truncated"]
    result["cached"] = False
    if result.get("text"):
        filing_cache.save(accession_number, model, result)
    return result


def analyze_filing_activity(ticker: str, model: str,
                            items: List[dict]) -> dict:
    """Multi-filing synthesis, persisted to disk under a synthetic key.

    Cache key is the joined accession numbers + model, so re-running the same
    set of filings returns instantly.
    """
    accn_key = "ACT_" + "_".join(i.get("accessionNumber", "") for i in items)
    existing = filing_cache.load(accn_key, model)
    if existing is not None:
        existing["cached"] = True
        return existing

    from data import anthropic_client as anth
    result = anth.analyze_filing_activity(ticker, items, model=model)
    result["cached"] = False
    if result.get("text"):
        filing_cache.save(accn_key, model, result)
    return result


def clear_live_caches() -> None:
    """Clear the live caches (Morningstar load + EDGAR CIK map are untouched).

    NOTE: the disk-persisted filing analyses are intentionally NOT cleared here —
    a filing's content never changes, so its analysis is valid forever.
    """
    get_history.clear()
    get_quotes_batch.clear()
    get_stock_news.clear()
    get_general_news.clear()
    get_institutional_holders.clear()
    get_insider_trades.clear()
    get_sec_filings.clear()
