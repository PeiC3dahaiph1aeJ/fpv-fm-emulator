"""Firmware profiles: which ways of talking to a Pluto-class board we may try.

Two boards are in use here and they disagree about one thing. Setting
``sdr.sample_rate`` in pyadi is libad9361's ``ad9361_set_bb_rate()``, which does
not only set a rate: it also loads an interpolating FIR filter and switches it
on. The Pluto+ on stock Analog Devices firmware takes that happily. A Nano
PlutoSDR running ``tezuka-v0.3.141592653`` refuses it with a bare EINVAL, while
advertising 2.083–61.44 MSPS as available — the range an AD9361 has with the FIR
*off*. The rate was never the problem; the filter is.

A profile therefore says what we are ALLOWED TO TRY, not what the board is:

    auto    try pyadi's way first, fall back to setting the rate with the FIR
            off if the board refuses. This is the behaviour that works on both
            boards and it stays the default.
    adi     pyadi's way only. A refusal is an error, not a fallback.
    tezuka  go straight to setting the rate with the FIR off, without the
            failed write first.

Deliberately NOT how this works: sniffing ``fw_version`` and picking a code path
from it. "Tezuka" is a firmware *builder* covering some ten different boards, so
the string does not predict the behaviour — and the reactive fallback already
discovers the truth by asking the board. The identity below is read only to
LABEL things for the operator and to warn when a manual override contradicts
what the board reports. It never gates anything on its own.

The vocabulary is kept clear of "stock"/"hacked" on purpose: those already mean
the AD9363-vs-AD9361 tuning range in config/bands.yaml, in the GUI's "HW range"
box and in ProbeResult.inferred_preset. Two selectors sharing a word would be
read as one axis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from .i18n import t

AUTO = "auto"
ADI = "adi"
TEZUKA = "tezuka"


@dataclass(frozen=True)
class Profile:
    """What a profile permits. Both flags false would leave no way to set a rate."""

    key: str
    try_pyadi_fir: bool      # attempt sdr.sample_rate (loads and enables a FIR)
    allow_fir_off: bool      # may write sampling_frequency with the FIR disabled


PROFILES: Dict[str, Profile] = {
    AUTO: Profile(AUTO, try_pyadi_fir=True, allow_fir_off=True),
    ADI: Profile(ADI, try_pyadi_fir=True, allow_fir_off=False),
    TEZUKA: Profile(TEZUKA, try_pyadi_fir=False, allow_fir_off=True),
}

#: order for menus and --help; auto first because it is the default
PROFILE_KEYS = (AUTO, ADI, TEZUKA)


def profile(key: Optional[str]) -> Profile:
    """The profile for ``key``, falling back to auto for anything unknown.

    An unrecognised value must not become a hard failure: it arrives from a
    persisted GUI setting or a hand-typed flag, and auto is the one choice that
    is right for every board.
    """
    return PROFILES.get((key or AUTO).strip().lower(), PROFILES[AUTO])


def profile_label(key: Optional[str]) -> str:
    """Human name for a profile key. The two firmware names are proper nouns."""
    k = profile(key).key
    return {AUTO: t("Auto"), ADI: "Analog Devices", TEZUKA: "Tezuka"}[k]


# --------------------------------------------------------------------------
#  identity — reported, never used to choose a code path
# --------------------------------------------------------------------------
#: fw_version prefixes we recognise. Anything else stays unknown on purpose:
#: claiming the wrong firmware is worse than admitting we cannot tell, and the
#: only thing this feeds is a label and a mismatch warning.
_FW_PATTERNS = (
    (TEZUKA, re.compile(r"^\s*tezuka", re.I)),
    (ADI, re.compile(r"^\s*v\d+\.\d+", re.I)),      # stock images: "v0.35", "v0.38"
)


@dataclass
class Firmware:
    """What the board says it runs. Empty ``key`` means we did not recognise it."""

    key: str = ""
    version: str = ""
    model: str = ""

    @property
    def known(self) -> bool:
        return bool(self.key)

    def describe(self) -> str:
        if not self.version:
            return t("Firmware: not reported")
        if self.known:
            return t("Firmware: {name} ({version})",
                     name=profile_label(self.key), version=self.version)
        return t("Firmware: {version} (not recognised)", version=self.version)


def identify(ctx) -> Firmware:
    """Read the firmware identity from an already-open ``iio.Context``.

    Takes a context the caller already has — opening a second one on a device
    that is transmitting is how the DMA ends up bound to a stale buffer.

    Note that a Pluto reports these attributes as empty strings rather than
    omitting them, so the value is what has to be tested, never the key.
    """
    try:
        attrs = getattr(ctx, "attrs", {}) or {}
        version = str(attrs.get("fw_version", "") or "").strip()
        model = str(attrs.get("hw_model", "") or "").strip()
    except Exception:
        return Firmware()
    for key, pattern in _FW_PATTERNS:
        if version and pattern.match(version):
            return Firmware(key=key, version=version, model=model)
    return Firmware(key="", version=version, model=model)


def mismatch_warning(chosen: Optional[str], fw: Firmware) -> Optional[str]:
    """Text to warn with when a manual profile contradicts the board, else None.

    Only fires on a positive identification. A board we could not recognise
    proves nothing about the operator's choice, and warning on "unknown" would
    train them to ignore the message.
    """
    key = profile(chosen).key
    if key == AUTO or not fw.known or fw.key == key:
        return None
    return t(
        "The firmware profile is set to {chosen}, but this board reports {actual} "
        "({version}). The profile is being applied as you selected it — set it to "
        "{auto} to let the board decide.",
        chosen=profile_label(key), actual=profile_label(fw.key),
        version=fw.version, auto=t("Auto"))
