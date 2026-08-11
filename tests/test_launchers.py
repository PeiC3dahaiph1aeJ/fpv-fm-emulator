"""The Windows launchers have to survive being downloaded and double-clicked.

Both failures below happened on a colleague's PC, on a machine where the project
had never run: they are not hypothetical.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

BATCH = sorted(ROOT.glob("*.bat"))


def test_there_are_launchers_to_check():
    assert {p.name for p in BATCH} >= {"setup.bat", "run_gui.bat"}


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_a_launcher_is_pure_ascii(path):
    """cmd.exe tracks its position in a batch file by BYTE offset. Under
    `chcp 65001` a multi-byte character makes it resume two bytes late on the
    following lines, so "echo" is read as "ho" and everything after it fails with
    "is not recognized". An em dash in setup.bat did exactly that."""
    data = path.read_bytes()
    bad = [(i, hex(b)) for i, b in enumerate(data) if b > 127]
    assert not bad, f"{path.name}: non-ASCII at {bad[:5]}"


@pytest.mark.parametrize("path", BATCH, ids=lambda p: p.name)
def test_a_launcher_has_windows_line_endings(path):
    """A GitHub ZIP hands out what the repository stores, so a .bat kept with LF
    is downloaded with LF, and cmd.exe mis-parses it. .gitattributes pins it."""
    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n"), f"{path.name} has bare LF endings"


def test_gitattributes_pins_the_line_endings():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in text


def test_setup_does_not_depend_on_python_being_on_the_path():
    """The python.org installer always provides the `py` launcher, and "Add to
    PATH" is the box people miss — which is exactly how a machine ends up with
    Python installed and setup.bat unable to find it."""
    text = (ROOT / "setup.bat").read_text(encoding="ascii")
    assert "where py " in text and "py -3" in text
