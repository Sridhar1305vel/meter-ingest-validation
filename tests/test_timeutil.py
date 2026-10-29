from datetime import date, datetime

from app.models import RowError
from app.timeutil import UTC, localize_sequence, parse_ts, slot_starts
from tests.helpers import BERLIN, day_readings


def _code(fn, *args):
    try:
        fn(*args)
    except RowError as e:
        return e.code
    return None


def test_interval_count_per_local_day():
    assert len(slot_starts(date(2026, 3, 28), BERLIN)) == 96
    assert len(slot_starts(date(2026, 3, 29), BERLIN)) == 92   # spring forward: 23 hours
    assert len(slot_starts(date(2026, 10, 25), BERLIN)) == 100  # fall back: 25 hours


def test_naive_timestamp_rejected():
    assert _code(parse_ts, "2026-03-28T10:00:00", BERLIN) == "naive_timestamp"


def test_garbage_timestamp_rejected():
    assert _code(parse_ts, "not a time", BERLIN) == "bad_timestamp"
    assert _code(parse_ts, 12345, BERLIN) == "bad_timestamp"


def test_offset_converted_to_utc():
    assert parse_ts("2026-03-28T10:00:00+01:00", BERLIN) == datetime(2026, 3, 28, 9, 0, tzinfo=UTC)
    assert parse_ts("2026-07-01T10:00:00+02:00", BERLIN) == datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    assert parse_ts("2026-07-01T10:00:00Z", BERLIN) == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def test_fixed_winter_offset_in_summer_rejected():
    assert _code(parse_ts, "2026-07-01T10:00:00+01:00", BERLIN) == "offset_mismatch"


def test_repeated_hour_is_two_distinct_instants():
    first = parse_ts("2026-10-25T02:30:00+02:00", BERLIN)
    second = parse_ts("2026-10-25T02:30:00+01:00", BERLIN)
    assert second - first == (second - first).__class__(hours=1)


def test_day_readings_cover_dst_days():
    spring = day_readings("M", date(2026, 3, 29))
    fall = day_readings("M", date(2026, 10, 25))
    assert len(day_readings("M", date(2026, 3, 28))) == 97
    assert len(spring) == 93
    assert len(fall) == 101
    assert len({r["ts"] for r in fall}) == 101  # the repeated 02:xx hour does not collide
    assert any("T02:00:00+02:00" in r["ts"] for r in fall) and any("T02:00:00+01:00" in r["ts"] for r in fall)


def test_localize_sequence_resolves_repeated_hour_by_order():
    utc_slots = [s for s in slot_starts(date(2026, 10, 25), BERLIN)]
    naive = [s.astimezone(BERLIN).replace(tzinfo=None) for s in utc_slots]
    assert len(naive) != len(set(naive))  # naive local times repeat
    assert localize_sequence(naive, BERLIN) == utc_slots


def test_localize_sequence_spring_gap():
    utc_slots = slot_starts(date(2026, 3, 29), BERLIN)
    naive = [s.astimezone(BERLIN).replace(tzinfo=None) for s in utc_slots]
    assert localize_sequence(naive, BERLIN) == utc_slots
