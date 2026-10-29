#!/usr/bin/env python3
"""The Step 1 form's placeholder must never reach the AI as the user's design.

Ext JS 4's form submit sends an empty field's emptyText as its value
(`submitEmptyText` defaults to true), and Ext draws emptyText as a placeholder
attribute, so nothing on screen shows it. On paintomics.org on 2026-10-28, 32
of 64 jobs stored the Experiment design placeholder

    e.g. RNA-seq of wildtype vs knockout mouse liver, 3 replicates per group, sampled at 24 h

as `experimentDesign`, on zebrafish, E. coli, potato, S. aureus, human and
Leishmania data among others. The walk's design card read it as the design
(perturbation "knockout of gene in mouse liver", system "mouse liver"), and
the walks wrote "knockout mouse liver" into the title, summary and statements
of zebrafish job JK5047lsnI and the rest.

Pinned here: the shared guard (src.common.FormPlaceholders); a new job stores
"" for the placeholder; a job already stored with it reads as having no design,
in the card and in every other prompt context; and every Ext form submit in the
client passes submitEmptyText: false, so a new one cannot bring the leak back.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_form_placeholder_is_not_a_design
"""
import glob
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from werkzeug.datastructures import MultiDict                               # noqa: E402

from src.common.FormPlaceholders import (EXPERIMENT_DESIGN_PLACEHOLDERS,     # noqa: E402
                                         designWithoutPlaceholder, isDesignPlaceholder)
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob  # noqa: E402
from src.classes.AIInterpret.walker import card as card_mod                 # noqa: E402
from src.classes.AIInterpret.walker import service                          # noqa: E402
from src.servlets import PathwayAcquisitionServlet as servlet               # noqa: E402
from src.tests import walker_fixture as fx                                  # noqa: E402

CURRENT = "e.g. RNA-seq of wildtype vs knockout mouse liver, 3 replicates per group, sampled at 24 h"
OLDER = 'e.g., "RNA-seq wildtype vs knockout mouse liver, n=3 per group"'
REAL = ("Mouse liver from Pten knockout and wildtype littermates, 3 replicates per group. "
        "Values are log2 fold changes against wildtype.")
CLIENT_APP = os.path.join(REPO, "PaintomicsClient", "public_html", "app")
STEP1_VIEW = os.path.join(CLIENT_APP, "view", "PathwayAcquisitionViews", "PA_Step1Views.js")


class PlaceholderGuardTest(unittest.TestCase):

    def test_both_placeholders_the_field_has_shipped_with_are_listed(self):
        self.assertEqual(set(EXPERIMENT_DESIGN_PLACEHOLDERS), {CURRENT, OLDER})

    def test_the_list_holds_the_placeholder_the_form_ships_today(self):
        """Change the field's emptyText and this fails until the new text is
        added -- the old one stays, for the jobs already stored with it."""
        with io.open(STEP1_VIEW, encoding="utf-8") as handle:
            source = handle.read()
        shipped = re.search(r"name: 'experimentDesign',.*?emptyText: '([^']*)'", source, re.S)
        self.assertIsNotNone(shipped, "the Experiment design field or its emptyText moved")
        self.assertIn(shipped.group(1), EXPERIMENT_DESIGN_PLACEHOLDERS)

    def test_a_placeholder_reads_as_no_design(self):
        for text in (CURRENT, OLDER):
            self.assertTrue(isDesignPlaceholder(text), text)
            self.assertEqual(designWithoutPlaceholder(text), "", text)

    def test_whitespace_case_and_quote_variants_are_the_placeholder_too(self):
        variants = ("  " + CURRENT + "\n",
                    CURRENT.upper(),
                    CURRENT.replace(" ", "  ").replace(", ", ",\n"),
                    OLDER.replace('"', "“", 1).replace('"', "”"),
                    "\t" + OLDER.lower() + "  ")
        for text in variants:
            self.assertEqual(designWithoutPlaceholder(text), "", repr(text))

    def test_a_real_design_that_mentions_mouse_liver_is_kept(self):
        for text in (REAL, "RNA-seq of wildtype vs knockout mouse liver",
                     CURRENT + " Values are log2 fold changes.", CURRENT[:-1]):
            self.assertFalse(isDesignPlaceholder(text), text)
            self.assertEqual(designWithoutPlaceholder(text), text)

    def test_nothing_is_nothing(self):
        for text in (None, "", 0):
            self.assertEqual(designWithoutPlaceholder(text), "")
            self.assertFalse(isDesignPlaceholder(text))


class _TmpJobTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="paintomics_placeholder_") + "/"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def job(self):
        return PathwayAcquisitionJob(jobID="placeholder", userID=None, CLIENT_TMP_DIR=self.tmp)


class JobAccessorTest(_TmpJobTestCase):

    def test_the_setter_stores_nothing_for_the_placeholder(self):
        job = self.job()
        job.setExperimentDesign(CURRENT)
        self.assertEqual(job.experimentDesign, "")
        job.setExperimentDesign(REAL)
        self.assertEqual(job.getExperimentDesign(), REAL)

    def test_a_job_stored_with_the_placeholder_reads_as_having_no_design(self):
        """parseBSON restores the field with setattr, past the setter: the
        shape of the 32 jobs already on paintomics.org."""
        for stored in (CURRENT, OLDER):
            job = self.job()
            job.parseBSON({"_id": "x", "experimentDesign": stored})
            self.assertEqual(job.experimentDesign, stored)
            self.assertEqual(job.getExperimentDesign(), "")
            self.assertEqual(card_mod.design_text_of(job), "")

    def test_compound_suggestion_is_not_told_the_placeholder(self):
        from src.servlets import CompoundSuggestionServlet
        job = self.job()
        job.experimentDesign = CURRENT
        job.setOrganism("dre")
        self.assertEqual(CompoundSuggestionServlet.buildContext(job)["experimentDesign"], "")


class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


class _Queue(object):
    """Keeps the job instance step 1 hands to the queue (its first argument)."""

    def __init__(self):
        self.jobs = []

    def enqueue(self, **call):
        self.jobs.append(call["args"][0])


class _Request(object):
    def __init__(self, form):
        self.form = MultiDict(form)
        self.files = MultiDict()
        self.cookies = {}


class JobCreationTest(_TmpJobTestCase):
    """pathwayAcquisitionStep1_PART1, upload and example branches, with the
    session, the file save and the database lookups stubbed out."""

    def submit(self, design, exampleMode=False):
        queue, response = _Queue(), _Response()
        form = {"specie": "dre", "jobDescription": "", "aiConsent": "true"}
        if design is not None:
            form["experimentDesign"] = design
        scenario = {"id": "default", "omics": [], "title": "Example"}
        with mock.patch.object(servlet, "CLIENT_TMP_DIR", self.tmp), \
                mock.patch.object(servlet, "UserSessionManager"), \
                mock.patch.object(servlet, "JobInformationManager"), \
                mock.patch.object(servlet.DatabaseAvailability, "resolveDatabases", return_value=["KEGG"]), \
                mock.patch.object(servlet.ExampleDatasets, "attachChainedExampleTargets", return_value=[]), \
                mock.patch.object(servlet.ExampleDatasets, "applyScenario", return_value=scenario):
            servlet.pathwayAcquisitionStep1_PART1(_Request(form), response, queue, "PH" + str(id(form))[-8:],
                                                  self.tmp, exampleMode)
        self.assertTrue(response.content and response.content.get("success"), response.content)
        self.assertEqual(len(queue.jobs), 1)
        return queue.jobs[0]

    def test_an_upload_with_the_placeholder_stores_no_design(self):
        for design in (CURRENT, OLDER, "  " + CURRENT.lower() + "\n"):
            job = self.submit(design)
            self.assertEqual(job.experimentDesign, "", design)

    def test_the_example_branch_stores_no_design_for_it_either(self):
        job = self.submit(CURRENT, exampleMode="example")
        self.assertEqual(job.experimentDesign, "")

    def test_a_typed_design_is_stored_as_typed(self):
        self.assertEqual(self.submit(REAL).getExperimentDesign(), REAL)
        self.assertEqual(self.submit(None).getExperimentDesign(), "")


def _placeholder_job():
    job = fx.make_job()
    job.getJobID = lambda: "JK5047lsnI"
    job.getExperimentDesign = lambda: CURRENT
    return job


class DesignCardTest(unittest.TestCase):
    """The card a stored placeholder job gets: nothing from the placeholder.
    The fake job's getter returns the raw text, so this is card.py's own
    guard, not the job class's."""

    def assertNothingFromThePlaceholder(self, card):
        text = "\n".join(card_mod.card_lines(card)).lower()
        for word in ("mouse liver", "knockout", "wildtype", "e.g."):
            self.assertNotIn(word, text)
        self.assertEqual(card["perturbation"], "not stated")
        self.assertEqual(card["perturbed_genes"], [])
        self.assertEqual(card["system"], "not stated")

    def test_the_job_card(self):
        self.assertNothingFromThePlaceholder(card_mod.job_card(_placeholder_job()))

    def test_the_walks_deterministic_card(self):
        job = _placeholder_job()
        card = service.design_card(job, card_mod.labels_by_omic(job), "greedy")
        self.assertNothingFromThePlaceholder(card)
        self.assertNotIn("mouse liver", service.walk_card_text(card).lower())

    def test_the_model_card_is_never_shown_the_placeholder(self):
        seen = []

        class Client(object):
            def complete_json(self, messages, *args, **kwargs):
                seen.append("\n".join(str(m.get("content", "")) for m in messages))
                return None

        job = _placeholder_job()
        service.design_card(job, card_mod.labels_by_omic(job), "model", client=Client())
        self.assertTrue(seen, "the model card made no call")
        self.assertNotIn("mouse liver", seen[0].lower())

    def test_a_real_design_still_reaches_the_card(self):
        job = _placeholder_job()
        job.getExperimentDesign = lambda: REAL
        card = card_mod.job_card(job)
        self.assertIn("Mouse liver", card["perturbation"])


def _calls(source, opener):
    """The argument text of every call that starts with `opener`, matched by
    bracket depth (the app's submit configs hold no string with a bracket)."""
    found = []
    for match in re.finditer(re.escape(opener), source):
        depth, start = 1, match.end()
        for index in range(start, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    found.append((source.count("\n", 0, match.start()) + 1, source[start:index]))
                    break
    return found


class ClientSubmitTest(unittest.TestCase):

    def test_every_form_submit_passes_submit_empty_text_false(self):
        checked, missing = 0, []
        for path in sorted(glob.glob(os.path.join(CLIENT_APP, "**", "*.js"), recursive=True)):
            with io.open(path, encoding="utf-8") as handle:
                source = handle.read()
            for line, args in _calls(source, ".submit("):
                checked += 1
                if not re.search(r"\bsubmitEmptyText\s*:\s*false\b", args):
                    missing.append("%s:%d" % (os.path.relpath(path, REPO), line))
        # Five at the time of writing (four in JobController, one in
        # DataManagementController) plus the grid's Excel export; fewer would
        # mean the scan stopped finding them, not that they are fixed.
        self.assertGreaterEqual(checked, 6)
        self.assertEqual(missing, [],
                         "these form submits send an empty field's emptyText as its value "
                         "(Ext JS 4's submitEmptyText defaults to true); pass "
                         "submitEmptyText: false: %s" % missing)


if __name__ == "__main__":
    unittest.main()
