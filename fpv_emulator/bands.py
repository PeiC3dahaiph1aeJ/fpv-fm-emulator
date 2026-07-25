"""FPV band / channel frequency tables and hardware-range guard.

Loads ``config/bands.yaml`` into a queryable table. Channel names are
case-insensitive (``"r1"`` == ``"R1"``). Frequencies are stored in Hz.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

_DEFAULT_BANDS_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "bands.yaml"
)


@dataclass(frozen=True)
class Channel:
    """A single tunable channel."""

    band: str          # band key, e.g. "R"
    name: str          # channel name, e.g. "R1"
    freq_hz: float     # centre frequency in Hz
    group: str         # coarse group, e.g. "5.8G"

    @property
    def freq_mhz(self) -> float:
        return self.freq_hz / 1e6

    @property
    def id(self) -> str:
        return self.name


@dataclass
class HardwareRange:
    name: str
    min_hz: float
    max_hz: float
    chip: str

    def covers(self, freq_hz: float) -> bool:
        return self.min_hz <= freq_hz <= self.max_hz


@dataclass
class BandTable:
    """All bands/channels plus hardware-range presets."""

    bands: Dict[str, dict] = field(default_factory=dict)
    hw_ranges: Dict[str, HardwareRange] = field(default_factory=dict)
    _by_channel: Dict[str, Channel] = field(default_factory=dict, repr=False)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "BandTable":
        hw = {}
        for key, r in (data.get("hardware_ranges") or {}).items():
            hw[key] = HardwareRange(
                name=key,
                min_hz=float(r["min_mhz"]) * 1e6,
                max_hz=float(r["max_mhz"]) * 1e6,
                chip=str(r.get("chip", "")),
            )
        table = cls(bands=dict(data.get("bands") or {}), hw_ranges=hw)
        table._index()
        return table

    def _index(self) -> None:
        self._by_channel = {}
        for band_key, band in self.bands.items():
            group = band.get("group", "")
            for ch_name, freq_mhz in (band.get("channels") or {}).items():
                ch = Channel(
                    band=band_key,
                    name=ch_name,
                    freq_hz=float(freq_mhz) * 1e6,
                    group=group,
                )
                # index by bare channel name and by "BAND:CHANNEL"
                self._by_channel[ch_name.upper()] = ch
                self._by_channel[f"{band_key.upper()}:{ch_name.upper()}"] = ch

    # -- queries ------------------------------------------------------------
    def channel(self, name: str) -> Channel:
        """Look up a channel by ``"R1"`` or ``"R:R1"`` (case-insensitive)."""
        key = name.strip().upper()
        if key not in self._by_channel:
            raise KeyError(
                f"Невідомий канал '{name}'. Доступні: {', '.join(sorted(self.list_channel_names()))}"
            )
        return self._by_channel[key]

    def list_channel_names(self) -> List[str]:
        return [k for k in self._by_channel if ":" not in k]

    def list_bands(self) -> List[str]:
        return list(self.bands.keys())

    def channels_in_band(self, band_key: str) -> List[Channel]:
        band = self.bands.get(band_key) or self.bands.get(band_key.upper())
        if band is None:
            raise KeyError(f"Невідомий банд '{band_key}'")
        group = band.get("group", "")
        return [
            Channel(band=band_key, name=n, freq_hz=float(f) * 1e6, group=group)
            for n, f in (band.get("channels") or {}).items()
        ]

    def channels_in_group(self, group: str) -> List[Channel]:
        out: List[Channel] = []
        for band_key, band in self.bands.items():
            if band.get("group") == group:
                out.extend(self.channels_in_band(band_key))
        return sorted(out, key=lambda c: c.freq_hz)

    def groups(self) -> List[str]:
        seen: List[str] = []
        for band in self.bands.values():
            g = band.get("group", "")
            if g and g not in seen:
                seen.append(g)
        return seen

    # -- hardware guard -----------------------------------------------------
    def hw_range(self, preset: str) -> HardwareRange:
        if preset not in self.hw_ranges:
            raise KeyError(f"Немає HW-пресету '{preset}'. Є: {list(self.hw_ranges)}")
        return self.hw_ranges[preset]

    def check_reachable(self, freq_hz: float, preset: str) -> Tuple[bool, Optional[str]]:
        """Return (ok, warning). warning is a UA-string when out of range."""
        rng = self.hw_range(preset)
        if rng.covers(freq_hz):
            return True, None
        return False, (
            f"Частота {freq_hz/1e6:.1f} МГц поза діапазоном {rng.chip} "
            f"({rng.min_hz/1e6:.0f}–{rng.max_hz/1e6:.0f} МГц). "
            + (
                "Потрібен AD9361-мод або зовнішній ап-конвертер."
                if preset == "stock"
                else "Перевірте налаштування/ап-конвертер."
            )
        )


def load_band_table(path: Optional[str] = None) -> BandTable:
    """Load the band table from YAML (defaults to ``config/bands.yaml``)."""
    path = path or _DEFAULT_BANDS_YAML
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return BandTable.from_dict(data)
