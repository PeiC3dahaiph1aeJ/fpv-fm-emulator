"""Composite (CVBS) analog video test-pattern generator.

Формує один повний кадр композитного відео як дійсний baseband-сигнал у вольтах
(діапазон приблизно [-0.3 .. +0.7] В, 1 Vpp). Кадр періодичний: конкатенація
однакових кадрів безшовна, тому його зручно віддавати в циклічний TX-буфер Pluto.

Реалізовано прогресивний кадр із коректним таймінгом рядків (гориз. синхро,
задня/передня площадки) та спрощеним вертикальним синхро-блоком. Саме частота
рядків (15.625 кГц PAL / 15.734 кГц NTSC) та кадрова частота формують характерну
сигнатуру аналогового FPV-відео, на яку реагує детектор.

Спрощення (задокументовані навмисно):
  * прогресивна розгортка (без чересрядкової) — кадрова/рядкова цятка збережені;
  * вертикальне синхро — суцільний широкий імпульс без серрацій;
  * без кольоровської піднесучої (луми достатньо для ЧМ-сигнатури); за бажання
    можна ввімкнути burst через параметр color_burst.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

import numpy as np


# ---------------------------------------------------------------------------
#  Video standard definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VideoStandard:
    name: str
    total_lines: int          # рядків на кадр (повний, з VBI)
    active_lines: int         # видимих рядків
    fps: float                # кадрів/с
    line_us: float            # період рядка, мкс
    sync_us: float            # тривалість горизонтального синхроімпульсу
    back_porch_us: float
    front_porch_us: float
    vsync_lines: int          # рядків широкого вертикального синхро
    # рівні в нормованих вольтах
    sync_level: float = -0.30
    blank_level: float = 0.00
    black_level: float = 0.00
    white_level: float = 0.70
    color_subcarrier_hz: float = 0.0   # 4.43 МГц PAL / 3.58 МГц NTSC, якщо burst

    @property
    def line_rate_hz(self) -> float:
        return 1e6 / self.line_us

    @property
    def frame_period_s(self) -> float:
        return 1.0 / self.fps


# Робочі стандарти — польові (прогресивні 288p/240p) з коректною частотою полів:
# вертикальний синхро йде 50/60 Гц, як чекає монітор — виправляє зсув по вертикалі.
# Проходять через той самий generate_composite (змінюються лише к-сть рядків і fps).
PAL50 = VideoStandard(
    name="PAL50",
    total_lines=312,       # 312 рядків/поле * 64 мкс = 19.97 мс -> 50.08 Гц
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
    total_lines=262,       # 262 * 63.556 мкс = 16.65 мс -> 60.06 Гц
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

# застарілі імена зі старих конфігів -> робочі польові стандарти
_STANDARD_ALIASES = {"PAL": "PAL50", "NTSC": "NTSC60"}


def get_standard(name: str) -> VideoStandard:
    key = name.strip().upper()
    key = _STANDARD_ALIASES.get(key, key)
    if key not in STANDARDS:
        raise KeyError(f"Невідомий стандарт '{name}'. Доступні: {list(STANDARDS)}")
    return STANDARDS[key]


# ---------------------------------------------------------------------------
#  Test patterns.  Кожен повертає 2D-масив луми active_lines x width у [0..1]
#  (0 = чорний, 1 = білий).
# ---------------------------------------------------------------------------
def _pat_bars(h: int, w: int, steps: int = 8) -> np.ndarray:
    """Вертикальні градаційні смуги 0..1 (різкі краї — багато ВЧ)."""
    idx = (np.arange(w) * steps // w).clip(0, steps - 1)
    row = idx / (steps - 1)
    return np.tile(row, (h, 1))


def _pat_smpte75(h: int, w: int) -> np.ndarray:
    """Луми 75% color-bars (сірий, жовтий, блакитний, зелений, пурпур, черв., синій)."""
    lumas = np.array([0.75, 0.69, 0.56, 0.48, 0.36, 0.28, 0.15])
    idx = (np.arange(w) * len(lumas) // w).clip(0, len(lumas) - 1)
    row = lumas[idx]
    return np.tile(row, (h, 1))


def _pat_ramp(h: int, w: int) -> np.ndarray:
    """Горизонтальний градієнт 0..1."""
    row = np.linspace(0.0, 1.0, w)
    return np.tile(row, (h, 1))


def _pat_crosshair(h: int, w: int) -> np.ndarray:
    img = np.zeros((h, w))
    t = max(1, w // 200)
    img[h // 2 - t:h // 2 + t, :] = 1.0
    img[:, w // 2 - t:w // 2 + t] = 1.0
    # рамка
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


def _pat_multiburst(h: int, w: int) -> np.ndarray:
    """Пакети синусоїд зі зростаючою частотою — максимум ВЧ, найширша смуга.
    Найкраще навантажує детектор за зайнятою смугою."""
    x = np.arange(w)
    seg = w // 6
    row = np.full(w, 0.5)
    for i in range(1, 6):
        f = 0.02 * i               # відн. частота (циклів на семпл)
        s0, s1 = i * seg, (i + 1) * seg
        row[s0:s1] = 0.5 + 0.5 * np.sin(2 * np.pi * f * (x[s0:s1] - s0))
    return np.tile(row, (h, 1))


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
    """Люма-патерни (ч/б)."""
    return list(_PATTERNS.keys())


# ---------------------------------------------------------------------------
#  Color test patterns → RGB [0..1] (H x W x 3).  Окремо від люма-патернів.
# ---------------------------------------------------------------------------
# 8 стовпчиків EBU/SMPTE: білий, жовтий, блакитний, зелений, пурпур, черв., синій, чорний
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


def render_pattern_image(pattern: str, height: int = 288, width: int = 384) -> np.ndarray:
    """Прев'ю: люма-патерн → 2D [0..1]; кольоровий → RGB [0..1] (HxWx3)."""
    if pattern in _COLOR_PATTERNS:
        return np.clip(_COLOR_PATTERNS[pattern](height, width), 0.0, 1.0)
    if pattern in _PATTERNS:
        return np.clip(_PATTERNS[pattern](height, width), 0.0, 1.0)
    raise KeyError(f"Невідомий патерн '{pattern}'")


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
    """Повертає один кадр композитного відео (float32, вольти).

    Довжина = samples_per_line * total_lines, тому кадр безшовно тайлиться в
    циклічному буфері.
    """
    if pattern not in _PATTERNS:
        raise KeyError(f"Невідомий патерн '{pattern}'. Доступні: {list_patterns()}")

    n_line = _samples(std.line_us, fs)
    if n_line < 16:
        raise ValueError(
            f"Замала частота дискретизації {fs/1e6:.2f} MSPS для {std.name}: "
            f"{n_line} семплів на рядок. Підніміть fs."
        )
    n_sync = _samples(std.sync_us, fs)
    n_bp = _samples(std.back_porch_us, fs)
    n_fp = _samples(std.front_porch_us, fs)
    n_active = n_line - n_sync - n_bp - n_fp
    if n_active < 8:
        raise ValueError(
            f"Замало семплів на активну частину рядка ({n_active}). Підніміть fs."
        )

    # згенерувати зображення патерну для активних рядків
    img = _PATTERNS[pattern](std.active_lines, n_active)
    img = np.clip(img, 0.0, 1.0)
    active_pixels = std.black_level + img * (std.white_level - std.black_level)

    # шаблон одного видимого рядка: [sync][back porch][active][front porch]
    def visible_line(active_row: np.ndarray) -> np.ndarray:
        line = np.empty(n_line, dtype=np.float32)
        line[:n_sync] = std.sync_level
        line[n_sync:n_sync + n_bp] = std.blank_level
        line[n_sync + n_bp:n_sync + n_bp + n_active] = active_row
        line[n_sync + n_bp + n_active:] = std.blank_level
        if color_burst and std.color_subcarrier_hz > 0:
            _add_burst(line, n_sync, n_bp, std, fs)
        return line

    # порожній (blank) рядок VBI — синхро + площадки, активна частина = чорний
    black_row = np.full(n_active, std.black_level, dtype=np.float32)
    blank_line = visible_line(black_row)

    # рядок вертикального синхро — суцільний широкий імпульс на рівні sync
    vsync_line = np.full(n_line, std.sync_level, dtype=np.float32)

    vbi_lines = std.total_lines - std.active_lines
    vbi_blank = max(0, vbi_lines - std.vsync_lines)

    lines: List[np.ndarray] = []
    # вертикальний синхро-блок
    for _ in range(std.vsync_lines):
        lines.append(vsync_line)
    # решта VBI — порожні рядки
    for _ in range(vbi_blank):
        lines.append(blank_line)
    # активні рядки з патерном
    for r in range(std.active_lines):
        lines.append(visible_line(active_pixels[r]))

    frame = np.concatenate(lines).astype(np.float32)
    # страхуємось на кратність (округлення таймінгу могло дати ± кілька семплів)
    expected = n_line * std.total_lines
    if frame.size != expected:
        frame = frame[:expected] if frame.size > expected else np.pad(
            frame, (0, expected - frame.size), constant_values=std.blank_level
        )
    return frame


def _add_burst(line: np.ndarray, n_sync: int, n_bp: int, std: VideoStandard, fs: float) -> None:
    """Додати кілька циклів кольорової піднесучої на задній площадці (burst)."""
    n_burst = min(n_bp // 2, _samples(2.25, fs))  # ~2.25 мкс burst
    start = n_sync + max(1, n_bp // 6)
    t = np.arange(n_burst) / fs
    line[start:start + n_burst] += 0.15 * np.sin(2 * np.pi * std.color_subcarrier_hz * t)


# амплітуди хроми/burst у композитних «вольтах»
_CHROMA_GAIN = 0.5
_BURST_AMP = 0.15


def generate_composite_color(pattern: str, std: VideoStandard, fs: float) -> np.ndarray:
    """Повертає один кадр КОЛЬОРОВОГО композиту (float32, вольти).

    Люма + чрома-піднесуча (U/V QAM) + кольоровий burst на задній площадці.
    Для PAL фаза V чергується по рядках. Структура рядків/синхро — як у
    generate_composite; luma-функції не зачіпаються.

    Потрібна fs >= ~2.2*піднесуча (для PAL 4.43 МГц -> fs >= ~10 МГц), інакше
    піднесуча завернеться. Рекомендовано fs 13–15 MSPS.
    """
    import warnings

    if pattern not in _COLOR_PATTERNS:
        raise KeyError(f"Невідомий кольоровий патерн '{pattern}'. Доступні: {list_color_patterns()}")
    if std.color_subcarrier_hz <= 0:
        raise ValueError(f"Стандарт {std.name} не має кольорової піднесучої")
    if fs < 2.2 * std.color_subcarrier_hz:
        warnings.warn(
            f"Замала fs {fs/1e6:.1f} MSPS для кольору {std.name}: піднесуча "
            f"{std.color_subcarrier_hz/1e6:.2f} МГц завернеться. Підніміть fs до "
            f">= {2.2*std.color_subcarrier_hz/1e6:.1f} MSPS.",
            stacklevel=2,
        )

    n_line = _samples(std.line_us, fs)
    if n_line < 16:
        raise ValueError(f"Замала fs {fs/1e6:.2f} MSPS: {n_line} семплів/рядок.")
    n_sync = _samples(std.sync_us, fs)
    n_bp = _samples(std.back_porch_us, fs)
    n_fp = _samples(std.front_porch_us, fs)
    n_active = n_line - n_sync - n_bp - n_fp
    if n_active < 8:
        raise ValueError(f"Замало семплів на активну частину рядка ({n_active}).")

    N = n_line * std.total_lines
    field = np.empty(N, dtype=np.float32)

    # неперервна піднесуча по всьому кадру (спільна фаза для burst і хроми)
    idx = np.arange(N)
    omega = 2.0 * np.pi * std.color_subcarrier_hz / fs
    sc_sin = np.sin(omega * idx).astype(np.float32)
    sc_cos = np.cos(omega * idx).astype(np.float32)

    is_pal = std.name.upper().startswith("PAL")
    n_burst = min(n_bp // 2, int(round(10.0 / std.color_subcarrier_hz * fs)))  # ~10 циклів
    burst_rel = n_sync + max(1, _samples(0.9, fs))

    # активне зображення -> Y/U/V
    rgb = np.clip(_COLOR_PATTERNS[pattern](std.active_lines, n_active), 0.0, 1.0)
    Y, U, V = _rgb_to_yuv(rgb)
    active_luma = std.black_level + Y * (std.white_level - std.black_level)

    la0, la1 = n_sync + n_bp, n_sync + n_bp + n_active   # межі активної частини в рядку

    pos = 0
    # вертикальний синхро-блок (суцільний, як у робочій версії)
    vsync_line = np.full(n_line, std.sync_level, dtype=np.float32)
    for _ in range(std.vsync_lines):
        field[pos:pos + n_line] = vsync_line
        pos += n_line
    # порожні рядки VBI
    vbi_blank = max(0, (std.total_lines - std.active_lines) - std.vsync_lines)
    blank_line = np.empty(n_line, dtype=np.float32)
    blank_line[:n_sync] = std.sync_level
    blank_line[n_sync:] = std.blank_level
    for _ in range(vbi_blank):
        field[pos:pos + n_line] = blank_line
        pos += n_line
    # активні рядки: люма + чрома + burst
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
