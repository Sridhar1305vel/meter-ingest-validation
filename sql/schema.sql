-- All timestamps are UTC instants (timestamptz). Local time is only used at the API edge.

CREATE TABLE IF NOT EXISTS readings (
    meter_id     TEXT             NOT NULL,
    ts_utc       TIMESTAMPTZ      NOT NULL,
    register_kwh DOUBLE PRECISION NOT NULL CHECK (register_kwh >= 0),
    batch_id     TEXT             NOT NULL,
    ingested_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts_utc);

-- Anomalies that cannot be re-derived from stored readings (the duplicate itself is not stored).
CREATE TABLE IF NOT EXISTS ingest_anomalies (
    id          BIGSERIAL   PRIMARY KEY,
    batch_id    TEXT        NOT NULL,
    kind        TEXT        NOT NULL,
    severity    TEXT        NOT NULL,
    meter_id    TEXT        NOT NULL,
    ts_utc      TIMESTAMPTZ,
    detail      TEXT        NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ingest_anomalies_idx ON ingest_anomalies (meter_id, ts_utc);

-- Grid-level totals per 15-minute interval (start of interval), in kWh.
CREATE TABLE IF NOT EXISTS grid_totals (
    area   TEXT             NOT NULL,
    ts_utc TIMESTAMPTZ      NOT NULL,
    kwh    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (area, ts_utc)
);
