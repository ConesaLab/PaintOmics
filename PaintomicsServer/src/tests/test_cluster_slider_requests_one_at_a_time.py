#!/usr/bin/env python3
"""Two Step 3 cluster slider requests on one job no longer run over each other.

The defect
----------
The Step 3 slider ("Generate N clusters", then Apply) is queued under a new id
for every request (paintomicsserver: /pa_get_clusters passes
generateRandomID()), so the queue's one-step-per-job rule, which keeps two
Step 2s of one job apart (test_one_step_per_job_at_a_time), did not cover it,
and the queue's 4 workers ran two requests on one job at once:

* both worked in the job's one temporary folder, and the first to finish
  removed it (cleanDirectories) while the other's R script was still running.
  That script then had nowhere to write; the request failed and its clusters
  were lost;
* both changed and stored the job's pathways. A request whose copy of the job
  was older than what the other had stored (the job left the cache between
  the two) stored that copy over it.

A slider request and a Step 2 ("Go back", then Next) of one job could run
together the same way.

What this file pins
-------------------
* RealJobCodeTest runs the real slider servlet (pathwayAcquisitionMetagenes_PART2)
  and the real generateMetagenesList twice on one job with two omics, a
  stand-in for the R script that reads its input when it starts and writes
  when it ends, as generateMetaGenes.R does. The first request's script waits
  until the second request has finished, if the code lets it finish (on master
  it does, and removes the folder the first one is writing to). Both requests
  must succeed, the pathways stored last must carry both new cluster counts,
  both omics' new pictures must be there, and no work folder may be left.
  Then two requests whose copies of the job both predate either store: the
  second must keep what the first stored. And a request leaves the job's own
  temporary folder, where a Step 2 works, alone.
* OneJobAtATimeTest counts how many requests are inside generateMetagenesList
  at once, with stand-in jobs: two slider requests on one job, a slider
  request and a Step 2 of one job (1 at a time), and two jobs (2 at a time:
  the rule is per job, not one for the server). A request's answer is built
  before the next request on the job starts. A request that fails, or a
  Step 2 whose clean-up raises, still lets go of the job.
* JobLocksTest checks the lock table on its own.

On master 10 of the 11 tests fail; the two-jobs control passes.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_cluster_slider_requests_one_at_a_time
"""
import copy
import logging
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests.test_cluster_slider_keeps_other_pictures import (  # noqa: E402
    JOB_ID, _FakeScript, _Job, thumbnail_name, thumbnails_of)
from src.classes.JobInstances import PathwayAcquisitionJob as jobModule  # noqa: E402

# How long a stand-in waits for a second request that the code under test may
# never let in. On master the second request finishes in milliseconds; on the
# branch it is waiting for the lock, so this is what each such test costs.
WAIT = 2.0

ROOT_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep

OMIC_A = "RNA 100%"
OMIC_B = "Prot B"


def _servlet():
    """Imported late: the servlet module reads serverconf on import."""
    from src.servlets import PathwayAcquisitionServlet
    return PathwayAcquisitionServlet


def _response():
    from src.paintomicsserver import Response
    return Response()


def _runTogether(*calls):
    """Start every call at the same moment, each in its own thread, and return
    their results in order."""
    ready = threading.Barrier(len(calls))
    results = [None] * len(calls)
    errors = []

    def run(index, call):
        ready.wait()
        try:
            results[index] = call()
        except BaseException as ex:  # noqa: BLE001 - reported below
            errors.append(ex)

    # Daemon threads, so a request that never gets the job (a lock left held)
    # fails this test instead of keeping the process alive after it.
    threads = [threading.Thread(target=run, args=(i, call), daemon=True) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    if any(thread.is_alive() for thread in threads):
        raise AssertionError("a request did not finish within 30 s: it is waiting for a job no one releases")
    if errors:
        raise errors[0]
    return results


class _ScriptThatTakesItsTime(_FakeScript):
    """generateMetaGenes.R's timing: it reads --input_file when it starts and
    writes into --data_dir when it ends. The first call waits, before writing,
    until some request has finished (or WAIT has passed); a script whose
    folder went meanwhile fails the way R does on a write to a missing folder."""

    def __init__(self, job):
        super().__init__(job)
        self.lock = threading.Lock()
        self.started = 0
        self.someoneFinished = threading.Event()

    def __call__(self, omicNames, databases, commands):
        with self.lock:
            self.started += 1
            first = self.started == 1
        dataDirs = set()
        for (_omic, _database), (argv, _k) in commands.items():
            args = dict(item[2:].split("=", 1) for item in argv if item.startswith("--") and "=" in item)
            with open(args["input_file"]) as handle:     # R reads its input first
                handle.read()
            dataDirs.add(args["data_dir"])
        if first:
            self.someoneFinished.wait(WAIT)
        if not all(os.path.isdir(folder) for folder in dataDirs):
            return {pair: (1, b"Error in file(file, ifelse(append, \"a\", \"w\")) : cannot open the connection\n"
                              b"cannot open file: No such file or directory")
                    for pair in commands}
        return super().__call__(omicNames, databases, commands)


class _Store(object):
    """Stands in for JobInformationManager: the job MongoDB holds, and what the
    requests stored. `shared` hands every caller the one cached object, as
    loadJobInstance does while the job is in the cache; otherwise each caller
    gets its own copy of what was stored last, as after the job left it."""

    def __init__(self, job, shared):
        self.shared = shared
        self.job = job
        self.stored = []
        self.lock = threading.Lock()

    def __call__(self):
        return self

    def loadJobInstance(self, jobID):
        assert jobID == JOB_ID, jobID
        with self.lock:
            return self.job if self.shared else copy.deepcopy(self.job)

    def storePathways(self, jobInstance):
        with self.lock:
            snapshot = copy.deepcopy(jobInstance)
            self.stored.append(snapshot)
            if not self.shared:
                self.job = snapshot


def _clusters(job, omicName, source):
    return sorted({metagene["cluster"]
                   for pathway in job.matchedPathways.values() if pathway.getSource() == source
                   for metagene in pathway.metagenes.get(omicName, [])}, key=int)


def _answerClusters(response, omicName, source):
    return sorted({str(metagene["cluster"])
                   for pathway in response.content["pathwaysInfo"] if pathway.get("source") == source
                   for metagene in (pathway.get("metagenes") or {}).get(omicName, [])}, key=int)


class RealJobCodeTest(unittest.TestCase):

    def setUp(self):
        self.run_ = _Job([OMIC_A, OMIC_B], ("KEGG", "Reactome"))
        self.addCleanup(self.run_.close)
        # Step 2: every omic and database, the cluster count left to the script.
        with mock.patch.object(jobModule, "_runMetagenesScripts", _FakeScript(self.run_.job)):
            self.run_.metagenes({})
        self.assertEqual(_clusters(self.run_.job, OMIC_A, "KEGG"), ["1", "2", "3", "4"])
        self.script = _ScriptThatTakesItsTime(self.run_.job)
        patchers = [mock.patch.object(jobModule, "_runMetagenesScripts", self.script),
                    mock.patch.object(jobModule, "JobProgress", mock.MagicMock())]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def useStore(self, store):
        """Patched once, here, not by each request's thread: a patch is a
        module global, and one thread undoing it would pull it from under the
        other."""
        patcher = mock.patch.object(_servlet(), "JobInformationManager", store)
        patcher.start()
        self.addCleanup(patcher.stop)
        return store

    def slider(self, jobInstance, omicName, clusters):
        servlet = _servlet()

        def request():
            try:
                return servlet.pathwayAcquisitionMetagenes_PART2(
                    ROOT_DIRECTORY, "u1", jobInstance, omicName, clusters, "KEGG", _response())
            finally:
                self.script.someoneFinished.set()
        return request

    def assertSucceeded(self, response, what):
        self.assertIs(response.content.get("success"), True,
                      "%s failed: %s" % (what, str(response.content.get("message"))[:400]))

    def assertNoWorkFolderLeft(self):
        tmp = os.path.dirname(self.run_.job.getTemporalDir().rstrip("/"))
        left = sorted(name for name in (os.listdir(tmp) if os.path.isdir(tmp) else [])
                      if name != JOB_ID)
        self.assertEqual(left, [], "work folders left behind")

    def test_two_requests_at_once_both_succeed_and_both_are_kept(self):
        store = self.useStore(_Store(self.run_.job, shared=True))
        answerA, answerB = _runTogether(self.slider(self.run_.job, OMIC_A, 2),
                                        self.slider(self.run_.job, OMIC_B, 3))
        self.assertSucceeded(answerA, "the request for %r" % OMIC_A)
        self.assertSucceeded(answerB, "the request for %r" % OMIC_B)

        last = store.stored[-1]
        self.assertEqual(_clusters(last, OMIC_A, "KEGG"), ["1", "2"])
        self.assertEqual(_clusters(last, OMIC_B, "KEGG"), ["1", "2", "3"])
        # Reactome was not asked for: Step 2's four clusters stay.
        self.assertEqual(_clusters(last, OMIC_A, "Reactome"), ["1", "2", "3", "4"])

        # The page shows the answer that arrives last: the request that ran
        # second answers with both new counts.
        both = [answer for answer in (answerA, answerB)
                if _answerClusters(answer, OMIC_A, "KEGG") == ["1", "2"]
                and _answerClusters(answer, OMIC_B, "KEGG") == ["1", "2", "3"]]
        self.assertTrue(both, "no answer carries both new cluster counts")

        pictures = self.run_.pictures()
        self.assertEqual(thumbnails_of(pictures, OMIC_A, "KEGG"),
                         sorted(thumbnail_name(OMIC_A, k, "KEGG") for k in (1, 2)))
        self.assertEqual(thumbnails_of(pictures, OMIC_B, "KEGG"),
                         sorted(thumbnail_name(OMIC_B, k, "KEGG") for k in (1, 2, 3)))
        self.assertNoWorkFolderLeft()

    def test_a_request_keeps_what_the_request_before_it_stored(self):
        """Both requests' copies of the job were loaded before either ran (the
        job had left the cache): the second must still keep the first's."""
        store = self.useStore(_Store(copy.deepcopy(self.run_.job), shared=False))
        copyA, copyB = copy.deepcopy(self.run_.job), copy.deepcopy(self.run_.job)
        self.script.someoneFinished.set()      # nothing to wait for: one after the other
        answerA = self.slider(copyA, OMIC_A, 2)()
        answerB = self.slider(copyB, OMIC_B, 3)()
        self.assertSucceeded(answerA, "the request for %r" % OMIC_A)
        self.assertSucceeded(answerB, "the request for %r" % OMIC_B)

        last = store.stored[-1]
        self.assertEqual(_clusters(last, OMIC_B, "KEGG"), ["1", "2", "3"])
        self.assertEqual(_clusters(last, OMIC_A, "KEGG"), ["1", "2"],
                         "the second request stored its older copy over the first's clusters")
        self.assertEqual(_answerClusters(answerB, OMIC_A, "KEGG"), ["1", "2"])

    def test_the_request_leaves_the_jobs_temporary_folder_alone(self):
        """A Step 2 of the job works in it; the slider has a folder of its own."""
        temporal = self.run_.job.getTemporalDir()
        os.makedirs(temporal, exist_ok=True)
        marker = os.path.join(temporal, "step2-was-here.txt")
        with open(marker, "w") as handle:
            handle.write("x")
        self.script.someoneFinished.set()
        self.useStore(_Store(self.run_.job, shared=True))
        answer = self.slider(self.run_.job, OMIC_A, 2)()
        self.assertSucceeded(answer, "the request")
        self.assertTrue(os.path.exists(marker), "the slider removed the job's temporary folder")


class _Overlap(object):
    """Stands in for generateMetagenesList and counts how many calls are
    inside it at once. The first call waits for a second to come in (or WAIT),
    so code that lets two in together shows 2 every time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.inside = 0
        self.most = 0
        self.second = threading.Event()

    def __call__(self, *args, **kwargs):
        with self.lock:
            self.calls += 1
            first = self.calls == 1
            self.inside += 1
            self.most = max(self.most, self.inside)
            if not first:
                self.second.set()
        try:
            if first:
                self.second.wait(WAIT)
        finally:
            with self.lock:
                self.inside -= 1


def _standInJob(jobID, overlap, folder):
    job = mock.MagicMock()
    job.getJobID.return_value = jobID
    job.getTemporalDir.return_value = os.path.join(folder, "tmp", jobID)
    job.getOutputDir.return_value = os.path.join(folder, "jobsData", jobID, "output") + os.sep
    job.generateMetagenesList.side_effect = overlap
    job.getMatchedPathways.return_value = {}
    job.getMatchedClass.return_value = {}
    job.explainEmptyMapping.return_value = None
    job.generatePathwaysList.return_value = {}
    job.getGlobalExpressionData.return_value = {}
    job.getGeneBasedInputOmics.return_value = []
    job.getCompoundBasedInputOmics.return_value = []
    job.getOrganism.return_value = "mmu"
    job.getDatabases.return_value = ["KEGG"]
    job.getValueIdTable.return_value = {}
    job.getAIConsent.return_value = False
    job.getExperimentDesign.return_value = ""
    job.conditionNames = []
    job.regulationPerConditionData = None
    return job


class OneJobAtATimeTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="paintomics-one-at-a-time-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.overlap = _Overlap()
        self.jobs = {jobID: _standInJob(jobID, self.overlap, self.folder) for jobID in ("JOB1", "JOB2")}
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.side_effect = lambda jobID: self.jobs[jobID]
        servlet = _servlet()
        for name, value in (("JobInformationManager", manager), ("JobProgress", mock.MagicMock())):
            patcher = mock.patch.object(servlet, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def slider(self, jobID, omicName):
        servlet = _servlet()
        return lambda: servlet.pathwayAcquisitionMetagenes_PART2(
            "/nowhere/", "u1", self.jobs[jobID], omicName, 2, "KEGG", _response())

    def step2(self, jobID):
        servlet = _servlet()
        return lambda: servlet.pathwayAcquisitionStep2_PART2(
            jobID, "u1", [], {}, _response(), "/nowhere/", {})

    def assertAllSucceeded(self, responses):
        for response in responses:
            self.assertIs(response.content.get("success"), True, str(response.content)[:400])
        self.assertEqual(self.overlap.calls, len(responses))

    def test_two_slider_requests_on_one_job(self):
        responses = _runTogether(self.slider("JOB1", OMIC_A), self.slider("JOB1", OMIC_B))
        self.assertAllSucceeded(responses)
        self.assertEqual(self.overlap.most, 1, "two slider requests ran on one job at once")

    def test_a_slider_request_and_step2_of_one_job(self):
        responses = _runTogether(self.slider("JOB1", OMIC_A), self.step2("JOB1"))
        self.assertAllSucceeded(responses)
        self.assertEqual(self.overlap.most, 1, "a slider request and Step 2 ran on one job at once")

    def test_two_jobs_still_run_together(self):
        """The control: the rule is one request per job, not one per server,
        and the stand-in does see two at once when they are allowed."""
        responses = _runTogether(self.slider("JOB1", OMIC_A), self.slider("JOB2", OMIC_A))
        self.assertAllSucceeded(responses)
        self.assertEqual(self.overlap.most, 2, "requests on two different jobs waited for each other")

    def test_the_answer_is_built_before_the_next_request_starts(self):
        """A request answers with the pathways as it stored them, not as the
        next request, already changing the shared job, has half redone them.
        The first answer waits (up to WAIT) for a second request to be inside
        generateMetagenesList; the second stays inside until that answer is
        built. Code that answers outside the lock lets the second one in."""
        secondInside, answerBuilt = threading.Event(), threading.Event()
        calls = []

        def recompute(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                secondInside.set()
                answerBuilt.wait(WAIT)

        seen = []

        def toBSON():
            if not seen:
                secondInside.wait(WAIT)
                seen.append(secondInside.is_set())
                answerBuilt.set()
            return {"ID": "mmu00010", "source": "KEGG"}

        pathway = mock.MagicMock()
        pathway.toBSON.side_effect = toBSON
        self.jobs["JOB1"].getMatchedPathways.return_value = {"mmu00010": pathway}
        self.jobs["JOB1"].generateMetagenesList.side_effect = recompute
        responses = _runTogether(self.slider("JOB1", OMIC_A), self.slider("JOB1", OMIC_B))
        for response in responses:
            self.assertIs(response.content.get("success"), True, str(response.content)[:400])
        self.assertEqual(seen, [False], "the first answer was built while the next request was changing the job")

    def test_a_request_that_fails_lets_go_of_the_job(self):
        """Left held, the lock would stop every later request on the job."""
        from src.common import JobLocks
        self.jobs["JOB1"].generateMetagenesList.side_effect = RuntimeError("Metagenes generation failed")
        response = self.slider("JOB1", OMIC_A)()
        self.assertIs(response.content.get("success"), False)
        self.assertEqual(JobLocks.jobsHeld(), [])
        self.assertEqual(os.listdir(os.path.join(self.folder, "tmp")), [], "the work folder was left")

        self.jobs["JOB2"].cleanDirectories.side_effect = OSError("busy")
        with self.assertRaises(OSError):
            self.step2("JOB2")()
        self.assertEqual(JobLocks.jobsHeld(), [])

        self.jobs["JOB2"] = None   # removed while it waited on the queue
        response = self.step2("JOB2")()
        self.assertIs(response.content["extra"]["job_unavailable"], True)
        self.assertEqual(JobLocks.jobsHeld(), [])


class JobLocksTest(unittest.TestCase):

    def test_the_table_holds_only_jobs_in_use(self):
        from src.common import JobLocks
        with JobLocks.heldFor("JOBX"):
            self.assertIn("JOBX", JobLocks.jobsHeld())
        self.assertNotIn("JOBX", JobLocks.jobsHeld())

    def test_the_holder_can_take_it_again(self):
        from src.common import JobLocks
        with JobLocks.heldFor("JOBX"):
            with JobLocks.heldFor("JOBX"):
                pass
        self.assertNotIn("JOBX", JobLocks.jobsHeld())

    def test_a_waiting_request_says_so_once(self):
        from src.common import JobLocks
        holding, release = threading.Event(), threading.Event()

        def holder():
            with JobLocks.heldFor("JOBX"):
                holding.set()
                release.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        holding.wait(10)
        with self.assertLogs(level=logging.INFO) as captured:
            threading.Timer(0.2, release.set).start()
            with JobLocks.heldFor("JOBX"):
                pass
        thread.join(10)
        waits = [line for line in captured.output if "waiting for another request" in line]
        self.assertEqual(len(waits), 1, captured.output)
        self.assertIn("JOBX", waits[0])
        self.assertNotIn("JOBX", JobLocks.jobsHeld())


if __name__ == "__main__":
    unittest.main(verbosity=2)
