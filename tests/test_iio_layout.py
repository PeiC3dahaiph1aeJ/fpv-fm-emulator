"""Device-layout detection — offline, with fake IIO contexts.

Alternative firmwares (Tezuka builds, PlutoSky boards) and custom FPGA images do
not use the device names pyadi hard-codes. A real user hit exactly this: a
FISHBall-PlutoSky running tezuka-v0.3.12 with a maia-hdl FPGA failed with
"argument of type 'NoneType' is not iterable" and worked once reflashed to stock.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.iio_layout import STOCK_PHY, STOCK_RX, STOCK_TX, detect_layout


class FakeChan:
    def __init__(self, output, scan_element):
        self.output = output
        self.scan_element = scan_element


class FakeDev:
    def __init__(self, name, channels=(), dev_id=None):
        self.name = name
        self.id = dev_id or name
        self.channels = list(channels)


class FakeCtx:
    def __init__(self, devices):
        self.devices = list(devices)


def _dma(name, output):
    """A buffer-capable device: what makes it a DMA endpoint is a scan element."""
    return FakeDev(name, [FakeChan(output, True), FakeChan(output, True)])


def _stock_ctx():
    return FakeCtx([
        FakeDev(STOCK_PHY, [FakeChan(False, False), FakeChan(True, False)]),
        _dma(STOCK_RX, output=False),
        _dma(STOCK_TX, output=True),
    ])


def test_stock_image_is_recognised_and_left_alone():
    layout = detect_layout(_stock_ctx())
    assert layout.is_stock and layout.can_transmit
    assert (layout.phy, layout.rx, layout.tx) == (STOCK_PHY, STOCK_RX, STOCK_TX)


def test_non_standard_names_are_found_by_shape():
    """A different FPGA image renames the DMA devices; find them by their channels."""
    ctx = FakeCtx([
        FakeDev("ad9361-phy", [FakeChan(False, False)]),
        _dma("axi-ad9361-rx", output=False),
        _dma("axi-ad9361-tx", output=True),
    ])
    layout = detect_layout(ctx)
    assert layout.can_transmit
    assert layout.rx == "axi-ad9361-rx"
    assert layout.tx == "axi-ad9361-tx"
    assert not layout.is_stock, "must not claim to be the stock layout"


def test_an_rx_only_image_reports_that_it_cannot_transmit():
    """maia-hdl and friends ship a receive-only design — say so, do not guess."""
    ctx = FakeCtx([
        FakeDev(STOCK_PHY, [FakeChan(False, False)]),
        _dma("cf-ad9361-lpc", output=False),
    ])
    layout = detect_layout(ctx)
    assert not layout.can_transmit
    assert layout.tx is None
    assert "no transmit path" in layout.describe()


def test_a_control_device_is_not_mistaken_for_a_dma_endpoint():
    """The phy has channels too, but no scan elements — it must not be picked."""
    ctx = FakeCtx([
        FakeDev(STOCK_PHY, [FakeChan(True, False), FakeChan(False, False)]),
        _dma("some-tx", output=True),
    ])
    layout = detect_layout(ctx)
    assert layout.tx == "some-tx"
    assert layout.phy == STOCK_PHY


def test_an_unnamed_device_does_not_crash_the_scan():
    """A device with name=None is what made pyadi raise the NoneType error."""
    dev = FakeDev(None, [FakeChan(True, True)], dev_id="iio:device3")
    ctx = FakeCtx([FakeDev(STOCK_PHY, [FakeChan(False, False)]), dev])
    layout = detect_layout(ctx)          # must not raise
    assert "iio:device3" in layout.devices


def test_describe_lists_the_devices():
    text = detect_layout(_stock_ctx()).describe()
    assert STOCK_TX in text and STOCK_RX in text
