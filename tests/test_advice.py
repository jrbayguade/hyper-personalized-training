import json

import pytest

from fitdash.advice import (
    AdviceError,
    build_prompt,
    call_llm,
    enforce_restrictions,
    generate_advice,
    load_cached,
    parse_advice,
)
from fitdash.metrics import CATEGORIES
from fitdash.models import WeekSummary
from fitdash.profile import PROFILE, RESTRICTIONS


def make_weeks(n=8):
    return [
        WeekSummary(
            week_start=f"2026-09-{14 + i:02d}",
            sessions={c: (i % 3) for c in CATEGORIES},
            minutes={c: 30.0 * (i % 3) for c in CATEGORIES},
            total_minutes=150.0,
            total_kcal=900.0,
        )
        for i in range(n)
    ]


GOOD_ADVICE = {
    "training": [
        {"title": "Força de tren superior", "detail": "Treball suau i controlat.", "exercises": ["Press de banca amb mancuernes", "Planxa"]},
        {"title": "Bici estàtica", "detail": "Resistència baixa.", "exercises": []},
    ],
    "nutrition": [{"title": "Proteïna", "detail": "1,6-2 g/kg repartits en 4 àpats."}],
}


class FakeResponse:
    def __init__(self, status_code=200, content=None, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"choices": [{"message": {"content": content}}]}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def ok_post(advice=None):
    payload = json.dumps(advice if advice is not None else GOOD_ADVICE)

    def post(url, **kwargs):
        post.calls.append((url, kwargs))
        return FakeResponse(content=payload)

    post.calls = []
    return post


GAPS = [
    {"category": "strength", "label": "Strength", "avg_minutes": 95.0, "target_minutes": 90, "gap_minutes": 0.0, "status": "on_target"},
    {"category": "rehab", "label": "Rehab & mobility", "avg_minutes": 60.0, "target_minutes": 90, "gap_minutes": 30.0, "status": "short"},
    {"category": "alt", "label": "Swim, row, elliptical", "avg_minutes": 0.0, "target_minutes": 45, "gap_minutes": 45.0, "status": "missing"},
]
LAB_LINES = ["Colesterol LDL: 164 mg/dL (reference < 116) - HIGH"]


def generate(tmp_path, post, api_key="k"):
    return generate_advice(
        PROFILE, make_weeks(), ["No strength session in 8 days."], 2400.0, GAPS, LAB_LINES,
        cache_path=tmp_path / "advice.json", api_key=api_key, post=post,
    )


def test_prompt_contains_restrictions_and_stays_compact():
    messages = build_prompt(PROFILE, make_weeks(8), ["alert"], 2400.0, GAPS, LAB_LINES)
    text = "\n".join(m["content"] for m in messages)
    for line in RESTRICTIONS:
        assert line in text
    assert "1750" in text
    assert "2400" in text
    assert "Rehab & mobility" in text and "not started" in text
    assert "Colesterol LDL" in text
    assert "English" in text
    assert len(text) < 6500
    assert [m["role"] for m in messages] == ["system", "user"]


def test_prompt_without_labs_or_gaps_still_builds():
    text = "\n".join(m["content"] for m in build_prompt(PROFILE, make_weeks(2), [], None, [], []))
    assert "No bloodwork" in text and "unknown" in text


def test_parse_advice_with_fences():
    text = "```json\n" + json.dumps(GOOD_ADVICE) + "\n```"
    advice = parse_advice(text)
    assert advice["training"][0]["title"] == "Força de tren superior"
    assert advice["nutrition"][0]["title"] == "Proteïna"


def test_parse_advice_defaults_missing_exercises():
    advice = parse_advice(json.dumps({"training": [{"title": "Bici", "detail": "x"}], "nutrition": []}))
    assert advice["training"][0]["exercises"] == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"training": "x"}',
        '{"training": [], "nutrition": "x"}',
        '{"training": [{"detail": "sense títol"}], "nutrition": []}',
        '{"nutrition": []}',
    ],
)
def test_parse_advice_invalid(text):
    with pytest.raises(AdviceError):
        parse_advice(text)


def test_enforce_drops_forbidden_and_counts():
    advice = {
        "training": [
            {"title": "Córrer 20 min", "detail": "", "exercises": []},
            {"title": "Pliomètrics", "detail": "", "exercises": ["JUMP squats"]},
            {"title": "Cames", "detail": "", "exercises": ["prensa de piernas"]},
            {"title": "Core", "detail": "Treball suau.", "exercises": ["Planxa abdominal"]},
        ],
        "nutrition": [],
    }
    cleaned, removed = enforce_restrictions(advice)
    assert removed == 3
    assert [t["title"] for t in cleaned["training"]] == ["Core"]


def test_enforce_keeps_shallow_unloaded_squat_and_drops_loaded_one():
    advice = {
        "training": [
            {"title": "Cames suaus", "detail": "", "exercises": ["Sentadilla poc profunda sense pes"]},
            {"title": "Cames fortes", "detail": "", "exercises": ["Sentadilla profunda amb barra"]},
        ],
        "nutrition": [],
    }
    cleaned, removed = enforce_restrictions(advice)
    assert removed == 1
    assert cleaned["training"][0]["title"] == "Cames suaus"


@pytest.mark.parametrize(
    "detail",
    [
        "No gaire, fes running suau.",
        "Si no tens bici, surt a córrer 20 min.",
        "Si no pots anar al gimnàs, fes salts a la corda.",
        "Evita córrer o saltar.",
    ],
)
def test_enforce_is_strict_about_detail_even_with_negation_words(detail):
    advice = {"training": [{"title": "Cardio", "detail": detail, "exercises": []}], "nutrition": []}
    cleaned, removed = enforce_restrictions(advice)
    assert removed == 1 and cleaned["training"] == []


def test_enforce_filters_nutrition_items_and_counts_them():
    advice = {
        "training": [],
        "nutrition": [
            {"title": "Dèficit", "detail": "Combina-ho amb 20 min de córrer per cremar més."},
            {"title": "Proteïna", "detail": "1,6 g/kg repartits en 4 àpats."},
        ],
    }
    cleaned, removed = enforce_restrictions(advice)
    assert removed == 1
    assert [n["title"] for n in cleaned["nutrition"]] == ["Proteïna"]


def test_prompt_tells_the_model_not_to_mention_forbidden_exercises_even_as_warnings():
    text = "\n".join(m["content"] for m in build_prompt(PROFILE, make_weeks(2), [], None, [], []))
    assert "even as a warning" in text


def test_enforce_drops_forbidden_recommendation_in_detail():
    advice = {"training": [{"title": "Cardio", "detail": "Fes 20 min de running suau.", "exercises": []}], "nutrition": []}
    cleaned, removed = enforce_restrictions(advice)
    assert removed == 1 and cleaned["training"] == []


def test_enforce_does_not_mutate_input():
    advice = {"training": [{"title": "Córrer", "detail": "", "exercises": []}], "nutrition": []}
    enforce_restrictions(advice)
    assert len(advice["training"]) == 1


def test_generate_writes_cache_and_calls_deepseek(tmp_path):
    post = ok_post()
    result = generate(tmp_path, post)
    assert result["removed"] == 0
    assert result["advice"]["training"][0]["title"] == "Força de tren superior"
    assert "generated_at" in result
    assert load_cached(tmp_path / "advice.json") == result
    url, kwargs = post.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["json"]["model"] == "deepseek-chat"
    assert kwargs["timeout"] == 60


def test_generate_counts_removed_items(tmp_path):
    advice = {"training": [{"title": "Córrer", "detail": "", "exercises": []}, GOOD_ADVICE["training"][1]], "nutrition": []}
    result = generate(tmp_path, ok_post(advice))
    assert result["removed"] == 1
    assert len(result["advice"]["training"]) == 1


def _failing_posts():
    def http_500(url, **kw):
        return FakeResponse(status_code=500, body={"error": "x"})

    def bad_json(url, **kw):
        return FakeResponse(content="not json at all")

    def wrong_shape(url, **kw):
        return FakeResponse(content='{"training": "x"}')

    def timeout(url, **kw):
        raise TimeoutError("slow")

    def malformed_body(url, **kw):
        return FakeResponse(body={"nope": 1})

    return [http_500, bad_json, wrong_shape, timeout, malformed_body]


@pytest.mark.parametrize("post", _failing_posts())
def test_generate_failure_keeps_previous_cache(tmp_path, post):
    generate(tmp_path, ok_post())
    cache = tmp_path / "advice.json"
    before = cache.read_bytes()
    with pytest.raises(AdviceError):
        generate(tmp_path, post)
    assert cache.read_bytes() == before


def test_generate_failure_without_previous_cache_creates_nothing(tmp_path):
    with pytest.raises(AdviceError):
        generate(tmp_path, _failing_posts()[0])
    assert not (tmp_path / "advice.json").exists()


@pytest.mark.parametrize("api_key", [None, ""])
def test_no_api_key_raises_without_calling_post(tmp_path, api_key):
    post = ok_post()
    with pytest.raises(AdviceError, match="DEEPSEEK"):
        generate(tmp_path, post, api_key=api_key)
    assert post.calls == []


def test_call_llm_error_does_not_leak_api_key():
    def post(url, **kw):
        raise RuntimeError("connection failed with Bearer sk-secret")

    with pytest.raises(AdviceError) as exc:
        call_llm([{"role": "user", "content": "x"}], api_key="sk-secret", post=post)
    assert "sk-secret" not in str(exc.value)


def test_load_cached_missing_or_corrupt_returns_none(tmp_path):
    assert load_cached(tmp_path / "advice.json") is None
    (tmp_path / "advice.json").write_text("{broken", encoding="utf-8")
    assert load_cached(tmp_path / "advice.json") is None
    (tmp_path / "advice.json").write_text('{"unexpected": 1}', encoding="utf-8")
    assert load_cached(tmp_path / "advice.json") is None


def test_generated_advice_is_marked_as_english(tmp_path):
    assert generate(tmp_path, ok_post())["lang"] == "en"


def test_cached_advice_without_the_english_marker_is_ignored(tmp_path):
    legacy = {
        "generated_at": "2026-11-07T12:22:00",
        "removed": 0,
        "advice": {"training": [{"title": "Força", "detail": "", "exercises": []}], "nutrition": []},
    }
    (tmp_path / "advice.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert load_cached(tmp_path / "advice.json") is None
