"""Unit tests for core/divergence.py — the three price–fundamentals classifiers."""

from __future__ import annotations

import pandas as pd

from core import divergence as dv


def _frames(rows):
    """Build (full, perf_full, scores) from a list of per-ticker dicts.

    Each row dict may set: ticker, today, w1, m1, upside, value, quality, growth,
    name, crest, side, bucket, value_trap.
    """
    full_rows, perf_rows, score_rows = [], [], []
    for r in rows:
        t = r["ticker"]
        full_rows.append({
            "Ticker": t, "Name": r.get("name", f"Co {t}"),
            "Crest": r.get("crest", "Mid"), "Side": r.get("side", "Supplier"),
            "Primary Bucket": r.get("bucket", "1 Compute Semi"),
            "upside_fv": r.get("upside"),
            "valuation_tier": r.get("tier", "Tier 1"),
            "value_trap": r.get("value_trap", False),
        })
        perf_rows.append({
            "Ticker": t, "Today": r.get("today"), "1W": r.get("w1"),
            "1M": r.get("m1"),
        })
        score_rows.append({
            "Ticker": t, "Value": r.get("value"), "Quality": r.get("quality"),
            "Growth": r.get("growth"),
        })
    return (pd.DataFrame(full_rows), pd.DataFrame(perf_rows),
            pd.DataFrame(score_rows))


def _filler(n, m1=0.0):
    """Neutral filler names so universe quartiles are well-defined."""
    return [{"ticker": f"F{i}", "today": 0.0, "w1": 0.0, "m1": m1,
             "upside": 0.0, "value": 50, "quality": 50, "growth": 50}
            for i in range(n)]


# ---------------------------------------------------------------------------
# Flavor A — euphoria / stretched (price ahead of value)
# ---------------------------------------------------------------------------
class TestEuphoria:
    def test_known_euphoria_case(self):
        target = {"ticker": "HOT", "today": 0.06, "w1": 0.20, "m1": 0.45,
                  "upside": -0.25,  # 25% above fair value
                  "value": 30, "quality": 55, "growth": 60}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        tickers = [r["ticker"] for r in groups["euphoria"]]
        assert "HOT" in tickers
        row = next(r for r in groups["euphoria"] if r["ticker"] == "HOT")
        assert row["direction"] == "up"
        assert "above fair value" in row["reason"]
        assert "upside-to-FV -25%" in row["reason"]

    def test_up_but_cheap_not_euphoria(self):
        # Strong up move but still a discount to FV → not euphoria.
        target = {"ticker": "OK", "today": 0.06, "w1": 0.20, "m1": 0.45,
                  "upside": 0.30, "value": 70, "quality": 60, "growth": 60}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        assert "OK" not in [r["ticker"] for r in groups["euphoria"]]


# ---------------------------------------------------------------------------
# Flavor B — cheap and falling (possible opportunity)
# ---------------------------------------------------------------------------
class TestOpportunity:
    def test_known_cheap_and_falling(self):
        target = {"ticker": "BABA", "today": -0.03, "w1": -0.10, "m1": -0.22,
                  "upside": 0.28,  # 28% below fair value
                  "value": 80, "quality": 70, "growth": 50}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        tickers = [r["ticker"] for r in groups["opportunity"]]
        assert "BABA" in tickers
        row = next(r for r in groups["opportunity"] if r["ticker"] == "BABA")
        assert row["direction"] == "down"
        assert "below fair value" in row["reason"]

    def test_widened_discount_noted_with_prior(self):
        target = {"ticker": "BABA", "today": -0.03, "w1": -0.10, "m1": -0.22,
                  "upside": 0.28, "value": 80, "quality": 70, "growth": 50}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(
            full, perf, scores, prior_fv={"BABA": 0.12})
        row = next(r for r in groups["opportunity"] if r["ticker"] == "BABA")
        assert "widened" in row["reason"]
        assert "+12%" in row["reason"] and "+28%" in row["reason"]

    def test_down_but_expensive_not_opportunity(self):
        target = {"ticker": "EXP", "today": -0.03, "w1": -0.10, "m1": -0.22,
                  "upside": -0.20, "value": 20, "quality": 40, "growth": 40}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        assert "EXP" not in [r["ticker"] for r in groups["opportunity"]]


# ---------------------------------------------------------------------------
# Flavor C — momentum vs fundamentals dislocation
# ---------------------------------------------------------------------------
class TestDislocation:
    def test_up_on_weak_fundamentals(self):
        target = {"ticker": "WEAK", "today": 0.09, "w1": 0.15, "m1": 0.30,
                  "upside": 0.05,  # near FV, so not euphoria
                  "value": 18, "quality": 22, "growth": 40}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        row = next((r for r in groups["dislocation"] if r["ticker"] == "WEAK"),
                   None)
        assert row is not None
        assert row["direction"] == "up"
        assert "not supported by fundamentals" in row["reason"]
        assert "Quality 22th pct" in row["reason"]

    def test_down_despite_strong_fundamentals(self):
        target = {"ticker": "STRONG", "today": -0.08, "w1": -0.12, "m1": -0.20,
                  "upside": 0.0, "value": 60, "quality": 81, "growth": 78}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        row = next((r for r in groups["dislocation"] if r["ticker"] == "STRONG"),
                   None)
        assert row is not None
        assert row["direction"] == "down"
        assert "not matched by fundamentals" in row["reason"]

    def test_up_on_strong_fundamentals_no_dislocation(self):
        target = {"ticker": "ALIGNED", "today": 0.09, "w1": 0.15, "m1": 0.30,
                  "upside": 0.0, "value": 75, "quality": 80, "growth": 80}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        assert "ALIGNED" not in [r["ticker"] for r in groups["dislocation"]]


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------
class TestRobustness:
    def test_empty_frames(self):
        groups = dv.price_fundamentals_divergence(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        assert groups == {"euphoria": [], "opportunity": [], "dislocation": []}

    def test_missing_fv_skips_a_and_b(self):
        target = {"ticker": "NOFV", "today": 0.06, "w1": 0.20, "m1": 0.45,
                  "upside": None, "value": 30, "quality": 30, "growth": 30}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        assert "NOFV" not in [r["ticker"] for r in groups["euphoria"]]
        assert "NOFV" not in [r["ticker"] for r in groups["opportunity"]]

    def test_missing_factors_skips_c(self):
        target = {"ticker": "NOFAC", "today": 0.09, "w1": 0.15, "m1": 0.30,
                  "upside": 0.0, "value": None, "quality": None, "growth": None}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        assert "NOFAC" not in [r["ticker"] for r in groups["dislocation"]]

    def test_no_earnings_surprise_language(self):
        target = {"ticker": "HOT", "today": 0.06, "w1": 0.20, "m1": 0.45,
                  "upside": -0.25, "value": 30, "quality": 22, "growth": 20}
        full, perf, scores = _frames([target] + _filler(10))
        groups = dv.price_fundamentals_divergence(full, perf, scores)
        for flavor in groups.values():
            for row in flavor:
                text = row["reason"].lower()
                assert "surprise" not in text
                assert "beat" not in text
                assert "miss" not in text
                assert "buy" not in text
                assert "sell" not in text or "sell-off" in text
