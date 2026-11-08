#!/usr/bin/env python3
"""A Step 2 enrichment worker that dies fails the job; it does not shorten the list.

Why this exists
---------------
Step 2 (PathwayAcquisitionJob.generatePathwaysList) splits the pathways into
shares and forks one worker per share (_matchPathways), plus one per share of
Reactome classes. Each worker tests its share and hands the matched pathways to
the parent through a Manager dict. Nothing checked that every share came back.
A worker the system killed for memory, or one that stopped on an error, took its
share with it: the job finished as a success, with fewer pathways, a smaller
count of matched pathways and no word about it. A missing pathway looked as if
none of the user's genes or metabolites were in it, since Step 2 lists every
pathway that holds at least one.

Step 1's identifier mapper has had this check since PR #222 ("Identifier mapping
lost N of M worker processes"). Step 2 now has its own: each worker hands its
share over in ONE assignment under its own slot, and the parent fails the job
when a slot is missing, with a message that says how many workers and how many
pathways were lost.

The tests run the real generatePathwaysList and the real workers on a small
organism held in the KeggInformationManager's cache, so no MongoDB is needed.
Only the worker that should die is a stand-in: it kills itself the way the
kernel's out-of-memory killer does (SIGKILL), raises, or leaves with exit
code 0 without handing anything over, and otherwise runs the real worker.
TheMessageReachesTheBrowserTest runs the Step 2 servlet around it: the message
arrives as an internal error (Report error, not "check your file"), and the
short list is never stored.

The count is of what arrived, never of exit codes: when two jobs run at once
another job's thread can collect a worker's exit status first, and its exit
code then stays unknown (see _stillRunning in FeatureNamesToKeggIDsMapper).
WorkersCollectedElsewhereTest holds that state open for every worker.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_lost_enrichment_worker_fails_the_job
"""
import multiprocessing
import os
import signal
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene, OmicValue
from src.classes.JobInstances import PathwayAcquisitionJob as paj
from src.common.KeggInformationManager import KeggInformationManager
from src.common.ServerErrorManager import InputFileError

# The server's workers are forked, on Linux. CI's unit tests run on macOS, where
# multiprocessing spawns by default, and a spawned child sees none of the
# patches below.
multiprocessing.set_start_method("fork", force=True)

ORGANISM = "lostworkertest"
OMIC = "Gene expression"
N_GENES = 40
N_PATHWAYS = 30
N_CLASSES = 6
# With 3 threads, 30 pathways go out in shares of 11, 11 and 8 (ceil(30 / 3) + 1
# per share), and 6 Reactome classes in shares of 3 and 3.
THREADS = 3
FIRST_PATHWAY = "path:lw00"
FIRST_CLASS = "Class 0"

_realMatchPathways = paj._matchPathways


def _pathwayID(i):
    return "path:lw%02d" % i


def _organism(source):
    """Thirty pathways of three genes each, every gene in some pathway."""
    pathways = {}
    for i in range(N_PATHWAYS):
        pathways[_pathwayID(i)] = {
            "name": "Pathway %d" % i,
            "source": source,
            "classification": "Class %d;Subclass" % (i % N_CLASSES),
            "genes": [{"id": "gene%02d" % ((i + k) % N_GENES)} for k in range(3)],
            "compounds": [],
        }
    return {"name": ORGANISM, "pathways": pathways}


def _job(database):
    job = paj.PathwayAcquisitionJob("LOSTWORKERTEST", None, "/tmp/")
    job.setOrganism(ORGANISM)
    job.setDatabases([database])
    job.addGeneBasedInputOmic({"omicName": OMIC, "omicSummary": [{"Total": N_GENES}, 0],
                               "enrichment": "genes"})
    for i in range(N_GENES):
        gene = Gene("gene%02d" % i)
        gene.setName("gene%02d" % i)
        value = OmicValue("input%02d" % i)
        value.setOmicName(OMIC)
        value.setOriginalName("input%02d" % i)
        value.setValues([float(i)])
        value.setRelevant([i % 3 == 0])
        gene.addOmicValue(value)
        job.addInputGeneData(gene)
    return job


def _dyingOn(victim, die):
    """The real worker, except that the one whose share holds ``victim`` dies first."""
    def worker(jobInstance, share, *rest):
        if victim in share:
            die()
        _realMatchPathways(jobInstance, share, *rest)
    return worker


def _killedLikeTheOutOfMemoryKiller():
    os.kill(os.getpid(), signal.SIGKILL)


def _leavesWithoutAWord():
    os._exit(0)


def _stopsOnAnError():
    # multiprocessing prints this traceback on the worker's stderr.
    raise RuntimeError("a fault in the worker (expected by this test)")


class _CollectedByAnotherThread(multiprocessing.Process):
    """A worker whose exit status another thread collects before this object
    sees it, so its exitcode stays None (see the same class in
    test_concurrent_jobs_keep_finished_workers)."""
    started = []

    def start(self):
        super().start()
        os.waitpid(self.pid, 0)
        _CollectedByAnotherThread.started.append(self)


class _OrganismInCache(unittest.TestCase):
    SOURCE = "KEGG"

    def setUp(self):
        self.organism = _organism(self.SOURCE)
        manager = KeggInformationManager()
        manager.lastOrganisms.append(self.organism)
        self.addCleanup(manager.lastOrganisms.remove, self.organism)
        for name, value in (("MAX_THREADS", THREADS), ("MAX_WAIT_THREADS", 60)):
            patcher = mock.patch.object(paj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_step2(self, worker=None):
        job = _job(self.SOURCE)
        if worker is None:
            job.generatePathwaysList()
        else:
            with mock.patch.object(paj, "_matchPathways", worker):
                job.generatePathwaysList()
        return job

    def assertLost(self, worker, workers, results):
        with self.assertRaises(Exception) as caught:
            self.run_step2(worker)
        message = str(caught.exception)
        # Plain Exception: our side failed, so the browser offers Report error.
        self.assertNotIsInstance(caught.exception, InputFileError)
        self.assertIn("Pathway enrichment lost %s worker processes" % workers, message)
        self.assertIn("the results for %s" % results, message)
        # The internal-error dialog adds right after it "This happened on the
        # server, so retrying in the browser will not help."; advice to try
        # again here would say the opposite.
        self.assertNotIn("try again", message.lower())


class LostPathwayWorkerTest(_OrganismInCache):

    def test_a_worker_killed_for_memory_fails_the_job(self):
        self.assertLost(_dyingOn(FIRST_PATHWAY, _killedLikeTheOutOfMemoryKiller),
                        "1 of 3", "11 of 30 pathways")

    def test_a_worker_that_stops_on_an_error_fails_the_job(self):
        self.assertLost(_dyingOn(FIRST_PATHWAY, _stopsOnAnError), "1 of 3", "11 of 30 pathways")

    def test_a_worker_that_leaves_with_exit_code_0_and_no_results_fails_the_job(self):
        # Counted from what arrived: a clean exit code is not a delivered share.
        self.assertLost(_dyingOn(FIRST_PATHWAY, _leavesWithoutAWord), "1 of 3", "11 of 30 pathways")

    def test_two_lost_workers_are_both_counted(self):
        def worker(jobInstance, share, *rest):
            if _pathwayID(N_PATHWAYS - 1) not in share:
                _killedLikeTheOutOfMemoryKiller()
            _realMatchPathways(jobInstance, share, *rest)
        self.assertLost(worker, "2 of 3", "22 of 30 pathways")


class EveryShareArrivesTest(_OrganismInCache):

    def test_every_pathway_comes_back_with_the_same_values_as_with_one_worker(self):
        # Merging the shares must give exactly what one worker computes alone.
        shared = self.run_step2().getMatchedPathways()
        with mock.patch.object(paj, "MAX_THREADS", 1):
            alone = self.run_step2().getMatchedPathways()
        self.assertEqual(sorted(shared), sorted(_pathwayID(i) for i in range(N_PATHWAYS)))
        self.assertEqual(list(shared), sorted(shared))   # in the order of the pathway list
        self.assertEqual({pathwayID: pathway.toBSON() for pathwayID, pathway in shared.items()},
                         {pathwayID: pathway.toBSON() for pathwayID, pathway in alone.items()})

    def test_a_worker_that_dies_after_handing_its_share_over_loses_nothing(self):
        def worker(*args):
            _realMatchPathways(*args)
            os._exit(1)
        self.assertEqual(len(self.run_step2(worker).getMatchedPathways()), N_PATHWAYS)


class WorkersCollectedElsewhereTest(_OrganismInCache):

    def test_a_finished_job_is_not_failed_for_exit_codes_it_never_saw(self):
        _CollectedByAnotherThread.started = []
        with mock.patch.object(multiprocessing, "Process", _CollectedByAnotherThread):
            job = self.run_step2()
        self.assertEqual(len(job.getMatchedPathways()), N_PATHWAYS)
        # The state held open: three workers, none of whose exit codes was seen.
        self.assertEqual([worker.exitcode for worker in _CollectedByAnotherThread.started], [None] * THREADS)


class LostReactomeClassWorkerTest(_OrganismInCache):
    SOURCE = "Reactome"

    def test_every_class_comes_back(self):
        job = self.run_step2()
        self.assertEqual(len(job.getMatchedPathways()), N_PATHWAYS)
        self.assertEqual(sorted(job.getMatchedClass()), ["Class %d" % i for i in range(N_CLASSES)])

    def test_a_lost_class_worker_fails_the_job(self):
        self.assertLost(_dyingOn(FIRST_CLASS, _killedLikeTheOutOfMemoryKiller),
                        "1 of 5", "3 of 6 Reactome classes")

    def test_a_lost_pathway_worker_and_a_lost_class_worker_are_both_named(self):
        def worker(jobInstance, share, *rest):
            if FIRST_PATHWAY in share or FIRST_CLASS in share:
                _killedLikeTheOutOfMemoryKiller()
            _realMatchPathways(jobInstance, share, *rest)
        self.assertLost(worker, "2 of 5", "11 of 30 pathways and 3 of 6 Reactome classes")


class TheMessageReachesTheBrowserTest(_OrganismInCache):
    """The Step 2 servlet, with only the job's loading and storing stood in for:
    the failure is answered as an internal error carrying the message, and the
    short list is never stored."""

    def test_step2_answers_with_the_message_and_stores_nothing(self):
        from src.paintomicsserver import Response
        from src.servlets import PathwayAcquisitionServlet as servlet

        realJob = _job(self.SOURCE)
        job = mock.MagicMock()
        job.getJobID.return_value = "lostworker"
        job.explainEmptyMapping.return_value = None
        job.generatePathwaysList.side_effect = realJob.generatePathwaysList
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"), \
                mock.patch.object(paj, "_matchPathways", _dyingOn(FIRST_PATHWAY, _killedLikeTheOutOfMemoryKiller)):
            response = servlet.pathwayAcquisitionStep2_PART2(
                "lostworker", "u1", [], {}, Response(), "/nowhere/", {})

        self.assertIs(response.content["success"], False)
        self.assertIs(response.content["extra"]["input_error"], False)
        self.assertIn("ERROR MESSAGE: Pathway enrichment lost 1 of 3 worker processes",
                      response.content["message"])
        manager.return_value.storeJobInstance.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
