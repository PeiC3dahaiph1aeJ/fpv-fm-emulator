"""Minimal i18n layer.

English is the **source language**: English strings are used directly in the code
and act as translation keys. Ukrainian comes from the catalog below.

Usage::

    from .i18n import t, set_language
    set_language("uk")
    print(t("Output"))                     # -> "Вихід"
    print(t("Frame: {n} samples", n=1000))  # formatting via kwargs

Language selection:
  * GUI  — combo box (saved via QSettings), default English
  * CLI  — ``--lang uk|en`` flag
  * both — env var ``FPV_LANG=uk`` as a fallback default
"""
from __future__ import annotations

import os
from typing import Dict, List

DEFAULT_LANGUAGE = "en"
LANGUAGES: Dict[str, str] = {"en": "English", "uk": "Українська"}

_lang = DEFAULT_LANGUAGE

# Ukrainian catalog: English key -> Ukrainian text.
# Keys must match the English strings used in the code exactly.
CATALOG_UK: Dict[str, str] = {
    "Probe an ADALM-Pluto / Pluto+": "Проба ADALM-Pluto / Pluto+",
    "  deviation pp={dev} MHz, occupied bandwidth ~{bw} MHz": "  девіація pp={dev} МГц, зайнята смуга ~{bw} МГц",
    "  standard={std}, pattern={pattern}, fs={fs} MSPS": "  стандарт={std}, патерн={pattern}, fs={fs} MSPS",
    "  {name}  {freq} MHz   (band {band})": "  {name}  {freq} МГц   (банд {band})",
    " (lap {lap})": " (коло {lap})",
    ", bw ~{bw} MHz": ", смуга ~{bw} МГц",
    ", gain {gain} dB": ", підсил. {gain} дБ",
    "1.2/1.3 GHz (representative set)": "1.2/1.3 ГГц (репрезентативний набір)",
    "2.4 GHz analog (representative set)": "2.4 ГГц аналогові (репрезентативний набір)",
    "3.3 GHz (representative set)": "3.3 ГГц (репрезентативний набір)",
    "900 MHz (representative set)": "900 МГц (репрезентативний набір)",
    "Aliasing risk: peak excursion {peak} MHz exceeds 0.45*fs = {limit} MHz. Increase sample_rate or reduce the deviation/offsets.": "Ризик аліасингу: пікова екскурсія {peak} МГц перевищує 0.45*fs = {limit} МГц. Підніміть sample_rate або зменшіть девіацію/зсуви.",
    "An AD9361 mod or an external up-converter is required.": "Потрібен AD9361-мод або зовнішній ап-конвертер.",
    "Analog FPV video (FM) emulator on Pluto+ for testing FPV detectors": "Емулятор аналогового FPV-відео (ЧМ) на Pluto+ для перевірки детекторів FPV",
    "Available scenarios:": "Доступні сценарії:",
    "Backend:": "Backend:",
    "Backend: {backend}  |  fs={fs} MSPS  |  scenario: {name}": "Backend: {backend}  |  fs={fs} MSPS  |  сценарій: {name}",
    "Band A / Boscam A (5.8 GHz)": "Band A / Boscam A (5.8 ГГц)",
    "Band B / Boscam B (5.8 GHz)": "Band B / Boscam B (5.8 ГГц)",
    "Band E / Boscam E (5.8 GHz)": "Band E / Boscam E (5.8 ГГц)",
    "Band F / Airwave / FatShark (5.8 GHz)": "Band F / Airwave / FatShark (5.8 ГГц)",
    "Band L / low race (5.8 GHz, vendor variant)": "Band L / low race (5.8 ГГц, варіант виробника)",
    "Band:": "Банд:",
    "Carrier:": "Несуча:",
    "Carrier:     {mhz} MHz": "Несуча:      {mhz} МГц",
    "Channel:": "Канал:",
    "Check the settings / up-converter.": "Перевірте налаштування/ап-конвертер.",
    "Color burst": "Кольоровий burst",
    "Color patterns:  {items}   (requires fs >= ~13 MSPS)": "Кольорові патерни:  {items}   (потрібна fs >= ~13 MSPS)",
    "Deviation:": "Девіація:",
    "Direct 5.8 GHz: {answer}": "5.8 ГГц напряму: {answer}",
    "Do not retune TX during the probe": "Не перестроювати TX під час проби",
    "Drone list is empty": "Список дронів порожній",
    "Event log:": "Журнал подій:",
    "FPV FM emulator for Pluto+ · test signal for FPV detectors": "FPV FM-емулятор для Pluto+ · тестовий сигнал для детекторів FPV",
    "Field 'type' must be one of {types}, got '{value}'": "Поле 'type' має бути одним з {types}, отримано '{value}'",
    "File (file):": "Файл (file):",
    "Find Pluto and determine its frequency range": "Знайти Pluto й визначити діапазон",
    "Frame:       {n} samples · {ms} ms · buffer ~{mb} MB": "Кадр:        {n} семпл · {ms} мс · буфер ~{mb} МБ",
    "Frequency": "Частота",
    "Frequency {freq} MHz is outside the {chip} range ({min}–{max} MHz).": "Частота {freq} МГц поза діапазоном {chip} ({min}–{max} МГц).",
    "Generate IQ into a file (no hardware)": "Згенерувати IQ у файл (без апаратури)",
    "HW range:": "HW-діапазон:",
    "IIO context was not created (device not connected?)": "IIO-контекст не створено (пристрій не підключено?)",
    "Language:": "Мова:",
    "Likely type: {preset}": "Ймовірний тип: {preset}",
    "Line rate:   {khz} kHz": "Рядкова:     {khz} кГц",
    "List SDRs": "Список SDR",
    "List SDRs via SoapySDR": "Список SDR через SoapySDR",
    "List bands/channels": "Список діапазонів/каналів",
    "List scenarios": "Список сценаріїв",
    "List test patterns": "Список тестових патернів",
    "Luma patterns (B/W): {items}": "Люма-патерни (ч/б): {items}",
    "MHz": "МГц",
    "MHz pp": "МГц pp",
    "Manual carrier (static)": "Ручна несуча (static)",
    "Missing block '{stype}:' for a scenario of type '{stype}'": "Відсутній блок '{stype}:' для сценарію типу '{stype}'",
    "Mode": "Режим",
    "NO (a mod / up-converter is required)": "НІ (потрібен мод/ап-конвертер)",
    "No HW preset '{preset}'. Available: {list}": "Немає HW-пресету '{preset}'. Є: {list}",
    "No SoapySDR devices found (or SoapySDR is not installed).": "SoapySDR-пристроїв не знайдено (або SoapySDR не встановлено).",
    "No scenarios found in config/scenarios": "Сценаріїв не знайдено у config/scenarios",
    "Occupied bandwidth ~{bw} MHz (fs={fs} MSPS){alias}": "Зайнята смуга ~{bw} МГц (fs={fs} MSPS){alias}",
    "Output": "Вихід",
    "Pattern:": "Патерн:",
    "Pluto URI (backend=pluto)": "URI Pluto (backend=pluto)",
    "Pluto connected: {uri}": "Pluto підключено: {uri}",
    "Pluto not found ({uri}): {err}": "Pluto не знайдено ({uri}): {err}",
    "Power (tx_hardwaregain)": "Потужність (tx_hardwaregain)",
    "Power ramp R4 (drone approaching / moving away)": "Рамп потужності R4 (наближення/віддалення дрона)",
    "Probe Pluto": "Проба Pluto",
    "Probe finished": "Проба завершена",
    "Probing Pluto…": "Проба Pluto…",
    "Raceband (5.8 GHz)": "Raceband (5.8 ГГц)",
    "Raceband sweep (5.8 GHz)": "Свіп по Raceband (5.8 ГГц)",
    "SDR (soapy):": "SDR (soapy):",
    "SDRs found (SoapySDR):": "Знайдені SDR (SoapySDR):",
    "Sample rate {fs} MSPS is too low for color {std}: the {sc} MHz subcarrier will alias. Increase fs to >= {min_fs} MSPS.": "Замала fs {fs} MSPS для кольору {std}: піднесуча {sc} МГц завернеться. Підніміть fs до >= {min_fs} MSPS.",
    "Sample rate {fs} MSPS is too low for {std}: {n} samples per line. Increase fs.": "Замала частота дискретизації {fs} MSPS для {std}: {n} семплів на рядок. Підніміть fs.",
    "Sample rate {fs} MSPS is too low: {n} samples/line.": "Замала fs {fs} MSPS: {n} семплів/рядок.",
    "Sample rate:": "Част. дискр.:",
    "Scenario not found: {path}": "Сценарій не знайдено: {path}",
    "Scenario: {name}": "Сценарій: {name}",
    "Several drones at once (5.8 GHz)": "Кілька дронів одночасно (5.8 ГГц)",
    "Signal": "Сигнал",
    "SoapySDR device args (backend=soapy): driver=hackrf|lime|uhd|bladerf": "аргументи пристрою SoapySDR (backend=soapy): driver=hackrf|lime|uhd|bladerf",
    "SoapySDR is not installed. Install SoapySDR + the device driver: Windows — PothosSDR (contains SoapySDR and the drivers); Linux — apt install python3-soapysdr soapysdr-module-hackrf (or -lime/-uhd/-bladerf).": "SoapySDR не встановлено. Постав SoapySDR + драйвер пристрою: Windows — PothosSDR (містить SoapySDR і драйвери); Linux — apt install python3-soapysdr soapysdr-module-hackrf (або -lime/-uhd/-bladerf).",
    "Specify '{key}' or 'freq_mhz'": "Задайте '{key}' або 'freq_mhz'",
    "Specify --channel, --freq-mhz or --scenario": "Задайте --channel, --freq-mhz або --scenario",
    "Standard {std} has no color subcarrier": "Стандарт {std} не має кольорової піднесучої",
    "Standard:": "Стандарт:",
    "Static carrier R1": "Статична несуча R1",
    "Stopped": "Зупинено",
    "Stopping…": "Зупинка…",
    "Sweep across bands (detector coverage check)": "Свіп по діапазонах (перевірка покриття детектора)",
    "Sweep across ranges (example)": "Свіп по діапазонах (приклад)",
    "TX LO: {min} – {max} MHz": "TX LO: {min} – {max} МГц",
    "Too few samples for the active part of the line ({n}).": "Замало семплів на активну частину рядка ({n}).",
    "Too few samples for the active part of the line ({n}). Increase fs.": "Замало семплів на активну частину рядка ({n}). Підніміть fs.",
    "Transmit (Pluto/file/null) or run a scenario": "Передавати (Pluto/file/null) або запустити сценарій",
    "URI Pluto:": "URI Pluto:",
    "Unknown backend '{kind}' (pluto|soapy|file|null)": "Невідомий backend '{kind}' (pluto|soapy|file|null)",
    "Unknown band '{band}'": "Невідомий банд '{band}'",
    "Unknown channel '{name}'. Available: {list}": "Невідомий канал '{name}'. Доступні: {list}",
    "Unknown color pattern '{pattern}'. Available: {list}": "Невідомий кольоровий патерн '{pattern}'. Доступні: {list}",
    "Unknown pattern '{pattern}'": "Невідомий патерн '{pattern}'",
    "Unknown pattern '{pattern}'. Available: {list}": "Невідомий патерн '{pattern}'. Доступні: {list}",
    "Unknown scenario type '{stype}'": "Невідомий тип сценарію '{stype}'",
    "Unknown standard '{name}'. Available: {list}": "Невідомий стандарт '{name}'. Доступні: {list}",
    "Windows: install PothosSDR. Linux: apt install python3-soapysdr soapysdr-module-<driver>.": "Windows: постав PothosSDR. Linux: apt install python3-soapysdr soapysdr-module-<драйвер>.",
    "Wrote {n} samples ({ms} ms/frame) -> {out}": "Записано {n} семплів ({ms} мс/кадр) -> {out}",
    "YES": "ТАК",
    "[ERROR] {msg}": "[ПОМИЛКА] {msg}",
    "[GAIN] power -> {gain} dB (applied)": "[GAIN] потужність -> {gain} дБ (застосовано)",
    "[MULTI] center {center} MHz, {n_drones} drone(s), offsets {offsets} MHz, span ~{span} MHz": "[MULTI] центр {center} МГц, {n_drones} дрон(ів), зсуви {offsets} МГц, розмах ~{span} МГц",
    "[PAUSE] silence {seconds} s (RF off) @ {freq} MHz": "[PAUSE] тиша {seconds} с (RF off) @ {freq} МГц",
    "[PWR ] {channel} @ {freq} MHz, gain {gain} dB": "[PWR ] {channel} @ {freq} МГц, підсил. {gain} дБ",
    "[START] scenario '{name}' (type {type})": "[START] сценарій '{name}' (тип {type})",
    "[STOP ] scenario '{name}'": "[STOP ] сценарій '{name}'",
    "[TUNE] {channel} @ {freq} MHz": "[TUNE] {channel} @ {freq} МГц",
    "[WARN] backend = null — dry run, nothing goes on air.": "[УВАГА] backend = null — сухий прогін, у ефір нічого не йде.",
    "[info] Color pattern — fs raised to 20 MSPS (subcarrier + wide bandwidth).": "[info] Кольоровий патерн — fs піднято до 20 MSPS (піднесуча + широка смуга).",
    "[info] backend = soapy — set the device in the «SDR (soapy)» field (e.g. driver=hackrf|lime|uhd). «List SDRs» shows what is available.": "[info] backend = soapy — задай пристрій у полі «SDR (soapy)» (напр. driver=hackrf|lime|uhd). «Список SDR» покаже доступні.",
    "e.g. R1": "напр. R1",
    "file for backend=file": "файл для backend=file",
    "hold for N seconds (static)": "тримати N секунд (static)",
    "interface language (default: from the FPV_LANG env var, else en)": "мова інтерфейсу (типово: зі змінної оточення FPV_LANG, інакше en)",
    "manual carrier, MHz": "несуча вручну, МГц",
    "multi_drone: specify the 'drones' list": "multi_drone: задайте список 'drones'",
    "no context": "немає контексту",
    "pp deviation, Hz": "девіація pp, Гц",
    "pyadi-iio is not installed — skipping the TX range check": "pyadi-iio не встановлено — пропускаю перевірку меж TX",
    "pyadi-iio is not installed. Install it with: pip install pyadi-iio pylibiio": "pyadi-iio не встановлено. Встановіть: pip install pyadi-iio pylibiio",
    "pylibiio (the iio module) is not installed — skipping the context read": "pylibiio (модуль iio) не встановлено — пропускаю читання контексту",
    "scenario file or name from config/scenarios": "файл або ім'я сценарію з config/scenarios",
    "sweep: specify channels | band | group | freq_list_mhz": "sweep: задайте channels | band | group | freq_list_mhz",
    "tx_hardwaregain, dB (0=max)": "tx_hardwaregain, дБ (0=макс)",
    "{ch} @ {mhz} MHz, {gain} dB": "{ch} @ {mhz} МГц, {gain} дБ",
    "{v} dB": "{v} дБ",
    "— all —": "— всі —",
    "— start: {name} —": "— старт: {name} —",
    "■ Stop": "■ Стоп",
    "▶ Start": "▶ Старт",
    "⚠ ALIASING (raise fs)": "⚠ АЛІАСИНГ (підніміть fs)",
}


def set_language(lang: str) -> None:
    """Set the active language ('en' or 'uk'). Unknown values fall back to English."""
    global _lang
    _lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def get_language() -> str:
    return _lang


def available_languages() -> List[str]:
    return list(LANGUAGES)


def detect_language() -> str:
    """Language from the FPV_LANG env var, else English."""
    env = (os.environ.get("FPV_LANG") or "").strip().lower()
    return env if env in LANGUAGES else DEFAULT_LANGUAGE


def t(text: str, **kwargs) -> str:
    """Translate ``text`` into the active language and format it with ``kwargs``."""
    out = CATALOG_UK.get(text, text) if _lang == "uk" else text
    if kwargs:
        try:
            out = out.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass  # never let a bad placeholder break the UI
    return out
