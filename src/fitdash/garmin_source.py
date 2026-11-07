from __future__ import annotations

import os
from datetime import date, timedelta

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from .metrics import parse_activities
from .models import Activity, DailyEnergy

RATE_LIMIT_MESSAGE = "Garmin is rate limiting requests (429). Try again later."


class GarminUnavailable(Exception):
    """Garmin cannot be reached. The message is safe to show."""


def is_rate_limited(exc: BaseException | None) -> bool:
    """True if ``exc`` or anything in its cause chain is a Garmin 429.

    garminconnect re-raises a 429 during token login as an authentication
    error, so the type of the outer exception alone is not enough.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, GarminConnectTooManyRequestsError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _token_path(token_path: str | None) -> str:
    return os.path.expanduser(
        token_path or os.getenv("GARMINTOKENS") or "~/.garminconnect"
    )


def load_client(token_path: str | None = None) -> Garmin:
    path = _token_path(token_path)
    if not os.path.isdir(path):
        raise GarminUnavailable(
            f"No Garmin tokens found in {path}. "
            "Run 'garmin-mcp-auth' to sign in."
        )
    client = Garmin()
    try:
        client.login(path)
    except Exception as exc:
        if is_rate_limited(exc):
            raise GarminUnavailable(RATE_LIMIT_MESSAGE) from None
        if isinstance(exc, GarminConnectAuthenticationError):
            raise GarminUnavailable(
                "Garmin tokens are invalid or expired. "
                "Run 'garmin-mcp-auth' to sign in again."
            ) from None
        raise GarminUnavailable(
            "Could not connect to Garmin. Try again later."
        ) from None
    return client


def fetch_activities(client, start: date, end: date) -> list[Activity]:
    raw = client.get_activities_by_date(start.isoformat(), end.isoformat())
    return parse_activities(raw)


def fetch_daily(client, days: int, today: date) -> list[DailyEnergy]:
    result: list[DailyEnergy] = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        try:
            summary = client.get_user_summary(day) or {}
        except Exception as exc:
            if is_rate_limited(exc):
                raise
            continue
        result.append(
            DailyEnergy(
                date=day,
                steps=int(summary.get("totalSteps") or 0),
                active_kcal=float(summary.get("activeKilocalories") or 0),
                total_kcal=float(summary.get("totalKilocalories") or 0),
            )
        )
    return result
