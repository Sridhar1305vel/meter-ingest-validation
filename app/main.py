"""HTTP layer. Thin on purpose: validation and analysis live in the pure modules."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date as Date

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .config import Config
from .ingest import ingest_batch
from .models import KINDS, RowError
from .repository import PostgresRepository
from .service import analyse_day, reconcile_day
from .timeutil import is_aligned, parse_ts


class ReadingIn(BaseModel):
    meter_id: str
    ts: str = Field(description="ISO-8601 with explicit UTC offset, e.g. 2026-10-25T02:15:00+01:00")
    register_kwh: float = Field(description="Cumulative register value in kWh")


class ReadingsBatch(BaseModel):
    readings: list[ReadingIn] = Field(max_length=10_000)


class GridPoint(BaseModel):
    ts: str = Field(description="Start of the 15-minute interval, ISO-8601 with offset")
    kwh: float


class GridBatch(BaseModel):
    area: str = "DE"
    points: list[GridPoint] = Field(max_length=20_000)


def create_app(repo=None, cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = None
        if app.state.repo is None:
            owned = PostgresRepository(os.environ["DATABASE_URL"])
            owned.init_schema()
            app.state.repo = owned
        yield
        if owned is not None:
            owned.close()

    app = FastAPI(title="meter-ingest", version="0.1.0", lifespan=lifespan,
                  description="Interval meter-reading ingestion, validation and grid reconciliation")
    app.state.repo = repo

    @app.get("/health")
    def health():
        try:
            ok = app.state.repo.ping()
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(503, "database unavailable")
        return {"status": "ok"}

    @app.post("/readings")
    def post_readings(batch: ReadingsBatch):
        """Malformed JSON gives 422 for the whole request; bad individual readings are
        rejected row by row and listed in the response."""
        items = [(r.meter_id, r.ts, r.register_kwh) for r in batch.readings]
        return asdict(ingest_batch(app.state.repo, items, cfg))

    @app.get("/anomalies")
    def get_anomalies(day: Date = Query(..., alias="date", description="Local calendar day"),
                      meter_id: str | None = None, kind: str | None = None):
        if kind is not None and kind not in KINDS:
            raise HTTPException(422, f"kind must be one of {list(KINDS)}")
        try:
            return analyse_day(app.state.repo, cfg, day, meter_id, kind)
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.get("/reconciliation")
    def get_reconciliation(day: Date = Query(..., alias="date"), area: str = "DE",
                           scale: float = Query(1.0, gt=0, description="Share of the grid the meters represent"),
                           tolerance_pct: float = Query(5.0, gt=0)):
        return reconcile_day(app.state.repo, cfg, day, area, scale, tolerance_pct)

    @app.put("/grid-totals")
    def put_grid_totals(batch: GridBatch):
        rows = []
        for i, p in enumerate(batch.points):
            try:
                ts = parse_ts(p.ts, cfg.tz)
                if not is_aligned(ts):
                    raise RowError("misaligned_interval", "grid timestamps must be on 15-minute boundaries")
            except RowError as e:
                raise HTTPException(422, f"point {i}: {e.code}: {e.detail}")
            rows.append((ts, p.kwh))
        app.state.repo.upsert_grid(batch.area, rows)
        return {"area": batch.area, "upserted": len(rows)}

    return app


app = create_app()
