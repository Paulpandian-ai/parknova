"""Compare two dated Morningstar exports — what the analysts changed.

Pure, testable diff over two already-loaded Morningstar frames (same loader, so
percent-units/typing already match — never re-parsed here). Produces three change
groups, loudest first:

  1. Analyst-judgment changes — rating, moat, fair-value revisions, letter
     grades, fair-value uncertainty. Lead with these; downgrades/cuts are louder.
  2. Fundamental shifts — material numeric moves beyond per-field thresholds
     (a new quarter's data flowing in), ranked by how far past threshold.
  3. Universe structural changes — added / dropped tickers.

Factual only: this describes what Morningstar changed. No buy/sell interpretation
(a fair-value cut is a "warning"-colored fact, not advice). All thresholds are
tunable constants at the top.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Archive discovery
# ---------------------------------------------------------------------------
ARCHIVE_DIR = os.path.join("data", "morningstar_archive")
# Match any dated .xlsx in the archive dir by its trailing YYYY-MM-DD — tolerant
# of space vs underscore in the stem (..._from MorningStar_DATE.xlsx and
# ..._from_MorningStar_DATE.xlsx both qualify). The date filter does the work.
ARCHIVE_GLOB = "*.xlsx"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------
FV_REVISION_PCT = 0.02          # |ΔFair Value| / old > 2% counts as a revision

# Fundamental fields: (column, kind, threshold). kind "pts" = absolute move in
# the column's own units (percent-unit margins/growth, ratio for D/E); kind "rel"
# = relative move |Δ|/|old|.
FUNDAMENTAL_FIELDS: List[Tuple[str, str, float]] = [
    ("Gross Margin", "pts", 2.0),
    ("Operating Margin", "pts", 2.0),
    ("Net Margin", "pts", 2.0),
    ("Return on Equity", "pts", 2.0),
    ("Return on Invested Capital", "pts", 2.0),
    ("Revenue Growth (1Y)", "pts", 3.0),
    ("Price/Earnings (Forward)", "rel", 0.10),
    ("Total Debt/Equity", "rel", 0.10),
]

# Ordinal ranks so we can call a change an upgrade/downgrade / widen/narrow.
MOAT_RANK = {"None": 0, "Narrow": 1, "Wide": 2}
UNCERTAINTY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Very High": 3}
GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
GRADE_FIELDS = ["Profitability Grade", "Growth Grade"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def archive_date(path: str) -> Optional[str]:
    """Extract the YYYY-MM-DD date stamp from an archive filename."""
    m = _DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def list_archives(root: str = ARCHIVE_DIR) -> List[Tuple[str, str]]:
    """Return ``[(date, path), ...]`` for dated archives, sorted ascending."""
    if not os.path.isdir(root):
        return []
    out: List[Tuple[str, str]] = []
    for p in glob.glob(os.path.join(root, ARCHIVE_GLOB)):
        d = archive_date(p)
        if d:
            out.append((d, p))
    out.sort(key=lambda t: t[0])
    return out


def working_file_path() -> str:
    """Path to the current working Morningstar export the app already loads."""
    from data import morningstar as ms
    return ms._excel_path()


def latest_archive_and_working(root: str = ARCHIVE_DIR) -> Optional[Dict[str, str]]:
    """Selector for the What Changed view: most-recent archive vs working file.

    Prior snapshot (old) = the single most-recent dated archive in ``root``.
    Latest snapshot (new) = the current working file at the repo root (the
    no-date export the app already loads). Returns
    ``{old_date, old_path, new_date, new_path}`` with ``new_date`` a label
    ("current working file"), or None when there are zero dated archives.
    """
    archives = list_archives(root)
    if not archives:
        return None
    old_date, old_path = archives[-1]   # max trailing date
    return {"old_date": old_date, "old_path": old_path,
            "new_date": "current working file",
            "new_path": working_file_path()}


# ---------------------------------------------------------------------------
# Core diff
# ---------------------------------------------------------------------------
def _upside(fv: Optional[float], price: Optional[float]) -> Optional[float]:
    """Upside-to-fair-value as a fraction: (FV − price) / price."""
    if fv is None or price is None or price == 0:
        return None
    return (fv - price) / price


def compare_snapshots(df_old: pd.DataFrame, df_new: pd.DataFrame,
                      old_date: Optional[str] = None,
                      new_date: Optional[str] = None) -> Dict[str, Any]:
    """Diff two Morningstar frames (joined on Ticker). See module docstring.

    Returns a dict with: old_date, new_date, ratings, moats, fair_values,
    grades, uncertainty, fundamentals, added, dropped, counts. Each change list
    is pre-sorted with the loudest items first (downgrades / cuts / rises /
    biggest moves). Tickers present in only one file go to added/dropped only —
    never into the change tables (no null-diff noise).
    """
    result: Dict[str, Any] = {
        "old_date": old_date, "new_date": new_date,
        "ratings": [], "moats": [], "fair_values": [], "grades": [],
        "uncertainty": [], "fundamentals": [], "added": [], "dropped": [],
        "counts": {},
    }
    if df_old is None or df_new is None or df_old.empty or df_new.empty:
        if df_new is not None and not df_new.empty and (df_old is None or df_old.empty):
            result["added"] = sorted(df_new["Ticker"].astype(str).tolist())
        result["counts"] = _counts(result)
        return result

    old_idx = df_old.set_index("Ticker")
    new_idx = df_new.set_index("Ticker")
    old_tickers = set(old_idx.index)
    new_tickers = set(new_idx.index)
    common = old_tickers & new_tickers

    result["added"] = sorted(new_tickers - old_tickers)
    result["dropped"] = sorted(old_tickers - new_tickers)

    def _name(t: str) -> str:
        n = _str(new_idx.loc[t].get("Name")) if t in new_idx.index else None
        if n is None and t in old_idx.index:
            n = _str(old_idx.loc[t].get("Name"))
        return n or ""

    for t in common:
        o = old_idx.loc[t]
        n = new_idx.loc[t]
        name = _name(t)

        # --- Rating (stars; numeric, higher = better) --------------------
        ro, rn = _num(o.get("Morningstar Rating for Stocks")), \
            _num(n.get("Morningstar Rating for Stocks"))
        if ro is not None and rn is not None and ro != rn:
            result["ratings"].append({
                "ticker": t, "name": name, "old": ro, "new": rn,
                "delta": rn - ro,
                "direction": "upgrade" if rn > ro else "downgrade"})

        # --- Economic moat ----------------------------------------------
        mo, mn = _str(o.get("Economic Moat")), _str(n.get("Economic Moat"))
        if mo is not None and mn is not None and mo != mn:
            rank_d = MOAT_RANK.get(mn, 0) - MOAT_RANK.get(mo, 0)
            result["moats"].append({
                "ticker": t, "name": name, "old": mo, "new": mn,
                "delta": rank_d,
                "direction": "widened" if rank_d > 0 else "narrowed"})

        # --- Fair value revision ----------------------------------------
        fo, fn = _num(o.get("Fair Value")), _num(n.get("Fair Value"))
        if fo is not None and fn is not None and fo != 0:
            pct = (fn - fo) / abs(fo)
            if abs(pct) > FV_REVISION_PCT:
                po, pn = _num(o.get("Last Price")), _num(n.get("Last Price"))
                result["fair_values"].append({
                    "ticker": t, "name": name, "old": fo, "new": fn,
                    "pct_change": pct,
                    "direction": "raise" if fn > fo else "cut",
                    "old_upside": _upside(fo, po),
                    "new_upside": _upside(fn, pn)})

        # --- Letter grades ----------------------------------------------
        for field in GRADE_FIELDS:
            go, gn = _str(o.get(field)), _str(n.get(field))
            if go is not None and gn is not None and go != gn \
                    and go in GRADE_RANK and gn in GRADE_RANK:
                rank_d = GRADE_RANK[gn] - GRADE_RANK[go]
                result["grades"].append({
                    "ticker": t, "name": name, "field": field,
                    "old": go, "new": gn, "delta": rank_d,
                    "direction": "upgrade" if rank_d > 0 else "downgrade"})

        # --- Fair-value uncertainty -------------------------------------
        uo, un = _str(o.get("Fair Value Uncertainty")), \
            _str(n.get("Fair Value Uncertainty"))
        if uo is not None and un is not None and uo != un \
                and uo in UNCERTAINTY_RANK and un in UNCERTAINTY_RANK:
            rank_d = UNCERTAINTY_RANK[un] - UNCERTAINTY_RANK[uo]
            result["uncertainty"].append({
                "ticker": t, "name": name, "old": uo, "new": un,
                "delta": rank_d,
                "direction": "rose" if rank_d > 0 else "fell"})

        # --- Material fundamental shifts --------------------------------
        for col, kind, thresh in FUNDAMENTAL_FIELDS:
            vo, vn = _num(o.get(col)), _num(n.get(col))
            if vo is None or vn is None:
                continue
            delta = vn - vo
            if kind == "pts":
                magnitude = abs(delta)
                rel = (delta / abs(vo)) if vo != 0 else None
            else:  # relative
                if vo == 0:
                    continue
                rel = delta / abs(vo)
                magnitude = abs(rel)
            if magnitude > thresh:
                result["fundamentals"].append({
                    "ticker": t, "name": name, "field": col, "kind": kind,
                    "old": vo, "new": vn, "delta": delta, "rel": rel,
                    "magnitude": magnitude,
                    "score": magnitude / thresh})  # × over threshold

    _sort_groups(result)
    result["counts"] = _counts(result)
    return result


def _sort_groups(result: Dict[str, Any]) -> None:
    """Loudest first within each group."""
    # Downgrades before upgrades, then by magnitude.
    result["ratings"].sort(key=lambda r: (r["direction"] != "downgrade",
                                          -abs(r["delta"])))
    result["grades"].sort(key=lambda r: (r["direction"] != "downgrade",
                                         -abs(r["delta"])))
    # Cuts before raises, then by size of the revision.
    result["fair_values"].sort(key=lambda r: (r["direction"] != "cut",
                                              -abs(r["pct_change"])))
    # Narrowing before widening, then by rank distance.
    result["moats"].sort(key=lambda r: (r["direction"] != "narrowed",
                                        -abs(r["delta"])))
    # Rising uncertainty is the signal -> first.
    result["uncertainty"].sort(key=lambda r: (r["direction"] != "rose",
                                             -abs(r["delta"])))
    # Biggest material moves first (normalised by each field's threshold).
    result["fundamentals"].sort(key=lambda r: -r["score"])


def _counts(result: Dict[str, Any]) -> Dict[str, int]:
    return {k: len(result[k]) for k in
            ("ratings", "moats", "fair_values", "grades", "uncertainty",
             "fundamentals", "added", "dropped")}


def has_changes(result: Dict[str, Any]) -> bool:
    """True if any change group is non-empty (identical snapshots -> False)."""
    return any(v > 0 for v in result.get("counts", {}).values())
