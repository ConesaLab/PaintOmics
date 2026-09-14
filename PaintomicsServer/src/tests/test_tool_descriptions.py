#!/usr/bin/env python3
"""Nothing in the AI package may outlive its last caller.

A prompt nobody sends and a function nobody calls both read as live code to
the next person who opens the file: they get edited, tuned and measured while
the product never runs them. The previous AI interpreter left seven such
definitions behind before its removal; these two guards are what keeps the
package honest now that the graph walk is the only interpretation.

    python -m src.tests.test_tool_descriptions
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

_PASSED, _FAILED = [], []


# Prompts that are defined but used nowhere. The two V2 prompts that sat here
# since March 2026 were deleted in the dead-code pass (reports/deadcode.md);
# a new orphan goes in here only with a reason it is being kept.
KNOWN_ORPHAN_PROMPTS = set()


def test_no_new_orphan_prompts():
    """A prompt nobody sends is dead weight that reads as live configuration.

    SYSTEM_PROMPT_DELEGATED_INTERPRET was written, measured, reverted on the
    evidence -- citations fell 5 -> 18 under the old prompt against 7 -> 3 under
    it -- and then sat in the file looking exactly like the prompt that is
    actually used. The next person to tune delegation would have edited it and
    measured nothing.
    """
    from src.classes.AIInterpret import prompts

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    sources = []
    for base, _dirs, files in os.walk(os.path.join(root, "src")):
        if "/tests" in base or "__pycache__" in base:
            continue
        for name in files:
            if name.endswith(".py") and name != "prompts.py":
                with open(os.path.join(base, name)) as handle:
                    sources.append(handle.read())
    blob = "\n".join(sources)

    orphans = sorted(name for name in dir(prompts)
                     if name.startswith("SYSTEM_PROMPT")
                     and name not in KNOWN_ORPHAN_PROMPTS
                     and name not in blob)
    assert not orphans, (
        "prompt constants nobody sends: %s -- delete them or wire them up"
        % ", ".join(orphans))


# Top-level definitions in src/classes/AIInterpret that nothing calls and that
# are kept on purpose. Empty since the 2026-09 cleanup removed the seven that
# had accumulated (the v1 redactor, verify_report, the two-pass and synthesis
# prompt builders, the interpretation executor and the Verdict model); a NEW
# one fails the build.
KNOWN_ORPHAN_DEFS = set()


def _names_read_in(source):
    """Every name this source READS, and how often, by the parser's reckoning.

    Counts ast.Name loads and attribute accesses; ignores every mention that is
    only text -- docstrings, comments, prose in a commit-worthy explanation.
    That distinction is the whole point: a function referred to in its own
    docstring is not a function anybody calls.

    One pass per FILE, not one pass per (name, file). The per-name shape this
    replaces re-parsed the whole tree for every definition it wanted to score:
    189 definitions x 209 non-test sources = 39,501 ast.parse calls for a
    question 209 parses can answer. Measured on this checkout, the suite went
    from 50.2 s to 1.5 s with no change to what it asserts -- and it was 47% of
    one unit-test shard's budget against a 10-minute cap the shard has hit
    three times.
    """
    import ast as _ast
    from collections import Counter
    read = Counter()
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return read
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            read[node.id] += 1
        elif isinstance(node, _ast.Attribute):
            read[node.attr] += 1
    return read


def test_no_new_orphan_definitions_in_the_ai_package():
    """A function nobody calls reads as live code to the next person.

    Every top-level definition in src/classes/AIInterpret must be read from
    somewhere outside the tests; the exceptions live in KNOWN_ORPHAN_DEFS.
    """
    import ast
    from collections import Counter

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    package = os.path.join(root, "src", "classes", "AIInterpret")
    sources = {}
    for base, dirs, files in os.walk(os.path.join(root, "src")):
        if "__pycache__" in base:
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                with open(path) as handle:
                    sources[path] = handle.read()

    # Scored once, off the sources that are not tests -- a helper alive only in
    # its own test is still dead in the product.
    read_outside_tests = Counter()
    for other, body in sources.items():
        if "/tests/" not in other.replace("\\", "/"):
            read_outside_tests.update(_names_read_in(body))

    orphans = []
    for path, text in sorted(sources.items()):
        if not path.startswith(package):
            continue
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("__") or name in KNOWN_ORPHAN_DEFS:
                    continue
                # Counted from the AST, not from text. A regex over sources
                # counts the name in its own docstring, in a comment, and in a
                # test -- so _writer_window scored three "uses" while having
                # zero call sites, and this test passed while the function was
                # dead. Tests are excluded on purpose: a helper alive only in
                # its own test is still dead in the product.
                if read_outside_tests[name] <= 0:
                    orphans.append("%s:%s" % (os.path.basename(path), name))
    assert not orphans, (
        "definitions nothing calls: %s -- wire them up, delete them, or add them "
        "to KNOWN_ORPHAN_DEFS with a reason" % ", ".join(orphans))


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_no_new_orphan_prompts,
              test_no_new_orphan_definitions_in_the_ai_package):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
