"""Paper portfolios — store-backed, versioned, thesis-at-entry frozen.

A *paper* record of the user's own buy/sell decisions across named strategy
portfolios ("Value tilt", "Momentum tilt", "Segram-strict", …). Each position
freezes the evidence snapshot the user had at entry so the tool can later show
the gap between thesis and reality — believability-weighting applied to oneself.

This module is **paper only**: it records decisions, it never executes trades
and emits no buy/sell advice. It owns the data model + CRUD + scoring math; it
does *not* recompute factor/timing/signal logic — callers freeze an evidence
snapshot via ``core.thesis.capture_evidence`` (and the signal functions) and
hand it in.

Persistence is via :mod:`core.store` (local by default, S3 when configured), so
records sync across the user's laptop/phone. All writes are best-effort and the
empty / first-run / missing-price cases degrade gracefully rather than crash.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from core import store

INDEX_KEY = "portfolios/_index.json"
DEFAULT_CASH = 100_000.0

# Thresholds for the entry-vs-now "thesis-held" verdict.
_UPSIDE_DELTA = 0.03   # 3 percentage points of upside-to-FV
_SCORE_DELTA = 3.0     # 3 percentile points on a factor score


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return s or "portfolio"


def _key(portfolio_id: str) -> str:
    return f"portfolios/{portfolio_id}.json"


# ---------------------------------------------------------------------------
# Index + record CRUD
# ---------------------------------------------------------------------------
def _read_index() -> List[Dict[str, Any]]:
    idx = store.read_json(INDEX_KEY)
    return idx if isinstance(idx, list) else []


def _write_index(entries: List[Dict[str, Any]]) -> bool:
    return store.write_json(INDEX_KEY, entries)


def list_portfolios() -> List[Dict[str, Any]]:
    """Index entries: ``{portfolio_id, name, strategy_note, created_at,
    active_version}`` (empty list on first run)."""
    return _read_index()


def read_portfolio(portfolio_id: str) -> Optional[Dict[str, Any]]:
    rec = store.read_json(_key(portfolio_id))
    return rec if isinstance(rec, dict) else None


def create_portfolio(name: str, strategy_note: str = "",
                     cash_start: float = DEFAULT_CASH) -> Dict[str, Any]:
    """Create a named paper portfolio and register it in the index."""
    existing = {e.get("portfolio_id") for e in _read_index()}
    base = _slug(name)
    pid = base
    n = 2
    while pid in existing:
        pid = f"{base}-{n}"
        n += 1
    ts = _now()
    record = {
        "portfolio_id": pid,
        "name": name.strip() or pid,
        "strategy_note": strategy_note.strip(),
        "created_at": ts,
        "cash_start": float(cash_start),
        "positions": [],
        "versions": [],
    }
    store.write_json(_key(pid), record)
    idx = _read_index()
    idx.append({"portfolio_id": pid, "name": record["name"],
                "strategy_note": record["strategy_note"], "created_at": ts,
                "active_version": 0})
    _write_index(idx)
    return record


def delete_portfolio(portfolio_id: str) -> bool:
    """Remove a portfolio from the index and delete its record."""
    idx = [e for e in _read_index() if e.get("portfolio_id") != portfolio_id]
    _write_index(idx)
    return store.delete_json(_key(portfolio_id))


def _save(record: Dict[str, Any]) -> Dict[str, Any]:
    store.write_json(_key(record["portfolio_id"]), record)
    return record


def _sync_index(record: Dict[str, Any]) -> None:
    """Keep the index entry's name / strategy_note / active_version in step."""
    idx = _read_index()
    for e in idx:
        if e.get("portfolio_id") == record["portfolio_id"]:
            e["name"] = record.get("name")
            e["strategy_note"] = record.get("strategy_note")
            e["active_version"] = len(record.get("versions", []))
            break
    _write_index(idx)


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------
def open_position(portfolio_id: str, ticker: str, action: str,
                  shares: float, entry_price: float,
                  thesis_at_entry: Dict[str, Any],
                  entry_date: Optional[str] = None,
                  rationale: str = "", stance: str = "",
                  conviction: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Append an open position with a FROZEN thesis-at-entry snapshot.

    ``thesis_at_entry`` is built by the caller (evidence snapshot + active
    signals) and stored verbatim — it never mutates after entry.
    """
    record = read_portfolio(portfolio_id)
    if record is None:
        return None
    import copy
    pos = {
        "id": uuid.uuid4().hex[:12],
        "ticker": ticker.upper(),
        "action": (action or "BUY").upper(),
        "shares": float(shares),
        "entry_price": float(entry_price),
        "entry_date": entry_date or _now(),
        "status": "open",
        "exit_price": None,
        "exit_date": None,
        # Deep-copy so the frozen snapshot can never be mutated after entry,
        # independent of whether the store round-trips through JSON.
        "thesis_at_entry": {
            "stance": stance,
            "conviction": conviction,
            "rationale": rationale,
            "evidence": copy.deepcopy(thesis_at_entry.get("evidence", {})),
            "signals": copy.deepcopy(thesis_at_entry.get("signals", [])),
        },
    }
    record.setdefault("positions", []).append(pos)
    return _save(record)


def close_position(portfolio_id: str, position_id: str, exit_price: float,
                   exit_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Mark a position closed and record exit price/date (realized P&L)."""
    record = read_portfolio(portfolio_id)
    if record is None:
        return None
    for pos in record.get("positions", []):
        if pos.get("id") == position_id:
            pos["status"] = "closed"
            pos["exit_price"] = float(exit_price)
            pos["exit_date"] = exit_date or _now()
            break
    return _save(record)


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------
def save_version(portfolio_id: str, label: str = "") -> Optional[Dict[str, Any]]:
    """Append a labeled snapshot of the whole portfolio (positions + cash)."""
    record = read_portfolio(portfolio_id)
    if record is None:
        return None
    import copy
    versions = record.setdefault("versions", [])
    versions.append({
        "version": len(versions) + 1,
        "ts": _now(),
        "label": label.strip() or f"Version {len(versions) + 1}",
        "snapshot": {
            "cash_start": record.get("cash_start"),
            "positions": copy.deepcopy(record.get("positions", [])),
        },
    })
    _save(record)
    _sync_index(record)
    return record


def diff_versions(snapshot_positions: List[Dict[str, Any]],
                  current_positions: List[Dict[str, Any]]) -> Dict[str, list]:
    """Diff a past version's positions against the current ones.

    Returns ``{added, closed, resized}`` — added since the snapshot, closed
    since (open then, closed now), and resized (share count changed).
    """
    snap = {p["id"]: p for p in snapshot_positions}
    cur = {p["id"]: p for p in current_positions}
    added, closed, resized = [], [], []
    for pid, p in cur.items():
        if pid not in snap:
            added.append(p)
        else:
            was, now = snap[pid], p
            if was.get("status") == "open" and now.get("status") == "closed":
                closed.append(now)
            elif _num(was.get("shares")) != _num(now.get("shares")):
                resized.append({"ticker": now.get("ticker"),
                                "from": was.get("shares"), "to": now.get("shares")})
    return {"added": added, "closed": closed, "resized": resized}


# ---------------------------------------------------------------------------
# Scoring — P&L, thesis-held, portfolio/compare aggregates
# ---------------------------------------------------------------------------
def position_pnl(position: Dict[str, Any],
                 current_price: Optional[float]) -> Dict[str, Any]:
    """Price P&L for one position. Uses exit price for closed positions.

    Returns ``{price, return_pct, dollar_pnl, market_value, cost_basis,
    realized}``. ``return_pct`` is None when no price is available.
    """
    entry = _num(position.get("entry_price"))
    shares = _num(position.get("shares")) or 0.0
    closed = position.get("status") == "closed"
    price = _num(position.get("exit_price")) if closed else _num(current_price)
    cost_basis = (entry or 0.0) * shares
    direction = -1.0 if str(position.get("action")).upper() == "SELL" else 1.0
    if entry is None or price is None or entry == 0:
        return {"price": price, "return_pct": None, "dollar_pnl": None,
                "market_value": None, "cost_basis": cost_basis,
                "realized": closed}
    ret = direction * (price - entry) / entry
    return {"price": price, "return_pct": ret,
            "dollar_pnl": direction * (price - entry) * shares,
            "market_value": price * shares, "cost_basis": cost_basis,
            "realized": closed}


def _factor(ev: Dict[str, Any], name: str) -> Optional[float]:
    return _num((ev.get("factors") or {}).get(name))


def _upside(ev: Dict[str, Any]) -> Optional[float]:
    return _num((ev.get("valuation") or {}).get("upside_fv"))


def thesis_held(entry_evidence: Dict[str, Any],
                now_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Compare the evidence that justified entry against now.

    Returns ``{status, deltas}`` where status is "improved" | "held" |
    "weakened" and deltas is a list of ``{label, entry, now, dir}`` (dir in
    {+1, 0, -1}) for the underlying numbers. Higher = better for all metrics
    except value_trap (becoming a trap deteriorates).
    """
    deltas: List[Dict[str, Any]] = []
    net = 0

    def _cmp(label, e, n, thresh, fmt="score"):
        nonlocal net
        if e is None or n is None:
            return
        d = n - e
        direction = 0
        if d > thresh:
            direction = 1
        elif d < -thresh:
            direction = -1
        net += direction
        deltas.append({"label": label, "entry": e, "now": n,
                       "dir": direction, "fmt": fmt})

    _cmp("Upside to FV", _upside(entry_evidence), _upside(now_evidence),
         _UPSIDE_DELTA, fmt="pct")
    for fac in ("Value", "Quality", "Composite", "MS Upside"):
        _cmp(fac, _factor(entry_evidence, fac), _factor(now_evidence, fac),
             _SCORE_DELTA)

    # Value-trap: True is worse.
    e_trap = (entry_evidence.get("valuation") or {}).get("value_trap")
    n_trap = (now_evidence.get("valuation") or {}).get("value_trap")
    if e_trap is not None and n_trap is not None and e_trap != n_trap:
        direction = -1 if (n_trap and not e_trap) else 1
        net += direction
        deltas.append({"label": "Value-trap flag", "entry": e_trap,
                       "now": n_trap, "dir": direction, "fmt": "bool"})

    status = "improved" if net > 0 else "weakened" if net < 0 else "held"
    return {"status": status, "deltas": deltas, "net": net}


def portfolio_summary(record: Dict[str, Any],
                      prices: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """Portfolio-level roll-up. ``prices`` maps ticker -> current price.

    Returns total value, total P&L ($ and %), open/closed counts, exposure by
    crest/side/bucket, win rate on closed positions, conviction-weighted return,
    and an equal-weight (same-tickers) benchmark return.
    """
    positions = record.get("positions", [])
    cash_start = _num(record.get("cash_start")) or 0.0
    total_cost = 0.0
    total_value = 0.0
    total_pnl = 0.0
    open_n = closed_n = 0
    wins = 0
    rets: List[float] = []
    conv_num = conv_den = 0.0
    exposure = {"Crest": {}, "Side": {}, "Primary Bucket": {}}

    for pos in positions:
        price = prices.get(pos.get("ticker"))
        pnl = position_pnl(pos, price)
        if pos.get("status") == "closed":
            closed_n += 1
            if pnl["return_pct"] is not None:
                rets.append(pnl["return_pct"])
                if pnl["dollar_pnl"] is not None and pnl["dollar_pnl"] > 0:
                    wins += 1
                total_pnl += pnl["dollar_pnl"] or 0.0
        else:
            open_n += 1
            if pnl["dollar_pnl"] is not None:
                total_pnl += pnl["dollar_pnl"]
            if pnl["market_value"] is not None:
                total_value += pnl["market_value"]
            if pnl["return_pct"] is not None:
                rets.append(pnl["return_pct"])
        total_cost += pnl["cost_basis"] or 0.0

        # Conviction-weighted return (open + closed).
        conv = _num((pos.get("thesis_at_entry") or {}).get("conviction"))
        if conv and pnl["return_pct"] is not None:
            conv_num += conv * pnl["return_pct"]
            conv_den += conv

        # Exposure from the frozen entry evidence.
        ev = (pos.get("thesis_at_entry") or {}).get("evidence", {})
        posn = ev.get("position", {})
        mv = pnl["market_value"] or pnl["cost_basis"] or 0.0
        for dim, val in (("Crest", posn.get("crest")),
                         ("Side", posn.get("side")),
                         ("Primary Bucket", posn.get("primary_bucket"))):
            if val:
                exposure[dim][val] = exposure[dim].get(val, 0.0) + mv

    total_pct = (total_pnl / total_cost) if total_cost else None
    win_rate = (wins / closed_n) if closed_n else None
    conv_wtd = (conv_num / conv_den) if conv_den else None
    benchmark = (sum(rets) / len(rets)) if rets else None
    return {
        "total_value": total_value + cash_start - total_cost
        if cash_start else total_value,
        "total_pnl": total_pnl, "total_pct": total_pct,
        "open_n": open_n, "closed_n": closed_n,
        "win_rate": win_rate, "conviction_weighted_return": conv_wtd,
        "benchmark_equal_weight": benchmark, "cost_basis": total_cost,
        "exposure": exposure,
    }


def top_exposure(summary: Dict[str, Any], dim: str = "Crest"
                 ) -> Optional[str]:
    """Largest exposure bucket on ``dim`` (e.g. 'Early', 'Supplier')."""
    exp = (summary.get("exposure") or {}).get(dim, {})
    if not exp:
        return None
    return max(exp.items(), key=lambda kv: kv[1])[0]


def compare_portfolios(records: List[Dict[str, Any]],
                       prices: Dict[str, Optional[float]]
                       ) -> List[Dict[str, Any]]:
    """Side-by-side factual metrics for 2+ portfolios. No 'best' verdict."""
    out = []
    for rec in records:
        s = portfolio_summary(rec, prices)
        out.append({
            "name": rec.get("name"), "portfolio_id": rec.get("portfolio_id"),
            "total_pct": s["total_pct"], "total_pnl": s["total_pnl"],
            "win_rate": s["win_rate"],
            "conviction_weighted_return": s["conviction_weighted_return"],
            "open_n": s["open_n"], "closed_n": s["closed_n"],
            "top_crest": top_exposure(s, "Crest"),
            "top_side": top_exposure(s, "Side"),
        })
    return out
