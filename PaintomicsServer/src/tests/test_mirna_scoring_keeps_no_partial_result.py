#!/usr/bin/env python3
"""A miRNA scoring step that stops part-way fails the job and leaves nothing behind.

What happened
-------------
miRNA2Target scored the miRNA-target pairs under a catch-all that kept the rows
already written, and MiRNA2GeneJob refused a run only when it had read no gene
at all. So a run that stopped after one pair was read as a smaller success. On
Tools > From miRNA to Genes, with pearson, three miRNAs with three conditions,
targets miR-1 to G1, miR-2 to #gene and miR-3 to G1, and a gene file whose header
is "#gene C1 C2 C3", the page said "miRNA2Genes finished successfully" and the
archive held one row, hsa-miR-1 with G1: nothing for the other two miRNAs, and no
word about them (staging copy of master, 2026-11-07, job g333HB62Wh).

What this file asserts
----------------------
1. a scoring step that dies after N rows leaves no output file, partial or old,
   and its account says where it stopped;
2. the job then fails, says it failed on our side, offers Report (the answer is
   not an input refusal), and nothing it wrote reaches the user's files;
3. the case above is refused as what it is: the header row of the gene file;
4. a header that names more (or fewer) conditions than the rows hold is refused
   under every method, with both counts; a header that ends in a tab is not;
5. counts of pairs read "1 ... pair was read" and "N ... pairs were read", and
   the count is the same under every method;
6. the refusal of targets that carry no identifier is typed as a refusal.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_mirna_scoring_keeps_no_partial_result
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances import MiRNA2GeneJob as MiRNA2GeneJob_module
from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob
from src.common.bioscripts import miRNA2Target
from src.servlets import MiRNA2GenesServlet

METHODS = ("pearson", "kendall", "spearman")
INPUTS = ["genes.tab", "relevant.txt", "targets.tab", "values.tab"]

THREE_MIRNAS = [["#miRNA", "C1", "C2", "C3"],
                ["hsa-miR-1", "0.1", "0.4", "0.2"],
                ["hsa-miR-2", "0.3", "0.1", "0.9"],
                ["hsa-miR-3", "0.5", "0.2", "0.4"]]
GENES = [["#gene", "C1", "C2", "C3"],
         ["G1", "1.0", "2.0", "0.5"],
         ["G2", "2.0", "1.0", "3.0"],
         ["G3", "0.4", "0.9", "0.1"]]


def write(path, rows):
    with io.open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("\t".join(row) + "\n")
    return path


class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


def runTool(rows, method="kendall"):
    """The real job and its real queue step on three files.

    Returns the answer and what the job left on disk. Storing the job is stubbed,
    so a run that finishes answers success here as it would on the server.
    """
    root = tempfile.mkdtemp(prefix="paintomics_mirna_partial_") + "/"
    try:
        job = MiRNA2GeneJob("partialjob", None, root)
        job.score_method = method
        job.initializeDirectories()
        os.makedirs(job.getInputDir(), exist_ok=True)
        files = {"values": "values.tab", "targets": "targets.tab", "genes": "genes.tab",
                 "relevant": "relevant.txt"}
        for key, content in dict({"relevant": []}, **rows).items():
            write(os.path.join(job.getInputDir(), files[key]), content)
        job.geneBasedInputOmics = [
            {"omicName": "miRNA-seq", "inputDataFile": files["values"],
             "relevantFeaturesFile": files["relevant"], "associationsFile": files["targets"]},
            {"omicName": "Gene expression", "inputDataFile": files["genes"]}]
        answer = _Response()
        with mock.patch.object(MiRNA2GenesServlet, "JobInformationManager"):
            MiRNA2GenesServlet.fromMiRNAtoGenes_STEP2(job, None, False, answer, {})
        output = job.getOutputDir()
        left = {"input": sorted(os.listdir(job.getInputDir())),
                "temporal": os.path.isdir(job.getTemporalDir()),
                "output": sorted(os.listdir(output)) if os.path.isdir(output) else []}
        return answer, left
    finally:
        shutil.rmtree(root, ignore_errors=True)


def dyingAfter(rowsWritten, real=miRNA2Target.getScore):
    """getScore that scores rowsWritten pairs and then raises."""
    calls = {"n": 0}

    def score(values_1, values_2, method):
        calls["n"] += 1
        if calls["n"] > rowsWritten:
            raise RuntimeError("scoring died")
        return real(values_1, values_2, method)
    return score


class _Case(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="paintomics-mirna-partial-")
        self.out = os.path.join(self.dir, "out.tab")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def files(self, values=THREE_MIRNAS, targets=None, genes=GENES):
        targets = targets or [["hsa-miR-1", "G1"], ["hsa-miR-1", "G2"],
                              ["hsa-miR-2", "G2"], ["hsa-miR-2", "G3"],
                              ["hsa-miR-3", "G1"], ["hsa-miR-3", "G3"]]
        return (write(os.path.join(self.dir, "t.tab"), targets),
                write(os.path.join(self.dir, "v.tab"), values),
                write(os.path.join(self.dir, "g.tab"), genes))

    def assertRefused(self, answer, left, *sentences):
        self.assertEqual(answer.status, 400)
        self.assertIs(answer.content["success"], False)
        self.assertIs(answer.content["extra"]["input_error"], True, answer.content["message"])
        for sentence in sentences:
            self.assertIn(sentence, answer.content["message"])
        self.assertNothingLeft(left)

    def assertNothingLeft(self, left):
        self.assertEqual(left["input"], INPUTS, "an output reached the user's files")
        self.assertFalse(left["temporal"])
        self.assertEqual(left["output"], [])


class AStoppedRunLeavesNoOutputTest(_Case):

    def test_a_scoring_step_that_dies_after_n_rows_leaves_no_file(self):
        targets, values, genes = self.files()
        with mock.patch.object(miRNA2Target, "getScore", dyingAfter(3)):
            stats = miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertFalse(os.path.exists(self.out), "the rows written before the failure were kept")
        self.assertFalse(os.path.exists(self.out + ".partial"))
        self.assertEqual(stats["aborted"], "scoring died")
        self.assertEqual(stats["abortedAfterPairs"], 3)
        self.assertEqual(stats["abortedOn"], ["hsa-miR-2", "G3"])
        self.assertEqual(stats["pairs"], 6)

    def test_an_older_output_under_the_same_name_goes_too(self):
        write(self.out, [["# an earlier run"], ["hsa-miR-9", "G9", "0.5", "kendall"]])
        targets, values, genes = self.files()
        with mock.patch.object(miRNA2Target, "getScore", dyingAfter(1)):
            miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertFalse(os.path.exists(self.out))

    def test_the_output_name_appears_only_after_the_last_pair(self):
        """So a worker killed mid-run leaves nothing under the name the job reads."""
        seen = []

        def score(values_1, values_2, method, real=miRNA2Target.getScore):
            seen.append(os.path.exists(self.out))
            return real(values_1, values_2, method)
        targets, values, genes = self.files()
        with mock.patch.object(miRNA2Target, "getScore", score):
            miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertEqual(seen, [False] * 6)
        self.assertTrue(os.path.exists(self.out))

    def test_a_run_that_finishes_writes_every_pair_and_no_partial_file(self):
        targets, values, genes = self.files()
        stats = miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertNotIn("aborted", stats)
        self.assertFalse(os.path.exists(self.out + ".partial"))
        with io.open(self.out, encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 1 + 6)

    def test_a_header_that_does_not_fit_writes_nothing(self):
        values = [["#miRNA", "C1", "C2", "C3", "C4"]] + [row[:4] for row in THREE_MIRNAS[1:]]
        targets, values, genes = self.files(values=values)
        stats = miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertEqual(stats["headerMismatch"], {"cells": 5, "values": 3})
        self.assertEqual(stats["scored"], 0)
        self.assertFalse(os.path.exists(self.out))


class TheJobFailsAndSaysWhoseFaultItIsTest(_Case):

    def test_a_scoring_step_that_dies_after_a_row_is_a_failure_of_ours(self):
        """Not a success with a third of the result, and not a refusal of the files."""
        rows = {"values": THREE_MIRNAS, "genes": GENES,
                "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "G2"], ["hsa-miR-3", "G1"]]}
        for method in METHODS:
            with self.subTest(method=method):
                with mock.patch.object(miRNA2Target, "getScore", dyingAfter(1)):
                    answer, left = runTool(rows, method)
                self.assertEqual(answer.status, 400)
                self.assertIs(answer.content["success"], False)
                self.assertIs(answer.content["extra"]["input_error"], False)
                message = answer.content["message"]
                self.assertIn("The scoring step failed on our side at miRNA-target pair 2 of 3 "
                              "(hsa-miR-2 with G2), with the error: scoring died.", message)
                self.assertIn("no partial result was saved", message)
                self.assertNotIn("No miRNA was matched", message)
                self.assertNothingLeft(left)

    def test_the_run_that_was_reported_as_a_success(self):
        """The case from the top of this file, under every method."""
        rows = {"values": THREE_MIRNAS, "genes": GENES,
                "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "#gene"], ["hsa-miR-3", "G1"]]}
        for method in METHODS:
            with self.subTest(method=method):
                answer, left = runTool(rows, method)
                self.assertRefused(
                    answer, left,
                    'pairs hsa-miR-2 with a target called "#gene", which is also the first cell '
                    'of the header row of your gene expression file',
                    'its cell "C1" is not a number',
                    "no result was saved")
                self.assertNotIn("not a problem with your identifiers", answer.content["message"])

    def test_the_same_files_without_that_pair_still_succeed(self):
        rows = {"values": THREE_MIRNAS, "genes": GENES,
                "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "G2"], ["hsa-miR-3", "G1"]]}
        answer, left = runTool(rows, "pearson")
        self.assertIs(answer.content["success"], True, answer.content)
        self.assertEqual(len([name for name in left["input"] if name not in INPUTS]), 4)


class AHeaderThatDoesNotFitItsRowsTest(_Case):

    def test_more_names_than_values_is_refused_under_every_method(self):
        """It was a crash with Report under pearson and "values are identical" under
        kendall and spearman, for the same file."""
        rows = {"values": [["#miRNA", "C1", "C2"], ["hsa-miR-1", "0.1"], ["hsa-miR-2", "0.2"]],
                "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "G2"]],
                "genes": [["#gene", "C1", "C2"], ["G1", "1.0"], ["G2", "2.0"]]}
        for method in METHODS:
            with self.subTest(method=method):
                answer, left = runTool(rows, method)
                self.assertRefused(
                    answer, left,
                    "The header row of your miRNA quantification file names 2 conditions, but "
                    "its rows have 1 value after the miRNA id.",
                    "so it should have 1 or 2 cells; it has 3.")

    def test_the_counts_are_the_files(self):
        values = [["#miRNA", "C1", "C2", "C3", "C4", "C5", "C6"],
                  ["hsa-miR-1", "0.1", "0.4", "0.2", "0.7"],
                  ["hsa-miR-2", "0.3", "0.1", "0.9", "0.2"]]
        genes = [["#gene", "C1", "C2", "C3", "C4"], ["G1", "1.0", "2.0", "0.5", "0.1"]]
        for method in METHODS:
            with self.subTest(method=method):
                answer, left = runTool({"values": values, "genes": genes,
                                        "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "G1"]]}, method)
                self.assertRefused(answer, left, "names 6 conditions, but its rows have 4 values",
                                   "should have 4 or 5 cells; it has 7.")

    def test_fewer_names_than_values_is_refused_too(self):
        """"C1 C2" over four values: the second name was taken for the first column."""
        values = [["C1", "C2"], ["hsa-miR-1", "0.1", "0.4", "0.2", "0.7"]]
        genes = [["#gene", "C1", "C2", "C3", "C4"], ["G1", "1.0", "2.0", "0.5", "0.1"]]
        answer, left = runTool({"values": values, "genes": genes, "targets": [["hsa-miR-1", "G1"]]})
        self.assertRefused(answer, left, "The header row of your miRNA quantification file has only "
                                         "2 cells, but its rows have 4 values after the miRNA id.")

    def test_a_header_that_ends_in_a_tab_names_every_condition(self):
        for header, label in ((["#miRNA", "C1", "C2", "C3", ""], "an id label"),
                              (["C1", "C2", "C3", ""], "no id label")):
            with self.subTest(label):
                targets, values, genes = self.files(values=[header] + THREE_MIRNAS[1:])
                stats = miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
                self.assertNotIn("headerMismatch", stats)
                with io.open(self.out, encoding="utf-8") as handle:
                    self.assertEqual(handle.readline(),
                                     "# miRNA_id\ttarget_id\tscore\tscore method\tC1\tC2\tC3\n")

    def test_an_empty_line_after_the_header_is_not_a_row(self):
        targets, values, genes = self.files(values=THREE_MIRNAS[:1] + [[]] + THREE_MIRNAS[1:])
        stats = miRNA2Target.run(targets, None, values, genes, self.out, "kendall")
        self.assertNotIn("headerMismatch", stats)
        self.assertEqual(stats["scored"], 6)


class PairsAreCountedInWordsTest(_Case):

    ONE_CONDITION = {"values": [["#miRNA", "C1"], ["hsa-miR-1", "0.1"], ["hsa-miR-2", "0.2"]],
                     "targets": [["hsa-miR-1", "G1"], ["hsa-miR-2", "G2"]],
                     "genes": [["#gene", "C1"], ["G1", "1.0"], ["G2", "2.0"]]}

    def test_every_method_reads_the_same_count(self):
        """pearson raises on the first pair and its count stopped there: "1 miRNA-target
        pairs were read" beside kendall's 2, for the same file."""
        for method in METHODS:
            with self.subTest(method=method):
                answer, left = runTool(self.ONE_CONDITION, method)
                self.assertRefused(answer, left, "Your data has 1 condition column",
                                   "2 miRNA-target pairs were read.")

    def test_one_pair_is_one_pair(self):
        rows = dict(self.ONE_CONDITION, targets=[["hsa-miR-1", "G1"]])
        for method in METHODS:
            with self.subTest(method=method):
                answer, left = runTool(rows, method)
                self.assertRefused(answer, left, "1 miRNA-target pair was read.")
                self.assertNotIn("1 miRNA-target pairs", answer.content["message"])

    def test_the_other_counts_of_pairs(self):
        explain = MiRNA2GeneJob_module.explainEmptyResult
        self.assertIn("1 miRNA-target pair was read, but not one target gene was found",
                      explain({"regulators": 1, "pairs": 1, "scored": 0, "unmatchedTargets": 1}))
        self.assertIn("The one scored miRNA-target pair gave an undefined correlation (nan): its values",
                      explain({"regulators": 1, "pairs": 1, "scored": 1, "nanScores": 1}))
        self.assertIn("All 2 scored miRNA-target pairs gave", explain({"regulators": 1, "pairs": 2,
                                                                       "scored": 2, "nanScores": 2}))
        self.assertIn("1 miRNA-target pair was read and 0 were scored",
                      explain({"regulators": 1, "pairs": 1, "scored": 0}))
        self.assertIn("after 0 of 1 miRNA-target pair,", explain({"regulators": 1, "pairs": 1,
                                                                  "aborted": "x", "abortedAfterPairs": 0}))


class TargetsWithNoIdentifierAreARefusalTest(_Case):

    def test_it_is_typed_as_a_refusal(self):
        """miRNA2Target drops blank targets, so this guard is reached only by an output
        that holds them; it was a plain Exception and read as an internal error."""
        def blankTargets(reference, relevant, data, genes, tmpFile, method):
            write(tmpFile, [["# miRNA_id", "target_id", "score", "score method", "C1", "C2", "C3"],
                            ["hsa-miR-1", "", "-0.9", "kendall", "0.1", "0.4", "0.2"]])
            return {"pairs": 1, "scored": 1, "nanScores": 0, "dropped": {}}
        rows = {"values": THREE_MIRNAS, "genes": GENES, "targets": [["hsa-miR-1", "G1"]]}
        with mock.patch.object(MiRNA2GeneJob_module, "run_miRNA2Target", blankTargets):
            answer, left = runTool(rows)
        self.assertRefused(answer, left, "ERROR MESSAGE: No target gene carried an identifier "
                                         "(1 target gene read)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
