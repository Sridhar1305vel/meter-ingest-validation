"""Shared test data builders (stdlib only)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from app.timeutil import INTERVAL, local_day_bounds

BERLIN = ZoneInfo("Europe/Berlin")


def day_readings(meter_id, day, start_register=1000.0, per_interval=0.1, tz=BERLIN, register_max=100_000.0):
    """Cumulative readings at every 15-minute boundary from local midnight to the next local
    midnight, inclusive. Timestamps are local time WITH offset, as a real client would send."""
    start, end = local_day_bounds(day, tz)
    out, t, i = [], start, 0
    while t <= end:
        value = round(start_register + per_interval * i, 6) % register_max
        out.append({"meter_id": meter_id, "ts": t.astimezone(tz).isoformat(), "register_kwh": round(value, 6)})
        t += INTERVAL
        i += 1
    return out


def as_items(rows):
    return [(r["meter_id"], r["ts"], r["register_kwh"]) for r in rows]
