"""Thesis records — structured, conviction-weighted decision log per ticker.

Each ticker gets one JSON record under ``theses/{ticker}.json`` (via
``core.store``: local by default, S3 when ``PARKNOVA_S3_BUCKET`` is set). A record
holds the *current* thesis plus a reverse-appended ``journal`` of timestamped
entries, each freezing an **evidence snapshot** captured from the app's existing
factor/timing data at that moment — so conviction and the data behind it are
plottable over time.

Evidence is *captured* from already-computed frames (factor scores, timing
columns, performance windows) — this module never recomputes that logic.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

import pandas as pd

from core import factors as fc
from core import store

STANCES = ["Watch", "Tactical long", "Core long", "Trim/Exit", "Avoid"]
_PERF_WINDOWS = ["Today", "1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"]


def _key(ticker: str) -> str:
    return f"theses/{ticker.upper()}.json"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


# ---------------------------------------------------------------------------
# Evidence snapshot — frozen, point-in-time, from existing data only
# ---------------------------------------------------------------------------
def capture_evidence(ticker: str, full_row: Optional[pd.Series],
                     scores_row: Optional[pd.Series],
                     perf_row: Optional[pd.Series],
                     imported_for_ticker: Optional[List[dict]] = None
                     ) -> Dict[str, Any]:
    """Freeze an evidence snapshot from already-computed frames (no recompute)."""
    snap: Dict[str, Any] = {"captured_at": _now(), "ticker": ticker.upper()}

    # Factor percentiles + composite (universe-relative, from core.factors).
    factors: Dict[str, Any] = {}
    if scores_row is not None:
        for name in fc.FACTOR_NAMES:
            if name in scores_row.index:
                factors[name] = _num(scores_row.get(name))
        if "Composite" in scores_row.index:
            factors["Composite"] = _num(scores_row.get("Composite"))
    snap["factors"] = factors

    # Valuation + timing (from core.timing columns already on the full frame).
    val: Dict[str, Any] = {}
    if full_row is not None:
        val = {
            "last_price": _num(full_row.get("Last Price")),
            "fair_value": _num(full_row.get("Fair Value")),
            "upside_fv": _num(full_row.get("upside_fv")),
            "forward_pe": _num(full_row.get("Price/Earnings (Forward)")),
            "valuation_tier": full_row.get("valuation_tier"),
            "value_trap": bool(full_row.get("value_trap"))
            if full_row.get("value_trap") is not None else None,
        }
    snap["valuation"] = val

    # Crest / Side position.
    if full_row is not None:
        snap["position"] = {
            "side": full_row.get("Side"),
            "crest": full_row.get("Crest"),
            "primary_bucket": full_row.get("Primary Bucket"),
            "crest_rationale": full_row.get("Crest Rationale"),
        }
    else:
        snap["position"] = {}

    # Returns: Morningstar trailing + live momentum windows.
    returns: Dict[str, Any] = {}
    if perf_row is not None:
        for w in _PERF_WINDOWS:
            if w in perf_row.index:
                returns[w] = _num(perf_row.get(w))
        if "Momentum score" in perf_row.index:
            snap["momentum_score"] = _num(perf_row.get("Momentum score"))
    elif full_row is not None:
        for w, col in [("YTD", "Total Return (YTD)"), ("1Y", "Total Return (1Y)"),
                       ("3Y", "Total Return (3Y)"), ("5Y", "Total Return (5Y)")]:
            returns[w] = _num(full_row.get(col))
    snap["returns"] = returns

    # Imported filing analyses for this ticker (count + latest net_read).
    imp = imported_for_ticker or []
    latest_net = None
    if imp:
        def _ts(o):
            return (o.get("filing_date") or o.get("analyzed_at") or "")
        latest = sorted(imp, key=_ts)[-1]
        latest_net = (latest.get("analysis") or {}).get("net_read")
    snap["filings"] = {"imported_count": len(imp), "latest_net_read": latest_net}
    return snap


# ---------------------------------------------------------------------------
# Record CRUD
# ---------------------------------------------------------------------------
def read_thesis(ticker: str) -> Optional[Dict[str, Any]]:
    return store.read_json(_key(ticker))


def list_theses() -> List[Dict[str, Any]]:
    """All saved thesis records (skips unreadable ones)."""
    out: List[Dict[str, Any]] = []
    for key in store.list_keys("theses/"):
        obj = store.read_json(key)
        if isinstance(obj, dict) and obj.get("ticker"):
            out.append(obj)
    return out


def empty_current() -> Dict[str, Any]:
    """A blank `current` block for the editor's empty state."""
    return {"stance": "Watch", "conviction": 5, "time_horizon": "",
            "position_size_pct": 0.0, "bull_case": "", "bear_case": "",
            "valuation_view": "", "catalysts": [], "risks": [],
            "crest_note": "", "defined_exit": ""}


def save_thesis(ticker: str, current: Dict[str, Any],
                evidence: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """Write the current thesis and append a timestamped journal entry.

    The journal entry freezes the conviction + evidence snapshot at save time.
    Returns the persisted record.
    """
    ticker = ticker.upper()
    record = read_thesis(ticker) or {"ticker": ticker, "journal": []}
    ts = _now()
    record["ticker"] = ticker
    record["updated_at"] = ts
    record["current"] = current
    record["evidence_snapshot"] = evidence
    entry = {
        "ts": ts,
        "note": note or "Thesis updated",
        "stance": current.get("stance"),
        "conviction": current.get("conviction"),
        "position_size_pct": current.get("position_size_pct"),
        "evidence_snapshot": evidence,
    }
    record.setdefault("journal", []).append(entry)
    store.write_json(_key(ticker), record)
    return record


def conviction_history(record: Dict[str, Any]) -> pd.DataFrame:
    """DataFrame of {ts, conviction, stance} from the journal (chronological)."""
    rows = []
    for e in (record or {}).get("journal", []):
        c = _num(e.get("conviction"))
        if c is None:
            continue
        rows.append({"ts": pd.to_datetime(e.get("ts"), errors="coerce"),
                     "conviction": c, "stance": e.get("stance")})
    df = pd.DataFrame(rows)
    return df.dropna(subset=["ts"]).sort_values("ts") if not df.empty else df


# ---------------------------------------------------------------------------
# Export / import (zero-cost AI drafting via the skill/import pattern)
# ---------------------------------------------------------------------------
def export_brief(ticker: str, evidence: Dict[str, Any],
                 current: Optional[Dict[str, Any]]) -> str:
    """A paste-ready JSON brief (evidence + any existing thesis) for Claude."""
    import json
    brief = {
        "task": "Draft/refine an investment thesis from the evidence below. "
                "Return ONLY a JSON object matching the 'current' schema "
                "(stance, conviction 1-10, time_horizon, position_size_pct, "
                "bull_case, bear_case, valuation_view, catalysts[], risks[], "
                "crest_note, defined_exit). Be specific; tie the crest_note to "
                "the capex-cycle timing and define a concrete exit.",
        "ticker": ticker.upper(),
        "evidence_snapshot": evidence,
        "existing_thesis": current or None,
        "schema_hint": empty_current(),
    }
    return json.dumps(brief, indent=2, default=str)


def parse_imported_thesis(text_or_obj: Any) -> Optional[Dict[str, Any]]:
    """Coerce a drafted-thesis JSON (full record or a bare 'current') into a
    `current` block for the editor. Returns None if unusable."""
    import json
    obj = text_or_obj
    if isinstance(text_or_obj, str):
        try:
            obj = json.loads(text_or_obj)
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    cur = obj.get("current") if "current" in obj else obj
    if not isinstance(cur, dict):
        return None
    base = empty_current()
    for k in base:
        if k in cur and cur[k] is not None:
            base[k] = cur[k]
    return base
