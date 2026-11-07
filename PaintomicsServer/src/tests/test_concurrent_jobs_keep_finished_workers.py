#!/usr/bin/env python3
"""Two jobs running at once on one server do not fail each other.

The first and largest part: a mapping worker that has finished is never killed
as "still running" just because another job's thread collected its exit status
first.

Why this exists
---------------
While PR #218 was tested, two Step 1 jobs on one server started mapping in the
same second and one of them failed after 0.6 s with "Your data took too long to
process and it was killed", against a limit (MAX_WAIT_THREADS) of 900 s. It
passed when rerun alone.

The jobs run on threads of one server process. Every ``Process.start()`` in
that process begins with multiprocessing's ``_cleanup()``, which calls
``os.waitpid`` on every child the process has, so on the workers of the other
jobs too. The mappers then decided "still running" with
``Process.is_alive()``, which calls ``os.waitpid`` as well. When another
thread has just collected a finished worker and not yet stored its exit code,
that second ``waitpid`` fails with "no such child", and Python 3.11 reads that
as "not started yet" and answers alive. The mapper terminated the worker it
thought was stuck and failed the job.

The tests below hold that moment open: the stand-in for "another job's thread"
collects each worker with ``os.waitpid`` as soon as it exits and never stores
the exit code. Before the fix both identifier mappers failed with "took too
long" here; they now decide from each worker's sentinel (a pipe the kernel
closes when the worker exits). RealTimeLimitStillAppliesTest checks the real
limit still works: a worker still running at the deadline is stopped and the job
fails as before, also when the server thread that forked it blocks SIGTERM, as
uWSGI's threads do.

The other ways one job could reach another, all found by testing this one:

* A worker that exited while another job's child also holds its pipe: the
  sentinel stays quiet, so only the exit code tells (PipeHeldElsewhereTest).
* A worker forked while another job's thread holds a lock of the
  KeggInformationManager froze until the deadline and got the same message
  (ForkWhileAnotherJobHoldsACacheLockTest).
* A worker forked while another thread was looking up a host name could never
  look one up itself, so it could not reach MongoDB
  (WorkersDoNotLookUpTheDatabaseHostTest). It listed valid genes as not matched,
  and the job finished as a success (DatabaseFailureIsNotAnUnmatchedNameTest).

Usage:
    cd PaintomicsServer
    python -m src.tests.test_concurrent_jobs_keep_finished_workers
"""
import ast
import inspect
import multiprocessing
import os
import signal
import sys
import textwrap
import threading
import time
import unittest
from unittest import mock

from pymongo.errors import ServerSelectionTimeoutError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene
from src.common import FeatureNamesToKeggIDsMapper as mapper
from src.common.KeggInformationManager import KeggInformationManager

TOO_LONG = "took too long"


class _CollectedByAnotherThread(multiprocessing.Process):
    """A worker whose exit status another thread collects before this object sees it.

    ``os.waitpid`` here is the first half of what ``_cleanup()`` does on another
    job's thread; the second half, storing the code on this object, never comes,
    which is the state the mapper used to look at.
    """

    def start(self):
        super().start()
        os.waitpid(self.pid, 0)


class _Recorded(multiprocessing.Process):
    """An ordinary worker, kept so the test can look at it afterwards."""
    started = []

    def start(self):
        super().start()
        _Recorded.started.append(self)


def _geneWorker(jobID, organism, databases, featureList, matchedFeatures, notMatchedFeatures,
                foundFeatures, enrichment, progressArray=None, progressSlot=0, databaseIds=None,
                cacheTables=None, resultSlot=None, mongoHost=None):
    """mapFeatureIdentifiers without MongoDB: every name is reported not matched."""
    matchedFeatures[resultSlot] = []
    notMatchedFeatures[resultSlot] = list(featureList)
    foundFeatures.append({"entrezgene": []})


def _compoundWorker(jobID, featureList, matchedFeatures, notMatchedFeatures, foundFeatures,
                    resultSlot=None):
    """mapCompoundsIdentifiers without MongoDB: every name is reported not matched."""
    matchedFeatures[resultSlot] = []
    notMatchedFeatures[resultSlot] = list(featureList)
    foundFeatures.append(0)


def _stuckWorker(*args, **kwargs):
    time.sleep(120)


NAMES = ["feature%02d" % i for i in range(20)]


def _mapGenes():
    return mapper.mapFeatureNamesToKeggIDs("concurrent-test", "hsa", ["KEGG"], list(NAMES), False)


def _mapCompounds():
    return mapper.mapFeatureNamesToCompoundsIDs("concurrent-test", list(NAMES))


class FinishedWorkersCollectedElsewhereTest(unittest.TestCase):

    def test_gene_mapping_finishes(self):
        with mock.patch.object(mapper, "Process", _CollectedByAnotherThread), \
                mock.patch.object(mapper, "mapFeatureIdentifiers", _geneWorker), \
                mock.patch.object(mapper, "resolveDatabaseIds", return_value={}):
            found, matched, notMatched = _mapGenes()
        self.assertEqual(matched, [])
        self.assertEqual(notMatched, NAMES)
        self.assertEqual(found, {"entrezgene": 0})

    def test_compound_mapping_finishes(self):
        with mock.patch.object(mapper, "Process", _CollectedByAnotherThread), \
                mock.patch.object(mapper, "mapCompoundsIdentifiers", _compoundWorker), \
                mock.patch.object(mapper, "getCompoundNameTable", return_value=None):
            found, matched, notMatched = _mapCompounds()
        self.assertEqual((found, matched, notMatched), (0, [], NAMES))


class RealTimeLimitStillAppliesTest(unittest.TestCase):

    def setUp(self):
        _Recorded.started = []

    def tearDown(self):
        for process in _Recorded.started:
            if process.exitcode is None:
                process.kill()
            process.join(5)

    def _assertStoppedAndReported(self, run, workerName):
        began = time.monotonic()
        with mock.patch.object(mapper, "Process", _Recorded), \
                mock.patch.object(mapper, workerName, _stuckWorker), \
                mock.patch.object(mapper, "MAX_WAIT_THREADS", 0.5), \
                mock.patch.object(mapper, "resolveDatabaseIds", return_value={}), \
                mock.patch.object(mapper, "getCompoundNameTable", return_value=None):
            with self.assertRaises(Exception) as caught:
                run()
        self.assertIn(TOO_LONG, str(caught.exception))
        self.assertLess(time.monotonic() - began, 30)
        self.assertTrue(_Recorded.started)
        for process in _Recorded.started:
            process.join(5)
            self.assertEqual(process.exitcode, -signal.SIGTERM)

    def test_gene_mapping_still_stops_a_stuck_worker(self):
        self._assertStoppedAndReported(_mapGenes, "mapFeatureIdentifiers")

    def test_compound_mapping_still_stops_a_stuck_worker(self):
        self._assertStoppedAndReported(_mapCompounds, "mapCompoundsIdentifiers")

    def test_a_worker_forked_by_a_thread_that_blocks_sigterm_is_still_stopped(self):
        # uWSGI's threads block every signal and a forked child inherits the
        # mask: terminate() then left the worker alive with SIGTERM pending.
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM])
        try:
            self._assertStoppedAndReported(_mapGenes, "mapFeatureIdentifiers")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class _HolderOfThePipe:
    """What a child of another job does when it is forked while a worker's pipe
    is open: holds the write end after the worker has gone. Here the worker
    forks the holder itself, so that it inherits the pipe at a known moment.
    ``release()`` lets every holder exit.
    """

    def __init__(self):
        self.releaseRead, self.releaseWrite = os.pipe()

    def leaveOne(self):
        if os.fork() == 0:
            os.close(self.releaseWrite)
            os.read(self.releaseRead, 1)
            os._exit(0)

    def release(self):
        os.close(self.releaseWrite)
        os.close(self.releaseRead)


class PipeHeldElsewhereTest(unittest.TestCase):

    def setUp(self):
        self.holder = _HolderOfThePipe()
        self.addCleanup(self.holder.release)

    def _exitedWorker(self):
        worker = multiprocessing.Process(target=self.holder.leaveOne)
        worker.start()
        # Waits for the exit and leaves the status for the code under test.
        os.waitid(os.P_PID, worker.pid, os.WEXITED | os.WNOWAIT)
        self.assertEqual(mapper._readySentinels([worker.sentinel], 0), [])   # the pipe says "running"
        return worker

    def test_a_worker_that_has_gone_is_not_running(self):
        worker = self._exitedWorker()
        self.assertEqual(mapper._stillRunning([worker]), [])

    def test_the_wait_does_not_last_as_long_as_the_holder(self):
        worker = self._exitedWorker()
        began = time.monotonic()
        mapper._joinAllWithinDeadline([worker], 10)
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(worker.exitcode, 0)

    def test_gene_mapping_finishes_though_its_workers_pipes_are_held(self):
        holder = self.holder

        def worker(*args, **kwargs):
            _geneWorker(*args, **kwargs)
            holder.leaveOne()

        with mock.patch.object(mapper, "mapFeatureIdentifiers", worker), \
                mock.patch.object(mapper, "MAX_WAIT_THREADS", 10), \
                mock.patch.object(mapper, "resolveDatabaseIds", return_value={}):
            began = time.monotonic()
            _, _, notMatched = _mapGenes()
        self.assertEqual(notMatched, NAMES)
        self.assertLess(time.monotonic() - began, 5)


def _askTheCacheAndExit():
    # The call _matchPathways makes first. Exit code 0 means it came back.
    KeggInformationManager().getPathwaySourceByID("forktest", "path1")


class ForkWhileAnotherJobHoldsACacheLockTest(unittest.TestCase):

    def setUp(self):
        self.pathway = {"source": "KEGG"}
        self.manager = KeggInformationManager()
        self.organism = {"name": "forktest", "pathways": {"path1": self.pathway}}
        self.manager.lastOrganisms.append(self.organism)
        self.addCleanup(self.manager.lastOrganisms.remove, self.organism)

    def _assertWorkerFinishesWhileHeld(self, lockName):
        held, letGo = threading.Event(), threading.Event()

        def anotherJob():
            with getattr(self.manager, lockName):
                held.set()
                letGo.wait(30)

        thread = threading.Thread(target=anotherJob)
        thread.start()
        self.assertTrue(held.wait(5))
        worker = multiprocessing.Process(target=_askTheCacheAndExit)
        try:
            worker.start()
            worker.join(5)
            self.assertEqual(worker.exitcode, 0)
        finally:
            letGo.set()
            thread.join(5)
            if worker.exitcode is None:
                worker.kill()
                worker.join(5)

    def test_translation_cache_lock(self):
        self._assertWorkerFinishesWhileHeld("lock")

    def test_organism_lock(self):
        self._assertWorkerFinishesWhileHeld("organismLock")

    def test_a_half_built_id_list_is_never_visible(self):
        # With the locks new in the child, a worker forked in the middle of this
        # build goes straight on and reads what it finds.
        seen = []

        def features(key):
            for featureID in ("a", "b"):
                seen.append(key in self.pathway)
                yield {"id": featureID}

        self.pathway["genes"] = features("geneIDList")
        self.pathway["compounds"] = features("compoundIDList")
        genes, compounds = self.manager.getAllFeatureIDsByPathwayID("forktest", "path1")
        self.assertEqual((genes, compounds), ({"a", "b"}, {"a", "b"}))
        self.assertEqual(seen, [False] * 4)


class _DatabaseThatDoesNotAnswer:
    class xref:
        @staticmethod
        def find(*args, **kwargs):
            raise ServerSelectionTimeoutError("No servers found yet, Timeout: 30s")

    def close(self):
        pass


class DatabaseFailureIsNotAnUnmatchedNameTest(unittest.TestCase):

    def test_both_lookups_raise_it(self):
        db = _DatabaseThatDoesNotAnswer()
        with self.assertRaises(ServerSelectionTimeoutError):
            mapper.findIDsByFeaturesName("job", ["TP53"], db, "target")
        with self.assertRaises(ServerSelectionTimeoutError):
            mapper.findIDsByFeaturesNameForDatabases("job", ["TP53"], db, ["target"])

    def test_any_other_failure_is_still_logged_and_leaves_the_names_without_a_match(self):
        db = mock.Mock()
        db.xref.find.side_effect = ValueError("not about the database")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(mapper.findIDsByFeaturesName("job", ["TP53"], db, "target"), {})
        with self.assertLogs(level="ERROR"):
            self.assertEqual(mapper.findIDsByFeaturesNameForDatabases("job", ["TP53"], db, ["target"]),
                             {"target": {}})

    def test_the_job_fails_and_does_not_report_the_names_as_not_matched(self):
        # The real workers, forked: only the database is a stand-in.
        db = _DatabaseThatDoesNotAnswer()
        genes = [Gene("") for _ in NAMES]
        for gene, name in zip(genes, NAMES):
            gene.setName(name)
        with mock.patch.object(mapper, "getConnectionByOrganismCode", return_value=(db, db)), \
                mock.patch.object(mapper, "resolveDatabaseIds",
                                  return_value=({"KEGG": "target"}, {"KEGG": "target"})):
            with self.assertRaises(Exception) as caught:
                mapper.mapFeatureNamesToKeggIDs("concurrent-test", "hsa", ["KEGG"], genes, False)
        self.assertIn("could not reach the database", str(caught.exception))


def _geneWorkerThatInsistsOnTheAddress(*args, **kwargs):
    if args[-1] != "192.0.2.7":   # the mongoHost the parent passed
        os._exit(3)
    _geneWorker(*args, **kwargs)


class WorkersDoNotLookUpTheDatabaseHostTest(unittest.TestCase):

    def test_the_parent_looks_it_up_and_hands_the_address_to_its_workers(self):
        with mock.patch.object(mapper, "mapFeatureIdentifiers", _geneWorkerThatInsistsOnTheAddress), \
                mock.patch.object(mapper.socket, "gethostbyname", return_value="192.0.2.7"), \
                mock.patch.object(mapper, "resolveDatabaseIds", return_value={}):
            _, _, notMatched = _mapGenes()
        self.assertEqual(notMatched, NAMES)

    def test_a_worker_connects_to_the_address_it_was_handed(self):
        with mock.patch.object(mapper, "MongoClient") as client:
            found = []
            mapper.mapFeatureIdentifiers("concurrent-test", "hsa", ["KEGG"], [], [None], [None], found, "genes",
                                         databaseIds=({"KEGG": "target"}, {"KEGG": "target"}),
                                         resultSlot=0, mongoHost="192.0.2.7")
        self.assertEqual(client.call_args.args[0], "192.0.2.7")
        self.assertEqual(len(found), 1)

    def test_without_an_address_it_connects_to_the_configured_host(self):
        with mock.patch.object(mapper, "MongoClient") as client:
            mapper.getConnectionByOrganismCode("hsa")
        self.assertEqual(client.call_args.args[0], mapper.MONGODB_HOST)

    def test_a_host_that_does_not_resolve_is_handed_over_as_it_is(self):
        for failure in (OSError("no such host"), UnicodeError("label too long")):
            with mock.patch.object(mapper.socket, "gethostbyname", side_effect=failure):
                self.assertEqual(mapper._mongoAddressForWorkers(), mapper.MONGODB_HOST)


class StillRunningTest(unittest.TestCase):

    def test_exited_and_collected_elsewhere_is_not_running(self):
        worker = _CollectedByAnotherThread(target=os._exit, args=(0,))
        worker.start()
        self.assertIsNone(worker.exitcode)   # the state the old check mistook for "running"
        self.assertEqual(mapper._stillRunning([worker]), [])

    def test_a_running_worker_is_running(self):
        finished = multiprocessing.Process(target=os._exit, args=(0,))
        running = multiprocessing.Process(target=_stuckWorker)
        finished.start()
        running.start()
        try:
            finished.join(5)
            self.assertEqual(mapper._stillRunning([finished, running]), [running])
        finally:
            running.kill()
            running.join(5)
        self.assertEqual(mapper._stillRunning([finished, running]), [])

    def test_every_time_limit_check_uses_it(self):
        # Step 2's enrichment workers have the same check; it cannot be driven
        # without a MongoDB, so at least nothing may go back to is_alive().
        from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
        for function in (mapper.mapFeatureNamesToKeggIDs, mapper.mapFeatureNamesToCompoundsIDs,
                         PathwayAcquisitionJob.generatePathwaysList):
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            called = [node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                      for node in ast.walk(tree) if isinstance(node, ast.Call)]
            self.assertIn("_stillRunning", called, function.__name__)
            self.assertNotIn("is_alive", called, function.__name__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
