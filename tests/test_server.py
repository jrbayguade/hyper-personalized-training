import json
from datetime import date

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from fitdash.garmin_source import GarminUnavailable
from fitdash.server import create_app

TODAY = date(2026, 11, 7)

ACTIVITIES = [
    {
        "startTimeLocal": "2026-11-05 18:30:00",
        "duration": 3120,
        "calories": 328,
        "averageHR": 108,
        "activityType": {"typeKey": "strength_training"},
    },
    {
        "startTimeLocal": "2026-11-06 10:00:00",
        "duration": 2700,
        "calories": 338,
        "averageHR": 112,
        "activityType": {"typeKey": "indoor_cycling"},
    },
]

GOOD_ADVICE = {
    "training": [{"title": "Upper-body strength", "detail": "Easy pace.", "exercises": ["Plank"]}],
    "nutrition": [{"title": "Protein", "detail": "1.6 g/kg."}],
}

LABS_CSV = """Secció,Paràmetre,Resultat,Unitats,Valors de referència
Hemograma,Hemoglobina,"14,5",g/dL,12.5 - 17.2
Metabolismo lipoproteíco,Colesterol LDL,164,mg/dL,< 116 (RCV bajo)
Vitaminas,Vitamina D 25OH (calcidiol),"22,6",ng/mL,30 - 100
"""


class FakeClient:
    def __init__(self, activities=ACTIVITIES, fail=False):
        self.activities = activities
        self.fail = fail

    def get_activities_by_date(self, start, end):
        if self.fail:
            raise RuntimeError("network down")
        return self.activities

    def get_user_summary(self, day):
        return {"totalSteps": 8000, "activeKilocalories": 350, "totalKilocalories": 2400}


class Spy:
    """Counts calls so tests can assert nothing external was touched."""

    def __init__(self, result=None, error=None):
        self.calls = 0
        self.result = result
        self.error = error

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class LlmPost:
    def __init__(self, advice=GOOD_ADVICE, status=200):
        self.calls = 0
        self.advice = advice
        self.status = status
        self.last_prompt = ""

    def __call__(self, url, **kwargs):
        self.calls += 1
        self.last_prompt = "\n".join(m["content"] for m in kwargs["json"]["messages"])
        outer = self

        class Response:
            status_code = outer.status

            def json(self):
                return {"choices": [{"message": {"content": json.dumps(outer.advice)}}]}

        return Response()


def make_app(tmp_path, client=None, factory=None, llm=None, api_key="k"):
    factory = factory or Spy(result=client if client is not None else FakeClient())
    llm = llm or LlmPost()
    app = create_app(
        {
            "DATA_DIR": tmp_path,
            "GARMIN_CLIENT_FACTORY": factory,
            "LLM_POST": llm,
            "API_KEY": api_key,
            "TODAY": TODAY,
        }
    )
    return app, factory, llm


def get_dashboard(app):
    return app.test_client().get("/api/dashboard").get_json()


def test_dashboard_json_shape_with_data(tmp_path):
    app, _, _ = make_app(tmp_path)
    data = get_dashboard(app)
    assert data["garmin_error"] is None
    assert data["profile"]["bmr"] == 1750.0 and round(data["profile"]["bmi"], 1) == 22.6
    assert data["profile"]["goal"] and data["profile"]["restrictions"]
    assert data["period"] == {"start": "2026-09-14", "end": "2026-11-07", "days": 55, "sessions": 2}
    assert len(data["weeks"]) == 8
    assert data["weeks"][-1]["sessions"]["strength"] == 1
    assert data["weeks"][-1]["sessions"]["cardio"] == 1
    assert len(data["daily"]) == 14
    assert data["avg_daily_kcal"] == 2400.0
    assert [g["category"] for g in data["gaps"]] == ["strength", "cardio", "walking", "rehab", "alt"]
    assert isinstance(data["headline"], str) and data["headline"]
    assert set(data["stats"]) == {"days", "sessions", "hours", "active_days"}
    assert data["targets"]["active_kcal"] == 500
    assert data["targets"]["weekly"]["strength"] == 90
    assert data["labels"]["alt"] == "Swim, row, elliptical"
    assert isinstance(data["alerts"], list)
    assert data["advice"] is None
    assert data["labs"] == {"available": False}
    assert "weights" not in data


def test_daily_entries_carry_the_energy_target_and_shortfall(tmp_path):
    app, _, _ = make_app(tmp_path)
    day = get_dashboard(app)["daily"][-1]
    assert (day["active_kcal"], day["target_kcal"], day["gap_kcal"]) == (350.0, 500, 150)
    assert day["partial"] is True  # the last entry is today, still in progress


def test_dashboard_with_no_activities(tmp_path):
    app, _, _ = make_app(tmp_path, client=FakeClient(activities=[]))
    response = app.test_client().get("/api/dashboard")
    data = response.get_json()
    assert response.status_code == 200
    assert len(data["weeks"]) == 8
    assert all(w["total_minutes"] == 0 for w in data["weeks"])
    assert data["alerts"]
    assert {g["status"] for g in data["gaps"]} == {"missing"}
    assert data["period"]["sessions"] == 0


def test_dashboard_when_garmin_unavailable_still_shows_cached_advice(tmp_path):
    seed_app, _, _ = make_app(tmp_path)
    assert seed_app.test_client().post("/api/advice/refresh", json={}).status_code == 200

    factory = Spy(error=GarminUnavailable("Garmin is rate limiting requests (429). Try again later."))
    app, _, _ = make_app(tmp_path, factory=factory)
    response = app.test_client().get("/api/dashboard")
    data = response.get_json()
    assert response.status_code == 200
    assert "429" in data["garmin_error"]
    assert data["advice"]["advice"]["training"][0]["title"] == "Upper-body strength"
    assert data["weeks"] and all(w["total_minutes"] == 0 for w in data["weeks"])


def test_dashboard_when_garmin_fetch_raises_unexpected_error(tmp_path):
    app, _, _ = make_app(tmp_path, client=FakeClient(fail=True))
    response = app.test_client().get("/api/dashboard")
    assert response.status_code == 200
    assert response.get_json()["garmin_error"]


def test_labs_are_summarised_but_values_never_reach_the_page_data(tmp_path):
    (tmp_path / "analitica_test.csv").write_text(LABS_CSV, encoding="utf-8-sig")
    app, _, _ = make_app(tmp_path)
    response = app.test_client().get("/api/dashboard")
    data = response.get_json()
    assert data["labs"] == {"available": True, "checked": 3, "flagged": 2}
    raw = response.get_data(as_text=True)
    assert "164" not in raw and "Colesterol" not in raw


def test_flagged_lab_markers_are_sent_to_the_llm_but_normal_ones_are_not(tmp_path):
    (tmp_path / "analitica_test.csv").write_text(LABS_CSV, encoding="utf-8-sig")
    app, _, llm = make_app(tmp_path)
    assert app.test_client().post("/api/advice/refresh", json={}).status_code == 200
    assert "Colesterol LDL" in llm.last_prompt
    assert "Vitamina D" in llm.last_prompt
    assert "Hemoglobina" not in llm.last_prompt


def test_logged_in_client_is_reused_across_requests(tmp_path):
    app, factory, _ = make_app(tmp_path)
    client = app.test_client()
    client.get("/api/dashboard")
    client.get("/api/dashboard")
    assert factory.calls == 1


def test_client_is_dropped_after_a_failure_and_retried(tmp_path):
    app, factory, _ = make_app(tmp_path, client=FakeClient(fail=True))
    client = app.test_client()
    assert client.get("/api/dashboard").get_json()["garmin_error"]
    assert client.get("/api/dashboard").get_json()["garmin_error"]
    assert factory.calls == 2


def test_rate_limit_while_reading_activities_says_try_again_later(tmp_path):
    class Limited(FakeClient):
        def get_activities_by_date(self, start, end):
            raise GarminConnectTooManyRequestsError("429")

    app, _, _ = make_app(tmp_path, client=Limited())
    assert "try again later" in get_dashboard(app)["garmin_error"].lower()


def test_rate_limit_in_daily_summaries_is_not_silent(tmp_path):
    class Limited(FakeClient):
        def get_user_summary(self, day):
            raise GarminConnectTooManyRequestsError("429")

    app, _, _ = make_app(tmp_path, client=Limited())
    assert "try again later" in get_dashboard(app)["garmin_error"].lower()


def test_weight_endpoint_is_gone(tmp_path):
    app, _, _ = make_app(tmp_path)
    response = app.test_client().post("/api/weight", json={"date": "2026-11-07", "weight_kg": 80})
    assert response.status_code == 404


def test_refresh_advice_success(tmp_path):
    app, _, llm = make_app(tmp_path)
    response = app.test_client().post("/api/advice/refresh", json={})
    assert response.status_code == 200
    body = response.get_json()
    assert body["generated_at"] and body["removed"] == 0
    assert (tmp_path / "advice.json").exists()
    assert llm.calls == 1
    assert "English" in llm.last_prompt


def test_refresh_advice_llm_failure_returns_502_keeps_cache(tmp_path):
    app, _, _ = make_app(tmp_path)
    assert app.test_client().post("/api/advice/refresh", json={}).status_code == 200
    before = (tmp_path / "advice.json").read_bytes()

    failing, _, _ = make_app(tmp_path, llm=LlmPost(status=500))
    response = failing.test_client().post("/api/advice/refresh", json={})
    assert response.status_code == 502
    assert response.get_json()["error"]
    assert (tmp_path / "advice.json").read_bytes() == before


def test_refresh_without_api_key_returns_502_and_touches_nothing(tmp_path):
    app, factory, llm = make_app(tmp_path, api_key=None)
    response = app.test_client().post("/api/advice/refresh", json={})
    assert response.status_code == 502
    assert "DEEPSEEK" in response.get_json()["error"]
    assert llm.calls == 0 and factory.calls == 0


def test_refresh_when_garmin_unavailable_returns_502(tmp_path):
    factory = Spy(error=GarminUnavailable("No tokens."))
    app, _, llm = make_app(tmp_path, factory=factory)
    response = app.test_client().post("/api/advice/refresh", json={})
    assert response.status_code == 502
    assert response.get_json()["error"] == "No tokens."
    assert llm.calls == 0


def test_refresh_requires_a_json_request(tmp_path):
    app, factory, llm = make_app(tmp_path)
    response = app.test_client().post(
        "/api/advice/refresh", data="x=1", content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code == 415
    assert factory.calls == 0 and llm.calls == 0


@pytest.mark.parametrize(
    "needle",
    [
        "Fixed profile values, not read from Garmin",
        "General guidance only. It does not replace your physio or a doctor.",
        "Not based on what you eat: meals are not logged.",
        "Refresh advice",
    ],
)
def test_index_html_has_the_required_notices(tmp_path, needle):
    app, factory, llm = make_app(tmp_path)
    response = app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert needle in html
    assert "weight-form" not in html
    assert "cdn.jsdelivr.net/npm/chart.js" not in html
    assert factory.calls == 0 and llm.calls == 0


def test_old_cached_advice_in_another_language_is_not_shown(tmp_path):
    legacy = {
        "generated_at": "2026-11-07T12:22:00",
        "removed": 0,
        "advice": {"training": [{"title": "Força", "detail": "", "exercises": []}], "nutrition": []},
    }
    (tmp_path / "advice.json").write_text(json.dumps(legacy), encoding="utf-8")
    app, _, _ = make_app(tmp_path)
    assert get_dashboard(app)["advice"] is None
