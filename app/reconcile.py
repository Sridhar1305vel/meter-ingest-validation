"""Reconcile the sum of metered consumption against a grid-level total, interval by interval."""
from __future__ import annotations

from datetime import datetime


def reconcile(
    slots: list[datetime],
    metered: dict[datetime, float],
    reporting: dict[datetime, int],
    meters_expected: int,
    grid: dict[datetime, float],
    scale: float = 1.0,
    tolerance_pct: float = 5.0,
) -> dict:
    """Compare per interval: metered sum vs grid_total * scale.

    `scale` is the share of the grid the metered fleet represents. An interval is only compared
    when every known meter reported it (otherwise the sum is understated by construction) and a
    grid value exists; the other intervals are counted, not silently dropped.
    """
    compared = incomplete = no_grid = out_of_tol = 0
    metered_total = grid_total = 0.0
    flagged: list[dict] = []
    for slot in slots:
        g = grid.get(slot)
        if g is None:
            no_grid += 1
            continue
        if reporting.get(slot, 0) < meters_expected or meters_expected == 0:
            incomplete += 1
            continue
        m = metered.get(slot, 0.0)
        expected = g * scale
        compared += 1
        metered_total += m
        grid_total += expected
        diff_pct = (m - expected) / expected * 100 if expected > 0 else None
        if diff_pct is None or abs(diff_pct) > tolerance_pct:
            out_of_tol += 1
            flagged.append({"start": slot, "metered_kwh": round(m, 3),
                            "grid_kwh_scaled": round(expected, 3),
                            "diff_pct": None if diff_pct is None else round(diff_pct, 2)})
    return {
        "expected_intervals": len(slots),
        "compared_intervals": compared,
        "incomplete_intervals": incomplete,
        "no_grid_value_intervals": no_grid,
        "out_of_tolerance_intervals": out_of_tol,
        "metered_kwh": round(metered_total, 3),
        "grid_kwh_scaled": round(grid_total, 3),
        "diff_pct": round((metered_total - grid_total) / grid_total * 100, 2) if grid_total > 0 else None,
        "tolerance_pct": tolerance_pct,
        "scale": scale,
        "flagged": flagged,
    }
