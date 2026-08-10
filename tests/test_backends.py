"""Sample-rate handling, and what each firmware profile is allowed to try.

``sdr.sample_rate = x`` is ad9361_set_bb_rate() from libad9361: it loads an
interpolating FIR filter alongside the rate. A Tezuka build on a Nano PlutoSDR
refuses that at 20 MSPS with a bare EINVAL, while the very same board advertises
2.083–61.44 MSPS as available — the AD9361's range with the FIR switched off.

Both boards stay in use, so the tests that matter most here are the ones proving
the default path did not move for the Pluto+.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.backends import (
    allowed_sample_rates,
    apply_sample_rate,
    set_sample_rate_without_fir,
)
from fpv_emulator.firmware import ADI, AUTO, TEZUKA

RATES_AVAILABLE = "[2083333 1 61440000]"       # verbatim from the Nano


class FakeAttr:
    """An IIO attribute; ``snap`` mimics a board quantising what it is given."""

    def __init__(self, value, snap=None):
        self._value = str(value)
        self._snap = snap

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = str(self._snap(float(v))) if self._snap else str(v)


class FakeChan:
    def __init__(self, cid, output, attrs):
        self.id = cid
        self.output = output
        self.attrs = attrs


class FakeDev:
    def __init__(self, channels):
        self.channels = list(channels)


class FakeCtx:
    def __init__(self, name, dev):
        self._name, self._dev = name, dev

    def find_device(self, name):
        if name != self._name:
            raise KeyError(name)
        return self._dev


class FakeSdr:
    """A board. ``takes_fir`` is the one thing the two real boards disagree on."""

    def __init__(self, ctx, takes_fir=True):
        self.ctx = ctx
        self.takes_fir = takes_fir
        self.pyadi_attempts = 0
        self.rate = None

    @property
    def sample_rate(self):
        return self.rate

    @sample_rate.setter
    def sample_rate(self, value):
        self.pyadi_attempts += 1
        if not self.takes_fir:
            raise OSError(22, "Invalid argument")
        self.rate = int(value)


def _board(snap=None, *, takes_fir=True, with_fir=True, with_rate=True):
    chans = []
    if with_fir:
        chans.append(FakeChan("out", False, {"voltage_filter_fir_en": FakeAttr(1)}))
    if with_rate:
        chans.append(FakeChan("voltage0", False,
                              {"sampling_frequency": FakeAttr(3000000, snap)}))
    # the TX side of the same channel pair: it publishes the ranges, and it must
    # not be the one written — RX/TX share the AD9361 clock, the rate lives on in
    chans.append(FakeChan("voltage0", True, {
        "rf_bandwidth": FakeAttr(18000000),
        "sampling_frequency_available": FakeAttr(RATES_AVAILABLE),
    }))
    dev = FakeDev(chans)
    return FakeSdr(FakeCtx("ad9361-phy", dev), takes_fir=takes_fir), dev


def _attr(dev, cid, output, name):
    for ch in dev.channels:
        if ch.id == cid and ch.output is output:
            return ch.attrs[name].value
    raise AssertionError("no such channel")


def _fir(dev):
    return _attr(dev, "out", False, "voltage_filter_fir_en")


# --------------------------- the unfiltered write --------------------------
def test_the_rate_is_written_and_the_fir_is_switched_off():
    sdr, dev = _board()
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) == 20e6
    assert _attr(dev, "voltage0", False, "sampling_frequency") == "20000000"
    assert _fir(dev) == "0"


def test_a_small_quantisation_step_is_accepted():
    """Boards land on their own grid; a fraction of a percent is normal."""
    sdr, _ = _board(snap=lambda v: int(v) - 7)
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) == 19999993.0


def test_a_rate_that_lands_somewhere_else_is_rejected():
    """Silently transmitting at the wrong rate moves the 15.625 kHz line rate the
    detector matches on — a signal that looks right on a spectrum analyser and is
    invisible to the detector is worse than a failure."""
    sdr, _ = _board(snap=lambda v: 15360000)
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) is None


def test_a_board_without_the_rate_attribute_reports_failure():
    sdr, _ = _board(with_rate=False)
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) is None


def test_a_board_without_the_fir_attribute_still_sets_the_rate():
    """An image with no FIR control has nothing to disable — that is not an error."""
    sdr, dev = _board(with_fir=False)
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) == 20e6
    assert _attr(dev, "voltage0", False, "sampling_frequency") == "20000000"


def test_a_wrong_phy_name_does_not_raise():
    sdr, _ = _board()
    assert set_sample_rate_without_fir(sdr, "not-there", 20e6) is None


def test_the_published_range_is_read_from_the_tx_channel():
    sdr, _ = _board()
    assert allowed_sample_rates(sdr, "ad9361-phy") == (2083333.0, 61440000.0)


def test_a_board_that_publishes_no_range_gives_none():
    """No range must mean 'do not check', never 'the range is empty'."""
    sdr, dev = _board()
    dev.channels[-1].attrs.pop("sampling_frequency_available")
    assert allowed_sample_rates(sdr, "ad9361-phy") is None


# --------------------------- profiles --------------------------------------
def test_auto_on_a_board_that_takes_the_filter_leaves_it_alone():
    """The Pluto+ path. Nothing about it may move: it works today."""
    sdr, dev = _board(takes_fir=True)
    assert apply_sample_rate(sdr, "ad9361-phy", 20e6, AUTO) is None
    assert sdr.pyadi_attempts == 1 and sdr.rate == 20000000
    assert _fir(dev) == "1", "the FIR was disabled on a board that accepts it"


def test_auto_on_a_board_that_refuses_falls_back_and_says_so():
    sdr, dev = _board(takes_fir=False)
    note = apply_sample_rate(sdr, "ad9361-phy", 20e6, AUTO)
    assert sdr.pyadi_attempts == 1, "auto must ask first, not assume"
    assert _fir(dev) == "0"
    assert note and "refused" in note


def test_the_tezuka_profile_does_not_make_the_failing_attempt():
    sdr, dev = _board(takes_fir=False)
    note = apply_sample_rate(sdr, "ad9361-phy", 20e6, TEZUKA)
    assert sdr.pyadi_attempts == 0
    assert _fir(dev) == "0"
    assert note and "Tezuka" in note


def test_the_tezuka_profile_skips_the_filter_even_where_it_would_work():
    """A forced profile must be honoured, and must not claim the board refused."""
    sdr, dev = _board(takes_fir=True)
    note = apply_sample_rate(sdr, "ad9361-phy", 20e6, TEZUKA)
    assert sdr.pyadi_attempts == 0 and _fir(dev) == "0"
    assert note and "refused" not in note


def test_the_adi_profile_does_not_fall_back_and_names_itself():
    """Otherwise the operator reads the failure as the old EINVAL bug returning."""
    sdr, dev = _board(takes_fir=False)
    with pytest.raises(RuntimeError) as exc:
        apply_sample_rate(sdr, "ad9361-phy", 20e6, ADI)
    assert _fir(dev) == "1", "it fell back despite the profile"
    assert "Analog Devices" in str(exc.value)
    assert RATES_AVAILABLE in str(exc.value), "the range it accepts must be quoted"


def test_the_adi_profile_works_where_the_filter_works():
    sdr, _ = _board(takes_fir=True)
    assert apply_sample_rate(sdr, "ad9361-phy", 20e6, ADI) is None


def test_an_unknown_profile_behaves_as_auto():
    """It arrives from a persisted setting; auto is the answer that fits any board."""
    sdr, _ = _board(takes_fir=False)
    note = apply_sample_rate(sdr, "ad9361-phy", 20e6, "nonsense")
    assert sdr.pyadi_attempts == 1 and note


def test_a_failure_on_every_allowed_route_raises_naming_the_profile():
    sdr, _ = _board(takes_fir=False, with_rate=False)
    with pytest.raises(RuntimeError) as exc:
        apply_sample_rate(sdr, "ad9361-phy", 20e6, TEZUKA)
    assert "Tezuka" in str(exc.value)
