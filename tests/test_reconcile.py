from datetime import date

from app.reconcile import reconcile
from app.timeutil import slot_starts
from tests.helpers import BERLIN

SLOTS = slot_starts(date(2026, 3, 28), BERLIN)


def _series(v):
    return {s: v for s in SLOTS}


def test_perfect_match():
    out = reconcile(SLOTS, _series(0.2), {s: 2 for s in SLOTS}, 2, _series(4.0), scale=0.05)
    assert out["compared_intervals"] == 96 and out["out_of_tolerance_intervals"] == 0
    assert out["diff_pct"] == 0.0


def test_out_of_tolerance_flagged():
    metered = _series(0.2)
    metered[SLOTS[10]] = 0.3  # +50 %
    out = reconcile(SLOTS, metered, {s: 2 for s in SLOTS}, 2, _series(4.0), scale=0.05, tolerance_pct=5)
    assert out["out_of_tolerance_intervals"] == 1
    assert out["flagged"][0]["start"] == SLOTS[10] and out["flagged"][0]["diff_pct"] == 50.0


def test_incomplete_intervals_are_counted_not_compared():
    reporting = {s: 2 for s in SLOTS}
    reporting[SLOTS[3]] = 1
    out = reconcile(SLOTS, _series(0.2), reporting, 2, _series(4.0), scale=0.05)
    assert out["incomplete_intervals"] == 1 and out["compared_intervals"] == 95


def test_missing_grid_values_are_counted():
    grid = _series(4.0)
    del grid[SLOTS[0]]
    out = reconcile(SLOTS, _series(0.2), {s: 2 for s in SLOTS}, 2, grid, scale=0.05)
    assert out["no_grid_value_intervals"] == 1 and out["compared_intervals"] == 95


def test_no_meters_means_nothing_compared():
    out = reconcile(SLOTS, {}, {}, 0, _series(4.0))
    assert out["compared_intervals"] == 0 and out["diff_pct"] is None
