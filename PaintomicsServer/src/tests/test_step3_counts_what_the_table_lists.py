#!/usr/bin/env python3
"""Step 3's counts are the pathways the table lists, on every species.

Why this exists
---------------
The server sends KEGG's global map, Metabolic pathways (<organism>01100), with
every other matched pathway. The enrichment table never lists it and the
network never draws it (4_1_pathway_enrichment.md says why), but the summary
card counted it: "Pathways found" read one more than the table had rows on any
job that matched the map, which is nearly every job, and "Significant" one more
whenever the map passed. The network's "N of M KEGG pathways" counted it in M.

The rule pinned here: one predicate, paStep3IsGlobalMap, decides what the
global map is, on any organism code (a KEGG organism, the second rice entry, a
species installed from a KO table); the counts skip what it names; whether a
database is filtered is still asked of every pathway the server sent, as
before, because the server adjusted the p-values over a set that holds the map.

The real functions from PA_Step3Views.js run under node against a stand-in
model; no DOM.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_step3_counts_what_the_table_lists
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

STEP3_VIEWS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html", "app", "view", "PathwayAcquisitionViews",
    "PA_Step3Views.js"))

HELPER = "var paStep3IsGlobalMap = function"
COUNTS = "this.getTotalVisiblePathways = function(db)"
FILTERED = "this.isDatabaseFiltered = function(db)"


def read_source():
    with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_block(source, header):
    """`header` plus the brace-matched block after it, or None when absent."""
    start = source.find(header)
    if start == -1:
        return None
    opening = source.index("{", start + len(header) - 1)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces after %s" % header)


# A pathway as the view reads it, a model holding them, and a view with the
# extracted methods. One omic, so significance is the single-omic branch.
STAGE = r"""
function P(id, source, visible, p) {
    return {
        getID: function () { return id; },
        getSource: function () { return source; },
        isVisible: function () { return visible; },
        getSignificanceValues: function () { return {"Gene expression": [[10, 3, p]]}; },
        getGlobalOmicPvalues: function () { return {"Gene expression": p}; },
        getTotalGlobalPvalues: function () { return {}; },
        getCombinedSignificanceValueByMethod: function () { return p; }
    };
}
function stage(organism, pathways) {
    var model = {
        getOrganism: function () { return organism; },
        getPathways: function () { return pathways; },
        getPathwaysByDB: function (db) {
            return pathways.filter(function (p) { return p.getSource() == db; });
        }
    };
    var view = {
        model: {summary: [0, 0, 0, 0, {"Gene expression": 1}]},
        visualOptions: {selectedCombinedMethod: "Fisher"},
        getModel: function () { return model; }
    };
    METHODS
    return view;
}
"""


def run(organism, pathways, calls):
    """pathways: [[id, source, visible, p]]; calls: {label: js expression on `view`}."""
    source = read_source()
    helper = extract_block(source, HELPER)
    counts = extract_block(source, COUNTS)
    filtered = extract_block(source, FILTERED)
    if counts is None:
        raise AssertionError("getTotalVisiblePathways is missing from PA_Step3Views.js")
    methods = ";\n".join(block.replace("this.", "view.", 1) for block in (counts, filtered) if block) + ";"
    script = ((helper + ";\n" if helper else "") + STAGE.replace("METHODS", methods) +
              "var view = stage(%s, %s.map(function (a) { return P.apply(null, a); }));\n"
              % (json.dumps(organism), json.dumps(pathways)) +
              "var out = {};\n" +
              "".join("out[%s] = %s;\n" % (json.dumps(label), expression)
                      for label, expression in calls.items()) +
              "console.log(JSON.stringify(out));\n")
    directory = tempfile.mkdtemp(prefix="paintomics-step3-counts-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError("node failed:\n%s" % done.stderr)
        return json.loads(done.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def job(code):
    """A KEGG job that matched the global map (visible, significant) and three
    pathways the table lists, one of them hidden by the category filter, plus a
    Reactome pathway."""
    return [[code + "01100", "KEGG", True, 0.0001],
            [code + "00010", "KEGG", True, 0.01],
            [code + "04110", "KEGG", True, 0.5],
            [code + "00020", "KEGG", False, 0.001],
            ["R-XXX-0001", "Reactome", True, 0.02]]


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class CountsLeaveOutTheGlobalMapTest(unittest.TestCase):

    def test_pathways_found_and_significant_are_the_rows_the_table_lists(self):
        # mmu, the second rice entry, and a species installed from a KO table:
        # each keeps the map under its own code.
        for code in ("mmu", "dosa", "thaz"):
            out = run(code, job(code), {"kegg": "view.getTotalVisiblePathways('KEGG')",
                                        "reactome": "view.getTotalVisiblePathways('Reactome')",
                                        "all": "view.getTotalVisiblePathways()"})
            self.assertEqual(2, out["kegg"]["visible"],
                             "%s: 2 KEGG rows show; the global map is not one of them" % code)
            self.assertEqual(1, out["kegg"]["significative"],
                             "%s: the global map passed, but it is no row in the table" % code)
            self.assertEqual(3, out["kegg"].get("listed"),
                             "%s: the network's 'of M' is the 3 rows, hidden one included" % code)
            self.assertEqual({"listed": 1, "visible": 1, "significative": 1}, out["reactome"], code)
            self.assertEqual(4, out["all"].get("listed"), code)
            self.assertEqual(3, out["all"]["visible"], code)

    def test_another_organisms_map_is_an_ordinary_pathway(self):
        """Only the job's own <organism>01100 is the map it was given."""
        out = run("mmu", [["hsa01100", "KEGG", True, 0.01], ["map01100", "KEGG", True, 0.01],
                          ["mmu00010", "KEGG", True, 0.5]],
                  {"kegg": "view.getTotalVisiblePathways('KEGG')"})
        self.assertEqual(3, out["kegg"]["visible"])
        self.assertEqual(2, out["kegg"]["significative"])

    def test_the_predicate(self):
        source = read_source()
        helper = extract_block(source, HELPER)
        self.assertIsNotNone(helper, "paStep3IsGlobalMap is missing from PA_Step3Views.js")
        cases = [["mmu01100", "mmu", True], ["dosa01100", "dosa", True], ["thaz01100", "thaz", True],
                 ["osa01100", "dosa", False], ["mmu01100", "", False], ["mmu01100", None, False],
                 ["mmu00010", "mmu", False], ["R-MMU-01100", "mmu", False]]
        script = (helper + ";\nconsole.log(JSON.stringify(%s.map(function (c) { "
                  "return paStep3IsGlobalMap(c[0], c[1]); })));\n" % json.dumps(cases))
        directory = tempfile.mkdtemp(prefix="paintomics-step3-counts-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([c[2] for c in cases], json.loads(done.stdout))
        finally:
            shutil.rmtree(directory, ignore_errors=True)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class FilteredIsAskedOfEveryPathwayTest(unittest.TestCase):
    """Whether the adjusted p-values are recomputed must not move with the
    counts: hiding only the global map is a filter, as it always was."""

    def test_hiding_only_the_global_map_still_filters(self):
        pathways = job("mmu")
        pathways[3][2] = True                       # nothing hidden ...
        self.assertFalse(run("mmu", pathways, {"f": "view.isDatabaseFiltered('KEGG')"})["f"])
        pathways[0][2] = False                      # ... but the map
        self.assertTrue(run("mmu", pathways, {"f": "view.isDatabaseFiltered('KEGG')"})["f"])
        self.assertFalse(run("mmu", pathways, {"f": "view.isDatabaseFiltered('Reactome')"})["f"])

    def test_both_callers_ask_it(self):
        source = read_source()
        self.assertEqual(2, len(re.findall(r"isFiltered\[db\]\s*=\s*(?:this|me)\.isDatabaseFiltered\(db\);",
                                           source)))
        self.assertNotIn("getPathwaysByDB(db).length != ", source)


class OnePredicateDecidesTest(unittest.TestCase):
    """The table, the network and the counts all leave the map out, and through
    the same predicate: three hand-written comparisons are how the counts came
    to disagree with the table."""

    def test_no_other_01100_comparison_is_left(self):
        source = read_source()
        helper = extract_block(source, HELPER)
        self.assertIsNotNone(helper, "paStep3IsGlobalMap is missing from PA_Step3Views.js")
        rest = source.replace(helper, "")
        code = "\n".join(line for line in rest.splitlines()
                         if not line.lstrip().startswith(("*", "/*", "//")))
        left = [line.strip() for line in code.splitlines() if '"01100"' in line or "'01100'" in line]
        self.assertEqual([], left, "01100 compared outside paStep3IsGlobalMap")

    def test_table_network_and_counts_call_it(self):
        source = read_source()
        for name, call in (("table row", "paStep3IsGlobalMap(pathwayModel.getID(), this.getModel().getOrganism())"),
                           ("network node", "paStep3IsGlobalMap(elem.data.id, this.getModel().getOrganism())"),
                           ("network edge", "paStep3IsGlobalMap(elem.data.source, this.getModel().getOrganism())"),
                           ("network edge", "paStep3IsGlobalMap(elem.data.target, this.getModel().getOrganism())")):
            self.assertTrue(call in source, "%s: %s is not in PA_Step3Views.js" % (name, call))
        self.assertTrue("paStep3IsGlobalMap(" in (extract_block(source, COUNTS) or ""),
                        "getTotalVisiblePathways does not ask paStep3IsGlobalMap")

    def test_the_network_subtitle_counts_what_the_table_lists(self):
        source = read_source()
        self.assertTrue(re.search(r"updateNetworkSubtitle\(nodesAux\.length, edgesAux\.length,\s*"
                                  r"me\.getParent\(\)\.getTotalVisiblePathways\(me\.database\)\.listed",
                                  source),
                        "the network subtitle's total is not the listed count")


if __name__ == "__main__":
    unittest.main(verbosity=2)
