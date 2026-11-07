from datetime import date

import pytest

from fitdash.metrics import (
    CATEGORIES,
    alerts,
    avg_daily_kcal,
    categorize,
    energy_gap,
    gap_report,
    headline,
    parse_activities,
    summary_stats,
    weekly_summary,
)
from fitdash.models import Activity, DailyEnergy, WeekSummary

TODAY = date(2026, 11, 7)  # Saturday
TARGETS = {"strength": 90, "cardio": 60, "walking": 90, "rehab": 90, "alt": 45}


def act(day: str, type_key: str, minutes: float = 30, kcal: float = 200) -> Activity:
    return Activity(date=day, type_key=type_key, minutes=minutes, kcal=kcal, avg_hr=100)


def week(start: str, **minutes: float) -> WeekSummary:
    mins = {c: float(minutes.get(c, 0)) for c in CATEGORIES}
    return WeekSummary(
        week_start=start,
        sessions={c: int(m > 0) for c, m in mins.items()},
        minutes=mins,
        total_minutes=sum(mins.values()),
        total_kcal=0.0,
    )


@pytest.mark.parametrize(
    "type_key,expected",
    [
        ("strength_training", "strength"),
        ("walking", "walking"),
        ("indoor_cycling", "cardio"),
        ("cycling", "cardio"),
        ("mobility", "rehab"),
        ("other", "rehab"),
        ("yoga", "rehab"),
        ("lap_swimming", "alt"),
        ("indoor_rowing", "alt"),
        ("elliptical", "alt"),
        ("running", "other"),
    ],
)
def test_categorize(type_key, expected):
    assert categorize(type_key) == expected


def test_parse_activities_from_garmin_shape():
    raw = [
        {
            "startTimeLocal": "2026-11-05 18:30:00",
            "duration": 3120,
            "calories": 328,
            "averageHR": 108,
            "activityType": {"typeKey": "strength_training"},
        }
    ]
    [a] = parse_activities(raw)
    assert a == Activity("2026-11-05", "strength_training", 52.0, 328.0, 108.0)


def test_parse_skips_malformed_and_tolerates_none_values():
    raw = [
        {"duration": 600},  # no date
        {"startTimeLocal": None, "duration": 600},
        {
            "startTimeLocal": "2026-11-05 10:00:00",
            "duration": None,
            "calories": None,
            "averageHR": None,
        },
    ]
    result = parse_activities(raw)
    assert len(result) == 1
    assert result[0].minutes == 0.0
    assert result[0].kcal == 0.0
    assert result[0].avg_hr is None
    assert result[0].type_key == "unknown"


def test_weekly_summary_empty_returns_n_empty_weeks():
    weeks = weekly_summary([], weeks=8, today=TODAY)
    assert len(weeks) == 8
    assert weeks[-1].week_start == "2026-11-02"
    assert weeks[0].week_start == "2026-09-14"
    for w in weeks:
        assert w.total_minutes == 0
        assert w.total_kcal == 0
        assert set(w.sessions) == set(CATEGORIES)
        assert set(w.minutes) == set(CATEGORIES)
        assert all(v == 0 for v in w.sessions.values())


def test_weekly_summary_buckets_by_monday():
    acts = [act("2026-11-01", "walking", 30, 100), act("2026-11-02", "walking", 20, 80)]
    previous, current = weekly_summary(acts, weeks=2, today=TODAY)
    assert (previous.week_start, current.week_start) == ("2026-10-26", "2026-11-02")
    assert previous.sessions["walking"] == 1 and previous.minutes["walking"] == 30
    assert current.sessions["walking"] == 1 and current.minutes["walking"] == 20
    assert current.total_minutes == 20 and current.total_kcal == 80


def test_weekly_summary_ignores_activities_outside_window():
    weeks = weekly_summary([act("2026-01-01", "walking")], weeks=4, today=TODAY)
    assert sum(w.total_minutes for w in weeks) == 0


def test_alert_no_strength_for_8_days():
    acts = [act("2026-10-30", "strength_training"), act("2026-11-06", "indoor_cycling")]
    assert any("strength" in a for a in alerts(acts, TODAY))


def test_no_alert_when_recent_strength_and_cardio():
    acts = [
        act("2026-11-02", "strength_training"),
        act("2026-11-05", "strength_training"),
        act("2026-11-06", "indoor_cycling"),
    ]
    assert alerts(acts, TODAY) == []


def test_alert_fewer_than_two_strength_sessions_late_in_week():
    acts = [act("2026-11-05", "strength_training"), act("2026-11-06", "indoor_cycling")]
    assert any("this week" in a for a in alerts(acts, TODAY))


def test_no_weekly_strength_alert_early_in_week():
    monday = date(2026, 11, 2)
    acts = [act("2026-10-31", "strength_training"), act("2026-10-30", "indoor_cycling")]
    assert alerts(acts, monday) == []


def test_alert_no_cardio_for_10_days():
    acts = [
        act("2026-11-02", "strength_training"),
        act("2026-11-05", "strength_training"),
        act("2026-10-20", "indoor_cycling"),
    ]
    assert any("cardio" in a.lower() for a in alerts(acts, TODAY))


def test_alert_no_activity_at_all_gives_single_alert():
    assert len(alerts([], TODAY)) == 1


def test_avg_daily_kcal():
    assert avg_daily_kcal([]) is None
    daily = [
        DailyEnergy("2026-11-05", 8000, 500, 2300),
        DailyEnergy("2026-11-06", 9000, 600, 2500),
        DailyEnergy("2026-11-07", 100, 0, 0),  # day without data: ignored
    ]
    assert avg_daily_kcal(daily) == 2400.0
    assert avg_daily_kcal([DailyEnergy("2026-11-07", 0, 0, 0)]) is None


def test_gap_report_averages_last_four_complete_weeks_and_ignores_the_current_one():
    weeks = [
        week("2026-10-05", strength=999),  # older than the window
        week("2026-10-12", strength=80, cardio=60, walking=120, rehab=60),
        week("2026-10-19", strength=100, cardio=60, walking=120, rehab=60),
        week("2026-10-26", strength=100, cardio=60, walking=120, rehab=60),
        week("2026-11-02", strength=100, cardio=60, walking=120, rehab=60),
        week("2026-11-09", strength=0),  # current, partial: ignored
    ]
    by_cat = {g["category"]: g for g in gap_report(weeks, TARGETS, window=4)}
    assert by_cat["strength"]["avg_minutes"] == 95.0
    assert by_cat["strength"]["status"] == "on_target"
    assert by_cat["walking"]["status"] == "on_target"
    assert by_cat["rehab"]["status"] == "short"
    assert by_cat["rehab"]["gap_minutes"] == 30.0
    assert by_cat["alt"]["status"] == "missing"
    assert by_cat["alt"]["gap_minutes"] == 45.0
    assert list(by_cat) == list(TARGETS)


def test_gap_report_with_a_single_week_uses_it():
    [only] = [week("2026-11-02", strength=90)]
    by_cat = {g["category"]: g for g in gap_report([only], TARGETS, window=4)}
    assert by_cat["strength"]["status"] == "on_target"


def test_gap_report_without_weeks_marks_everything_missing():
    assert {g["status"] for g in gap_report([], TARGETS, window=4)} == {"missing"}


def test_headline_when_everything_is_on_target():
    gaps = [{"category": c, "label": c, "avg_minutes": t, "target_minutes": t, "gap_minutes": 0.0, "status": "on_target"} for c, t in TARGETS.items()]
    assert headline(gaps) == "Every training type is on target."


def test_headline_names_short_and_missing_types():
    weeks = [week(f"2026-10-{d:02d}", strength=100, cardio=60, walking=120, rehab=60) for d in (5, 12, 19, 26)] + [week("2026-11-02")]
    text = headline(gap_report(weeks, TARGETS, window=4))
    assert "Rehab & mobility" in text and "30 min/week short" in text
    assert "Swim, row, elliptical" in text and "not started" in text
    assert "Strength" not in text.split("On target")[0]
    assert "On target: Strength" in text


def test_energy_gap_adds_target_and_shortfall_without_negative_gaps():
    daily = [DailyEnergy("2026-11-06", 8000, 350, 2200), DailyEnergy("2026-11-07", 9000, 650, 2500)]
    low, high = energy_gap(daily, target_kcal=500)
    assert (low["target_kcal"], low["gap_kcal"]) == (500, 150)
    assert (high["target_kcal"], high["gap_kcal"]) == (500, 0)
    assert high["active_kcal"] == 650 and high["steps"] == 9000


def test_energy_gap_marks_today_as_in_progress_so_it_is_not_a_false_shortfall():
    daily = [DailyEnergy("2026-11-06", 8000, 700, 2500), DailyEnergy("2026-11-07", 3000, 120, 1300)]
    yesterday, today = energy_gap(daily, target_kcal=500, today=TODAY)
    assert yesterday["partial"] is False
    assert today["partial"] is True
    assert energy_gap(daily, target_kcal=500)[1]["partial"] is False


def test_summary_stats_counts_the_last_56_days_only():
    acts = [
        act("2026-11-07", "walking", 60),
        act("2026-11-07", "strength_training", 30),
        act("2026-11-05", "walking", 30),
        act("2026-09-12", "walking", 600),  # 56 days before today: outside the window
    ]
    stats = summary_stats(acts, TODAY, days=56)
    assert stats == {"days": 56, "sessions": 3, "hours": 2.0, "active_days": 2}


def test_summary_stats_empty():
    assert summary_stats([], TODAY, days=56) == {"days": 56, "sessions": 0, "hours": 0.0, "active_days": 0}
