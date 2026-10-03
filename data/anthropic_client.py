"""Optional Anthropic LLM narrative for the News & Filings view.

Strictly opt-in: only used when ``ANTHROPIC_API_KEY`` is set AND the user enables
the 'AI summary' toggle. Uses the Messages API directly via ``requests`` so we
don't add an SDK dependency. Never commits keys; failures degrade to None and the
UI falls back to the deterministic summary.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests

from core.config import get_secret

API_URL = "https://api.anthropic.com/v1/messages"
# (connect, read). A 60k-char filing with a 2k-token answer can take well over
# a minute on Sonnet, so the read timeout must be generous.
TIMEOUT = (10, 240)

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
    return bool(get_secret("ANTHROPIC_API_KEY"))


def _api_error(resp: requests.Response) -> str:
    """Human-readable reason from a non-2xx Messages API response."""
    try:
        err = (resp.json() or {}).get("error") or {}
        etype, msg = err.get("type"), err.get("message")
    except ValueError:
        etype, msg = None, None
    detail = f"{etype}: {msg}" if etype and msg else (msg or resp.text[:200])
    return f"Anthropic API returned HTTP {resp.status_code} ({detail})"


def _call_messages(prompt: str, model: str, max_tokens: int
                   ) -> Tuple[Optional[str], Optional[Dict[str, int]],
                              Optional[str]]:
    """POST one user message to the Messages API.

    Returns ``(text, usage, error)``. ``usage`` is ``{input_tokens,
    output_tokens}`` when provided. On failure ``text`` is None and ``error``
    explains why (missing key, HTTP status + API message, timeout, refusal).
    """
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        return None, None, "ANTHROPIC_API_KEY is not set."
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
            return None, None, _api_error(resp)
        data = resp.json()
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts
                       if p.get("type") == "text").strip()
        usage = data.get("usage") or {}
        usage_out = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        } if usage else None
        if not text:
            return None, usage_out, (
                f"Model returned no text (stop_reason: "
                f"{data.get('stop_reason') or 'unknown'}).")
        return text, usage_out, None
    except requests.Timeout:
        return None, None, (
            f"Request to Anthropic timed out after {TIMEOUT[1]}s. Try the "
            "Haiku model or retry.")
    except requests.RequestException as exc:
        return None, None, f"Network error calling Anthropic: {exc}"
    except ValueError:
        return None, None, "Could not parse the Anthropic API response."


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
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        return None
    context = _format_context(ticker, name, news, filings, insider_summary,
                              institutional)
    prompt = (
        "Summarize the news flow, recent SEC filings, and institutional/insider "
        f"activity for {ticker} in 4-6 sentences. Be factual, cite filing types "
        "and dates, flag anything an investor should note. Do not give buy/sell "
        "advice.\n\n" + context)
    text, _, _ = _call_messages(prompt, MODEL, max_tokens=400)
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
    out, usage, err = _call_messages(prompt, model, max_tokens=1024)
    return {"text": out, "usage": usage, "model": model, "error": err}


def equity_research_filing(
    ticker: str,
    name: str,
    form: str,
    filing_date: str,
    text: str,
    truncated: bool = False,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Deep structured filing analysis for the Equity Research tab.

    Produces a 7-section report (Overview / Financial Highlights / Business
    Findings / Risk Factors / Forward-Looking Statements / Red Flags / Net
    Assessment). Returns the same ``{text, usage, model}`` shape as
    :func:`analyze_filing`.
    """
    from core.equity_research import filing_research_prompt
    prompt = filing_research_prompt(ticker, name, form, filing_date, text, truncated)
    out, usage, err = _call_messages(prompt, model, max_tokens=4000)
    return {"text": out, "usage": usage, "model": model, "error": err}


def equity_research_web(
    ticker: str,
    name: str,
    question: str,
    snippets: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Synthesize web search snippets into a factual answer for ``question``.

    Returns ``{text, usage, model}``.
    """
    from core.equity_research import web_research_prompt
    prompt = web_research_prompt(ticker, name, question, snippets)
    out, usage, err = _call_messages(prompt, model, max_tokens=800)
    return {"text": out, "usage": usage, "model": model, "error": err}


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
    out, usage, err = _call_messages(prompt, model, max_tokens=512)
    return {"text": out, "usage": usage, "model": model, "error": err}
