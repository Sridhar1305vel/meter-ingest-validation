from datetime import date

from app.config import Config
from app.models import IMPLAUSIBLE, MISSING, NEGATIVE, ROLLOVER, Reading, RowError
from app.timeutil import UTC, local_day_bounds, parse_ts
from app.validation import analyse_series, validate_row
from tests.helpers import BERLIN, day_readings

CFG = Config()


def _readings(rows):
    return [Reading(r["meter_id"], parse_ts(r["ts"], BERLIN), r["register_kwh"]) for r in rows]


def _code(*args):
    try:
        validate_row(*args, CFG)
    except RowError as e:
        return e.code
    return None


def _analyse(rows, day):
    return analyse_series("M1", _readings(rows), CFG, window=local_day_bounds(day, BERLIN))


def _kinds(res):
    return [a.kind for a in res.anomalies]


# --- row validation ---------------------------------------------------------
def test_valid_row():
    r = validate_row("M1", "2026-03-28T10:00:00+01:00", 12.5, CFG)
    assert r.meter_id == "M1" and r.ts.tzinfo == UTC and r.register_kwh == 12.5


def test_row_rejections():
    assert _code("", "2026-03-28T10:00:00+01:00", 1.0) == "bad_meter_id"
    assert _code("M1", "2026-03-28T10:07:00+01:00", 1.0) == "misaligned_interval"
    assert _code("M1", "2026-03-28T10:00:00", 1.0) == "naive_timestamp"
    assert _code("M1", "2026-03-28T10:00:00+01:00", -1.0) == "invalid_register"
    assert _code("M1", "2026-03-28T10:00:00+01:00", float("nan")) == "invalid_register"
    assert _code("M1", "2026-03-28T10:00:00+01:00", float("inf")) == "invalid_register"
    assert _code("M1", "2026-03-28T10:00:00+01:00", True) == "invalid_register"
    assert _code("M1", "2026-03-28T10:00:00+01:00", 100_000.0) == "register_above_max"


# --- series analysis --------------------------------------------------------
def test_clean_days_have_no_anomalies_and_right_interval_counts():
    for day, n in [(date(2026, 3, 28), 96), (date(2026, 3, 29), 92), (date(2026, 10, 25), 100)]:
        res = _analyse(day_readings("M1", day), day)
        assert res.anomalies == [], (day, res.anomalies)
        assert len(res.consumption) == n, (day, len(res.consumption))
        assert abs(sum(res.consumption.values()) - 0.1 * n) < 1e-3
        assert res.missing_readings == 0


def test_missing_run_is_grouped_and_excludes_touching_intervals():
    day = date(2026, 3, 28)
    rows = day_readings("M1", day)
    del rows[10:14]  # four consecutive readings
    res = _analyse(rows, day)
    missing = [a for a in res.anomalies if a.kind == MISSING]
    assert len(missing) == 1 and missing[0].count == 4 and missing[0].severity == "error"
    assert len(res.consumption) == 96 - 5  # the five intervals that touch a missing reading


def test_missing_whole_day_for_meter_with_no_data():
    day = date(2026, 10, 25)
    res = analyse_series("M1", [], CFG, window=local_day_bounds(day, BERLIN))
    assert _kinds(res) == [MISSING]
    assert res.anomalies[0].count == 101


def test_rollover_is_handled_not_flagged_as_negative():
    day = date(2026, 3, 28)
    res = _analyse(day_readings("M1", day, start_register=99_999.0), day)
    assert _kinds(res) == [ROLLOVER]
    assert len(res.consumption) == 96
    assert all(abs(v - 0.1) < 1e-3 for v in res.consumption.values())


def test_negative_consumption_flagged_and_neighbour_excluded():
    day = date(2026, 3, 28)
    rows = day_readings("M1", day)
    rows[20]["register_kwh"] -= 5.0  # one corrupted reading
    res = _analyse(rows, day)
    assert _kinds(res) == [NEGATIVE]
    assert len(res.consumption) == 96 - 2  # the interval into it and out of it


def test_implausible_jump_flagged():
    day = date(2026, 3, 28)
    rows = day_readings("M1", day)
    for r in rows[30:]:
        r["register_kwh"] += 80.0
    res = _analyse(rows, day)
    assert _kinds(res) == [IMPLAUSIBLE]
    assert len(res.consumption) == 95


def test_without_window_only_internal_gaps_reported():
    day = date(2026, 3, 28)
    rows = day_readings("M1", day)[5:50]
    assert analyse_series("M1", _readings(rows), CFG).anomalies == []
