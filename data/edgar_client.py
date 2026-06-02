"""SEC EDGAR client — free, authoritative filings data (read-only).

Resolves ticker -> CIK via the public ``company_tickers.json`` map, then fetches
recent filings from the submissions API. SEC **requires** a descriptive
User-Agent on every request and asks clients to stay under 10 req/s; we cache
aggressively (the company map and per-CIK submissions for a day) so normal use
makes very few calls.

Streamlit-free; caching wrappers live in ``data/service.py``.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

# A descriptive UA is mandatory — SEC rejects requests without one. The contact
# string is intentionally generic; override via FMPClient-style config if needed.
USER_AGENT = "ParkNova research contact@parknova.app"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_DOC_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}")
TIMEOUT = 15

# Trimming / cost-control limits (Feature: on-demand filing analysis).
HARD_CHAR_CAP = 60_000   # absolute cap on text sent to the LLM
TRUNCATE_FALLBACK = 40_000  # first-N fallback when section extraction fails

# Forms that are usually small enough to send whole.
SMALL_FORMS = {"8-K", "8-K/A", "6-K", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
# Large periodic forms -> extract high-value sections only.
LARGE_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"}

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

        Each item: ``{form, filingDate, accessionNumber, primaryDocument,
        primaryDocDescription, cik, url}`` where ``url`` points at the primary
        document on sec.gov (or a browse fallback when no doc is named).
        """
        data = self._get(SUBMISSIONS_URL.format(cik10=cik10))
        if not isinstance(data, dict):
            return []
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accns = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descs = recent.get("primaryDocDescription") or []
        cik_int = int(cik10)
        out: List[Dict[str, Any]] = []
        for i in range(min(len(forms), len(dates), len(accns))):
            accn = accns[i]
            doc = docs[i] if i < len(docs) else ""
            desc = descs[i] if i < len(descs) else ""
            url = build_doc_url(cik_int, accn, doc) if doc else (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik10}&type=&dateb=&owner=include&count=40")
            out.append({
                "form": forms[i],
                "filingDate": dates[i],
                "accessionNumber": accn,
                "primaryDocument": doc,
                "primaryDocDescription": desc,
                "cik": cik_int,
                "url": url,
            })
            if len(out) >= limit:
                break
        return out

    def fetch_document_text(self, cik: int, accession_number: str,
                            primary_document: str) -> str:
        """Fetch a filing's primary document and return cleaned plain text.

        Returns "" on any failure (missing doc, network error, non-OK status).
        """
        if not primary_document:
            return ""
        url = build_doc_url(cik, accession_number, primary_document)
        for attempt in range(2):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return ""
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return ""
            if not resp.ok:
                return ""
            return html_to_text(resp.text)
        return ""


def classify_form(form: str) -> Optional[str]:
    """Return the badge group for a form type ('financials'/'material'/...)."""
    f = (form or "").strip()
    for group, forms in FORM_GROUPS.items():
        if f in forms:
            return group
    return None


def build_doc_url(cik: int, accession_number: str, primary_document: str) -> str:
    """Build the EDGAR archives URL for a filing's primary document.

    ``cik`` has no leading zeros; the accession number has its dashes stripped.
    """
    accn_nodash = str(accession_number).replace("-", "")
    return ARCHIVE_DOC_URL.format(cik=int(cik), accn_nodash=accn_nodash,
                                  doc=primary_document)


def html_to_text(html: str) -> str:
    """Convert filing HTML to clean text: drop scripts/styles, collapse space.

    Uses BeautifulSoup (lxml). Falls back to a light regex strip if bs4 is
    unavailable for any reason.
    """
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "head", "title", "meta", "link"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
    except Exception:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    # Collapse whitespace; normalise non-breaking spaces.
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# High-value section headings for 10-K/10-Q extraction (case-insensitive).
_SECTION_PATTERNS = [
    r"management'?s\s+discussion\s+and\s+analysis",
    r"results\s+of\s+operations",
    r"risk\s+factors",
    r"quantitative\s+and\s+qualitative\s+disclosures",
]


def _extract_sections(text: str) -> str:
    """Pull MD&A / Results of Operations / Risk Factors blocks by heading.

    Returns concatenated sections, or "" if nothing matched. Each section runs
    from its heading to the next major "Item N." heading (or +25k chars).
    """
    if not text:
        return ""
    lowered = text.lower()
    spans: List[tuple] = []
    for pat in _SECTION_PATTERNS:
        for m in re.finditer(pat, lowered):
            start = m.start()
            # End at the next "Item N" heading after a reasonable minimum.
            nxt = re.search(r"\n\s*item\s+\d+[a-z]?\.", lowered[start + 200:])
            end = start + 200 + nxt.start() if nxt else start + 25_000
            spans.append((start, min(end, len(text))))
    if not spans:
        return ""
    # Merge overlapping spans, then concatenate in document order.
    spans.sort()
    merged: List[tuple] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return "\n\n".join(text[s:e].strip() for s, e in merged)


def trim_for_analysis(form: str, text: str) -> Dict[str, Any]:
    """Reduce filing text before the LLM call (cost control).

    * Small forms (8-K/6-K/13D/G): send the full cleaned text.
    * Large forms (10-K/10-Q...): extract MD&A/Risk Factors/Results; if that
      fails, fall back to the first ``TRUNCATE_FALLBACK`` chars.
    * Always enforce ``HARD_CHAR_CAP``.

    Returns ``{text, truncated, method}``.
    """
    text = text or ""
    f = (form or "").strip()
    method = "full"
    truncated = False

    if f in LARGE_FORMS or (f not in SMALL_FORMS and len(text) > HARD_CHAR_CAP):
        sections = _extract_sections(text)
        if sections:
            out, method = sections, "sections"
        else:
            out, method, truncated = text[:TRUNCATE_FALLBACK], "truncated", True
    else:
        out = text

    if len(out) > HARD_CHAR_CAP:
        out = out[:HARD_CHAR_CAP]
        truncated = True

    return {"text": out, "truncated": truncated, "method": method}
