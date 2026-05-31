"""Cached access layer over the FMP client (live data only).

Caching policy:
  * history / momentum / quotes ... 15 min  (intraday)
  * news .......................... 30 min
The FMP client (holds a requests session, not hashable) lives in cache_resource.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from core import filing_cache
from data.edgar_client import EDGARClient
from data.finnhub_client import FinnhubClient, has_finnhub_key
from data.fmp_client import FMPClient, FMPError

logger = logging.getLogger("parknova.service")

QUOTE_TTL = 15           # near-real-time, but don't hammer
HISTORY_TTL = 900        # 15 min
MOMENTUM_TTL = 900       # back-compat alias
NEWS_TTL = 1800          # 30 min
DISCLOSURE_TTL = 86_400  # 1 day (institutional / insider)
EDGAR_TTL = 86_400       # 1 day


@st.cache_resource
def get_client() -> FMPClient:
    return FMPClient()


@st.cache_resource
def get_finnhub_client() -> FinnhubClient:
    return FinnhubClient()


@st.cache_resource
def get_edgar_client() -> EDGARClient:
    return EDGARClient()


def has_api_key() -> bool:
    try:
        get_client()
        return True
    except FMPError:
        return False


def has_finnhub() -> bool:
    return has_finnhub_key()


# ---------------------------------------------------------------------------
# History: prefer FMP historical; fall back to Finnhub candles. The result
# carries the source + any failure reason so the UI is never silently empty.
# ---------------------------------------------------------------------------
def _rows_to_series(rows: list, ticker: str) -> pd.Series:
    """Build a price Series from stable/legacy history rows.

    Defensive about the price field name (stable EOD may use ``adjClose``,
    ``close``, or the light endpoint's ``price``); logs which one was used so a
    live shape mismatch is easy to spot.
    """
    from data.fmp_client import pick_price_field
    if not rows:
        return pd.Series(dtype="float64", name=ticker)
    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        logger.warning("FMP history for %s has no 'date' column; keys=%s",
                       ticker, list(df.columns)[:8])
        return pd.Series(dtype="float64", name=ticker)
    price_col = pick_price_field(rows[0])
    if price_col is None or price_col not in df.columns:
        logger.warning("FMP history for %s: no known price field in %s",
                       ticker, list(df.columns)[:8])
        return pd.Series(dtype="float64", name=ticker)
    logger.info("FMP history for %s: using price field '%s'", ticker, price_col)
    df["date"] = pd.to_datetime(df["date"])
    s = (df.set_index("date")[price_col].astype("float64")
         .sort_index().dropna())
    s.name = ticker
    return s


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def get_history_result(ticker: str) -> dict:
    """Return ``{series, source, error}`` for a ticker's daily history.

    Precedence: FMP historical -> Finnhub candles -> empty (with reason).
    """
    fmp_err = None
    try:
        res = get_client().historical_result(ticker)
        rows, fmp_err = res["rows"], res.get("error")
        if rows:
            s = _rows_to_series(rows, ticker)
            if not s.empty:
                return {"series": s, "source": "FMP", "error": None}
    except FMPError:
        fmp_err = "FMP_API_KEY not set"

    # Fall back to Finnhub candles (last ~6 years of daily closes).
    if has_finnhub_key():
        to_u = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        from_u = to_u - int(6 * 365.25 * 86400)
        c = get_finnhub_client().candles(ticker, from_u, to_u)
        if c["closes"]:
            idx = pd.to_datetime(pd.Series(c["timestamps"]), unit="s")
            s = pd.Series(c["closes"], index=idx, dtype="float64",
                          name=ticker).sort_index().dropna()
            reason = (f"FMP: {fmp_err}" if fmp_err else
                      "FMP historical unavailable")
            return {"series": s, "source": "Finnhub", "error": reason}
        finn_err = c["error"]
        reason = "; ".join(x for x in [f"FMP: {fmp_err}" if fmp_err else None,
                                       f"Finnhub: {finn_err}"] if x)
        return {"series": pd.Series(dtype="float64", name=ticker),
                "source": None, "error": reason}

    reason = (f"FMP historical not available ({fmp_err})" if fmp_err else
              "FMP returned no history; set FINNHUB_API_KEY to enable fallback")
    return {"series": pd.Series(dtype="float64", name=ticker),
            "source": None, "error": reason}


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def get_history(ticker: str) -> pd.Series:
    """Adjusted-close Series (ascending date index). Empty when no data.

    Back-compat shim over :func:`get_history_result` (drops source/reason).
    """
    return get_history_result(ticker)["series"]


@st.cache_data(ttl=MOMENTUM_TTL, show_spinner=False)
def get_quotes_batch(tickers: tuple) -> Dict[str, dict]:
    return get_client().quotes_batch(list(tickers))


# ---------------------------------------------------------------------------
# Single near-real-time quote: prefer Finnhub -> fall back to FMP -> reason.
# ttl=15s keeps it fresh without hammering the free tier.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=QUOTE_TTL, show_spinner=False)
def get_live_quote(ticker: str) -> dict:
    """Return ``{price, pct, source, ts, error}`` for one ticker.

    Finnhub (near-real-time) first, then FMP quote, else a reason.
    """
    if has_finnhub_key():
        q = get_finnhub_client().quote(ticker)
        if q["price"] is not None:
            return {"price": q["price"], "pct": q["pct"], "source": "Finnhub",
                    "ts": q["ts"], "error": None}
        finn_err = q["error"]
    else:
        finn_err = "no FINNHUB_API_KEY"

    # Fall back to FMP batch quote (single symbol).
    try:
        fq = get_client().quotes_batch([ticker]).get(ticker) or {}
    except FMPError:
        fq = {}
    if fq.get("price") is not None:
        return {"price": fq.get("price"), "pct": fq.get("changesPercentage"),
                "source": "FMP", "ts": None, "error": None}
    return {"price": None, "pct": None, "source": None, "ts": None,
            "error": f"Finnhub: {finn_err}; FMP quote also empty"}


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
    get_history_result.clear()
    get_quotes_batch.clear()
    get_live_quote.clear()
    get_stock_news.clear()
    get_general_news.clear()
    get_institutional_holders.clear()
    get_insider_trades.clear()
    get_sec_filings.clear()


# ---------------------------------------------------------------------------
# Data diagnostics — probe each live endpoint for a test ticker and report the
# real reason a call fails (bad key / plan-gated / rate-limited / wrong shape).
# ---------------------------------------------------------------------------
def run_diagnostics(ticker: str = "NVDA") -> List[dict]:
    """Return a list of per-endpoint diagnostic rows for the Settings panel."""
    import datetime as dt

    rows: List[dict] = []

    # --- FMP stable endpoints (probe the EXACT paths the client calls) ---
    try:
        client = get_client()
        for name, url, params in client.diagnostic_targets(ticker):
            p = client.probe(url, params)
            rows.append({"endpoint": name, **p})
    except FMPError:
        rows.append({"endpoint": "FMP", "ok": False, "status": None,
                     "error": "FMP_API_KEY not set", "empty": False,
                     "kind": None, "keys": None, "sample": None})

    # --- Finnhub endpoints ---
    if has_finnhub_key():
        fc = get_finnhub_client()
        q = fc.quote(ticker)
        rows.append({"endpoint": "Finnhub quote",
                     "ok": q["price"] is not None, "status": None,
                     "error": q["error"], "empty": q["price"] is None,
                     "kind": "dict",
                     "keys": ["c", "dp", "pc", "t"],
                     "sample": {"price": q["price"], "pct": q["pct"]}})
        to_u = int(dt.datetime.now(dt.timezone.utc).timestamp())
        cdl = fc.candles(ticker, to_u - 30 * 86400, to_u)
        rows.append({"endpoint": "Finnhub candles (D)",
                     "ok": bool(cdl["closes"]), "status": None,
                     "error": cdl["error"], "empty": not cdl["closes"],
                     "kind": "dict", "keys": ["c", "t", "s"],
                     "sample": {"n_closes": len(cdl["closes"])}})
    else:
        rows.append({"endpoint": "Finnhub", "ok": False, "status": None,
                     "error": "FINNHUB_API_KEY not set", "empty": False,
                     "kind": None, "keys": None, "sample": None})
    return rows
