"""The version must be one number, in a known shape, and match the CHANGELOG.

A version exists to answer "what exactly is running?" from a screenshot or a
pasted log. One that lags behind the code, or that disagrees with the changelog,
answers it wrongly — which is worse than not answering at all.
"""
import importlib.util
import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from fpv_emulator import __version__

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
#: "## 1.2.3 — 2026-08-10", the first such heading in the changelog
_ENTRY = re.compile(r"^##\s+(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\s+—\s+(\d{4}-\d{2}-\d{2})\s*$",
                    re.M)


def _changelog_entries():
    return _ENTRY.findall((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))


def test_the_version_is_a_version():
    assert _SEMVER.match(__version__), __version__


def test_the_changelog_leads_with_this_version():
    entries = _changelog_entries()
    assert entries, "no dated version headings in CHANGELOG.md"
    assert entries[0][0] == __version__, (
        f"__version__ is {__version__} but the changelog leads with {entries[0][0]} — "
        "bump both together")


def test_the_changelog_reads_newest_first_with_no_repeats():
    versions = [v for v, _ in _changelog_entries()]
    dates = [d for _, d in _changelog_entries()]
    assert len(set(versions)) == len(versions), "a version appears twice"
    assert dates == sorted(dates, reverse=True), "entries are not newest first"


def test_the_version_only_lives_in_one_place():
    """A second literal would be the one that goes stale."""
    others = []
    for path in list(ROOT.glob("*.py")) + list((ROOT / "fpv_emulator").rglob("*.py")) \
            + list((ROOT / "gui").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""__version__\s*=\s*["']""", line):
                others.append(f"{path.relative_to(ROOT).as_posix()}:{n}")
    assert others == ["fpv_emulator/__init__.py:12"], others


# --------------------------- the launcher's own copy -----------------------
def _run_gui_module():
    spec = importlib.util.spec_from_file_location("_run_gui_probe", ROOT / "run_gui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_launcher_reports_the_same_version():
    assert _run_gui_module()._version() == __version__


def test_the_launcher_still_finds_it_with_the_package_broken(monkeypatch):
    """That is the case it exists for: the dialog that says the GUI would not start
    has to say which build would not start."""
    module = _run_gui_module()
    monkeypatch.setitem(sys.modules, "fpv_emulator", None)
    assert module._version() == __version__


def test_the_launcher_never_raises_looking_for_it(monkeypatch, tmp_path):
    """A missing source tree must not turn a startup error into a second one."""
    module = _run_gui_module()
    monkeypatch.setitem(sys.modules, "fpv_emulator", None)
    monkeypatch.setattr(module.os.path, "dirname", lambda _p: str(tmp_path))
    assert module._version() == "?"


# --------------------------- where it is shown -----------------------------
@pytest.mark.parametrize("path,needle", [
    ("fpv_emulator/cli.py", "__version__"),
    ("gui/app.py", "__version__"),
    ("scripts/probe_pluto.py", "__version__"),
])
def test_the_places_that_show_it_import_it(path, needle):
    assert needle in (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["README.md", "README.uk.md"])
def test_the_readme_offers_this_version_for_download(path):
    """The download link is the one thing a colleague acts on; a stale tag in it
    hands them a different program from the one the page describes."""
    text = (ROOT / path).read_text(encoding="utf-8")
    tags = set(re.findall(r"refs/tags/v(\d+\.\d+\.\d+)", text))
    assert tags == {__version__}, f"{path} offers {tags or 'no tag'}, code is {__version__}"
