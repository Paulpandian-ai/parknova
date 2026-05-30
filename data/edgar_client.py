"""SEC EDGAR client — free, authoritative filings data (read-only).

Resolves ticker -> CIK via the public ``company_tickers.json`` map, then fetches
recent filings from the submissions API. SEC **requires** a descriptive
User-Agent on every request and asks clients to stay under 10 req/s; we cache
aggressively (the company map and per-CIK submissions for a day) so normal use
makes very few calls.

Streamlit-free; caching wrappers live in ``data/service.py``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

# A descriptive UA is mandatory — SEC rejects requests without one. The contact
# string is intentionally generic; override via FMPClient-style config if needed.
USER_AGENT = "ParkNova research contact@parknova.app"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
TIMEOUT = 15

# Form-type groups for badge styling in the UI.
FORM_GROUPS = {
    "financials": {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"},
    "material": {"8-K", "8-K/A", "6-K"},
    "offering": {"S-1", "S-1/A", "424B1", "424B2", "424B3", "424B4", "424B5",
                 "S-3", "S-3/A", "F-1"},
    "stake": {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"},
    "insider": {"4", "3", "5"},
}


class EDGARClient:
    def __init__(self, user_agent: str = USER_AGENT, timeout: int = TIMEOUT):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent,
                                     "Accept-Encoding": "gzip, deflate"})
        self.timeout = timeout

    def _get(self, url: str) -> Any:
        for attempt in range(2):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return None
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(1.0)  # respect SEC rate limit
                    continue
                return None
            if not resp.ok:
                return None
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------
    def ticker_cik_map(self) -> Dict[str, str]:
        """Return a {TICKER: zero-padded-10-digit-CIK} map (empty on failure)."""
        data = self._get(TICKERS_URL)
        out: Dict[str, str] = {}
        if isinstance(data, dict):
            for row in data.values():
                tk = str(row.get("ticker", "")).upper().strip()
                cik = row.get("cik_str")
                if tk and cik is not None:
                    out[tk] = str(int(cik)).zfill(10)
        return out

    def recent_filings(self, cik10: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent filings for a zero-padded CIK.

        Each item: ``{form, filingDate, accessionNumber, primaryDocument, url}``.
        """
        data = self._get(SUBMISSIONS_URL.format(cik10=cik10))
        if not isinstance(data, dict):
            return []
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accns = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        cik_int = int(cik10)
        out: List[Dict[str, Any]] = []
        for i in range(min(len(forms), len(dates), len(accns))):
            accn = accns[i]
            accn_nodash = accn.replace("-", "")
            doc = docs[i] if i < len(docs) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                   f"{accn_nodash}/{doc}") if doc else (
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                   f"&CIK={cik10}&type=&dateb=&owner=include&count=40")
            out.append({
                "form": forms[i],
                "filingDate": dates[i],
                "accessionNumber": accn,
                "primaryDocument": doc,
                "url": url,
            })
            if len(out) >= limit:
                break
        return out


def classify_form(form: str) -> Optional[str]:
    """Return the badge group for a form type ('financials'/'material'/...)."""
    f = (form or "").strip()
    for group, forms in FORM_GROUPS.items():
        if f in forms:
            return group
    return None
