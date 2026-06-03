"""AppTest: the Portfolios view renders, persists, freezes entry snapshots,
and emits no execution/advice language."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

# Advice / execution phrases that must never appear in this paper-only view.
_FORBIDDEN = [
    "we recommend", "you should buy", "you should sell", "buy now",
    "sell now", "price target", "strong buy", "our recommendation",
    "place an order", "execute the trade", "guaranteed",
]


def _portfolios_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")

    import streamlit as st  # noqa: F401
    from core import store
    from core import portfolios as pfl

    # In-memory store so the test never touches disk/S3.
    _db: dict = {}
    store.read_json = lambda k: _db.get(k)
    store.write_json = lambda k, v: (_db.__setitem__(k, v) or True)
    store.delete_json = lambda k: (_db.pop(k, None) or True)
    pfl.store = store  # ensure module sees the patched callables

    from data import morningstar as ms
    from core import timing as tm, factors as fc, performance as perf
    import app

    # --- Build the universe frames the view expects (no live API). ---------
    full = ms.load_morningstar().copy()
    full = tm.add_timing_columns(full)
    perf_full = perf.build_performance_frame(full, live=False).copy()
    perf_full["Momentum score"] = perf.blended_momentum_score(perf_full)
    merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                        on="Ticker", how="left")
    scores = fc.compute_factor_scores(merged)
    for i, col in enumerate(["Ticker", "Name", "Primary Bucket",
                             "Sector", "Side", "Crest"]):
        scores.insert(i, col, merged[col].values)

    # --- Seed a portfolio with one open + one closed position. -------------
    pid = pfl.create_portfolio("Value Tilt", "Buy below fair value").get(
        "portfolio_id")
    tk = full["Ticker"].iloc[0]

    def _row(df, t):
        sub = df[df["Ticker"] == t]
        return sub.iloc[0] if not sub.empty else None

    import core.thesis as thx
    snap = {"evidence": thx.capture_evidence(tk, _row(full, tk),
                                             _row(scores, tk), None, []),
            "signals": []}
    pfl.open_position(pid, tk, "BUY", 100, 100.0, thesis_at_entry=snap,
                      rationale="cheap vs fair value", conviction=7)

    # Persistence check: a fresh read returns the saved position.
    reread = pfl.read_portfolio(pid)
    assert reread is not None and len(reread["positions"]) == 1, "did not persist"

    # Freeze check: mutating the source dict must not change the stored copy.
    snap["evidence"]["valuation"]["upside_fv"] = -9.99
    frozen = pfl.read_portfolio(pid)["positions"][0]["thesis_at_entry"]
    assert frozen["evidence"]["valuation"].get("upside_fv") != -9.99, "not frozen"

    # A closed position to exercise realized P&L + win rate.
    tk2 = full["Ticker"].iloc[1]
    snap2 = {"evidence": thx.capture_evidence(tk2, _row(full, tk2),
                                              _row(scores, tk2), None, []),
             "signals": []}
    r2 = pfl.open_position(pid, tk2, "BUY", 10, 50.0, thesis_at_entry=snap2,
                          conviction=5)
    pfl.close_position(pid, r2["positions"][-1]["id"], exit_price=60.0)

    st.session_state["pf_active"] = pid
    app.styles.inject_css()
    app.view_portfolios(full, perf_full, scores)


def _empty_state_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    from core import store
    _db: dict = {}
    store.read_json = lambda k: _db.get(k)
    store.write_json = lambda k, v: (_db.__setitem__(k, v) or True)
    store.delete_json = lambda k: (_db.pop(k, None) or True)

    from data import morningstar as ms
    from core import timing as tm, factors as fc, performance as perf
    import app

    full = tm.add_timing_columns(ms.load_morningstar().copy())
    perf_full = perf.build_performance_frame(full, live=False).copy()
    perf_full["Momentum score"] = perf.blended_momentum_score(perf_full)
    merged = full.merge(perf_full[["Ticker", "mom_3m", "mom_6m"]],
                        on="Ticker", how="left")
    scores = fc.compute_factor_scores(merged)
    for i, col in enumerate(["Ticker", "Name", "Primary Bucket",
                             "Sector", "Side", "Crest"]):
        scores.insert(i, col, merged[col].values)
    app.styles.inject_css()
    app.view_portfolios(full, perf_full, scores)  # no portfolios -> first run


def test_empty_first_run_does_not_crash():
    at = AppTest.from_function(_empty_state_script, default_timeout=120)
    at.run()
    if len(at.exception):
        detail = "\n".join(
            str(getattr(e, "message", e)) + "\n"
            + "".join(getattr(e, "stack_trace", []) or [])
            for e in at.exception)
        raise AssertionError("empty-state view raised:\n" + detail)
    blob = " ".join(m.value for m in at.markdown).lower()
    blob += " ".join(a.value for a in at.info).lower()
    assert "paper portfolio" in blob       # paper label shown on first run
    assert "no portfolios" in blob         # empty-state guidance shown


def test_portfolios_view_renders_and_no_advice_language():
    at = AppTest.from_function(_portfolios_script, default_timeout=120)
    at.run()
    if len(at.exception):
        detail = "\n".join(
            str(getattr(e, "message", e)) + "\n"
            + "".join(getattr(e, "stack_trace", []) or [])
            for e in at.exception)
        raise AssertionError("view raised:\n" + detail)

    blob = " ".join(m.value for m in at.markdown).lower()
    blob += " ".join(getattr(c, "value", "") for c in at.caption).lower()
    # Paper label is present.
    assert "paper portfolio" in blob
    # No execution / advice language anywhere in the rendered view.
    for phrase in _FORBIDDEN:
        assert phrase not in blob, f"forbidden phrase present: {phrase!r}"
