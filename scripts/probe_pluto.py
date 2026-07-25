#!/usr/bin/env python
"""Автономна проба Pluto: чип, прошивка, реальні межі перестроювання TX.

Використання:
    python scripts/probe_pluto.py [--uri ip:192.168.2.1] [--no-range-test]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.probe import probe


def main() -> int:
    ap = argparse.ArgumentParser(description="Проба ADALM-Pluto / Pluto+")
    ap.add_argument("--uri", default="ip:192.168.2.1",
                    help="IIO URI (ip:192.168.2.1, usb:x.y.z, serial:...)")
    ap.add_argument("--no-range-test", action="store_true",
                    help="Не перестроювати TX під час проби")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    res = probe(uri=args.uri, do_range_test=not args.no_range_test)
    print(res.summary())
    return 0 if res.connected else 2


if __name__ == "__main__":
    raise SystemExit(main())
