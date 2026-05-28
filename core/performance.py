"""Window-return computation from a single daily price series.

Each ticker's full adjusted-close history is fetched once; every window return is
sliced from that one series using nearest-prior ("asof") logic so we never assume
an exact calendar date exists as a trading day.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

# Ordered windows. "Today" comes from the live quote; the rest are computed from
# history. "Max" uses the earliest available date.
WINDOWS = ["Today", "1W", "1M", "3M", "6M", "1Y", "5Y", "Max"]
RETURN_COLS = WINDOWS  # column labels used throughout the UI

# Calendar offsets for each historical window, anchored on the latest trade date.
_OFFSETS = {
    "1W": pd.DateOffset(weeks=1),
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "5Y": pd.DateOffset(years=5),
}


def compute_window_returns(
    series: pd.Series, today_pct: Optional[float] = None
) -> Dict[str, Optional[float]]:
    """Return a dict of fractional returns (e.g. 0.123 == +12.3%) per window.

    ``series`` is an ascending date-indexed adjusted-close series.
    ``today_pct`` is FMP's ``changesPercentage`` (already a percent, e.g. 1.2).
    Missing/uncomputable windows map to ``None``.
    """
    out: Dict[str, Optional[float]] = {w: None for w in WINDOWS}

    # Today comes straight from the quote (percent -> fraction).
    if today_pct is not None and pd.notna(today_pct):
        out["Today"] = float(today_pct) / 100.0

    if series is None or series.empty:
        return out

    series = series.sort_index()
    latest_val = float(series.iloc[-1])
    latest_date = series.index[-1]
    if latest_val == 0:
        return out

    for label, offset in _OFFSETS.items():
        start_date = latest_date - offset
        if start_date < series.index[0]:
            # Not enough history for this window.
            continue
        start_val = series.asof(start_date)  # nearest value at/before start_date
        if pd.notna(start_val) and start_val:
            out[label] = latest_val / float(start_val) - 1.0

    # Max = since earliest available date.
    first_val = float(series.iloc[0])
    if first_val:
        out["Max"] = latest_val / first_val - 1.0

    return out


def latest_price(series: pd.Series) -> Optional[float]:
    if series is None or series.empty:
        return None
    return float(series.iloc[-1])


def build_performance_frame(
    universe: pd.DataFrame, progress: Optional[Callable[[float, str], None]] = None
) -> pd.DataFrame:
    """Assemble the performance frame for the (already filtered) universe.

    Uses one batched quote call for "Today" + price, then one cached history
    fetch per ticker for the remaining windows. ``progress`` is an optional
    (fraction, label) callback for the loading bar.
    """
    from data import service  # local import keeps compute_window_returns IO-free

    tickers = list(universe["ticker"])
    quotes = service.get_quotes_batch(tuple(tickers))

    rows: List[Dict] = []
    total = len(universe)
    for i, (_, meta) in enumerate(universe.iterrows()):
        ticker = meta["ticker"]
        q = quotes.get(ticker) or {}
        series = service.get_history(ticker)
        today_pct = q.get("changesPercentage")
        returns = compute_window_returns(series, today_pct=today_pct)
        price = q.get("price")
        if price is None:
            price = latest_price(series)
        has_data = bool(q) or not series.empty

        row = {
            "ticker": ticker,
            "company": meta["company"],
            "bucket": meta["bucket"],
            "group": meta["group"],
            "low_liquidity": meta["low_liquidity"],
            "price": price,
            "has_data": has_data,
        }
        row.update(returns)
        rows.append(row)
        if progress and total:
            progress((i + 1) / total, ticker)

    return pd.DataFrame(rows)


def bucket_heatmap(perf_df: pd.DataFrame) -> pd.DataFrame:
    """Median return per bucket (rows) x window (cols), as fractions."""
    if perf_df.empty:
        return pd.DataFrame(columns=RETURN_COLS)
    grouped = perf_df.groupby("bucket")[RETURN_COLS].median(numeric_only=True)
    return grouped
