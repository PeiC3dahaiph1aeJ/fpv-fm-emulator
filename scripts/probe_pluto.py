#!/usr/bin/env python
"""Standalone Pluto probe: chip, firmware, real TX tuning limits.

Usage:
    python scripts/probe_pluto.py [--uri ip:192.168.2.1] [--no-range-test] [--lang uk]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.cli import _force_utf8_stdout, _preapply_language
from fpv_emulator.i18n import available_languages, get_language, set_language, t
from fpv_emulator.probe import probe


def main() -> int:
    # Same prologue as `python -m fpv_emulator.cli`, so the two entry points cannot drift:
    # UTF-8 on stdout *and* stderr before anything is printed, and the language applied
    # before argparse renders any help text (argparse builds --help inside parse_args).
    _force_utf8_stdout()
    _preapply_language()

    ap = argparse.ArgumentParser(description=t("Probe an ADALM-Pluto / Pluto+"))
    ap.add_argument("--uri", default="ip:192.168.2.1",
                    help="IIO URI (ip:192.168.2.1, usb:x.y.z, serial:...)")
    ap.add_argument("--no-range-test", action="store_true",
                    help=t("Do not retune TX during the probe"))
    ap.add_argument("--lang", choices=available_languages(), default=get_language(),
                    help=t("interface language (default: from the FPV_LANG env var, else en)"))
    args = ap.parse_args()
    set_language(args.lang)

    res = probe(uri=args.uri, do_range_test=not args.no_range_test)
    print(res.summary())
    return 0 if res.connected else 2


if __name__ == "__main__":
    raise SystemExit(main())
