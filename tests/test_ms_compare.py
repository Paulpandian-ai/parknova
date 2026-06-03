"""Unit tests for core/ms_compare.py — analyst-judgment / fundamental / universe diffs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import ms_compare as mc


# A small base universe; helpers mutate copies to plant known changes.
def _base():
    return pd.DataFrame([
        {"Ticker": "AAA", "Name": "Alpha", "Morningstar Rating for Stocks": 4.0,
         "Economic Moat": "Narrow", "Fair Value": 100.0, "Last Price": 90.0,
         "Profitability Grade": "B", "Growth Grade": "B",
         "Fair Value Uncertainty": "Medium", "Net Margin": 20.0,
         "Operating Margin": 25.0, "Gross Margin": 50.0, "Return on Equity": 18.0,
         "Return on Invested Capital": 15.0, "Revenue Growth (1Y)": 30.0,
         "Price/Earnings (Forward)": 25.0, "Total Debt/Equity": 0.50},
        {"Ticker": "BBB", "Name": "Beta", "Morningstar Rating for Stocks": 3.0,
         "Economic Moat": "Wide", "Fair Value": 200.0, "Last Price": 210.0,
         "Profitability Grade": "A", "Growth Grade": "C",
         "Fair Value Uncertainty": "High", "Net Margin": 10.0,
         "Operating Margin": 12.0, "Gross Margin": 40.0, "Return on Equity": 8.0,
         "Return on Invested Capital": 7.0, "Revenue Growth (1Y)": 10.0,
         "Price/Earnings (Forward)": 15.0, "Total Debt/Equity": 1.00},
    ])


# ---------------------------------------------------------------------------
# Analyst-judgment changes
# ---------------------------------------------------------------------------
def test_rating_downgrade_detected_and_sorted_first():
    old, new = _base(), _base()
    # AAA upgraded 4->5, BBB downgraded 3->1 (bigger downgrade).
    new.loc[new["Ticker"] == "AAA", "Morningstar Rating for Stocks"] = 5.0
    new.loc[new["Ticker"] == "BBB", "Morningstar Rating for Stocks"] = 1.0
    res = mc.compare_snapshots(old, new)
    assert res["counts"]["ratings"] == 2
    # Downgrade leads.
    assert res["ratings"][0]["ticker"] == "BBB"
    assert res["ratings"][0]["direction"] == "downgrade"
    assert res["ratings"][0]["old"] == 3.0 and res["ratings"][0]["new"] == 1.0
    assert res["ratings"][1]["direction"] == "upgrade"


def test_fair_value_cut_with_recomputed_upside():
    old, new = _base(), _base()
    # AAA fair value cut 100 -> 80 (-20%); price steady at 90.
    new.loc[new["Ticker"] == "AAA", "Fair Value"] = 80.0
    res = mc.compare_snapshots(old, new)
    fvs = [r for r in res["fair_values"] if r["ticker"] == "AAA"]
    assert len(fvs) == 1
    row = fvs[0]
    assert row["direction"] == "cut"
    assert row["pct_change"] == pytest.approx(-0.20)
    # Old upside (100-90)/90 = +11.1%; new upside (80-90)/90 = -11.1%.
    assert row["old_upside"] == pytest.approx((100 - 90) / 90)
    assert row["new_upside"] == pytest.approx((80 - 90) / 90)
    # The value case shrank from the analyst side.
    assert row["new_upside"] < row["old_upside"]


def test_tiny_fair_value_move_below_threshold_ignored():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Fair Value"] = 101.0  # +1% < 2%
    res = mc.compare_snapshots(old, new)
    assert all(r["ticker"] != "AAA" for r in res["fair_values"])


def test_fair_value_cut_sorts_before_raise():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Fair Value"] = 130.0  # +30% raise
    new.loc[new["Ticker"] == "BBB", "Fair Value"] = 180.0  # -10% cut
    res = mc.compare_snapshots(old, new)
    assert res["fair_values"][0]["direction"] == "cut"
    assert res["fair_values"][0]["ticker"] == "BBB"


def test_moat_change_widen_and_narrow():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Economic Moat"] = "Wide"   # Narrow->Wide
    new.loc[new["Ticker"] == "BBB", "Economic Moat"] = "Narrow"  # Wide->Narrow
    res = mc.compare_snapshots(old, new)
    assert res["counts"]["moats"] == 2
    by_t = {r["ticker"]: r for r in res["moats"]}
    assert by_t["AAA"]["direction"] == "widened"
    assert by_t["BBB"]["direction"] == "narrowed"
    # Narrowing (louder) sorts first.
    assert res["moats"][0]["direction"] == "narrowed"


def test_grade_and_uncertainty_changes():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "BBB", "Growth Grade"] = "D"          # C->D downgrade
    new.loc[new["Ticker"] == "AAA", "Fair Value Uncertainty"] = "High"  # Medium->High rise
    res = mc.compare_snapshots(old, new)
    grades = [r for r in res["grades"] if r["ticker"] == "BBB"]
    assert grades and grades[0]["direction"] == "downgrade"
    assert grades[0]["field"] == "Growth Grade"
    unc = [r for r in res["uncertainty"] if r["ticker"] == "AAA"]
    assert unc and unc[0]["direction"] == "rose"


# ---------------------------------------------------------------------------
# Fundamental shifts
# ---------------------------------------------------------------------------
def test_material_margin_move_flagged():
    old, new = _base(), _base()
    # AAA net margin 20 -> 26 (+6 pts, well past 2-pt threshold).
    new.loc[new["Ticker"] == "AAA", "Net Margin"] = 26.0
    res = mc.compare_snapshots(old, new)
    nm = [r for r in res["fundamentals"]
          if r["ticker"] == "AAA" and r["field"] == "Net Margin"]
    assert len(nm) == 1
    assert nm[0]["delta"] == pytest.approx(6.0)
    assert nm[0]["score"] == pytest.approx(3.0)  # 6 / 2-pt threshold


def test_trivial_margin_move_not_flagged():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Net Margin"] = 21.0  # +1 pt < 2
    res = mc.compare_snapshots(old, new)
    assert all(not (r["ticker"] == "AAA" and r["field"] == "Net Margin")
               for r in res["fundamentals"])


def test_fundamentals_ranked_by_score():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Net Margin"] = 30.0       # +10 pts -> score 5
    new.loc[new["Ticker"] == "BBB", "Gross Margin"] = 43.5     # +3.5 pts -> score 1.75
    res = mc.compare_snapshots(old, new)
    assert res["fundamentals"][0]["score"] >= res["fundamentals"][-1]["score"]
    assert res["fundamentals"][0]["ticker"] == "AAA"


def test_forward_pe_relative_threshold():
    old, new = _base(), _base()
    # AAA fwd P/E 25 -> 28 (+12% > 10% relative).
    new.loc[new["Ticker"] == "AAA", "Price/Earnings (Forward)"] = 28.0
    res = mc.compare_snapshots(old, new)
    pe = [r for r in res["fundamentals"]
          if r["ticker"] == "AAA" and r["field"] == "Price/Earnings (Forward)"]
    assert len(pe) == 1
    assert pe[0]["rel"] == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# Universe structural changes
# ---------------------------------------------------------------------------
def test_added_and_dropped_tickers():
    old = _base()
    new = _base()
    # Drop BBB, add CCC.
    new = new[new["Ticker"] != "BBB"].copy()
    new = pd.concat([new, pd.DataFrame([{
        "Ticker": "CCC", "Name": "Gamma",
        "Morningstar Rating for Stocks": 3.0, "Economic Moat": "Narrow",
        "Fair Value": 50.0, "Last Price": 48.0}])], ignore_index=True)
    res = mc.compare_snapshots(old, new)
    assert res["added"] == ["CCC"]
    assert res["dropped"] == ["BBB"]
    # One-sided tickers must NOT appear in the change tables.
    assert all(r["ticker"] != "CCC" for r in res["ratings"])
    assert all(r["ticker"] != "BBB" for r in res["fair_values"])


# ---------------------------------------------------------------------------
# Robustness / null handling
# ---------------------------------------------------------------------------
def test_one_sided_nulls_do_not_create_change_rows():
    old, new = _base(), _base()
    # AAA gains rating coverage in new only (old NaN) -> not a "change".
    old.loc[old["Ticker"] == "AAA", "Morningstar Rating for Stocks"] = np.nan
    res = mc.compare_snapshots(old, new)
    assert all(r["ticker"] != "AAA" for r in res["ratings"])


def test_empty_old_returns_all_added():
    new = _base()
    res = mc.compare_snapshots(pd.DataFrame(), new)
    assert set(res["added"]) == {"AAA", "BBB"}
    assert res["counts"]["ratings"] == 0


def test_no_advice_language_in_directions():
    old, new = _base(), _base()
    new.loc[new["Ticker"] == "AAA", "Fair Value"] = 80.0
    new.loc[new["Ticker"] == "BBB", "Morningstar Rating for Stocks"] = 1.0
    res = mc.compare_snapshots(old, new)
    blob = " ".join(str(r) for grp in
                    ("ratings", "fair_values", "moats", "grades") for r in res[grp]).lower()
    for word in ("buy", "sell", "target price", "recommend"):
        assert word not in blob
