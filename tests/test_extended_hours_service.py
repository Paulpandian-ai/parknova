"""Service-level tests: extended-hours-aware get_live_quote + the probe verdict.

Mocks the providers (sandbox can't reach them) to exercise the extended-vs-
regular labeling: a quote WITH a real extended-hours print -> labeled
pre/after-hours; WITHOUT -> honest regular/last-close fallback.
"""

from __future__ import annotations

from core import market_session as msx
from data import service


def _patch_regular(monkeypatch, mode="last close", price=100.0, pct=0.0):
    monkeypatch.setattr(service, "_regular_quote", lambda t: {
        "price": price, "pct": pct, "source": "FMP", "mode": mode,
        "as_of": "2025-06-10", "error": None})


class TestExtendedAwareQuote:
    def test_after_hours_with_real_extended_print(self, monkeypatch):
        monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.AFTER)
        monkeypatch.setattr(service, "_extended_quote", lambda t: {
            "price": 95.0, "ts": 1_700_000_000, "prior_close": 100.0,
            "pct": -0.05, "source": "FMP (aftermarket)"})
        _patch_regular(monkeypatch)
        service.get_live_quote.clear()
        q = service.get_live_quote("XEXT1")
        assert q["is_extended"] is True
        assert q["mode"] == "after-hours"
        assert q["price"] == 95.0
        assert q["pct"] == -0.05
        assert q["session"] == msx.AFTER

    def test_after_hours_without_extended_falls_back_to_regular(self, monkeypatch):
        monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.AFTER)
        monkeypatch.setattr(service, "_extended_quote", lambda t: None)
        _patch_regular(monkeypatch, mode="last close", price=100.0, pct=0.012)
        service.get_live_quote.clear()
        q = service.get_live_quote("XEXT2")
        # No extended print -> honest last-close, never relabeled.
        assert q["is_extended"] is False
        assert q["mode"] == "last close"
        assert q["price"] == 100.0
        assert q["ext_price"] is None and q["ext_pct"] is None
        assert q["session"] == msx.AFTER

    def test_regular_session_never_extended(self, monkeypatch):
        monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.REGULAR)
        # Even if an ext print existed, regular hours must not use it.
        called = {"n": 0}

        def _ext(t):
            called["n"] += 1
            return {"price": 95.0, "ts": 1, "prior_close": 100.0, "pct": -0.05,
                    "source": "FMP (aftermarket)"}
        monkeypatch.setattr(service, "_extended_quote", _ext)
        _patch_regular(monkeypatch, mode="live", price=101.0, pct=0.01)
        service.get_live_quote.clear()
        q = service.get_live_quote("XEXT3")
        assert q["is_extended"] is False
        assert q["mode"] == "live"
        assert called["n"] == 0   # extended path not even consulted in regular


# ---------------------------------------------------------------------------
# Probe verdict
# ---------------------------------------------------------------------------
class _FakeFMP:
    def __init__(self, trade):
        self._trade = trade

    def extended_hours_targets(self, ticker):
        return [("FMP aftermarket-trade",
                 "https://x/stable/aftermarket-trade", {"symbol": ticker})]

    def probe(self, url, params):
        return {"ok": True, "status": 200, "error": None, "empty": False,
                "kind": "list", "keys": ["symbol", "price", "timestamp"],
                "sample": self._trade}

    def aftermarket_trade(self, ticker):
        return self._trade


def _patch_probe_env(monkeypatch, trade, session=msx.AFTER, prior_close=100.0):
    monkeypatch.setattr(msx, "market_session", lambda *a, **k: session)
    monkeypatch.setattr(service, "has_finnhub_key", lambda: False)
    monkeypatch.setattr(service, "get_client", lambda: _FakeFMP(trade))
    monkeypatch.setattr(service, "get_history_result",
                        lambda t: {"raw_last": prior_close})


class TestExtendedHoursProbe:
    def test_available_when_distinct_extended_print(self, monkeypatch):
        _patch_probe_env(monkeypatch, trade={"price": 95.0, "ts": 1_700_000_000,
                                             "raw": {}, "error": None})
        res = service.extended_hours_probe("XPRB1")
        assert res["available"] is True
        assert "AVAILABLE" in res["verdict"]
        assert res["session"] == msx.AFTER

    def test_not_available_when_no_extended_price(self, monkeypatch):
        _patch_probe_env(monkeypatch, trade={"price": None, "ts": None,
                                             "raw": {}, "error":
                                             "Legacy Endpoint"})
        res = service.extended_hours_probe("XPRB2")
        assert res["available"] is False
        assert "NOT AVAILABLE" in res["verdict"]

    def test_regular_session_price_not_called_extended(self, monkeypatch):
        # Even with a price+ts, in the regular session it isn't extended-hours.
        _patch_probe_env(monkeypatch, trade={"price": 95.0, "ts": 1_700_000_000,
                                             "raw": {}, "error": None},
                         session=msx.REGULAR)
        res = service.extended_hours_probe("XPRB3")
        assert res["available"] is False


# ---------------------------------------------------------------------------
# Regression: extended-hours failures must NEVER crash the quote/returns path
# ---------------------------------------------------------------------------
class _ClientNoAftermarket:
    """A client missing the aftermarket method entirely (the original bug)."""
    # no aftermarket_trade / extended_hours_targets attributes


class _ClientRaisingAftermarket:
    def aftermarket_trade(self, ticker):
        raise RuntimeError("boom: network/parse/plan-gated")

    def extended_hours_targets(self, ticker):
        raise AttributeError("legacy build")


def _force_extended_session(monkeypatch):
    monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.AFTER)
    monkeypatch.setattr(service, "_regular_quote", lambda t: {
        "price": 100.0, "pct": 0.01, "source": "FMP", "mode": "last close",
        "as_of": "2025-06-10", "error": None})
    monkeypatch.setattr(service, "get_history_result",
                        lambda t: {"raw_last": 100.0})


class TestExtendedFailSafe:
    def test_extended_quote_none_when_method_missing(self, monkeypatch):
        monkeypatch.setattr(service, "get_client", lambda: _ClientNoAftermarket())
        service._extended_quote.clear()
        assert service._extended_quote("XMISS") is None   # no AttributeError

    def test_extended_quote_none_when_method_raises(self, monkeypatch):
        monkeypatch.setattr(service, "get_client",
                            lambda: _ClientRaisingAftermarket())
        service._extended_quote.clear()
        assert service._extended_quote("XRAISE") is None  # swallowed

    def test_live_quote_falls_back_when_method_missing(self, monkeypatch):
        _force_extended_session(monkeypatch)
        monkeypatch.setattr(service, "get_client", lambda: _ClientNoAftermarket())
        service._extended_quote.clear()
        service.get_live_quote.clear()
        q = service.get_live_quote("XFB1")           # must not raise
        assert q["is_extended"] is False
        assert q["mode"] == "last close"
        assert q["price"] == 100.0
        assert q["session"] == msx.AFTER

    def test_live_quote_falls_back_when_method_raises(self, monkeypatch):
        _force_extended_session(monkeypatch)
        monkeypatch.setattr(service, "get_client",
                            lambda: _ClientRaisingAftermarket())
        service._extended_quote.clear()
        service.get_live_quote.clear()
        q = service.get_live_quote("XFB2")           # must not raise
        assert q["is_extended"] is False
        assert q["mode"] == "last close"

    def test_probe_reports_not_available_when_method_missing(self, monkeypatch):
        monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.AFTER)
        monkeypatch.setattr(service, "has_finnhub_key", lambda: False)
        monkeypatch.setattr(service, "get_client", lambda: _ClientNoAftermarket())
        monkeypatch.setattr(service, "get_history_result",
                            lambda t: {"raw_last": 100.0})
        res = service.extended_hours_probe("XPRBMISS")  # must not raise
        assert res["available"] is False
        assert "NOT AVAILABLE" in res["verdict"]

    def test_probe_reports_not_available_when_method_raises(self, monkeypatch):
        monkeypatch.setattr(msx, "market_session", lambda *a, **k: msx.AFTER)
        monkeypatch.setattr(service, "has_finnhub_key", lambda: False)
        monkeypatch.setattr(service, "get_client",
                            lambda: _ClientRaisingAftermarket())
        monkeypatch.setattr(service, "get_history_result",
                            lambda t: {"raw_last": 100.0})
        res = service.extended_hours_probe("XPRBRAISE")  # must not raise
        assert res["available"] is False
        assert "NOT AVAILABLE" in res["verdict"]
