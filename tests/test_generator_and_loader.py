"""End-to-end check without a web server: generate data with known faults, ingest it, and
confirm every injected fault is found and nothing else is flagged (DST days included)."""
import csv
import os
import tempfile
from datetime import date

from app.config import Config
from app.ingest import ingest_batch
from app.models import CONFLICTING_DUPLICATE, DUPLICATE, IMPLAUSIBLE, MISSING, NEGATIVE, ROLLOVER
from app.repository import InMemoryRepository
from app.service import reconcile_day
from app.timeutil import UTC, parse_ts, slot_starts
from app.validation import analyse_series
from scripts.generate_synthetic import build_dataset
from scripts.load_smard import load_grid_csv
from tests.helpers import BERLIN, as_items

CFG = Config()


def _ingest_all(ds):
    repo = InMemoryRepository()
    rows, total = ds["readings"], None
    dups = conflicts = rejected = 0
    for i in range(0, len(rows), 5000):
        res = ingest_batch(repo, as_items(rows[i:i + 5000]), CFG)
        dups += res.duplicates
        conflicts += res.conflicts
        rejected += res.rejected_total
    return repo, dups, conflicts, rejected


def test_every_injected_fault_is_detected_and_nothing_else():
    ds = build_dataset(meters=10, start=date(2026, 3, 28), days=3, seed=7)  # spans spring DST change
    repo, dups, conflicts, rejected = _ingest_all(ds)
    assert rejected == 0
    assert (dups, conflicts) == (5, 3)

    by_type = {f["type"]: f["meter_id"] for f in ds["faults"]}
    all_readings = repo.readings_in_window(
        parse_ts("2026-03-27T00:00:00Z", BERLIN), parse_ts("2026-04-05T00:00:00Z", BERLIN))
    for m in ds["meters"]:
        kinds = [(a.kind, a.count) for a in analyse_series(m, all_readings[m], CFG).anomalies]
        if m == by_type["missing_run"]:
            assert kinds == [(MISSING, 12)]
        elif m == by_type["missing_single"]:
            assert kinds == [(MISSING, 1)]
        elif m == by_type["negative_dip"]:
            assert kinds == [(NEGATIVE, 1)]
        elif m == by_type["step_jump"]:
            assert kinds == [(IMPLAUSIBLE, 1)]
        elif m == by_type["rollover"]:
            assert kinds == [(ROLLOVER, 1)]
        else:
            assert kinds == [], (m, kinds)  # clean meters, including across the DST change


def test_synthetic_grid_reconciles_within_tolerance_on_clean_dst_day():
    ds = build_dataset(meters=10, start=date(2026, 3, 28), days=3, seed=7, inject_faults=False)
    repo, *_ = _ingest_all(ds)
    repo.upsert_grid("DE", [(parse_ts(p["ts"], BERLIN), p["kwh"]) for p in ds["grid"]])
    out = reconcile_day(repo, CFG, date(2026, 3, 29), scale=ds["coverage_scale"], tolerance_pct=5)
    assert out["expected_intervals"] == 92 and out["compared_intervals"] == 92
    assert out["out_of_tolerance_intervals"] == 0
    assert -4.0 < out["diff_pct"] < -2.0  # the simulated 3 % unmetered loss shows up as ~ -2.9 %


def test_faults_show_up_in_reconciliation_as_incomplete_intervals():
    ds = build_dataset(meters=10, start=date(2026, 3, 28), days=3, seed=7)
    repo, *_ = _ingest_all(ds)
    repo.upsert_grid("DE", [(parse_ts(p["ts"], BERLIN), p["kwh"]) for p in ds["grid"]])
    incomplete = sum(reconcile_day(repo, CFG, date(2026, 3, 28 + d), scale=ds["coverage_scale"])["incomplete_intervals"]
                     for d in range(3))
    assert incomplete > 0


def test_grid_loader_resolves_the_25_hour_day():
    slots = slot_starts(date(2026, 10, 25), BERLIN)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "smard.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Start date", "End date", "Grid load [MWh]"])
            for i, s in enumerate(slots):
                local = s.astimezone(BERLIN)
                value = "-" if i == 9 else "12.345,5"  # a missing value must not break the fold logic
                w.writerow([local.strftime("%d.%m.%Y %H:%M"), "", value])
        rows = load_grid_csv(path)
    assert len(rows) == 99  # 100 intervals minus the one with "-"
    assert [t for t, _ in rows] == slots[:9] + slots[10:]
    assert rows[0][1] == 12_345_500.0  # MWh -> kWh
