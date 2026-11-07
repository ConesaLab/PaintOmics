#!/usr/bin/env python3
"""A job whose only data is metabolomics is refused at Step 2 when nothing is selected.

Why this exists
---------------
PR #218 made a job with only metabolites, none of whose names matched a KEGG
compound, say so when it is reopened from its link at Step 2: "None of the
metabolite names ... matched a KEGG compound, so there is nothing to analyse."
The first run of the same job said nothing. Step 2 found no gene omic for
explainEmptyMapping to explain and ran on nothing; a real run of 20 unmatched
names reached Step 3 with 0 pathways and no word about why (checked in headless
Chromium for #218).

PathwayAcquisitionJob.explainEmptyMapping now answers for a job with no gene omic
too. No found compound means no name matched, and it gives the sentence the link
gives. Found compounds and an empty selection means the user unticked every one
(Step 1 ticks the best match of each name), and it says that. The servlet raises
either as InputFileError, so the page reads "Please check your input files" with
no Report button, as for gene identifiers that match nothing.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_metabolites_only_job_is_refused_on_its_first_run
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
from src.paintomicsserver import Response
from src.servlets import PathwayAcquisitionServlet as servlet

METABOLOMICS = {"omicName": "Metabolomics", "omicSummary": [0, 20]}
GENES = {"omicName": "Gene expression", "omicSummary": [{"KEGG": 150, "Total": 150}, 10]}
NO_NAME_MATCHED = "None of the metabolite names in your data matched a KEGG compound, so there is nothing to analyse."
NONE_SELECTED = "No compound is selected, and the job has no gene data, so there is nothing to analyse."


class _Case(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="paintomics_metabolites_") + "/"
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = PathwayAcquisitionJob(jobID="metonly", userID=None, CLIENT_TMP_DIR=self.root)
        self.job.setOrganism("hsa")
        self.job.geneBasedInputOmics = []
        self.job.compoundBasedInputOmics = [dict(METABOLOMICS)]
        self.job.foundCompounds = []


class WhatTheJobSaysTest(_Case):

    def test_no_name_matched(self):
        self.assertIn(NO_NAME_MATCHED, self.job.explainEmptyMapping([]))
        self.assertIn(NO_NAME_MATCHED, self.job.explainEmptyMapping(None))

    def test_the_same_words_as_the_link(self):
        """pathwayAcquisitionRecoverJob, reopening the same job at Step 2."""
        self.assertIn("matched a KEGG compound, so there is nothing to analyse", self.job.explainEmptyMapping([]))

    def test_names_matched_and_none_is_selected(self):
        self.job.foundCompounds = [mock.MagicMock()]
        self.assertIn(NONE_SELECTED, self.job.explainEmptyMapping([]))

    def test_a_selection_goes_on(self):
        self.job.foundCompounds = [mock.MagicMock()]
        self.assertIsNone(self.job.explainEmptyMapping(["C00041#L-Alanine#alanine"]))

    def test_a_job_with_gene_data_is_unchanged(self):
        """Its gene analysis can run: matched genes and no compound go on, as before."""
        self.job.geneBasedInputOmics = [dict(GENES)]
        self.assertIsNone(self.job.explainEmptyMapping([]))

    def test_no_omic_at_all_says_nothing(self):
        self.job.compoundBasedInputOmics = []
        self.assertIsNone(self.job.explainEmptyMapping([]))


class Step2RefusesItTest(_Case):

    def runStep2(self, selected):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = self.job
        # The job's directories stay in this test's own folder.
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"), \
                mock.patch.object(self.job, "setDirectories"), \
                mock.patch.object(self.job, "initializeDirectories"), \
                mock.patch.object(self.job, "cleanDirectories"), \
                mock.patch.object(self.job, "generatePathwaysList") as pathways:
            response = servlet.pathwayAcquisitionStep2_PART2("metonly", "17", selected, {}, Response(), "/nowhere/", {})
        return response, pathways

    def test_the_first_run_is_refused_as_a_problem_with_the_input(self):
        response, pathways = self.runStep2([])
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["extra"]["input_error"], True, "it would read as an internal error")
        self.assertIn(NO_NAME_MATCHED, response.content["message"])
        pathways.assert_not_called()

    def test_an_emptied_selection_is_refused_with_its_own_words(self):
        self.job.foundCompounds = [mock.MagicMock()]
        response, pathways = self.runStep2([])
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["extra"]["input_error"], True)
        self.assertIn(NONE_SELECTED, response.content["message"])
        pathways.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
