from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    date: str  # ISO YYYY-MM-DD (local)
    type_key: str
    minutes: float
    kcal: float
    avg_hr: float | None


@dataclass(frozen=True)
class DailyEnergy:
    date: str
    steps: int
    active_kcal: float
    total_kcal: float


@dataclass(frozen=True)
class WeekSummary:
    week_start: str  # ISO Monday
    sessions: dict[str, int]
    minutes: dict[str, float]
    total_minutes: float
    total_kcal: float
