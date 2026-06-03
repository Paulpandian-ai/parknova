"""AppTest: the What Changed tab renders an empty state with <2 archives and a
populated diff with both dates, analyst-judgment lead, and no advice language."""

from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

_FORBIDDEN = ["we recommend", "you should buy", "you should sell",
              "price target", "strong buy"]


def _two_frames():
    """Old/new frames with a known downgrade, FV cut, moat narrow, margin move,
    plus an added and a dropped ticker."""
    old = pd.DataFrame([
        {"Ticker": "AAA", "Name": "Alpha",
         "Morningstar Rating for Stocks": 4.0, "Economic Moat": "Wide",
         "Fair Value": 100.0, "Last Price": 90.0, "Profitability Grade": "B",
         "Growth Grade": "B", "Fair Value Uncertainty": "Medium",
         "Net Margin": 20.0, "Operating Margin": 25.0, "Gross Margin": 50.0,
         "Return on Equity": 18.0, "Return on Invested Capital": 15.0,
         "Revenue Growth (1Y)": 30.0, "Price/Earnings (Forward)": 25.0,
         "Total Debt/Equity": 0.5},
        {"Ticker": "ZZZ", "Name": "Zed", "Morningstar Rating for Stocks": 3.0,
         "Economic Moat": "Narrow", "Fair Value": 10.0, "Last Price": 9.0},
    ])
    new = pd.DataFrame([
        {"Ticker": "AAA", "Name": "Alpha",
         "Morningstar Rating for Stocks": 2.0,   # downgrade 4->2
         "Economic Moat": "Narrow",              # narrowed
         "Fair Value": 80.0, "Last Price": 90.0,  # FV cut -20%
         "Profitability Grade": "B", "Growth Grade": "B",
         "Fair Value Uncertainty": "Medium",
         "Net Margin": 28.0,                      # +8 pts material
         "Operating Margin": 25.0, "Gross Margin": 50.0,
         "Return on Equity": 18.0, "Return on Invested Capital": 15.0,
         "Revenue Growth (1Y)": 30.0, "Price/Earnings (Forward)": 25.0,
         "Total Debt/Equity": 0.5},
        {"Ticker": "CCC", "Name": "Gamma", "Morningstar Rating for Stocks": 3.0,
         "Economic Moat": "Narrow", "Fair Value": 50.0, "Last Price": 48.0},
    ])
    return old, new


def _empty_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import app
    from core import ms_compare as mcmp
    mcmp.latest_archive_and_working = lambda root=None: None
    mcmp.list_archives = lambda root=None: []
    app.styles.inject_css()
    app.view_what_changed()


def _populated_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import app
    from core import ms_compare as mcmp
    from tests.test_what_changed_apptest import _two_frames

    old, new = _two_frames()
    res = mcmp.compare_snapshots(old, new, "2026-06-02", "current working file")
    mcmp.latest_archive_and_working = lambda root=None: {
        "old_date": "2026-06-02", "old_path": "/x.xlsx",
        "new_date": "current working file", "new_path": "/y.xlsx"}
    app._load_comparison = lambda *a, **k: res
    app.styles.inject_css()
    app.view_what_changed()


def _no_changes_script():
    import sys
    sys.path.insert(0, "/home/user/parknova")
    import app
    from core import ms_compare as mcmp
    from tests.test_what_changed_apptest import _two_frames

    old, _ = _two_frames()
    # Identical snapshots: archive == working file -> no changes.
    res = mcmp.compare_snapshots(old, old.copy(), "2026-06-02",
                                 "current working file")
    mcmp.latest_archive_and_working = lambda root=None: {
        "old_date": "2026-06-02", "old_path": "/x.xlsx",
        "new_date": "current working file", "new_path": "/y.xlsx"}
    app._load_comparison = lambda *a, **k: res
    app.styles.inject_css()
    app.view_what_changed()


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


def test_empty_state_no_crash():
    at = AppTest.from_function(_empty_script, default_timeout=60)
    at.run()
    _raise_if_exc(at, "Empty What Changed")
    blob = " ".join(m.value for m in at.markdown).lower()
    blob += " ".join(a.value for a in at.info).lower()
    assert "no dated archive yet to compare against" in blob


def _df_blob(at) -> str:
    """Concatenated cell text across all rendered dataframes (lowercased)."""
    out = []
    for d in at.dataframe:
        try:
            out.append(d.value.astype(str).to_string())
        except Exception:
            pass
    return " ".join(out).lower()


def test_populated_render_leads_with_compact_tables():
    at = AppTest.from_function(_populated_script, default_timeout=60)
    at.run()
    _raise_if_exc(at, "Populated What Changed")
    blob = _blob(at)
    # Header states archive date -> current working file.
    assert "2026-06-02" in blob
    assert "comparing archive" in blob and "current working file" in blob
    # Analyst-judgment section rendered as split, counted tables.
    assert "analyst judgment" in blob
    assert "rating downgrades (1)" in blob       # split by direction + count
    assert "fair-value cuts (1)" in blob
    assert "other analyst changes (1)" in blob   # moat narrowing lives here
    # Empty direction collapsed to a one-liner, not a big empty table.
    assert "no rating upgrades." in blob and "no fair-value raises." in blob
    assert "fundamental shifts" in blob
    assert "universe changes" in blob
    assert "added (1)" in blob and "dropped (1)" in blob
    assert "not advice" in blob
    for phrase in _FORBIDDEN:
        assert phrase not in blob, f"forbidden phrase: {phrase!r}"

    # Table content present: tickers searchable + moat narrowing in a cell.
    dblob = _df_blob(at)
    assert "aaa" in dblob          # the changed ticker is visible in tables
    assert "narrowed" in dblob     # direction shown in the combined table


def test_no_changes_state_is_honest_not_error():
    at = AppTest.from_function(_no_changes_script, default_timeout=60)
    at.run()
    _raise_if_exc(at, "No-changes What Changed")
    blob = _blob(at)
    blob += " ".join(a.value for a in at.success).lower()
    assert "comparing archive" in blob
    assert "no changes detected" in blob
    assert "your next data refresh" in blob
