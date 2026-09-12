#!/usr/bin/env python3
"""The regulatory-engine wording lives in the server catalogue, and only there.

What this guards
----------------
`MOREServlet.MORE_ENGINES` is the catalogue: four entries, each with a `label`
and a `detail`, served to the browser by `/more_backends`. The client kept a
second copy of all four descriptions in `MORE_ENGINES_FALLBACK`, for the case
where that route cannot be reached -- and the two drifted, as two copies of a
paragraph do. By the time they were compared the client's copy had lost the
speed figure and the recommendation, and both copies still told the user this
about MLR:

    "correlated regulators are collapsed into a group and one member is chosen
     at random to represent it, so re-running the same job can credit a
     different regulator"

which is false. `MORE/R/more.R:162` calls `set.seed(seed)` with `seed = 123`
defaulted in `more()`'s signature, and `runMORE.R` calls `more()` without
overriding it, so the draw is seeded: re-running an MLR job credits the *same*
regulator. Two engines running the bundled example twice each confirmed it --
identical tables, identical collinearity representatives.

The fallback still exists, and still lists every engine: a host that has R but
no `more-rs` binary must not lose the feature because one request failed. What
it no longer carries is prose. It holds the ids, the method each entry runs and
the fact that it is offered; the label is derived from method and engine, and
the description is left empty so the card renders nothing rather than a
sentence this client cannot vouch for.

So the rule is: no `detail` string from the server catalogue may appear in the
client source. A second copy is how the first one went stale.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_more_engine_text_has_one_home
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.servlets.MOREServlet import MORE_ENGINES

CLIENT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "PaintomicsClient", "public_html",
    "app", "view", "PathwayAcquisitionViews", "PA_Step1Views.js"))

# Long enough that a match is a copied sentence rather than a shared noun. The
# shortest `detail` in the catalogue is comfortably above this.
_PHRASE = 40


def _clientSource():
    with open(CLIENT, "r", encoding="utf-8") as handle:
        return handle.read()


def _fallbackBlock(source):
    """The MORE_ENGINES_FALLBACK initialiser, bracket-matched.

    Counting brackets rather than regex-matching to `]` because the entries are
    piped through `.map()` and a naive match stops at the first one.
    """
    start = source.index("var MORE_ENGINES_FALLBACK")
    depth, index = 0, source.index("[", start)
    opened = index
    while index < len(source):
        if source[index] == "[":
            depth += 1
        elif source[index] == "]":
            depth -= 1
            if depth == 0:
                return source[opened:index + 1]
        index += 1
    raise AssertionError("MORE_ENGINES_FALLBACK is not bracket-balanced")


class EngineTextHasOneHomeTest(unittest.TestCase):

    def test_no_catalogue_detail_is_copied_into_the_client(self):
        """No sentence from a server `detail` may appear in the client source."""
        source = _clientSource()
        for entry in MORE_ENGINES:
            detail = entry.get("detail") or ""
            # Compare on the opening clause: the client's copy was a trimmed
            # paraphrase, so requiring the whole string to match would let a
            # shortened duplicate straight through.
            head = " ".join(detail.split())[:_PHRASE]
            if not head:
                continue
            self.assertNotIn(
                head, " ".join(source.split()),
                "PA_Step1Views.js repeats the %r description from "
                "MOREServlet.MORE_ENGINES. The wording belongs in the "
                "catalogue only; the client receives it from /more_backends."
                % entry["id"])

    def test_the_fallback_carries_structure_and_no_prose(self):
        """Every fallback entry is ids and flags -- no sentence-length literal."""
        block = _fallbackBlock(_clientSource())
        literals = re.findall(r'"([^"\\]*)"', block)
        wordy = [text for text in literals if len(text) > _PHRASE]
        self.assertEqual(
            [], wordy,
            "MORE_ENGINES_FALLBACK has grown prose again: %r. It carries "
            "structure only; descriptions come from /more_backends." % wordy)

    def test_the_fallback_still_offers_every_catalogue_engine(self):
        """Dropping an engine here would take it away from an offline host.

        The fallback exists so a server that cannot answer `/more_backends`
        still gets a usable picker -- including on a host that has R and no
        `more-rs`, where offering only the Rust entries would leave nothing
        runnable.
        """
        block = _fallbackBlock(_clientSource())
        for entry in MORE_ENGINES:
            self.assertIn(
                '"%s"' % entry["id"], block,
                "MORE_ENGINES_FALLBACK no longer lists %r, so a client that "
                "cannot reach /more_backends would not offer it."
                % entry["id"])

    def test_every_fallback_entry_names_its_method(self):
        """`method` drives the posted `more_method` and the alpha/VIP toggle.

        The combo's change handler reads `method` off the selected record; an
        entry without one falls back to PLS1, which would silently run the
        wrong model for the two MLR rows and leave the alpha and VIP fields
        enabled on a model that has no use for either.
        """
        block = _fallbackBlock(_clientSource())
        entries = re.findall(r"\{[^{}]*\}", block)
        self.assertEqual(
            len(MORE_ENGINES), len(entries),
            "expected one fallback entry per catalogue engine, found %d"
            % len(entries))
        for entry in entries:
            self.assertRegex(
                entry, r'method:\s*"(PLS1|MLR)"',
                "fallback entry %s names no method" % entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
