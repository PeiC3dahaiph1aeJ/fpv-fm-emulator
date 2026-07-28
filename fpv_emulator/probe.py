"""Detect a connected Pluto and report chip / tuning range.

Identifies a connected Pluto+, reads the context attributes (model, firmware,
serial) and functionally probes the real TX tuning limits (to tell whether it is
a stock AD9363 or an AD9361 mod with access to 5.8 GHz). Hardware dependencies
are imported lazily — without a device the function still returns an informative
result.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .i18n import t


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
    # A reachable device whose driver layer then failed is NOT "not found" —
    # saying so sends the user hunting for a cable when the problem is software.
    ctx_ok: bool = False                       # the raw IIO context opened
    traceback_text: Optional[str] = None       # full traceback of the first failure
    lib_versions: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.connected:
            if self.ctx_ok:
                # we did reach it — the failure is on our side of the wire
                head = t("Pluto is reachable at {uri}, but opening it failed: {err}",
                         uri=self.uri, err=self.error or "?")
            else:
                head = t("Pluto not found ({uri}): {err}",
                         uri=self.uri, err=self.error or t("no context"))
            lines = [head]
            for k, v in self.lib_versions.items():
                lines.append(f"  {k}: {v}")
            for m in self.messages:
                lines.append(f"  · {m}")
            if self.traceback_text:
                lines.append("  " + t("Details (send this when reporting the problem):"))
                lines.extend("    " + ln for ln in self.traceback_text.strip().splitlines())
            return "\n".join(lines)
        lines = [t("Pluto connected: {uri}", uri=self.uri)]
        for k in ("hw_model", "hw_serial", "fw_version"):
            if k in self.attrs:
                lines.append(f"  {k}: {self.attrs[k]}")
        if self.tx_lo_max_hz:
            lines.append(
                "  " + t("TX LO: {min} – {max} MHz",
                         min=f"{self.tx_lo_min_hz/1e6:.0f}",
                         max=f"{self.tx_lo_max_hz/1e6:.0f}")
            )
        if self.inferred_preset:
            lines.append("  " + t("Likely type: {preset}", preset=self.inferred_preset))
        lines.append(
            "  " + t("Direct 5.8 GHz: {answer}",
                     answer=t("YES") if self.reaches_5g8
                     else t("NO (a mod / up-converter is required)"))
        )
        for m in self.messages:
            lines.append(f"  · {m}")
        return "\n".join(lines)


# frequencies for the functional limit check (Hz)
_TEST_FREQS_HZ = [70e6, 325e6, 1200e6, 2450e6, 3300e6, 3800e6, 5800e6, 6000e6]


def probe(uri: str = "ip:192.168.2.1", do_range_test: bool = True) -> ProbeResult:
    res = ProbeResult(connected=False, uri=uri)

    # 1) context via libiio (pylibiio)
    ctx = None
    try:
        import iio  # type: ignore
        res.lib_versions["libiio (host)"] = ".".join(str(x) for x in iio.version[:2])
        ctx = iio.Context(uri)
        res.ctx_ok = True
        res.connected = True
        for name in ("hw_model", "hw_serial", "fw_version", "uri"):
            try:
                res.attrs[name] = ctx.attrs.get(name, "")
            except Exception:
                pass
        try:
            res.lib_versions["libiio (device)"] = ".".join(str(x) for x in ctx.version[:2])
        except Exception:
            pass
    except ImportError:
        res.messages.append(
            t("pylibiio (the iio module) is not installed — skipping the context read")
        )
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.traceback_text = traceback.format_exc()

    # 2) functional TX limit check via pyadi
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
            res.messages.append(
                t("pyadi-iio is not installed — skipping the TX range check")
            )
        except Exception as exc:
            if not res.error:
                res.error = f"{type(exc).__name__}: {exc}"
                res.traceback_text = traceback.format_exc()

    if not res.connected and not res.error:
        res.error = t("IIO context was not created (device not connected?)")
    return res
