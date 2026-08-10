"""Firmware identity is a label, never a switch.

"Tezuka" is a firmware *builder* covering some ten different boards, so the
version string does not predict whether a given board will take pyadi's FIR
filter. The code discovers that by asking the board. What is read from
``fw_version`` only labels things for the operator and warns when a manual
override contradicts what the board says — so the tests here are mostly about
what identification must NOT do.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.firmware import (
    ADI,
    AUTO,
    TEZUKA,
    Firmware,
    identify,
    mismatch_warning,
    profile,
    profile_label,
)


class FakeCtx:
    def __init__(self, attrs=None):
        if attrs is not None:
            self.attrs = attrs


# --------------------------- identity --------------------------------------
def test_the_nano_is_recognised():
    fw = identify(FakeCtx({"fw_version": "tezuka-v0.3.141592653",
                           "hw_model": "Nano PlutoSDR (Z7010-AD9361)"}))
    assert fw.key == TEZUKA and fw.known
    assert "Tezuka" in fw.describe() and "141592653" in fw.describe()


def test_an_older_tezuka_build_is_the_same_firmware():
    """The suffix moves between builds; only the prefix is stable."""
    assert identify(FakeCtx({"fw_version": "tezuka-v0.3.12"})).key == TEZUKA


def test_a_stock_image_is_recognised_by_its_version_shape():
    assert identify(FakeCtx({"fw_version": "v0.35"})).key == ADI


def test_an_unfamiliar_firmware_is_reported_as_unrecognised_not_guessed():
    """Claiming the wrong firmware is worse than admitting we cannot tell."""
    fw = identify(FakeCtx({"fw_version": "plutosky-2024.1"}))
    assert fw.key == "" and not fw.known
    assert "plutosky-2024.1" in fw.describe()


def test_an_empty_version_is_not_a_firmware():
    """A Pluto reports these attributes as empty strings rather than omitting
    them, so the value has to be tested and never the key."""
    fw = identify(FakeCtx({"fw_version": "", "hw_model": ""}))
    assert not fw.known
    assert fw.describe() == Firmware().describe()


def test_a_context_without_attributes_does_not_raise():
    assert identify(FakeCtx()).key == ""
    assert identify(None).key == ""


# --------------------------- profiles --------------------------------------
def test_unknown_and_empty_profiles_resolve_to_auto():
    """They arrive from a persisted setting or a hand-typed flag; auto fits any board."""
    for value in ("", None, "nonsense", "STOCK"):
        assert profile(value).key == AUTO


def test_profile_keys_are_case_and_space_insensitive():
    assert profile("  Tezuka ").key == TEZUKA


def test_auto_is_allowed_both_routes_and_the_others_exactly_one_each():
    assert (profile(AUTO).try_pyadi_fir, profile(AUTO).allow_fir_off) == (True, True)
    assert (profile(ADI).try_pyadi_fir, profile(ADI).allow_fir_off) == (True, False)
    assert (profile(TEZUKA).try_pyadi_fir, profile(TEZUKA).allow_fir_off) == (False, True)


def test_the_labels_avoid_the_word_stock():
    """'stock' already means the AD9363 tuning range in bands.yaml and in the GUI's
    HW-range box; two selectors sharing a word get the wrong one set."""
    labels = " ".join(profile_label(k) for k in (AUTO, ADI, TEZUKA)).lower()
    assert "stock" not in labels and "hacked" not in labels


# --------------------------- the mismatch notice ---------------------------
def test_auto_never_warns():
    assert mismatch_warning(AUTO, Firmware(TEZUKA, "tezuka-v0.3.1")) is None


def test_a_profile_matching_the_board_does_not_warn():
    assert mismatch_warning(TEZUKA, Firmware(TEZUKA, "tezuka-v0.3.1")) is None


def test_an_unrecognised_board_does_not_warn():
    """Warning on 'unknown' would train the operator to ignore the message."""
    assert mismatch_warning(TEZUKA, Firmware("", "plutosky-2024.1")) is None
    assert mismatch_warning(ADI, Firmware()) is None


def test_a_contradiction_warns_and_says_it_is_still_being_applied():
    note = mismatch_warning(TEZUKA, Firmware(ADI, "v0.35"))
    assert note and "Tezuka" in note and "Analog Devices" in note and "v0.35" in note
