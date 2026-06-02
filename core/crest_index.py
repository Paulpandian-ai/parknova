"""Crest / Side return indices — rebased-to-100 equal-weighted (or median) series.

Builds a cumulative index per crest layer (Early/Mid/Late) and per side
(Supplier/Demand/Hyperscaler) from per-ticker daily price history, so the capex
rotation (Early-crest rising while Late-crest sells off) shows up as diverging
lines and the Early-minus-Late spread becomes a single tracked signal.

Method (documented, on purpose)
-------------------------------
Equal-weight the **daily simple returns of whoever is present** that day, then
chain into a cumulative index rebased to 100 at the start. A name contributes to
a day's group return only when it has a price on both that day and the prior
trading day, so late IPOs / sparse history enter naturally as they become
available — no survivorship assumption beyond "present today". ``median`` swaps
the row-wise mean for a median (robust to a few extreme movers).

HONEST CAVEAT (surfaced in the UI): the crest classification is *current* (2026).
Applying today's labels to older prices is a backcast — "how would today's crest
groups have moved", not a contemporaneous read. The constituent_count column
exposes how thin/backcast-heavy each index is on each date.

Pure / Streamlit-free so it is unit-testable; caching + persistence live in
``data/service.py`` + ``core/store.py``.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

CREST_GROUPS = ["Early", "Mid", "Late"]
SIDE_GROUPS = ["Supplier", "Demand", "Hyperscaler"]


def _prices_to_frame(prices: Dict[str, pd.Series]) -> pd.DataFrame:
    """Align per-ticker price Series into one wide frame (cols = tickers)."""
    cleaned = {t: s.sort_index() for t, s in prices.items()
               if s is not None and not s.empty}
    if not cleaned:
        return pd.DataFrame()
    frame = pd.concat(cleaned, axis=1)
    frame.columns = list(cleaned.keys())
    # Normalise to a sorted DatetimeIndex of unique dates.
    frame.index = pd.to_datetime(frame.index)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame


def _group_index(returns: pd.DataFrame, members: List[str], present: pd.DataFrame,
                 method: str) -> pd.DataFrame:
    """Build one group's {index_level, constituent_count} from member returns."""
    cols = [m for m in members if m in returns.columns]
    if not cols:
        return pd.DataFrame()
    r = returns[cols]
    # Equal-weight daily return of whoever has a return that day.
    if method == "median":
        grp_ret = r.median(axis=1, skipna=True)
    else:
        grp_ret = r.mean(axis=1, skipna=True)
    grp_ret = grp_ret.fillna(0.0)
    level = (1.0 + grp_ret).cumprod() * 100.0
    # Constituent count = names with an actual price that day (not just returns).
    count = present[cols].sum(axis=1).astype(int)
    return pd.DataFrame({"index_level": level, "constituent_count": count})


def build_indices(
    prices: Dict[str, pd.Series],
    classification: pd.DataFrame,
    start_date: Optional[str] = None,
    method: str = "equal",
) -> pd.DataFrame:
    """Return tidy long-form indices for crest + side groups.

    Columns: ``date, group_type (crest|side), group, index_level,
    constituent_count``. Rebased so the first row of each group == 100.

    ``classification`` must carry ``Ticker``, ``Crest`` and ``Side`` columns.
    ``method`` is ``"equal"`` (default) or ``"median"``. Never raises on
    missing/short history — absent names simply never contribute.
    """
    frame = _prices_to_frame(prices)
    if frame.empty:
        return pd.DataFrame(columns=["date", "group_type", "group",
                                     "index_level", "constituent_count"])
    if start_date:
        frame = frame[frame.index >= pd.to_datetime(start_date)]
    if frame.empty:
        return pd.DataFrame(columns=["date", "group_type", "group",
                                     "index_level", "constituent_count"])

    returns = frame.pct_change()
    present = frame.notna()

    cls = classification.set_index("Ticker")
    out_rows: List[pd.DataFrame] = []

    def _emit(group_type: str, group: str, members: List[str]):
        idx = _group_index(returns, members, present, method)
        if idx.empty or idx["constituent_count"].max() == 0:
            return
        # Rebase to 100 at the first date the group actually has a constituent.
        first = idx.index[idx["constituent_count"] > 0]
        if len(first) == 0:
            return
        base = first[0]
        scale = idx.loc[base, "index_level"]
        if not scale:
            return
        idx = idx.loc[base:].copy()
        idx["index_level"] = idx["index_level"] / idx.loc[base, "index_level"] * 100.0
        idx = idx.reset_index().rename(columns={"index": "date"})
        idx.columns = ["date", "index_level", "constituent_count"]
        idx["group_type"] = group_type
        idx["group"] = group
        out_rows.append(idx)

    for g in CREST_GROUPS:
        members = cls.index[cls["Crest"] == g].tolist()
        _emit("crest", g, members)
    for g in SIDE_GROUPS:
        members = cls.index[cls["Side"] == g].tolist()
        _emit("side", g, members)

    if not out_rows:
        return pd.DataFrame(columns=["date", "group_type", "group",
                                     "index_level", "constituent_count"])
    out = pd.concat(out_rows, ignore_index=True)
    return out[["date", "group_type", "group", "index_level", "constituent_count"]]


# ---------------------------------------------------------------------------
# Read helpers for the UI (operate on the tidy long-form frame)
# ---------------------------------------------------------------------------
def pivot_levels(indices: pd.DataFrame, group_type: str) -> pd.DataFrame:
    """Wide frame of index levels (index=date, cols=group) for a group_type."""
    sub = indices[indices["group_type"] == group_type]
    if sub.empty:
        return pd.DataFrame()
    wide = sub.pivot_table(index="date", columns="group", values="index_level")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def constituent_counts(indices: pd.DataFrame, group_type: str) -> pd.DataFrame:
    sub = indices[indices["group_type"] == group_type]
    if sub.empty:
        return pd.DataFrame()
    wide = sub.pivot_table(index="date", columns="group",
                           values="constituent_count")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def window_rebased(wide: pd.DataFrame, start) -> pd.DataFrame:
    """Re-rebase a wide level frame to 100 at the first row on/after ``start``."""
    if wide.empty:
        return wide
    w = wide[wide.index >= pd.to_datetime(start)] if start is not None else wide
    if w.empty:
        return w
    base = w.iloc[0]
    return w.divide(base).multiply(100.0)


def spread_series(wide: pd.DataFrame, a: str = "Early", b: str = "Late"
                  ) -> Optional[pd.Series]:
    """Early-minus-Late spread (in index points) — the single rotation signal."""
    if wide.empty or a not in wide.columns or b not in wide.columns:
        return None
    return (wide[a] - wide[b]).rename(f"{a} − {b}")


def rotation_read(wide: pd.DataFrame, a: str = "Early", b: str = "Late"
                  ) -> Optional[str]:
    """Computed caption: % change of each group over the window + direction."""
    if wide.empty or a not in wide.columns or b not in wide.columns:
        return None
    wa, wb = wide[a].dropna(), wide[b].dropna()
    if len(wa) < 2 or len(wb) < 2:
        return None
    ra = wa.iloc[-1] / wa.iloc[0] - 1.0
    rb = wb.iloc[-1] / wb.iloc[0] - 1.0
    a_s, b_s = f"{ra * 100:+.1f}%", f"{rb * 100:+.1f}%"
    if ra > rb:
        direction = f"rotation toward {a.lower()}-crest"
    elif rb > ra:
        direction = f"rotation toward {b.lower()}-crest"
    else:
        direction = "no clear rotation"
    return f"{a}-crest {a_s} vs {b}-crest {b_s} over the window — {direction}"
