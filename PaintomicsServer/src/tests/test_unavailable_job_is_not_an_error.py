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
    because the gene analysis in it can still run;
  * a job at Step 3 with no pathways either finished with none (a warning) or did
    not finish storing them (an error with Report, since that one is ours);
  * the id is off the request and every answer reaches .html(), so it is escaped.

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
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.User import User
from src.common.UserSessionManager import UserSessionManager
from src.servlets import PathwayAcquisitionServlet as servlet
from src.tests.test_status_poll_retries_unreadable_answers import (
    JOB_CONTROLLER, UTIL, extract_block, extract_function, read)

JOB_ID = "E7r3oPI1HS"
HOSTILE_ID = "<img src=x onerror=console.log(1)>"
OWNER = "17"
ANOTHER_ACCOUNT = "18"
NO_COOKIES = {}


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


def _job(userID=OWNER, lastStep=3, compoundOmics=(), summary=None):
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
    job.summary = summary
    return job


def _recover(job, queueJob=None, jobID=JOB_ID, cookies=None, isGuest=False):
    """The real servlet and session check; `cookies` defaults to the owner's."""
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
    job.is_finished.return_value = False
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
        return _recover(_job(lastStep=2, compoundOmics=[_compoundOmic(s) for s in summaries]))

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


class TheIdIsNotMarkupTest(unittest.TestCase):
    """Every one of these reaches the dialog through jQuery's .html()."""

    STATES = {
        "not found": lambda jobID: _recover(None, jobID=jobID),
        "still running": lambda jobID: _recover(_job(), queueJob=_running(), jobID=jobID),
        "signed out": lambda jobID: _recover(_job(), jobID=jobID, cookies=NO_COOKIES),
        "another account": lambda jobID: _recover(_job(), jobID=jobID, cookies=_session(ANOTHER_ACCOUNT)),
        "no compounds stored": lambda jobID: _recover(_job(lastStep=2, compoundOmics=[_compoundOmic([5, 1])]), jobID=jobID),
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
const shown = [];
function showMessage(title, data) { shown.push({title: title, data: data}); }
%(dialogs)s
let answer = null;
const $ = {ajax: function (opts) { opts.success(answer); }};
function ajaxErrorHandler() {}
function Controller() {
    this.cleanStoredApplicationData = function () { throw new Error("a refused job reached the model building"); };
    %(handler)s
}
const answers = %(answers)s;
const results = {};
for (const name of Object.keys(answers)) {
    shown.length = 0;
    answer = answers[name];
    new Controller().recoverPAJobHandler("JOB");
    results[name] = shown[shown.length - 1];
}
console.log(JSON.stringify(results));
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
        script = HARNESS % {
            "dialogs": "\n".join(extract_function(util, name, "Util.js") for name in
                                 ("showInfoMessage", "showWarningMessage", "showErrorMessage")),
            "handler": extract_block(read(JOB_CONTROLLER), "this.recoverPAJobHandler = function (jobID)", "JobController.js"),
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

    def test_the_flag_gets_a_warning_with_a_title_and_a_way_to_close_it(self):
        expected = {"removed": "removed after 7 days", "signedOut": "sign in",
                    "anotherAccount": "Invalid Job ID", "noPathwayMatched": "belongs to a pathway"}
        for name, phrase in expected.items():
            with self.subTest(answer=name):
                dialog = self.results[name]
                self.assertEqual(dialog["title"], "This job is not available")
                self.assertEqual(dialog["data"]["messageType"], "warning")
                self.assertIn(phrase, dialog["data"]["message"])
                self.assertIs(dialog["data"]["showButton"], True)
                self.assertFalse(dialog["data"].get("showReportButton"))

    def test_an_answer_with_no_flag_still_reads_as_an_error(self):
        """A server older than this client, mid-deploy."""
        dialog = self.results["olderServer"]
        self.assertEqual(dialog["title"], "old server")
        self.assertEqual(dialog["data"]["messageType"], "error")
        self.assertIs(dialog["data"]["showReportButton"], True)

    def test_a_store_that_did_not_finish_can_be_reported(self):
        dialog = self.results["storeDidNotFinish"]
        self.assertIn("run it again", dialog["title"])
        self.assertEqual(dialog["data"]["messageType"], "error")
        self.assertIs(dialog["data"]["showReportButton"], True)

    def test_a_running_job_can_be_closed(self):
        """A modal with closable:false has no other way out, and a reload of a
        job link lands on the same dialog until the job ends."""
        dialog = self.results["stillRunning"]
        self.assertIn("still running", dialog["title"])
        self.assertEqual(dialog["data"]["messageType"], "info")
        self.assertIs(dialog["data"]["showButton"], True)
        self.assertFalse(dialog["data"].get("showReportButton"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
