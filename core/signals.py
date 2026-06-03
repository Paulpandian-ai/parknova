"""Signal detection — crest-rotation inflection, stretch warnings, opportunity hints.

Pure functions only; no Streamlit imports. All signals are observations about
present conditions, not forecasts or investment advice. Every result carries the
underlying numbers so the UI can show exactly what triggered each signal.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core import store

logger = logging.getLogger("parknova.signals")

# ---------------------------------------------------------------------------
# Thresholds — documented here as a single source of truth
# ---------------------------------------------------------------------------
SLOPE_RECENT_DAYS = 10     # "recent" slope window for inflection detection
SLOPE_PRIOR_DAYS = 20      # comparison window for prior slope

STRETCH_1Y_PCTILE = 90     # top-decile 1Y return = stretched
STRETCH_FV_PREMIUM = -0.30 # upside-to-FV < −30% → large premium to fair value
MA_SHORT = 50              # 50-day MA
MA_STRETCH = 0.20          # >20% above MA = price-stretch signal

OPP_MOM_QUARTILE = 25      # bottom-quartile 1M = momentum pressure
OPP_QUALITY_FLOOR = 67     # top-third quality percentile
OPP_GROWTH_FLOOR = 67      # top-third growth percentile

PRIOR_FV_KEY = "signals/prior_fv.json"


# ---------------------------------------------------------------------------
# Signal 1 — Crest-rotation inflection
# ---------------------------------------------------------------------------

def _poly_slope(series: pd.Series, n: int) -> Optional[float]:
    """Linear slope (units / trading day) over the last ``n`` points via polyfit."""
    if len(series) < n:
        return None
    tail = series.dropna().iloc[-n:]
    if len(tail) < 3:
        return None
    x = np.arange(len(tail), dtype=float)
    try:
        coef = np.polyfit(x, tail.values.astype(float), 1)
        return float(coef[0])
    except Exception:
        return None


def rotation_inflection(wide_full: pd.DataFrame,
                        perf_full: pd.DataFrame) -> Dict[str, Any]:
    """Detect whether the Early-minus-Late spread is inflecting.

    Returns a result dict with these keys:
      regime          : "inflection_late" | "inflection_early" |
                        "early_leading" | "late_leading" | "neutral"
      advisory        : plain-English description of the present condition
      spread_current  : float | None  — latest spread value (index pts)
      slope_recent    : float | None  — slope over SLOPE_RECENT_DAYS (pts/day)
      slope_prior     : float | None  — slope over SLOPE_PRIOR_DAYS (pts/day)
      early_1m        : float | None  — median 1M return of Early-crest names
      late_1m         : float | None  — median 1M return of Late-crest names
      spread_series   : pd.Series | None — full history for the chart
      inflection_date : pd.Timestamp | None — approximate turn date
    """
    from core import crest_index as ci

    empty: Dict[str, Any] = {
        "regime": "neutral", "advisory": "Insufficient crest index data.",
        "spread_current": None, "slope_recent": None, "slope_prior": None,
        "early_1m": None, "late_1m": None,
        "spread_series": None, "inflection_date": None,
    }

    if wide_full is None or wide_full.empty:
        return empty

    spread_raw = ci.spread_series(wide_full, "Early", "Late")
    if spread_raw is None:
        return empty
    spread = spread_raw.dropna()
    if len(spread) < SLOPE_RECENT_DAYS + SLOPE_PRIOR_DAYS:
        empty["advisory"] = (
            f"Insufficient history for inflection detection "
            f"(need {SLOPE_RECENT_DAYS + SLOPE_PRIOR_DAYS} days, "
            f"have {len(spread)}).")
        empty["spread_series"] = spread_raw
        return empty

    slope_recent = _poly_slope(spread, SLOPE_RECENT_DAYS)
    # Prior slope: fit over the SLOPE_PRIOR_DAYS window ending just before the
    # recent window starts.
    prior_end = len(spread) - SLOPE_RECENT_DAYS
    slope_prior = _poly_slope(spread.iloc[:prior_end], SLOPE_PRIOR_DAYS)
    spread_current = float(spread.iloc[-1])

    # Median 1M returns by crest layer from perf_full.
    early_1m: Optional[float] = None
    late_1m: Optional[float] = None
    if perf_full is not None and not perf_full.empty and "1M" in perf_full.columns:
        for crest_val, attr in [("Early", "early_1m"), ("Late", "late_1m")]:
            rows = perf_full[perf_full.get("Crest", pd.Series(dtype=str)) == crest_val]["1M"].dropna() \
                if "Crest" in perf_full.columns else pd.Series(dtype=float)
            if len(rows):
                locals()[attr] if False else None  # just force name lookup
                if crest_val == "Early":
                    early_1m = float(rows.median())
                else:
                    late_1m = float(rows.median())

    # Both conditions required to fire an inflection advisory:
    #   (a) slope flips sign from prior to recent
    #   (b) 1M median of the gaining crest layer exceeds the other
    slope_flipped_to_late = (slope_recent is not None and slope_recent < 0 and
                             slope_prior is not None and slope_prior > 0)
    slope_flipped_to_early = (slope_recent is not None and slope_recent > 0 and
                              slope_prior is not None and slope_prior < 0)
    late_1m_higher = (late_1m is not None and early_1m is not None and late_1m > early_1m)
    early_1m_higher = (early_1m is not None and late_1m is not None and early_1m > late_1m)

    def _fmt_pct(v):
        return f"{v*100:+.1f}%" if v is not None else "—"

    def _fmt_slope(v):
        return f"{v:+.3f}" if v is not None else "—"

    if slope_flipped_to_late and late_1m_higher:
        inflection_date = _last_local_max(spread)
        advisory = (
            f"Rotation inflection — toward Late-crest. "
            f"The Early-minus-Late spread reversed (slope {_fmt_slope(slope_recent)}/day "
            f"vs prior {_fmt_slope(slope_prior)}/day) and Late-crest 1M median "
            f"({_fmt_pct(late_1m)}) now exceeds Early-crest 1M ({_fmt_pct(early_1m)}). "
            f"Current spread: {spread_current:.1f} pts. "
            f"Both the slope-flip and the 1M cross have triggered. "
            f"This is a condition worth reviewing — not a prediction."
        )
        return {
            "regime": "inflection_late", "advisory": advisory,
            "spread_current": spread_current,
            "slope_recent": slope_recent, "slope_prior": slope_prior,
            "early_1m": early_1m, "late_1m": late_1m,
            "spread_series": spread_raw, "inflection_date": inflection_date,
        }

    if slope_flipped_to_early and early_1m_higher:
        inflection_date = _last_local_min(spread)
        advisory = (
            f"Rotation inflection — toward Early-crest. "
            f"The Early-minus-Late spread re-widened (slope {_fmt_slope(slope_recent)}/day "
            f"vs prior {_fmt_slope(slope_prior)}/day) and Early-crest 1M median "
            f"({_fmt_pct(early_1m)}) now exceeds Late-crest 1M ({_fmt_pct(late_1m)}). "
            f"Current spread: {spread_current:.1f} pts. "
            f"Both conditions have triggered. "
            f"This is a condition worth reviewing — not a prediction."
        )
        return {
            "regime": "inflection_early", "advisory": advisory,
            "spread_current": spread_current,
            "slope_recent": slope_recent, "slope_prior": slope_prior,
            "early_1m": early_1m, "late_1m": late_1m,
            "spread_series": spread_raw, "inflection_date": inflection_date,
        }

    # No inflection — state current regime.
    if spread_current > 0:
        regime = "early_leading"
        conds_met = []
        if slope_flipped_to_late:
            conds_met.append("spread slope has recently turned negative")
        if late_1m_higher:
            conds_met.append(f"Late 1M ({_fmt_pct(late_1m)}) > Early 1M ({_fmt_pct(early_1m)})")
        partial = f" Partial signal: {', '.join(conds_met)}." if conds_met else ""
        advisory = (
            f"Early-crest leading; no inflection (both conditions not yet met). "
            f"Spread: {spread_current:.1f} pts · "
            f"recent slope {_fmt_slope(slope_recent)}/day · "
            f"Early 1M {_fmt_pct(early_1m)} vs Late 1M {_fmt_pct(late_1m)}."
            f"{partial}"
        )
    else:
        regime = "late_leading"
        advisory = (
            f"Late-crest leading; no Early-crest inflection detected. "
            f"Spread: {spread_current:.1f} pts · "
            f"recent slope {_fmt_slope(slope_recent)}/day · "
            f"Early 1M {_fmt_pct(early_1m)} vs Late 1M {_fmt_pct(late_1m)}."
        )

    return {
        "regime": regime, "advisory": advisory,
        "spread_current": spread_current,
        "slope_recent": slope_recent, "slope_prior": slope_prior,
        "early_1m": early_1m, "late_1m": late_1m,
        "spread_series": spread_raw, "inflection_date": None,
    }


def _last_local_max(s: pd.Series) -> Optional[pd.Timestamp]:
    """Most recent local maximum — approximate inflection point toward Late."""
    arr = s.values
    for i in range(len(arr) - 2, 0, -1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            return s.index[i]
    return s.index[-1]


def _last_local_min(s: pd.Series) -> Optional[pd.Timestamp]:
    """Most recent local minimum — approximate inflection point toward Early."""
    arr = s.values
    for i in range(len(arr) - 2, 0, -1):
        if arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            return s.index[i]
    return s.index[-1]


# ---------------------------------------------------------------------------
# Signal 2 — Stretch / mean-reversion warnings
# ---------------------------------------------------------------------------

def compute_ma_distance(series: pd.Series,
                        window: int = MA_SHORT) -> Optional[float]:
    """Fractional distance of last close above the ``window``-day MA.

    Returns (last − MA) / MA (positive = above). None if series too short.
    """
    if series is None or len(series) < window:
        return None
    ma = float(series.rolling(window).mean().iloc[-1])
    if pd.isna(ma) or ma == 0:
        return None
    return float(series.iloc[-1]) / ma - 1.0


def stretch_warnings(
    full: pd.DataFrame,
    perf_full: pd.DataFrame,
    histories: Optional[Dict[str, pd.Series]] = None,
) -> List[Dict[str, Any]]:
    """Return stretch/mean-reversion warnings ranked by stacked conditions.

    ``histories`` is an optional {ticker: adjusted-close Series} mapping used
    to compute MA distance. When absent, MA distance is skipped (noted in the
    warning).

    Each result dict:
      ticker, name, crest, bucket,
      conditions (int — stacking weight),
      reasons (list[str]),
      value_trap (bool),
      one_y_raw (fraction | None),
      one_y_pct (universe percentile 0-100 | None),
      ma_distance (fraction | None),
      ma_note (str | None),
      upside_fv (fraction | None),
      forward_pe (float | None)
    """
    if perf_full.empty:
        return []

    one_y_vals = (perf_full["1Y"].dropna()
                  if "1Y" in perf_full.columns else pd.Series(dtype=float))
    n_universe = max(len(one_y_vals), 1)

    full_idx = full.set_index("Ticker")
    perf_idx = perf_full.set_index("Ticker")

    results: List[Dict[str, Any]] = []

    for ticker in full["Ticker"].tolist():
        if ticker not in full_idx.index:
            continue
        frow = full_idx.loc[ticker]
        prow = perf_idx.loc[ticker] if ticker in perf_idx.index else None

        conditions = 0
        reasons: List[str] = []

        # ---- Value-trap (double-weight — most diagnostic) ----
        is_trap = bool(frow.get("value_trap", False))
        fwd_pe_raw = frow.get("Price/Earnings (Forward)") or frow.get("Price/Earnings")
        forward_pe = float(fwd_pe_raw) if fwd_pe_raw and pd.notna(fwd_pe_raw) else None

        if is_trap:
            conditions += 2
            pe_s = f"{forward_pe:.1f}x" if forward_pe else "?"
            one_y_s = ""
            if prow is not None and "1Y" in prow.index and pd.notna(prow["1Y"]):
                one_y_s = f" · 1Y {float(prow['1Y'])*100:+.0f}%"
            reasons.append(
                f"Value-trap pattern: fwd P/E {pe_s} (< 15x) on Early-crest "
                f"deep cyclical{one_y_s} — cheap multiple at likely cycle top"
            )

        # ---- 1Y top-decile ----
        one_y_raw = None
        one_y_pct = None
        if prow is not None and "1Y" in prow.index and pd.notna(prow["1Y"]):
            one_y_raw = float(prow["1Y"])
            one_y_pct = float((one_y_vals < one_y_raw).sum()) / n_universe * 100
        if one_y_pct is not None and one_y_pct >= STRETCH_1Y_PCTILE:
            conditions += 1
            reasons.append(
                f"1Y return {one_y_raw*100:+.0f}% · {one_y_pct:.0f}th universe "
                f"percentile (top decile)"
            )

        # ---- MA distance ----
        ma_distance = None
        ma_note = None
        series = (histories or {}).get(ticker)
        if series is not None and not series.empty:
            ma_distance = compute_ma_distance(series, MA_SHORT)
            if ma_distance is not None and ma_distance > MA_STRETCH:
                conditions += 1
                reasons.append(
                    f"Price {ma_distance*100:+.0f}% above {MA_SHORT}-day MA"
                )
        elif histories is not None:
            ma_note = "Price history unavailable — MA not computed"
        else:
            ma_note = "MA skipped (histories not loaded)"

        # ---- Premium to fair value ----
        upside_fv_raw = frow.get("upside_fv")
        upside_fv = (float(upside_fv_raw)
                     if upside_fv_raw is not None and pd.notna(upside_fv_raw)
                     else None)
        if upside_fv is not None and upside_fv <= STRETCH_FV_PREMIUM:
            conditions += 1
            reasons.append(
                f"Trading {abs(upside_fv)*100:.0f}% above Morningstar fair value "
                f"(upside-to-FV {upside_fv*100:+.0f}%)"
            )

        if conditions == 0:
            continue

        results.append({
            "ticker": ticker,
            "name": str(frow.get("Name") or ""),
            "crest": str(frow.get("Crest") or ""),
            "bucket": str(frow.get("Primary Bucket") or ""),
            "conditions": conditions,
            "reasons": reasons,
            "value_trap": is_trap,
            "one_y_raw": one_y_raw,
            "one_y_pct": one_y_pct,
            "ma_distance": ma_distance,
            "ma_note": ma_note,
            "upside_fv": upside_fv,
            "forward_pe": forward_pe,
        })

    results.sort(key=lambda r: -r["conditions"])
    return results


# ---------------------------------------------------------------------------
# Signal 3 — Opportunity hints
# ---------------------------------------------------------------------------

def load_prior_fv() -> Dict[str, float]:
    """Load last-persisted upside-to-FV readings keyed by ticker."""
    obj = store.read_json(PRIOR_FV_KEY)
    if not isinstance(obj, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in obj.items():
        try:
            f = float(v)
            if not np.isnan(f):
                out[str(k)] = f
        except (TypeError, ValueError):
            pass
    return out


def save_prior_fv(full: pd.DataFrame) -> None:
    """Persist current upside_fv readings for crossing detection next session."""
    if "upside_fv" not in full.columns:
        return
    readings: Dict[str, float] = {}
    for _, row in full.iterrows():
        t = str(row.get("Ticker") or "")
        v = row.get("upside_fv")
        if t and v is not None and pd.notna(v):
            readings[t] = float(v)
    if readings:
        store.write_json(PRIOR_FV_KEY, readings)


def opportunity_hints(
    full: pd.DataFrame,
    perf_full: pd.DataFrame,
    scores: pd.DataFrame,
    rotation_regime: str = "neutral",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return opportunity hints and whether a prior FV baseline existed.

    Returns ``(hints, had_prior_baseline)``. ``had_prior_baseline`` is False on
    the very first run; the UI notes that crossing signals need a prior reading.

    Each hint dict:
      ticker, name, crest, bucket,
      hint_type ("fv_crossing" | "fundamental_divergence" | "late_crest_leader"),
      priority (int — for ranking),
      reasons (list[str]),
      upside_fv (fraction | None),
      quality_pct, growth_pct (0-100 | None),
      momentum_1m (fraction | None)
    """
    prior_fv = load_prior_fv()
    had_prior = bool(prior_fv)

    if perf_full.empty:
        return [], had_prior

    full_idx = full.set_index("Ticker")
    perf_idx = perf_full.set_index("Ticker")
    scores_idx = scores.set_index("Ticker") if not scores.empty else pd.DataFrame()

    # Bottom-quartile 1M threshold
    one_m_vals = (perf_full["1M"].dropna()
                  if "1M" in perf_full.columns else pd.Series(dtype=float))
    q25_1m = float(one_m_vals.quantile(OPP_MOM_QUARTILE / 100)) if len(one_m_vals) > 4 else None

    hints: List[Dict[str, Any]] = []

    for ticker in full["Ticker"].tolist():
        if ticker not in full_idx.index:
            continue
        frow = full_idx.loc[ticker]
        prow = perf_idx.loc[ticker] if ticker in perf_idx.index else None
        srow = scores_idx.loc[ticker] if ticker in scores_idx.index else None

        upside_raw = frow.get("upside_fv")
        upside_fv = (float(upside_raw)
                     if upside_raw is not None and pd.notna(upside_raw) else None)
        quality = (float(srow["Quality"])
                   if srow is not None and "Quality" in srow.index
                   and pd.notna(srow["Quality"]) else None)
        growth = (float(srow["Growth"])
                  if srow is not None and "Growth" in srow.index
                  and pd.notna(srow["Growth"]) else None)
        mom_1m = (float(prow["1M"])
                  if prow is not None and "1M" in prow.index
                  and pd.notna(prow["1M"]) else None)
        mom_3m = (float(prow["3M"])
                  if prow is not None and "3M" in prow.index
                  and pd.notna(prow["3M"]) else None)
        crest = str(frow.get("Crest") or "")

        priority = 0
        reasons: List[str] = []
        hint_type: Optional[str] = None

        def _p(v):
            return f"{v*100:+.1f}%" if v is not None else "—"

        # 1. Fair-value crossing (prior reading existed and sign flipped)
        prior_val = prior_fv.get(ticker)
        if (had_prior and prior_val is not None and upside_fv is not None
                and prior_val < 0 <= upside_fv):
            priority += 3
            hint_type = "fv_crossing"
            reasons.append(
                f"Crossed from premium to discount vs Morningstar fair value: "
                f"prior {_p(prior_val)} → now {_p(upside_fv)} upside"
            )

        # 2. Momentum/fundamental divergence
        mom_weak = (q25_1m is not None and mom_1m is not None
                    and mom_1m <= q25_1m)
        fund_strong = (quality is not None and quality >= OPP_QUALITY_FLOOR and
                       growth is not None and growth >= OPP_GROWTH_FLOOR)
        if mom_weak and fund_strong:
            priority += 2
            if hint_type is None:
                hint_type = "fundamental_divergence"
            reasons.append(
                f"Near-term weakness ({_p(mom_1m)} 1M, bottom quartile) while "
                f"Quality {quality:.0f}/100 and Growth {growth:.0f}/100 remain "
                f"in top-third — price under pressure, fundamentals intact"
            )

        # 3. Late-crest leaders when rotation is toward Late
        if rotation_regime == "inflection_late" and crest == "Late":
            # Accelerating = 1M better than 3M
            if (mom_1m is not None and mom_3m is not None
                    and mom_1m > mom_3m and mom_1m > 0):
                priority += 1
                if hint_type is None:
                    hint_type = "late_crest_leader"
                reasons.append(
                    f"Late-crest with accelerating momentum ({_p(mom_1m)} 1M "
                    f"vs {_p(mom_3m)} 3M) as rotation shifts toward Late-crest"
                )

        if priority == 0:
            continue

        hints.append({
            "ticker": ticker,
            "name": str(frow.get("Name") or ""),
            "crest": crest,
            "bucket": str(frow.get("Primary Bucket") or ""),
            "hint_type": hint_type or "other",
            "priority": priority,
            "reasons": reasons,
            "upside_fv": upside_fv,
            "quality_pct": quality,
            "growth_pct": growth,
            "momentum_1m": mom_1m,
        })

    hints.sort(key=lambda h: -h["priority"])
    return hints, had_prior
