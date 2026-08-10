"""Every translatable string the code uses must exist in the Ukrainian catalog.

t() falls back to its English key when the catalog lacks it, and never says so.
That is the right runtime behaviour — a missing translation must not break a
transmission — but it means a forgotten entry ships as a half-Ukrainian window to
an operator who runs the tool in Ukrainian, with nothing to indicate it. This
test is the thing that says so.

Strings reach t() from two places: literals in the code, and the ``name:`` fields
of the YAML in config/, which the GUI and CLI pass through t() as they display
them. Both are collected here. Private scenarios (``*_local.yaml``, gitignored)
are skipped — they hold real detector frequencies and are nobody else's UI.

The placeholders are checked too, because t() swallows a formatting error and
returns the string unformatted: a translation that renamed {rate} to {частота}
would print braces at the operator instead of a number.
"""
import ast
import os
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from fpv_emulator.i18n import CATALOG_UK

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
#: t() in the package, _t() in run_gui.py — that one degrades to English when the
#: package itself failed to import, which is exactly when it must still speak.
_FUNCS = {"t", "_t"}

#: Functions that translate their arguments instead of being handed a t() call.
#: The literal then sits at the CALL SITE, where a scan for t("...") cannot see it
#: — and both of run_gui.py's startup-failure strings were in fact untranslated
#: for exactly that reason, with this test passing.
_WRAPPERS = {"_show_error": {"args": (0,), "kwargs": ("message", "hint")}}


def _keys_in_code():
    """Every t("literal") in the project. A t(variable) cannot be seen statically."""
    out = set()
    files = list(ROOT.glob("*.py"))
    for sub in ("fpv_emulator", "gui", "scripts"):
        files += list((ROOT / sub).rglob("*.py"))
    for path in files:
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            wanted = []
            if node.func.id in _FUNCS and node.args:
                wanted.append(node.args[0])
            spec = _WRAPPERS.get(node.func.id)
            if spec:
                wanted += [node.args[i] for i in spec["args"] if i < len(node.args)]
                wanted += [kw.value for kw in node.keywords if kw.arg in spec["kwargs"]]
            for arg in wanted:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def _keys_in_config():
    """The ``name:`` fields of the shipped YAML — displayed through t()."""
    out = set()
    for path in (ROOT / "config").rglob("*.yaml"):
        if path.stem.endswith("_local"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                name = node.get("name")
                if isinstance(name, str) and name:
                    out.add(name)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        # bands.yaml is documented as editable and the GUI renders a band as
        # t(entry.get("name", key)) — an entry added without a name displays its
        # raw key through t(). Only that one mapping: every other key in these
        # files is structure, never shown to anyone.
        if path.name == "bands.yaml":
            for key, entry in (data.get("bands") or {}).items():
                if not (isinstance(entry, dict) and entry.get("name")):
                    out.add(str(key))
    return out


USED = _keys_in_code() | _keys_in_config()


def test_the_scan_found_the_strings():
    """Guard the guard: a broken extractor would make every check below vacuous."""
    assert len(_keys_in_code()) > 150
    assert len(_keys_in_config()) > 10


def test_every_string_has_a_ukrainian_translation():
    missing = sorted(USED - set(CATALOG_UK))
    assert not missing, "no Ukrainian for:\n" + "\n".join(repr(m) for m in missing)


def test_translations_keep_the_same_placeholders():
    bad = [(en, uk) for en, uk in CATALOG_UK.items()
           if set(_PLACEHOLDER.findall(en)) != set(_PLACEHOLDER.findall(uk))]
    assert not bad, "placeholders differ:\n" + "\n".join(f"{e!r}\n  -> {u!r}" for e, u in bad)


def test_no_stale_entries():
    """A key nothing uses is dead weight that hides the ones that matter."""
    stale = sorted(set(CATALOG_UK) - USED)
    assert not stale, "translated but never used:\n" + "\n".join(repr(s) for s in stale)
