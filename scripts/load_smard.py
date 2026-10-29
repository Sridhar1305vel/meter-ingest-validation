"""Load grid-level totals from a SMARD (Bundesnetzagentur) CSV export.

The download format can change, so every assumption is a flag. Check the header of YOUR file and
adjust --time-col / --value-col / --time-format / --decimal / --thousands if needed.

Defaults assume: semicolon-separated, first column = start of interval as "dd.mm.yyyy HH:MM" in
local German time WITHOUT an offset, third column = value in MWh with decimal comma and "."
as thousands separator, "-" for missing values. Missing-value rows are kept during time
conversion on purpose: dropping them first could mis-assign the repeated hour on the 25-hour day.

Examples:
  python -m scripts.load_smard --file smard.csv --out data/grid.json
  python -m scripts.load_smard --file smard.csv --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.timeutil import UTC, localize_sequence


def parse_number(text: str, decimal: str, thousands: str) -> float | None:
    s = text.strip()
    if s in ("", "-", "n/a", "N/A"):
        return None
    if thousands:
        s = s.replace(thousands, "")
    return float(s.replace(decimal, "."))


def load_grid_csv(path, time_col=0, value_col=2, time_format="%d.%m.%Y %H:%M", decimal=",",
                  thousands=".", tz="Europe/Berlin", already_utc=False, delimiter=";",
                  unit_to_kwh=1000.0) -> list[tuple[datetime, float]]:
    zone = ZoneInfo(tz)
    naive, values = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for lineno, row in enumerate(csv.reader(f, delimiter=delimiter), start=1):
            if not row or all(not c.strip() for c in row):
                continue
            try:
                naive.append(datetime.strptime(row[time_col].strip(), time_format))
            except ValueError:
                if lineno == 1:
                    continue  # header
                raise ValueError(f"line {lineno}: cannot parse time {row[time_col]!r} with {time_format!r}")
            values.append(parse_number(row[value_col], decimal, thousands))
    if already_utc:
        stamps = [n.replace(tzinfo=UTC) for n in naive]
    else:
        stamps = localize_sequence(naive, zone)
    return [(t, v * unit_to_kwh) for t, v in zip(stamps, values) if v is not None]


def _put(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--time-col", type=int, default=0)
    ap.add_argument("--value-col", type=int, default=2)
    ap.add_argument("--time-format", default="%d.%m.%Y %H:%M")
    ap.add_argument("--decimal", default=",")
    ap.add_argument("--thousands", default=".")
    ap.add_argument("--delimiter", default=";")
    ap.add_argument("--tz", default="Europe/Berlin")
    ap.add_argument("--utc", action="store_true", help="times in the file are already UTC")
    ap.add_argument("--area", default="DE")
    ap.add_argument("--out", help="write [[iso_utc, kwh], ...] for generate_synthetic --grid-json")
    ap.add_argument("--api", help="base URL of a running API")
    a = ap.parse_args()

    rows = load_grid_csv(a.file, a.time_col, a.value_col, a.time_format, a.decimal, a.thousands,
                         a.tz, a.utc, a.delimiter)
    if not rows:
        raise SystemExit("no rows parsed; check the column and format flags")
    print(f"parsed {len(rows)} intervals, {rows[0][0].isoformat()} .. {rows[-1][0].isoformat()}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps([[t.isoformat(), v] for t, v in rows]))
    if a.api:
        for i in range(0, len(rows), 10_000):
            chunk = [{"ts": t.isoformat(), "kwh": v} for t, v in rows[i:i + 10_000]]
            print(_put(a.api.rstrip("/") + "/grid-totals", {"area": a.area, "points": chunk}))


if __name__ == "__main__":
    main()
