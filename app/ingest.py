"""Batch ingestion: row validation, duplicate handling, persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .config import Config
from .models import CONFLICTING_DUPLICATE, DUPLICATE, Anomaly, Reading, RowError
from .validation import validate_row

MAX_REJECTS_RETURNED = 100


@dataclass
class IngestResult:
    batch_id: str
    received: int
    accepted: int = 0
    duplicates: int = 0  # identical value already stored (or earlier in the batch): ignored
    conflicts: int = 0  # same meter+timestamp, different value: stored value kept, flagged
    rejected_total: int = 0
    rejected: list[dict] = field(default_factory=list)  # first MAX_REJECTS_RETURNED


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6


def _dup_anomaly(r: Reading, kept: float, where: str) -> Anomaly:
    if _same(kept, r.register_kwh):
        return Anomaly(DUPLICATE, r.meter_id, r.ts, "info",
                       f"identical reading {r.register_kwh:.3f} kWh received again ({where}); ignored")
    return Anomaly(CONFLICTING_DUPLICATE, r.meter_id, r.ts, "error",
                   f"{where}: kept {kept:.3f} kWh, received {r.register_kwh:.3f} kWh; stored value kept")


def ingest_batch(repo, items: list[tuple], cfg: Config) -> IngestResult:
    result = IngestResult(batch_id=uuid4().hex, received=len(items))
    first: dict[tuple, Reading] = {}
    anomalies: list[Anomaly] = []

    for index, (meter_id, ts_raw, register) in enumerate(items):
        try:
            r = validate_row(meter_id, ts_raw, register, cfg)
        except RowError as e:
            result.rejected_total += 1
            if len(result.rejected) < MAX_REJECTS_RETURNED:
                result.rejected.append({"index": index, "reason": e.code, "detail": e.detail})
            continue
        key = (r.meter_id, r.ts)
        if key in first:
            a = _dup_anomaly(r, first[key].register_kwh, "earlier in the same batch")
            anomalies.append(a)
        else:
            first[key] = r

    existing = repo.existing_values(list(first)) if first else {}
    new: list[Reading] = []
    for key, r in first.items():
        if key in existing:
            anomalies.append(_dup_anomaly(r, existing[key], "already stored"))
        else:
            new.append(r)

    result.duplicates = sum(1 for a in anomalies if a.kind == DUPLICATE)
    result.conflicts = sum(1 for a in anomalies if a.kind == CONFLICTING_DUPLICATE)
    repo.insert_readings(result.batch_id, new)
    repo.add_anomalies(result.batch_id, anomalies)
    result.accepted = len(new)
    return result
