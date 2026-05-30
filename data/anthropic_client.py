"""Optional Anthropic LLM narrative for the News & Filings view.

Strictly opt-in: only used when ``ANTHROPIC_API_KEY`` is set AND the user enables
the 'AI summary' toggle. Uses the Messages API directly via ``requests`` so we
don't add an SDK dependency. Never commits keys; failures degrade to None and the
UI falls back to the deterministic summary.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"
TIMEOUT = 30


def has_anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _format_context(ticker: str, name: str, news: List[dict], filings: List[dict],
                    insider_summary: Dict[str, Any],
                    institutional: List[dict]) -> str:
    lines = [f"Company: {name} ({ticker})", "", "Recent news headlines:"]
    for n in news[:12]:
        lines.append(f"- [{n.get('site', '?')}, {n.get('publishedDate', '?')}] "
                     f"{n.get('title', '')}"
                     + (f" (sentiment: {n['sentiment']})" if n.get("sentiment") else ""))
    lines.append("")
    lines.append("Recent SEC filings:")
    for f in filings[:12]:
        lines.append(f"- {f.get('form', '?')} on {f.get('filingDate', '?')}")
    lines.append("")
    ins = insider_summary or {}
    lines.append(
        f"Insider activity (last {ins.get('days', 90)}d): "
        f"{ins.get('n_buy', 0)} buys (${ins.get('buy', 0):,.0f}), "
        f"{ins.get('n_sell', 0)} sells (${ins.get('sell', 0):,.0f}), "
        f"net ${ins.get('net', 0):,.0f} ({ins.get('direction', 'n/a')}).")
    if institutional:
        lines.append("Top institutional position changes:")
        for h in institutional:
            ch = h.get("change")
            lines.append(f"- {h.get('holder', '?')}: change "
                         f"{ch if ch is not None else 'n/a'} shares")
    return "\n".join(lines)


def generate_summary(ticker: str, name: str, news: List[dict],
                     filings: List[dict], insider_summary: Dict[str, Any],
                     institutional: List[dict]) -> Optional[str]:
    """Return a 4-6 sentence factual narrative, or None on any failure."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    context = _format_context(ticker, name, news, filings, insider_summary,
                              institutional)
    prompt = (
        "Summarize the news flow, recent SEC filings, and institutional/insider "
        f"activity for {ticker} in 4-6 sentences. Be factual, cite filing types "
        "and dates, flag anything an investor should note. Do not give buy/sell "
        "advice.\n\n" + context)
    try:
        resp = requests.post(
            API_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=TIMEOUT,
        )
        if not resp.ok:
            return None
        data = resp.json()
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return text.strip() or None
    except (requests.RequestException, ValueError):
        return None
