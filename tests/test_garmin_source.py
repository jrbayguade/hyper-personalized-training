from datetime import date

import pytest
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from fitdash.garmin_source import (
    GarminUnavailable,
    fetch_activities,
    fetch_daily,
    load_client,
)

TODAY = date(2026, 11, 7)


class FakeClient:
    def __init__(self, summaries=None, failing_days=(), activities=None):
        self.summaries = summaries or {}
        self.failing_days = set(failing_days)
        self.activities = activities or []
        self.activity_calls = []
        self.summary_calls = []

    def get_activities_by_date(self, start, end):
        self.activity_calls.append((start, end))
        return self.activities

    def get_user_summary(self, day):
        self.summary_calls.append(day)
        if day in self.failing_days:
            raise RuntimeError("boom")
        return self.summaries.get(day, {})


def test_fetch_activities_parses_and_passes_dates():
    raw = [
        {
            "startTimeLocal": "2026-11-05 18:30:00",
            "duration": 3120,
            "calories": 328,
            "averageHR": 108,
            "activityType": {"typeKey": "strength_training"},
        }
    ]
    client = FakeClient(activities=raw)
    result = fetch_activities(client, date(2026, 9, 12), TODAY)
    assert client.activity_calls == [("2026-09-12", "2026-11-07")]
    assert result[0].type_key == "strength_training"
    assert result[0].minutes == 52.0


def test_fetch_daily_maps_fields_and_skips_failures():
    client = FakeClient(
        summaries={
            "2026-11-07": {"totalSteps": 8000, "activeKilocalories": 500.5, "totalKilocalories": 2300.2},
            "2026-11-06": {"totalSteps": 9000, "activeKilocalories": 600, "totalKilocalories": 2500},
        },
        failing_days={"2026-11-05"},
    )
    result = fetch_daily(client, days=3, today=TODAY)
    assert [d.date for d in result] == ["2026-11-06", "2026-11-07"]
    assert result[1].steps == 8000
    assert result[1].active_kcal == 500.5
    assert result[1].total_kcal == 2300.2


def test_fetch_daily_handles_none_fields():
    client = FakeClient(
        summaries={"2026-11-07": {"totalSteps": None, "activeKilocalories": None, "totalKilocalories": None}}
    )
    [day] = fetch_daily(client, days=1, today=TODAY)
    assert (day.steps, day.active_kcal, day.total_kcal) == (0, 0.0, 0.0)


def test_load_client_missing_tokens_raises_unavailable(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(GarminUnavailable) as exc:
        load_client(str(missing))
    message = str(exc.value)
    assert "garmin-mcp-auth" in message
    assert "Traceback" not in message


def test_load_client_uses_garmintokens_env(tmp_path, monkeypatch):
    missing = tmp_path / "from-env"
    monkeypatch.setenv("GARMINTOKENS", str(missing))
    with pytest.raises(GarminUnavailable) as exc:
        load_client()
    assert str(missing) in str(exc.value)


def test_load_client_rate_limit_is_wrapped(tmp_path, monkeypatch):
    def boom(self, tokenstore=None):
        raise GarminConnectTooManyRequestsError("429 secret-token-abc")

    monkeypatch.setattr(Garmin, "login", boom)
    with pytest.raises(GarminUnavailable) as exc:
        load_client(str(tmp_path))
    assert "try again later" in str(exc.value).lower()
    assert "secret-token-abc" not in str(exc.value)


def test_load_client_auth_error_points_to_reauth(tmp_path, monkeypatch):
    def boom(self, tokenstore=None):
        raise GarminConnectAuthenticationError("expired")

    monkeypatch.setattr(Garmin, "login", boom)
    with pytest.raises(GarminUnavailable) as exc:
        load_client(str(tmp_path))
    assert "garmin-mcp-auth" in str(exc.value)


def test_load_client_success_returns_logged_in_client(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Garmin, "login", lambda self, tokenstore=None: calls.append(tokenstore))
    client = load_client(str(tmp_path))
    assert isinstance(client, Garmin)
    assert calls == [str(tmp_path)]


def _auth_error_caused_by_429():
    """The real library re-raises a 429 during token login as an authentication error."""
    try:
        try:
            raise GarminConnectTooManyRequestsError("429")
        except GarminConnectTooManyRequestsError as inner:
            raise GarminConnectAuthenticationError("Failed to retrieve social profile") from inner
    except GarminConnectAuthenticationError as outer:
        return outer


def test_is_rate_limited_follows_the_cause_chain():
    from fitdash.garmin_source import is_rate_limited

    assert is_rate_limited(GarminConnectTooManyRequestsError("429"))
    assert is_rate_limited(_auth_error_caused_by_429())
    assert not is_rate_limited(GarminConnectAuthenticationError("expired"))
    assert not is_rate_limited(RuntimeError("boom"))


def test_load_client_429_wrapped_as_auth_error_still_says_retry_later(tmp_path, monkeypatch):
    err = _auth_error_caused_by_429()

    def boom(self, tokenstore=None):
        raise err

    monkeypatch.setattr(Garmin, "login", boom)
    with pytest.raises(GarminUnavailable) as exc:
        load_client(str(tmp_path))
    assert "try again later" in str(exc.value).lower()
    assert "garmin-mcp-auth" not in str(exc.value)


def test_fetch_daily_does_not_swallow_rate_limiting():
    class Limited(FakeClient):
        def get_user_summary(self, day):
            raise GarminConnectTooManyRequestsError("429")

    with pytest.raises(GarminConnectTooManyRequestsError):
        fetch_daily(Limited(), days=3, today=TODAY)
