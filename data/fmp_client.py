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
    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET returning parsed JSON, or None on any failure.

        Handles timeouts/HTTP errors/empty bodies and retries once on 429 with
        back-off.
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
            if data in ({}, []):
                return None
            return data
        return None

    # ------------------------------------------------------------------
    # Daily history (one fetch per ticker; momentum windows sliced locally)
    # ------------------------------------------------------------------
    def historical(
        self, ticker: str, from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._get(f"{BASE_V3}/historical-price-full/{ticker}", params)
        if isinstance(data, dict):
            return data.get("historical", []) or []
        return []

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
