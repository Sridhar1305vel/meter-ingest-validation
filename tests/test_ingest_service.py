"""Ingestion and use-case tests against the in-memory repository (no web server, no DB)."""
from datetime import date

from app.config import Config
from app.ingest import ingest_batch
from app.models import CONFLICTING_DUPLICATE, DUPLICATE, MISSING
from app.repository import InMemoryRepository
from app.service import analyse_day, reconcile_day
from app.timeutil import local_day_bounds
from tests.helpers import BERLIN, as_items, day_readings

CFG = Config()


def _ingest(repo, rows):
    return ingest_batch(repo, as_items(rows), CFG)


def test_accepts_clean_day_and_is_idempotent():
    repo = InMemoryRepository()
    rows = day_readings("M1", date(2026, 3, 28))
    first = _ingest(repo, rows)
    assert (first.accepted, first.duplicates, first.rejected_total) == (97, 0, 0)
    again = _ingest(repo, rows)
    assert (again.accepted, again.duplicates, again.conflicts) == (0, 97, 0)
    assert len(repo.readings) == 97


def test_conflicting_duplicate_keeps_stored_value():
    repo = InMemoryRepository()
    rows = day_readings("M1", date(2026, 3, 28))
    _ingest(repo, rows)
    changed = dict(rows[5], register_kwh=rows[5]["register_kwh"] + 0.5)
    res = _ingest(repo, [changed])
    assert res.conflicts == 1 and res.accepted == 0
    assert [a.kind for a in repo.anomalies][-1] == CONFLICTING_DUPLICATE
    stored = [v for (m, t), v in repo.readings.items() if abs(v - rows[5]["register_kwh"]) < 1e-9]
    assert stored


def test_duplicate_inside_one_batch():
    repo = InMemoryRepository()
    rows = day_readings("M1", date(2026, 3, 28))[:3]
    res = _ingest(repo, rows + [rows[1]])
    assert res.accepted == 3 and res.duplicates == 1
    assert [a.kind for a in repo.anomalies] == [DUPLICATE]


def test_bad_rows_rejected_individually():
    repo = InMemoryRepository()
    rows = day_readings("M1", date(2026, 3, 28))[:3]
    bad = [{"meter_id": "M1", "ts": "2026-03-28T10:00:00", "register_kwh": 1.0},
           {"meter_id": "M1", "ts": "2026-03-28T10:05:00+01:00", "register_kwh": 1.0}]
    res = _ingest(repo, rows + bad)
    assert res.accepted == 3 and res.rejected_total == 2
    assert [r["reason"] for r in res.rejected] == ["naive_timestamp", "misaligned_interval"]
    assert [r["index"] for r in res.rejected] == [3, 4]


def test_dst_days_report_no_false_gaps():
    repo = InMemoryRepository()
    for day, n in [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100)]:
        _ingest(repo, day_readings("M1", day))
        out = analyse_day(repo, CFG, day)
        assert out["expected_intervals"] == n
        assert out["anomalies"] == [], out["anomalies"]


def test_meter_that_stops_reporting_is_found():
    repo = InMemoryRepository()
    day = date(2026, 3, 28)
    _ingest(repo, day_readings("M1", day) + day_readings("M2", date(2026, 3, 27)))
    out = analyse_day(repo, CFG, day)
    # M2's last reading (local midnight) is shared with this day's window, so 96 of 97 are missing
    assert [(a["meter_id"], a["kind"], a["count"]) for a in out["anomalies"]] == [("M2", MISSING, 96)]


def test_unknown_meter_raises_lookup_error():
    try:
        analyse_day(InMemoryRepository(), CFG, date(2026, 3, 28), meter_id="nope")
    except LookupError:
        return
    raise AssertionError("expected LookupError")


def _load_grid(repo, day, per_interval):
    start, end = local_day_bounds(day, BERLIN)
    from app.timeutil import slot_starts
    repo.upsert_grid("DE", [(s, per_interval) for s in slot_starts(day, BERLIN)])


def test_reconciliation_end_to_end():
    repo = InMemoryRepository()
    day = date(2026, 10, 25)  # the 25-hour day
    _ingest(repo, day_readings("M1", day) + day_readings("M2", day))
    _load_grid(repo, day, 4.0)  # two meters x 0.1 kWh = 0.2 kWh = 5 % of 4.0
    out = reconcile_day(repo, CFG, day, scale=0.05)
    assert out["expected_intervals"] == 100 and out["compared_intervals"] == 100
    assert out["out_of_tolerance_intervals"] == 0 and out["diff_pct"] == 0.0

    wrong = reconcile_day(repo, CFG, day, scale=0.04)  # metered share is 25 % above this expectation
    assert wrong["out_of_tolerance_intervals"] == 100


def test_reconciliation_excludes_intervals_with_a_silent_meter():
    repo = InMemoryRepository()
    day = date(2026, 3, 28)
    rows2 = day_readings("M2", day)
    del rows2[40]
    _ingest(repo, day_readings("M1", day) + rows2)
    _load_grid(repo, day, 4.0)
    out = reconcile_day(repo, CFG, day, scale=0.05)
    assert out["incomplete_intervals"] == 2 and out["compared_intervals"] == 94
    assert out["out_of_tolerance_intervals"] == 0
