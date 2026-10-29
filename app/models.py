"""Plain data types shared by the pure-logic modules (no framework imports here)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Anomaly kinds
DUPLICATE = "duplicate"
CONFLICTING_DUPLICATE = "conflicting_duplicate"
MISSING = "missing_interval"
NEGATIVE = "negative_consumption"
ROLLOVER = "meter_rollover"
IMPLAUSIBLE = "implausible_consumption"
KINDS = (DUPLICATE, CONFLICTING_DUPLICATE, MISSING, NEGATIVE, ROLLOVER, IMPLAUSIBLE)


class RowError(ValueError):
    """A single submitted reading was rejected. `code` is a stable machine-readable reason."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Reading:
    """A cumulative register value (kWh) read from a meter at `ts` (timezone-aware, UTC)."""

    meter_id: str
    ts: datetime
    register_kwh: float


@dataclass(frozen=True)
class Anomaly:
    kind: str
    meter_id: str
    ts: datetime | None
    severity: str  # "info" | "warning" | "error"
    detail: str
    count: int = 1
