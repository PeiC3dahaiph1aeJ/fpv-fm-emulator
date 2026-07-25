"""Scenario file loading, validation and defaults."""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

from .i18n import t

_VALID_TYPES = {"static", "sweep", "power_ramp", "multi_drone"}

_SCEN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "scenarios"
)


def load_scenario(path: str) -> Dict[str, Any]:
    """Load and validate a scenario. ``path`` is a file or a name from config/scenarios."""
    if not os.path.exists(path):
        cand = os.path.join(_SCEN_DIR, path)
        if not cand.endswith((".yaml", ".yml")):
            cand += ".yaml"
        if os.path.exists(cand):
            path = cand
        else:
            raise FileNotFoundError(t("Scenario not found: {path}", path=path))
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    validate_scenario(data)
    return data


def validate_scenario(data: Dict[str, Any]) -> None:
    stype = str(data.get("type", "")).lower()
    if stype not in _VALID_TYPES:
        raise ValueError(
            t("Field 'type' must be one of {types}, got '{value}'",
              types=_VALID_TYPES, value=stype)
        )
    if stype not in data:
        raise ValueError(
            t("Missing block '{stype}:' for a scenario of type '{stype}'", stype=stype)
        )


def list_scenarios() -> Dict[str, str]:
    """Return {name: path} for the scenarios in config/scenarios."""
    out: Dict[str, str] = {}
    if os.path.isdir(_SCEN_DIR):
        for fn in sorted(os.listdir(_SCEN_DIR)):
            if fn.endswith((".yaml", ".yml")):
                out[os.path.splitext(fn)[0]] = os.path.join(_SCEN_DIR, fn)
    return out
