"""Composite (CVBS) analog video test-pattern generator.

Builds one full composite video frame as a real baseband signal in volts
(range roughly [-0.3 .. +0.7] V, 1 Vpp). The frame is periodic: concatenating
identical frames is seamless, so it is convenient to feed into the cyclic TX
buffer of the Pluto.

A progressive frame is produced with correct line timing (horizontal sync,
back/front porch) and a simplified vertical sync block. It is exactly the line
rate (15.625 kHz PAL / 15.734 kHz NTSC) together with the frame rate that form
the characteristic signature of analog FPV video the detector reacts to.

Deliberate simplifications:
  * progressive scan (no interlace) — the frame/line "flicker" is preserved;
  * vertical sync — one solid wide pulse without serrations;
  * no color subcarrier (luma alone is enough for the FM signature); a burst
    can be enabled on demand via the color_burst parameter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

import numpy as np

from .i18n import t


# ---------------------------------------------------------------------------
#  Video standard definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VideoStandard:
    name: str
    total_lines: int          # lines per frame (full, including VBI)
    active_lines: int         # visible lines
    fps: float                # frames per second
    line_us: float            # line period, us
    sync_us: float            # horizontal sync pulse duration
    back_porch_us: float
    front_porch_us: float
    vsync_lines: int          # lines of wide vertical sync
    # levels in normalised volts
    sync_level: float = -0.30
    blank_level: float = 0.00
    black_level: float = 0.00
    white_level: float = 0.70
    color_subcarrier_hz: float = 0.0   # 4.43 MHz PAL / 3.58 MHz NTSC, if burst

    @property
    def line_rate_hz(self) -> float:
        return 1e6 / self.line_us

    @property
    def frame_period_s(self) -> float:
        return 1.0 / self.fps


# Working standards — field-based (progressive 288p/240p) with the correct field
# rate: vertical sync runs at 50/60 Hz, as the monitor expects, which fixes the
# vertical roll. They go through the same generate_composite (only the number of
# lines and fps change).
PAL50 = VideoStandard(
    name="PAL50",
    total_lines=312,       # 312 lines/field * 64 us = 19.97 ms -> 50.08 Hz
    active_lines=288,
    fps=50.0,
    line_us=64.0,
    sync_us=4.7,
    back_porch_us=5.7,
    front_porch_us=1.65,
    vsync_lines=5,
    color_subcarrier_hz=4_433_618.75,
)

NTSC60 = VideoStandard(
    name="NTSC60",
    total_lines=262,       # 262 * 63.556 us = 16.65 ms -> 60.06 Hz
    active_lines=240,
    fps=60000.0 / 1001.0,  # ~59.94
    line_us=63.556,
    sync_us=4.7,
    back_porch_us=4.5,
    front_porch_us=1.5,
    vsync_lines=5,
    color_subcarrier_hz=3_579_545.0,
)

STANDARDS: Dict[str, VideoStandard] = {"PAL50": PAL50, "NTSC60": NTSC60}

# legacy names from older configs -> working field standards
_STANDARD_ALIASES = {"PAL": "PAL50", "NTSC": "NTSC60"}


def get_standard(name: str) -> VideoStandard:
    key = name.strip().upper()
    key = _STANDARD_ALIASES.get(key, key)
    if key not in STANDARDS:
        raise KeyError(
            t("Unknown standard '{name}'. Available: {list}",
              name=name, list=list(STANDARDS))
        )
    return STANDARDS[key]


# ---------------------------------------------------------------------------
#  Test patterns.  Each returns a 2D luma array active_lines x width in [0..1]
#  (0 = black, 1 = white).
# ---------------------------------------------------------------------------
def _pat_bars(h: int, w: int, steps: int = 8) -> np.ndarray:
    """Vertical grayscale bars 0..1 (sharp edges — plenty of HF)."""
    idx = (np.arange(w) * steps // w).clip(0, steps - 1)
    row = idx / (steps - 1)
    return np.tile(row, (h, 1))


def _pat_smpte75(h: int, w: int) -> np.ndarray:
    """Luma of 75% color bars (gray, yellow, cyan, green, magenta, red, blue)."""
    lumas = np.array([0.75, 0.69, 0.56, 0.48, 0.36, 0.28, 0.15])
    idx = (np.arange(w) * len(lumas) // w).clip(0, len(lumas) - 1)
    row = lumas[idx]
    return np.tile(row, (h, 1))


def _pat_ramp(h: int, w: int) -> np.ndarray:
    """Horizontal gradient 0..1."""
    row = np.linspace(0.0, 1.0, w)
    return np.tile(row, (h, 1))


def _pat_crosshair(h: int, w: int) -> np.ndarray:
    img = np.zeros((h, w))
    t = max(1, w // 200)
    img[h // 2 - t:h // 2 + t, :] = 1.0
    img[:, w // 2 - t:w // 2 + t] = 1.0
    # frame border
    img[:t, :] = img[-t:, :] = 1.0
    img[:, :t] = img[:, -t:] = 1.0
    return img


def _pat_grid(h: int, w: int, n: int = 16) -> np.ndarray:
    img = np.zeros((h, w))
    t = max(1, w // 400)
    for gx in np.linspace(0, w - 1, n + 1).astype(int):
        img[:, max(0, gx - t):gx + t] = 1.0
    for gy in np.linspace(0, h - 1, n + 1).astype(int):
        img[max(0, gy - t):gy + t, :] = 1.0
    return img


def _pat_checker(h: int, w: int, n: int = 16) -> np.ndarray:
    xs = (np.arange(w) * n // w) & 1
    ys = (np.arange(h) * n // h) & 1
    return (xs[None, :] ^ ys[:, None]).astype(float)


# multiburst: absolute video frequencies (Hz), so the transmitted content does
# not depend on the sample rate. A burst is only rendered while it stays below
# _MULTIBURST_MAX_REL of the sample rate; higher ones are skipped (left flat).
_MULTIBURST_HZ = (0.5e6, 1.0e6, 2.0e6, 3.0e6, 4.0e6)
_MULTIBURST_MAX_REL = 0.4
# nominal sample rate used when a pattern is rendered for preview only
_PREVIEW_FS_HZ = 20.0e6


def _pat_multiburst(h: int, w: int, fs: float = _PREVIEW_FS_HZ) -> np.ndarray:
    """Sine bursts of increasing video frequency (0.5/1/2/3/4 MHz) — maximum HF,
    widest bandwidth. Loads the detector hardest in terms of occupied bandwidth.

    The frequencies are absolute (Hz) and converted to cycles-per-sample with the
    current fs, so the transmitted content is the same at any sample rate. A burst
    whose relative frequency would exceed _MULTIBURST_MAX_REL (~0.4, i.e. the fs
    cannot represent it) is skipped and its segment stays at the blanking level.
    """
    x = np.arange(w)
    seg = w // 6
    row = np.full(w, 0.5)
    for i, f_hz in enumerate(_MULTIBURST_HZ, start=1):
        f_rel = f_hz / float(fs)          # cycles per sample
        if f_rel > _MULTIBURST_MAX_REL:   # fs too low for this burst — skip it
            continue
        s0, s1 = i * seg, (i + 1) * seg
        row[s0:s1] = 0.5 + 0.5 * np.sin(2 * np.pi * f_rel * (x[s0:s1] - s0))
    return np.tile(row, (h, 1))


_pat_multiburst.needs_fs = True   # generate_composite passes the real fs


def _pat_flat(level: float) -> Callable[[int, int], np.ndarray]:
    return lambda h, w: np.full((h, w), level)


_PATTERNS: Dict[str, Callable[[int, int], np.ndarray]] = {
    "bars": _pat_bars,
    "smpte75": _pat_smpte75,
    "ramp": _pat_ramp,
    "crosshair": _pat_crosshair,
    "grid": _pat_grid,
    "checker": _pat_checker,
    "multiburst": _pat_multiburst,
    "white": _pat_flat(1.0),
    "gray": _pat_flat(0.5),
    "black": _pat_flat(0.0),
}


def list_patterns() -> List[str]:
    """Luma patterns (black and white)."""
    return list(_PATTERNS.keys())


# ---------------------------------------------------------------------------
#  Color test patterns → RGB [0..1] (H x W x 3).  Separate from luma patterns.
# ---------------------------------------------------------------------------
# 8 EBU/SMPTE columns: white, yellow, cyan, green, magenta, red, blue, black
_BARS_RGB = np.array([
    [1, 1, 1], [1, 1, 0], [0, 1, 1], [0, 1, 0],
    [1, 0, 1], [1, 0, 0], [0, 0, 1], [0, 0, 0],
], dtype=float)


def _color_bars(h: int, w: int, scale: float = 0.75) -> np.ndarray:
    idx = (np.arange(w) * 8 // w).clip(0, 7)
    row = _BARS_RGB[idx] * scale
    return np.tile(row[None, :, :], (h, 1, 1))


_COLOR_PATTERNS: Dict[str, Callable[[int, int], np.ndarray]] = {
    "color_bars": _color_bars,                       # 75%
    "color_bars100": lambda h, w: _color_bars(h, w, 1.0),
}


def list_color_patterns() -> List[str]:
    return list(_COLOR_PATTERNS.keys())


def list_all_patterns() -> List[str]:
    return list(_PATTERNS.keys()) + list(_COLOR_PATTERNS.keys())


def is_color_pattern(pattern: str) -> bool:
    return pattern in _COLOR_PATTERNS


def _rgb_to_yuv(rgb: np.ndarray):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = 0.492111 * (b - y)
    v = 0.877283 * (r - y)
    return y, u, v


def render_pattern_image(pattern: str, height: int = 288, width: int = 384,
                         fs: float = _PREVIEW_FS_HZ) -> np.ndarray:
    """Preview: luma pattern → 2D [0..1]; color pattern → RGB [0..1] (HxWx3).

    fs only matters for patterns defined in absolute video frequencies
    (multiburst); pass the real sample rate to preview what will be transmitted.
    """
    if pattern in _COLOR_PATTERNS:
        return np.clip(_COLOR_PATTERNS[pattern](height, width), 0.0, 1.0)
    if pattern in _PATTERNS:
        return np.clip(_render_luma(pattern, height, width, fs), 0.0, 1.0)
    raise KeyError(t("Unknown pattern '{pattern}'", pattern=pattern))


def _render_luma(pattern: str, h: int, w: int, fs: float) -> np.ndarray:
    """Call a luma pattern function, giving it fs only if it asks for it."""
    fn = _PATTERNS[pattern]
    if getattr(fn, "needs_fs", False):
        return fn(h, w, fs=fs)
    return fn(h, w)


# ---------------------------------------------------------------------------
#  Composite assembly
# ---------------------------------------------------------------------------
def _samples(us: float, fs: float) -> int:
    return int(round(us * 1e-6 * fs))


def generate_composite(
    pattern: str,
    std: VideoStandard,
    fs: float,
    color_burst: bool = False,
) -> np.ndarray:
    """Return one frame of composite video (float32, volts).

    Length = samples_per_line * total_lines, so the frame tiles seamlessly in a
    cyclic buffer.
    """
    if pattern not in _PATTERNS:
        raise KeyError(
            t("Unknown pattern '{pattern}'. Available: {list}",
              pattern=pattern, list=list_patterns())
        )

    n_line = _samples(std.line_us, fs)
    if n_line < 16:
        raise ValueError(
            t("Sample rate {fs} MSPS is too low for {std}: {n} samples per line. "
              "Increase fs.",
              fs=f"{fs/1e6:.2f}", std=std.name, n=n_line)
        )
    n_sync = _samples(std.sync_us, fs)
    n_bp = _samples(std.back_porch_us, fs)
    n_fp = _samples(std.front_porch_us, fs)
    n_active = n_line - n_sync - n_bp - n_fp
    if n_active < 8:
        raise ValueError(
            t("Too few samples for the active part of the line ({n}). Increase fs.",
              n=n_active)
        )

    # render the pattern image for the active lines
    img = _render_luma(pattern, std.active_lines, n_active, fs)
    img = np.clip(img, 0.0, 1.0)
    active_pixels = std.black_level + img * (std.white_level - std.black_level)

    # template of a single visible line: [sync][back porch][active][front porch]
    def visible_line(active_row: np.ndarray) -> np.ndarray:
        line = np.empty(n_line, dtype=np.float32)
        line[:n_sync] = std.sync_level
        line[n_sync:n_sync + n_bp] = std.blank_level
        line[n_sync + n_bp:n_sync + n_bp + n_active] = active_row
        line[n_sync + n_bp + n_active:] = std.blank_level
        if color_burst and std.color_subcarrier_hz > 0:
            _add_burst(line, n_sync, n_bp, std, fs)
        return line

    # empty (blank) VBI line — sync + porches, active part = black
    black_row = np.full(n_active, std.black_level, dtype=np.float32)
    blank_line = visible_line(black_row)

    # vertical sync line — one solid wide pulse at the sync level
    vsync_line = np.full(n_line, std.sync_level, dtype=np.float32)

    vbi_lines = std.total_lines - std.active_lines
    vbi_blank = max(0, vbi_lines - std.vsync_lines)

    lines: List[np.ndarray] = []
    # vertical sync block
    for _ in range(std.vsync_lines):
        lines.append(vsync_line)
    # the rest of the VBI — blank lines
    for _ in range(vbi_blank):
        lines.append(blank_line)
    # active lines carrying the pattern
    for r in range(std.active_lines):
        lines.append(visible_line(active_pixels[r]))

    frame = np.concatenate(lines).astype(np.float32)
    # guard the exact length (timing rounding may add/remove a few samples)
    expected = n_line * std.total_lines
    if frame.size != expected:
        frame = frame[:expected] if frame.size > expected else np.pad(
            frame, (0, expected - frame.size), constant_values=std.blank_level
        )
    return frame


def _add_burst(line: np.ndarray, n_sync: int, n_bp: int, std: VideoStandard, fs: float) -> None:
    """Add a few cycles of the color subcarrier on the back porch (burst)."""
    n_burst = min(n_bp // 2, _samples(2.25, fs))  # ~2.25 us burst
    start = n_sync + max(1, n_bp // 6)
    t = np.arange(n_burst) / fs
    line[start:start + n_burst] += 0.15 * np.sin(2 * np.pi * std.color_subcarrier_hz * t)


# chroma/burst amplitudes in composite "volts"
_CHROMA_GAIN = 0.5
_BURST_AMP = 0.15


def generate_composite_color(pattern: str, std: VideoStandard, fs: float) -> np.ndarray:
    """Return one frame of COLOR composite video (float32, volts).

    Luma + chroma subcarrier (U/V QAM) + color burst on the back porch. For PAL
    the V phase alternates line by line. The line/sync structure is the same as
    in generate_composite; the luma functions are left untouched.

    Requires fs >= ~2.2*subcarrier (for PAL 4.43 MHz -> fs >= ~10 MHz), otherwise
    the subcarrier aliases. fs of 13-15 MSPS is recommended.
    """
    import warnings

    if pattern not in _COLOR_PATTERNS:
        raise KeyError(
            t("Unknown color pattern '{pattern}'. Available: {list}",
              pattern=pattern, list=list_color_patterns())
        )
    if std.color_subcarrier_hz <= 0:
        raise ValueError(t("Standard {std} has no color subcarrier", std=std.name))
    if fs < 2.2 * std.color_subcarrier_hz:
        warnings.warn(
            t("Sample rate {fs} MSPS is too low for color {std}: the {sc} MHz "
              "subcarrier will alias. Increase fs to >= {min_fs} MSPS.",
              fs=f"{fs/1e6:.1f}", std=std.name,
              sc=f"{std.color_subcarrier_hz/1e6:.2f}",
              min_fs=f"{2.2*std.color_subcarrier_hz/1e6:.1f}"),
            stacklevel=2,
        )

    n_line = _samples(std.line_us, fs)
    if n_line < 16:
        raise ValueError(
            t("Sample rate {fs} MSPS is too low: {n} samples/line.",
              fs=f"{fs/1e6:.2f}", n=n_line)
        )
    n_sync = _samples(std.sync_us, fs)
    n_bp = _samples(std.back_porch_us, fs)
    n_fp = _samples(std.front_porch_us, fs)
    n_active = n_line - n_sync - n_bp - n_fp
    if n_active < 8:
        raise ValueError(
            t("Too few samples for the active part of the line ({n}).", n=n_active)
        )

    N = n_line * std.total_lines
    field = np.empty(N, dtype=np.float32)

    # continuous subcarrier across the whole frame (shared phase for burst and chroma)
    idx = np.arange(N)
    omega = 2.0 * np.pi * std.color_subcarrier_hz / fs
    sc_sin = np.sin(omega * idx).astype(np.float32)
    sc_cos = np.cos(omega * idx).astype(np.float32)

    is_pal = std.name.upper().startswith("PAL")
    n_burst = min(n_bp // 2, int(round(10.0 / std.color_subcarrier_hz * fs)))  # ~10 cycles
    burst_rel = n_sync + max(1, _samples(0.9, fs))

    # active image -> Y/U/V
    rgb = np.clip(_COLOR_PATTERNS[pattern](std.active_lines, n_active), 0.0, 1.0)
    Y, U, V = _rgb_to_yuv(rgb)
    active_luma = std.black_level + Y * (std.white_level - std.black_level)

    la0, la1 = n_sync + n_bp, n_sync + n_bp + n_active   # bounds of the active part of a line

    pos = 0
    # vertical sync block (solid, as in the working version)
    vsync_line = np.full(n_line, std.sync_level, dtype=np.float32)
    for _ in range(std.vsync_lines):
        field[pos:pos + n_line] = vsync_line
        pos += n_line
    # blank VBI lines
    vbi_blank = max(0, (std.total_lines - std.active_lines) - std.vsync_lines)
    blank_line = np.empty(n_line, dtype=np.float32)
    blank_line[:n_sync] = std.sync_level
    blank_line[n_sync:] = std.blank_level
    for _ in range(vbi_blank):
        field[pos:pos + n_line] = blank_line
        pos += n_line
    # active lines: luma + chroma + burst
    for r in range(std.active_lines):
        ln = np.empty(n_line, dtype=np.float32)
        ln[:n_sync] = std.sync_level
        ln[n_sync:n_sync + n_bp] = std.blank_level
        ln[la0:la1] = active_luma[r]
        ln[la1:] = std.blank_level
        s = -1.0 if (is_pal and (r & 1)) else 1.0
        ln[la0:la1] += _CHROMA_GAIN * (U[r] * sc_sin[pos + la0:pos + la1]
                                       + s * V[r] * sc_cos[pos + la0:pos + la1])
        phi = np.pi - (s * np.pi / 4.0 if is_pal else 0.0)   # burst 180° (NTSC) / 180°±45° (PAL)
        b0, b1 = pos + burst_rel, min(pos + burst_rel + n_burst, pos + la0)
        field[pos:pos + n_line] = ln
        field[b0:b1] += _BURST_AMP * (np.cos(phi) * sc_sin[b0:b1] + np.sin(phi) * sc_cos[b0:b1])
        pos += n_line

    expected = n_line * std.total_lines
    if field.size != expected:
        field = field[:expected] if field.size > expected else np.pad(
            field, (0, expected - field.size), constant_values=std.blank_level)
    return field
