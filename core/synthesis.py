"""Deterministic 'At a glance' synthesis for the News & Filings view.

Pure functions over already-fetched news / filings / insider / institutional
data plus the Morningstar row. No network, no LLM — this is the always-available
default summary. The optional LLM narrative lives in ``data/anthropic_client.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

LEADING_SOURCES = {
    "reuters", "bloomberg", "wsj", "the wall street journal", "cnbc",
    "financial times", "ft", "barron's", "barrons", "associated press", "ap",
}


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _days_ago(s: str) -> Optional[int]:
    dt = _parse_dt(s)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).days


def is_leading_source(site: object) -> bool:
    return str(site or "").strip().lower() in LEADING_SOURCES


def news_sentiment_tally(news: List[dict]) -> Dict[str, int]:
    """Count positive / negative / neutral from FMP sentiment fields."""
    pos = neg = neu = 0
    for n in news:
        s = str(n.get("sentiment") or "").strip().lower()
        if s == "positive":
            pos += 1
        elif s == "negative":
            neg += 1
        elif s:
            neu += 1
    return {"positive": pos, "negative": neg, "neutral": neu, "total": len(news)}


def filings_tally(filings: List[dict]) -> Dict[str, Any]:
    """Counts in last 30/90 days and a per-form breakdown."""
    last30 = last90 = 0
    by_form: Dict[str, int] = {}
    for f in filings:
        d = _days_ago(f.get("filingDate", ""))
        if d is not None and d <= 30:
            last30 += 1
        if d is not None and d <= 90:
            last90 += 1
        form = f.get("form", "?")
        by_form[form] = by_form.get(form, 0) + 1
    return {"last30": last30, "last90": last90, "by_form": by_form}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def insider_net(trades: List[dict], days: int = 90) -> Dict[str, Any]:
    """Net insider buy/sell value over the trailing ``days``.

    FMP Form-4 rows vary; we read ``transactionType``/``acquisitionOrDisposition``
    plus ``securitiesTransacted`` * ``price``. A/D = 'A' (acquired/buy) or 'D'
    (disposed/sell). Returns dollar buys, sells, net and a direction label.
    """
    buy = sell = 0.0
    n_buy = n_sell = 0
    for t in trades:
        d = _days_ago(t.get("transactionDate") or t.get("filingDate") or "")
        if d is None or d > days:
            continue
        shares = _num(t.get("securitiesTransacted")) or 0.0
        price = _num(t.get("price")) or 0.0
        value = abs(shares * price)
        ad = str(t.get("acquisitionOrDisposition") or "").upper()
        ttype = str(t.get("transactionType") or "").upper()
        is_buy = ad == "A" or ttype.startswith("P") or "BUY" in ttype
        is_sell = ad == "D" or ttype.startswith("S") or "SELL" in ttype
        if is_buy and not is_sell:
            buy += value
            n_buy += 1
        elif is_sell:
            sell += value
            n_sell += 1
    net = buy - sell
    direction = "neutral"
    if net > 0 and buy > 0:
        direction = "net buying"
    elif net < 0 and sell > 0:
        direction = "net selling"
    return {"buy": buy, "sell": sell, "net": net, "n_buy": n_buy,
            "n_sell": n_sell, "direction": direction, "days": days}


def institutional_changes(holders: List[dict], top: int = 3) -> List[Dict[str, Any]]:
    """Top position changes (by absolute share change) if the field is present."""
    rows = []
    for h in holders:
        change = _num(h.get("change"))
        rows.append({
            "holder": h.get("holder") or h.get("investorName") or "?",
            "shares": _num(h.get("shares")),
            "change": change,
        })
    rows.sort(key=lambda r: abs(r["change"]) if r["change"] is not None else -1,
              reverse=True)
    return rows[:top]


def build_at_a_glance(ms_row: pd.Series, news: List[dict], filings: List[dict],
                      insider: List[dict], holders: List[dict]) -> Dict[str, Any]:
    """Assemble all deterministic summary signals into one dict for the cards."""
    return {
        "sentiment": news_sentiment_tally(news),
        "filings": filings_tally(filings),
        "insider": insider_net(insider, days=90),
        "institutional": institutional_changes(holders, top=3),
        "upside_pct": _num(ms_row.get("upside_pct")),
        "rating": _num(ms_row.get("Morningstar Rating for Stocks")),
    }
