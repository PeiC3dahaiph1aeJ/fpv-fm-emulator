"""Work out which IIO devices a given Pluto-class board actually exposes.

pyadi's ``adi.Pluto`` hard-codes the device names of the stock Analog Devices
image::

    _control_device_name = "ad9361-phy"
    _rx_data_device_name = "cf-ad9361-lpc"
    _tx_data_device_name = "cf-ad9361-dds-core-lpc"

Alternative firmwares and FPGA images (Tezuka builds, maia-hdl, PlutoSky boards …)
do not have to use those names, and some RX-oriented FPGA images have no transmit
DMA at all. When a name does not match, the failure surfaces from deep inside
pyadi as ``argument of type 'NoneType' is not iterable`` — which says nothing
about the real cause and cost a user an evening.

So: look at the context, find the control device and the RX/TX DMA devices by
their shape rather than by their name, and say plainly when there is no transmit
path to be had.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .i18n import t

#: names used by the stock image — tried first so nothing changes for stock boards
STOCK_PHY = "ad9361-phy"
STOCK_RX = "cf-ad9361-lpc"
STOCK_TX = "cf-ad9361-dds-core-lpc"


@dataclass
class IioLayout:
    """Which device does what on this particular board."""

    phy: Optional[str] = None          # the transceiver control device
    rx: Optional[str] = None           # RX DMA (buffer capable, input)
    tx: Optional[str] = None           # TX DMA (buffer capable, output)
    devices: List[str] = field(default_factory=list)
    is_stock: bool = False

    @property
    def can_transmit(self) -> bool:
        return bool(self.phy and self.tx)

    def describe(self) -> str:
        lines = [t("IIO devices: {list}", list=", ".join(self.devices) or "—")]
        lines.append(t("  control: {name}", name=self.phy or "—"))
        lines.append(t("  RX DMA:  {name}", name=self.rx or "—"))
        lines.append(t("  TX DMA:  {name}", name=self.tx or "—"))
        if not self.can_transmit:
            lines.append(t("  -> this image has no transmit path; the emulator "
                           "cannot use it (an RX-only FPGA image such as maia-hdl "
                           "does this). A stock Pluto image is required."))
        elif not self.is_stock:
            lines.append(t("  -> non-standard names; the emulator adapts to them."))
        return "\n".join(lines)


def _has_buffer_channels(dev, output: bool) -> bool:
    """True if the device has scan elements in the requested direction.

    That is what makes a device a DMA endpoint rather than a control device, and
    it is the property we can rely on when the names differ.
    """
    try:
        for ch in dev.channels:
            if bool(ch.output) is output and getattr(ch, "scan_element", False):
                return True
    except Exception:
        pass
    return False


def detect_layout(ctx) -> IioLayout:
    """Inspect an open ``iio.Context`` and report its layout."""
    names: List[str] = []
    for dev in ctx.devices:
        names.append(dev.name or dev.id or "?")
    layout = IioLayout(devices=names)

    by_name = {}
    for dev in ctx.devices:
        key = dev.name or dev.id
        if key:
            by_name[key] = dev

    # control device: the stock name first, then anything that looks like a phy
    if STOCK_PHY in by_name:
        layout.phy = STOCK_PHY
    else:
        for name, dev in by_name.items():
            low = name.lower()
            if "phy" in low or "ad936" in low or "ad9364" in low:
                layout.phy = name
                break

    # DMA endpoints: prefer the stock names, else find them by shape
    if STOCK_RX in by_name and _has_buffer_channels(by_name[STOCK_RX], output=False):
        layout.rx = STOCK_RX
    else:
        for name, dev in by_name.items():
            if name != layout.phy and _has_buffer_channels(dev, output=False):
                layout.rx = name
                break

    if STOCK_TX in by_name and _has_buffer_channels(by_name[STOCK_TX], output=True):
        layout.tx = STOCK_TX
    else:
        for name, dev in by_name.items():
            if name != layout.phy and _has_buffer_channels(dev, output=True):
                layout.tx = name
                break

    layout.is_stock = (layout.phy == STOCK_PHY and layout.rx == STOCK_RX
                       and layout.tx == STOCK_TX)
    return layout


def pluto_class_for(layout: IioLayout):
    """Return an ``adi.Pluto`` subclass wired to this board's device names.

    Stock boards get ``adi.Pluto`` unchanged, so nothing moves for them.
    """
    import adi  # noqa: WPS433 — hardware dependency

    if layout.is_stock or not layout.can_transmit:
        return adi.Pluto

    class _AdaptedPluto(adi.Pluto):
        _control_device_name = layout.phy or STOCK_PHY
        _rx_data_device_name = layout.rx or STOCK_RX
        _tx_data_device_name = layout.tx or STOCK_TX

    _AdaptedPluto.__name__ = "Pluto[%s]" % (layout.tx or "?")
    return _AdaptedPluto
