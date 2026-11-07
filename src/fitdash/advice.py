from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

import requests

from .fsutil import atomic_write_text
from .metrics import CATEGORIES, CATEGORY_LABELS
from .models import WeekSummary
from .profile import GOAL, RESTRICTIONS, Profile, bmi, bmr, violates_restrictions

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
ADVICE_LANG = "en"

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class AdviceError(Exception):
    """Advice could not be generated. The message is safe to show."""


def _gap_line(g: dict) -> str:
    if g["status"] == "missing":
        detail = f"not started (target {g['target_minutes']} min/week)"
    else:
        detail = (
            f"average {g['avg_minutes']:.0f} min/week vs target {g['target_minutes']}"
            + (f", short by {g['gap_minutes']:.0f}" if g["status"] == "short" else ", on target")
        )
    return f"- {g['label']}: {detail}"


def build_prompt(
    profile: Profile,
    weeks: list[WeekSummary],
    alerts: list[str],
    avg_kcal: float | None,
    gaps: list[dict],
    lab_lines: list[str],
) -> list[dict]:
    restrictions = "\n".join(f"- {r}" for r in RESTRICTIONS)
    system = (
        "You are a careful strength and nutrition coach. Reply ONLY with one JSON object shaped as "
        '{"training": [{"title": str, "detail": str, "exercises": [str]}], '
        '"nutrition": [{"title": str, "detail": str}]}. '
        "Write everything in English.\n"
        f"User goal: {GOAL}\n"
        f"MANDATORY restrictions:\n{restrictions}\n"
        "Never mention forbidden exercises, not even as a warning or as an alternative.\n"
        f"Basal metabolic rate: {bmr(profile):.0f} kcal/day. The user does not log meals, so nutrition "
        "guidance is orientative: suggest a reasonable daily calorie range (a mild deficit) relative to "
        "the average daily expenditure and say it is guidance, not medical advice. Bloodwork flags are "
        "context only: do not diagnose, and tell the user to discuss supplements or medication with a doctor."
    )
    week_lines = []
    for w in weeks:
        parts = [
            f"{CATEGORY_LABELS[c]} {w.sessions[c]}x/{w.minutes[c]:.0f}min"
            for c in CATEGORIES
            if w.sessions.get(c)
        ]
        week_lines.append(f"- week of {w.week_start}: " + (", ".join(parts) or "no activity"))
    gap_lines = "\n".join(_gap_line(g) for g in gaps) or "- no data"
    lab_block = "\n".join(f"- {line}" for line in lab_lines) or "- No bloodwork provided."
    energy = f"{avg_kcal:.0f} kcal" if avg_kcal else "unknown"
    user = (
        f"Profile: male, {profile.age} years, {profile.weight_kg:g} kg, {profile.height_cm:g} cm, "
        f"BMI {bmi(profile):.1f}.\n"
        "Weekly activity (type sessions/minutes):\n" + "\n".join(week_lines) + "\n"
        f"Gaps against weekly targets (last 4 complete weeks):\n{gap_lines}\n"
        "Alerts: " + ("; ".join(alerts) or "none") + "\n"
        f"Bloodwork flags (out of range):\n{lab_block}\n"
        f"Average daily energy expenditure: {energy}.\n"
        "Propose 3 to 5 training options that close the gaps above and fit how the user trains "
        "(no rigid routine), plus nutrition guidance."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _text(value, field: str, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise AdviceError(f"The LLM response is not valid (field '{field}').")
    return value


def parse_advice(text: str) -> dict:
    cleaned = (text or "").strip()
    fenced = _FENCE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except ValueError:
        raise AdviceError("The LLM response is not valid JSON.") from None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("training"), list)
        or not isinstance(data.get("nutrition"), list)
    ):
        raise AdviceError("The LLM response does not have the expected shape.")
    training = []
    for item in data["training"]:
        if not isinstance(item, dict):
            raise AdviceError("The LLM response does not have the expected shape.")
        exercises = item.get("exercises") or []
        if not isinstance(exercises, list) or not all(isinstance(e, str) for e in exercises):
            raise AdviceError("The LLM response is not valid (field 'exercises').")
        training.append(
            {
                "title": _text(item.get("title"), "title", True),
                "detail": _text(item.get("detail"), "detail", False),
                "exercises": exercises,
            }
        )
    nutrition = []
    for item in data["nutrition"]:
        if not isinstance(item, dict):
            raise AdviceError("The LLM response does not have the expected shape.")
        nutrition.append(
            {
                "title": _text(item.get("title"), "title", True),
                "detail": _text(item.get("detail"), "detail", False),
            }
        )
    return {"training": training, "nutrition": nutrition}


def enforce_restrictions(advice: dict) -> tuple[dict, int]:
    """Drop every training/nutrition item that recommends something unsafe.

    Strict on purpose: title, detail and exercises are all checked, with no
    "negation" exceptions ("if you have no bike, go running" must not slip through).
    """
    cleaned = copy.deepcopy(advice)
    removed = 0
    for section in ("training", "nutrition"):
        kept = []
        for item in cleaned[section]:
            texts = [item["title"], item["detail"], *item.get("exercises", [])]
            if any(violates_restrictions(t) for t in texts):
                removed += 1
            else:
                kept.append(item)
        cleaned[section] = kept
    return cleaned, removed


def call_llm(messages: list[dict], *, api_key: str | None, post=requests.post) -> str:
    if not api_key:
        raise AdviceError("Missing DEEPSEEK_API_KEY in dashboard/.env.")
    try:
        response = post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.4,
            },
            timeout=60,
        )
    except Exception:
        raise AdviceError("Could not reach DeepSeek. Try again later.") from None
    if response.status_code != 200:
        raise AdviceError(f"DeepSeek replied with HTTP error {response.status_code}.")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        content = None
    if not isinstance(content, str) or not content.strip():
        raise AdviceError("DeepSeek returned an empty or unexpected response.")
    return content


def generate_advice(
    profile: Profile,
    weeks: list[WeekSummary],
    alerts: list[str],
    avg_kcal: float | None,
    gaps: list[dict],
    lab_lines: list[str],
    *,
    cache_path: Path,
    api_key: str | None,
    post=requests.post,
) -> dict:
    messages = build_prompt(profile, weeks, alerts, avg_kcal, gaps, lab_lines)
    text = call_llm(messages, api_key=api_key, post=post)
    advice, removed = enforce_restrictions(parse_advice(text))
    payload = {
        "lang": ADVICE_LANG,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "removed": removed,
        "advice": advice,
    }
    atomic_write_text(Path(cache_path), json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def load_cached(cache_path: Path) -> dict | None:
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    advice = data.get("advice") if isinstance(data, dict) else None
    if not isinstance(advice, dict) or not isinstance(data.get("generated_at"), str):
        return None
    if data.get("lang") != ADVICE_LANG:  # advice written before the English prompt: regenerate
        return None
    if not isinstance(advice.get("training"), list) or not isinstance(advice.get("nutrition"), list):
        return None
    return data
