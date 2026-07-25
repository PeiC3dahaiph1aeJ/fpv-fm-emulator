"""Detect a connected Pluto and report chip / tuning range.

Визначає підключений Pluto+, зчитує атрибути контексту (модель, прошивка, серійник)
та функціонально перевіряє реальні межі перестроювання TX (щоб зрозуміти, чи це
стоковий AD9363, чи AD9361-мод із доступом до 5.8 ГГц). Апаратні залежності
імпортуються ліниво — без пристрою функція повертає інформативний результат.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProbeResult:
    connected: bool
    uri: str
    attrs: Dict[str, str] = field(default_factory=dict)
    tx_lo_min_hz: Optional[float] = None
    tx_lo_max_hz: Optional[float] = None
    inferred_preset: Optional[str] = None      # "stock" | "hacked" | None
    reaches_5g8: bool = False
    messages: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def summary(self) -> str:
        if not self.connected:
            return f"Pluto не знайдено ({self.uri}): {self.error or 'немає контексту'}"
        lines = [f"Pluto підключено: {self.uri}"]
        for k in ("hw_model", "hw_serial", "fw_version"):
            if k in self.attrs:
                lines.append(f"  {k}: {self.attrs[k]}")
        if self.tx_lo_max_hz:
            lines.append(
                f"  TX LO: {self.tx_lo_min_hz/1e6:.0f} – {self.tx_lo_max_hz/1e6:.0f} МГц"
            )
        if self.inferred_preset:
            lines.append(f"  Ймовірний тип: {self.inferred_preset}")
        lines.append(
            f"  5.8 ГГц напряму: {'ТАК' if self.reaches_5g8 else 'НІ (потрібен мод/ап-конвертер)'}"
        )
        for m in self.messages:
            lines.append(f"  · {m}")
        return "\n".join(lines)


# частоти для функціональної перевірки меж (Гц)
_TEST_FREQS_HZ = [70e6, 325e6, 1200e6, 2450e6, 3300e6, 3800e6, 5800e6, 6000e6]


def probe(uri: str = "ip:192.168.2.1", do_range_test: bool = True) -> ProbeResult:
    res = ProbeResult(connected=False, uri=uri)

    # 1) контекст через libiio (pylibiio)
    ctx = None
    try:
        import iio  # type: ignore
        ctx = iio.Context(uri)
        res.connected = True
        for name in ("hw_model", "hw_serial", "fw_version", "uri"):
            try:
                res.attrs[name] = ctx.attrs.get(name, "")
            except Exception:
                pass
    except ImportError:
        res.messages.append("pylibiio (модуль iio) не встановлено — пропускаю читання контексту")
    except Exception as exc:
        res.error = str(exc)

    # 2) функціональна перевірка меж TX через pyadi
    if do_range_test:
        try:
            import adi  # type: ignore
            sdr = adi.Pluto(uri=uri)
            res.connected = True
            reachable: List[float] = []
            for f in _TEST_FREQS_HZ:
                try:
                    sdr.tx_lo = int(f)
                    if abs(int(sdr.tx_lo) - int(f)) < 1e6:
                        reachable.append(f)
                except Exception:
                    pass
            if reachable:
                res.tx_lo_min_hz = min(reachable)
                res.tx_lo_max_hz = max(reachable)
                res.reaches_5g8 = any(f >= 5.7e9 for f in reachable)
                res.inferred_preset = "hacked" if res.reaches_5g8 else "stock"
            del sdr
        except ImportError:
            res.messages.append("pyadi-iio не встановлено — пропускаю перевірку меж TX")
        except Exception as exc:
            if not res.error:
                res.error = str(exc)

    if not res.connected and not res.error:
        res.error = "IIO-контекст не створено (пристрій не підключено?)"
    return res
