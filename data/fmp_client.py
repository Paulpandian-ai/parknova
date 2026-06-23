"""Financial Modeling Prep (FMP) wrapper — live data ONLY, **stable API**.

Per the strict division of labor, FMP is used solely for:
  * short-window price momentum (Today/1W/1M/3M/6M) from daily close history, and
  * per-ticker news, plus (plan-permitting) institutional / insider disclosure.

No fundamentals are ever fetched here — those come from the Morningstar export.

MIGRATION NOTE (legacy /api/v3 -> /stable):
  The legacy v3/v4 paths now return HTTP 403 "Legacy Endpoint". This client
  targets the **stable** root and the stable query-param style (``?symbol=...``).
  Stable field names can differ from v3, so responses are normalised back to the
  names the rest of the app expects (``symbol``/``price``/``changesPercentage``;
  history ``date`` + a price field; news ``title``/``site``/``publishedDate``/
  ``url``/``sentiment``). Each method carries an inline comment with the EXACT
  URL + params used, so if the live response differs it is a one-line fix.

The class is Streamlit-free so it can be unit-tested; caching lives in
``data/service.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.config import get_secret

logger = logging.getLogger("parknova.fmp")

# This client targets the stable API root (not /api/v3 or /api/v4).
STABLE = "https://financialmodelingprep.com/stable"
STABLE_API = True
DEFAULT_TIMEOUT = 15

# Candidate field names for defensive parsing (stable may differ from v3).
_PRICE_FIELDS = ("adjClose", "adj_close", "adjustedClose", "close", "price")
_PCT_FIELDS = ("changePercentage", "changesPercentage", "changePercent",
               "changesPercentageValue")


def fmp_error_text(data: Any) -> Optional[str]:
    """Return FMP's error/usage message if ``data`` is an error object.

    FMP returns a JSON object like ``{"Error Message": "..."}`` (or an
    "Exclusive Endpoint"/"Legacy Endpoint"/usage notice) when a key is invalid,
    an endpoint is plan-gated/legacy, or a limit is hit — sometimes with HTTP 200,
    sometimes 403. Detect those so the reason is never lost.
    """
    if isinstance(data, dict):
        for k in ("Error Message", "error", "message", "Information"):
            if k in data and isinstance(data[k], str):
                return data[k]
    return None


def _first_num(row: Dict[str, Any], fields: Tuple[str, ...]) -> Optional[float]:
    """First field in ``fields`` present on ``row`` that parses to a float."""
    for f in fields:
        if f in row and row[f] is not None:
            try:
                return float(row[f])
            except (TypeError, ValueError):
                continue
    return None


class FMPError(RuntimeError):
    """Raised when the API key is missing."""


class FMPClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or get_secret("FMP_API_KEY")
        if not self.api_key:
            raise FMPError(
                "FMP_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ParkNova/1.0"})

    # ------------------------------------------------------------------
    def _get(self, url: str, params: Optional[Dict[str, Any]] = None,
             return_errors: bool = False) -> Any:
        """GET returning parsed JSON, or None on any failure.

        Handles timeouts/HTTP errors/empty bodies and retries once on 429 with
        back-off. When ``return_errors`` is True, an FMP error/usage object — on
        HTTP 200 *or* on a 403 "Legacy Endpoint" body — is returned as-is so
        callers can surface the reason instead of getting a silent None.
        """
        params = dict(params or {})
        params["apikey"] = self.api_key

        for attempt in range(2):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                return None

            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                return None
            # Even on a non-2xx (e.g. 403 Legacy/plan-gated) try to parse the
            # JSON body so the verbatim reason reaches diagnostics/callers.
            try:
                data = resp.json()
            except ValueError:
                return None
            if return_errors and fmp_error_text(data):
                return data
            if not resp.ok:
                return None
            if data in ({}, []):
                return None
            return data
        return None

    def probe(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Diagnostics: GET ``url`` and report status, error text, and shape.

        Returns ``{ok, status, error, empty, kind, keys, sample}`` without
        raising — used by the Settings → Data diagnostics panel so the real
        reason a call fails (bad key / plan-gated / legacy / rate-limited / wrong
        shape) is visible verbatim.
        """
        params = dict(params or {})
        params["apikey"] = self.api_key
        out: Dict[str, Any] = {"ok": False, "status": None, "error": None,
                               "empty": False, "kind": None, "keys": None,
                               "sample": None}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            out["error"] = f"request failed: {exc}"
            return out
        out["status"] = resp.status_code
        if resp.status_code == 429:
            out["error"] = "rate-limited (HTTP 429)"
            return out
        try:
            data = resp.json()
        except ValueError:
            out["error"] = f"non-JSON body: {resp.text[:160]}"
            return out
        err = fmp_error_text(data)
        if err:
            out["error"] = err  # verbatim legacy / plan-gated / bad-key message
            return out
        if not resp.ok:
            out["error"] = f"HTTP {resp.status_code}: {str(data)[:160]}"
            return out
        if data in ({}, [], None):
            out["empty"] = True
            return out
        out["kind"] = type(data).__name__
        if isinstance(data, dict):
            out["keys"] = list(data.keys())[:10]
            out["sample"] = {k: data[k] for k in list(data.keys())[:3]}
        elif isinstance(data, list):
            out["keys"] = list(data[0].keys())[:10] if isinstance(data[0], dict) else None
            out["sample"] = data[0] if data else None
        out["ok"] = True
        return out

    def diagnostic_targets(self, ticker: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        """(name, url, params) for each stable endpoint, so diagnostics probe the
        exact same paths these methods call."""
        import datetime as dt
        today = dt.date.today()
        frm = today - dt.timedelta(days=30)
        return [
            ("FMP historical-price-eod/full",
             f"{STABLE}/historical-price-eod/full",
             {"symbol": ticker, "from": frm.isoformat(), "to": today.isoformat()}),
            ("FMP quote", f"{STABLE}/quote", {"symbol": ticker}),
            ("FMP news/stock", f"{STABLE}/news/stock",
             {"symbols": ticker, "limit": 1}),
        ]

    def extended_hours_targets(self, ticker: str
                               ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """(name, url, params) for the pre/post-market endpoints to probe.

        These are plan-gated on many tiers; the aftermarket-trade endpoint, when
        available, carries an extended-hours print (price + epoch-ms timestamp).
        Diagnostics probes both so the verbatim status/shape is visible.
        """
        return [
            ("FMP aftermarket-trade", f"{STABLE}/aftermarket-trade",
             {"symbol": ticker}),
            ("FMP aftermarket-quote", f"{STABLE}/aftermarket-quote",
             {"symbol": ticker}),
        ]

    def aftermarket_trade(self, ticker: str) -> Dict[str, Any]:
        """Return ``{price, ts, raw, error}`` from the aftermarket-trade endpoint.

        ``price`` is the extended-hours trade price and ``ts`` its timestamp
        (epoch seconds, normalised from FMP's epoch-ms) when present. Returns
        price/ts None with the FMP reason when the endpoint is plan-gated/empty —
        the caller must NOT treat that as an extended-hours price.
        """
        data = self._get(f"{STABLE}/aftermarket-trade", {"symbol": ticker},
                         return_errors=True)
        err = fmp_error_text(data)
        if err:
            return {"price": None, "ts": None, "raw": data, "error": err}
        row = data[0] if isinstance(data, list) and data else (
            data if isinstance(data, dict) else None)
        if not isinstance(row, dict):
            return {"price": None, "ts": None, "raw": data,
                    "error": "no aftermarket trade row"}
        price = _first_num(row, ("price", "tradePrice", "lastPrice", "p"))
        ts = _first_num(row, ("timestamp", "ts", "t", "date"))
        if ts is not None and ts > 1e12:   # epoch ms -> seconds
            ts = ts / 1000.0
        return {"price": price, "ts": int(ts) if ts is not None else None,
                "raw": row, "error": None}

    # ------------------------------------------------------------------
    # Daily history (one fetch per ticker; momentum windows sliced locally)
    #
    # STABLE: GET /stable/historical-price-eod/full?symbol={t}&from=&to=
    #   -> bare array of {symbol, date, open, high, low, close, adjClose?, volume}
    #   "light" variant (/stable/historical-price-eod/light?symbol={t}) returns
    #   {symbol, date, price, volume} and is used as a fallback when full is
    #   plan-gated. Field names verified defensively (_PRICE_FIELDS).
    # ------------------------------------------------------------------
    def historical(
        self, ticker: str, from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return history rows, or [] (errors swallowed). Prefer
        :meth:`historical_result` when you need the failure reason."""
        return self.historical_result(ticker, from_date, to_date)["rows"]

    def historical_result(
        self, ticker: str, from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return ``{rows, error}``: parsed history rows + any FMP error text.

        Tries the stable EOD "full" endpoint, then the "light" endpoint if full
        is plan-gated/empty. ``error`` is set (rows empty) when both fail so
        callers can fall back to Finnhub and show why.
        """
        params: Dict[str, Any] = {"symbol": ticker}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        # Primary: stable EOD full.
        data = self._get(f"{STABLE}/historical-price-eod/full", params,
                         return_errors=True)
        err = fmp_error_text(data)
        rows = _extract_history_rows(data) if not err else []
        if rows:
            return {"rows": rows, "error": None}

        # Fallback: stable EOD light (often available on lower tiers).
        light = self._get(f"{STABLE}/historical-price-eod/light", params,
                          return_errors=True)
        light_err = fmp_error_text(light)
        light_rows = _extract_history_rows(light) if not light_err else []
        if light_rows:
            return {"rows": light_rows, "error": None}

        return {"rows": [], "error": err or light_err or None}

    # ------------------------------------------------------------------
    # Quotes  —  STABLE: GET /stable/quote?symbol={t}
    #   -> bare array [{symbol, name, price, changePercentage, change,
    #      marketCap, volume, dayLow, dayHigh, ...}]
    #   Stable batches via comma-joined symbols (?symbol=A,B,C); we chunk to be
    #   safe. Normalised to expose ``changesPercentage`` (v3 name) downstream.
    # ------------------------------------------------------------------
    def quotes_batch(self, tickers: List[str], batch_size: int = 50
                     ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        tickers = [t for t in tickers if t]
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            data = self._get(f"{STABLE}/quote", {"symbol": ",".join(chunk)})
            if isinstance(data, list):
                for row in data:
                    sym = row.get("symbol")
                    if sym:
                        out[sym] = _normalise_quote(row)
        return out

    # ------------------------------------------------------------------
    # News  —  STABLE: GET /stable/news/stock?symbols={t}&limit={n}
    #   -> bare array [{symbol, publishedDate, title, image, site, text, url}]
    #   (some tenants expose /stable/news/stock-latest — adjust here if so).
    #   Normalised to title/site/publishedDate/url/sentiment downstream.
    # ------------------------------------------------------------------
    def stock_news(self, ticker: str, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._get(f"{STABLE}/news/stock",
                         {"symbols": ticker, "limit": limit})
        if not isinstance(data, list):
            return []
        return [_normalise_news(n) for n in data]

    def general_news(self, limit: int = 30) -> List[Dict[str, Any]]:
        # STABLE: GET /stable/news/general-latest?limit={n}
        data = self._get(f"{STABLE}/news/general-latest", {"limit": limit})
        if not isinstance(data, list):
            return []
        return [_normalise_news(n) for n in data]

    # ------------------------------------------------------------------
    # Institutional & insider disclosure (often plan-gated -> graceful empty).
    # ------------------------------------------------------------------
    def institutional_holders(self, ticker: str) -> List[Dict[str, Any]]:
        # STABLE: GET /stable/institutional-ownership/holders?symbol={t}
        #   (verify exact path/fields live; falls back to empty on 403/plan-gate)
        data = self._get(f"{STABLE}/institutional-ownership/holders",
                         {"symbol": ticker})
        return data if isinstance(data, list) else []

    def insider_trades(self, ticker: str, limit: int = 50) -> List[Dict[str, Any]]:
        # STABLE: GET /stable/insider-trading/search?symbol={t}&page=0&limit={n}
        data = self._get(f"{STABLE}/insider-trading/search",
                         {"symbol": ticker, "page": 0, "limit": limit})
        return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Defensive parsing helpers (module-level so they're easy to unit-test)
# ---------------------------------------------------------------------------
def _extract_history_rows(data: Any) -> List[Dict[str, Any]]:
    """Pull the list of daily rows from any of the shapes stable might return.

    Handles: a bare array; ``{"historical": [...]}`` (legacy-ish); and
    ``{"historicalStockList": [{"historical": [...]}]}``.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("historical"), list):
            return data["historical"]
        hsl = data.get("historicalStockList")
        if isinstance(hsl, list) and hsl and isinstance(hsl[0], dict):
            return hsl[0].get("historical", []) or []
    return []


def pick_price_field(row: Dict[str, Any]) -> Optional[str]:
    """Return the first present price field name in a history row (or None)."""
    for f in _PRICE_FIELDS:
        if f in row and row[f] is not None:
            return f
    return None


def _normalise_quote(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure the v3 field names the app reads exist on a stable quote row."""
    row = dict(row)
    if "changesPercentage" not in row:
        for f in _PCT_FIELDS:
            if f in row and row[f] is not None:
                row["changesPercentage"] = row[f]
                break
    return row


def _normalise_news(n: Dict[str, Any]) -> Dict[str, Any]:
    """Map stable news fields to the keys the UI/synthesis expect."""
    n = dict(n)
    if not n.get("site"):
        n["site"] = n.get("publisher") or n.get("source") or ""
    if not n.get("publishedDate"):
        n["publishedDate"] = n.get("date") or n.get("publishedAt") or ""
    return n
