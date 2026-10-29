from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    register_max_kwh: float = 100_000.0  # register wraps to 0 at this value
    max_interval_kwh: float = 50.0  # above this per 15 min a delta is implausible
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Berlin"))

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            register_max_kwh=float(os.environ.get("REGISTER_MAX_KWH", 100_000.0)),
            max_interval_kwh=float(os.environ.get("MAX_INTERVAL_KWH", 50.0)),
            tz=ZoneInfo(os.environ.get("GRID_TZ", "Europe/Berlin")),
        )
