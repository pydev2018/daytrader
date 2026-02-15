"""
Tests for grid/orders.py — rung creation, comment parsing, fill toggling.
"""

import pytest

from grid.orders import (
    build_rungs,
    make_comment,
    parse_comment,
    update_rung_on_fill,
    desired_orders,
)
from grid.state import GridRung


class TestBuildRungs:
    def test_correct_count(self):
        """Should create 2 * levels rungs."""
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 6)
        assert len(rungs) == 12

    def test_buy_side_below(self):
        """Negative indices should be BUY entries below center."""
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 3)
        buy_rungs = [r for r in rungs if r.entry_side == "BUY"]
        assert len(buy_rungs) == 3
        for r in buy_rungs:
            assert r.level_index < 0
            assert r.entry_price < 1.10000

    def test_sell_side_above(self):
        """Positive indices should be SELL entries above center."""
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 3)
        sell_rungs = [r for r in rungs if r.entry_side == "SELL"]
        assert len(sell_rungs) == 3
        for r in sell_rungs:
            assert r.level_index > 0
            assert r.entry_price > 1.10000

    def test_prices_are_evenly_spaced(self):
        spacing = 0.00010
        rungs = build_rungs("EURUSD", 1.10000, spacing, 3)
        for r in rungs:
            expected = 1.10000 + r.level_index * spacing
            assert abs(r.entry_price - expected) < 1e-10


class TestCommentParsing:
    def test_roundtrip_entry(self):
        comment = make_comment("abc123", "L-1", "ENTRY")
        assert comment == "gabc123:L-1:E"
        parsed = parse_comment(comment)
        assert parsed == ("abc123", "L-1", "ENTRY")

    def test_roundtrip_exit(self):
        comment = make_comment("abc123", "L1", "EXIT")
        parsed = parse_comment(comment)
        assert parsed == ("abc123", "L1", "EXIT")

    def test_invalid_comment(self):
        assert parse_comment("random_text") is None
        assert parse_comment("") is None
        assert parse_comment("no_prefix:a:b") is None

    def test_too_few_parts(self):
        assert parse_comment("gabc:L1") is None


class TestUpdateRungOnFill:
    def test_entry_to_exit(self):
        rung = GridRung(
            rung_id="L-1", level_index=-1, entry_side="BUY",
            state="ENTRY", entry_price=1.09990, size=0.01,
        )
        update_rung_on_fill(rung, 1.09990, 0.00010, "2025-01-01T00:00:00")
        assert rung.state == "EXIT"
        assert rung.fill_price == 1.09990
        assert rung.exit_price == 1.09990 + 0.00010

    def test_exit_to_entry(self):
        rung = GridRung(
            rung_id="L-1", level_index=-1, entry_side="BUY",
            state="EXIT", entry_price=1.09990, size=0.01,
            fill_price=1.09990, exit_price=1.10000,
        )
        update_rung_on_fill(rung, 1.10000, 0.00010, "2025-01-01T00:00:00")
        assert rung.state == "ENTRY"
        assert rung.fill_price is None
        assert rung.exit_price is None

    def test_sell_entry_exit_direction(self):
        rung = GridRung(
            rung_id="L1", level_index=1, entry_side="SELL",
            state="ENTRY", entry_price=1.10010, size=0.01,
        )
        update_rung_on_fill(rung, 1.10010, 0.00010, "2025-01-01T00:00:00")
        assert rung.state == "EXIT"
        assert rung.exit_price == 1.10010 - 0.00010


class TestDesiredOrders:
    def test_entry_orders(self):
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 2)
        sizes = {r.rung_id: 0.01 for r in rungs}
        orders = desired_orders("EURUSD", "abc", rungs, sizes, allow_entries=True)
        assert len(orders) == 4  # 2 buy + 2 sell

    def test_no_entries_when_disabled(self):
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 2)
        sizes = {r.rung_id: 0.01 for r in rungs}
        orders = desired_orders("EURUSD", "abc", rungs, sizes, allow_entries=False)
        assert len(orders) == 0  # All are ENTRY, none pass

    def test_exit_orders_pass_through(self):
        """EXIT rungs should always generate orders regardless of allow_entries."""
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 1)
        # Fill the first rung
        rung = rungs[0]
        update_rung_on_fill(rung, rung.entry_price, 0.00010, "2025-01-01T00:00:00")
        rung.size = 0.01

        sizes = {r.rung_id: 0.01 for r in rungs}
        orders = desired_orders("EURUSD", "abc", rungs, sizes, allow_entries=False)
        # Should have 1 exit order (from the filled rung)
        assert len(orders) == 1
        assert orders[0].state == "EXIT"

    def test_zero_size_skipped(self):
        rungs = build_rungs("EURUSD", 1.10000, 0.00010, 2)
        sizes = {r.rung_id: 0.0 for r in rungs}  # All zero
        orders = desired_orders("EURUSD", "abc", rungs, sizes, allow_entries=True)
        assert len(orders) == 0
