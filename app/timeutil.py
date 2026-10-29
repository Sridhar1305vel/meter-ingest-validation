"""Time handling.

Everything is stored and compared in UTC. Local time (Europe/Berlin) is used only to decide
what "a day" is: on DST-change days a local day has 92 or 100 quarter-hour intervals, not 96.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import RowError

UTC = timezone.utc
INTERVAL = timedelta(minutes=15)


def parse_ts(raw, tz: ZoneInfo) -> datetime:
    """Parse an ISO-8601 timestamp that carries an explicit UTC offset and return it in UTC.

    Naive timestamps are rejected: on the 25-hour day 02:00-02:59 local happens twice and a
    naive value cannot say which one it is. A non-UTC offset must also match the grid timezone
    at that instant, which catches clients that send a fixed +01:00 all year.
    """
    if not isinstance(raw, str):
        raise RowError("bad_timestamp", "timestamp must be a string")
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise RowError("bad_timestamp", f"cannot parse {raw!r}") from None
    offset = dt.utcoffset()
    if dt.tzinfo is None or offset is None:
        raise RowError("naive_timestamp", "an explicit UTC offset is required")
    if offset != timedelta(0) and offset != dt.astimezone(tz).utcoffset():
        raise RowError(
            "offset_mismatch",
            f"offset {raw[-6:]} is not the {tz.key} offset at that instant",
        )
    return dt.astimezone(UTC)


def is_aligned(ts_utc: datetime) -> bool:
    return ts_utc.minute % 15 == 0 and ts_utc.second == 0 and ts_utc.microsecond == 0


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day, as UTC instants."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def slot_starts(day: date, tz: ZoneInfo) -> list[datetime]:
    """Start of every 15-minute interval in the local day: 96, or 92 / 100 on DST days."""
    start, end = local_day_bounds(day, tz)
    return [start + i * INTERVAL for i in range(int((end - start) / INTERVAL))]


def expected_reading_times(start: datetime, end: datetime) -> list[datetime]:
    """Reading instants needed to cover [start, end): both boundaries inclusive."""
    n = int((end - start) / INTERVAL)
    return [start + i * INTERVAL for i in range(n + 1)]


def localize_sequence(naive_times: list[datetime], tz: ZoneInfo) -> list[datetime]:
    """Turn a chronologically ordered list of naive local times into UTC.

    Used for exports that give local time without an offset (e.g. a CSV download). On the
    fall-back day the repeated hour is resolved by order: if the first interpretation would
    go backwards in time, the second occurrence (fold=1) is used.
    """
    out: list[datetime] = []
    prev: datetime | None = None
    for naive in naive_times:
        utc = naive.replace(tzinfo=tz).astimezone(UTC)
        if prev is not None and utc <= prev:
            utc = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
        out.append(utc)
        prev = utc
    return out
