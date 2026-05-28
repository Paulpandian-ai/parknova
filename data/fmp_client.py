"""Financial Modeling Prep (FMP) API wrapper.

The :class:`FMPClient` holds a configured ``requests.Session`` and knows how to
talk to the handful of FMP endpoints this app needs. It is deliberately free of
any Streamlit dependency so it can be unit-tested in isolation.

The Streamlit caching layer lives in the module-level helper functions at the
bottom of this file (``get_quote``, ``get_quotes_batch``, ``get_historical``,
``get_profile`` ...). Those wrap a single cached client instance and apply the
appropriate ``ttl`` so we stay within FMP rate limits and keep the UI snappy.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE_URL = "https://financialmodelingprep.com/api/v3"
DEFAULT_TIMEOUT = 15  # seconds
BATCH_SIZE = 50  # tickers per batch quote request


class FMPError(RuntimeError):
    """Raised for unrecoverable FMP problems (e.g. missing API key)."""


class FMPClient:
    """Thin, defensive wrapper around the FMP REST API."""

    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise FMPError(
                "FMP_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AI-Equities-Tracker/1.0"})

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Perform a GET request and return parsed JSON.

        Handles timeouts, HTTP errors, empty bodies and a single 429 retry with
        back-off. On any unrecoverable failure it returns ``None`` (callers then
        decide how to degrade gracefully) rather than raising.
        """
        params = dict(params or {})
        params["apikey"] = self.api_key
        url = f"{BASE_URL}/{path.lstrip('/')}"

        for attempt in range(2):  # one initial try + one retry on 429
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException:
                # Network error / timeout — back off once then give up.
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                return None

            if resp.status_code == 429:
                # Rate limited — back off and retry once.
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

            # FMP returns {} or [] when it has nothing for a symbol.
            if data in ({}, []):
                return None
            return data

        return None

    # ------------------------------------------------------------------
    # Endpoint methods
    # ------------------------------------------------------------------
    def quote(self, ticker: str) -> Optional[Dict[str, Any]]:
        data = self._get(f"quote/{ticker}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def quotes_batch(self, tickers: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch many quotes at once, chunked into groups of ``BATCH_SIZE``.

        Returns a mapping of ``ticker -> quote dict`` for whatever FMP returned.
        Missing tickers are simply absent from the result.
        """
        tickers = [t for t in tickers if t]
        out: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(tickers), BATCH_SIZE):
            chunk = tickers[i : i + BATCH_SIZE]
            data = self._get(f"quote/{','.join(chunk)}")
            if isinstance(data, list):
                for row in data:
                    sym = row.get("symbol")
                    if sym:
                        out[sym] = row
        return out

    def historical(
        self, ticker: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return the raw list of daily history rows (newest first per FMP)."""
        params: Dict[str, Any] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._get(f"historical-price-full/{ticker}", params)
        if isinstance(data, dict):
            return data.get("historical", []) or []
        return []

    def profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        data = self._get(f"profile/{ticker}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def key_metrics_ttm(self, ticker: str) -> Optional[Dict[str, Any]]:
        data = self._get(f"key-metrics-ttm/{ticker}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def ratios_ttm(self, ticker: str) -> Optional[Dict[str, Any]]:
        data = self._get(f"ratios-ttm/{ticker}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def income_statement(self, ticker: str, limit: int = 5) -> List[Dict[str, Any]]:
        data = self._get(
            f"income-statement/{ticker}", {"period": "annual", "limit": limit}
        )
        if isinstance(data, list):
            return data
        return []
