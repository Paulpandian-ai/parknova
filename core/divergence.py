"""Price–fundamentals divergence — observations, NOT earnings surprises.

This module has no analyst-estimate / EPS feed and never claims a beat or miss.
It flags names whose recent *price* move diverges from what their current
fundamentals or fair value obviously justify:

  Flavor A — euphoria / stretched : price running UP, ahead of value (premium to FV)
  Flavor B — possible opportunity  : price falling AWAY from value (discount to FV)
  Flavor C — momentum vs factors   : a move whose direction conflicts with the
                                      factor percentiles (up on weak fundamentals,
                                      or down on strong ones)

All inputs are already-computed fields — live/last quote and short-window returns
from the performance frame, factor percentiles from :mod:`core.factors`, and
upside-to-fair-value from :mod:`core.timing`. Nothing is recomputed here. Every
flagged row carries the triggering numbers and a plain one-line reason. These are
conditions for review, not advice or predictions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Thresholds — single source of truth.
MOVE_PCTILE_HI = 75      # top-quartile short-window return = strong up move
MOVE_PCTILE_LO = 25      # bottom-quartile short-window return = strong down move
TODAY_BIG = 0.04         # a "large" single-day move (±4%)
FV_PREMIUM = -0.15       # upside-to-FV < −15%  → price >15% above fair value
FV_DISCOUNT = 0.15       # upside-to-FV > +15%  → price >15% below fair value
FACTOR_LOW = 33          # bottom third of a factor percentile
FACTOR_HIGH = 67         # top third of a factor percentile
FV_WIDEN_EPS = 0.02      # min change to call the FV gap "widened"


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(x) else x


def _pct(frac: Optional[float]) -> str:
    return "n/a" if frac is None else f"{frac * 100:+.0f}%"


def _quantile(series: pd.Series, q: float) -> Optional[float]:
    vals = series.dropna()
    if len(vals) < 5:
        return None
    return float(vals.quantile(q))


def _best_up(moves: Dict[str, Optional[float]]) -> float:
    """Largest positive move across windows (0.0 if none positive)."""
    return max([m for m in moves.values() if m is not None and m > 0], default=0.0)


def _worst_down(moves: Dict[str, Optional[float]]) -> float:
    """Most negative move across windows (0.0 if none negative)."""
    return min([m for m in moves.values() if m is not None and m < 0], default=0.0)


def _move_label(moves: Dict[str, Optional[float]], want_up: bool) -> str:
    """Plain label for the most extreme move in the wanted direction."""
    items = [(k, v) for k, v in moves.items() if v is not None]
    if not items:
        return ""
    if want_up:
        k, v = max(items, key=lambda kv: kv[1])
    else:
        k, v = min(items, key=lambda kv: kv[1])
    return f"{k} {_pct(v)}"


def price_fundamentals_divergence(
    full: pd.DataFrame,
    perf_full: pd.DataFrame,
    scores: pd.DataFrame,
    prior_fv: Optional[Dict[str, float]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Classify every ticker into the divergence flavors it triggers.

    Returns ``{"euphoria": [...], "opportunity": [...], "dislocation": [...]}``,
    each a list of row dicts ranked by divergence strength (most extreme first).
    ``prior_fv`` (optional) maps ticker -> last upside-to-FV reading; when present
    it lets Flavor B note that the discount *widened*.

    Each row dict carries: ticker, name, crest, side, bucket, today, ret_1w,
    ret_1m, upside_fv, valuation_tier, value_trap, value_pct, quality_pct,
    growth_pct, flavor, direction, strength, reason.
    """
    out: Dict[str, List[Dict[str, Any]]] = {
        "euphoria": [], "opportunity": [], "dislocation": []}
    if perf_full is None or perf_full.empty or full is None or full.empty:
        return out

    prior_fv = prior_fv or {}
    full_idx = full.set_index("Ticker")
    perf_idx = perf_full.set_index("Ticker")
    scores_idx = (scores.set_index("Ticker")
                  if scores is not None and not scores.empty else pd.DataFrame())

    # Universe quartile thresholds for the short-window returns.
    hi_1w = _quantile(perf_full.get("1W", pd.Series(dtype=float)), MOVE_PCTILE_HI / 100)
    lo_1w = _quantile(perf_full.get("1W", pd.Series(dtype=float)), MOVE_PCTILE_LO / 100)
    hi_1m = _quantile(perf_full.get("1M", pd.Series(dtype=float)), MOVE_PCTILE_HI / 100)
    lo_1m = _quantile(perf_full.get("1M", pd.Series(dtype=float)), MOVE_PCTILE_LO / 100)

    for ticker in perf_full["Ticker"].tolist():
        if ticker not in full_idx.index:
            continue
        frow = full_idx.loc[ticker]
        prow = perf_idx.loc[ticker]
        srow = scores_idx.loc[ticker] if ticker in scores_idx.index else None

        today = _f(prow.get("Today"))
        w1 = _f(prow.get("1W"))
        m1 = _f(prow.get("1M"))
        moves = {"today": today, "1W": w1, "1M": m1}
        if all(v is None for v in moves.values()):
            continue

        upside = _f(frow.get("upside_fv"))
        val_pct = _f(srow.get("Value")) if srow is not None else None
        qual_pct = _f(srow.get("Quality")) if srow is not None else None
        grow_pct = _f(srow.get("Growth")) if srow is not None else None

        base = {
            "ticker": ticker,
            "name": str(frow.get("Name") or ""),
            "crest": str(frow.get("Crest") or ""),
            "side": str(frow.get("Side") or ""),
            "bucket": str(frow.get("Primary Bucket") or ""),
            "today": today, "ret_1w": w1, "ret_1m": m1,
            "upside_fv": upside,
            "valuation_tier": frow.get("valuation_tier"),
            "value_trap": bool(frow.get("value_trap"))
            if frow.get("value_trap") is not None else None,
            "value_pct": val_pct, "quality_pct": qual_pct, "growth_pct": grow_pct,
        }

        strong_up = ((hi_1w is not None and w1 is not None and w1 >= hi_1w) or
                     (hi_1m is not None and m1 is not None and m1 >= hi_1m) or
                     (today is not None and today >= TODAY_BIG))
        strong_down = ((lo_1w is not None and w1 is not None and w1 <= lo_1w) or
                       (lo_1m is not None and m1 is not None and m1 <= lo_1m) or
                       (today is not None and today <= -TODAY_BIG))

        # ---- Flavor A — price running ahead of value (euphoria) -----------
        if strong_up and upside is not None and upside <= FV_PREMIUM:
            up_mag = _best_up(moves)
            trap = " · value-trap flag set" if base["value_trap"] else ""
            reason = (f"{_move_label(moves, want_up=True)} while trading "
                      f"{abs(upside) * 100:.0f}% above fair value "
                      f"(upside-to-FV {_pct(upside)}){trap}")
            row = dict(base, flavor="euphoria", direction="up",
                       strength=up_mag + abs(upside), reason=reason)
            out["euphoria"].append(row)

        # ---- Flavor B — price falling away from value (opportunity) --------
        if strong_down and upside is not None and upside >= FV_DISCOUNT:
            down_mag = abs(_worst_down(moves))
            widen = ""
            prev = prior_fv.get(ticker)
            if prev is not None and (upside - prev) >= FV_WIDEN_EPS:
                widen = f"; discount widened from {_pct(prev)} to {_pct(upside)}"
            reason = (f"{_move_label(moves, want_up=False)} while trading "
                      f"{upside * 100:.0f}% below fair value "
                      f"(upside-to-FV {_pct(upside)}){widen}")
            row = dict(base, flavor="opportunity", direction="down",
                       strength=down_mag + upside, reason=reason)
            out["opportunity"].append(row)

        # ---- Flavor C — momentum vs fundamentals dislocation --------------
        # Up hard on weak fundamentals.
        weak = [p for p in (qual_pct, val_pct, grow_pct) if p is not None]
        if strong_up and len(weak) >= 2 and sum(
                1 for p in (qual_pct, val_pct) if p is not None and p < FACTOR_LOW) >= 2:
            up_mag = _best_up(moves)
            facs = _factor_phrase(qual_pct, val_pct, grow_pct)
            reason = (f"{_move_label(moves, want_up=True)}; {facs} — "
                      f"move not supported by fundamentals")
            avg_low = np.mean([p for p in weak])
            row = dict(base, flavor="dislocation", direction="up",
                       strength=up_mag * (1.0 - avg_low / 100.0), reason=reason)
            out["dislocation"].append(row)
        # Down hard despite strong fundamentals.
        elif strong_down and qual_pct is not None and grow_pct is not None and \
                qual_pct >= FACTOR_HIGH and grow_pct >= FACTOR_HIGH:
            down_mag = abs(_worst_down(moves))
            facs = _factor_phrase(qual_pct, val_pct, grow_pct)
            reason = (f"{_move_label(moves, want_up=False)}; {facs} — "
                      f"sell-off not matched by fundamentals")
            avg_hi = np.mean([qual_pct, grow_pct])
            row = dict(base, flavor="dislocation", direction="down",
                       strength=down_mag * (avg_hi / 100.0), reason=reason)
            out["dislocation"].append(row)

    for key in out:
        out[key].sort(key=lambda r: -r["strength"])
    return out


def _factor_phrase(qual: Optional[float], val: Optional[float],
                   grow: Optional[float]) -> str:
    parts = []
    if qual is not None:
        parts.append(f"Quality {qual:.0f}th pct")
    if val is not None:
        parts.append(f"Value {val:.0f}th pct")
    if grow is not None:
        parts.append(f"Growth {grow:.0f}th pct")
    return ", ".join(parts)


def divergence_counts(groups: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """Counts per flavor for the summary strip."""
    return {k: len(v) for k, v in groups.items()}
