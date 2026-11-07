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

Each answer has to say what is true of that job:

  * an owner with no account session is told signing in opens it, since the job
    is intact and the "Keep this job" mail sends them to this very link;
  * a Step 2 job whose metabolomics names all failed to match goes on to Step 2,
    because the gene analysis in it can still run. That needs a summary that really
    says zero, and gene data Step 2 can draw: a compound summary that cannot be read
    keeps the refusal, a gene summary that cannot be read says so and does not blame
    the names, and a job with only metabolites is told there is nothing to analyse;
  * a job is "still running" only while its queue entry is queued or started: one
    that failed gets the answer it would get with no entry at all;
  * a job at Step 3 with no pathways either finished with none (a warning) or did
    not finish storing them (an error with Report, since that one is ours);
  * the id is off the request and every answer reaches .html(), so it is escaped.

A link that cannot be opened on a page with nothing on it puts the Step 1 form
behind the dialog, with the job still in the address, and leaves any other page
where it is.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_unavailable_job_is_not_an_error
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.User import User
from src.common import JobProgress
from src.common.PySiQ import JobStatus, Queue
from src.common.UserSessionManager import UserSessionManager
from src.servlets import PathwayAcquisitionServlet as servlet
from src.tests.test_status_poll_retries_unreadable_answers import (
    CLIENT_ROOT, JOB_CONTROLLER, UTIL, extract_block, extract_function, read)

JOB_ID = "E7r3oPI1HS"
HOSTILE_ID = "<img src=x onerror=console.log(1)>"
OWNER = "17"
ANOTHER_ACCOUNT = "18"
NO_COOKIES = {}
MAIN_VIEW = os.path.join(CLIENT_ROOT, "app", "view", "MainView.js")
GENE_OMIC = {"omicName": "Gene expression", "omicSummary": [{"KEGG": 5, "Total": 5}, 3]}


def _session(userID):
    """The cookies of a session the real isValidUser accepts."""
    return {"userID": userID, "sessionToken": UserSessionManager().registerNewUser(userID)}


class _Request(object):
    def __init__(self, cookies, jobID):
        self.cookies = cookies
        self.form = {"jobID": jobID}
        self.files = {}


class _Response(object):
    def __init__(self):
        self.status, self.content = 200, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


def _job(userID=OWNER, lastStep=3, compoundOmics=(), summary=None, geneOmics=()):
    """A job with nothing stored in it, owned by `userID`."""
    job = mock.MagicMock()
    job.getJobID.return_value = JOB_ID
    job.getUserID.return_value = userID
    job.getAllowSharing.return_value = False
    job.getLastStep.return_value = lastStep
    job.getFoundCompounds.return_value = []
    job.getMatchedPathways.return_value = {}
    job.getMatchedClass.return_value = {}
    job.getCompoundBasedInputOmics.return_value = compoundOmics
    job.getGeneBasedInputOmics.return_value = geneOmics
    job.summary = summary
    return job


def _recover(job, queueJob=None, jobID=JOB_ID, cookies=None, isGuest=False, queue=None):
    """The real servlet and session check; `cookies` defaults to the owner's."""
    if queue is None:
        queue = mock.MagicMock()
        queue.fetch_job.return_value = queueJob
    manager = mock.MagicMock()
    manager.return_value.loadJobInstance.return_value = job
    account = User(OWNER)
    account.setIsGuest(isGuest)
    users = mock.MagicMock()
    users.return_value.findByID.return_value = account
    with mock.patch.object(servlet, "JobInformationManager", manager), \
            mock.patch.object(servlet, "UserDAO", users):
        response = _Response()
        servlet.pathwayAcquisitionRecoverJob(
            _Request(_session(OWNER) if cookies is None else cookies, jobID), response, queue)
    return response


def _running():
    job = mock.MagicMock()
    job.status = JobStatus.STARTED
    return job


def _compoundOmic(summary):
    return {"omicName": "Metabolomics", "omicSummary": summary}


class _AnswerCase(unittest.TestCase):

    def assertUnavailable(self, response, *phrases):
        self.assertEqual(response.status, 200)
        content = response.content
        self.assertIs(content["success"], False)
        self.assertIs(content.get("jobUnavailable"), True)
        # An older cached client reads errorMessage, and only after asking for
        # `message` first: a `message` here would turn this into its info dialog.
        self.assertNotIn("message", content)
        for phrase in phrases:
            self.assertIn(phrase, content["errorMessage"])


class TheAnswersAreFlaggedTest(_AnswerCase):

    def test_a_removed_job(self):
        self.assertUnavailable(_recover(None), JOB_ID,
                               "removed after 7 days for guests and 14 days for registered users",
                               "start a new analysis")

    def test_a_job_with_no_compounds_stored(self):
        self.assertUnavailable(_recover(_job(lastStep=2, compoundOmics=[{"omicName": "Metabolomics"}])),
                               "found compounds", "start a new analysis")

    def test_a_running_job_is_not_flagged_while_a_removed_one_is(self):
        """Still in the queue is not "cannot be opened": it keeps its own dialog."""
        self.assertIs(_recover(None).content.get("jobUnavailable"), True)
        content = _recover(_job(), queueJob=_running()).content
        self.assertIn("still running", content["message"])
        self.assertNotIn("jobUnavailable", content)


class WhoIsAskingTest(_AnswerCase):
    """The real isValidUser, for the kinds of caller it lets through."""

    def assertToldToSignIn(self, response):
        self.assertUnavailable(response, JOB_ID, "sign in", "has not been removed")
        self.assertNotIn("Invalid Job ID", response.content["errorMessage"])
        self.assertNotIn("start a new analysis", response.content["errorMessage"],
                         "the job is intact, and signing in opens it")

    def test_no_cookies_at_all(self):
        self.assertToldToSignIn(_recover(_job(), cookies=NO_COOKIES))

    def test_a_cookie_that_carried_no_user(self):
        """isValidUser compares str(userID), so this passes it as well."""
        self.assertToldToSignIn(_recover(_job(), cookies={"userID": "None"}))

    def test_a_guest_session(self):
        """A guest has a real userID and token, for an account made for that browser."""
        self.assertToldToSignIn(_recover(_job(), cookies=_session("90001"), isGuest=True))

    def test_a_registered_account_that_is_not_the_owner(self):
        response = _recover(_job(), cookies=_session(ANOTHER_ACCOUNT), isGuest=False)
        self.assertUnavailable(response, "Invalid Job ID (" + JOB_ID + ")", "check the Job ID")
        self.assertNotIn("sign in", response.content["errorMessage"])
        self.assertNotIn("start a new analysis", response.content["errorMessage"])

    def test_the_owner_gets_past_the_check(self):
        answer = _recover(_job(summary=[2, 0])).content["errorMessage"]
        self.assertNotIn("Invalid Job ID", answer)
        self.assertNotIn("sign in", answer)

    def test_a_session_that_is_not_valid_is_not_told_either(self):
        """The dialog for this one is "Invalid session", from ajaxErrorHandler."""
        response = _recover(_job(), cookies={"userID": OWNER, "sessionToken": "stale"})
        self.assertEqual(response.status, 400)
        self.assertIn("User not valid", response.content["message"])


class AStep2JobWithNoCompoundsTest(_AnswerCase):

    def recover(self, *summaries):
        return _recover(_job(lastStep=2, compoundOmics=[_compoundOmic(s) for s in summaries],
                             geneOmics=[GENE_OMIC]))

    def assertRefused(self, response):
        self.assertUnavailable(response, "found compounds")

    def test_metabolite_names_that_all_failed_to_match_go_on_to_step_2(self):
        for summaries in ([[0, 3]], [[0, 3, "extra"]], [[0, 3], [0, 8]]):
            with self.subTest(summaries=summaries):
                content = self.recover(*summaries).content
                self.assertIs(content["success"], True)
                self.assertEqual(content["stepNumber"], 2)
                self.assertNotIn("jobUnavailable", content)

    def test_names_that_matched_and_stored_nothing_are_still_refused(self):
        self.assertRefused(self.recover([5, 1]))
        self.assertRefused(self.recover([0, 3], [5, 1]))

    def test_a_summary_that_says_nothing_is_still_refused(self):
        """adaptBSON stores a None as the string "None", and mappedTotal reads that as 0."""
        for summary in (None, "None", [], {}):
            with self.subTest(summary=summary):
                self.assertRefused(self.recover(summary))

    def test_a_summary_that_cannot_be_read_is_not_read_as_zero(self):
        """The list and both counts, as updateSubmitedCompoundsList checks them.

        [0] raises IndexError in getMappedRatios on Next step. ['5', '3'] matched 5,
        and going on would drop those metabolites, then throw in Step 3. Compound
        documents store an int first; the dict is the gene omic's shape, so it is
        no more readable here than the rest.
        """
        for summary in ([0], [5], ["5", "3"], ["0", "3"], [None, 3], [5.0, 3], [0.0, 3],
                        [{"Total": "x"}, 3], [{"Total": 0}, 3], [{"KEGG": 0}, 3], [[0, 3]],
                        [False, 3], [True, 3], [0, False], [0, "3"], [0, None], [0, 3.0]):
            with self.subTest(summary=summary):
                self.assertRefused(self.recover(summary))

    def test_a_readable_omic_beside_an_unreadable_one_is_refused(self):
        self.assertRefused(self.recover([0, 3], ["5", "3"]))

    def test_an_omic_list_that_is_not_a_list_of_dicts_is_refused(self):
        """adaptBSON turns a stored None into the string, and omic.get would raise."""
        for omics in ("None", ["None"], [None], [_compoundOmic([0, 3]), "None"], (_compoundOmic([0, 3]),)):
            with self.subTest(omics=omics):
                self.assertRefused(_recover(_job(lastStep=2, compoundOmics=omics, geneOmics=[GENE_OMIC])))


class AJobWithOnlyMetabolitesThatMatchedNothingTest(_AnswerCase):
    """With no gene analysis to reach, Step 2 leads to 0 pathways and a message
    about the organism and the databases, which is not what is wrong."""

    def assertNothingToAnalyse(self, response):
        self.assertUnavailable(response, "None of the metabolite names in job " + JOB_ID,
                               "nothing to analyse", "Check the names", "start a new analysis")
        self.assertNotIn("found compounds", response.content["errorMessage"])

    def assertGeneDataCannotBeRead(self, response):
        """The names are not what is wrong here, and there is something to analyse."""
        self.assertUnavailable(response, "Job " + JOB_ID + " does not contain saved information about its gene data.",
                               "start a new analysis")
        for blame in ("metabolite", "names", "nothing to analyse", "found compounds"):
            self.assertNotIn(blame, response.content["errorMessage"])

    def recoverWithGeneOmics(self, omics):
        return _recover(_job(lastStep=2, compoundOmics=[_compoundOmic([0, 3])], geneOmics=omics))

    def test_no_gene_omics(self):
        for omics in ((), [], None, "None"):
            with self.subTest(omics=omics):
                self.assertNothingToAnalyse(self.recoverWithGeneOmics(omics))
                self.assertFalse(servlet.hasGeneAnalysisToRun(omics))

    def test_gene_omics_with_no_summary_to_read(self):
        for omics in (["None"], [None], [{"omicName": "G"}], [{"omicSummary": None}], [{"omicSummary": "None"}],
                      [{"omicSummary": []}], [{"omicSummary": [{"KEGG": 5}]}], [{"omicSummary": ["5", 3]}],
                      [{"omicSummary": [500.0, 0]}], [{"omicSummary": [None, 3]}], [{"omicSummary": [True, 3]}],
                      (GENE_OMIC,), GENE_OMIC):
            with self.subTest(omics=omics):
                self.assertGeneDataCannotBeRead(self.recoverWithGeneOmics(omics))

    def test_every_gene_omic_has_to_be_readable(self):
        """Step 2 draws them all: one it cannot read left "Loading job information..."
        up with no Close, so it does not go on with the readable one beside it."""
        unreadable = {"omicName": "G2", "omicSummary": None}
        for omics in ([GENE_OMIC, unreadable], [unreadable, GENE_OMIC], [GENE_OMIC, {"omicName": "G2"}], [GENE_OMIC, "None"],
                      [GENE_OMIC, GENE_OMIC, unreadable]):
            with self.subTest(omics=omics):
                self.assertGeneDataCannotBeRead(self.recoverWithGeneOmics(omics))

    def test_several_readable_gene_omics_go_on(self):
        for summaries in ([[{"KEGG": 5}, 3], [{"KEGG": 8}, 1]], [[{}, 3], [{}, 3]], [[5, 3], [8, 1]],
                          [[{"KEGG": 5}, 3], [8, 1]], [[{"KEGG": 5}, 3]], [[{"KEGG": 5}, 3, "extra"], [8, 1, "extra"]]):
            with self.subTest(summaries=summaries):
                omics = [{"omicName": "G%d" % i, "omicSummary": summary} for i, summary in enumerate(summaries)]
                content = self.recoverWithGeneOmics(omics).content
                self.assertIs(content["success"], True)
                self.assertEqual(content["stepNumber"], 2)
                self.assertNotIn("jobUnavailable", content)

    def test_a_job_that_matched_keeps_its_own_refusal(self):
        self.assertUnavailable(_recover(_job(lastStep=2, compoundOmics=[_compoundOmic([5, 1])])), "found compounds")

    def test_a_job_with_no_compound_omics_is_not_asked_about_metabolites(self):
        content = _recover(_job(lastStep=2, geneOmics=[GENE_OMIC])).content
        self.assertIs(content["success"], True)

    def test_a_later_step_is_not_asked_about_metabolites(self):
        response = _recover(_job(lastStep=3, summary=[2, 0], compoundOmics=[_compoundOmic([0, 3])]))
        self.assertUnavailable(response, "belongs to a pathway")


class AStep3JobWithNoPathwaysTest(_AnswerCase):

    def test_a_job_that_finished_with_none_says_why_and_offers_no_report(self):
        response = _recover(_job(lastStep=3, summary=[2, 0, 5, 0]))
        self.assertUnavailable(response, JOB_ID,
                               "none of its identifiers belongs to a pathway of the selected databases")
        self.assertNotIn("run it again", response.content["errorMessage"])

    def test_a_store_that_did_not_finish_stays_an_error(self):
        """summary[1] counted the pathways, and they are not there: that is ours."""
        for summary in ([2, 2], None, "None", [2]):
            with self.subTest(summary=summary):
                content = _recover(_job(lastStep=3, summary=summary)).content
                self.assertIs(content["success"], False)
                self.assertNotIn("jobUnavailable", content)
                self.assertNotIn("message", content)
                self.assertIn("run it again", content["errorMessage"])
                self.assertNotIn("<br>", content["errorMessage"], "this one is drawn as a title")


class TheQueueEntrySaysWhetherItIsRunningTest(_AnswerCase):
    """The real PySiQ queue and worker."""

    STILL_RUNNING = "is still running in the queue"

    def setUp(self):
        self.queue = Queue()
        self.queue.start_worker(1)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def waitFor(self, jobID, *statuses):
        deadline = time.monotonic() + 30
        while self.queue.check_status(jobID) not in statuses:
            self.assertLess(time.monotonic(), deadline, "%s is %s" % (jobID, self.queue.check_status(jobID)))
            time.sleep(0.005)

    def run_failing_entry(self):
        def fails():
            raise RuntimeError("this run failed")
        self.queue.enqueue(fn=fails, args=(), job_id=JOB_ID)
        self.waitFor(JOB_ID, JobStatus.FAILED)

    def test_a_job_that_is_running_or_waiting_says_so(self):
        self.queue.enqueue(fn=self.release.wait, args=(30,), job_id=JOB_ID)
        self.waitFor(JOB_ID, JobStatus.STARTED)
        self.queue.enqueue(fn=self.release.wait, args=(30,), job_id="waiting")
        self.assertEqual(self.queue.check_status("waiting"), JobStatus.QUEUED)
        for jobID in (JOB_ID, "waiting"):
            with self.subTest(jobID=jobID):
                content = _recover(None, jobID=jobID, queue=self.queue).content
                self.assertIn(self.STILL_RUNNING, content["message"])
                self.assertNotIn("jobUnavailable", content)

    def test_a_failed_entry_with_no_job_is_not_found(self):
        self.run_failing_entry()
        response = _recover(None, queue=self.queue)
        self.assertUnavailable(response, "not found at database", "removed after 7 days")
        self.assertNotIn(self.STILL_RUNNING, response.content["errorMessage"])

    def test_a_failed_entry_gets_the_stored_state_of_its_job(self):
        self.run_failing_entry()
        self.assertUnavailable(_recover(_job(lastStep=3, summary=[2, 0]), queue=self.queue), "belongs to a pathway")
        content = _recover(_job(lastStep=2), queue=self.queue).content
        self.assertIs(content["success"], True)
        self.assertEqual(content["stepNumber"], 2)

    def test_a_finished_entry_still_gets_the_stored_state(self):
        self.queue.enqueue(fn=lambda: None, args=(), job_id=JOB_ID)
        self.waitFor(JOB_ID, JobStatus.FINISHED)
        self.assertEqual(_recover(_job(lastStep=2), queue=self.queue).content["stepNumber"], 2)

    def step2(self, job):
        answer = _Response()
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        with mock.patch.object(servlet, "JobInformationManager", manager):
            self.queue.enqueue(fn=servlet.pathwayAcquisitionStep2_PART2,
                               args=(JOB_ID, "u1", [], {}, answer, "/nowhere/", {}), job_id=JOB_ID)
            self.waitFor(JOB_ID, JobStatus.FINISHED, JobStatus.FAILED)
        return answer

    def test_a_job_removed_while_it_waited_says_so_and_does_not_fail_the_entry(self):
        answer = self.step2(None)
        entry = self.queue.fetch_job(JOB_ID)
        self.assertEqual(entry.status, JobStatus.FINISHED, entry.error_message)
        self.assertEqual(answer.status, 400)
        self.assertIn(JOB_ID + " was not found at database", answer.content["message"])
        self.assertNotIn(JOB_ID, JobProgress._records, "the progress ledger never lets go of it otherwise")
        self.assertUnavailable(_recover(None, queue=self.queue), "not found at database")

    def test_a_job_that_was_found_still_cleans_up_after_itself(self):
        job = mock.MagicMock()
        job.getJobID.return_value = JOB_ID
        job.updateSubmitedCompoundsList.side_effect = ValueError("stop here")
        answer = self.step2(job)
        job.cleanDirectories.assert_called_once_with()
        self.assertEqual(answer.status, 400)
        self.assertNotIn(JOB_ID, JobProgress._records)


class TheIdIsNotMarkupTest(unittest.TestCase):
    """Every one of these reaches the dialog through jQuery's .html()."""

    STATES = {
        "not found": lambda jobID: _recover(None, jobID=jobID),
        "still running": lambda jobID: _recover(_job(), queueJob=_running(), jobID=jobID),
        "signed out": lambda jobID: _recover(_job(), jobID=jobID, cookies=NO_COOKIES),
        "another account": lambda jobID: _recover(_job(), jobID=jobID, cookies=_session(ANOTHER_ACCOUNT)),
        "no compounds stored": lambda jobID: _recover(_job(lastStep=2, compoundOmics=[_compoundOmic([5, 1])]), jobID=jobID),
        "only metabolites, none matched": lambda jobID: _recover(_job(lastStep=2, compoundOmics=[_compoundOmic([0, 3])]), jobID=jobID),
        "gene summary cannot be read": lambda jobID: _recover(_job(lastStep=2, compoundOmics=[_compoundOmic([0, 3])], geneOmics=[{"omicName": "G"}]), jobID=jobID),
        "no pathways matched": lambda jobID: _recover(_job(lastStep=3, summary=[2, 0]), jobID=jobID),
        "store did not finish": lambda jobID: _recover(_job(lastStep=3, summary=[2, 2]), jobID=jobID),
    }

    @staticmethod
    def text(content):
        return content.get("errorMessage") or content.get("message")

    def test_a_hostile_id_arrives_as_text_in_every_answer(self):
        for name, answer in self.STATES.items():
            with self.subTest(state=name):
                text = self.text(answer(HOSTILE_ID).content)
                self.assertNotIn("<img", text)
                self.assertIn("&lt;img src=x onerror=console.log(1)&gt;", text)

    def test_a_normal_id_is_left_alone(self):
        for name, answer in self.STATES.items():
            with self.subTest(state=name):
                self.assertIn(JOB_ID, self.text(answer(JOB_ID).content))

    def test_load_requested_job(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = None
        with mock.patch.object(servlet, "JobInformationManager", manager):
            with self.assertRaises(UserWarning) as hostile:
                servlet.loadRequestedJob(HOSTILE_ID, "step 2")
            with self.assertRaises(UserWarning) as normal:
                servlet.loadRequestedJob(JOB_ID, "step 2")
        self.assertNotIn("<img", str(hostile.exception))
        self.assertIn("&lt;img", str(hostile.exception))
        self.assertIn(JOB_ID, str(normal.exception))

    def test_step_2_on_the_queue(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = None
        response = _Response()
        # Its `finally` then calls cleanDirectories on the job it did not find.
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"), contextlib.suppress(AttributeError):
            servlet.pathwayAcquisitionStep2_PART2(HOSTILE_ID, None, [], {}, response, "/nowhere/", {})
        self.assertNotIn("<img", response.content["message"])
        self.assertIn("&lt;img", response.content["message"])

    def test_the_hub_routes(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = None
        with mock.patch.object(servlet, "JobInformationManager", manager):
            _, refusal = servlet._hubOwnedJob(HOSTILE_ID, None, "HUB")
        self.assertNotIn("<img", refusal["errorMessage"])
        self.assertIn("&lt;img", refusal["errorMessage"])

    def test_step_1_with_an_unknown_example_mode(self):
        response = _Response()
        with mock.patch.object(servlet, "PathwayAcquisitionJob"):
            servlet.pathwayAcquisitionStep1_PART1(
                _Request(NO_COOKIES, JOB_ID), response, mock.MagicMock(), JOB_ID, "/nowhere/", HOSTILE_ID)
        self.assertEqual(response.status, 400)
        self.assertNotIn("<img", response.content["message"])
        self.assertIn("&lt;img", response.content["message"])
        self.assertNotIn("<dataset-id>", response.content["message"], "its own example read as an element")


class TheReplicateMappingEchoesTest(unittest.TestCase):

    def answer(self, job, **payload):
        request = mock.MagicMock()
        request.cookies = NO_COOKIES
        request.get_json.return_value = dict({"jobID": JOB_ID, "omicName": "RNA", "mode": "auto"}, **payload)
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        response = _Response()
        with mock.patch.object(servlet, "JobInformationManager", manager):
            servlet.pathwayAcquisitionApplyReplicateMapping(request, response)
        return response.content["message"]

    def test_none_of_the_three_is_markup(self):
        noSuchOmic = mock.MagicMock()
        noSuchOmic.getReadOnly.return_value = False
        noSuchOmic._findInputOmicByName.return_value = (None, None, None)
        for message in (self.answer(_job(), mode=HOSTILE_ID),
                        self.answer(None, jobID=HOSTILE_ID),
                        self.answer(noSuchOmic, omicName=HOSTILE_ID)):
            self.assertNotIn("<img", message)
            self.assertIn("&lt;img", message)


HARNESS = r"""
const SERVER_URL_PA_RECOVER_JOB = "/recover";
const SERVER_URL_PA_TOUCH_JOB = "/touch";
let events = [];
const shown = [];
function showMessage(title, data) { events.push("dialog: " + title); shown.push({title: title, data: data}); }
%(dialogs)s
let answer = null, failure = null;
const requests = [];
const $ = {ajax: function (opts) {
    requests.push(opts.url);
    if (failure) { opts.error(failure); } else { opts.success(answer); }
}};
function ajaxErrorHandler(response) { events.push("ajaxErrorHandler: " + response.status); }
/* What the real showJobInstance touches, so a job id in the model would reach the
   address and the touch route here as it does in the browser. */
const replaced = [];
const window = {location: {pathname: "/"}, history: {replaceState(a, b, url) { replaced.push(url); }}};
const console = {info() {}, error() {}, log: globalThis.console.log};
Date.logFormat = function () { return ""; };
function JobInstance(jobID) {
    this.jobID = jobID;
    this.getJobID = function () { return this.jobID; };
    this.getStepNumber = function () { return 1; };
    this.addObserver = function () {};
}
function PA_Step1JobView() {
    this.setController = function () {};
    this.loadModel = function (model) { this.model = model; };
    this.getName = function () { return "PA_Step1JobView"; };
}
let mainView = null;
const application = {getMainView() { return mainView; }};
function newMainView(onScreen) {
    return {
        currentView: onScreen, subviews: {},
        getSubView(name) { return this.subviews[name]; },
        addMainView(view) { this.subviews[view.getName()] = view; },
        changeMainView(name) { events.push("view: " + name + " job " + this.subviews[name].model.getJobID()); this.currentView = this.subviews[name]; }
    };
}
function Controller() {
    this.cleanStoredApplicationData = function () { throw new Error("a refused job reached the model building"); };
    %(handler)s
    %(showJobInstance)s
}
function run(onScreen) {
    events = [];
    requests.length = 0;
    replaced.length = 0;
    mainView = newMainView(onScreen);
    new Controller().recoverPAJobHandler("JOB");
    return {dialog: shown[shown.length - 1], events: events, requests: requests.slice(), replaced: replaced.slice(),
            onScreen: mainView.currentView && mainView.currentView.getName()};
}
const someView = {getName() { return "DM_MyDataListView"; }};
const answers = %(answers)s;
const results = {emptyPage: {}, busyPage: {}};
for (const name of Object.keys(answers)) {
    answer = answers[name];
    results.emptyPage[name] = run(null);
    results.busyPage[name] = run(someView);
}
failure = {status: 400, responseText: "{}"};
results.failedRequest = {emptyPage: run(null), busyPage: run(someView)};
globalThis.console.log(JSON.stringify(results));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheBrowserShowsAWarningTest(unittest.TestCase):
    """The real handler and dialog helpers, fed the server's real answers."""

    @classmethod
    def setUpClass(cls):
        util = read(UTIL)
        answers = {"removed": _recover(None).content,
                   "signedOut": _recover(_job(), cookies=NO_COOKIES).content,
                   "anotherAccount": _recover(_job(), cookies=_session(ANOTHER_ACCOUNT)).content,
                   "noPathwayMatched": _recover(_job(lastStep=3, summary=[2, 0])).content,
                   "storeDidNotFinish": _recover(_job(lastStep=3, summary=[2, 2])).content,
                   "stillRunning": _recover(_job(), queueJob=_running()).content,
                   "olderServer": {"success": False, "errorMessage": "old server"}}
        controller = read(JOB_CONTROLLER)
        script = HARNESS % {
            "dialogs": "\n".join(extract_function(util, name, "Util.js") for name in
                                 ("showInfoMessage", "showWarningMessage", "showErrorMessage")),
            "handler": extract_block(controller, "this.recoverPAJobHandler = function (jobID)", "JobController.js"),
            "showJobInstance": extract_block(controller, "this.showJobInstance = function (jobModel, options)", "JobController.js"),
            "answers": json.dumps(answers),
        }
        directory = tempfile.mkdtemp(prefix="paintomics-recover-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)
        cls.dialogs = {name: run["dialog"] for name, run in cls.results["emptyPage"].items()}

    def test_the_flag_gets_a_warning_with_a_title_and_a_way_to_close_it(self):
        expected = {"removed": "removed after 7 days", "signedOut": "sign in",
                    "anotherAccount": "Invalid Job ID", "noPathwayMatched": "belongs to a pathway"}
        for name, phrase in expected.items():
            with self.subTest(answer=name):
                dialog = self.dialogs[name]
                self.assertEqual(dialog["title"], "This job is not available")
                self.assertEqual(dialog["data"]["messageType"], "warning")
                self.assertIn(phrase, dialog["data"]["message"])
                self.assertIs(dialog["data"]["showButton"], True)
                self.assertFalse(dialog["data"].get("showReportButton"))

    def test_an_answer_with_no_flag_still_reads_as_an_error(self):
        """A server older than this client, mid-deploy."""
        dialog = self.dialogs["olderServer"]
        self.assertEqual(dialog["title"], "old server")
        self.assertEqual(dialog["data"]["messageType"], "error")
        self.assertIs(dialog["data"]["showReportButton"], True)

    def test_a_store_that_did_not_finish_can_be_reported(self):
        dialog = self.dialogs["storeDidNotFinish"]
        self.assertIn("run it again", dialog["title"])
        self.assertEqual(dialog["data"]["messageType"], "error")
        self.assertIs(dialog["data"]["showReportButton"], True)

    def test_a_running_job_can_be_closed(self):
        """A modal with closable:false has no other way out, and a reload of a
        job link lands on the same dialog until the job ends."""
        dialog = self.dialogs["stillRunning"]
        self.assertIn("still running", dialog["title"])
        self.assertEqual(dialog["data"]["messageType"], "info")
        self.assertIs(dialog["data"]["showButton"], True)
        self.assertFalse(dialog["data"].get("showReportButton"))

    def test_a_link_refused_on_an_empty_page_puts_step_1_behind_the_dialog(self):
        for name, run in self.results["emptyPage"].items():
            with self.subTest(answer=name):
                self.assertEqual(run["events"][0], "dialog: Loading job information...")
                self.assertEqual(run["events"][1], "view: PA_Step1JobView job null")
                self.assertTrue(run["events"][2].startswith("dialog: "), "the dialog has to end up on top")
                self.assertEqual(len(run["events"]), 3)
                self.assertEqual(run["onScreen"], "PA_Step1JobView")

    def test_the_job_stays_in_the_address_for_the_sign_in_reload(self):
        for name, run in self.results["emptyPage"].items():
            with self.subTest(answer=name):
                self.assertEqual(run["replaced"], [])
                self.assertEqual(run["requests"], ["/recover"], "no job was opened, so none is touched")

    def test_a_page_already_showing_something_stays_where_it_is(self):
        """My Jobs calls this handler too."""
        for name, run in self.results["busyPage"].items():
            with self.subTest(answer=name):
                self.assertEqual(run["onScreen"], "DM_MyDataListView")
                self.assertFalse([event for event in run["events"] if event.startswith("view: ")])
                self.assertEqual(run["replaced"], [])

    def test_a_request_that_failed_is_handled_the_same_way(self):
        failed = self.results["failedRequest"]
        self.assertEqual(failed["emptyPage"]["events"][1:],
                         ["view: PA_Step1JobView job null", "ajaxErrorHandler: 400"])
        self.assertEqual(failed["busyPage"]["events"][1:], ["ajaxErrorHandler: 400"])
        self.assertEqual(failed["busyPage"]["onScreen"], "DM_MyDataListView")


MAIN_VIEW_HARNESS = r"""
const shown = [];
const application = {getController() { return {showJobInstance(model) { shown.push(model); }}; }};
function MainView(lastJobView) {
    this.currentView = null;
    this.subviews = {};
    this.getLastJobView = function () { return lastJobView; };
    %(changeMainView)s
}
const results = {};
results.noJobView = new MainView(null).changeMainView("paintPathways") === undefined;
results.shownWithNoJobView = shown.length;
new MainView({getModel() { return "the job"; }}).changeMainView("paintPathways");
results.shownWithAJobView = shown;
console.log(JSON.stringify(results));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheJobViewButtonWithNoJobTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        script = MAIN_VIEW_HARNESS % {
            "changeMainView": extract_block(read(MAIN_VIEW), "this.changeMainView = function(aViewName)", "MainView.js")}
        directory = tempfile.mkdtemp(prefix="paintomics-mainview-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_it_does_nothing_when_there_is_no_job_view(self):
        self.assertIs(self.results["noJobView"], True)
        self.assertEqual(self.results["shownWithNoJobView"], 0)

    def test_it_still_goes_back_to_the_last_job_view(self):
        self.assertEqual(self.results["shownWithAJobView"], ["the job"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
