"""Scenario file loading, validation and defaults."""
from __future__ import annotations

import os
import warnings
from typing import Any, Dict, Mapping

import yaml

from .i18n import t

_VALID_TYPES = {"static", "sweep", "power_ramp", "multi_drone"}

# tx_hardwaregain limits (Pluto convention: 0 dB = maximum, -89 dB = minimum).
# The AD936x accepts -89.75..0 only; anything outside makes libiio return
# OSError [Errno 22] halfway through a run, so every power value is checked
# here (at load time) and clamped at use time — see ``clamp_gain``.
GAIN_MIN_DB = -89.0
GAIN_MAX_DB = 0.0

_SCEN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "scenarios"
)


def clamp_gain(gain_db: float) -> float:
    """Clamp a power value into the tx_hardwaregain range (-89..0 dB)."""
    return max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(gain_db)))


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


# -- field helpers ----------------------------------------------------------
def _num(block: Mapping[str, Any], key: str, default: float) -> float:
    """Read a numeric field (YAML often hands us '8.0e6' as a string)."""
    value = block.get(key)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            t("Field '{field}' must be a number, got '{value}'", field=key, value=value)
        ) from None


def _require_positive(block: Mapping[str, Any], key: str, default: float) -> float:
    value = _num(block, key, default)
    if value <= 0:
        raise ValueError(
            t("Field '{field}' must be greater than 0, got {value}", field=key, value=value)
        )
    return value


def _require_gain(block: Mapping[str, Any], key: str, default: float) -> float:
    value = _num(block, key, default)
    if not GAIN_MIN_DB <= value <= GAIN_MAX_DB:
        raise ValueError(
            t("Field '{field}' must be within {min}..{max} dB (0 = maximum power), got {value}",
              field=key, min=GAIN_MIN_DB, max=GAIN_MAX_DB, value=value)
        )
    return value


def _validate_sweep(cfg: Mapping[str, Any]) -> None:
    selectors = [k for k in ("ranges", "channels", "band", "group", "freq_list_mhz")
                 if k in cfg]
    if not selectors:
        raise ValueError(t("sweep: specify channels | band | group | freq_list_mhz"))
    for key in ("channels", "freq_list_mhz", "ranges", "band", "group"):
        if key in cfg and not cfg[key]:
            raise ValueError(t("Field '{field}' must not be empty", field=key))
    for rng in cfg.get("ranges") or []:
        if not isinstance(rng, dict):
            raise ValueError(
                t("Field 'ranges' must be a list of start_mhz/stop_mhz/step_mhz items")
            )
        for required in ("start_mhz", "stop_mhz"):
            if rng.get(required) is None:
                raise ValueError(
                    t("Field '{field}' is required in 'ranges'", field=required)
                )
        start = _require_positive(rng, "start_mhz", 0.0)
        stop = _require_positive(rng, "stop_mhz", 0.0)
        _require_positive(rng, "step_mhz", 50.0)
        if stop < start:
            raise ValueError(
                t("Field 'stop_mhz' ({stop}) must not be below 'start_mhz' ({start})",
                  stop=stop, start=start)
            )
    _require_positive(cfg, "dwell_s", 2.0)


def _validate_power_ramp(cfg: Mapping[str, Any]) -> None:
    _require_gain(cfg, "start_db", -40.0)
    _require_gain(cfg, "end_db", 0.0)
    _require_positive(cfg, "duration_s", 10.0)


#: Below this the realistic FPV profile does not fit — the 4.43 MHz colour
#: subcarrier needs fs >= 2.2x it, and the FM skirts need room beyond that.
#: Verified on the bench: 20 MSPS with 7 MHz deviation is what gets recognised.
_MIN_USABLE_FS_HZ = 20e6


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
    block = data.get(stype)
    # 'sweep:' with nothing under it parses as None -> a bare TypeError deep in the engine
    if not isinstance(block, dict) or not block:
        raise ValueError(
            t("Block '{stype}:' must be a non-empty mapping", stype=stype)
        )

    signal = data.get("signal")
    if signal is not None:
        if not isinstance(signal, dict):
            raise ValueError(t("Block 'signal:' must be a mapping"))
        _require_gain(signal, "gain_db", -10.0)
        _require_positive(signal, "sample_rate", 20e6)
        _require_positive(signal, "deviation_pp_hz", 7e6)
        # Three of the shipped scenarios sat at 8-10 MSPS for months, left over from
        # before the colour work, and radiated a signal no detector was going to
        # recognise — strong on a spectrum analyser, invisible as FPV video. Nothing
        # said so, because a narrow signal is not an error. Say so now.
        fs = float(signal.get("sample_rate", 20e6) or 20e6)
        if fs < _MIN_USABLE_FS_HZ:
            warnings.warn(
                t("This scenario uses {fs} MSPS. The profile the detector recognises "
                  "needs at least {min} MSPS: below that the colour subcarrier "
                  "aliases and the occupied bandwidth is too narrow, so the carrier "
                  "reads as strong and is not recognised as video.",
                  fs=f"{fs / 1e6:.2f}", min=f"{_MIN_USABLE_FS_HZ / 1e6:.0f}"),
                stacklevel=2,
            )

    if stype == "sweep":
        _validate_sweep(block)
    elif stype == "power_ramp":
        _validate_power_ramp(block)
    elif stype == "multi_drone":
        if not block.get("drones"):
            raise ValueError(t("multi_drone: specify the 'drones' list"))


def list_scenarios() -> Dict[str, str]:
    """Return {name: path} for the scenarios in config/scenarios."""
    out: Dict[str, str] = {}
    if os.path.isdir(_SCEN_DIR):
        for fn in sorted(os.listdir(_SCEN_DIR)):
            if fn.endswith((".yaml", ".yml")):
                out[os.path.splitext(fn)[0]] = os.path.join(_SCEN_DIR, fn)
    return out
