"""Financial Modeling Prep (FMP) wrapper — live data ONLY.

Per the strict division of labor, FMP is used solely for:
  * short-window price momentum (Today/1W/1M/3M/6M) from daily adjusted-close
    history, and
  * per-ticker news.

No fundamentals are ever fetched here — those come from the Morningstar export.

The class is Streamlit-free so it can be unit-tested; caching lives in
``data/service.py``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests

BASE_V3 = "https://financialmodelingprep.com/api/v3"
BASE_V4 = "https://financialmodelingprep.com/api/v4"
DEFAULT_TIMEOUT = 15


def fmp_error_text(data: Any) -> Optional[str]:
    """Return FMP's error/usage message if ``data`` is an error object.

    FMP returns HTTP 200 with a JSON object like ``{"Error Message": "..."}``
    (or an "Exclusive Endpoint"/usage notice) when a key is invalid, an endpoint
    is plan-gated, or a limit is hit. Detect those so the reason is never lost.
    """
    if isinstance(data, dict):
        for k in ("Error Message", "error", "message", "Information"):
            if k in data and isinstance(data[k], str):
                return data[k]
    return None


class FMPError(RuntimeError):
    """Raised when the API key is missing."""


class FMPClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
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
        back-off. When ``return_errors`` is True, an FMP error/usage object
        (HTTP 200 with ``{"Error Message": ...}``) is returned as-is so callers
        can surface the reason instead of getting a silent None.
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
            if not resp.ok:
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            if return_errors and fmp_error_text(data):
                return data
            if data in ({}, []):
                return None
            return data
        return None

    def probe(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Diagnostics: GET ``url`` and report status, error text, and shape.

        Returns ``{ok, status, error, empty, kind, keys, sample}`` without
        raising — used by the Settings → Data diagnostics panel so the real
        reason a call fails (bad key / plan-gated / rate-limited / wrong shape)
        is visible verbatim.
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
            out["error"] = err  # verbatim plan-gated / bad-key / usage message
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

    # ------------------------------------------------------------------
    # Daily history (one fetch per ticker; momentum windows sliced locally)
    # ------------------------------------------------------------------
    def historical(
        self, ticker: str, from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the historical rows, or [] (errors are swallowed here).

        Prefer :meth:`historical_result` when you need the failure reason.
        """
        return self.historical_result(ticker, from_date, to_date)["rows"]

    def historical_result(
        self, ticker: str, from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return ``{rows, error}``: parsed history rows + any FMP error text.

        ``error`` is set (and ``rows`` empty) when FMP returns a plan-gated /
        bad-key / usage object so callers can fall back to Finnhub and show why.
        """
        params: Dict[str, Any] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._get(f"{BASE_V3}/historical-price-full/{ticker}", params,
                         return_errors=True)
        err = fmp_error_text(data)
        if err:
            return {"rows": [], "error": err}
        if isinstance(data, dict):
            return {"rows": data.get("historical", []) or [], "error": None}
        return {"rows": [], "error": None}

    # ------------------------------------------------------------------
    # Quotes (for "Today" change + intraday last price)
    # ------------------------------------------------------------------
    def quotes_batch(self, tickers: List[str], batch_size: int = 50
                     ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        tickers = [t for t in tickers if t]
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            data = self._get(f"{BASE_V3}/quote/{','.join(chunk)}")
            if isinstance(data, list):
                for row in data:
                    sym = row.get("symbol")
                    if sym:
                        out[sym] = row
        return out

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------
    def stock_news(self, ticker: str, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._get(
            f"{BASE_V3}/stock_news", {"tickers": ticker, "limit": limit}
        )
        return data if isinstance(data, list) else []

    def general_news(self, limit: int = 30) -> List[Dict[str, Any]]:
        data = self._get(f"{BASE_V4}/general_news", {"page": 0, "size": limit})
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Institutional & insider disclosure
    #
    # These endpoints may not be included on every FMP plan. Callers should
    # treat an empty list as "no data / not on plan" and show a clear note
    # rather than crashing.
    # ------------------------------------------------------------------
    def institutional_holders(self, ticker: str) -> List[Dict[str, Any]]:
        """Top 13F institutional holders (v3/institutional-holder)."""
        data = self._get(f"{BASE_V3}/institutional-holder/{ticker}")
        return data if isinstance(data, list) else []

    def insider_trades(self, ticker: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent Form 4 insider transactions (v4/insider-trading)."""
        data = self._get(f"{BASE_V4}/insider-trading",
                         {"symbol": ticker, "page": 0, "limit": limit})
        return data if isinstance(data, list) else []
