"""Merge Morningstar trailing returns with live FMP short-window momentum.

Live windows (Today/1W/1M/3M/6M) are computed from a single daily adjusted-close
series per ticker using nearest-prior ("asof") slicing — no per-window requests.
Morningstar provides YTD/1Y/3Y/5Y (already in percent; converted to fractions
here so the whole performance frame uses one convention: fractions, e.g. 0.123).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

# Live windows (from FMP) then Morningstar trailing windows.
LIVE_WINDOWS = ["Today", "1W", "1M", "3M", "6M"]
MS_WINDOWS = ["YTD", "1Y", "3Y", "5Y"]
ALL_WINDOWS = LIVE_WINDOWS + MS_WINDOWS

_MS_SOURCE = {
    "YTD": "Total Return (YTD)",
    "1Y": "Total Return (1Y)",
    "3Y": "Total Return (3Y)",
    "5Y": "Total Return (5Y)",
}

_OFFSETS = {
    "1W": pd.DateOffset(weeks=1),
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
}


def live_windows_from_series(
    series: pd.Series, today_pct: Optional[float] = None
) -> Dict[str, Optional[float]]:
    """Return live window returns as fractions. ``today_pct`` is FMP percent."""
    out: Dict[str, Optional[float]] = {w: None for w in LIVE_WINDOWS}
    if today_pct is not None and pd.notna(today_pct):
        out["Today"] = float(today_pct) / 100.0

    if series is None or series.empty:
        return out
    series = series.sort_index()
    latest = float(series.iloc[-1])
    latest_date = series.index[-1]
    if latest == 0:
        return out
    for label, off in _OFFSETS.items():
        start_date = latest_date - off
        if start_date < series.index[0]:
            continue
        start_val = series.asof(start_date)
        if pd.notna(start_val) and start_val:
            out[label] = latest / float(start_val) - 1.0
    return out


def build_performance_frame(
    ms_df: pd.DataFrame,
    progress: Optional[Callable[[float, str], None]] = None,
    live: bool = True,
) -> pd.DataFrame:
    """Build the merged performance frame for the full Morningstar universe.

    When ``live`` is True: one batched quote call (Today + intraday price) + one
    cached history fetch per ticker for the live windows. When False (e.g. no FMP
    key), the live windows are left blank and only Morningstar windows populate —
    the app still works fully for fundamentals/factors.
    """
    from data import service

    tickers = ms_df["Ticker"].tolist()
    quotes = service.get_quotes_batch(tuple(tickers)) if live else {}

    rows: List[Dict] = []
    total = len(ms_df)
    for i, (_, meta) in enumerate(ms_df.iterrows()):
        ticker = meta["Ticker"]
        q = quotes.get(ticker) or {}
        series = service.get_history(ticker) if live else pd.Series(dtype="float64")
        live_vals = live_windows_from_series(series, q.get("changesPercentage"))

        # Prefer live intraday price; fall back to Morningstar Last Price.
        price = q.get("price")
        if price is None or pd.isna(price):
            price = meta.get("Last Price")

        row = {
            "Ticker": ticker,
            "Name": meta.get("Name"),
            "Sector": meta.get("Sector"),
            "Stock Style Box": meta.get("Stock Style Box"),
            "Last Price": price,
            "has_live": not series.empty or bool(q),
        }
        row.update(live_vals)
        for w in MS_WINDOWS:
            raw = meta.get(_MS_SOURCE[w])
            row[w] = (float(raw) / 100.0) if pd.notna(raw) else np.nan

        # Momentum inputs for the factor engine (fractions).
        row["mom_3m"] = live_vals.get("3M")
        row["mom_6m"] = live_vals.get("6M")
        rows.append(row)

        if progress and total:
            progress((i + 1) / total, ticker)

    return pd.DataFrame(rows)


def sector_heatmap(perf_df: pd.DataFrame) -> pd.DataFrame:
    """Median return per sector (rows) x window (cols), fractions.

    All windows are always present as columns (NaN when a window has no data),
    so downstream rendering can rely on a stable shape.
    """
    if perf_df.empty:
        return pd.DataFrame(columns=ALL_WINDOWS)
    num = perf_df[ALL_WINDOWS].apply(pd.to_numeric, errors="coerce")
    num["Sector"] = perf_df["Sector"].values
    return num.groupby("Sector")[ALL_WINDOWS].median()


def blended_momentum_score(perf_df: pd.DataFrame) -> pd.Series:
    """Average of z-scored 1M/3M/6M live returns + 1Y Morningstar return."""
    cols = ["1M", "3M", "6M", "1Y"]
    zs = []
    for c in cols:
        if c in perf_df.columns:
            v = pd.to_numeric(perf_df[c], errors="coerce")
            std = v.std(ddof=0)
            zs.append((v - v.mean()) / std if std else v * 0)
    if not zs:
        return pd.Series(np.nan, index=perf_df.index)
    return pd.concat(zs, axis=1).mean(axis=1, skipna=True)
