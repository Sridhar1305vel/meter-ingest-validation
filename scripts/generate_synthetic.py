"""Generate SYNTHETIC smart-meter data with known, injected faults.

Everything this script produces is synthetic and is labelled as such. Two modes:

* default: synthetic load shape AND a synthetic grid total (the grid is derived from the meters
  plus a 3 % simulated unmetered loss, so this mode proves the pipeline mechanics only).
* --grid-json FILE: meter load follows the shape of real grid data (written by
  scripts/load_smard.py --out FILE). Meters are synthetic, the grid shape is real.

Faults are injected on distinct meters so each can be checked independently; the ground truth is
written to faults.json. Readings are cumulative register values, sent with local-time offsets
(+01:00 / +02:00) the way a real client would, so DST days are exercised.

Usage:
  python -m scripts.generate_synthetic --out data/synthetic            # write files
  python -m scripts.generate_synthetic --api http://localhost:8000     # send to a running API
"""
from __future__ import annotations

import argparse
import json
import math
import random
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
STEP = timedelta(minutes=15)
REGISTER_MAX = 100_000.0
BASE_KWH = 0.12  # average household use per 15 minutes
FAULT_ROLES = ["missing_run", "missing_single", "negative_dip", "step_jump", "rollover",
               "duplicates", "conflicts"]


def load_shape(local: datetime) -> float:
    h = local.hour + local.minute / 60
    shape = 0.55 + 0.30 * math.exp(-(((h - 7.5) / 2.0) ** 2)) + 0.55 * math.exp(-(((h - 19.0) / 2.5) ** 2))
    return shape * (0.92 if local.weekday() >= 5 else 1.0)


def build_dataset(meters=20, start=date(2026, 3, 28), days=3, seed=42, grid_load=None,
                  inject_faults=True, tz=BERLIN) -> dict:
    if inject_faults and meters < len(FAULT_ROLES):
        raise ValueError(f"need at least {len(FAULT_ROLES)} meters to inject all faults")
    rng = random.Random(seed)
    t0 = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    t_end = datetime.combine(start + timedelta(days=days), time.min, tzinfo=tz).astimezone(UTC)
    n_int = int((t_end - t0) / STEP)
    starts = [t0 + i * STEP for i in range(n_int)]  # interval starts
    ids = [f"SYN-{i:04d}" for i in range(1, meters + 1)]
    weights = {m: rng.uniform(0.5, 2.0) for m in ids}
    mean_w = sum(weights.values()) / meters

    if grid_load is not None:
        missing = [s for s in starts if s not in grid_load]
        if missing:
            raise ValueError(f"grid file lacks {len(missing)} intervals, first: {missing[0].isoformat()}")
        coverage = meters * BASE_KWH / (sum(grid_load[s] for s in starts) / n_int)
        base = lambda s: grid_load[s] * coverage  # kWh per interval for the whole fleet
        per_meter = lambda m, s: base(s) * weights[m] / mean_w / meters * rng.uniform(0.97, 1.03)
    else:
        coverage = 0.001
        per_meter = lambda m, s: BASE_KWH * load_shape(s.astimezone(tz)) * weights[m] * rng.uniform(0.95, 1.05)

    true = {m: [per_meter(m, s) for s in starts] for m in ids}
    totals = [sum(true[m][i] for m in ids) for i in range(n_int)]

    series: dict[str, list[list]] = {}
    roles = dict(zip(FAULT_ROLES, rng.sample(ids, len(FAULT_ROLES)))) if inject_faults else {}
    for m in ids:
        reg = REGISTER_MAX - 3.0 if roles.get("rollover") == m else rng.uniform(1000, 50000)
        rows = [[t0, round(reg % REGISTER_MAX, 3)]]
        for i in range(n_int):
            reg += true[m][i]
            rows.append([starts[i] + STEP, round(reg % REGISTER_MAX, 3)])
        series[m] = rows

    faults, extras = [], []
    if inject_faults:
        pos = lambda: rng.randrange(20, n_int - 30)
        m, p = roles["missing_run"], pos()
        faults.append({"type": "missing_run", "meter_id": m, "ts": series[m][p][0].isoformat(), "count": 12})
        del series[m][p:p + 12]
        m, p = roles["missing_single"], pos()
        faults.append({"type": "missing_single", "meter_id": m, "ts": series[m][p][0].isoformat(), "count": 1})
        del series[m][p]
        m, p = roles["negative_dip"], pos()
        series[m][p][1] = round(series[m][p][1] - 5.0, 3)
        faults.append({"type": "negative_dip", "meter_id": m, "ts": series[m][p][0].isoformat(), "count": 1})
        m, p = roles["step_jump"], pos()
        for row in series[m][p:]:
            row[1] = round(row[1] + 80.0, 3)
        faults.append({"type": "step_jump", "meter_id": m, "ts": series[m][p][0].isoformat(), "count": 1})
        faults.append({"type": "rollover", "meter_id": roles["rollover"], "ts": None, "count": 1,
                       "note": "register wraps within the first day; correct handling is not an error"})
        m = roles["duplicates"]
        for p in rng.sample(range(5, len(series[m]) - 5), 5):
            extras.append((m, series[m][p][0], series[m][p][1]))
        faults.append({"type": "duplicates", "meter_id": m, "ts": None, "count": 5})
        m = roles["conflicts"]
        for p in rng.sample(range(5, len(series[m]) - 5), 3):
            extras.append((m, series[m][p][0], round(series[m][p][1] + 0.5, 3)))
        faults.append({"type": "conflicts", "meter_id": m, "ts": None, "count": 3})

    def fmt(t):
        return t.astimezone(tz).isoformat()

    readings = [{"meter_id": m, "ts": fmt(t), "register_kwh": v} for m in ids for t, v in series[m]]
    readings.sort(key=lambda r: (datetime.fromisoformat(r["ts"]), r["meter_id"]))
    readings += [{"meter_id": m, "ts": fmt(t), "register_kwh": v} for m, t, v in extras]

    grid = None
    if grid_load is None:  # synthetic grid: fleet total / coverage, plus 3 % unmetered loss and noise
        grid = [{"ts": fmt(s), "kwh": round(totals[i] / coverage * 1.03 * rng.uniform(0.99, 1.01), 3)}
                for i, s in enumerate(starts)]
    return {"readings": readings, "grid": grid, "faults": faults, "coverage_scale": coverage,
            "meters": ids, "start": start.isoformat(), "days": days, "seed": seed,
            "label": "SYNTHETIC DATA"}


def _send(url, payload, method):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meters", type=int, default=20)
    ap.add_argument("--start", default="2026-03-28", help="first local day (default spans the spring DST change)")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--grid-json", help="grid file from load_smard.py --out; makes meter load follow real grid shape")
    ap.add_argument("--no-faults", action="store_true")
    ap.add_argument("--out", help="write readings_*.json, grid.json and faults.json here")
    ap.add_argument("--api", help="base URL of a running API, e.g. http://localhost:8000")
    ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()

    grid_load = None
    if a.grid_json:
        grid_load = {datetime.fromisoformat(ts).astimezone(UTC): kwh for ts, kwh in json.load(open(a.grid_json))}
    ds = build_dataset(a.meters, date.fromisoformat(a.start), a.days, a.seed, grid_load, not a.no_faults)
    print(f"{ds['label']}: {len(ds['readings'])} readings, {len(ds['meters'])} meters, "
          f"{len(ds['faults'])} fault types; reconciliation scale = {ds['coverage_scale']:.6g}")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(ds["readings"]), a.batch):
            (out / f"readings_{i // a.batch:03d}.json").write_text(
                json.dumps({"readings": ds["readings"][i:i + a.batch]}))
        if ds["grid"]:
            (out / "grid.json").write_text(json.dumps({"area": "DE", "points": ds["grid"]}))
        (out / "faults.json").write_text(json.dumps(
            {k: ds[k] for k in ("label", "faults", "coverage_scale", "start", "days", "seed")}, indent=2))
        print(f"wrote files to {out}/")
    if a.api:
        base = a.api.rstrip("/")
        for i in range(0, len(ds["readings"]), a.batch):
            r = _send(f"{base}/readings", {"readings": ds["readings"][i:i + a.batch]}, "POST")
            print({k: r[k] for k in ("accepted", "duplicates", "conflicts", "rejected_total")})
        if ds["grid"]:
            print(_send(f"{base}/grid-totals", {"area": "DE", "points": ds["grid"]}, "PUT"))


if __name__ == "__main__":
    main()
