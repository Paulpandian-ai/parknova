"""Capex-cycle timing math: valuation tier, value-trap flag, upside-to-FV.

Pure functions over the Morningstar frame (no Streamlit, no I/O) so they are
easy to unit-test. The framework:

* Valuation tier from forward P/E (fallback to trailing P/E):
  <20x = Tier 1, 20-35x = Tier 2, >35x = Tier 3, missing/negative = n/m.
* Value-trap flag = low forward P/E (<15x) AND Early-crest (deep cyclical that
  crests first — memory/chips) AND an extended price (1Y total return > +50%).
  This reproduces the "single-digit memory at the top of the cycle" signature: a
  cheap multiple on a late-stage cyclical is a trap, not a bargain.
* upside-to-fair-value = (Fair Value - Last Price) / Last Price.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Thresholds (single source of truth — easy to tune).
TIER1_MAX = 20.0
TIER2_MAX = 35.0
TRAP_FWD_PE_MAX = 15.0   # "low multiple"
TRAP_1Y_RUN_MIN = 50.0   # "extended price" — 1Y total return in PERCENT units

TIER_NM = "n/m"


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def effective_forward_pe(row) -> Optional[float]:
    """Forward P/E, falling back to trailing P/E when forward is missing."""
    fwd = _num(row.get("Price/Earnings (Forward)"))
    if fwd is not None:
        return fwd
    return _num(row.get("Price/Earnings"))


def valuation_tier(row) -> str:
    """Return 'Tier 1' / 'Tier 2' / 'Tier 3' / 'n/m' from the effective fwd P/E."""
    pe = effective_forward_pe(row)
    if pe is None or pe <= 0:
        return TIER_NM
    if pe < TIER1_MAX:
        return "Tier 1"
    if pe <= TIER2_MAX:
        return "Tier 2"
    return "Tier 3"


def is_value_trap(row) -> bool:
    """True when a low forward P/E sits on an extended Early-crest cyclical."""
    pe = _num(row.get("Price/Earnings (Forward)"))
    if pe is None:
        pe = _num(row.get("Price/Earnings"))
    if pe is None or pe <= 0 or pe >= TRAP_FWD_PE_MAX:
        return False
    if str(row.get("Crest") or "") != "Early":
        return False
    run_1y = _num(row.get("Total Return (1Y)"))
    if run_1y is None or run_1y <= TRAP_1Y_RUN_MIN:
        return False
    return True


def value_trap_reason(row) -> str:
    """Human tooltip explaining why a name is on value-trap watch."""
    pe = effective_forward_pe(row)
    run = _num(row.get("Total Return (1Y)"))
    pe_s = f"{pe:.1f}x" if pe is not None else "n/m"
    run_s = f"+{run:.0f}%" if run is not None else "n/m"
    return (f"Low forward P/E ({pe_s}) on an Early-crest deep cyclical that has "
            f"run hard (1Y {run_s}). Cheap multiple at a likely cycle top is a "
            f"value-trap signature, not a bargain.")


def upside_to_fair_value(row) -> Optional[float]:
    """(Fair Value - Last Price) / Last Price as a fraction (None if unusable)."""
    fv = _num(row.get("Fair Value"))
    lp = _num(row.get("Last Price"))
    if fv is None or lp is None or lp == 0:
        return None
    return fv / lp - 1.0


def add_timing_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with valuation_tier / value_trap / upside_fv added.

    ``upside_pct`` may already exist from the loader; we (re)compute a clean
    ``upside_fv`` here so this module is self-contained and testable.
    """
    out = df.copy()
    out["valuation_tier"] = out.apply(valuation_tier, axis=1)
    out["value_trap"] = out.apply(is_value_trap, axis=1)
    out["upside_fv"] = out.apply(upside_to_fair_value, axis=1)
    return out


# ---------------------------------------------------------------------------
# Capex-cycle aggregations (for the Capex Cycle view)
# ---------------------------------------------------------------------------
CREST_ORDER = ["Early", "Mid", "Late"]
SIDE_ORDER = ["Supplier", "Demand", "Hyperscaler"]


def crest_side_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Crest x Side cells with count, median 1Y return (%) and median fwd P/E.

    Returns a long frame: columns [Crest, Side, n, median_1y, median_fwd_pe].
    ``df`` must carry Crest, Side, 'Total Return (1Y)' and a forward-P/E column.
    """
    rows = []
    fwd = df.apply(effective_forward_pe, axis=1)
    work = df.assign(_fwd=fwd.values)
    for crest in CREST_ORDER:
        for side in SIDE_ORDER:
            cell = work[(work["Crest"] == crest) & (work["Side"] == side)]
            rows.append({
                "Crest": crest, "Side": side, "n": int(len(cell)),
                "median_1y": (pd.to_numeric(cell["Total Return (1Y)"],
                                            errors="coerce").median()
                              if len(cell) else np.nan),
                "median_fwd_pe": cell["_fwd"].median() if len(cell) else np.nan,
            })
    return pd.DataFrame(rows)


def rotation_table(perf_df: pd.DataFrame, windows) -> pd.DataFrame:
    """Median return per Crest (rows) x window (cols), as fractions.

    ``perf_df`` is the performance frame (carries Crest + window columns as
    fractions). Rows ordered Early/Mid/Late; missing windows stay NaN.
    """
    cols = [w for w in windows if w in perf_df.columns]
    if perf_df.empty or "Crest" not in perf_df.columns or not cols:
        return pd.DataFrame(columns=cols)
    num = perf_df[cols].apply(pd.to_numeric, errors="coerce")
    num["Crest"] = perf_df["Crest"].values
    out = num.groupby("Crest")[cols].median()
    return out.reindex([c for c in CREST_ORDER if c in out.index])


def rotation_caption(perf_df: pd.DataFrame, window: str) -> Optional[str]:
    """Computed one-liner comparing Early vs Late crest for ``window``.

    Returns None when the data for that window is unavailable, so the caller can
    fall back to a Morningstar window and label it.
    """
    if perf_df.empty or "Crest" not in perf_df.columns or window not in perf_df.columns:
        return None
    s = pd.to_numeric(perf_df[window], errors="coerce")
    med = s.groupby(perf_df["Crest"]).median()
    early, late = med.get("Early"), med.get("Late")
    if early is None or late is None or pd.isna(early) or pd.isna(late):
        return None
    e_s = f"{early * 100:+.1f}%"
    l_s = f"{late * 100:+.1f}%"
    if early > late:
        direction = "capital rotating toward early-crest layers"
    elif late > early:
        direction = "capital rotating toward late-crest layers"
    else:
        direction = "no clear crest rotation"
    return f"Last {window}: Early-crest {e_s} vs Late-crest {l_s} -> {direction}"
