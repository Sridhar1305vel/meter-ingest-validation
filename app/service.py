"""Use-cases behind the API. Framework-free so they can be tested without a web server."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from .config import Config
from .reconcile import reconcile
from .timeutil import local_day_bounds, slot_starts
from .validation import analyse_series


def _anomaly_dict(a, cfg: Config) -> dict:
    return {
        "kind": a.kind,
        "severity": a.severity,
        "meter_id": a.meter_id,
        "ts": a.ts.astimezone(cfg.tz).isoformat() if a.ts else None,
        "count": a.count,
        "detail": a.detail,
    }


def analyse_day(repo, cfg: Config, day: date, meter_id: str | None = None, kind: str | None = None) -> dict:
    known = repo.list_meter_ids()
    if meter_id is not None and meter_id not in known:
        raise LookupError(f"unknown meter {meter_id!r}")
    start, end = local_day_bounds(day, cfg.tz)
    meters = [meter_id] if meter_id else known
    data = repo.readings_in_window(start, end, meter_id)

    anomalies = []
    for m in meters:
        anomalies += analyse_series(m, data.get(m, []), cfg, window=(start, end)).anomalies
    anomalies += repo.stored_anomalies(meter_id, start, end)
    if kind:
        anomalies = [a for a in anomalies if a.kind == kind]
    anomalies.sort(key=lambda a: (a.ts or start, a.meter_id, a.kind))
    return {
        "date": day.isoformat(),
        "timezone": cfg.tz.key,
        "expected_intervals": len(slot_starts(day, cfg.tz)),  # 92 / 96 / 100
        "meters_checked": len(meters),
        "summary": dict(Counter(a.kind for a in anomalies)),
        "anomalies": [_anomaly_dict(a, cfg) for a in anomalies],
    }


def reconcile_day(repo, cfg: Config, day: date, area: str = "DE",
                  scale: float = 1.0, tolerance_pct: float = 5.0) -> dict:
    start, end = local_day_bounds(day, cfg.tz)
    meters = repo.list_meter_ids()
    data = repo.readings_in_window(start, end)
    metered: dict = defaultdict(float)
    reporting: dict = defaultdict(int)
    for m in meters:
        res = analyse_series(m, data.get(m, []), cfg, window=(start, end))
        for t, kwh in res.consumption.items():
            metered[t] += kwh
            reporting[t] += 1
    grid = repo.get_grid(area, start, end)
    out = reconcile(slot_starts(day, cfg.tz), metered, reporting, len(meters), grid, scale, tolerance_pct)
    out["date"] = day.isoformat()
    out["area"] = area
    out["meters"] = len(meters)
    for row in out["flagged"]:
        row["start"] = row["start"].astimezone(cfg.tz).isoformat()
    return out
