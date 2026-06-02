"""Merge Morningstar trailing returns with live FMP short-window momentum.

Live windows (Today/1W/1M/3M/6M) are computed from a single daily adjusted-close
series per ticker using nearest-prior ("asof") slicing — no per-window requests.
Morningstar provides YTD/1Y/3Y/5Y (already in percent; converted to fractions
here so the whole performance frame uses one convention: fractions, e.g. 0.123).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("parknova.performance")

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


def last_session_change(series: pd.Series) -> Optional[Dict[str, Any]]:
    """Close-to-close change of the last completed session from a price Series.

    Returns ``{pct, prev_close, last_close, date}`` (pct as a fraction) using the
    already-cached per-ticker history — no extra API call. ``None`` when there
    aren't two sessions to compare.
    """
    if series is None or series.empty or len(series) < 2:
        return None
    series = series.sort_index()
    last_close = float(series.iloc[-1])
    prev_close = float(series.iloc[-2])
    if prev_close == 0:
        return None
    return {
        "pct": last_close / prev_close - 1.0,
        "prev_close": prev_close,
        "last_close": last_close,
        "date": series.index[-1],
    }


# Returns above this magnitude (e.g. 500%) are almost always a split/units
# artifact rather than a real move — log the inputs so they surface.
SANITY_RETURN = 5.0


def trailing_return(series: pd.Series, offset) -> Optional[float]:
    """Total return over ``offset`` (a pandas DateOffset) from an (adjusted)
    series, using nearest-prior pricing. None when there isn't enough history."""
    if series is None or series.empty:
        return None
    s = series.sort_index()
    latest = float(s.iloc[-1])
    start_date = s.index[-1] - offset
    if start_date < s.index[0] or latest == 0:
        return None
    start_val = s.asof(start_date)
    if pd.isna(start_val) or not start_val:
        return None
    return latest / float(start_val) - 1.0


def ytd_return(series: pd.Series) -> Optional[float]:
    """Year-to-date total return from an (adjusted) series (None if too short)."""
    if series is None or series.empty:
        return None
    s = series.sort_index()
    latest = float(s.iloc[-1])
    jan1 = pd.Timestamp(year=s.index[-1].year, month=1, day=1)
    if s.index[0] > jan1 or latest == 0:
        return None
    start_val = s.asof(jan1)
    if pd.isna(start_val) or not start_val:
        return None
    return latest / float(start_val) - 1.0


# Adjusted-series offsets for the Morningstar trailing windows (split/dividend
# safe). YTD is handled separately by ytd_return.
_MS_OFFSETS = {"1Y": pd.DateOffset(years=1), "3Y": pd.DateOffset(years=3),
               "5Y": pd.DateOffset(years=5)}


def live_windows_from_series(
    series: pd.Series, today_pct: Optional[float] = None,
    ticker: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """Return live window returns as fractions. ``today_pct`` is FMP percent.

    "Today" precedence: a non-null/non-zero live percent, else the last completed
    session's close-to-close change from ``series`` (so it is never blank when
    history exists). Window returns use the (adjusted) series; any |return| above
    ``SANITY_RETURN`` is logged with the two prices used (likely split-distorted).
    """
    out: Dict[str, Optional[float]] = {w: None for w in LIVE_WINDOWS}
    if today_pct is not None and pd.notna(today_pct) and float(today_pct) != 0.0:
        out["Today"] = float(today_pct) / 100.0

    if series is None or series.empty:
        return out

    # EOD fallback for "Today" when the live percent is missing/zero (closed).
    if out["Today"] is None:
        eod = last_session_change(series)
        if eod is not None:
            out["Today"] = eod["pct"]

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
            ret = latest / float(start_val) - 1.0
            if abs(ret) > SANITY_RETURN:
                logger.warning("Implausible %s return for %s: %.0f%% "
                               "(start %.2f -> latest %.2f) — likely split/units "
                               "artifact", label, ticker or "?", ret * 100,
                               float(start_val), latest)
            out[label] = ret
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
        if live:
            hist = service.get_history_result(ticker)
            series = hist["series"]
            raw_last = hist.get("raw_last")
        else:
            series = pd.Series(dtype="float64")
            raw_last = None
        live_vals = live_windows_from_series(series, q.get("changesPercentage"),
                                             ticker=ticker)

        # Authoritative displayed price (ONE basis, consistent with returns):
        #   live intraday quote -> latest raw/unadjusted close -> Morningstar.
        # Returns above are computed from the *adjusted* series, so splits never
        # create phantom moves while the shown price is what the stock trades at.
        price = q.get("price")
        if price is None or pd.isna(price):
            price = raw_last
        if price is None or pd.isna(price):
            price = meta.get("Last Price")

        row = {
            "Ticker": ticker,
            "Name": meta.get("Name"),
            "Sector": meta.get("Sector"),
            "Primary Bucket": meta.get("Primary Bucket"),
            "Side": meta.get("Side"),
            "Crest": meta.get("Crest"),
            "Stock Style Box": meta.get("Stock Style Box"),
            "Last Price": price,
            "has_live": not series.empty or bool(q),
        }
        row.update(live_vals)
        # Trailing returns: compute from the ADJUSTED series (split/dividend
        # safe) when history allows; fall back to the Morningstar column only
        # when there isn't enough history. This keeps the trailing returns on
        # the same adjusted basis as the live windows + crest index, avoiding
        # the phantom (split/units-distorted) Morningstar values.
        for w in MS_WINDOWS:
            computed = (ytd_return(series) if w == "YTD"
                        else trailing_return(series, _MS_OFFSETS[w]))
            ms_raw = meta.get(_MS_SOURCE[w])
            ms_frac = (float(ms_raw) / 100.0) if pd.notna(ms_raw) else np.nan
            if computed is not None:
                frac = computed
            else:
                # Falling back to Morningstar — flag if it looks implausible.
                if pd.notna(ms_frac) and abs(ms_frac) > SANITY_RETURN:
                    logger.warning("Implausible Morningstar %s for %s: %.0f%% — "
                                   "no FMP history to override; verify units/"
                                   "splits", w, ticker, ms_frac * 100)
                frac = ms_frac
            row[w] = frac

        # Momentum inputs for the factor engine (fractions).
        row["mom_3m"] = live_vals.get("3M")
        row["mom_6m"] = live_vals.get("6M")
        rows.append(row)

        if progress and total:
            progress((i + 1) / total, ticker)

    return pd.DataFrame(rows)


def group_heatmap(perf_df: pd.DataFrame, by: str,
                  row_order: Optional[List[str]] = None) -> pd.DataFrame:
    """Median return per ``by`` group (rows) x window (cols), fractions.

    All windows are always present as columns (NaN when a window has no data).
    ``row_order`` (if given) reindexes rows to a canonical order, dropping groups
    not present.
    """
    if perf_df.empty or by not in perf_df.columns:
        return pd.DataFrame(columns=ALL_WINDOWS)
    num = perf_df[ALL_WINDOWS].apply(pd.to_numeric, errors="coerce")
    num[by] = perf_df[by].values
    out = num.groupby(by)[ALL_WINDOWS].median()
    if row_order:
        keep = [r for r in row_order if r in out.index]
        out = out.reindex(keep)
    return out


def sector_heatmap(perf_df: pd.DataFrame) -> pd.DataFrame:
    """Median return per sector x window (fractions)."""
    return group_heatmap(perf_df, "Sector")


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
