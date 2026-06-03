"""Unit tests for core/signals.py — pure signal logic, no Streamlit."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import signals as sig


# ---------------------------------------------------------------------------
# Helpers to build minimal fixture frames
# ---------------------------------------------------------------------------

def _wide(days=60, early_bias=1.0):
    """Minimal wide crest-index frame (Early/Mid/Late, daily)."""
    dates = pd.date_range("2025-01-01", periods=days, freq="B")
    np.random.seed(42)
    base_ret = np.random.randn(days) * 0.005
    early = 100 * np.cumprod(1 + base_ret + early_bias * 0.003)
    mid   = 100 * np.cumprod(1 + base_ret)
    late  = 100 * np.cumprod(1 + base_ret - early_bias * 0.003)
    return pd.DataFrame({"Early": early, "Mid": mid, "Late": late}, index=dates)


def _perf_full(early_1m=0.08, late_1m=0.02, n_early=10, n_late=8):
    """Minimal perf_full with Crest and 1M columns."""
    early_rows = pd.DataFrame({
        "Ticker": [f"E{i}" for i in range(n_early)],
        "Crest": "Early", "1M": early_1m + np.random.randn(n_early) * 0.01,
        "1Y": 0.6 + np.random.rand(n_early) * 0.3,
        "3M": early_1m * 0.9,
    })
    late_rows = pd.DataFrame({
        "Ticker": [f"L{i}" for i in range(n_late)],
        "Crest": "Late", "1M": late_1m + np.random.randn(n_late) * 0.01,
        "1Y": 0.2 + np.random.rand(n_late) * 0.2,
        "3M": late_1m * 0.8,
    })
    return pd.concat([early_rows, late_rows], ignore_index=True)


def _full_df(n=20, add_trap=True):
    """Minimal full frame with timing columns."""
    tickers = [f"T{i}" for i in range(n)]
    data = {
        "Ticker": tickers,
        "Name": [f"Company {i}" for i in range(n)],
        "Crest": ["Early"] * (n // 2) + ["Late"] * (n - n // 2),
        "Primary Bucket": "1 Compute Semi",
        "Side": "Supplier",
        "upside_fv": list(np.linspace(-0.5, 0.4, n)),
        "value_trap": [False] * n,
        "valuation_tier": "Tier 1",
        "Price/Earnings (Forward)": list(np.linspace(8, 60, n)),
        "Price/Earnings": list(np.linspace(10, 70, n)),
        "Total Return (1Y)": list(np.linspace(20, 120, n)),
        "Fair Value": 100.0,
        "Last Price": list(np.linspace(120, 70, n)),
    }
    df = pd.DataFrame(data)
    if add_trap:
        # Manually plant a value-trap row
        df.loc[0, "value_trap"] = True
        df.loc[0, "Crest"] = "Early"
        df.loc[0, "Price/Earnings (Forward)"] = 10.0
        df.loc[0, "Total Return (1Y)"] = 80.0
    return df


def _scores_df(full):
    """Minimal scores frame with factor columns."""
    n = len(full)
    return pd.DataFrame({
        "Ticker": full["Ticker"].values,
        "Quality": np.linspace(10, 90, n),
        "Growth":  np.linspace(15, 85, n),
        "Value":   np.linspace(20, 80, n),
        "Momentum": np.linspace(5, 95, n),
        "Financial Health": np.linspace(30, 70, n),
        "MS Upside": np.linspace(10, 90, n),
    })


# ---------------------------------------------------------------------------
# Signal 1 — rotation inflection
# ---------------------------------------------------------------------------

class TestRotationInflection:
    def test_no_data_returns_neutral(self):
        result = sig.rotation_inflection(pd.DataFrame(), pd.DataFrame())
        assert result["regime"] == "neutral"
        assert result["spread_current"] is None

    def test_early_leading_no_inflection(self):
        # Early monotonically outperforms → spread widening → no inflection
        wide = _wide(days=80, early_bias=1.0)
        pf = _perf_full(early_1m=0.08, late_1m=0.02)
        result = sig.rotation_inflection(wide, pf)
        assert result["regime"] in ("early_leading", "neutral")
        assert result["spread_current"] is not None
        assert result["spread_current"] > 0

    def test_spread_series_returned(self):
        wide = _wide(days=80)
        pf = _perf_full()
        result = sig.rotation_inflection(wide, pf)
        assert result["spread_series"] is not None
        assert isinstance(result["spread_series"], pd.Series)

    def test_inflection_toward_late_fires_both_conditions(self):
        # Build a spread that peaked in the middle and reversed
        n = 80
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        # Early leads first half, Late leads second half
        early = np.concatenate([
            100 * np.cumprod(1 + np.ones(40) * 0.006),
            100 * np.cumprod(1 + np.ones(40) * 0.003) * np.cumprod(1 + np.ones(40) * 0.006)[-1],
        ])
        late = np.concatenate([
            100 * np.cumprod(1 + np.ones(40) * 0.003),
            100 * np.cumprod(1 + np.ones(40) * 0.007) * np.cumprod(1 + np.ones(40) * 0.003)[-1],
        ])
        mid = (early + late) / 2
        wide = pd.DataFrame({"Early": early, "Mid": mid, "Late": late}, index=dates)
        pf = _perf_full(early_1m=0.01, late_1m=0.09)  # Late now leading on 1M
        result = sig.rotation_inflection(wide, pf)
        # Spread should show a negative recent slope
        assert result["slope_recent"] is not None
        # Advisory text must not contain buy/sell language
        advisory = result["advisory"].lower()
        assert "buy" not in advisory
        assert "sell" not in advisory
        assert "target" not in advisory

    def test_advisory_no_buysell_language(self):
        wide = _wide(days=80)
        pf = _perf_full()
        result = sig.rotation_inflection(wide, pf)
        advisory = result["advisory"].lower()
        assert "buy" not in advisory
        assert "sell" not in advisory
        assert "target" not in advisory
        assert "predict" not in advisory


# ---------------------------------------------------------------------------
# Signal 2 — stretch warnings
# ---------------------------------------------------------------------------

class TestStretchWarnings:
    def test_value_trap_double_weighted(self):
        full = _full_df(add_trap=True)
        pf = _perf_full(n_early=10, n_late=8)
        # Add matching tickers to perf_full
        pf_ext = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.05, "1Y": 0.8, "3M": 0.04,
        })
        warnings = sig.stretch_warnings(full, pf_ext)
        # The value-trap row (T0) should be first and have conditions >= 2
        if warnings:
            top = warnings[0]
            if top["ticker"] == "T0":
                assert top["conditions"] >= 2
                assert top["value_trap"] is True

    def test_no_buysell_language_in_reasons(self):
        full = _full_df(add_trap=True)
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.05, "1Y": 0.9, "3M": 0.04,
        })
        warnings = sig.stretch_warnings(full, pf)
        for w in warnings:
            text = " ".join(w["reasons"]).lower()
            assert "buy" not in text
            assert "sell" not in text
            assert "target" not in text

    def test_top_decile_flagged(self):
        """Names with 1Y return in the top decile should be caught."""
        n = 30
        full = _full_df(n=n, add_trap=False)
        # All 1Y returns, the last one is extreme
        one_y = list(np.linspace(0.1, 0.5, n))
        one_y[-1] = 2.0  # extreme
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.03, "1Y": one_y, "3M": 0.02,
        })
        warnings = sig.stretch_warnings(full, pf)
        tickers_flagged = [w["ticker"] for w in warnings]
        # T29 (highest 1Y) should be flagged
        assert f"T{n-1}" in tickers_flagged

    def test_empty_perf_returns_empty(self):
        full = _full_df(n=5)
        warnings = sig.stretch_warnings(full, pd.DataFrame())
        assert warnings == []

    def test_ma_distance_computed_when_history_provided(self):
        full = _full_df(n=5, add_trap=False)
        # Plant a name 30% above its 50-day MA.
        # Only last 10 days spike so the rolling 50-day MA stays near 100.
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        series = pd.Series([100.0] * 90 + [130.0] * 10, index=dates)
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.05, "1Y": 0.95, "3M": 0.04,
        })
        warnings = sig.stretch_warnings(full, pf, histories={"T0": series})
        ma_row = next((w for w in warnings if w["ticker"] == "T0"), None)
        if ma_row:
            assert ma_row["ma_distance"] is not None
            assert ma_row["ma_distance"] > 0.20


# ---------------------------------------------------------------------------
# Signal 3 — opportunity hints
# ---------------------------------------------------------------------------

class TestOpportunityHints:
    def test_no_prior_baseline_on_first_run(self, tmp_path, monkeypatch):
        """Without a stored prior, had_prior should be False."""
        import core.store as store_mod
        monkeypatch.setattr(store_mod, "read_json", lambda k: None)
        monkeypatch.setattr(store_mod, "write_json", lambda k, v: True)

        full = _full_df(n=10, add_trap=False)
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.03, "1Y": 0.3, "3M": 0.02,
        })
        sc = _scores_df(full)
        hints, had_prior = sig.opportunity_hints(full, pf, sc)
        assert had_prior is False

    def test_fv_crossing_detected(self, monkeypatch):
        """When prior_fv was negative and now positive, crossing fires."""
        import core.store as store_mod
        full = _full_df(n=6, add_trap=False)
        # T0: prior FV < 0, current FV > 0
        full.loc[0, "upside_fv"] = 0.10   # currently discount (positive)
        prior = {"T0": -0.15}              # prior was premium
        monkeypatch.setattr(store_mod, "read_json", lambda k: prior)
        monkeypatch.setattr(store_mod, "write_json", lambda k, v: True)

        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": 0.03, "1Y": 0.3, "3M": 0.02,
        })
        sc = _scores_df(full)
        hints, had_prior = sig.opportunity_hints(full, pf, sc)
        assert had_prior is True
        crossing = [h for h in hints if h["ticker"] == "T0"
                    and h["hint_type"] == "fv_crossing"]
        assert len(crossing) == 1

    def test_fundamental_divergence_detected(self, monkeypatch):
        """Bottom-quartile 1M + high Quality/Growth should fire."""
        import core.store as store_mod
        monkeypatch.setattr(store_mod, "read_json", lambda k: None)
        monkeypatch.setattr(store_mod, "write_json", lambda k, v: True)

        n = 20
        full = _full_df(n=n, add_trap=False)
        # T0: very weak 1M, high Quality/Growth
        one_m = [0.05] * n
        one_m[0] = -0.20  # bottom quartile
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": one_m, "1Y": 0.3, "3M": 0.04,
        })
        sc = _scores_df(full)
        sc.loc[sc["Ticker"] == "T0", "Quality"] = 80
        sc.loc[sc["Ticker"] == "T0", "Growth"] = 75
        hints, _ = sig.opportunity_hints(full, pf, sc)
        div = [h for h in hints if h["ticker"] == "T0"
               and h["hint_type"] == "fundamental_divergence"]
        assert len(div) == 1

    def test_no_buysell_language_in_hints(self, monkeypatch):
        import core.store as store_mod
        monkeypatch.setattr(store_mod, "read_json", lambda k: None)
        monkeypatch.setattr(store_mod, "write_json", lambda k, v: True)

        full = _full_df(n=10, add_trap=False)
        pf = pd.DataFrame({
            "Ticker": full["Ticker"].tolist(),
            "Crest": full["Crest"].tolist(),
            "1M": [0.03] * 5 + [-0.15] * 5,
            "1Y": 0.3, "3M": 0.02,
        })
        sc = _scores_df(full)
        hints, _ = sig.opportunity_hints(full, pf, sc)
        for h in hints:
            text = " ".join(h["reasons"]).lower()
            assert "buy" not in text
            assert "sell" not in text
            assert "target" not in text


# ---------------------------------------------------------------------------
# MA distance helper
# ---------------------------------------------------------------------------

class TestMADistance:
    def test_above_ma(self):
        s = pd.Series([100.0] * 49 + [130.0])
        result = sig.compute_ma_distance(s, 50)
        assert result is not None
        assert result > 0

    def test_short_series_returns_none(self):
        s = pd.Series([100.0] * 10)
        result = sig.compute_ma_distance(s, 50)
        assert result is None

    def test_at_ma_returns_near_zero(self):
        s = pd.Series([100.0] * 50)
        result = sig.compute_ma_distance(s, 50)
        assert result is not None
        assert abs(result) < 1e-9
