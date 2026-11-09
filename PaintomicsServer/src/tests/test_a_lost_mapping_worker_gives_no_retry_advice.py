#!/usr/bin/env python3
"""Step 1's lost-worker message says what happened and why, and gives no retry advice.

Why this exists
---------------
When a Step 1 identifier mapping worker is lost (killed by the system for memory,
or stopped on an error such as not reaching the database), mapFeatureNamesToKeggIDs
fails the job, which the browser shows as *Oops..Internal error!* with Report error.
That dialog (ajaxErrorHandler in Util.js) adds, right after the server's message:

    This happened on the server, so retrying in the browser will not help.

The message ended "Please try again, and upload smaller files if it persists.", so
the two lines said opposite things. #230 fixed the same clash in Step 2's message
(_lostWorkersMessage in PathwayAcquisitionJob); this does it for Step 1: the
message ends after saying why a worker is lost.

The tests run the real mapFeatureNamesToKeggIDs with forked workers. Only the
worker is a stand-in, with no MongoDB: the one whose share holds the first name
dies (SIGKILL as the out-of-memory killer sends it, an error, or a clean exit with
nothing handed over), the others report every name as not matched.
TheMessageReachesTheBrowserTest runs the Step 1 queue function around it: the
answer is an internal error carrying the message, and nothing is stored.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_lost_mapping_worker_gives_no_retry_advice
"""
import multiprocessing
import os
import signal
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import FeatureNamesToKeggIDsMapper as mapper
from src.common.ServerErrorManager import InputFileError

# The server's workers are forked, on Linux. CI's unit tests run on macOS, where
# multiprocessing spawns by default, and a spawned child sees none of the patches.
multiprocessing.set_start_method("fork", force=True)

NAMES = ["feature%02d" % i for i in range(20)]
# 20 names over 3 workers go out in shares of 8, 8 and 4 (ceil(20 / 3) + 1).
THREADS = 3

EXPECTED = ("Identifier mapping lost 1 of 3 worker processes, and with them the identifiers "
            "they were mapping. The job was stopped rather than go on without them. A worker "
            "is lost when the server runs short of memory or the worker stops on an error, for "
            "example when it cannot reach the database.")


def _killedLikeTheOutOfMemoryKiller():
    os.kill(os.getpid(), signal.SIGKILL)


def _stopsOnAnError():
    # multiprocessing prints this traceback on the worker's stderr.
    raise RuntimeError("a fault in the worker (expected by this test)")


def _leavesWithoutAWord():
    os._exit(0)


def _dyingOnTheFirstShare(die):
    """mapFeatureIdentifiers without MongoDB; the worker holding NAMES[0] dies first."""
    def worker(jobID, organism, databases, featureList, matchedFeatures, notMatchedFeatures,
               foundFeatures, enrichment, progressArray=None, progressSlot=0, databaseIds=None,
               cacheTables=None, resultSlot=None, mongoHost=None):
        if NAMES[0] in featureList:
            die()
        matchedFeatures[resultSlot] = []
        notMatchedFeatures[resultSlot] = list(featureList)
        foundFeatures.append({"entrezgene": []})
    return worker


def _map(worker):
    with mock.patch.object(mapper, "mapFeatureIdentifiers", worker), \
            mock.patch.object(mapper, "resolveDatabaseIds", return_value={}), \
            mock.patch.object(mapper, "MAX_THREADS", THREADS):
        return mapper.mapFeatureNamesToKeggIDs("lost-mapping-worker", "hsa", ["KEGG"], list(NAMES), False)


class TheMessageGivesNoRetryAdviceTest(unittest.TestCase):

    def assertLost(self, die):
        with self.assertRaises(Exception) as caught:
            _map(_dyingOnTheFirstShare(die))
        message = str(caught.exception)
        # Plain Exception: our side failed, so the browser offers Report error.
        self.assertNotIsInstance(caught.exception, InputFileError)
        self.assertEqual(message, EXPECTED)
        # The dialog adds "This happened on the server, so retrying in the browser
        # will not help." right after it; advice to try again would say the opposite.
        self.assertNotIn("try again", message.lower())
        self.assertNotIn("smaller files", message)
        self.assertNotIn("if it persists", message)

    def test_a_worker_killed_for_memory(self):
        self.assertLost(_killedLikeTheOutOfMemoryKiller)

    def test_a_worker_that_stops_on_an_error(self):
        self.assertLost(_stopsOnAnError)

    def test_a_worker_that_leaves_with_exit_code_0_and_nothing_handed_over(self):
        self.assertLost(_leavesWithoutAWord)

    def test_the_counts_are_the_ones_passed(self):
        self.assertTrue(mapper.lostMappingWorkersMessage(2, 11).startswith(
            "Identifier mapping lost 2 of 11 worker processes, and with them"))

    def test_a_mapping_with_every_worker_is_not_refused(self):
        found, matched, notMatched = _map(_dyingOnTheFirstShare(lambda: None))
        self.assertEqual((found, matched, notMatched), ({"entrezgene": 0}, [], NAMES))


class TheMessageReachesTheBrowserTest(unittest.TestCase):
    """The Step 1 queue function, with the job's file reading and storing stood in
    for: the failure is answered as an internal error carrying the message, which
    the browser shows under "Oops..Internal error!" with Report error, followed by
    its own "retrying in the browser will not help"."""

    def test_step1_answers_with_the_message_and_stores_nothing(self):
        from src.paintomicsserver import Response
        from src.servlets import PathwayAcquisitionServlet as servlet

        job = mock.MagicMock()
        job.getJobID.return_value = "lost-mapping-worker"
        job.processFilesContent.side_effect = lambda: _map(_dyingOnTheFirstShare(_killedLikeTheOutOfMemoryKiller))
        manager = mock.MagicMock()
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"):
            response = servlet.pathwayAcquisitionStep1_PART2(job, "u1", False, Response())

        self.assertIs(response.content["success"], False)
        self.assertIs(response.content["extra"]["input_error"], False)
        self.assertIn("ERROR MESSAGE: " + EXPECTED, response.content["message"])
        manager.return_value.storeJobInstance.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
