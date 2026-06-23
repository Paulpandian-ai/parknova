"""US market-session clock + extended-hours quote labeling (pure, testable).

No network, no Streamlit. Two concerns:

  1. Which trading session is it right now in US/Eastern — pre-market, regular,
     after-hours, or closed (weekends/holidays -> closed).
  2. Given what a provider returned, decide *honestly* whether a price is a real
     extended-hours print or just the regular-session close — so the UI never
     mislabels a stale regular price as pre/post-market.

The schedule (ET): pre 04:00–09:30, regular 09:30–16:00, after 16:00–20:00,
otherwise closed.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _ET = None

# Session identifiers.
PRE = "pre"
REGULAR = "regular"
AFTER = "after"
CLOSED = "closed"

_SESSION_LABEL = {
    PRE: "pre-market",
    REGULAR: "regular session",
    AFTER: "after-hours",
    CLOSED: "market closed",
}

# ET schedule boundaries (hour, minute).
_PRE_OPEN = (4, 0)
_REG_OPEN = (9, 30)
_REG_CLOSE = (16, 0)
_AFTER_CLOSE = (20, 0)

# Static US market holiday list (full-day closures). Half-days still trade, so
# they are intentionally treated as normal sessions. Extend as needed.
_HOLIDAYS = {
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def et_now(now_utc: Optional[_dt.datetime] = None) -> _dt.datetime:
    """Current time in US/Eastern. ``now_utc`` (tz-aware or naive UTC) for tests."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_dt.timezone.utc)
    return now_utc.astimezone(_ET) if _ET is not None else now_utc


def is_market_holiday(d: _dt.date) -> bool:
    return d.isoformat() in _HOLIDAYS


def _hm(t: _dt.datetime) -> int:
    """Minutes since midnight."""
    return t.hour * 60 + t.minute


def market_session(now_utc: Optional[_dt.datetime] = None) -> str:
    """Return the active session: 'pre' | 'regular' | 'after' | 'closed' (ET)."""
    et = et_now(now_utc)
    if et.weekday() >= 5 or is_market_holiday(et.date()):
        return CLOSED
    m = _hm(et)
    if _hm_at(_PRE_OPEN) <= m < _hm_at(_REG_OPEN):
        return PRE
    if _hm_at(_REG_OPEN) <= m < _hm_at(_REG_CLOSE):
        return REGULAR
    if _hm_at(_REG_CLOSE) <= m < _hm_at(_AFTER_CLOSE):
        return AFTER
    return CLOSED


def _hm_at(hm: tuple) -> int:
    return hm[0] * 60 + hm[1]


def session_label(session: str) -> str:
    return _SESSION_LABEL.get(session, session)


def is_extended_session(session: str) -> bool:
    return session in (PRE, AFTER)


def extended_change(ext_price: Optional[float],
                    prior_close: Optional[float]) -> Optional[float]:
    """Extended-hours % move vs the prior regular close, as a fraction."""
    try:
        ep, pc = float(ext_price), float(prior_close)
    except (TypeError, ValueError):
        return None
    if pc == 0:
        return None
    return (ep - pc) / pc


def classify_extended(session: str, ext_price: Optional[float],
                      ext_ts: Optional[Any],
                      prior_close: Optional[float]) -> Dict[str, Any]:
    """Decide honestly whether a returned price is a real extended-hours print.

    A price counts as extended-hours ONLY when (a) the clock is in a pre/after
    session, (b) the provider returned a price AND an extended-hours timestamp,
    and (c) we can express a change vs the prior regular close. Otherwise it is
    NOT extended-hours and the caller must fall back to the regular/last price —
    never relabel a regular close as pre/post-market.

    Returns ``{is_extended, mode, session, ext_pct}`` where ``mode`` is the
    honest label ('pre-market' / 'after-hours') only when is_extended is True,
    else None (caller picks 'live'/'last close').
    """
    out = {"is_extended": False, "mode": None, "session": session,
           "ext_pct": None}
    if not is_extended_session(session):
        return out
    if ext_price is None or ext_ts is None:
        return out
    pct = extended_change(ext_price, prior_close)
    if pct is None:
        return out
    out["is_extended"] = True
    out["mode"] = _SESSION_LABEL[session]   # 'pre-market' or 'after-hours'
    out["ext_pct"] = pct
    return out


def movers_session_suffix(session: str, extended_available: bool) -> str:
    """Heading suffix for the Top-movers section reflecting the active session.

    ``extended_available`` says whether real extended-hours data backs the move
    in a pre/after session; when False we say so and fall back to last session.
    """
    if session == REGULAR:
        return "regular session"
    if session == CLOSED:
        return "last close (market closed)"
    if session in (PRE, AFTER):
        label = _SESSION_LABEL[session]
        if extended_available:
            return label
        return f"{label} data not available on current plan — last regular session"
    return session_label(session)
