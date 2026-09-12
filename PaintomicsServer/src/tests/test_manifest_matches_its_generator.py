#!/usr/bin/env python3
"""The served example manifest must say what its generator says.

Why this exists
---------------
`examplefiles/datasets/manifest.json` is what the server hands the browser
(`ExampleDatasets.catalogueForClient`, reached by `/example_datasets`), and it
is rendered as the **Exercises** list in the Step 1 example picker. It is
generated from the scenario dictionaries in
`AdminTools/scripts/exampledata/` -- `scenarios.py` and `legacy.py`, which
`__main__.py` builds together -- but nothing regenerates the bundle in CI, so
the two can drift, and the drift is invisible: the generators are not imported
by the running server, so a stale manifest keeps being served while the source
of truth reads correctly.

That is not hypothetical. The STATegra MORE example advertised

    All three regulatory engines (Rust PLS1, R PLS1, R MLR)

which went stale when a fourth, `rust-mlr`, was added. It was fixed in
`legacy.py` and in the generated `README.md` and *not* in `manifest.json`, so the
picker went on showing "three" to every user while two files in the repo said
four. Only the manifest is served.

So: every `tests` line the manifest SERVES must still exist in a generator
source. That direction is the checkable one -- `scenarios.py` writes
`"id": scenarioId`, a variable, so manifest entries cannot be paired back to a
literal id -- and it is the direction that catches the defect: a served string
the generator no longer contains is a file nobody regenerated.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_manifest_matches_its_generator
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

MANIFEST = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "examplefiles", "datasets", "manifest.json"))
# Both halves of the catalogue. `__main__.py` builds the manifest from
# `scenarios.CATALOGUE` and `legacy.CATALOGUE` together (lines 174 and 182), so
# reading only one of them silently skips two thirds of the scenarios -- which
# is what the first version of this test did.
GENERATORS = [
    os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "AdminTools", "scripts", "exampledata", name))
    for name in ("legacy.py", "scenarios.py", "stategrametabolomics.py")
]

# `stategra-metabolomics-replicates` is not built by the bundle generator at
# all. `__main__.py` imports only `legacy` and `scenarios`, and their two
# CATALOGUEs hold eleven builders for the manifest's twelve scenarios;
# `stategrametabolomics.py` is a standalone script run by hand
# (`PYTHONPATH=. python src/AdminTools/scripts/exampledata/stategrametabolomics.py`)
# and its three `tests` lines exist nowhere but the manifest. So it is exempt
# from the drift check below -- there is nothing to drift against -- and that
# is recorded here rather than silently skipped, because the underlying oddity
# is real: running the bundle generator would not reproduce this entry.
_MAINTAINED_OUTSIDE_THE_BUNDLE = {"stategra-metabolomics-replicates"}


def _manifestScenarios():
    with open(MANIFEST, encoding="utf-8") as handle:
        return {s["id"]: s for s in json.load(handle)["scenarios"] if "id" in s}


def _generatorSource():
    """Every generator source, concatenated.

    Checked as text, and in the manifest -> generator direction, because the
    ids cannot be paired: `scenarios.py` writes `"id": scenarioId`, a variable,
    so there is no literal to match a manifest entry against. The direction
    that matters is covered anyway -- a manifest string that no longer appears
    in any generator is a manifest nobody regenerated, which is exactly the
    defect this exists to catch.
    """
    out = []
    for path in GENERATORS:
        with open(path, encoding="utf-8") as handle:
            out.append(handle.read())
    return "\n".join(out)


class ManifestMatchesItsGeneratorTest(unittest.TestCase):

    def test_the_generator_sources_are_readable_and_substantial(self):
        """A source that failed to load would make every check below vacuous."""
        source = _generatorSource()
        self.assertGreater(len(source), 20000,
                           "generator sources look truncated: %d chars" % len(source))
        for path in GENERATORS:
            self.assertTrue(os.path.exists(path), "missing generator %s" % path)

    def test_every_served_exercise_still_exists_in_a_generator(self):
        """Every `tests` line the manifest serves must be in a generator.

        A line the generator no longer contains is a line nobody regenerated:
        the source of truth was edited and the served file was not. That is how
        `All three regulatory engines (Rust PLS1, R PLS1, R MLR)` went on being
        shown in the Step 1 picker after the fourth engine was added and the
        generator corrected.
        """
        source = _generatorSource()
        for scenarioId, scenario in sorted(_manifestScenarios().items()):
            if scenarioId in _MAINTAINED_OUTSIDE_THE_BUNDLE:
                continue
            for entry in scenario.get("tests") or []:
                self.assertIn(
                    entry, source,
                    "manifest.json serves %r for scenario %r, but no generator "
                    "source contains it. manifest.json is what "
                    "/example_datasets returns and what the Step 1 picker "
                    "renders, so it has gone stale against the thing that "
                    "writes it." % (entry, scenarioId))

    def test_no_scenario_miscounts_the_regulatory_engines(self):
        """The specific drift that got through: a hardcoded engine count.

        MOREServlet.MORE_ENGINES is the catalogue; an example that names a
        number has to agree with it, and nothing else checks that.
        """
        from src.servlets.MOREServlet import MORE_ENGINES
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
        expected = words.get(len(MORE_ENGINES), str(len(MORE_ENGINES)))
        pattern = re.compile(r"All (\w+) regulatory engines", re.I)
        for scenarioId, scenario in sorted(_manifestScenarios().items()):
            for entry in scenario.get("tests") or []:
                found = pattern.search(entry)
                if not found:
                    continue
                self.assertEqual(
                    expected, found.group(1).lower(),
                    "scenario %r says %r, but MOREServlet.MORE_ENGINES has %d "
                    "entries" % (scenarioId, entry, len(MORE_ENGINES)))


    def test_a_scenario_that_times_engines_times_all_of_them(self):
        """A runtime table must cover the catalogue, not a subset of it.

        `stategra-more` exists to be the example you can run on any engine, and
        says so: "All four fit inside the 1800 s job timeout, which is the
        property that makes this dataset usable as the example for the whole
        engine choice". A table missing an engine leaves the scenario
        advertising one whose cost nothing recorded -- which is what happened
        when `rust-mlr` was added to the catalogue and not to the table.
        """
        from src.servlets.MOREServlet import MORE_ENGINES
        catalogue = {entry["id"] for entry in MORE_ENGINES}
        for scenarioId, scenario in sorted(_manifestScenarios().items()):
            timings = (scenario.get("expected") or {}).get("measuredRuntimeSeconds")
            if not timings:
                continue
            self.assertEqual(
                catalogue, set(timings),
                "scenario %r times %s but MOREServlet.MORE_ENGINES offers %s; "
                "an engine with no measured runtime here is one the example "
                "cannot honestly claim to cover"
                % (scenarioId, sorted(timings), sorted(catalogue)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
