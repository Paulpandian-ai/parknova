"""DuckDuckGo web search client — no API key required.

Uses the public HTML search endpoint. Results are cached in ``data/service.py``
at a 1-hour TTL so repeated questions on the same query don't hammer DDG.
Streamlit-free; caching wrappers live in ``data/service.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("parknova.web_search")

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_TIMEOUT = 12
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ParkNova/1.0; research tool; "
        "contact@parknova.app)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class WebSearchClient:
    """Thin DuckDuckGo HTML search scraper.

    Each call makes at most two HTTP requests (retry on 429). Returns a list of
    ``{title, url, snippet}`` dicts, empty on any failure.
    """

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Run a web search and return up to ``max_results`` results."""
        if not query or not query.strip():
            return []
        for attempt in range(2):
            try:
                resp = self._session.post(
                    _DDG_HTML_URL,
                    data={"q": query.strip()},
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.debug("DDG search network error (%s): %s", query, exc)
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return []
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                return []
            if not resp.ok:
                logger.debug("DDG search HTTP %s for %r", resp.status_code, query)
                return []
            return _parse_results(resp.text, max_results)
        return []


def _parse_results(html: str, max_results: int) -> List[Dict[str, Any]]:
    """Extract search result cards from DDG HTML."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return []

    results: List[Dict[str, Any]] = []

    # DDG HTML result containers
    for div in soup.select(".result, .web-result"):
        title_el = div.select_one(".result__a, .result-title a")
        snippet_el = div.select_one(".result__snippet, .result-snippet")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        # DDG wraps redirect URLs; extract the actual destination from href params
        raw_href = title_el.get("href", "")
        url = _clean_ddg_url(raw_href)
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


def _clean_ddg_url(href: str) -> str:
    """Strip DDG redirect wrappers; return the destination URL."""
    if not href:
        return ""
    # DDG often uses //duckduckgo.com/l/?uddg=<encoded>
    if "uddg=" in href:
        try:
            from urllib.parse import parse_qs, urlparse, unquote
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "uddg" in params:
                return unquote(params["uddg"][0])
        except Exception:
            pass
    return href
