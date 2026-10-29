# meter-ingest: interval meter-reading ingestion, validation and grid reconciliation

A small FastAPI + PostgreSQL service that accepts 15-minute interval meter readings, validates them,
and reconciles the metered total against a grid-level total. Built around the problems energy-data
pipelines actually hit: **DST days with 92 or 100 intervals, meter register rollover, duplicates and
conflicting resends, and gaps.**

> **Data:** meters are **synthetic** (generated, with known injected faults). Grid totals are either
> synthetic (default) or real public data from SMARD / Bundesnetzagentur if you load a CSV export
> (see [Real grid data](#real-grid-data)). Results below say which one they used.

## What it does

| Endpoint | Purpose |
|---|---|
| `POST /readings` | Ingest a batch. Bad rows are rejected individually with a reason; identical resends are ignored; conflicting resends are flagged and the stored value is kept. |
| `GET /anomalies?date=YYYY-MM-DD[&meter_id=][&kind=]` | Anomalies for one **local** day: missing intervals, negative consumption, implausible jumps, rollovers, duplicates, conflicts. Also returns `expected_intervals` (92 / 96 / 100). |
| `GET /reconciliation?date=...&scale=...&tolerance_pct=5` | Sum of metered consumption vs `grid total x scale`, per interval, with a day summary. |
| `PUT /grid-totals` | Load grid totals (kWh per interval start). |
| `GET /health` | Liveness and database check. |

Interactive docs at `http://localhost:8000/docs`.

## Conventions (the decisions that matter)

* **Readings are cumulative register values (kWh)** taken at an instant. Consumption between two readings
  15 minutes apart is attributed to the interval that **starts** at the earlier reading (same labelling as grid data).
* **Everything is stored in UTC.** Clients send ISO-8601 **with an offset**. Naive timestamps are rejected,
  because on the 25-hour day 02:00-02:59 happens twice and a naive value cannot say which one it means.
  A non-UTC offset must also match Europe/Berlin at that instant, which catches clients sending a fixed `+01:00` all year.
* **A "day" is a local calendar day.** Expected intervals per day: 96 normally, **92** on spring-forward, **100** on fall-back.
  A full day needs readings from local midnight to the next local midnight inclusive (97 / 93 / 101).
* **Nothing is interpolated.** An interval touching a missing or suspect reading is excluded and reported.

## Validation rules

| Check | Result |
|---|---|
| Not parseable / no offset / offset wrong for the timezone / not on a 15-minute boundary | row rejected (`bad_timestamp`, `naive_timestamp`, `offset_mismatch`, `misaligned_interval`) |
| Register negative, NaN, infinite, or >= register max | row rejected (`invalid_register`, `register_above_max`) |
| Same meter + timestamp, same value | ignored, `duplicate` (info) |
| Same meter + timestamp, different value | stored value kept, `conflicting_duplicate` (error) |
| Expected reading absent | consecutive gaps grouped into one `missing_interval` with a count |
| Register falls and `fall + register_max` is a plausible 15-minute value | `meter_rollover` (info); consumption computed across the wrap |
| Register falls otherwise | `negative_consumption`; that interval **and the next** are excluded (one bad reading distorts both neighbours) |
| Rise above `MAX_INTERVAL_KWH` (default 50) in 15 min | `implausible_consumption`; interval excluded |

Reconciliation compares an interval only when **every known meter reported it** and a grid value exists.
Other intervals are counted as `incomplete_intervals` / `no_grid_value_intervals`, not silently dropped, because a
sum over a partial fleet is understated by construction.

## Run it (Linux)

Requirements: Docker with the Compose plugin, Python 3.12 for the tests.

```bash
docker compose up -d --build --wait     # PostgreSQL 16 + API on :8000
curl -s localhost:8000/health

# Generate synthetic meter data (with injected faults) and send it
python3 -m scripts.generate_synthetic --meters 20 --api http://localhost:8000

# Spring-forward day: 92 expected intervals, injected faults reported
curl -s "localhost:8000/anomalies?date=2026-03-29" | python3 -m json.tool | head -40
curl -s "localhost:8000/reconciliation?date=2026-03-30&scale=0.001"
docker compose down -v
```

Run the tests (the PostgreSQL variants use the compose database on port 5433):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d db --wait
TEST_DATABASE_URL=postgresql://meter:meter@127.0.0.1:5433/meter pytest -q
```

Without `TEST_DATABASE_URL` the PostgreSQL variants are skipped and the in-memory ones still run.

## Real grid data

```bash
# 1. Download a 15-minute grid-load CSV from smard.de (Bundesnetzagentur) and check its header.
# 2. Parse it. All format assumptions are flags; see --help.
python3 -m scripts.load_smard --file smard.csv --out data/grid.json --api http://localhost:8000
# 3. Generate meters whose load follows the real grid shape; the script prints the matching --scale
python3 -m scripts.generate_synthetic --grid-json data/grid.json --start <first day in your file> --api http://localhost:8000
```

The loader resolves the repeated local hour on the 25-hour day by order (test: `test_grid_loader_resolves_the_25_hour_day`).
Verify the column layout and units of your own export before trusting the numbers.

## Results (synthetic data, in-memory repository)

* 7 fault types injected on 7 different meters (12-reading gap, single gap, one corrupted low reading, an
  80 kWh step, a register rollover, 5 identical resends, 3 conflicting resends). The detector reported exactly those
  and nothing else on the other meters, across a spring-forward DST change, for **40 of 40 random seeds**.
* DST days: 92 and 100 expected intervals, **no false gaps** on clean data.
* Reconciliation on a clean synthetic day: **-2.9 %** difference, matching the 3 % unmetered loss built into the
  synthetic grid (so it proves the mechanics, not realism). A 12-reading outage made 13 intervals `incomplete`, and they were excluded from the comparison.
<!-- Add here after running against real SMARD data: day, scale, diff %, flagged intervals. -->

## Design notes and limits

* Validation and analysis are pure functions (`app/validation.py`, `app/reconcile.py`, `app/timeutil.py`),
  testable without a server. `app/main.py` is a thin HTTP layer; storage sits behind one interface with in-memory and PostgreSQL implementations, so the same API tests run against both.
* Anomalies are computed on read from stored readings; only duplicates and conflicts are stored at ingest (the duplicate itself is not kept). Fine at this size; a real deployment would precompute.
* Single global config for register max and plausibility limit (`REGISTER_MAX_KWH`, `MAX_INTERVAL_KWH`), not per meter.
* Cumulative-register feeds only; interval-energy feeds would need a second input model.
* No authentication, no multi-area handling beyond an `area` key on grid totals.
* Meters are synthetic. The reconciliation `scale` stands in for the share of the grid the fleet represents.

## Layout

```
app/        main.py (FastAPI), validation.py, reconcile.py, ingest.py, timeutil.py, service.py, repository.py
sql/        schema.sql
scripts/    generate_synthetic.py, load_smard.py
tests/      unit, ingest/service, generator+loader end-to-end, API (memory + PostgreSQL)
.github/    workflows/tests.yml  (pytest with a PostgreSQL service, plus a Docker smoke test)
```
