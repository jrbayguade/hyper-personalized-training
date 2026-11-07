import pytest

from fitdash.profile import PROFILE, bmi, bmr, violates_restrictions, RESTRICTIONS


def test_bmr_and_bmi():
    assert bmr(PROFILE) == 1750.0
    assert round(bmi(PROFILE), 1) == 22.6


def test_restrictions_are_listed_for_the_prompt():
    assert RESTRICTIONS
    assert all(isinstance(r, str) and r for r in RESTRICTIONS)


@pytest.mark.parametrize(
    "text",
    [
        "Córrer 20 min",
        "JUMP squats",
        "prensa de piernas",
        "burpees",
        "salts a la corda",
        "sentadilla profunda",
        "esquat amb pes",
        "zancadas con mancuernas",
        "esquat poc profund amb pes",
    ],
)
def test_forbidden_terms_detected(text):
    assert violates_restrictions(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Sentadilla poc profunda sense pes",
        "Press de banca amb mancuernes",
        "Planxa abdominal",
        "Bici estàtica baixa resistència",
        "Caminar 40 min",
        "Press d'espatlla (shoulder press)",
    ],
)
def test_allowed_items(text):
    assert violates_restrictions(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Gambades enrere",
        "Zancades",
        "Estocades laterals",
        "Premsa de cames",
        "Extensions de quàdriceps",
        "Extensions de cames amb màquina",
        "Corre 10 min",
        "corrent suau",
        "corriendo 20 min",
        "Cursa contínua",
        "Footing",
        "Trote suau",
        "Trot lleuger",
        "Pliometria",
        "Pliomètrics",
        "Plyometrics",
        "Step-ups",
        "Pujades al calaix",
        "Skipping",
        "Comba",
        "leg-press",
        "Dead lift",
        "Curl femoral",
        "Hip thrust amb barra",
        "Elevacions de bessons amb pes",
        "Sentadilla profunda sense pes",
        "Sentadilla búlgara sense pes",
        "Pistol squat (bodyweight)",
        "Deep squat bodyweight",
        "Goblet squat poc profund",
        "Sentadilla poc profunda amb càrrega",
        "Sentadilla poc profunda amb halteres",
        "Sentadilla poc profunda amb disc",
        "Sentadilla poc profunda amb motxilla",
        "Sentadilla poc profunda",
        "Squat",
    ],
)
def test_forbidden_forms_the_llm_really_writes(text):
    assert violates_restrictions(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Sentadilla poc profunda (sense pes)",
        "Esquat poc profund sense pes, amb suport a la cadira",
        "Mantén una postura correcta d'esquena",
        "Respiració correcta durant la planxa",
        "Remada amb goma elàstica",
        "Elevacions laterals amb mancuernes",
        "Pont de glutis sense pes",
        "Bici estàtica amb resistència baixa",
    ],
)
def test_safe_forms_are_not_flagged(text):
    assert violates_restrictions(text) is False
