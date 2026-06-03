"""AppTest: Performance shows Top movers and Signals shows the divergence
section; honest framing present; no earnings-surprise / advice language."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

_FORBIDDEN = ["earnings surprise", "earnings beat", "earnings miss",
              "we recommend", "you should buy", "you should sell", "price target"]


def _build_frames():
    import numpy as np
    from data import morningstar as ms
    from core import timing as tm, factors as fc, performance as perf

    full = tm.add_timing_columns(ms.load_morningstar().copy())
    perf_full = perf.build_performance_frame(full, live=False).copy()
    perf_full["Momentum score"] = perf.blended_momentum_score(perf_full)

    # Inject synthetic short-window moves so movers + divergence have content.
    rng = np.random.default_rng(7)
    n = len(perf_full)
    perf_full["Today"] = rng.normal(0, 0.03, n)
    perf_full["1W"] = rng.normal(0, 0.06, n)
    perf_full["1M"] = rng.normal(0, 0.12, n)
    # Plant a clear euphoria name and a clear opportunity name.
    perf_full.loc[perf_full.index[0], ["Today", "1W", "1M"]] = [0.08, 0.22, 0.5]
    full.loc[full.index[0], "upside_fv"] = -0.3        # 30% above FV
    perf_full.loc[perf_full.index[1], ["Today", "1W", "1M"]] = [-0.06, -0.15, -0.3]
    full.loc[full.index[1], "upside_fv"] = 0.3         # 30% below FV

    merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                        on="Ticker", how="left")
    scores = fc.compute_factor_scores(merged)
    for i, col in enumerate(["Ticker", "Name", "Primary Bucket",
                             "Sector", "Side", "Crest"]):
        scores.insert(i, col, merged[col].values)
    return full, perf_full, scores


def _performance_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import streamlit as st
    import app
    from tests.test_movers_divergence_apptest import _build_frames

    # Deterministic quote: avoids network and gives the movers a 'last close' tag.
    app.service.get_live_quote = lambda t: {
        "price": 100.0, "pct": 0.0, "source": "FMP", "mode": "last close",
        "as_of": "2026-06-02", "error": None}
    st.session_state["live_interval"] = "Off"  # no fragment polling in the test

    full, perf_full, scores = _build_frames()
    app.styles.inject_css()
    app.view_performance(full, perf_full)


def _signals_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    from core import store
    _db: dict = {}
    store.read_json = lambda k: _db.get(k)
    store.write_json = lambda k, v: (_db.__setitem__(k, v) or True)
    import app
    from tests.test_movers_divergence_apptest import _build_frames

    app.service.has_api_key = lambda: False
    app.service.has_finnhub = lambda: False
    full, perf_full, scores = _build_frames()
    app.styles.inject_css()
    app.view_signals(full, perf_full, scores)


def _blob(at) -> str:
    parts = [m.value for m in at.markdown]
    parts += [getattr(c, "value", "") for c in at.caption]
    return " ".join(parts).lower()


def _raise_if_exc(at, label):
    if len(at.exception):
        detail = "\n".join(
            str(getattr(e, "message", e)) + "\n"
            + "".join(getattr(e, "stack_trace", []) or [])
            for e in at.exception)
        raise AssertionError(f"{label} raised:\n" + detail)


def test_performance_shows_top_movers():
    at = AppTest.from_function(_performance_script, default_timeout=120)
    at.run()
    _raise_if_exc(at, "Performance")
    blob = _blob(at)
    assert "top movers" in blob
    assert "gainers" in blob and "losers" in blob
    # Honest market-state label.
    assert "last close" in blob
    for phrase in _FORBIDDEN:
        assert phrase not in blob, f"forbidden phrase: {phrase!r}"


def test_signals_shows_divergence_section():
    at = AppTest.from_function(_signals_script, default_timeout=120)
    at.run()
    _raise_if_exc(at, "Signals")
    blob = _blob(at)
    assert "divergence" in blob
    # Honest framing: explicitly not an earnings-surprise detector.
    assert "not an earnings-surprise detector" in blob
    assert "not advice or predictions" in blob
    for phrase in _FORBIDDEN:
        assert phrase not in blob, f"forbidden phrase: {phrase!r}"
