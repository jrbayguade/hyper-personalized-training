"""Read a lab-results CSV (Spanish/Catalan export) and flag out-of-range markers.

Only flagged markers ever leave this module (as short prompt lines for the
advice step). Values are never exposed to the web page.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_RESULT = re.compile(rf"^\s*[<>]?\s*({_NUM})\s*$")
_RANGE = re.compile(rf"^\s*({_NUM})\s*-\s*({_NUM})")
_UPPER = re.compile(rf"^\s*<\s*({_NUM})")
_LOWER = re.compile(rf"^\s*>\s*({_NUM})")
_FLAGGED = {"low", "high", "abnormal"}
_LABEL = {"low": "LOW", "high": "HIGH", "abnormal": "ABNORMAL"}


@dataclass(frozen=True)
class Marker:
    section: str
    name: str
    value: float | None
    text: str
    unit: str
    reference: str
    status: str  # ok | low | high | abnormal | n/a


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _status(value: float | None, result_text: str, reference: str) -> str:
    ref = reference.strip()
    if value is None:
        if not result_text or not ref:
            return "n/a"
        if _RANGE.match(ref) or _UPPER.match(ref) or _LOWER.match(ref) or re.fullmatch(_NUM, ref):
            return "n/a"
        return "ok" if result_text.strip().lower().startswith(ref.lower()) else "abnormal"
    if (m := _RANGE.match(ref)) is not None:
        low, high = _num(m.group(1)), _num(m.group(2))
        return "low" if value < low else "high" if value > high else "ok"
    if (m := _UPPER.match(ref)) is not None:
        return "high" if value >= _num(m.group(1)) else "ok"
    if (m := _LOWER.match(ref)) is not None:
        return "low" if value <= _num(m.group(1)) else "ok"
    return "n/a"


def load_labs(path: Path) -> list[Marker]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    markers: list[Marker] = []
    for row in rows:
        name = (row.get("Paràmetre") or "").strip()
        result = row.get("Resultat")
        if not name or result is None:
            continue
        match = _RESULT.match(result)
        value = _num(match.group(1)) if match else None
        reference = (row.get("Valors de referència") or "").strip()
        markers.append(
            Marker(
                section=(row.get("Secció") or "").strip(),
                name=name,
                value=value,
                text=result.strip(),
                unit=(row.get("Unitats") or "").strip(),
                reference=reference,
                status=_status(value, result.strip(), reference),
            )
        )
    return markers


def summary(markers: list[Marker]) -> dict:
    checked = [m for m in markers if m.status != "n/a"]
    return {"checked": len(checked), "flagged": sum(1 for m in checked if m.status in _FLAGGED)}


def prompt_lines(markers: list[Marker]) -> list[str]:
    return [
        f"{m.name}: {m.text} {m.unit} (reference {m.reference}) - {_LABEL[m.status]}".replace("  ", " ")
        for m in markers
        if m.status in _FLAGGED
    ]


def find_labs_file(data_dir: Path) -> Path | None:
    matches = sorted(Path(data_dir).glob("analitica*.csv"))
    return matches[0] if matches else None
