#!/usr/bin/env python3
"""Regression test for the Step 1 "Multiple databases used" matching table.

The table printed "Metabolomics undefined (NaN%)" for every database whenever
more than one database was selected.

omicSummary[0] -- the "mapped" slot of an omic summary -- reaches the client in
two different shapes:

  * gene based omics get a dict of feature-table name -> matched count, with a
    "Total" entry holding the de-duplicated count across databases;
  * compound based omics get a plain integer, because compounds are matched
    once against KEGG compound IDs and that one set backs every database
    (FeatureNamesToKeggIDsMapper.mapFeatureNamesToCompoundsIDs returns a count,
    and Job.parseCompoundBasedFile passes it straight through).

PA_Step2Views.js only ever handled the dict shape. Object.keys(51) is [], so the
feature-table lookup returned undefined, "matched" rendered as undefined and
undefined/total*100 rendered as NaN.

The test executes the real matchedFeaturesByDatabase() out of the shipped
JavaScript rather than a Python restatement of it, so it tracks the file that
actually ships.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_matching_summary_shapes
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

STEP2_VIEWS = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../PaintomicsClient/public_html/app/view/PathwayAcquisitionViews/PA_Step2Views.js"))

FUNCTION_NAME = "matchedFeaturesByDatabase"


def extract_function(source, name):
    """Return the source of a top-level `function name(...) {...}` declaration.

    Brace matching is done on the raw text. That is adequate here because the
    extracted helper contains no braces inside strings, comments or regexes;
    the test asserts the extracted text parses, which catches it if that ever
    stops being true.
    """
    match = re.search(r"^function\s+%s\s*\(" % re.escape(name), source, re.MULTILINE)
    if match is None:
        raise AssertionError(
            "%s() is missing from %s -- the matching-summary table is back to "
            "indexing the raw omicSummary and will print undefined (NaN%%) for "
            "compound based omics." % (name, STEP2_VIEWS))

    start = match.start()
    opening = source.index("{", match.end())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces in %s()" % name)


def read_source():
    with open(STEP2_VIEWS, "r", encoding="utf-8") as handle:
        return handle.read()


def run_in_node(function_source, calls):
    """Evaluate the helper against each call and return the parsed results."""
    script = (
        function_source
        + "\nconst calls = " + json.dumps(calls) + ";\n"
        + "const out = calls.map(c => %s(c.mapped, c.databases));\n" % FUNCTION_NAME
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    directory = tempfile.mkdtemp(prefix="paintomics-js-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        completed = subprocess.run(
            ["node", path], capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError(
                "node failed to run %s():\n%s" % (FUNCTION_NAME, completed.stderr))
        return json.loads(completed.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class SourceStructureTest(unittest.TestCase):
    """Checks that hold with or without a JavaScript runtime available."""

    def setUp(self):
        self.source = read_source()

    def test_helper_is_present(self):
        # Raises with an explanatory message if the helper was removed.
        extract_function(self.source, FUNCTION_NAME)

    def test_table_does_not_index_the_raw_summary(self):
        """The original defect, written as a shape check on the call site.

        dataDistribution[omicName][0][featureTable] is exactly the expression
        that yields undefined for an integer summary.
        """
        offending = re.search(
            r"dataDistribution\[omicName\]\[0\]\[", self.source)
        self.assertIsNone(
            offending,
            "the matching table indexes omicSummary[0] directly again; that "
            "returns undefined for compound based omics, whose summary is an "
            "integer rather than a per-database dict")

    def test_percentage_is_guarded_against_an_empty_omic(self):
        self.assertIn(
            "totalFeatures > 0", self.source,
            "an omic with no features would divide by zero and render NaN%")


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class HelperBehaviourTest(unittest.TestCase):
    """Runs the shipped helper for both summary shapes."""

    @classmethod
    def setUpClass(cls):
        cls.function_source = extract_function(read_source(), FUNCTION_NAME)

    def evaluate(self, mapped, databases):
        return run_in_node(self.function_source,
                           [{"mapped": mapped, "databases": databases}])[0]

    def test_compound_omic_integer_summary_counts_for_every_database(self):
        """The shape that produced undefined (NaN%).

        51 of the example's 58 metabolites match, so both databases must report
        51 -- never undefined.
        """
        result = self.evaluate(51, ["KEGG", "Reactome"])

        self.assertEqual(result["perDatabase"], {"KEGG": 51, "Reactome": 51})
        self.assertEqual(result["totalMapped"], 51)

    def test_compound_percentage_is_a_number(self):
        """Guards the rendered cell, not just the count."""
        result = self.evaluate(51, ["KEGG", "Reactome"])
        unmapped = 7

        for dbname, matched in result["perDatabase"].items():
            with self.subTest(database=dbname):
                total = unmapped + result["totalMapped"]
                percentage = int(matched * 100 / total + 0.5)  # Math.round
                self.assertEqual(matched, 51)
                self.assertEqual(percentage, 88)

    def test_gene_omic_dict_summary_still_splits_per_database(self):
        """The shape that already worked must keep working."""
        # FeatureNamesToKeggIDsMapper builds this dict as
        # {db: set() for db in databases + ["Total"]}, so the keys are the
        # database names themselves.
        result = self.evaluate(
            {"KEGG": 5620, "Reactome": 2827, "Total": 6103},
            ["KEGG", "Reactome"])

        self.assertEqual(result["perDatabase"],
                         {"KEGG": 5620, "Reactome": 2827})
        self.assertEqual(result["totalMapped"], 6103,
                         "the unique count across databases, not a per-database one")

    def test_single_database_summary_has_no_total_entry(self):
        """With one database the mapper drops the redundant "Total" entry."""
        result = self.evaluate({"KEGG": 5620}, ["KEGG"])

        self.assertEqual(result["perDatabase"], {"KEGG": 5620})
        self.assertEqual(result["totalMapped"], 5620)

    def test_database_with_no_feature_table_reports_zero_not_undefined(self):
        """MapMan selected for a species whose omic has no MapMan table."""
        result = self.evaluate(
            {"KEGG": 5620, "Total": 5620}, ["KEGG", "MapMan"])

        self.assertEqual(result["perDatabase"]["MapMan"], 0)

    def test_empty_summary_does_not_produce_undefined(self):
        result = self.evaluate({}, ["KEGG", "Reactome"])

        self.assertEqual(result["perDatabase"], {"KEGG": 0, "Reactome": 0})
        self.assertEqual(result["totalMapped"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
