"""The --firmware flag must reach the device, on every command that opens one.

A flag that parses, is accepted, and then does not reach the code that opens the
board is the worst outcome of this change: the operator sets it, sees no error,
and believes it applied. TxConfig is built in three places and a fourth device
open exists in latency.py, so the check is mechanical rather than by inspection.
"""
import ast
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from fpv_emulator.cli import build_parser
from fpv_emulator.firmware import AUTO, PROFILE_KEYS

#: the subcommands that open an SDR and set a sample rate
DEVICE_CMDS = ("tx", "bench-tx", "latency")
#: minimum arguments each needs to parse
_REQUIRED = {"latency": ["--port", "COM5"]}


def _parse(cmd, *extra):
    return build_parser().parse_args([cmd] + _REQUIRED.get(cmd, []) + list(extra))


@pytest.mark.parametrize("cmd", DEVICE_CMDS)
def test_the_flag_defaults_to_auto(cmd):
    assert _parse(cmd).firmware == AUTO


@pytest.mark.parametrize("cmd", DEVICE_CMDS)
def test_the_flag_is_accepted_after_the_subcommand(cmd):
    assert _parse(cmd, "--firmware", "tezuka").firmware == "tezuka"


@pytest.mark.parametrize("cmd", DEVICE_CMDS)
def test_the_flag_is_accepted_before_the_subcommand(cmd):
    """argparse.SUPPRESS on the shared parent is what stops the subparser from
    overwriting a value given ahead of it — without it the flag silently resets."""
    args = build_parser().parse_args(
        ["--firmware", "tezuka", cmd] + _REQUIRED.get(cmd, []))
    assert args.firmware == "tezuka"


@pytest.mark.parametrize("cmd", DEVICE_CMDS)
def test_a_nonsense_value_is_refused_at_the_parser(cmd):
    with pytest.raises(SystemExit):
        _parse(cmd, "--firmware", "stock")


def test_the_offered_values_are_the_ones_the_code_knows():
    for key in PROFILE_KEYS:
        assert _parse("tx", "--firmware", key).firmware == key


def test_commands_that_never_open_a_device_still_parse():
    for cmd in ("list-bands", "list-patterns", "list-scenarios", "serial-ports"):
        build_parser().parse_args([cmd])


# --------------------------- the wiring itself -----------------------------
def _txconfig_calls(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "TxConfig"]


@pytest.mark.parametrize("path", ["fpv_emulator/cli.py", "gui/app.py"])
def test_every_txconfig_carries_a_firmware_profile(path):
    """One construction site that forgets it makes the control a no-op there."""
    calls = _txconfig_calls(path)
    assert calls, f"no TxConfig built in {path} — has it moved?"
    for call in calls:
        assert any(kw.arg == "firmware" for kw in call.keywords), (
            f"{path}:{call.lineno} builds a TxConfig without firmware=")


def test_the_rf_benchmark_opens_the_device_the_same_way_the_sink_does():
    """bench-tx does not use PlutoSink (it needs RX too), so it is the one place
    the two paths can drift apart unnoticed."""
    src = (ROOT / "fpv_emulator/latency.py").read_text(encoding="utf-8")
    assert "apply_sample_rate(" in src, "bench-tx sets the rate its own way again"
    assert "pluto_class_for(" in src, "bench-tx ignores non-stock IIO device names"
    assert "sdr.sample_rate = " not in src
