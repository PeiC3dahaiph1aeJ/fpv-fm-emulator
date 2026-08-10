"""Sample-rate handling on boards that will not take pyadi's FIR filter.

``sdr.sample_rate = x`` is ad9361_set_bb_rate() from libad9361: it loads an
interpolating FIR alongside the rate. A Tezuka build on a Nano PlutoSDR refuses
that at 20 MSPS with a bare EINVAL, while the very same board advertises
2.083–61.44 MSPS as available — the AD9361's range with the FIR switched off.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.backends import set_sample_rate_without_fir


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
    def __init__(self, ctx):
        self.ctx = ctx


def _board(snap=None, *, with_fir=True, with_rate=True):
    chans = []
    if with_fir:
        chans.append(FakeChan("out", False, {"voltage_filter_fir_en": FakeAttr(1)}))
    if with_rate:
        chans.append(FakeChan("voltage0", False,
                              {"sampling_frequency": FakeAttr(3000000, snap)}))
    # the TX side of the same channel pair: must not be the one written
    chans.append(FakeChan("voltage0", True, {"rf_bandwidth": FakeAttr(18000000)}))
    dev = FakeDev(chans)
    return FakeSdr(FakeCtx("ad9361-phy", dev)), dev


def _attr(dev, cid, output, name):
    for ch in dev.channels:
        if ch.id == cid and ch.output is output:
            return ch.attrs[name].value
    raise AssertionError("no such channel")


def test_the_rate_is_written_and_the_fir_is_switched_off():
    sdr, dev = _board()
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) == 20e6
    assert _attr(dev, "voltage0", False, "sampling_frequency") == "20000000"
    assert _attr(dev, "out", False, "voltage_filter_fir_en") == "0"


def test_a_small_quantisation_step_is_accepted():
    """Boards land on their own grid; a fraction of a percent is normal."""
    sdr, _ = _board(snap=lambda v: int(v) - 7)
    assert set_sample_rate_without_fir(sdr, "ad9361-phy", 20e6) == 19999993.0


def test_a_rate_that_lands_somewhere_else_is_rejected():
    """Silently transmitting at the wrong rate moves the line rate the detector
    matches on — a signal that looks right on a spectrum analyser and is invisible
    to the detector is worse than a failure."""
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
