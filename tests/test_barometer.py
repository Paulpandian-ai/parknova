"""Unit tests for core/barometer.py — style-box parsing, cell stats, alpha hunt."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import barometer as bm


# ---------------------------------------------------------------------------
# Style-box parsing (incl. Core / Blend variants)
# ---------------------------------------------------------------------------
class TestParseStyleBox:
    def test_all_nine_cells_map(self):
        for size in ("Large", "Mid", "Small"):
            for style in ("Value", "Core", "Growth"):
                parsed = bm.parse_style_box(f"{size} {style}")
                assert parsed == (size, style)

    def test_blend_maps_to_core(self):
        assert bm.parse_style_box("Mid Blend") == ("Mid", "Core")
        assert bm.parse_style_box("Large Blend") == ("Large", "Core")

    def test_medium_alias_and_case_insensitive(self):
        assert bm.parse_style_box("medium growth") == ("Mid", "Growth")
        assert bm.parse_style_box("SMALL VALUE") == ("Small", "Value")

    def test_missing_or_unmappable_returns_none(self):
        assert bm.parse_style_box(None) is None
        assert bm.parse_style_box(np.nan) is None
        assert bm.parse_style_box("") is None
        assert bm.parse_style_box("Giant Sideways") is None

    def test_style_cell_label_and_unclassified(self):
        assert bm.style_cell("Small Growth") == "Small Growth"
        assert bm.style_cell(None) == bm.UNCLASSIFIED


# ---------------------------------------------------------------------------
# Fixture frame
# ---------------------------------------------------------------------------
def _frame():
    rows = []

    def add(t, sb, ret, upside, trap, val, qual, grw, mom, pe=20.0,
            bucket="1 Compute Semi", crest="Early", side="Supplier",
            tier="Tier 1"):
        rows.append({
            "Ticker": t, "Name": f"Co {t}", "Stock Style Box": sb,
            "Primary Bucket": bucket, "Crest": crest, "Side": side,
            "1Y": ret, "upside_fv": upside, "valuation_tier": tier,
            "value_trap": trap, "Price/Earnings (Forward)": pe,
            "Value": val, "Quality": qual, "Growth": grw, "Momentum": mom})

    # Small Growth cell: 4 names (thin), hot. Two below FV (one trap, one clean).
    add("SG1", "Small Growth", 0.40, 0.25, False, 60, 70, 80, 90)   # hot + cheap + clean
    add("SG2", "Small Growth", 0.35, 0.18, False, 55, 65, 78, 85)   # hot + cheap + clean
    add("SG3", "Small Growth", 0.50, 0.30, True, 40, 30, 88, 95)    # cheap but TRAP
    add("SG4", "Small Growth", 0.30, -0.10, False, 50, 60, 70, 80)  # above FV (priced-in)

    # Large Value cell: 6 names (not thin), cold.
    for i in range(6):
        add(f"LV{i}", "Large Value", -0.05 + i * 0.01, 0.05, False,
            80, 50, 20, 30)

    # Mid Core cell: 1 name (thin), middling.
    add("MC1", "Mid Core", 0.10, 0.02, False, 60, 60, 50, 50)

    # Unmappable style box -> Unclassified.
    add("UNC", "Giant Sideways", 0.99, 0.40, False, 50, 50, 50, 50)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cell stats: medians, counts, thin flag
# ---------------------------------------------------------------------------
class TestCellStats:
    def test_style_box_always_nine_cells_plus_unclassified(self):
        cells = bm.cell_stats(_frame(), bm.GROUP_STYLE, "1Y")
        labels = {c["label"] for c in cells}
        for size in ("Large", "Mid", "Small"):
            for style in ("Value", "Core", "Growth"):
                assert f"{size} {style}" in labels
        assert bm.UNCLASSIFIED in labels  # the unmappable name surfaced separately

    def test_unclassified_not_folded_into_a_grid_cell(self):
        cells = {c["label"]: c for c in bm.cell_stats(_frame(), bm.GROUP_STYLE, "1Y")}
        # The unmappable name must NOT inflate any of the 9 grid cells.
        total_grid_n = sum(c["n"] for lbl, c in cells.items()
                           if lbl != bm.UNCLASSIFIED)
        assert cells[bm.UNCLASSIFIED]["n"] == 1
        assert total_grid_n == len(_frame()) - 1

    def test_median_and_count_per_cell(self):
        cells = {c["label"]: c for c in bm.cell_stats(_frame(), bm.GROUP_STYLE, "1Y")}
        sg = cells["Small Growth"]
        assert sg["n"] == 4
        assert sg["median_return"] == pytest.approx(np.median([0.40, 0.35, 0.50, 0.30]))
        assert sg["median_upside_fv"] is not None

    def test_thin_cell_flag(self):
        cells = {c["label"]: c for c in bm.cell_stats(_frame(), bm.GROUP_STYLE, "1Y")}
        assert cells["Small Growth"]["thin"] is True   # n=4 < 5
        assert cells["Mid Core"]["thin"] is True        # n=1
        assert cells["Large Value"]["thin"] is False    # n=6
        # Empty cells are not "thin" (n=0), just empty.
        assert cells["Mid Growth"]["n"] == 0
        assert cells["Mid Growth"]["thin"] is False

    def test_bucket_and_crest_groupings(self):
        bucket_cells = bm.cell_stats(_frame(), bm.GROUP_BUCKET, "1Y")
        assert any(c["label"] == "1 Compute Semi" for c in bucket_cells)
        crest_cells = bm.cell_stats(_frame(), bm.GROUP_CREST, "1Y")
        assert any(c["label"] == "Early / Supplier" for c in crest_cells)


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------
class TestDrillDown:
    def test_constituents_columns_and_sort(self):
        d = bm.cell_constituents(_frame(), bm.GROUP_STYLE, "Small Growth", "1Y")
        assert list(d["Ticker"])[0] == "SG3"  # highest upside_fv (0.30) first
        for col in ("upside_fv", "valuation_tier", "value_trap",
                    "Value", "Quality", "Growth", "Momentum", "1Y"):
            assert col in d.columns

    def test_below_fv_filter(self):
        d = bm.cell_constituents(_frame(), bm.GROUP_STYLE, "Small Growth", "1Y",
                                 below_fv_only=True)
        # SG4 (upside -0.10, above FV) excluded; the three positive-upside kept.
        assert set(d["Ticker"]) == {"SG1", "SG2", "SG3"}
        assert (d["upside_fv"] > 0).all()


# ---------------------------------------------------------------------------
# Alpha hunt
# ---------------------------------------------------------------------------
class TestAlphaHunt:
    def test_outperforming_cells_beat_universe_median(self):
        hot = bm.outperforming_cells(_frame(), bm.GROUP_STYLE, "1Y")
        assert "Small Growth" in hot         # hot cell
        assert "Large Value" not in hot       # cold cell

    def test_alpha_selects_below_fv_non_trap_in_hot_cells(self):
        res = bm.alpha_hunt(_frame(), bm.GROUP_STYLE, "1Y")
        tickers = list(res["Ticker"])
        # SG1 & SG2: hot cell, below FV, not trap -> included.
        assert "SG1" in tickers and "SG2" in tickers
        # SG3 is below FV but a value trap -> excluded.
        assert "SG3" not in tickers
        # SG4 is in the hot cell but above FV (priced-in) -> excluded.
        assert "SG4" not in tickers
        # Ranked by upside-to-FV descending.
        ups = list(res["upside_fv"])
        assert ups == sorted(ups, reverse=True)

    def test_alpha_empty_when_no_hot_cells(self):
        df = _frame().copy()
        df["1Y"] = 0.0  # flat: no cell beats the median
        res = bm.alpha_hunt(df, bm.GROUP_STYLE, "1Y")
        assert res.empty

    def test_no_advice_language_in_columns(self):
        res = bm.alpha_hunt(_frame(), bm.GROUP_STYLE, "1Y")
        for col in res.columns:
            assert "buy" not in str(col).lower()
            assert "sell" not in str(col).lower()
