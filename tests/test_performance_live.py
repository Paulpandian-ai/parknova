"""Verify Performance/Detail share one quote source and the universe frame is
memoized (no full rebuild on every render)."""

from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest


# ---------------------------------------------------------------------------
# Issue 1 — single quote source: the Performance overlay and the Detail card
# both read service.get_live_quote, so they show the identical number.
# ---------------------------------------------------------------------------
def test_overlay_and_detail_share_quote_source(monkeypatch):
    import app

    def fake_quote(ticker):
        return {"price": 123.45, "pct": 0.05, "source": "Finnhub",
                "mode": "live", "as_of": 1, "error": None}

    monkeypatch.setattr(app.service, "get_live_quote", fake_quote)

    df = pd.DataFrame({
        "Ticker": ["AAA", "BBB"],
        "Last Price": [10.0, 20.0],
        "Today": [0.01, 0.02],
    })
    # Overlay scoped to AAA only (the "top-N visible" case).
    out, modes = app._overlay_live_quotes(df, ["AAA"])

    aaa = out[out["Ticker"] == "AAA"].iloc[0]
    bbb = out[out["Ticker"] == "BBB"].iloc[0]
    # AAA reflects the live quote; BBB (not polled) keeps its last-close baseline.
    assert aaa["Last Price"] == 123.45
    assert aaa["Today"] == 0.05
    assert bbb["Last Price"] == 20.0
    assert "live" in modes

    # The Detail card pulls the SAME function for the same ticker -> same number.
    detail_q = app.service.get_live_quote("AAA")
    assert detail_q["price"] == aaa["Last Price"]


def test_overlay_missing_quote_falls_back_to_last_close(monkeypatch):
    import app

    monkeypatch.setattr(app.service, "get_live_quote",
                        lambda t: {"price": None, "pct": None, "source": None,
                                   "mode": None, "as_of": None, "error": "no data"})
    df = pd.DataFrame({"Ticker": ["AAA"], "Last Price": [42.0], "Today": [0.03]})
    out, modes = app._overlay_live_quotes(df, ["AAA"])
    # Nothing crashes; the baseline last-close value is retained.
    assert out.iloc[0]["Last Price"] == 42.0
    assert out.iloc[0]["Today"] == 0.03


# ---------------------------------------------------------------------------
# Issue 2 — the 226-name frame is built once and reused from session_state.
# ---------------------------------------------------------------------------
def _perf_cache_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import streamlit as st
    import app
    from core import performance as perf
    from data import morningstar as ms

    # Force the no-API build path and count how often the heavy frame is built.
    app.service.has_api_key = lambda: False
    calls = {"n": 0}
    real = perf.build_performance_frame

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    perf.build_performance_frame = counting

    full = ms.load_morningstar().copy()
    first = app.get_perf_full(full)
    second = app.get_perf_full(full)   # should hit the session_state cache

    st.session_state["_build_calls"] = calls["n"]
    st.session_state["_same_object"] = (first is second)
    st.session_state["_has_momentum"] = ("Momentum score" in first.columns)


def test_universe_frame_built_once_and_reused():
    at = AppTest.from_function(_perf_cache_script, default_timeout=120)
    at.run()
    if len(at.exception):
        detail = "\n".join(
            str(getattr(e, "message", e)) + "\n"
            + "".join(getattr(e, "stack_trace", []) or [])
            for e in at.exception)
        raise AssertionError("script raised:\n" + detail)
    assert at.session_state["_build_calls"] == 1     # built once, not per call
    assert at.session_state["_same_object"] is True  # second call reused cache
    assert at.session_state["_has_momentum"] is True
