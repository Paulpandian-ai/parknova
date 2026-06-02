"""Finnhub client — near-real-time US quotes + daily candles (history fallback).

Free tier (finnhub.io → free API key): real-time-ish quotes and daily candles.
Read ``FINNHUB_API_KEY`` from the environment (dotenv or platform env var); never
commit it. Free tier is ~60 calls/min, so callers cache aggressively and only
poll the visible/selected ticker(s).

Streamlit-free so it can be unit-tested; caching lives in ``data/service.py``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from core.config import get_secret

BASE = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 12


def has_finnhub_key() -> bool:
    return bool(get_secret("FINNHUB_API_KEY"))


class FinnhubClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or get_secret("FINNHUB_API_KEY")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ParkNova/1.0"})

    def available(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """GET ``path`` -> ``{data, error, status}``; one 429 retry w/ backoff.

        Never raises. ``error`` carries a human reason (no key / rate-limited /
        403 / network) so the UI can show why a price is missing.
        """
        if not self.api_key:
            return {"data": None, "error": "FINNHUB_API_KEY not set", "status": None}
        params = dict(params)
        params["token"] = self.api_key
        url = f"{BASE}/{path.lstrip('/')}"
        for attempt in range(2):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return {"data": None, "error": f"request failed: {exc}",
                        "status": None}
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(1.5)  # respect ~60/min limit
                    continue
                return {"data": None, "error": "rate-limited, retrying shortly",
                        "status": 429}
            if resp.status_code == 403:
                return {"data": None, "error": "access denied (403 — not on free "
                        "tier for this symbol/endpoint)", "status": 403}
            if not resp.ok:
                return {"data": None, "error": f"HTTP {resp.status_code}",
                        "status": resp.status_code}
            try:
                data = resp.json()
            except ValueError:
                return {"data": None, "error": "non-JSON response",
                        "status": resp.status_code}
            return {"data": data, "error": None, "status": resp.status_code}
        return {"data": None, "error": "unknown", "status": None}

    # ------------------------------------------------------------------
    # Real-time-ish quote
    # ------------------------------------------------------------------
    def quote(self, ticker: str) -> Dict[str, Any]:
        """Return ``{price, change, pct, prev_close, ts, error}``.

        Finnhub /quote fields: c (current), d (change), dp (percent change),
        pc (prev close), t (unix ts). All-zero c with t==0 means "no data".
        """
        r = self._get("quote", {"symbol": ticker})
        if r["error"]:
            return {"price": None, "change": None, "pct": None,
                    "prev_close": None, "ts": None, "error": r["error"]}
        d = r["data"] or {}
        c = d.get("c")
        # Finnhub returns c=0,t=0 for unknown symbols.
        if not c and not d.get("t"):
            return {"price": None, "change": None, "pct": None,
                    "prev_close": None, "ts": None, "error": "no data for symbol"}
        return {"price": c, "change": d.get("d"), "pct": d.get("dp"),
                "prev_close": d.get("pc"), "ts": d.get("t"), "error": None}

    # ------------------------------------------------------------------
    # Daily candles (history fallback for FMP)
    # ------------------------------------------------------------------
    def candles(self, ticker: str, from_unix: int, to_unix: int
                ) -> Dict[str, Any]:
        """Return ``{closes, timestamps, error}`` for daily resolution.

        Finnhub /stock/candle fields: c (closes), t (timestamps), s (status:
        "ok"/"no_data"). On free tier some symbols return no_data/403 — handled.
        """
        r = self._get("stock/candle", {"symbol": ticker, "resolution": "D",
                                        "from": int(from_unix), "to": int(to_unix)})
        if r["error"]:
            return {"closes": [], "timestamps": [], "error": r["error"]}
        d = r["data"] or {}
        if d.get("s") != "ok":
            return {"closes": [], "timestamps": [],
                    "error": f"no candle data (status: {d.get('s', 'unknown')})"}
        return {"closes": d.get("c") or [], "timestamps": d.get("t") or [],
                "error": None}
