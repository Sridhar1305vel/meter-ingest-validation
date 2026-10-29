"""Storage. Two implementations share one interface: in-memory (tests) and PostgreSQL."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .models import Anomaly, Reading
from .timeutil import UTC

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


class InMemoryRepository:
    def __init__(self):
        self.readings: dict[tuple[str, datetime], float] = {}
        self.anomalies: list[Anomaly] = []
        self.grid: dict[tuple[str, datetime], float] = {}

    def ping(self) -> bool:
        return True

    def reset(self) -> None:
        self.__init__()

    def existing_values(self, keys):
        return {k: self.readings[k] for k in keys if k in self.readings}

    def insert_readings(self, batch_id, readings):
        for r in readings:
            self.readings.setdefault((r.meter_id, r.ts), r.register_kwh)

    def list_meter_ids(self):
        return sorted({m for m, _ in self.readings})

    def readings_in_window(self, start, end, meter_id=None):
        out = defaultdict(list)
        for (m, t), v in sorted(self.readings.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if start <= t <= end and (meter_id is None or m == meter_id):
                out[m].append(Reading(m, t, v))
        return dict(out)

    def add_anomalies(self, batch_id, anomalies):
        self.anomalies.extend(anomalies)

    def stored_anomalies(self, meter_id, start, end):
        return [a for a in self.anomalies
                if a.ts is not None and start <= a.ts <= end
                and (meter_id is None or a.meter_id == meter_id)]

    def upsert_grid(self, area, rows):
        for ts, kwh in rows:
            self.grid[(area, ts)] = kwh

    def get_grid(self, area, start, end):
        return {t: v for (a, t), v in self.grid.items() if a == area and start <= t < end}


class PostgresRepository:
    def __init__(self, dsn: str):
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True)

    def close(self) -> None:
        self._pool.close()

    def init_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA_PATH.read_text())

    def reset(self) -> None:
        with self._pool.connection() as conn:
            conn.execute("TRUNCATE readings, ingest_anomalies, grid_totals")

    def ping(self) -> bool:
        with self._pool.connection() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def existing_values(self, keys):
        if not keys:
            return {}
        meters = [k[0] for k in keys]
        stamps = [k[1] for k in keys]
        sql = """
            SELECT r.meter_id, r.ts_utc, r.register_kwh
            FROM readings r
            JOIN unnest(%s::text[], %s::timestamptz[]) AS k(meter_id, ts_utc)
              ON r.meter_id = k.meter_id AND r.ts_utc = k.ts_utc
        """
        with self._pool.connection() as conn:
            rows = conn.execute(sql, (meters, stamps)).fetchall()
        return {(m, t.astimezone(UTC)): v for m, t, v in rows}

    def insert_readings(self, batch_id, readings):
        if not readings:
            return
        rows = [(r.meter_id, r.ts, r.register_kwh, batch_id) for r in readings]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO readings (meter_id, ts_utc, register_kwh, batch_id) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (meter_id, ts_utc) DO NOTHING", rows)

    def list_meter_ids(self):
        with self._pool.connection() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT meter_id FROM readings ORDER BY meter_id").fetchall()]

    def readings_in_window(self, start, end, meter_id=None):
        sql = "SELECT meter_id, ts_utc, register_kwh FROM readings WHERE ts_utc >= %s AND ts_utc <= %s"
        params = [start, end]
        if meter_id is not None:
            sql += " AND meter_id = %s"
            params.append(meter_id)
        sql += " ORDER BY meter_id, ts_utc"
        out = defaultdict(list)
        with self._pool.connection() as conn:
            for m, t, v in conn.execute(sql, params).fetchall():
                out[m].append(Reading(m, t.astimezone(UTC), v))
        return dict(out)

    def add_anomalies(self, batch_id, anomalies):
        if not anomalies:
            return
        rows = [(batch_id, a.kind, a.severity, a.meter_id, a.ts, a.detail) for a in anomalies]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ingest_anomalies (batch_id, kind, severity, meter_id, ts_utc, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s)", rows)

    def stored_anomalies(self, meter_id, start, end):
        sql = ("SELECT kind, severity, meter_id, ts_utc, detail FROM ingest_anomalies "
               "WHERE ts_utc >= %s AND ts_utc <= %s")
        params = [start, end]
        if meter_id is not None:
            sql += " AND meter_id = %s"
            params.append(meter_id)
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Anomaly(k, m, t.astimezone(UTC), s, d) for k, s, m, t, d in rows]

    def upsert_grid(self, area, rows):
        if not rows:
            return
        data = [(area, ts, kwh) for ts, kwh in rows]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO grid_totals (area, ts_utc, kwh) VALUES (%s, %s, %s) "
                "ON CONFLICT (area, ts_utc) DO UPDATE SET kwh = EXCLUDED.kwh", data)

    def get_grid(self, area, start, end):
        sql = "SELECT ts_utc, kwh FROM grid_totals WHERE area = %s AND ts_utc >= %s AND ts_utc < %s"
        with self._pool.connection() as conn:
            return {t.astimezone(UTC): v for t, v in conn.execute(sql, (area, start, end)).fetchall()}
