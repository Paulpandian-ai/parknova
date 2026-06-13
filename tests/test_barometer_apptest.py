"""AppTest: the Barometer view renders (heatmap, drill-down, alpha hunt) over the
real universe offline, across groupings, with no advice language."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

_FORBIDDEN = ["we recommend", "you should buy", "you should sell",
              "price target", "strong buy"]


def _frames():
    from data import morningstar as ms
    from core import timing as tm, factors as fc, performance as perf

    full = tm.add_timing_columns(ms.load_morningstar().copy())
    perf_full = perf.build_performance_frame(full, live=False).copy()
    perf_full["Momentum score"] = perf.blended_momentum_score(perf_full)
    merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                        on="Ticker", how="left")
    scores = fc.compute_factor_scores(merged)
    for i, col in enumerate(["Ticker", "Name", "Primary Bucket",
                             "Sector", "Side", "Crest"]):
        scores.insert(i, col, merged[col].values)
    return full, perf_full, scores


def _style_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import app
    from tests.test_barometer_apptest import _frames
    full, perf_full, scores = _frames()
    app.styles.inject_css()
    app.view_barometer(full, perf_full, scores)


def _bucket_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import streamlit as st
    import app
    from tests.test_barometer_apptest import _frames
    st.session_state["barometer_group"] = "Primary Bucket"
    full, perf_full, scores = _frames()
    app.styles.inject_css()
    app.view_barometer(full, perf_full, scores)


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


def test_barometer_style_box_renders():
    at = AppTest.from_function(_style_script, default_timeout=120)
    at.run()
    _raise_if_exc(at, "Barometer (style box)")
    blob = _blob(at)
    assert "barometer — where return concentrates" in blob
    assert "median" in blob
    # The honest priced-in tension caption is present.
    assert "priced-in, not repeatable" in blob
    assert "alpha hunt" in blob
    # Drill-down + alpha-hunt render at least one table.
    assert len(at.dataframe) >= 1
    for phrase in _FORBIDDEN:
        assert phrase not in blob, f"forbidden phrase: {phrase!r}"


def test_barometer_bucket_grouping_renders():
    at = AppTest.from_function(_bucket_script, default_timeout=120)
    at.run()
    _raise_if_exc(at, "Barometer (bucket)")
    blob = _blob(at)
    assert "barometer — where return concentrates" in blob
    # A known bucket label appears in the 1-D grid.
    assert "compute semi" in blob
