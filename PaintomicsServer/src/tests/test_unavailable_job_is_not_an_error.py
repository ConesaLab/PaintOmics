#!/usr/bin/env python3
"""A job that cannot be opened is told as that, with no Report button.

Why this exists
---------------
Eleven error reports up to 2026-11-03 said

    Job E7r3oPI1HS not found at database. Please, note that jobs are
    automatically removed after 7 days for guests and 14 days for registered
    users.

Job E7r3oPI1HS was a guest job from 20 August, correctly removed. Nothing was
wrong: retention worked and the sentence said so. But recoverPAJobHandler showed
every such answer through showErrorMessage, which offers Report error, so people
pressed it.

pathwayAcquisitionRecoverJob now flags the answers that mean "this job cannot be
opened" with `jobUnavailable`, and the client shows those as a warning, as
sendReportMessage already does for its refusals. `errorMessage` stays the whole
message for a client still open from before the flag existed, and the sentence
with the two numbers stays too: test_retention_matches_the_promise reads them
out of it.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_unavailable_job_is_not_an_error
"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.servlets import PathwayAcquisitionServlet as servlet

JOB_CONTROLLER_JS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html", "app", "controller", "JobController.js"))

JOB_ID = "E7r3oPI1HS"


class _Request(object):
    def __init__(self):
        self.cookies = {"userID": "u1", "sessionToken": "token"}
        self.form = {"jobID": JOB_ID}


class _Response(object):
    def __init__(self):
        self.status, self.content = 200, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


def _job(userID="u1", lastStep=3, compoundOmics=()):
    """A job with nothing stored in it, owned by `userID`."""
    job = mock.MagicMock()
    job.getJobID.return_value = JOB_ID
    job.getUserID.return_value = userID
    job.getAllowSharing.return_value = False
    job.getLastStep.return_value = lastStep
    job.getFoundCompounds.return_value = []
    job.getMatchedPathways.return_value = {}
    job.getMatchedClass.return_value = {}
    job.getCompoundBasedInputOmics.return_value = list(compoundOmics)
    return job


def _recover(job, queueJob=None):
    queue = mock.MagicMock()
    queue.fetch_job.return_value = queueJob
    manager = mock.MagicMock()
    manager.return_value.loadJobInstance.return_value = job
    with mock.patch.object(servlet, "UserSessionManager"), \
            mock.patch.object(servlet, "JobInformationManager", manager):
        response = _Response()
        servlet.pathwayAcquisitionRecoverJob(_Request(), response, queue)
    return response


class TheAnswersAreFlaggedTest(unittest.TestCase):

    def assertUnavailable(self, response, *phrases):
        self.assertEqual(response.status, 200)
        content = response.content
        self.assertIs(content["success"], False)
        self.assertIs(content.get("jobUnavailable"), True)
        # An older cached client reads errorMessage, and only after asking for
        # `message` first: a `message` here would turn this into its info dialog.
        self.assertNotIn("message", content)
        for phrase in phrases + ("start a new analysis",):
            self.assertIn(phrase, content["errorMessage"])

    def test_a_removed_job(self):
        self.assertUnavailable(_recover(None), JOB_ID,
                               "removed after 7 days for guests and 14 days for registered users")

    def test_another_users_job(self):
        self.assertUnavailable(_recover(_job(userID="someone else")),
                               "Invalid Job ID (" + JOB_ID + ")")

    def test_a_job_with_no_compounds_stored(self):
        self.assertUnavailable(_recover(_job(lastStep=2, compoundOmics=[{"omicName": "Metabolomics"}])),
                               "found compounds")

    def test_a_job_with_no_pathways_stored(self):
        self.assertUnavailable(_recover(_job(lastStep=3)), "information about pathways")

    def test_a_running_job_is_not_flagged_while_a_removed_one_is(self):
        """Still in the queue is not "cannot be opened": it keeps its own dialog."""
        self.assertIs(_recover(None).content.get("jobUnavailable"), True)
        running = mock.MagicMock()
        running.is_finished.return_value = False
        content = _recover(_job(), queueJob=running).content
        self.assertIn("still running", content["message"])
        self.assertNotIn("jobUnavailable", content)


class TheBrowserShowsAWarningTest(unittest.TestCase):
    """Source assertions: this handler is a page of model building around a branch."""

    def setUp(self):
        with io.open(JOB_CONTROLLER_JS, encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("this.recoverPAJobHandler = function (jobID) {")
        self.handler = source[start:source.index("me.cleanStoredApplicationData();", start)]

    def test_the_flag_gets_a_warning_with_a_title_and_a_way_to_close_it(self):
        self.assertIn("response.jobUnavailable === true", self.handler)
        flag = self.handler.index("response.jobUnavailable === true")
        branch = self.handler[flag:self.handler.index("} else {", flag)]
        self.assertIn('showWarningMessage("This job is not available"', branch)
        self.assertIn("message: response.errorMessage", branch)
        # showMessage draws no Close button unless asked, and the window has no
        # other way out: a warning without this cannot be dismissed.
        self.assertIn("showButton: true", branch)
        self.assertNotIn("showReportButton: true", branch)

    def test_an_answer_with_no_flag_still_reads_as_an_error(self):
        """A server older than this client, mid-deploy."""
        self.assertIn("response.jobUnavailable === true", self.handler)
        flag = self.handler.index("response.jobUnavailable === true")
        self.assertIn("showErrorMessage(response.errorMessage)", self.handler[flag:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
