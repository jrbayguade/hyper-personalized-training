from __future__ import annotations

import os
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

from .advice import AdviceError, generate_advice, load_cached
from .garmin_source import (
    RATE_LIMIT_MESSAGE,
    GarminUnavailable,
    fetch_activities,
    fetch_daily,
    is_rate_limited,
    load_client,
)
from .labs import find_labs_file, load_labs, prompt_lines
from .labs import summary as labs_summary
from .metrics import (
    CATEGORIES,
    CATEGORY_LABELS,
    alerts as build_alerts,
    avg_daily_kcal,
    energy_gap,
    gap_report,
    headline,
    summary_stats,
    weekly_summary,
)
from .profile import (
    ACTIVE_KCAL_TARGET,
    GOAL,
    PROFILE,
    RESTRICTIONS,
    WEEKLY_TARGETS,
    bmi,
    bmr,
)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
WEEKS = 8
DAILY_DAYS = 14
STATS_DAYS = WEEKS * 7
MISSING_KEY = "Missing DEEPSEEK_API_KEY in dashboard/.env."


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    cfg = dict(config or {})
    data_dir = Path(cfg.get("DATA_DIR") or DEFAULT_DATA_DIR)
    factory = cfg.get("GARMIN_CLIENT_FACTORY") or load_client
    llm_post = cfg.get("LLM_POST") or requests.post

    def api_key() -> str | None:
        if "API_KEY" in cfg:
            return cfg["API_KEY"]
        return os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API")

    def today() -> date:
        return cfg.get("TODAY") or date.today()

    def window_start(t: date) -> date:
        return t - timedelta(days=t.weekday()) - timedelta(weeks=WEEKS - 1)

    session: dict = {"client": None}  # logged-in Garmin client, reused between requests

    def collect():
        """Read Garmin data; never raises. Returns (activities, daily, error_message)."""
        t = today()
        try:
            if session["client"] is None:
                session["client"] = factory()
            client = session["client"]
            return (
                fetch_activities(client, window_start(t), t),
                fetch_daily(client, DAILY_DAYS, t),
                None,
            )
        except GarminUnavailable as exc:
            session["client"] = None
            return [], [], str(exc)
        except Exception as exc:
            session["client"] = None
            if is_rate_limited(exc):
                return [], [], RATE_LIMIT_MESSAGE
            return [], [], "Could not read Garmin data. Try again later."

    def labs_markers():
        path = cfg.get("LABS_PATH") or find_labs_file(data_dir)
        return load_labs(path) if path else []

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/dashboard")
    def dashboard():
        acts, daily, garmin_error = collect()
        t = today()
        weeks = weekly_summary(acts, WEEKS, t)
        gaps = gap_report(weeks, WEEKLY_TARGETS)
        markers = labs_markers()
        start = window_start(t)
        return jsonify(
            {
                "profile": {
                    "weight_kg": PROFILE.weight_kg,
                    "height_cm": PROFILE.height_cm,
                    "age": PROFILE.age,
                    "bmi": bmi(PROFILE),
                    "bmr": bmr(PROFILE),
                    "goal": GOAL,
                    "restrictions": RESTRICTIONS,
                },
                "period": {
                    "start": start.isoformat(),
                    "end": t.isoformat(),
                    "days": (t - start).days + 1,
                    "sessions": len(acts),
                },
                "stats": summary_stats(acts, t, STATS_DAYS),
                "categories": list(CATEGORIES),
                "labels": CATEGORY_LABELS,
                "targets": {"weekly": WEEKLY_TARGETS, "active_kcal": ACTIVE_KCAL_TARGET},
                "weeks": [asdict(w) for w in weeks],
                "gaps": gaps,
                "headline": "" if garmin_error else headline(gaps),
                "daily": energy_gap(daily, ACTIVE_KCAL_TARGET, t),
                "alerts": [] if garmin_error else build_alerts(acts, t),
                "avg_daily_kcal": avg_daily_kcal(daily),
                "labs": {"available": True, **labs_summary(markers)} if markers else {"available": False},
                "advice": load_cached(data_dir / "advice.json"),
                "garmin_error": garmin_error,
            }
        )

    @app.post("/api/advice/refresh")
    def refresh_advice():
        # Browsers cannot send a JSON body cross-site without a CORS preflight,
        # so this blocks other web pages from triggering a paid LLM call.
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 415
        key = api_key()
        if not key:
            return jsonify({"error": MISSING_KEY}), 502
        acts, daily, garmin_error = collect()
        if garmin_error:
            return jsonify({"error": garmin_error}), 502
        t = today()
        weeks = weekly_summary(acts, WEEKS, t)
        try:
            payload = generate_advice(
                PROFILE,
                weeks,
                build_alerts(acts, t),
                avg_daily_kcal(daily),
                gap_report(weeks, WEEKLY_TARGETS),
                prompt_lines(labs_markers()),
                cache_path=data_dir / "advice.json",
                api_key=key,
                post=llm_post,
            )
        except AdviceError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(payload)

    return app
