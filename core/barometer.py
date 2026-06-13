"""Universe barometer — where return concentrates across the AI names.

Pure, testable. Builds a style-box / bucket / crest grid of median trailing
return per cell (with the name count behind each cell, since an AI-skewed
universe makes some cells thin and their medians noisy), and drill-downs that
hunt for the hedge-fund move: names in an *outperforming* cell that still trade
*below fair value* and are *not* flagged value traps — outperformance the
fundamentals still support, rather than already-priced-in re-rating.

Reuses already-computed fields only (Stock Style Box, trailing returns, factor
percentiles, upside-to-fair-value, valuation tier, value-trap flag). No new data
source, no recompute of factor/timing logic, no Streamlit. The caller passes one
merged universe frame; this module never fetches or rebuilds anything.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Grouping modes for the grid.
GROUP_STYLE = "Style Box"
GROUP_BUCKET = "Primary Bucket"
GROUP_CREST = "Crest"
GROUPINGS = [GROUP_STYLE, GROUP_BUCKET, GROUP_CREST]

# Style-box axes.
SIZES = ["Large", "Mid", "Small"]
STYLES = ["Value", "Core", "Growth"]
UNCLASSIFIED = "Unclassified"

# Capex-cycle axes (kept local so this module stays Streamlit-free).
CREST_ORDER = ["Early", "Mid", "Late"]
SIDE_ORDER = ["Supplier", "Demand", "Hyperscaler", "Unknown"]

THIN_CELL_N = 5            # n < this -> low-confidence median
_FWD_PE_COL = "Price/Earnings (Forward)"
FACTOR_PCT_COLS = ["Value", "Quality", "Growth", "Momentum"]


# ---------------------------------------------------------------------------
# Style-box parsing
# ---------------------------------------------------------------------------
def parse_style_box(value) -> Optional[Tuple[str, str]]:
    """Parse a Morningstar Stock Style Box string into ``(size, style)``.

    Tolerant of the Core/Blend variants and word order; returns None when the
    value is missing or either axis can't be mapped (-> Unclassified upstream).
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    if not s or s == "nan":
        return None

    if "large" in s:
        size = "Large"
    elif "small" in s:
        size = "Small"
    elif "mid" in s or "medium" in s:
        size = "Mid"
    else:
        size = None

    if "value" in s:
        style = "Value"
    elif "growth" in s:
        style = "Growth"
    elif "core" in s or "blend" in s:
        style = "Core"
    else:
        style = None

    if size is None or style is None:
        return None
    return (size, style)


def style_cell(value) -> str:
    """'Size Style' label for a style box, or 'Unclassified'."""
    parsed = parse_style_box(value)
    return f"{parsed[0]} {parsed[1]}" if parsed else UNCLASSIFIED


# ---------------------------------------------------------------------------
# Cell assignment
# ---------------------------------------------------------------------------
def assign_cell(df: pd.DataFrame, grouping: str) -> pd.Series:
    """Per-row cell label for the chosen grouping."""
    if grouping == GROUP_STYLE:
        return df["Stock Style Box"].map(style_cell)
    if grouping == GROUP_BUCKET:
        return df["Primary Bucket"].fillna(UNCLASSIFIED).astype(str)
    if grouping == GROUP_CREST:
        crest = df["Crest"].fillna("Mid").astype(str)
        side = df["Side"].fillna("Unknown").astype(str)
        return crest.str.cat(side, sep=" / ")
    raise ValueError(f"unknown grouping: {grouping!r}")


def _cell_axes(grouping: str, df: pd.DataFrame) -> List[Tuple[Optional[str],
                                                              Optional[str], str]]:
    """Canonical ``(row, col, label)`` cells for the grouping's grid."""
    if grouping == GROUP_STYLE:
        return [(sz, stl, f"{sz} {stl}") for sz in SIZES for stl in STYLES]
    if grouping == GROUP_CREST:
        sides = [s for s in SIDE_ORDER if s in set(df["Side"].fillna("Unknown"))]
        return [(cr, sd, f"{cr} / {sd}") for cr in CREST_ORDER for sd in sides]
    # Bucket: 1-D, ordered by presence.
    buckets = [b for b in df["Primary Bucket"].fillna(UNCLASSIFIED).unique()]
    return [(None, b, str(b)) for b in buckets]


def _median(series: pd.Series) -> Optional[float]:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return float(vals.median()) if len(vals) else None


def cell_stats(df: pd.DataFrame, grouping: str, window: str) -> List[Dict[str, Any]]:
    """Median return / forward P/E / upside-to-FV + name count per cell.

    Returns a list of cell dicts: ``label, row, col, n, median_return,
    median_fwd_pe, median_upside_fv, thin, is_unclassified``. For Style Box all
    9 cells are always present (n may be 0); unmappable style boxes are reported
    as a separate ``Unclassified`` cell, never folded into a grid cell.
    """
    cells = assign_cell(df, grouping)
    work = df.copy()
    work["_cell"] = cells
    out: List[Dict[str, Any]] = []

    for row, col, label in _cell_axes(grouping, df):
        sub = work[work["_cell"] == label]
        out.append(_cell_record(sub, label, window, row=row, col=col))

    # Unclassified (style box only — bucket/crest already include it as a cell).
    if grouping == GROUP_STYLE:
        sub = work[work["_cell"] == UNCLASSIFIED]
        if len(sub):
            rec = _cell_record(sub, UNCLASSIFIED, window, row=None, col=None)
            rec["is_unclassified"] = True
            out.append(rec)
    return out


def _cell_record(sub: pd.DataFrame, label: str, window: str,
                 row: Optional[str], col: Optional[str]) -> Dict[str, Any]:
    n = len(sub)
    return {
        "label": label, "row": row, "col": col, "n": n,
        "median_return": _median(sub[window]) if window in sub.columns else None,
        "median_fwd_pe": _median(sub[_FWD_PE_COL])
        if _FWD_PE_COL in sub.columns else None,
        "median_upside_fv": _median(sub["upside_fv"])
        if "upside_fv" in sub.columns else None,
        "thin": 0 < n < THIN_CELL_N,
        "is_unclassified": label == UNCLASSIFIED,
    }


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------
_DRILL_COLS = ["Ticker", "Name", "Primary Bucket", "Crest",
               "upside_fv", "valuation_tier", "value_trap"] + FACTOR_PCT_COLS


def cell_constituents(df: pd.DataFrame, grouping: str, cell_label: str,
                      window: str, below_fv_only: bool = False,
                      sort_by_upside: bool = True) -> pd.DataFrame:
    """Constituents of one cell with return / upside-to-FV / factors.

    ``below_fv_only`` keeps names trading below fair value (upside_fv > 0);
    ``sort_by_upside`` ranks by upside-to-FV descending (the alpha sort).
    """
    cells = assign_cell(df, grouping)
    sub = df[cells == cell_label].copy()
    if below_fv_only and "upside_fv" in sub.columns:
        sub = sub[pd.to_numeric(sub["upside_fv"], errors="coerce") > 0]

    keep = [c for c in _DRILL_COLS if c in sub.columns]
    if window in sub.columns and window not in keep:
        keep.insert(2, window)
    sub = sub[keep]
    if sort_by_upside and "upside_fv" in sub.columns:
        sub = sub.sort_values("upside_fv", ascending=False, na_position="last")
    elif window in sub.columns:
        sub = sub.sort_values(window, ascending=False, na_position="last")
    return sub.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Alpha hunt
# ---------------------------------------------------------------------------
def universe_median_return(df: pd.DataFrame, window: str) -> Optional[float]:
    return _median(df[window]) if window in df.columns else None


def outperforming_cells(df: pd.DataFrame, grouping: str,
                        window: str) -> List[str]:
    """Cell labels whose median return beats the universe median (n>0)."""
    bench = universe_median_return(df, window)
    if bench is None:
        return []
    labels = []
    for cell in cell_stats(df, grouping, window):
        if (cell["n"] > 0 and not cell["is_unclassified"]
                and cell["median_return"] is not None
                and cell["median_return"] > bench):
            labels.append(cell["label"])
    return labels


def alpha_hunt(df: pd.DataFrame, grouping: str, window: str) -> pd.DataFrame:
    """The hedge-fund screen: outperforming-cell + below-FV + not value-trap.

    Names in an outperforming cell (cell median return > universe median) that
    still trade below Morningstar fair value (upside_fv > 0) and are not flagged
    value traps — outperformance the fundamentals still support. Ranked by
    upside-to-FV descending. Returns an empty frame when nothing qualifies.
    """
    hot = set(outperforming_cells(df, grouping, window))
    if not hot:
        return pd.DataFrame()
    cells = assign_cell(df, grouping)
    work = df.copy()
    work["_cell"] = cells
    upside = pd.to_numeric(work.get("upside_fv"), errors="coerce")
    is_trap = work.get("value_trap")
    trap_mask = (is_trap == True) if is_trap is not None else False  # noqa: E712
    mask = work["_cell"].isin(hot) & (upside > 0) & ~np.asarray(trap_mask)
    sel = work[mask].copy()
    if sel.empty:
        return pd.DataFrame()

    keep = ["Ticker", "Name", "_cell"]
    if window in sel.columns:
        keep.append(window)
    keep += [c for c in ["upside_fv", "valuation_tier", "value_trap",
                         "Primary Bucket", "Crest"] + FACTOR_PCT_COLS
             if c in sel.columns]
    sel = sel[keep].rename(columns={"_cell": "Cell"})
    return sel.sort_values("upside_fv", ascending=False,
                           na_position="last").reset_index(drop=True)
