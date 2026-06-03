"""Unit tests for core/portfolios.py — paper-portfolio data model + scoring."""

from __future__ import annotations

import pytest

from core import portfolios as pf


# ---------------------------------------------------------------------------
# In-memory store so tests never touch disk/S3
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def mem_store(monkeypatch):
    import core.store as store_mod
    db: dict = {}
    monkeypatch.setattr(store_mod, "read_json", lambda k: db.get(k))
    monkeypatch.setattr(store_mod, "write_json",
                        lambda k, v: (db.__setitem__(k, v) or True))
    monkeypatch.setattr(store_mod, "delete_json",
                        lambda k: (db.pop(k, None) or True))
    return db


def _evidence(upside=0.10, value=70, quality=60, composite=65,
              ms_upside=55, trap=False, crest="Early", side="Supplier",
              bucket="1 Compute Semi"):
    return {
        "factors": {"Value": value, "Quality": quality, "Composite": composite,
                    "MS Upside": ms_upside},
        "valuation": {"upside_fv": upside, "value_trap": trap},
        "position": {"crest": crest, "side": side, "primary_bucket": bucket},
        "returns": {},
    }


def _entry(upside=0.10, value=70, conviction=6, trap=False, crest="Early"):
    return {"evidence": _evidence(upside=upside, value=value, trap=trap,
                                  crest=crest),
            "signals": []}


# ---------------------------------------------------------------------------
# CRUD + persistence
# ---------------------------------------------------------------------------
class TestPortfolioCRUD:
    def test_empty_on_first_run(self):
        assert pf.list_portfolios() == []

    def test_create_and_reload(self):
        rec = pf.create_portfolio("Value Tilt", "Buy below fair value")
        assert rec["portfolio_id"] == "value-tilt"
        idx = pf.list_portfolios()
        assert len(idx) == 1
        assert idx[0]["name"] == "Value Tilt"
        # Reload by id
        again = pf.read_portfolio("value-tilt")
        assert again["strategy_note"] == "Buy below fair value"
        assert again["cash_start"] == pf.DEFAULT_CASH

    def test_multiple_portfolios_unique_ids(self):
        pf.create_portfolio("Momentum")
        pf.create_portfolio("Momentum")
        ids = [e["portfolio_id"] for e in pf.list_portfolios()]
        assert ids == ["momentum", "momentum-2"]

    def test_delete(self):
        pf.create_portfolio("Temp")
        assert len(pf.list_portfolios()) == 1
        pf.delete_portfolio("temp")
        assert pf.list_portfolios() == []
        assert pf.read_portfolio("temp") is None


# ---------------------------------------------------------------------------
# Positions + frozen thesis-at-entry
# ---------------------------------------------------------------------------
class TestPositions:
    def test_open_freezes_snapshot(self):
        pf.create_portfolio("P1")
        rec = pf.open_position("p1", "MU", "BUY", 100, 92.86,
                               thesis_at_entry=_entry(upside=0.12),
                               rationale="cheap", conviction=6)
        pos = rec["positions"][0]
        assert pos["ticker"] == "MU"
        assert pos["status"] == "open"
        assert pos["thesis_at_entry"]["evidence"]["valuation"]["upside_fv"] == 0.12
        assert pos["thesis_at_entry"]["conviction"] == 6

    def test_snapshot_does_not_mutate_after_entry(self):
        pf.create_portfolio("P1")
        snap = _entry(upside=0.12)
        pf.open_position("p1", "MU", "BUY", 100, 92.86, thesis_at_entry=snap)
        # Mutate the source dict after entry; stored copy must be unchanged.
        snap["evidence"]["valuation"]["upside_fv"] = -0.99
        rec = pf.read_portfolio("p1")
        stored = rec["positions"][0]["thesis_at_entry"]["evidence"]
        assert stored["valuation"]["upside_fv"] == 0.12

    def test_close_records_exit(self):
        pf.create_portfolio("P1")
        rec = pf.open_position("p1", "MU", "BUY", 100, 90.0,
                               thesis_at_entry=_entry())
        pid = rec["positions"][0]["id"]
        rec = pf.close_position("p1", pid, exit_price=110.0)
        pos = rec["positions"][0]
        assert pos["status"] == "closed"
        assert pos["exit_price"] == 110.0
        assert pos["exit_date"] is not None


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------
class TestPnL:
    def test_buy_gain(self):
        pos = {"action": "BUY", "shares": 100, "entry_price": 100.0,
               "status": "open"}
        r = pf.position_pnl(pos, 120.0)
        assert r["return_pct"] == pytest.approx(0.20)
        assert r["dollar_pnl"] == pytest.approx(2000.0)

    def test_sell_short_gain_on_decline(self):
        pos = {"action": "SELL", "shares": 100, "entry_price": 100.0,
               "status": "open"}
        r = pf.position_pnl(pos, 80.0)
        assert r["return_pct"] == pytest.approx(0.20)
        assert r["dollar_pnl"] == pytest.approx(2000.0)

    def test_missing_price_is_safe(self):
        pos = {"action": "BUY", "shares": 100, "entry_price": 100.0,
               "status": "open"}
        r = pf.position_pnl(pos, None)
        assert r["return_pct"] is None
        assert r["dollar_pnl"] is None

    def test_closed_uses_exit_price(self):
        pos = {"action": "BUY", "shares": 10, "entry_price": 100.0,
               "status": "closed", "exit_price": 150.0}
        r = pf.position_pnl(pos, 999.0)  # current price ignored when closed
        assert r["return_pct"] == pytest.approx(0.50)
        assert r["realized"] is True


# ---------------------------------------------------------------------------
# Thesis-held comparison
# ---------------------------------------------------------------------------
class TestThesisHeld:
    def test_improved(self):
        entry = _evidence(upside=0.05, value=60, composite=60)
        now = _evidence(upside=0.20, value=75, composite=72)
        r = pf.thesis_held(entry, now)
        assert r["status"] == "improved"

    def test_weakened(self):
        entry = _evidence(upside=0.20, value=80, composite=75)
        now = _evidence(upside=-0.10, value=65, composite=60)
        r = pf.thesis_held(entry, now)
        assert r["status"] == "weakened"

    def test_held_when_flat(self):
        entry = _evidence(upside=0.10, value=70, composite=65)
        now = _evidence(upside=0.10, value=70, composite=65)
        r = pf.thesis_held(entry, now)
        assert r["status"] == "held"

    def test_value_trap_onset_weakens(self):
        entry = _evidence(upside=0.10, trap=False)
        now = _evidence(upside=0.10, trap=True)
        r = pf.thesis_held(entry, now)
        assert r["status"] == "weakened"


# ---------------------------------------------------------------------------
# Version history + diff
# ---------------------------------------------------------------------------
class TestVersions:
    def test_save_version_snapshots(self):
        pf.create_portfolio("P1")
        pf.open_position("p1", "MU", "BUY", 100, 90.0, thesis_at_entry=_entry())
        rec = pf.save_version("p1", "Initial build")
        assert len(rec["versions"]) == 1
        assert rec["versions"][0]["label"] == "Initial build"
        assert len(rec["versions"][0]["snapshot"]["positions"]) == 1

    def test_diff_added_and_closed(self):
        pf.create_portfolio("P1")
        r1 = pf.open_position("p1", "MU", "BUY", 100, 90.0,
                              thesis_at_entry=_entry())
        pf.save_version("p1", "v1")
        snap = pf.read_portfolio("p1")["versions"][0]["snapshot"]["positions"]
        # Add a new position and close the first.
        pid0 = r1["positions"][0]["id"]
        pf.open_position("p1", "NVDA", "BUY", 50, 800.0,
                         thesis_at_entry=_entry())
        pf.close_position("p1", pid0, exit_price=110.0)
        cur = pf.read_portfolio("p1")["positions"]
        diff = pf.diff_versions(snap, cur)
        assert any(p["ticker"] == "NVDA" for p in diff["added"])
        assert any(p["ticker"] == "MU" for p in diff["closed"])


# ---------------------------------------------------------------------------
# Portfolio summary + compare
# ---------------------------------------------------------------------------
class TestSummaryCompare:
    def test_summary_winrate_and_exposure(self):
        pf.create_portfolio("P1")
        r = pf.open_position("p1", "MU", "BUY", 100, 100.0,
                             thesis_at_entry=_entry(crest="Early"),
                             conviction=8)
        pid = r["positions"][0]["id"]
        pf.close_position("p1", pid, exit_price=130.0)  # +30% win
        pf.open_position("p1", "NVDA", "BUY", 10, 800.0,
                         thesis_at_entry=_entry(crest="Late"), conviction=4)
        rec = pf.read_portfolio("p1")
        s = pf.portfolio_summary(rec, {"MU": 130.0, "NVDA": 900.0})
        assert s["closed_n"] == 1
        assert s["open_n"] == 1
        assert s["win_rate"] == 1.0
        assert s["conviction_weighted_return"] is not None
        assert pf.top_exposure(s, "Crest") in ("Early", "Late")

    def test_summary_handles_missing_prices(self):
        pf.create_portfolio("P1")
        pf.open_position("p1", "MU", "BUY", 100, 100.0, thesis_at_entry=_entry())
        rec = pf.read_portfolio("p1")
        s = pf.portfolio_summary(rec, {})  # no prices
        assert s["open_n"] == 1
        # Nothing crashes; totals are numeric.
        assert isinstance(s["total_pnl"], float)

    def test_missing_price_pnl_is_zero(self):
        """With no live price, open position MV = cost; P&L = $0 (not distorted)."""
        pf.create_portfolio("P1")
        pf.open_position("p1", "MU", "BUY", 100, 100.0, thesis_at_entry=_entry())
        rec = pf.read_portfolio("p1")
        s = pf.portfolio_summary(rec, {})  # no prices
        assert s["total_pnl"] == pytest.approx(0.0)
        assert s["total_value"] == pytest.approx(pf.DEFAULT_CASH)

    # --- Worked example from the spec ---------------------------------------
    def test_worked_example_at_entry(self):
        """100k start. BUY 100 BABA @130.82 then BUY 100 BTBT @1.98.
        At entry (prices unchanged) total value = 100k, P&L = $0.
        cash + positions must reconcile; cash is NOT 100k untouched."""
        pf.create_portfolio("WE", cash_start=100_000.0)
        pf.open_position("we", "BABA", "BUY", 100, 130.82, thesis_at_entry=_entry())
        pf.open_position("we", "BTBT", "BUY", 100,   1.98, thesis_at_entry=_entry())
        rec = pf.read_portfolio("we")
        s = pf.portfolio_summary(rec, {"BABA": 130.82, "BTBT": 1.98})

        assert s["cash_remaining"] == pytest.approx(86_720.0,   rel=1e-4)
        assert s["invested_cost"]  == pytest.approx(13_280.0,   rel=1e-4)
        assert s["positions_market_value"] == pytest.approx(13_280.0, rel=1e-4)
        assert s["total_value"]    == pytest.approx(100_000.0,  rel=1e-4)
        assert s["total_pnl"]      == pytest.approx(0.0,        abs=0.01)
        # The total is cash + holdings, NOT just untouched starting cash.
        assert s["cash_remaining"] < 100_000.0

    def test_worked_example_price_move(self):
        """Same setup. BABA rises 130.82 → 140. Total value should be 100,918."""
        pf.create_portfolio("WE", cash_start=100_000.0)
        pf.open_position("we", "BABA", "BUY", 100, 130.82, thesis_at_entry=_entry())
        pf.open_position("we", "BTBT", "BUY", 100,   1.98, thesis_at_entry=_entry())
        rec = pf.read_portfolio("we")
        # BABA up to 140, BTBT unchanged.
        s = pf.portfolio_summary(rec, {"BABA": 140.0, "BTBT": 1.98})

        baba_mv = 100 * 140.0      # 14,000
        btbt_mv = 100 * 1.98       # 198
        expected_mv   = baba_mv + btbt_mv          # 14,198
        expected_cash = 100_000.0 - 13_280.0       # 86,720
        expected_total = expected_cash + expected_mv  # 100,918
        expected_pnl   = 100 * (140.0 - 130.82)   # 918

        assert s["positions_market_value"] == pytest.approx(expected_mv,    rel=1e-4)
        assert s["cash_remaining"]         == pytest.approx(expected_cash,  rel=1e-4)
        assert s["total_value"]            == pytest.approx(expected_total, rel=1e-4)
        assert s["total_pnl"]              == pytest.approx(expected_pnl,   rel=1e-3)

    def test_closed_position_credits_cash(self):
        """Close a position: proceeds credited to cash; realized P&L included."""
        pf.create_portfolio("P1", cash_start=100_000.0)
        r = pf.open_position("p1", "MU", "BUY", 100, 100.0, thesis_at_entry=_entry())
        pid = r["positions"][0]["id"]
        pf.close_position("p1", pid, exit_price=120.0)
        rec = pf.read_portfolio("p1")
        s = pf.portfolio_summary(rec, {})

        assert s["cash_remaining"]  == pytest.approx(102_000.0)  # 100k - 10k + 12k
        assert s["invested_cost"]   == pytest.approx(0.0)         # nothing open
        assert s["total_value"]     == pytest.approx(102_000.0)
        assert s["total_pnl"]       == pytest.approx(2_000.0)
        assert s["win_rate"]        == pytest.approx(1.0)

    def test_over_allocated_flag(self):
        """Paper portfolio: allow but flag when cost exceeds starting cash."""
        pf.create_portfolio("P1", cash_start=1_000.0)
        pf.open_position("p1", "MU", "BUY", 100, 100.0, thesis_at_entry=_entry())
        rec = pf.read_portfolio("p1")
        s = pf.portfolio_summary(rec, {"MU": 100.0})
        assert s["over_allocated"] is True
        assert s["cash_remaining"] < 0

    def test_compare_two_portfolios(self):
        pf.create_portfolio("Value")
        pf.create_portfolio("Momentum")
        v = pf.open_position("value", "MU", "BUY", 10, 100.0,
                             thesis_at_entry=_entry(), conviction=7)
        pf.close_position("value", v["positions"][0]["id"], exit_price=120.0)
        m = pf.open_position("momentum", "NVDA", "BUY", 10, 800.0,
                             thesis_at_entry=_entry(), conviction=5)
        pf.close_position("momentum", m["positions"][0]["id"], exit_price=760.0)
        rows = pf.compare_portfolios(
            [pf.read_portfolio("value"), pf.read_portfolio("momentum")], {})
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert names == {"Value", "Momentum"}
        # Factual only — no verdict key.
        for r in rows:
            assert "best" not in r
