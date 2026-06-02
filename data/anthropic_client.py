"""Optional Anthropic LLM narrative for the News & Filings view.

Strictly opt-in: only used when ``ANTHROPIC_API_KEY`` is set AND the user enables
the 'AI summary' toggle. Uses the Messages API directly via ``requests`` so we
don't add an SDK dependency. Never commits keys; failures degrade to None and the
UI falls back to the deterministic summary.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import requests

API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT = 60

# Model registry (configurable, not hardcoded in three places).
# Default to the cheapest/fastest model for on-demand filing analysis.
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
DEFAULT_MODEL = MODEL_HAIKU
# Friendly label -> model id, for the UI selector.
MODEL_CHOICES = {
    "Haiku (fast, cheap)": MODEL_HAIKU,
    "Sonnet (deeper read)": MODEL_SONNET,
}
# Model used by the older News & Filings narrative summary.
MODEL = MODEL_SONNET


def has_anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _call_messages(prompt: str, model: str, max_tokens: int
                   ) -> Tuple[Optional[str], Optional[Dict[str, int]]]:
    """POST one user message to the Messages API.

    Returns ``(text, usage)`` where usage is ``{input_tokens, output_tokens}``
    when the API provides it. On any failure returns ``(None, None)``.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None, None
    try:
        resp = requests.post(
            API_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=TIMEOUT,
        )
        if not resp.ok:
            return None, None
        data = resp.json()
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts
                       if p.get("type") == "text").strip()
        usage = data.get("usage") or {}
        usage_out = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        } if usage else None
        return (text or None), usage_out
    except (requests.RequestException, ValueError):
        return None, None


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
    text, _ = _call_messages(prompt, MODEL, max_tokens=400)
    return text


def analyze_filing(form: str, filing_date: str, ticker: str, text: str,
                   model: str = DEFAULT_MODEL, truncated: bool = False
                   ) -> Dict[str, Any]:
    """Analyze a single SEC filing's (already-trimmed) text.

    Returns ``{text, usage, model}``. ``text`` is None on failure. ``truncated``
    tells the model to disclose truncation in its output.
    """
    trunc_note = ("\n\nNOTE: The filing text below was truncated/section-"
                  "extracted for length; mention this in your summary."
                  if truncated else "")
    prompt = (
        f"You are analyzing a {form} filed {filing_date} by {ticker}. "
        "Summarize for an investor in this structure:\n"
        "(1) What this filing is and why it was filed;\n"
        "(2) Material facts — events, transactions, figures, guidance changes, "
        "with specific numbers and dates from the text;\n"
        "(3) Notable risks or red flags;\n"
        "(4) Net read in one sentence.\n"
        "Be factual and cite figures from the text. Do NOT give buy/sell advice. "
        "If the text is truncated, say so." + trunc_note
        + "\n\n--- FILING TEXT ---\n" + (text or ""))
    out, usage = _call_messages(prompt, model, max_tokens=1024)
    return {"text": out, "usage": usage, "model": model}


def analyze_filing_activity(ticker: str, items: List[Dict[str, Any]],
                            model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """Synthesize the last few filings (metadata + any per-filing analyses).

    ``items`` is a list of ``{form, filingDate, analysis}`` where ``analysis``
    may be a prior per-filing summary string (or empty). Returns the same shape
    as :func:`analyze_filing`.
    """
    lines = [f"Recent SEC filing activity for {ticker}:"]
    for it in items:
        lines.append(f"\n[{it.get('form', '?')} filed {it.get('filingDate', '?')}]")
        prior = (it.get("analysis") or "").strip()
        if prior:
            lines.append(prior[:1500])
        else:
            lines.append("(no individual analysis available)")
    prompt = (
        f"Below are {ticker}'s most recent SEC filings, with prior per-filing "
        "analyses where available. In one short paragraph (3-5 sentences), "
        "describe what has been happening at the company based on this filing "
        "activity. Be factual, cite filing types and dates, flag anything "
        "notable. Do NOT give buy/sell advice.\n\n" + "\n".join(lines))
    out, usage = _call_messages(prompt, model, max_tokens=512)
    return {"text": out, "usage": usage, "model": model}
