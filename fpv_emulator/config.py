"""Scenario file loading, validation and defaults."""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

_VALID_TYPES = {"static", "sweep", "power_ramp", "multi_drone"}

_SCEN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "scenarios"
)


def load_scenario(path: str) -> Dict[str, Any]:
    """Завантажити й перевірити сценарій. ``path`` — файл або ім'я з config/scenarios."""
    if not os.path.exists(path):
        cand = os.path.join(_SCEN_DIR, path)
        if not cand.endswith((".yaml", ".yml")):
            cand += ".yaml"
        if os.path.exists(cand):
            path = cand
        else:
            raise FileNotFoundError(f"Сценарій не знайдено: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    validate_scenario(data)
    return data


def validate_scenario(data: Dict[str, Any]) -> None:
    stype = str(data.get("type", "")).lower()
    if stype not in _VALID_TYPES:
        raise ValueError(
            f"Поле 'type' має бути одним з {_VALID_TYPES}, отримано '{stype}'"
        )
    if stype not in data:
        raise ValueError(f"Відсутній блок '{stype}:' для сценарію типу '{stype}'")


def list_scenarios() -> Dict[str, str]:
    """Повернути {ім'я: шлях} для сценаріїв у config/scenarios."""
    out: Dict[str, str] = {}
    if os.path.isdir(_SCEN_DIR):
        for fn in sorted(os.listdir(_SCEN_DIR)):
            if fn.endswith((".yaml", ".yml")):
                out[os.path.splitext(fn)[0]] = os.path.join(_SCEN_DIR, fn)
    return out
