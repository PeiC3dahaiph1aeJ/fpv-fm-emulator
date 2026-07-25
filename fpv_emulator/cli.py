"""Command-line interface for the FPV FM video emulator.

Приклади:
  python -m fpv_emulator.cli probe
  python -m fpv_emulator.cli list-bands
  python -m fpv_emulator.cli list-scenarios
  python -m fpv_emulator.cli gen --pattern bars --standard PAL --sample-rate 8e6 --out out.iq
  python -m fpv_emulator.cli tx --backend null --channel R1 --pattern bars
  python -m fpv_emulator.cli tx --backend pluto --uri ip:192.168.2.1 --scenario sweep_raceband
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from . import __version__
from .backends import TxConfig, make_sink
from .bands import load_band_table
from .config import list_scenarios, load_scenario
from .fm import to_int16_iq
from .probe import probe
from .scenarios import ScenarioRunner, SignalParams
from .signal_gen import generate_frame_iq
from .video import get_standard, list_all_patterns, list_color_patterns, list_patterns


def _fmt_event(e: dict) -> str:
    action = e.get("action")
    if action == "tune":
        s = f"[TUNE] {e.get('channel','?')} @ {e.get('freq_mhz',0):.1f} МГц"
        if "gain_db" in e:
            s += f", підсил. {e['gain_db']} дБ"
        if "bw_mhz" in e:
            s += f", смуга ~{e['bw_mhz']:.1f} МГц"
        if "lap" in e:
            s += f" (коло {e['lap']})"
        return s
    if action == "power":
        return f"[PWR ] {e.get('channel','?')} @ {e.get('freq_mhz',0):.1f} МГц, підсил. {e.get('gain_db')} дБ"
    if action == "live_gain":
        return f"[GAIN] потужність -> {e.get('gain_db')} дБ (застосовано)"
    if action == "pause":
        return f"[PAUSE] тиша {e.get('seconds')} с (RF off) @ {e.get('freq_mhz',0):.0f} МГц"
    if action == "multi":
        return (f"[MULTI] центр {e.get('center_mhz',0):.1f} МГц, {e.get('n_drones')} дрон(ів), "
                f"зсуви {e.get('offsets_mhz')} МГц, розмах ~{e.get('span_mhz',0):.1f} МГц")
    if action == "start":
        return f"[START] сценарій '{e.get('scenario')}' (тип {e.get('type')})"
    if action == "stop":
        return f"[STOP ] сценарій '{e.get('scenario')}'"
    return f"[{action}] {e}"


def cmd_probe(args) -> int:
    res = probe(uri=args.uri, do_range_test=not args.no_range_test)
    print(res.summary())
    return 0 if res.connected else 2


def cmd_list_bands(args) -> int:
    bt = load_band_table()
    for group in bt.groups():
        print(f"\n=== {group} ===")
        for ch in bt.channels_in_group(group):
            print(f"  {ch.name:5s}  {ch.freq_mhz:7.1f} МГц   (банд {ch.band})")
    return 0


def cmd_list_patterns(args) -> int:
    print("Люма-патерни (ч/б): " + ", ".join(list_patterns()))
    print("Кольорові патерни:  " + ", ".join(list_color_patterns())
          + "   (потрібна fs >= ~13 MSPS)")
    return 0


def cmd_list_scenarios(args) -> int:
    sc = list_scenarios()
    if not sc:
        print("Сценаріїв не знайдено у config/scenarios")
        return 0
    print("Доступні сценарії:")
    for name, path in sc.items():
        print(f"  {name:20s}  {path}")
    return 0


def cmd_list_devices(args) -> int:
    from .backends import soapy_enumerate
    devs = soapy_enumerate()
    if not devs:
        print("SoapySDR-пристроїв не знайдено (або SoapySDR не встановлено).")
        print("Windows: постав PothosSDR. Linux: apt install python3-soapysdr soapysdr-module-<драйвер>.")
        return 0
    print("Знайдені SDR (SoapySDR):")
    for d in devs:
        drv = d.get("driver", "?")
        label = d.get("label", "")
        print(f"  driver={drv}   {label}")
    return 0


def cmd_gen(args) -> int:
    std = get_standard(args.standard)
    fs = float(args.sample_rate)
    frame = generate_frame_iq(args.pattern, std, fs, float(args.deviation))
    iq16 = to_int16_iq(frame.iq)
    # запис
    if args.out.endswith(".npy"):
        import numpy as np
        np.save(args.out, iq16.astype("complex64"))
    else:
        import numpy as np
        inter = np.empty(iq16.size * 2, dtype=np.int16)
        inter[0::2] = iq16.real.astype(np.int16)
        inter[1::2] = iq16.imag.astype(np.int16)
        inter.tofile(args.out)
    print(f"Записано {frame.n_samples} семплів ({frame.duration_s*1e3:.2f} мс/кадр) -> {args.out}")
    print(f"  стандарт={frame.std_name}, патерн={frame.pattern}, fs={fs/1e6:.2f} MSPS")
    print(f"  девіація pp={frame.deviation_pp_hz/1e6:.2f} МГц, зайнята смуга ~{frame.occupied_bw_hz/1e6:.1f} МГц")
    return 0


def _run_scenario(runner: ScenarioRunner, scenario: dict) -> int:
    stop = threading.Event()

    def _sigint(_sig, _frm):
        print("\nЗупинка…")
        stop.set()

    signal.signal(signal.SIGINT, _sigint)
    runner.run(scenario, stop=stop)
    return 0


def cmd_tx(args) -> int:
    bt = load_band_table()

    # зібрати сценарій: або з файлу, або одиночний із прапорців
    if args.scenario:
        scenario = load_scenario(args.scenario)
    else:
        if args.channel:
            ch = bt.channel(args.channel)
            freq_hz = ch.freq_hz
            ch_name = ch.name
        elif args.freq_mhz:
            freq_hz = float(args.freq_mhz) * 1e6
            ch_name = f"{args.freq_mhz}MHz"
        else:
            print("Задайте --channel, --freq-mhz або --scenario", file=sys.stderr)
            return 1
        scenario = {
            "name": f"static-{ch_name}",
            "type": "static",
            "signal": {
                "standard": args.standard,
                "pattern": args.pattern,
                "sample_rate": float(args.sample_rate),
                "deviation_pp_hz": float(args.deviation),
                "gain_db": float(args.gain),
            },
            "static": ({"channel": args.channel} if args.channel
                       else {"freq_mhz": float(args.freq_mhz)}),
        }
        if args.duration:
            scenario["static"]["hold_s"] = float(args.duration)

    # попередження щодо HW-діапазону
    sp = SignalParams.from_dict(scenario.get("signal"))
    fs = sp.sample_rate

    cfg = TxConfig(fs=fs, freq_hz=0.0, gain_db=sp.gain_db, uri=args.uri,
                   rf_bw_hz=min(fs, 20e6), device=args.device)
    sink = make_sink(args.backend, cfg, file_path=args.out)

    runner = ScenarioRunner(sink, bt, on_event=lambda e: print(_fmt_event(e)))
    print(f"Backend: {args.backend}  |  fs={fs/1e6:.2f} MSPS  |  сценарій: {scenario.get('name')}")
    try:
        return _run_scenario(runner, scenario)
    finally:
        sink.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fpv-emulator",
        description="Емулятор аналогового FPV-відео (ЧМ) на Pluto+ для перевірки детекторів FPV",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("probe", help="Знайти Pluto й визначити діапазон")
    pp.add_argument("--uri", default="ip:192.168.2.1")
    pp.add_argument("--no-range-test", action="store_true",
                    help="Не перестроювати TX під час проби")
    pp.set_defaults(func=cmd_probe)

    sub.add_parser("list-bands", help="Список діапазонів/каналів").set_defaults(func=cmd_list_bands)
    sub.add_parser("list-patterns", help="Список тестових патернів").set_defaults(func=cmd_list_patterns)
    sub.add_parser("list-scenarios", help="Список сценаріїв").set_defaults(func=cmd_list_scenarios)
    sub.add_parser("list-devices", help="Список SDR через SoapySDR").set_defaults(func=cmd_list_devices)

    pg = sub.add_parser("gen", help="Згенерувати IQ у файл (без апаратури)")
    pg.add_argument("--pattern", default="color_bars", choices=list_all_patterns())
    pg.add_argument("--standard", default="PAL50")
    pg.add_argument("--sample-rate", default="20e6")
    pg.add_argument("--deviation", default="6e6", help="девіація pp, Гц")
    pg.add_argument("--out", default="out.iq")
    pg.set_defaults(func=cmd_gen)

    pt = sub.add_parser("tx", help="Передавати (Pluto/file/null) або запустити сценарій")
    pt.add_argument("--backend", default="null", choices=["pluto", "soapy", "file", "null"])
    pt.add_argument("--uri", default="ip:192.168.2.1", help="URI Pluto (backend=pluto)")
    pt.add_argument("--device", default="driver=hackrf",
                    help="SoapySDR device args (backend=soapy): driver=hackrf|lime|uhd|bladerf")
    pt.add_argument("--channel", help="напр. R1")
    pt.add_argument("--freq-mhz", help="несуча вручну, МГц")
    pt.add_argument("--pattern", default="color_bars", choices=list_all_patterns())
    pt.add_argument("--standard", default="PAL50")
    pt.add_argument("--sample-rate", default="20e6")
    pt.add_argument("--deviation", default="6e6")
    pt.add_argument("--gain", default="-10", help="tx_hardwaregain, дБ (0=макс)")
    pt.add_argument("--duration", help="тримати N секунд (static)")
    pt.add_argument("--scenario", help="файл або ім'я сценарію з config/scenarios")
    pt.add_argument("--out", default="out.iq", help="файл для backend=file")
    pt.set_defaults(func=cmd_tx)

    return p


def _force_utf8_stdout() -> None:
    """У консолях Windows (cp1251/cp866) кирилиця псується — перемикаємо на UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv=None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
