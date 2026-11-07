from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    weight_kg: float = 80.0
    height_cm: float = 188.0
    age: int = 46
    sex: str = "male"


PROFILE = Profile()


def bmi(p: Profile) -> float:
    return p.weight_kg / ((p.height_cm / 100) ** 2)


def bmr(p: Profile) -> float:
    """Mifflin-St Jeor basal metabolic rate, kcal/day."""
    base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    return base + (5 if p.sex == "male" else -161)


GOAL = "Lose fat and build muscle without loading the knees."

# Assumption, not a medical prescription: edit to taste.
# Active energy (Garmin "active kilocalories", i.e. above basal) wanted per day.
ACTIVE_KCAL_TARGET = 500

# Weekly minutes wanted per training type (insertion order = display order).
# strength 2 x 45 min; bike + walking = 150 min of low-impact aerobic work;
# rehab = physio session + 2-3 short home sessions; alt = one 45-min
# swim/row/elliptical session as a knee-friendly variation.
WEEKLY_TARGETS: dict[str, int] = {
    "strength": 90,
    "cardio": 60,
    "walking": 90,
    "rehab": 90,
    "alt": 45,
}

RESTRICTIONS: list[str] = [
    "Knee injuries: no impact exercise (no running, jumping or jogging).",
    "No loaded leg exercises (no leg press, loaded extensions, deadlifts or lunges).",
    "Squats only if shallow and unloaded (bodyweight).",
    "Allowed: upper-body strength, core, walking, low-resistance stationary bike and mobility work.",
]

_IMPACT = re.compile(
    r"\b(?:"
    r"run|jog|sprint|jump|hop|burpee|"
    r"corr(?:e|er|ent|iendo|edor|edora)\b|"
    r"salt|trot|cursa|carrera|footing|"
    r"pliom|plyo|"
    r"step[- ]?ups?\b|calaix|comba\b|skipping|"
    r"lunge|zancad|gambad|estocad|afondos?\b|"
    r"leg[- ]?press|prensa|premsa|"
    r"dead[- ]?lift|pes mort|peso muerto|"
    r"leg extension|extensi\w* de (?:cames|piernas|quadriceps|cuadriceps)|"
    r"leg curl|curl femoral|hip thrust"
    r")"
)
_SQUAT = re.compile(r"\b(?:squat|sentadilla|esquat)")
_CALF = re.compile(r"\b(?:calf raise|bessons|gemelos)")
_LOADED = re.compile(
    r"\b(?:amb pes|con peso|with weight|weighted|loaded|barbell|barra|mancuern|dumbbell|"
    r"kettlebell|goblet|carrega|carga|halter|disc\b|llast|lastr|motxill|mochila|smith|"
    r"multipower|pesa\b|pesas\b|pesos\b|manuell)"
)
_DEEP = re.compile(r"\b(?:complet|bulgar|pistol|sissy|deep|full|atg|profund)")
_SHALLOW_PHRASE = re.compile(r"poc[o]? profund\w*")
_SHALLOW_MARKERS = ("poc profund", "poco profund", "shallow", "parcial")
_UNLOADED_MARKERS = ("sense pes", "sin peso", "bodyweight", "unloaded", "sense carrega", "sin carga")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def violates_restrictions(text: str) -> bool:
    """True if ``text`` recommends something unsafe for the user's knees.

    Squats are allowed only when explicitly both shallow AND unloaded.
    """
    t = _normalize(text)
    if _IMPACT.search(t):
        return True
    loaded = _LOADED.search(t) is not None
    if _CALF.search(t) and loaded:
        return True
    if _SQUAT.search(t):
        shallow = any(m in t for m in _SHALLOW_MARKERS)
        unloaded = any(m in t for m in _UNLOADED_MARKERS)
        deep = _DEEP.search(_SHALLOW_PHRASE.sub(" ", t)) is not None
        return loaded or deep or not (shallow and unloaded)
    return False
