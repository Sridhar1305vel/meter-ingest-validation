"""Pure validation logic: row checks and per-meter series analysis.

Conventions
-----------
* A reading is a cumulative register value (kWh) taken at an instant `ts`.
* Consumption between two readings exactly 15 minutes apart is attributed to the interval that
  starts at the earlier reading (the same labelling grid data uses: start of interval).
* Nothing is interpolated. An interval touching a missing or suspect reading is excluded and
  reported, never guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .config import Config
from .models import (IMPLAUSIBLE, MISSING, NEGATIVE, ROLLOVER, Anomaly, Reading, RowError)
from .timeutil import INTERVAL, expected_reading_times, is_aligned, parse_ts


def validate_row(meter_id, ts_raw, register_kwh, cfg: Config) -> Reading:
    if not isinstance(meter_id, str) or not meter_id.strip() or len(meter_id.strip()) > 64:
        raise RowError("bad_meter_id", "meter_id must be 1-64 characters")
    ts = parse_ts(ts_raw, cfg.tz)
    if not is_aligned(ts):
        raise RowError("misaligned_interval", "timestamp must fall on a 15-minute boundary")
    if (
        isinstance(register_kwh, bool)
        or not isinstance(register_kwh, (int, float))
        or not math.isfinite(register_kwh)
        or register_kwh < 0
    ):
        raise RowError("invalid_register", "register_kwh must be a finite number >= 0")
    if register_kwh >= cfg.register_max_kwh:
        raise RowError("register_above_max", f"register must be below {cfg.register_max_kwh:g}")
    return Reading(meter_id.strip(), ts, float(register_kwh))


@dataclass
class SeriesResult:
    meter_id: str
    consumption: dict[datetime, float] = field(default_factory=dict)  # keyed by interval start (UTC)
    anomalies: list[Anomaly] = field(default_factory=list)
    expected_readings: int = 0
    missing_readings: int = 0


def _local(ts: datetime, cfg: Config) -> str:
    return ts.astimezone(cfg.tz).isoformat()


def analyse_series(
    meter_id: str,
    readings: Iterable[Reading],
    cfg: Config,
    window: tuple[datetime, datetime] | None = None,
) -> SeriesResult:
    """Derive per-interval consumption and anomalies for one meter.

    `window` is [start, end) in UTC. When given, every reading instant from start to end
    inclusive is expected, so a meter with no data at all is reported as missing. Without it,
    only gaps between the first and last reading are reported.
    """
    by_ts = {r.ts: r for r in readings}
    ordered = sorted(by_ts)
    res = SeriesResult(meter_id)
    suspect_starts: list[datetime] = []

    for prev_ts, ts in zip(ordered, ordered[1:]):
        if ts - prev_ts != INTERVAL:
            continue  # a gap: reported below as missing, no consumption derived across it
        prev, cur = by_ts[prev_ts], by_ts[ts]
        delta = cur.register_kwh - prev.register_kwh
        if delta < 0:
            wrapped = delta + cfg.register_max_kwh
            if wrapped <= cfg.max_interval_kwh:
                res.consumption[prev_ts] = wrapped
                res.anomalies.append(Anomaly(
                    ROLLOVER, meter_id, ts, "info",
                    f"register wrapped {prev.register_kwh:.3f} -> {cur.register_kwh:.3f}; "
                    f"consumption {wrapped:.3f} kWh computed across the rollover"))
            else:
                res.anomalies.append(Anomaly(
                    NEGATIVE, meter_id, ts, "error",
                    f"register fell {prev.register_kwh:.3f} -> {cur.register_kwh:.3f} kWh with no "
                    f"plausible rollover; this interval and the next are excluded"))
                suspect_starts.append(ts)  # the following interval is distorted by the same reading
        elif delta > cfg.max_interval_kwh:
            res.anomalies.append(Anomaly(
                IMPLAUSIBLE, meter_id, ts, "warning",
                f"{delta:.3f} kWh in 15 min exceeds the {cfg.max_interval_kwh:g} kWh limit; "
                f"interval excluded"))
        else:
            res.consumption[prev_ts] = delta

    for start in suspect_starts:
        res.consumption.pop(start, None)

    if window is not None:
        expected = expected_reading_times(*window)
    elif ordered:
        expected = expected_reading_times(ordered[0], ordered[-1])
    else:
        expected = []
    res.expected_readings = len(expected)
    missing = [t for t in expected if t not in by_ts]
    res.missing_readings = len(missing)
    res.anomalies.extend(_group_missing(meter_id, missing, cfg))
    return res


def _group_missing(meter_id: str, missing: list[datetime], cfg: Config) -> list[Anomaly]:
    runs: list[list[datetime]] = []
    for t in missing:
        if runs and t - runs[-1][-1] == INTERVAL:
            runs[-1].append(t)
        else:
            runs.append([t])
    return [
        Anomaly(
            MISSING, meter_id, run[0], "error" if len(run) >= 4 else "warning",
            f"{len(run)} consecutive reading(s) missing from {_local(run[0], cfg)} "
            f"to {_local(run[-1], cfg)}",
            count=len(run))
        for run in runs
    ]
