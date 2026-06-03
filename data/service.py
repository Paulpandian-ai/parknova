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

CREST_INDEX_KEY = "indices/crest_index.json"
CREST_INDEX_MAX_AGE = 86_400  # 1 day

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
def _rows_to_frame(rows: list, ticker: str) -> pd.DataFrame:
    """Build a DataFrame with ``adj`` (adjusted) and ``raw`` (unadjusted) close.

    Returns must be computed on the **adjusted** basis so splits/dividends don't
    fabricate moves; the displayed last price uses the **raw** (unadjusted) close
    — what the stock actually trades at. Keeping both columns lets us be
    consistent end to end and surface the difference in diagnostics.
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        logger.warning("FMP history for %s has no 'date' column; keys=%s",
                       ticker, list(df.columns)[:8])
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    def _first_col(cands):
        for c in cands:
            if c in df.columns:
                col = pd.to_numeric(df[c], errors="coerce")
                if col.notna().any():
                    return c, col
        return None, None

    adj_name, adj = _first_col(["adjClose", "adj_close", "adjustedClose",
                                "close", "price"])
    raw_name, raw = _first_col(["close", "price", "adjClose", "adj_close"])
    if adj is None:
        logger.warning("FMP history for %s: no known price field in %s",
                       ticker, list(df.columns)[:8])
        return pd.DataFrame()
    logger.info("FMP history for %s: adj field '%s', raw field '%s'",
                ticker, adj_name, raw_name)
    out = pd.DataFrame({"adj": adj})
    out["raw"] = raw if raw is not None else adj
    return out.dropna(subset=["adj"])


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def get_history_result(ticker: str) -> dict:
    """Return ``{series, raw_last, raw_last_date, source, error}``.

    ``series`` is the **adjusted**-close Series (used for all return math);
    ``raw_last`` / ``raw_last_date`` are the latest **unadjusted** close + date
    (used for the displayed last price). Precedence: FMP historical -> Finnhub
    candles -> empty (with reason). Finnhub candles are already split-adjusted,
    so raw == adj on that path.
    """
    fmp_err = None
    try:
        res = get_client().historical_result(ticker)
        rows, fmp_err = res["rows"], res.get("error")
        if rows:
            frame = _rows_to_frame(rows, ticker)
            if not frame.empty:
                s = frame["adj"].rename(ticker)
                raw = frame["raw"].dropna()
                return {"series": s,
                        "raw_last": float(raw.iloc[-1]) if not raw.empty else None,
                        "raw_last_date": raw.index[-1] if not raw.empty else None,
                        "source": "FMP", "error": None}
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
            return {"series": s,
                    "raw_last": float(s.iloc[-1]) if not s.empty else None,
                    "raw_last_date": s.index[-1] if not s.empty else None,
                    "source": "Finnhub", "error": reason}
        finn_err = c["error"]
        reason = "; ".join(x for x in [f"FMP: {fmp_err}" if fmp_err else None,
                                       f"Finnhub: {finn_err}"] if x)
        return {"series": pd.Series(dtype="float64", name=ticker),
                "raw_last": None, "raw_last_date": None,
                "source": None, "error": reason}

    reason = (f"FMP historical not available ({fmp_err})" if fmp_err else
              "FMP returned no history; set FINNHUB_API_KEY to enable fallback")
    return {"series": pd.Series(dtype="float64", name=ticker),
            "raw_last": None, "raw_last_date": None,
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
def _eod_change(ticker: str) -> Optional[dict]:
    """Last completed session's close-to-close change (reuses cached history)."""
    from core.performance import last_session_change
    series = get_history_result(ticker)["series"]
    return last_session_change(series)


@st.cache_data(ttl=QUOTE_TTL, show_spinner=False)
def get_live_quote(ticker: str) -> dict:
    """Return ``{price, pct, source, mode, as_of, error}`` for one ticker.

    The single source of truth for a ticker's displayed price/Today across the
    whole app (Stock Detail card AND the Performance table both call this), so
    the two never disagree. Cached at QUOTE_TTL (~15s) so repeated calls within
    a render — or back-to-back across views — return the identical quote object
    without re-hitting Finnhub/FMP, and the fragment poll picks up fresh values
    only after the TTL lapses (rate-limit safe).

    "Today" precedence so the value is meaningful 24/7:
      1. live intraday — Finnhub dp that is non-null AND non-zero (market open);
         mode='live', as_of=unix ts.
      2. EOD fallback — last completed session's close-to-close from the cached
         FMP-stable history (market closed / Finnhub flat); mode='last close',
         as_of=session date. No extra API call.
      3. FMP quote percent if present, else '—'.
    ``price`` prefers the freshest last price available (Finnhub -> FMP -> last
    close from history).
    """
    price = None
    source = None
    finn_err = None

    if has_finnhub_key():
        q = get_finnhub_client().quote(ticker)
        if q["price"] is not None:
            price, source = q["price"], "Finnhub"
            pct, ts = q["pct"], q["ts"]
            # Live intraday: non-null AND non-zero change.
            if pct is not None and float(pct) != 0.0:
                return {"price": price, "pct": float(pct) / 100.0,
                        "source": source, "mode": "live", "as_of": ts,
                        "error": None}
        else:
            finn_err = q["error"]
    else:
        finn_err = "no FINNHUB_API_KEY"

    # FMP quote (single symbol) for a price (and a possible percent).
    fq = {}
    if price is None:
        try:
            fq = get_client().quotes_batch([ticker]).get(ticker) or {}
        except FMPError:
            fq = {}
        if fq.get("price") is not None:
            price, source = fq.get("price"), "FMP"

    # EOD fallback for the percent (market closed / live flat).
    eod = _eod_change(ticker)
    if eod is not None:
        if price is None:
            price, source = eod["last_close"], "FMP (EOD)"
        return {"price": price, "pct": eod["pct"], "source": source or "FMP",
                "mode": "last close", "as_of": eod["date"], "error": None}

    # No history: last resort is the FMP quote percent if any.
    fpct = fq.get("changesPercentage")
    if price is not None:
        return {"price": price, "pct": (float(fpct) / 100.0)
                if fpct not in (None, 0) else None,
                "source": source, "mode": "live" if fpct else None,
                "as_of": None, "error": None}
    return {"price": None, "pct": None, "source": None, "mode": None,
            "as_of": None, "error": f"Finnhub: {finn_err}; FMP quote also empty"}


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
# Crest / Side return indices — built from the (broader) per-ticker history,
# persisted via core.store so the series accrues and loads fast.
# ---------------------------------------------------------------------------
def get_crest_indices(classification: pd.DataFrame, force: bool = False,
                      progress=None) -> dict:
    """Return ``{"records": [...], "as_of": iso, "rebuilt": bool}``.

    Uses a stored series when it is < 1 day old (unless ``force``); otherwise
    rebuilds from FMP-stable history (reusing the cached per-ticker fetch) and
    persists it. ``records`` is the tidy long-form index (date as ISO string).
    """
    from core import crest_index, store

    if not force:
        age = store.age_seconds(CREST_INDEX_KEY)
        if age is not None and age < CREST_INDEX_MAX_AGE:
            cached = store.load(CREST_INDEX_KEY)
            if cached:
                return {"records": cached, "as_of": store.as_of(CREST_INDEX_KEY),
                        "rebuilt": False}

    tickers = classification["Ticker"].tolist()
    prices: dict = {}
    total = len(tickers)
    for i, t in enumerate(tickers):
        s = get_history_result(t)["series"]   # cached single fetch per ticker
        if s is not None and not s.empty:
            prices[t] = s
        if progress and total:
            progress((i + 1) / total, t)

    df = crest_index.build_indices(
        prices, classification[["Ticker", "Crest", "Side"]])
    records = []
    if not df.empty:
        tmp = df.copy()
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m-%d")
        records = tmp.to_dict("records")
    store.save(CREST_INDEX_KEY, records)
    from core import store as _store
    return {"records": records, "as_of": _store.as_of(CREST_INDEX_KEY),
            "rebuilt": True}


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


def price_basis_report(ticker: str) -> dict:
    """Side-by-side price-basis diagnostics for one ticker, so a split/source
    mismatch is self-evident.

    Returns ``{latest_close, latest_adjclose, morningstar_last, computed_1y,
    last_rows, note}`` — last_rows is the last 3 history rows (date, close,
    adjClose). Computed 1Y uses the **adjusted** series (split-safe).
    """
    out = {"ticker": ticker.upper(), "latest_close": None,
           "latest_adjclose": None, "morningstar_last": None,
           "computed_1y": None, "last_rows": [], "note": None}

    # Morningstar static Last Price (the export-time value).
    try:
        from data import morningstar as _ms
        msf = _ms.load_morningstar()
        r = msf[msf["Ticker"] == ticker.upper()]
        if not r.empty:
            v = r["Last Price"].iloc[0]
            out["morningstar_last"] = float(v) if pd.notna(v) else None
    except Exception as exc:
        out["note"] = f"Morningstar lookup failed: {exc}"

    # Raw close vs adjusted close from the FMP history rows.
    try:
        res = get_client().historical_result(ticker)
        rows = res.get("rows") or []
        if res.get("error"):
            out["note"] = (out["note"] or "") + f" FMP: {res['error']}"
        if rows:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            close = pd.to_numeric(df.get("close"), errors="coerce") \
                if "close" in df else None
            adj = pd.to_numeric(df.get("adjClose"), errors="coerce") \
                if "adjClose" in df else None
            if close is not None and close.notna().any():
                out["latest_close"] = float(close.dropna().iloc[-1])
            if adj is not None and adj.notna().any():
                out["latest_adjclose"] = float(adj.dropna().iloc[-1])
            # Last 3 rows: date, close, adjClose.
            for _, rr in df.tail(3).iterrows():
                out["last_rows"].append({
                    "date": rr["date"].strftime("%Y-%m-%d"),
                    "close": (float(rr["close"]) if "close" in df
                              and pd.notna(rr.get("close")) else None),
                    "adjClose": (float(rr["adjClose"]) if "adjClose" in df
                                 and pd.notna(rr.get("adjClose")) else None),
                })
            # Computed 1Y on the adjusted series (split-safe).
            hist = get_history_result(ticker)
            s = hist["series"]
            if s is not None and not s.empty:
                from core.performance import trailing_return
                out["computed_1y"] = trailing_return(s, pd.DateOffset(years=1))
        elif not out["note"]:
            out["note"] = "FMP returned no history rows."
    except FMPError:
        out["note"] = "FMP_API_KEY not set"
    except Exception as exc:
        out["note"] = (out["note"] or "") + f" history error: {exc}"
    return out
