from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from .models import Activity, DailyEnergy, WeekSummary

CATEGORIES = ("strength", "cardio", "walking", "rehab", "alt", "other")

CATEGORY_LABELS = {
    "strength": "Strength",
    "cardio": "Bike & cardio",
    "walking": "Walking",
    "rehab": "Rehab & mobility",
    "alt": "Swim, row, elliptical",
    "other": "Other",
}

_CATEGORY_BY_TYPE = {
    "strength_training": "strength",
    "walking": "walking",
    "indoor_cycling": "cardio",
    "cycling": "cardio",
    "mobility": "rehab",
    "other": "rehab",
    "yoga": "rehab",
    "pilates": "rehab",
    "lap_swimming": "alt",
    "open_water_swimming": "alt",
    "swimming": "alt",
    "indoor_rowing": "alt",
    "rowing": "alt",
    "elliptical": "alt",
}


def categorize(type_key: str) -> str:
    return _CATEGORY_BY_TYPE.get(type_key, "other")


def parse_activities(raw: list[dict]) -> list[Activity]:
    activities: list[Activity] = []
    for item in raw or []:
        start = item.get("startTimeLocal")
        if not start or len(str(start)) < 10:
            continue
        activity_type = item.get("activityType") or {}
        avg_hr = item.get("averageHR")
        activities.append(
            Activity(
                date=str(start)[:10],
                type_key=activity_type.get("typeKey") or "unknown",
                minutes=float(item.get("duration") or 0) / 60,
                kcal=float(item.get("calories") or 0),
                avg_hr=None if avg_hr is None else float(avg_hr),
            )
        )
    return activities


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_summary(acts: list[Activity], weeks: int, today: date) -> list[WeekSummary]:
    current = _monday(today)
    starts = [current - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    buckets = {
        s: {
            "sessions": {c: 0 for c in CATEGORIES},
            "minutes": {c: 0.0 for c in CATEGORIES},
            "kcal": 0.0,
        }
        for s in starts
    }
    for a in acts:
        try:
            start = _monday(date.fromisoformat(a.date))
        except ValueError:
            continue
        bucket = buckets.get(start)
        if bucket is None:
            continue
        category = categorize(a.type_key)
        bucket["sessions"][category] += 1
        bucket["minutes"][category] += a.minutes
        bucket["kcal"] += a.kcal
    return [
        WeekSummary(
            week_start=s.isoformat(),
            sessions=b["sessions"],
            minutes=b["minutes"],
            total_minutes=sum(b["minutes"].values()),
            total_kcal=b["kcal"],
        )
        for s, b in buckets.items()
    ]


def alerts(acts: list[Activity], today: date) -> list[str]:
    dated: list[tuple[date, str]] = []
    for a in acts:
        try:
            day = date.fromisoformat(a.date)
        except ValueError:
            continue
        if day <= today:
            dated.append((day, categorize(a.type_key)))
    if not dated:
        return ["No recent activity found in Garmin."]

    result: list[str] = []
    strength_days = [d for d, c in dated if c == "strength"]
    if not strength_days:
        result.append("No strength session on record in this period.")
    else:
        gap = (today - max(strength_days)).days
        if gap >= 7:
            result.append(f"No strength session in {gap} days.")

    this_week = sum(1 for d in strength_days if d >= _monday(today))
    if today.weekday() >= 3 and this_week < 2:
        plural = "session" if this_week == 1 else "sessions"
        result.append(f"Only {this_week} strength {plural} this week (target: 2).")

    cardio_days = [d for d, c in dated if c == "cardio"]
    if not cardio_days or (today - max(cardio_days)).days >= 10:
        result.append("No low-impact cardio in 10+ days (stationary bike).")
    return result


def avg_daily_kcal(daily: list[DailyEnergy]) -> float | None:
    values = [d.total_kcal for d in daily if d.total_kcal > 0]
    if not values:
        return None
    return sum(values) / len(values)


def gap_report(weeks: list[WeekSummary], targets: dict[str, int], window: int = 4) -> list[dict]:
    """Average minutes per week over the last ``window`` complete weeks vs the targets.

    The newest week is partial, so it is ignored (unless it is the only one).
    """
    complete = weeks[:-1] if len(weeks) > 1 else list(weeks)
    recent = complete[-window:]
    report = []
    for category, target in targets.items():
        avg = sum(w.minutes.get(category, 0.0) for w in recent) / len(recent) if recent else 0.0
        if avg == 0:
            status = "missing"
        elif avg >= target:
            status = "on_target"
        else:
            status = "short"
        report.append(
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "avg_minutes": round(avg, 1),
                "target_minutes": target,
                "gap_minutes": round(max(0.0, target - avg), 1),
                "status": status,
            }
        )
    return report


def headline(gaps: list[dict]) -> str:
    below = []
    for g in gaps:
        if g["status"] == "missing":
            below.append(f"{g['label']} (not started)")
        elif g["status"] == "short":
            below.append(f"{g['label']} ({g['gap_minutes']:.0f} min/week short)")
    on_target = [g["label"] for g in gaps if g["status"] == "on_target"]
    if not below:
        return "Every training type is on target."
    text = "Below target: " + ", ".join(below) + "."
    if on_target:
        text += " On target: " + ", ".join(on_target) + "."
    return text


def energy_gap(
    daily: list[DailyEnergy], target_kcal: float, today: date | None = None
) -> list[dict]:
    """Per-day active energy vs target. Today is flagged ``partial``: it is still in progress."""
    result = []
    for d in daily:
        entry = asdict(d)
        entry["target_kcal"] = target_kcal
        entry["gap_kcal"] = max(0.0, target_kcal - d.active_kcal)
        entry["partial"] = today is not None and d.date == today.isoformat()
        result.append(entry)
    return result


def summary_stats(acts: list[Activity], today: date, days: int = 56) -> dict:
    start = today - timedelta(days=days)
    minutes = 0.0
    sessions = 0
    active_days: set[str] = set()
    for a in acts:
        try:
            day = date.fromisoformat(a.date)
        except ValueError:
            continue
        if start < day <= today:
            sessions += 1
            minutes += a.minutes
            active_days.add(a.date)
    return {
        "days": days,
        "sessions": sessions,
        "hours": round(minutes / 60, 1),
        "active_days": len(active_days),
    }
