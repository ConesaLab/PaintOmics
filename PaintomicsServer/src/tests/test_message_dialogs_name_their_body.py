#!/usr/bin/env python3
"""A dialog's body must be passed under a key `showMessage` actually reads.

Why this exists
---------------
`showMessage(title, data)` in Util.js takes its body from `data.message`:

    var message = (data.message || "");

There is no `data.text` branch, and no complaint when an unknown key is passed.
A call site that spells the body `text:` therefore opens a dialog with a heading,
two buttons, and nothing between them -- the caller looks correct, the dialog is
empty, and only a reader who opens Util.js can tell why.

That shipped once. `21f7c829` added the refusal shown when a server has no
more-rs binary:

    showWarningMessage("No regulatory model can be run here", {
        text: "This server has no more-rs binary installed, ...",

Measured in Chrome against a server with the binary removed, the same call made
both ways:

    text:    -> "No regulatory model can be run here"
    message: -> "No regulatory model can be run here This server has no more-rs
                 binary installed, so a regulatory analysis cannot be started.
                 Please contact the administrator."

The refusal is the one dialog whose body carries the whole point -- what is
wrong and who to ask -- and it was the one rendering blank, on the only servers
that can reach it.

How it works: the keys are read out of `showMessage` itself rather than listed
here, so adding a new option to Util.js does not make this test wrong. Every
`show*Message("...", {...})` call site in app/ is then checked against them.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_message_dialogs_name_their_body
"""
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

CLIENT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html"))

APP = os.path.join(CLIENT, "app")
UTIL = os.path.join(APP, "view", "common", "Util.js")

# `showMessage` and the four wrappers that forward `data` to it unchanged.
CALL = re.compile(r"\bshow(?:Warning|Error|Info|Success)?Message\s*\(")


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def matchingBrace(text, start):
    """Index of the `}` closing the `{` at `start`, or -1.

    Brace-counting, not a JavaScript parser: it steps over string literals and
    comments so that a `{` inside either does not shift the count. Good enough
    for an options literal, and the alternative is a regex that cannot nest.
    """
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char in "\"'":
            quote = char
            i += 1
            while i < len(text) and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                return -1
        elif text.startswith("/*", i):
            i = text.find("*/", i)
            if i < 0:
                return -1
            i += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def optionKeysReadByShowMessage():
    """Every `data.<key>` that the body of `showMessage` looks at."""
    text = read(UTIL)
    start = text.index("function showMessage(title, data)")
    brace = text.index("{", start)
    end = matchingBrace(text, brace)
    assert end > 0, "could not find the end of showMessage"
    return set(re.findall(r"\bdata\.([A-Za-z_$][\w$]*)", text[brace:end + 1]))


def topLevelKeys(literal):
    """The keys of an object literal, ignoring anything nested inside it."""
    keys = []
    depth = 0
    i = 0
    while i < len(literal):
        char = literal[i]
        if char in "\"'":
            quote = char
            i += 1
            while i < len(literal) and literal[i] != quote:
                i += 2 if literal[i] == "\\" else 1
        elif literal.startswith("//", i):
            i = literal.find("\n", i)
            if i < 0:
                break
        elif literal.startswith("/*", i):
            i = literal.find("*/", i)
            if i < 0:
                break
            i += 1
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif depth == 1:
            found = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:", literal[i:])
            if found and (i == 0 or literal[i - 1] in "{,\n\t "):
                keys.append(found.group(1))
                i += found.end() - 1
        i += 1
    return keys


def callSites():
    """(path, line, keys) for every show*Message call with an object literal."""
    sites = []
    for root, _dirs, names in os.walk(APP):
        for name in sorted(names):
            if not name.endswith(".js"):
                continue
            path = os.path.join(root, name)
            text = read(path)
            for found in CALL.finditer(text):
                brace = text.find("{", found.end())
                if brace < 0:
                    continue
                # Only an options literal that belongs to *this* call: nothing
                # but the title argument may sit between the paren and the `{`.
                between = text[found.end():brace]
                if between.count("(") != between.count(")") or ";" in between:
                    continue
                end = matchingBrace(text, brace)
                if end < 0:
                    continue
                line = text.count("\n", 0, found.start()) + 1
                rel = os.path.relpath(path, CLIENT)
                sites.append((rel, line, topLevelKeys(text[brace:end + 1])))
    return sites


class ShowMessageContractTest(unittest.TestCase):
    """Premise checks: the assertion below is reading what it thinks it is."""

    def test_show_message_reads_its_body_from_message(self):
        self.assertIn("message", optionKeysReadByShowMessage())

    def test_show_message_has_no_text_option(self):
        # The whole point: `text` is not a synonym, it is ignored.
        self.assertNotIn("text", optionKeysReadByShowMessage())

    def test_the_scan_finds_the_call_sites_it_is_meant_to_check(self):
        sites = callSites()
        self.assertGreater(len(sites), 20,
                           "found only %d call sites; the scan is broken, not "
                           "the client" % len(sites))
        self.assertTrue(any("message" in keys for _p, _l, keys in sites))


class NoDialogPassesAnIgnoredKeyTest(unittest.TestCase):

    def test_every_option_is_one_showMessage_reads(self):
        known = optionKeysReadByShowMessage()
        offenders = [
            "%s:%d passes %s" % (path, line, ", ".join(sorted(unknown)))
            for path, line, keys in callSites()
            for unknown in [set(keys) - known]
            if unknown
        ]
        self.assertEqual(offenders, [], "\n".join([
            "These show*Message call sites pass options showMessage ignores:",
            "",
        ] + offenders + [
            "",
            "showMessage reads: %s." % ", ".join(sorted(known)),
            "A body belongs under `message`; `text` renders nothing.",
        ]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
