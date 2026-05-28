"""Assemble the fundamentals frame for the universe.

Pulls from quote, key-metrics-ttm, ratios-ttm, profile and the annual income
statement, then maps a defensive set of candidate field names to a single tidy
numeric frame. Unavailable metrics stay ``None`` (rendered as "—" later) rather
than a misleading 0 — important for the unprofitable / data-sparse AI names.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from data import service

# Display order of fundamental columns (key -> friendly label).
FUNDAMENTAL_COLS: Dict[str, str] = {
    "market_cap": "Market Cap",
    "price": "Price",
    "pe": "P/E",
    "fwd_pe": "Fwd P/E",
    "ps": "P/S",
    "ev_ebitda": "EV/EBITDA",
    "gross_margin": "Gross Mgn",
    "operating_margin": "Op Mgn",
    "net_margin": "Net Mgn",
    "rev_growth": "Rev Growth",
    "roe": "ROE",
    "debt_equity": "Debt/Eq",
    "fcf_margin": "FCF Mgn",
    "beta": "Beta",
}

# Which columns are multiples (24.5x), margins/percent (34.2%) or raw money.
MULTIPLE_COLS = {"pe", "fwd_pe", "ps", "ev_ebitda", "debt_equity", "beta"}
PERCENT_COLS = {
    "gross_margin",
    "operating_margin",
    "net_margin",
    "rev_growth",
    "roe",
    "fcf_margin",
}


def _num(val: Any) -> Optional[float]:
    """Coerce to float, treating None/NaN/non-numeric as None."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _pick(d: Optional[dict], *keys: str) -> Optional[float]:
    """Return the first present, numeric value among ``keys`` in dict ``d``."""
    if not d:
        return None
    for k in keys:
        if k in d:
            v = _num(d[k])
            if v is not None:
                return v
    return None


def _revenue_growth(income: List[dict]) -> Optional[float]:
    """YoY revenue growth from the two most recent annual statements."""
    if not income or len(income) < 2:
        return None
    cur = _num(income[0].get("revenue"))
    prev = _num(income[1].get("revenue"))
    if cur is None or prev in (None, 0):
        return None
    return cur / prev - 1.0


def _fcf_margin(km: Optional[dict], income: List[dict]) -> Optional[float]:
    """FCF margin: prefer a direct field, else derive from FCF/share x shares / rev."""
    direct = _pick(km, "freeCashFlowMarginTTM", "fcfMarginTTM")
    if direct is not None:
        return direct
    # Derive: freeCashFlowPerShare * weightedShares / revenue (best-effort).
    fcf_ps = _pick(km, "freeCashFlowPerShareTTM")
    rev = _num(income[0].get("revenue")) if income else None
    shares = _num(income[0].get("weightedAverageShsOutDil")) if income else None
    if fcf_ps is not None and rev not in (None, 0) and shares not in (None, 0):
        return (fcf_ps * shares) / rev
    return None


def build_row(ticker: str, company: str, bucket: str) -> Dict[str, Any]:
    """Build a single fundamentals row for one ticker (all cached fetches)."""
    quote = service.get_quote(ticker) or {}
    profile = service.get_profile(ticker) or {}
    km = service.get_key_metrics_ttm(ticker) or {}
    ratios = service.get_ratios_ttm(ticker) or {}
    income = service.get_income_statement(ticker, limit=5) or []

    has_data = bool(quote or profile or km or ratios)

    market_cap = _pick(quote, "marketCap") or _pick(profile, "mktCap")
    price = _pick(quote, "price") or _pick(profile, "price")
    pe = _pick(quote, "pe") or _pick(ratios, "priceEarningsRatioTTM") or _pick(
        km, "peRatioTTM"
    )
    fwd_pe = _pick(quote, "forwardPE") or _pick(km, "forwardPETTM", "forwardPE")
    ps = _pick(ratios, "priceToSalesRatioTTM") or _pick(km, "priceToSalesRatioTTM")
    ev_ebitda = _pick(
        km, "enterpriseValueOverEBITDATTM", "evToEBITDATTM"
    ) or _pick(ratios, "enterpriseValueMultipleTTM")
    gross_margin = _pick(ratios, "grossProfitMarginTTM")
    operating_margin = _pick(ratios, "operatingProfitMarginTTM")
    net_margin = _pick(ratios, "netProfitMarginTTM")
    roe = _pick(ratios, "returnOnEquityTTM") or _pick(km, "roeTTM")
    debt_equity = _pick(ratios, "debtEquityRatioTTM") or _pick(km, "debtToEquityTTM")
    beta = _pick(profile, "beta")
    rev_growth = _revenue_growth(income)
    fcf_margin = _fcf_margin(km, income)

    return {
        "ticker": ticker,
        "company": company,
        "bucket": bucket,
        "has_data": has_data,
        "market_cap": market_cap,
        "price": price,
        "pe": pe,
        "fwd_pe": fwd_pe,
        "ps": ps,
        "ev_ebitda": ev_ebitda,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "rev_growth": rev_growth,
        "roe": roe,
        "debt_equity": debt_equity,
        "fcf_margin": fcf_margin,
        "beta": beta,
    }


def build_fundamentals_frame(
    universe: pd.DataFrame, progress: Optional[Callable[[float, str], None]] = None
) -> pd.DataFrame:
    """Build the fundamentals frame for the (already filtered) universe.

    ``progress`` is an optional callback (fraction, label) for a progress bar.
    """
    rows: List[Dict[str, Any]] = []
    total = len(universe)
    for i, (_, r) in enumerate(universe.iterrows()):
        rows.append(build_row(r["ticker"], r["company"], r["bucket"]))
        if progress and total:
            progress((i + 1) / total, r["ticker"])
    return pd.DataFrame(rows)
