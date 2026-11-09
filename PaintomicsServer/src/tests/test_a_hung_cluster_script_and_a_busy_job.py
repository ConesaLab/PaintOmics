#!/usr/bin/env python3
"""A hung cluster script is stopped at its limit, and a request on a busy job does not wait forever.

Why this exists
---------------
Since #242 a Step 3 cluster slider request ("Generate N clusters", then Apply)
and Step 2 wait for the job's lock (JobLocks) while another request rewrites
the job, and each waits on one of the queue's 4 workers. generateMetaGenes.R
ran with no time limit. One R run that hung kept its request, and the job's
lock, for good, and every later slider move or Step 2 of that job took one
more worker to wait for it: a few clicks could hold all 4 workers and stop
every job on the server until it was restarted.

Now:
* the R scripts of one generateMetagenesList call share one time limit,
  MAX_WAIT_THREADS (900 s). A script still running then is killed with every
  process it started (SIGKILL to its process group), and the request fails
  with a message that names the omic and database, says nothing was saved,
  and asks for a report;
* a request waits for the job's lock at most JobLocks.WAIT_LIMIT (60 s). Then
  it changes nothing and answers that the job is still busy with an earlier
  request, and to try again in a moment (JobBusyError), which the browser
  shows as a warning with no Report button.

What this file pins
-------------------
* HungScriptTest puts a stand-in "Rscript" first on PATH: a shell script that
  ends at once, or for one omic sleeps, with a sleeping child of its own, as R
  can start processes. Through the real slider servlet and the real
  generateMetagenesList, with a 1 second limit: the request fails within the
  limit with the message, both processes are gone, nothing is stored, the
  job's clusters and pictures are as they were, no work folder or lock is
  left, and the next request works. The same from a thread that blocks every
  signal, as uWSGI's threads do (a SIGTERM would wait, pending). Step 2's
  metagenes with two omics: the other omic's scripts finish, and the
  message names the omic and both of its databases, the second never started.
  A script that ends in time, or fails on its own, is handled as before.
* BusyJobTest: a slider request, or Step 2, on a job that another slider
  request holds answers JobBusyError's message within the wait limit, runs
  nothing, and leaves the holder its lock; one held by Step 2 names Step 2;
  a request that waits less than the limit runs as before; and the answer
  reaches the page through the real queue and status poll route.
* TheDialogTest: the real ajaxErrorHandler, under node, shows the busy answer
  as "This job is busy", a warning with no Report button.

On master every test here fails except the three that check a script that
ends in time, one that fails on its own, and a short wait (the controls).

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_hung_cluster_script_and_a_busy_job
"""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests.test_cluster_slider_keeps_other_pictures import (  # noqa: E402
    JOB_ID, _FakeScript, _Job)
from src.tests.test_cluster_slider_requests_one_at_a_time import (  # noqa: E402
    OMIC_A, OMIC_B, ROOT_DIRECTORY, _Store, _clusters, _response, _servlet, _standInJob)
from src.classes.JobInstances import PathwayAcquisitionJob as jobModule  # noqa: E402
from src.common import JobLocks  # noqa: E402

LIMIT = 1          # seconds, for the scripts and for the wait
SLACK = 8          # what a run may take beyond its limit before this calls it unbounded

# Written out here, not built with the code under test.
HUNG_TAIL = (", so it was stopped. Nothing was saved. For a whole job it usually takes "
             "under a minute, so something on the server most likely held it up. "
             "Please report this error.")
HUNG_SLIDER = ("Computing the clusters did not finish within its limit of 1 second for omic "
               "'RNA 100%' (KEGG)" + HUNG_TAIL)
HUNG_STEP2 = ("Computing the clusters did not finish within its limit of 1 second for omic "
              "'RNA 100%' (KEGG, Reactome)" + HUNG_TAIL)


def _busy(doing):
    return ("Job JOB1 is still busy %s for an earlier request, and this request waited 1 second "
            "for it to finish. Nothing was changed. Please try again in a moment." % doing)


# Started with the --arguments generateMetagenesList passes. Writes one line
# per run to $STANDIN_LOG ("<pid> <omic>|<database>"), and the pid of any
# child it starts to $STANDIN_PIDS. For the omic named in $STANDIN_HANG it
# starts a child that sleeps and waits for it; otherwise it prints a line and
# exits with $STANDIN_EXIT (0 when unset).
STAND_IN = r"""#!/bin/sh
prefix=
database=KEGG
for argument in "$@"; do
    case "$argument" in
        --output_prefix=*) prefix="${argument#--output_prefix=}" ;;
        --database=*) database="${argument#--database=}" ;;
    esac
done
echo "$$ $prefix|$database" >> "$STANDIN_LOG"
if [ -n "$STANDIN_HANG" ] && [ "$prefix" = "$STANDIN_HANG" ]; then
    sleep 300 &
    echo "$!" >> "$STANDIN_PIDS"
    wait
fi
echo "stand-in for $prefix"
exit "${STANDIN_EXIT:-0}"
"""


def _running(pid):
    """Whether the process is there and has not ended (a zombie has ended:
    it only waits for its parent, or init, to collect it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open("/proc/%d/stat" % pid) as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")


# Calls _within gave up on (on a tree with no limit), for _drain.
_stillRunning = []


def _drain():
    """Wait for the calls _within gave up on, once the test has let go of what
    they wait for and before its patches go: one still waiting would otherwise
    go on, under the next test, with the real JobInformationManager."""
    while _stillRunning:
        _stillRunning.pop().join(30)


def _within(call, seconds, blockSignals=False):
    """Run `call` in a thread of its own; its result and how long it took, or
    an AssertionError when it is still running after `seconds`. With
    `blockSignals` the thread blocks every signal first, as uWSGI's do."""
    box = {}

    def run():
        if blockSignals:
            signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
        began = time.monotonic()
        try:
            box["result"] = call()
        except BaseException as ex:  # noqa: BLE001 - reported below
            box["error"] = ex
        box["elapsed"] = time.monotonic() - began

    # Daemon: on a tree with no limit the call never returns.
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        _stillRunning.append(thread)
        raise AssertionError("still running after %s s: nothing bounds it" % seconds)
    if "error" in box:
        raise box["error"]
    return box["result"], box["elapsed"]


class HungScriptTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="paintomics-hung-script-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        script = os.path.join(self.folder, "Rscript")
        with open(script, "w") as handle:
            handle.write(STAND_IN)
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.log = os.path.join(self.folder, "runs.log")
        self.pids = os.path.join(self.folder, "children.pids")
        for path in (self.log, self.pids):
            open(path, "w").close()
        self.environment = {"PATH": self.folder + os.pathsep + os.environ.get("PATH", ""),
                            "STANDIN_LOG": self.log, "STANDIN_PIDS": self.pids,
                            "STANDIN_HANG": "", "STANDIN_EXIT": "0"}

        self.run_ = _Job([OMIC_A, OMIC_B], ("KEGG", "Reactome"))
        self.addCleanup(self.run_.close)
        # Step 2 drew four clusters for every omic and database.
        with mock.patch.object(jobModule, "_runMetagenesScripts", _FakeScript(self.run_.job)):
            self.run_.metagenes({})
        self.assertEqual(_clusters(self.run_.job, OMIC_A, "KEGG"), ["1", "2", "3", "4"])
        self.store = _Store(self.run_.job, shared=True)
        for patcher in (mock.patch.dict(os.environ, self.environment),
                        mock.patch.object(jobModule, "MAX_WAIT_THREADS", LIMIT),
                        mock.patch.object(jobModule, "JobProgress", mock.MagicMock()),
                        mock.patch.object(_servlet(), "JobInformationManager", self.store)):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Registered last, so it runs first: whatever a tree left running goes
        # before the patches and the job's folders do.
        self.addCleanup(self.killEverything)

    def killEverything(self):
        for pid in self.processes():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        _drain()

    def runs(self):
        """(omic, database) of every script started, in order."""
        with open(self.log) as handle:
            return [tuple(line.split(" ", 1)[1].rstrip("\n").split("|")) for line in handle if line.strip()]

    def processes(self):
        """Every stand-in started, and every child one started."""
        found = []
        for path in (self.log, self.pids):
            if os.path.exists(path):
                with open(path) as handle:
                    found += [int(line.split()[0]) for line in handle if line.strip()]
        return found

    def assertAllGone(self):
        processes = self.processes()
        self.assertTrue(processes)
        deadline = time.monotonic() + 5
        while any(_running(pid) for pid in processes) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([pid for pid in processes if _running(pid)], [],
                         "processes of the stopped script still running")

    def slider(self, omicName, clusters=2):
        servlet = _servlet()
        return lambda: servlet.pathwayAcquisitionMetagenes_PART2(
            ROOT_DIRECTORY, "u1", self.run_.job, omicName, clusters, "KEGG", _response())

    def assertNoWorkFolderLeft(self):
        tmp = os.path.dirname(self.run_.job.getTemporalDir().rstrip("/"))
        left = sorted(name for name in (os.listdir(tmp) if os.path.isdir(tmp) else []) if name != JOB_ID)
        self.assertEqual(left, [], "work folders left behind")

    def assertStoppedCleanly(self, blockSignals=False):
        os.environ["STANDIN_HANG"] = OMIC_A
        before = self.run_.pictures()
        response, elapsed = _within(self.slider(OMIC_A), LIMIT + SLACK + 12, blockSignals)
        self.assertLess(elapsed, LIMIT + SLACK, "the script was not stopped at its limit")
        content = response.content
        self.assertIs(content["success"], False)
        self.assertIs(content["extra"]["input_error"], False)       # ours: Report error
        self.assertIs(content["extra"].get("job_busy"), False)
        self.assertIn("ERROR MESSAGE: " + HUNG_SLIDER, content["message"])
        self.assertAllGone()
        self.assertEqual(self.store.stored, [], "a request stopped at its limit stored the job")
        self.assertEqual(_clusters(self.run_.job, OMIC_A, "KEGG"), ["1", "2", "3", "4"])
        self.assertEqual(self.run_.pictures(), before)
        self.assertNoWorkFolderLeft()
        self.assertEqual(JobLocks.jobsHeld(), [])

        # The job is not left stuck: the next request on it runs.
        os.environ["STANDIN_HANG"] = ""
        response, _ = _within(self.slider(OMIC_B), SLACK)
        self.assertIs(response.content["success"], True, str(response.content)[:400])
        self.assertEqual(len(self.store.stored), 1)

    def test_a_script_past_its_limit_is_stopped_and_the_request_fails_cleanly(self):
        self.assertStoppedCleanly()

    def test_a_script_started_from_a_thread_that_blocks_every_signal_is_still_stopped(self):
        self.assertStoppedCleanly(blockSignals=True)

    def test_step2_names_what_did_not_finish_and_lets_the_other_omic_finish(self):
        os.environ["STANDIN_HANG"] = OMIC_A

        def step2():
            try:
                self.run_.metagenes({})
            except RuntimeError as ex:
                return str(ex)
            return None

        message, elapsed = _within(step2, LIMIT + SLACK + 12)
        self.assertLess(elapsed, LIMIT + SLACK)
        self.assertEqual(message, HUNG_STEP2)
        # The other omic ran both of its scripts; this one's second database
        # was never started, its time being up.
        self.assertEqual(sorted(self.runs()), sorted([(OMIC_A, "KEGG"), (OMIC_B, "KEGG"), (OMIC_B, "Reactome")]))
        self.assertAllGone()
        self.assertEqual(_clusters(self.run_.job, OMIC_A, "KEGG"), ["1", "2", "3", "4"])

    def test_a_script_that_ends_in_time_is_unaffected(self):
        """The control: on master too."""
        response, _ = _within(self.slider(OMIC_A), SLACK + 12)
        self.assertIs(response.content["success"], True, str(response.content)[:400])
        self.assertEqual(self.runs(), [(OMIC_A, "KEGG")])
        self.assertEqual(len(self.store.stored), 1)

    def test_a_script_that_fails_on_its_own_says_so_as_before(self):
        """The control: on master too."""
        os.environ["STANDIN_EXIT"] = "1"
        response, _ = _within(self.slider(OMIC_A), SLACK + 12)
        self.assertIs(response.content["success"], False)
        self.assertIn("Metagenes generation failed for omic 'RNA 100%' and database 'KEGG'. "
                      "Details: stand-in for RNA 100%", response.content["message"])
        self.assertEqual(self.store.stored, [])


class _Holding(object):
    """A request that holds the job's lock until told to let go: generateMetagenesList
    waits inside the lock."""

    def __init__(self):
        self.inside = threading.Event()
        self.letGo = threading.Event()
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.inside.set()
            self.letGo.wait(30)


class BusyJobTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="paintomics-busy-job-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.holding = _Holding()
        self.job = _standInJob("JOB1", self.holding, self.folder)
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.side_effect = lambda jobID: self.job
        self.progress = mock.MagicMock()
        servlet = _servlet()
        # create=True: master has no WAIT_LIMIT, and waits with no limit.
        for patcher in (mock.patch.object(servlet, "JobInformationManager", manager),
                        mock.patch.object(servlet, "JobProgress", self.progress),
                        mock.patch.object(JobLocks, "WAIT_LIMIT", LIMIT, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Last, so it runs first: let the holder go, then wait for any request
        # still waiting for it, before the patches go.
        self.addCleanup(_drain)
        self.addCleanup(self.holding.letGo.set)

    def slider(self, omicName=OMIC_A):
        servlet = _servlet()
        return lambda: servlet.pathwayAcquisitionMetagenes_PART2(
            "/nowhere/", "u1", self.job, omicName, 2, "KEGG", _response())

    def step2(self):
        servlet = _servlet()
        return lambda: servlet.pathwayAcquisitionStep2_PART2("JOB1", "u1", [], {}, _response(), "/nowhere/", {})

    def hold(self, request):
        """Start `request`, and return once it holds the job (inside
        generateMetagenesList). Its result arrives in the returned box."""
        box = {}
        thread = threading.Thread(target=lambda: box.setdefault("result", request()), daemon=True)
        thread.start()
        self.assertTrue(self.holding.inside.wait(10), "the first request never got the job")
        box["thread"] = thread
        return box

    def finish(self, box):
        self.holding.letGo.set()
        box["thread"].join(10)
        self.assertFalse(box["thread"].is_alive())
        self.assertIs(box["result"].content.get("success"), True, str(box["result"].content)[:400])

    def assertBusy(self, response, elapsed, doing):
        self.assertGreaterEqual(elapsed, LIMIT * 0.9, "it did not wait for the job at all")
        self.assertLess(elapsed, LIMIT + SLACK)
        content = response.content
        self.assertIs(content["success"], False)
        self.assertIs(content["extra"].get("job_busy"), True)
        self.assertIs(content["extra"]["input_error"], False)
        self.assertIs(content["extra"]["job_unavailable"], False)
        self.assertIn("ERROR MESSAGE: " + _busy(doing), content["message"])

    def test_a_second_slider_request_answers_busy_within_the_wait_limit(self):
        box = self.hold(self.slider(OMIC_A))
        response, elapsed = _within(self.slider(OMIC_B), LIMIT + SLACK)
        self.assertBusy(response, elapsed, "making new clusters")
        self.assertEqual(self.holding.calls, 1, "the request that gave up ran anyway")
        self.assertEqual(JobLocks.jobsHeld(), ["JOB1"], "the holder lost its lock")
        self.finish(box)
        self.assertEqual(JobLocks.jobsHeld(), [])

    def test_step2_answers_busy_within_the_wait_limit_and_changes_nothing(self):
        box = self.hold(self.slider(OMIC_A))
        response, elapsed = _within(self.step2(), LIMIT + SLACK)
        self.assertBusy(response, elapsed, "making new clusters")
        self.job.generatePathwaysList.assert_not_called()
        self.job.cleanDirectories.assert_not_called()
        self.progress.finish.assert_called_with("JOB1")
        self.assertEqual(JobLocks.jobsHeld(), ["JOB1"], "Step 2 let go of a lock it never took")
        self.finish(box)
        self.assertEqual(JobLocks.jobsHeld(), [])
        # The job takes requests again.
        response, _ = _within(self.slider(OMIC_B), SLACK)
        self.assertIs(response.content["success"], True)

    def test_a_slider_request_behind_step2_names_step2(self):
        box = self.hold(self.step2())
        response, elapsed = _within(self.slider(OMIC_B), LIMIT + SLACK)
        self.assertBusy(response, elapsed, "running Step 2")
        self.finish(box)

    def test_a_request_that_waits_less_than_the_limit_runs(self):
        """The control: on master too."""
        box = self.hold(self.slider(OMIC_A))
        threading.Timer(LIMIT / 4.0, self.holding.letGo.set).start()
        response, _ = _within(self.slider(OMIC_B), LIMIT + SLACK)
        self.assertIs(response.content["success"], True, str(response.content)[:400])
        self.assertEqual(self.holding.calls, 2)
        self.finish(box)

    def test_the_answer_reaches_the_page_through_the_queue(self):
        """Step 2 on the real queue, polled through the real /check_job_status
        route, as the Step 2 page polls it, while another request holds the job."""
        from src import paintomicsserver as server
        from src.conf.serverconf import SERVER_SUBDOMAIN
        with mock.patch.object(server, "configureLogging"), \
                mock.patch.object(server, "KeggInformationManager"), \
                mock.patch.object(server, "JobInformationManager"), \
                mock.patch.object(server.Application, "ensureIndexes"), \
                mock.patch.object(server.Application, "startScheludeTasks"):
            application = server.Application()
        box = self.hold(self.slider(OMIC_A))
        try:
            application.queue.enqueue(fn=_servlet().pathwayAcquisitionStep2_PART2,
                                      args=("JOB1", "u1", [], {}, _response(), "/nowhere/", {}),
                                      job_id="JOB1")
            client = application.app.test_client()
            url = SERVER_SUBDOMAIN + "/check_job_status/JOB1"
            deadline = time.monotonic() + LIMIT + SLACK
            answer = client.post(url)
            while answer.status_code == 200 and time.monotonic() < deadline:
                time.sleep(0.05)
                answer = client.post(url)
            self.assertEqual(answer.status_code, 400, "Step 2 was still waiting for the job")
            body = json.loads(answer.get_data(as_text=True))
            self.assertIs(body["extra"]["job_busy"], True)
            self.assertIn(_busy("making new clusters"), body["message"])
        finally:
            self.finish(box)


class JobLocksTest(unittest.TestCase):

    def test_a_request_that_gives_up_holds_nothing(self):
        from src.common.ServerErrorManager import JobBusyError
        holding, letGo = threading.Event(), threading.Event()

        def holder():
            with JobLocks.heldFor("JOBX", "making new clusters"):
                holding.set()
                letGo.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(holding.wait(10))
            with self.assertRaises(JobBusyError) as caught:
                JobLocks.acquire("JOBX", "running Step 2", waitLimit=0.2)
            self.assertIn("busy making new clusters", str(caught.exception))
            self.assertIn("waited 0.2 seconds", str(caught.exception))
        finally:
            letGo.set()
            thread.join(10)
        self.assertEqual(JobLocks.jobsHeld(), [])

    def test_what_the_holder_does_is_kept_while_it_takes_the_lock_again(self):
        from src.common.ServerErrorManager import JobBusyError
        holding, letGo = threading.Event(), threading.Event()

        def holder():
            with JobLocks.heldFor("JOBX", "running Step 2"):
                with JobLocks.heldFor("JOBX", "making new clusters"):
                    pass
                holding.set()
                letGo.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(holding.wait(10))
            with self.assertRaises(JobBusyError) as caught:
                JobLocks.acquire("JOBX", waitLimit=0.1)
            self.assertIn("busy running Step 2", str(caught.exception))
        finally:
            letGo.set()
            thread.join(10)
        self.assertEqual(JobLocks.jobsHeld(), [])

    def test_an_id_that_is_not_plain_is_not_repeated(self):
        self.assertTrue(JobLocks.busyMessage("<b>x</b>", None, 60).startswith(
            "This job is still busy with an earlier request, and this request waited 60 seconds"))

    def test_only_a_busy_job_is_flagged(self):
        from src.common.ServerErrorManager import InputFileError, JobBusyError, JobUnavailableError
        from src.tests.test_an_invalid_session_is_told_by_its_own_answer import _answerTo
        self.assertIs(_answerTo(JobBusyError("busy"))["extra"]["job_busy"], True)
        for other in (ValueError("broken"), InputFileError("bad file"), JobUnavailableError("gone")):
            with self.subTest(exception=type(other).__name__):
                self.assertIs(_answerTo(other)["extra"]["job_busy"], False)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheDialogTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from src.tests.test_an_invalid_session_is_told_by_its_own_answer import _answerTo, runDialogs
        test = BusyJobTest("test_a_second_slider_request_answers_busy_within_the_wait_limit")
        test.setUp()
        try:
            box = test.hold(test.slider(OMIC_A))
            busy, _ = _within(test.slider(OMIC_B), LIMIT + SLACK)
            test.finish(box)
        finally:
            test.doCleanups()
        cls.results = runDialogs({"busy": busy.content,
                                  "crash": _answerTo(ValueError("The table is broken."))})

    def test_it_is_a_warning_that_says_the_job_is_busy(self):
        dialog = self.results["busy"]
        self.assertIn("This job is busy", dialog["title"])
        self.assertNotIn("Oops", dialog["title"])
        self.assertEqual(dialog["kind"], "warningDialog")

    def test_it_offers_no_report_and_can_be_closed(self):
        dialog = self.results["busy"]
        self.assertEqual(dialog["report"], "none")
        self.assertEqual(dialog["close"], "inline-block")

    def test_it_says_what_is_true_and_nothing_that_contradicts_it(self):
        body = self.results["busy"]["body"]
        self.assertIn(_busy("making new clusters"), body)
        self.assertNotIn("retrying in the browser", body)
        self.assertNotIn("JobBusyError", body)
        self.assertNotIn("User ID", body)

    def test_a_crash_is_still_an_error_with_report(self):
        dialog = self.results["crash"]
        self.assertIn("Oops..Internal error!", dialog["title"])
        self.assertEqual(dialog["report"], "inline-block")


if __name__ == "__main__":
    unittest.main(verbosity=2)
