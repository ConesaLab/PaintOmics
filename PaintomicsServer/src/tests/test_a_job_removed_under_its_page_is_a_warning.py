#!/usr/bin/env python3
"""A job that has gone is told as that wherever it is asked for, with no Report button.

Why this exists
---------------
PR #218 made a job's link answer "This job is not available", a warning with no
Report button, when the job is not in the database. Two places still said
something else about the same jobs.

A page that already had the job open, and then asked for it again after it was
removed (Next step, a pathway, or the status poll of a Step 2 waiting on the
queue), got the true sentence "Job X was not found at database." under
"Oops..Internal error!" with Report. loadRequestedJob and Step 2's queued half
raised a plain UserWarning, and ajaxErrorHandler had no way to tell it from a
crash. They now raise JobUnavailableError, handleException flags it
(extra.job_unavailable), and ajaxErrorHandler shows the same warning as the link,
with the same note about retention.

The link to a job whose Step 1 failed, opened while the queue still holds that
run, said the job was not found and that jobs are removed after 7 and 14 days.
Nothing was ever saved for it, so the note was false. It now says the job did not
finish its first step and nothing was saved.

Pinned here: the server raises and flags it, the flag survives the real status
poll route, the real ajaxErrorHandler words it as the link does, and the link's
answers for a first step that stopped, using the real queue and the real Step 1.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_job_removed_under_its_page_is_a_warning
"""
import json
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src import paintomicsserver as server
from src.common.PySiQ import Job, JobStatus, Queue
from src.common.ServerErrorManager import InputFileError, JobUnavailableError
from src.conf.serverconf import SERVER_SUBDOMAIN
from src.paintomicsserver import Response
from src.servlets import PathwayAcquisitionServlet as servlet
from src.tests.test_an_invalid_session_is_told_by_its_own_answer import _answerTo, runDialogs
from src.tests.test_unavailable_job_is_not_an_error import HOSTILE_ID, JOB_ID, _AnswerCase, _recover

RETENTION = "removed after 7 days for guests and 14 days for registered users"


def _missing():
    manager = mock.MagicMock()
    manager.return_value.loadJobInstance.return_value = None
    return mock.patch.object(servlet, "JobInformationManager", manager)


class TheServerSaysTheJobHasGoneTest(unittest.TestCase):

    def test_load_requested_job_raises_it_with_the_note_about_retention(self):
        with _missing():
            with self.assertRaises(JobUnavailableError) as caught:
                servlet.loadRequestedJob(JOB_ID, "step 3")
        message = str(caught.exception)
        self.assertIn("Job " + JOB_ID + " was not found at database.", message)
        self.assertIn(RETENTION, message)
        self.assertIn("start a new analysis", message)

    def test_it_is_still_a_user_warning(self):
        """Callers and tests that catch UserWarning keep working."""
        self.assertTrue(issubclass(JobUnavailableError, UserWarning))

    def test_the_id_is_escaped(self):
        with _missing():
            with self.assertRaises(JobUnavailableError) as caught:
                servlet.loadRequestedJob(HOSTILE_ID, "step 3")
        self.assertNotIn("<img", str(caught.exception))
        self.assertIn("&lt;img src=x", str(caught.exception))

    def test_a_missing_id_is_not_a_job_that_has_gone(self):
        """A request with no jobID is malformed, which is ours to hear about."""
        with self.assertRaises(UserWarning) as caught:
            servlet.loadRequestedJob(None, "step 3")
        self.assertNotIsInstance(caught.exception, JobUnavailableError)

    def test_handle_exception_flags_it_and_nothing_else(self):
        gone = _answerTo(JobUnavailableError("Job X was not found at database."))
        self.assertIs(gone["extra"]["job_unavailable"], True)
        self.assertIs(gone["extra"]["input_error"], False)
        for other in (UserWarning("Missing jobID parameter for step 3."), ValueError("broken"),
                      InputFileError("bad file")):
            with self.subTest(exception=type(other).__name__):
                self.assertIs(_answerTo(other)["extra"]["job_unavailable"], False)

    def test_step_2_on_the_queue_raises_it(self):
        with _missing(), mock.patch.object(servlet, "JobProgress"):
            response = servlet.pathwayAcquisitionStep2_PART2(JOB_ID, "u1", [], {}, Response(), "/nowhere/", {})
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["extra"]["job_unavailable"], True)
        self.assertIn(JOB_ID + " was not found at database.", response.content["message"])
        self.assertIn(RETENTION, response.content["message"])

    def test_step_3_raises_it(self):
        """A pathway clicked on a page whose job was removed."""
        request = mock.MagicMock()
        request.cookies = {}
        request.form = {"jobID": JOB_ID}
        with _missing():
            response = servlet.pathwayAcquisitionStep3(request, Response())
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["extra"]["job_unavailable"], True)

    def test_the_flag_survives_the_status_poll(self):
        """The page waiting on Step 2 reads the answer through /check_job_status,
        which hands back the body the queued function wrote."""
        with mock.patch.object(server, "configureLogging"), \
                mock.patch.object(server, "KeggInformationManager"), \
                mock.patch.object(server, "JobInformationManager"), \
                mock.patch.object(server.Application, "ensureIndexes"), \
                mock.patch.object(server.Application, "startScheludeTasks"):
            application = server.Application()
        with _missing(), mock.patch.object(servlet, "JobProgress"):
            application.queue.enqueue(fn=servlet.pathwayAcquisitionStep2_PART2,
                                      args=("gonejob", "u1", [], {}, Response(), "/nowhere/", {}),
                                      job_id="gonejob")
            client = application.app.test_client()
            url = SERVER_SUBDOMAIN + "/check_job_status/gonejob"
            deadline = time.monotonic() + 30
            answer = client.post(url)
            while answer.status_code == 200 and time.monotonic() < deadline:
                time.sleep(0.01)
                answer = client.post(url)
        self.assertEqual(answer.status_code, 400)
        body = json.loads(answer.get_data(as_text=True))
        self.assertIs(body["extra"]["job_unavailable"], True)
        self.assertIn("gonejob was not found at database.", body["message"])


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheBrowserShowsItAsTheLinkDoesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with _missing(), mock.patch.object(servlet, "JobProgress"):
            step2 = servlet.pathwayAcquisitionStep2_PART2(JOB_ID, "17", [], {}, Response(), "/nowhere/", {})
        cls.results = runDialogs({
            "removedWhileWaiting": step2.content,
            "crash": _answerTo(ValueError("The table is broken.")),
        })

    def test_it_is_the_warning_the_link_shows(self):
        dialog = self.results["removedWhileWaiting"]
        self.assertIn("This job is not available", dialog["title"])
        self.assertEqual(dialog["kind"], "warningDialog")
        self.assertNotIn("Oops", dialog["title"])

    def test_it_offers_no_report_and_can_be_closed(self):
        dialog = self.results["removedWhileWaiting"]
        self.assertEqual(dialog["report"], "none")
        self.assertEqual(dialog["close"], "inline-block")

    def test_it_says_what_is_true_and_nothing_for_the_log(self):
        body = self.results["removedWhileWaiting"]["body"]
        self.assertIn("Job " + JOB_ID + " was not found at database.", body)
        self.assertIn(RETENTION, body)
        self.assertNotIn("User ID", body)
        self.assertNotIn("JobUnavailableError", body)
        self.assertNotIn("retrying in the browser", body)

    def test_a_crash_is_still_an_error_with_report(self):
        dialog = self.results["crash"]
        self.assertIn("Oops..Internal error!", dialog["title"])
        self.assertEqual(dialog["kind"], "errorDialog")
        self.assertEqual(dialog["report"], "inline-block")


class AFirstStepThatStoppedTest(_AnswerCase):
    """The link to a job whose Step 1 failed, while the queue still holds that run."""

    UNSAVED = "did not finish its first step, so nothing was saved"

    def entry(self, status, result=None, fn=servlet.pathwayAcquisitionStep1_PART2):
        job = Job(fn, ())
        job.status = status
        job.result = result
        return job

    def failedResponse(self):
        return Response().setStatus(400).setContent({"success": False, "message": "refused"})

    def assertUnsaved(self, response):
        self.assertUnavailable(response, JOB_ID, self.UNSAVED, "start a new analysis")
        self.assertNotIn("7 days", response.content["errorMessage"],
                         "retention removed nothing: the job was never saved")

    def test_a_first_step_that_answered_an_error(self):
        self.assertUnsaved(_recover(None, queueJob=self.entry(JobStatus.FINISHED, self.failedResponse())))

    def test_a_first_step_that_raised(self):
        self.assertUnsaved(_recover(None, queueJob=self.entry(JobStatus.FAILED)))

    def test_a_first_step_that_finished_and_was_removed_later_keeps_the_note(self):
        done = Response().setContent({"success": True})
        response = _recover(None, queueJob=self.entry(JobStatus.FINISHED, done))
        self.assertUnavailable(response, "not found at database", RETENTION)

    def test_another_step_that_failed_keeps_the_note(self):
        """A Step 2 entry means Step 1 saved the job, so no document means it was removed."""
        response = _recover(None, queueJob=self.entry(JobStatus.FINISHED, self.failedResponse(),
                                                      fn=servlet.pathwayAcquisitionStep2_PART2))
        self.assertUnavailable(response, "not found at database", RETENTION)

    def test_no_entry_keeps_the_note(self):
        self.assertUnavailable(_recover(None), "not found at database", RETENTION)

    def test_a_saved_job_is_opened_whatever_its_first_run_did(self):
        from src.tests.test_unavailable_job_is_not_an_error import _job
        content = _recover(_job(lastStep=2), queueJob=self.entry(JobStatus.FAILED)).content
        self.assertIs(content["success"], True)

    def test_the_id_is_escaped(self):
        text = _recover(None, jobID=HOSTILE_ID, queueJob=self.entry(JobStatus.FAILED)).content["errorMessage"]
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img src=x", text)

    def test_the_real_first_step_refusing_its_files(self):
        """The real queue runs the real Step 1, whose files are refused."""
        queue = Queue()
        queue.start_worker(1)
        jobInstance = mock.MagicMock()
        jobInstance.getJobID.return_value = JOB_ID
        jobInstance.validateInput.side_effect = InputFileError("Errors detected in input files.")
        with mock.patch.object(servlet, "JobProgress"):
            queue.enqueue(fn=servlet.pathwayAcquisitionStep1_PART2,
                          args=(jobInstance, "17", False, Response()), job_id=JOB_ID)
            deadline = time.monotonic() + 30
            while queue.check_status(JOB_ID) not in (JobStatus.FINISHED, JobStatus.FAILED):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
        jobInstance.processFilesContent.assert_not_called()
        self.assertUnsaved(_recover(None, queue=queue))


if __name__ == "__main__":
    unittest.main(verbosity=2)
