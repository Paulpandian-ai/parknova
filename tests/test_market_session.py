"""Unit tests for core/market_session.py — ET session clock + ext-hours labeling."""

from __future__ import annotations

import datetime as dt

import pytest

from core import market_session as msx


def _utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc)


# A known weekday (Wed 2025-06-11) and weekend (Sat 2025-06-14).
# ET is UTC-4 in June (DST). 13:30 UTC = 09:30 ET.
class TestSession:
    def test_regular_hours(self):
        # 14:00 UTC = 10:00 ET, mid-regular-session.
        assert msx.market_session(_utc(2025, 6, 11, 14, 0)) == msx.REGULAR

    def test_pre_market(self):
        # 12:00 UTC = 08:00 ET -> pre-market (04:00–09:30).
        assert msx.market_session(_utc(2025, 6, 11, 12, 0)) == msx.PRE

    def test_after_hours(self):
        # 22:00 UTC = 18:00 ET -> after-hours (16:00–20:00).
        assert msx.market_session(_utc(2025, 6, 11, 22, 0)) == msx.AFTER

    def test_overnight_closed(self):
        # 02:00 UTC = 22:00 ET previous day -> closed.
        assert msx.market_session(_utc(2025, 6, 11, 2, 0)) == msx.CLOSED

    def test_boundaries(self):
        # Exactly 09:30 ET = regular; exactly 16:00 ET = after.
        assert msx.market_session(_utc(2025, 6, 11, 13, 30)) == msx.REGULAR
        assert msx.market_session(_utc(2025, 6, 11, 20, 0)) == msx.AFTER
        # 09:29 ET still pre-market.
        assert msx.market_session(_utc(2025, 6, 11, 13, 29)) == msx.PRE

    def test_weekend_closed(self):
        # Saturday, mid-day ET -> closed regardless of clock.
        assert msx.market_session(_utc(2025, 6, 14, 15, 0)) == msx.CLOSED

    def test_holiday_closed(self):
        # July 4, 2025 (Friday) is a market holiday -> closed during reg hours.
        assert msx.is_market_holiday(dt.date(2025, 7, 4)) is True
        assert msx.market_session(_utc(2025, 7, 4, 15, 0)) == msx.CLOSED

    def test_labels(self):
        assert msx.session_label(msx.PRE) == "pre-market"
        assert msx.session_label(msx.AFTER) == "after-hours"
        assert msx.session_label(msx.REGULAR) == "regular session"
        assert msx.session_label(msx.CLOSED) == "market closed"


class TestClassifyExtended:
    def test_real_extended_print_pre_market(self):
        # Provider returned an ext price + ts during pre-market -> extended.
        res = msx.classify_extended(msx.PRE, ext_price=110.0, ext_ts=1_700_000_000,
                                    prior_close=100.0)
        assert res["is_extended"] is True
        assert res["mode"] == "pre-market"
        assert res["ext_pct"] == pytest.approx(0.10)

    def test_real_extended_print_after_hours(self):
        res = msx.classify_extended(msx.AFTER, ext_price=95.0, ext_ts=1_700_000_000,
                                    prior_close=100.0)
        assert res["is_extended"] is True
        assert res["mode"] == "after-hours"
        assert res["ext_pct"] == pytest.approx(-0.05)

    def test_no_ext_field_is_not_extended(self):
        # No extended timestamp (provider returned only a regular close).
        res = msx.classify_extended(msx.PRE, ext_price=100.0, ext_ts=None,
                                    prior_close=100.0)
        assert res["is_extended"] is False
        assert res["mode"] is None

    def test_regular_session_never_extended(self):
        # Even with an ext price, during regular hours it's not "extended".
        res = msx.classify_extended(msx.REGULAR, ext_price=110.0,
                                    ext_ts=1_700_000_000, prior_close=100.0)
        assert res["is_extended"] is False

    def test_closed_session_never_extended(self):
        res = msx.classify_extended(msx.CLOSED, ext_price=110.0,
                                    ext_ts=1_700_000_000, prior_close=100.0)
        assert res["is_extended"] is False

    def test_missing_prior_close_cannot_label(self):
        res = msx.classify_extended(msx.PRE, ext_price=110.0, ext_ts=1, prior_close=None)
        assert res["is_extended"] is False


class TestMoversSuffix:
    def test_regular(self):
        assert msx.movers_session_suffix(msx.REGULAR, False) == "regular session"

    def test_closed(self):
        assert "market closed" in msx.movers_session_suffix(msx.CLOSED, False)

    def test_extended_available(self):
        assert msx.movers_session_suffix(msx.PRE, True) == "pre-market"
        assert msx.movers_session_suffix(msx.AFTER, True) == "after-hours"

    def test_extended_unavailable_says_so(self):
        s = msx.movers_session_suffix(msx.PRE, False)
        assert "not available on current plan" in s
        assert "last regular session" in s
