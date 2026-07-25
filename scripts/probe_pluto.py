#!/usr/bin/env python
"""Standalone Pluto probe: chip, firmware, real TX tuning limits.

Usage:
    python scripts/probe_pluto.py [--uri ip:192.168.2.1] [--no-range-test] [--lang uk]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.i18n import available_languages, detect_language, set_language, t
from fpv_emulator.probe import probe


def main() -> int:
    # apply the language before argparse renders any help text
    for i, item in enumerate(sys.argv[1:]):
        if item == "--lang" and i + 2 <= len(sys.argv[1:]):
            set_language(sys.argv[1:][i + 1])
        elif item.startswith("--lang="):
            set_language(item.split("=", 1)[1])

    ap = argparse.ArgumentParser(description=t("Probe an ADALM-Pluto / Pluto+"))
    ap.add_argument("--uri", default="ip:192.168.2.1",
                    help="IIO URI (ip:192.168.2.1, usb:x.y.z, serial:...)")
    ap.add_argument("--no-range-test", action="store_true",
                    help=t("Do not retune TX during the probe"))
    ap.add_argument("--lang", choices=available_languages(), default=detect_language(),
                    help=t("interface language (default: from the FPV_LANG env var, else en)"))
    args = ap.parse_args()
    set_language(args.lang)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    res = probe(uri=args.uri, do_range_test=not args.no_range_test)
    print(res.summary())
    return 0 if res.connected else 2


if __name__ == "__main__":
    raise SystemExit(main())
