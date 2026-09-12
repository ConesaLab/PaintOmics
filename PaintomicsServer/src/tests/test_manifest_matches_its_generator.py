#!/usr/bin/env python3
"""The served example manifest must say what its generator says.

Why this exists
---------------
`examplefiles/datasets/manifest.json` is what the server hands the browser
(`ExampleDatasets.catalogueForClient`, reached by `/example_datasets`), and it
is rendered as the **Exercises** list in the Step 1 example picker. It is
generated from the scenario dictionaries in
`AdminTools/scripts/exampledata/legacy.py` -- but nothing regenerates the bundle
in CI, so the two can drift, and the drift is invisible: the generator is not
imported by the running server, so a stale manifest keeps being served while the
source of truth reads correctly.

That is not hypothetical. The STATegra MORE example advertised

    All three regulatory engines (Rust PLS1, R PLS1, R MLR)

which went stale when a fourth, `rust-mlr`, was added. It was fixed in
`legacy.py` and in the generated `README.md` and *not* in `manifest.json`, so the
picker went on showing "three" to every user while two files in the repo said
four. Only the manifest is served.

So: every `tests` entry a scenario declares in the generator must appear
verbatim in the manifest the server ships.

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
GENERATOR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "AdminTools", "scripts", "exampledata", "legacy.py"))


def _manifestScenarios():
    with open(MANIFEST, encoding="utf-8") as handle:
        return {s["id"]: s for s in json.load(handle)["scenarios"] if "id" in s}


def _generatorScenarios():
    """`id` -> its `tests` list, read out of the generator's literals.

    Parsed rather than imported: `legacy.py` pulls in the whole AdminTools
    scenario stack, which wants a KEGG database and a server config this test
    has no use for. The two literals sit next to each other in one dict, so a
    scan for `"id": "..."` followed by the next `"tests": [...]` recovers the
    pairing without evaluating anything.
    """
    with open(GENERATOR, encoding="utf-8") as handle:
        source = handle.read()
    out = {}
    for match in re.finditer(r'"id":\s*"([^"]+)"', source):
        rest = source[match.end():]
        tests = re.search(r'"tests":\s*\[(.*?)\]', rest, re.S)
        nextId = re.search(r'"id":\s*"[^"]+"', rest)
        if not tests or (nextId and nextId.start() < tests.start()):
            continue
        out[match.group(1)] = re.findall(r'"((?:[^"\\]|\\.)*)"', tests.group(1))
    return out


class ManifestMatchesItsGeneratorTest(unittest.TestCase):

    def test_the_generator_declares_some_scenarios(self):
        """A parse that silently finds nothing would pass every test below."""
        generated = _generatorScenarios()
        self.assertGreaterEqual(
            len(generated), 4,
            "parsed %d scenarios out of legacy.py; the literal shape it is "
            "read from has probably changed" % len(generated))

    def test_every_generated_exercise_is_in_the_served_manifest(self):
        served = _manifestScenarios()
        for scenarioId, tests in sorted(_generatorScenarios().items()):
            if scenarioId not in served:
                continue
            have = served[scenarioId].get("tests") or []
            for entry in tests:
                self.assertIn(
                    entry, have,
                    "legacy.py declares %r for scenario %r but manifest.json "
                    "does not carry it. The manifest is what /example_datasets "
                    "serves and what the Step 1 picker renders, so editing only "
                    "the generator changes nothing a user sees."
                    % (entry, scenarioId))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
