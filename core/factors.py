"""Cross-sectional factor scoring for the AI universe (pure / testable).

Everything here operates on a DataFrame and returns new columns — no Streamlit,
no I/O. The Performance/Screener UI wraps these with caching.

Method
------
For each raw metric we compute a *cross-sectional* signal across the universe:

* winsorize at +/-3 sigma (clip extreme outliers),
* z-score (mean 0, std 1); if a column is too sparse (< MIN_COVERAGE non-null),
  fall back to a rank-based score so a handful of values still produce a signal,
* flip the sign for "lower-is-better" metrics (valuation, leverage) so that
  higher z always means "better",
* a stock's factor score = the **mean of available** sub-signals (missing values
  are ignored, never treated as 0),
* factor scores are finally mapped to 0..100 percentiles for display.

Composite = weighted average of the per-factor *z-scores* (not percentiles), then
percentile-mapped, so slider weights re-rank the universe smoothly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

WINSOR_SIGMA = 3.0
MIN_COVERAGE = 0.30  # below this fraction of non-null -> rank fallback

FACTOR_NAMES = ["Value", "Quality", "Growth", "Financial Health",
                "Momentum", "MS Upside"]


# ---------------------------------------------------------------------------
# Spec for a single factor: which columns feed it and their direction.
# ---------------------------------------------------------------------------
@dataclass
class FactorSpec:
    name: str
    higher_better: List[str] = field(default_factory=list)
    lower_better: List[str] = field(default_factory=list)


# Note: momentum columns (mom_3m, mom_6m) are produced by core.performance and
# merged in before scoring; "Total Return (1Y)" comes from Morningstar.
FACTOR_SPECS: List[FactorSpec] = [
    FactorSpec(
        "Value",
        higher_better=["Earnings Yield", "Cash Flow Yield", "Sales Yield",
                       "Book Yield", "Total Yield"],
        lower_better=["Price/Earnings (Normalized)", "Price/Book Value",
                      "Price/Sales", "Price/Free Cash Flow", "Price/EBITDA"],
    ),
    FactorSpec(
        "Quality",
        higher_better=["Return on Equity", "Return on Assets",
                       "Return on Invested Capital", "Net Margin",
                       "Operating Margin", "Gross Margin", "Interest Coverage"],
        lower_better=["Financial Leverage"],
    ),
    FactorSpec(
        "Growth",
        higher_better=["Revenue Growth (1Y)", "Revenue Growth (5Y)",
                       "EPS Growth (1Y)", "EPS Growth (3Y)",
                       "Net Income Growth (1Y)", "Operating Profit Growth (1Y)"],
    ),
    FactorSpec(
        "Financial Health",
        higher_better=["Current Ratio", "Quick Ratio", "Interest Coverage",
                       "Free Cash Flow"],
        lower_better=["Total Debt/Equity"],
    ),
    FactorSpec(
        "Momentum",
        higher_better=["mom_3m", "mom_6m", "Total Return (1Y)"],
    ),
    FactorSpec(
        "MS Upside",
        higher_better=["upside_dampened"],
    ),
]

# Grade / moat bonuses folded into Quality & Growth as extra z-like signals.
_GRADE_MAP = {"A": 2.0, "B": 1.0, "C": 0.0, "D": -1.0, "F": -2.0}
_MOAT_MAP = {"Wide": 1.0, "Narrow": 0.5, "None": -0.5}
_UNCERTAINTY_DAMP = {"Low": 1.0, "Medium": 0.8, "High": 0.6, "Very High": 0.4}


def _winsorize_z(s: pd.Series) -> pd.Series:
    """Winsorized z-score; rank-fallback when coverage is too sparse."""
    vals = pd.to_numeric(s, errors="coerce")
    n = vals.notna().sum()
    if n == 0:
        return pd.Series(np.nan, index=s.index)

    coverage = n / len(vals)
    if coverage < MIN_COVERAGE:
        # Rank-based fallback -> roughly standard-normal-ish centred signal.
        r = vals.rank(pct=True)
        return (r - 0.5) * 2.0  # spread ranks to ~[-1, 1]

    mean, std = vals.mean(), vals.std(ddof=0)
    if not std or np.isnan(std):
        return pd.Series(0.0, index=s.index).where(vals.notna(), np.nan)
    z = (vals - mean) / std
    return z.clip(-WINSOR_SIGMA, WINSOR_SIGMA)


def _mean_ignore_nan(frame: pd.DataFrame) -> pd.Series:
    """Row-wise mean ignoring NaN; all-NaN rows -> NaN (not 0)."""
    if frame.shape[1] == 0:
        return pd.Series(np.nan, index=frame.index)
    return frame.mean(axis=1, skipna=True)


def _dampened_upside(df: pd.DataFrame) -> pd.Series:
    """(FV - Price)/Price dampened by Fair Value Uncertainty."""
    if "upside_pct" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    damp = df.get("Fair Value Uncertainty")
    factor = (
        damp.map(_UNCERTAINTY_DAMP).fillna(0.6) if damp is not None else 0.6
    )
    return df["upside_pct"] * factor


def to_percentile(s: pd.Series) -> pd.Series:
    """Map a numeric series to 0..100 percentiles (NaN preserved)."""
    return s.rank(pct=True) * 100.0


def compute_factor_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame indexed like ``df`` with per-factor z, percentile columns.

    Columns produced:
      ``<Factor>_z``     raw composite z (used for the weighted total)
      ``<Factor>``       0..100 percentile (for display)
    """
    work = df.copy()
    work["upside_dampened"] = _dampened_upside(work)

    out = pd.DataFrame(index=df.index)

    for spec in FACTOR_SPECS:
        signals: List[pd.Series] = []
        for col in spec.higher_better:
            if col in work.columns:
                signals.append(_winsorize_z(work[col]))
        for col in spec.lower_better:
            if col in work.columns:
                signals.append(-_winsorize_z(work[col]))  # invert

        # Categorical bonus signals.
        if spec.name == "Quality":
            if "Profitability Grade" in work.columns:
                signals.append(work["Profitability Grade"].map(_GRADE_MAP))
            if "Economic Moat" in work.columns:
                signals.append(
                    work["Economic Moat"].map(_MOAT_MAP)
                )
        if spec.name == "Growth" and "Growth Grade" in work.columns:
            signals.append(work["Growth Grade"].map(_GRADE_MAP))

        if signals:
            comp = _mean_ignore_nan(pd.concat(signals, axis=1))
        else:
            comp = pd.Series(np.nan, index=df.index)

        out[f"{spec.name}_z"] = comp
        out[spec.name] = to_percentile(comp)

    return out


def weighted_composite(
    factor_scores: pd.DataFrame, weights: Dict[str, float]
) -> pd.Series:
    """Weighted-average the per-factor z columns into a 0..100 composite.

    Missing factor z's for a stock are ignored (weights renormalised per row).
    """
    z_cols = [f"{name}_z" for name in FACTOR_NAMES if f"{name}_z" in factor_scores]
    w = np.array([weights.get(name, 0.0) for name in FACTOR_NAMES
                  if f"{name}_z" in factor_scores], dtype=float)
    if w.sum() == 0:
        w = np.ones_like(w)

    z = factor_scores[z_cols].to_numpy(dtype=float)
    mask = ~np.isnan(z)
    wmat = np.where(mask, w[None, :], 0.0)
    wsum = wmat.sum(axis=1)
    zfilled = np.where(mask, z, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        comp = (zfilled * wmat).sum(axis=1) / np.where(wsum == 0, np.nan, wsum)

    comp_s = pd.Series(comp, index=factor_scores.index)
    return to_percentile(comp_s)


def default_weights() -> Dict[str, float]:
    return {name: 1.0 for name in FACTOR_NAMES}
