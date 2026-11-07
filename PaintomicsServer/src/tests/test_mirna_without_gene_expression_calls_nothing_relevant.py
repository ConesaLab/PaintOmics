#!/usr/bin/env python3
"""From miRNA to Genes with nothing to correlate calls no pair relevant by chance.

What happened
-------------
With neither a gene expression file nor a relevant associations file, miRNA2Target
switched to the method "fc", and getScore("fc") returned random.uniform(-1, 1) under
a comment saying "CALCULATE THE FOLD CHANGE". MiRNA2GeneJob marked a pair relevant
when that number passed the cutoff, so which pairs were called relevant was chance,
and changed on every run. The form cannot reach this (it asks for one of the two
files), but a request to /dm_fromMiRNAtoGenes can, and so can score_method=fc beside
a gene expression file.

What this file asserts
----------------------
1. two runs on the same input give identical output, file by file;
2. every pair read is in the results, with nan as its score, and none is relevant,
   under every selection method and the most permissive cutoff;
3. the answer says no score could be computed, and the job's description and the
   results' Selection column do not name a correlation that never ran;
4. score_method=fc beside a gene expression file does the same and says why;
5. a relevant associations file, and a real correlation, work as they did.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_mirna_without_gene_expression_calls_nothing_relevant
"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob
from src.servlets import MiRNA2GenesServlet

VALUES = [["#miRNA", "C1", "C2", "C3"],
          ["hsa-miR-1", "0.1", "0.4", "0.2"],
          ["hsa-miR-2", "0.3", "0.1", "0.9"]]
TARGETS = [["hsa-miR-1", "G1"], ["hsa-miR-1", "G2"], ["hsa-miR-2", "G2"], ["hsa-miR-2", "G3"]]
GENES = [["#gene", "C1", "C2", "C3"],
         ["G1", "1.0", "2.0", "0.5"],
         ["G2", "2.0", "1.0", "3.0"],
         ["G3", "0.4", "0.9", "0.1"]]
SELECTIONS = ("negative_correlation", "positive_correlation", "abs_correlation", "fc")


class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


def write(path, rows):
    with io.open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("\t".join(row) + "\n")


def runTool(genes=None, relevantAssociations=None, targets=TARGETS, **options):
    """The real job and its real queue step. Storing the job is stubbed.

    Returns the answer, the job's description, and the results archive as
    {member name without its date and random part: content}.
    """
    root = tempfile.mkdtemp(prefix="paintomics_mirna_noscore_") + "/"
    try:
        job = MiRNA2GeneJob("noscorejob", None, root)
        for name, value in options.items():
            setattr(job, name, value)
        job.initializeDirectories()
        os.makedirs(job.getInputDir(), exist_ok=True)
        write(os.path.join(job.getInputDir(), "values.tab"), VALUES)
        write(os.path.join(job.getInputDir(), "targets.tab"), targets)
        write(os.path.join(job.getInputDir(), "relevant.txt"), [])
        miRNA = {"omicName": "miRNA-seq", "inputDataFile": "values.tab",
                 "relevantFeaturesFile": "relevant.txt", "associationsFile": "targets.tab"}
        job.geneBasedInputOmics = [miRNA]
        if genes is not None:
            write(os.path.join(job.getInputDir(), "genes.tab"), genes)
            job.geneBasedInputOmics.append({"omicName": "Gene expression", "inputDataFile": "genes.tab"})
        if relevantAssociations is not None:
            write(os.path.join(job.getInputDir(), "chosen.tab"), relevantAssociations)
            miRNA["relevantAssociationsFile"] = "chosen.tab"
        answer = _Response()
        with mock.patch.object(MiRNA2GenesServlet, "JobInformationManager"):
            MiRNA2GenesServlet.fromMiRNAtoGenes_STEP2(job, None, False, answer, {})
        return answer, job, readArchive(job, answer)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def readArchive(job, answer):
    if not answer.content.get("success"):
        return {}
    members = {}
    with zipfile.ZipFile(os.path.join(job.getOutputDir(), answer.content["compressedFileName"])) as archive:
        for name in archive.namelist():
            # <job>_regulator2Gene_output_<date>_<random>.tab: only the content is compared.
            short = re.sub(r"[_0-9]+\.tab$", ".tab", name.replace("noscorejob_", ""))
            members[short] = archive.read(name).decode("utf-8")
    return members


def rows(text):
    return [line.split("\t") for line in text.splitlines() if not line.startswith("#")]


class WithNoGeneExpressionTest(unittest.TestCase):

    def test_two_runs_on_the_same_input_give_identical_output(self):
        """It failed here on master every time: the Score column held new random numbers."""
        first, _job, firstFiles = runTool()
        second, _job, secondFiles = runTool()
        self.assertIs(first.content["success"], True, first.content)
        self.assertEqual(sorted(firstFiles), sorted(secondFiles))
        self.assertEqual(firstFiles, secondFiles)
        self.assertEqual(first.content["notice"], second.content["notice"])

    def test_every_pair_is_in_the_results_and_none_is_relevant(self):
        for selection in SELECTIONS:
            for cutoff in (-1, -0.5, 0.5):
                with self.subTest(selection=selection, cutoff=cutoff):
                    answer, _job, files = runTool(selection_method=selection, cutoff=cutoff)
                    self.assertIs(answer.content["success"], True, answer.content)
                    self.assertEqual(files["regulator_relevant_associations.tab"], "")
                    self.assertEqual(len(rows(files["regulator2Gene_output.tab"])), len(TARGETS))
                    self.assertEqual(len(files["regulator_associations.tab"].splitlines()), len(TARGETS))

    def test_report_de_calls_nothing_relevant_either(self):
        answer, _job, files = runTool(report="DE")
        self.assertIs(answer.content["success"], True, answer.content)
        self.assertEqual(files["regulator_relevant_associations.tab"], "")

    def test_the_answer_says_no_score_could_be_computed(self):
        answer, _job, _files = runTool()
        self.assertEqual(answer.content["notice"],
                         "No score could be computed without gene expression: 4 miRNA-target pairs "
                         "were read and none was marked as relevant. Every pair read is in the results, "
                         "with nan as its score. To select pairs, use a gene expression file or a "
                         "relevant associations file.")

    def test_one_pair_is_one_pair(self):
        answer, _job, _files = runTool(targets=[["hsa-miR-1", "G1"]])
        self.assertIn("1 miRNA-target pair was read and none", answer.content["notice"])

    def test_the_results_name_no_correlation_that_never_ran(self):
        answer, job, files = runTool()
        self.assertEqual(files["genesToMiRNAFile.tab"].splitlines()[0], "# " + answer.content["notice"])
        self.assertEqual(len(rows(files["genesToMiRNAFile.tab"])), len(TARGETS))
        for row in rows(files["genesToMiRNAFile.tab"]):
            self.assertEqual(row[3:], ["nan", "none"])
        self.assertIn("Score method=none;Selection method=none;", answer.content["description"])
        self.assertEqual((job.score_method, job.selection_method), ("none", "none"))
        for row in rows(files["miRNAMatch_output.txt"]):
            self.assertEqual(row[2:4], ["nan", "none"])

    def test_the_miRNA_values_are_carried_onto_the_genes(self):
        _answer, _job, files = runTool()
        self.assertIn(["G1:::HSA-MIR-1", "0.1", "0.4", "0.2"], rows(files["regulator2Gene_output.tab"]))

    def test_files_that_do_not_join_are_still_refused(self):
        answer, _job, _files = runTool(targets=[["other-miR", "G1"]])
        self.assertIs(answer.content["extra"]["input_error"], True)
        self.assertIn("none of them appears in the first column", answer.content["message"])


class ScoreMethodFcBesideGeneExpressionTest(unittest.TestCase):
    """The form stopped offering "fc", and getScore gave it random numbers all the same."""

    def test_two_runs_are_identical_and_nothing_is_relevant(self):
        first, _job, firstFiles = runTool(genes=GENES, score_method="fc")
        second, _job, secondFiles = runTool(genes=GENES, score_method="fc")
        self.assertIs(first.content["success"], True, first.content)
        self.assertEqual(firstFiles, secondFiles)
        self.assertEqual(firstFiles["regulator_relevant_associations.tab"], "")

    def test_it_says_why(self):
        answer, _job, _files = runTool(genes=GENES, score_method="fc")
        self.assertTrue(answer.content["notice"].startswith(
            'The score method "fc" (fold change) computes no score: 4 miRNA-target pairs were read'))
        self.assertIn("use a correlation score method or a relevant associations file.", answer.content["notice"])


class WhatStillWorksTest(unittest.TestCase):

    def test_a_relevant_associations_file_decides_with_no_gene_expression(self):
        answer, _job, files = runTool(relevantAssociations=[["# Gene name", "miRNA ID"], ["G1", "hsa-miR-1"],
                                                            ["G3", "hsa-miR-2"]])
        self.assertIs(answer.content["success"], True, answer.content)
        self.assertIsNone(answer.content["notice"])
        self.assertEqual(sorted(files["regulator_relevant_associations.tab"].splitlines()),
                         ["G1\tHSA-MIR-1", "G3\tHSA-MIR-2"])
        self.assertIn("Score method=kendall;", answer.content["description"])

    def test_a_correlation_scores_and_selects(self):
        answer, _job, files = runTool(genes=GENES, selection_method="negative_correlation", cutoff=-0.5)
        self.assertIs(answer.content["success"], True, answer.content)
        self.assertIsNone(answer.content["notice"])
        # Kendall on three conditions: miR-2 with G3 is -1, the others are +1 and +-1/3.
        self.assertEqual(files["regulator_relevant_associations.tab"], "G3\tHSA-MIR-2\n")
        self.assertIn("Score method=kendall;Selection method=negative_correlation;",
                      answer.content["description"])

    def test_a_score_keeps_its_sign_in_the_results(self):
        """The job inverts a negative selection's score to compare it and back to write
        it. The branch that skipped this for "fc" went with "fc"."""
        _answer, _job, files = runTool(genes=GENES, selection_method="negative_correlation", cutoff=-0.5)
        written = {(row[0], row[1]): float(row[3]) for row in rows(files["genesToMiRNAFile.tab"])}
        self.assertEqual(written[("G3", "HSA-MIR-2")], -1.0)
        self.assertEqual(written[("G2", "HSA-MIR-2")], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
