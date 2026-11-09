#!/usr/bin/env python3
"""A step stopped at its time limit says how long the limit is, why, and what helps.

Why this exists
---------------
Step 1's identifier and compound name mapping, and Step 2's pathway enrichment,
stop their workers when they have run MAX_WAIT_THREADS (900 s) and fail the job.
The browser shows the message as an internal error (Oops..Internal error!, with
Report error), followed by the dialog's own line (ajaxErrorHandler in Util.js):

    This happened on the server, so retrying in the browser will not help.

The messages read "Your data took too long to process and it was killed. Try it
again later or upload smaller files if it persists." (Step 2: "Try again
later"), so the two lines said opposite things, and nothing said how long the
limit is or why a job reaches it. #241 and #230 fixed the same clash in the
lost-worker messages.

Each now says it took longer than its limit (named: 15 minutes), the causes
(very large files, more so on a busy server, and a fault on the server), what
helps (smaller files, fewer omics in one job, and reporting it when the files
are not large), and no advice to run it again. Why those causes and not a
retry: timeLimitMessage in FeatureNamesToKeggIDsMapper, with the figures from
paintomics.org's logs.

The tests run the real mappers with forked workers and the real Step 2
enrichment on a small organism held in the cache; only the worker is a stand-in
that never finishes, and the limit is 1 second. The answer goes through the
real Step 1 queue function and handleException, and under node through the
real ajaxErrorHandler, to the text the dialog shows.

On master 7 of the 9 tests fail. The two that pass are the controls: a mapping
that finishes in time, and the answer being an internal error with Report.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_time_limit_says_what_helps
"""
import multiprocessing
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import FeatureNamesToKeggIDsMapper as mapper  # noqa: E402
from src.common.ServerErrorManager import InputFileError  # noqa: E402
from src.tests.test_a_lost_enrichment_worker_fails_the_job import (  # noqa: E402
    FIRST_PATHWAY, _OrganismInCache, _realMatchPathways)

# The server's workers are forked, on Linux. CI's unit tests run on macOS, where
# multiprocessing spawns by default, and a spawned child sees none of the patches.
multiprocessing.set_start_method("fork", force=True)

NAMES = ["feature%02d" % i for i in range(20)]

# Written out here, not built with the code under test.
REST = (" This happens with very large files, more so while the server is busy with "
        "other jobs, and it can also be a fault on the server. Smaller files, or fewer "
        "omics in one job, take less time. If your files are not large, please report "
        "this error.")
GENES = "Identifier mapping took longer than its limit of 1 second, so it was stopped." + REST
COMPOUNDS = "Compound name mapping took longer than its limit of 1 second, so it was stopped." + REST
ENRICHMENT = "Pathway enrichment took longer than its limit of 1 second, so it was stopped." + REST

DIALOG_LINE = "This happened on the server, so retrying in the browser will not help."


def _stuck(*args, **kwargs):
    time.sleep(120)


def _quickGeneWorker(jobID, organism, databases, featureList, matchedFeatures, notMatchedFeatures,
                     foundFeatures, enrichment, progressArray=None, progressSlot=0, databaseIds=None,
                     cacheTables=None, resultSlot=None, mongoHost=None):
    """mapFeatureIdentifiers without MongoDB: every name is reported not matched."""
    matchedFeatures[resultSlot] = []
    notMatchedFeatures[resultSlot] = list(featureList)
    foundFeatures.append({"entrezgene": []})


def _mapGenes(worker, limit=1):
    with mock.patch.object(mapper, "mapFeatureIdentifiers", worker), \
            mock.patch.object(mapper, "resolveDatabaseIds", return_value={}), \
            mock.patch.object(mapper, "MAX_THREADS", 3), \
            mock.patch.object(mapper, "MAX_WAIT_THREADS", limit):
        return mapper.mapFeatureNamesToKeggIDs("time-limit-test", "hsa", ["KEGG"], list(NAMES), False)


def _mapCompounds(worker, limit=1):
    with mock.patch.object(mapper, "mapCompoundsIdentifiers", worker), \
            mock.patch.object(mapper, "getCompoundNameTable", return_value=None), \
            mock.patch.object(mapper, "MAX_THREADS", 3), \
            mock.patch.object(mapper, "MAX_WAIT_THREADS", limit):
        return mapper.mapFeatureNamesToCompoundsIDs("time-limit-test", list(NAMES))


def _failure(run):
    """The exception `run` raises, and how long it took."""
    began = time.monotonic()
    try:
        run()
    except Exception as ex:   # noqa: BLE001 - the point of the call
        return ex, time.monotonic() - began
    raise AssertionError("it finished; a worker never finishes, so it had to be stopped")


class _Wording(object):
    """What every one of the three messages must and must not say."""

    def assertSaysWhatHelps(self, failure, expected):
        exception, elapsed = failure
        # Plain Exception: our side stopped it, so the browser offers Report error.
        self.assertNotIsInstance(exception, InputFileError)
        message = str(exception)
        self.assertEqual(message, expected)
        self.assertNotIn("try again", message.lower())
        self.assertNotIn("try it again", message.lower())
        self.assertNotIn("later", message)
        self.assertNotIn("if it persists", message)
        self.assertLess(elapsed, 30, "the limit of 1 second was not applied")


class Step1Test(_Wording, unittest.TestCase):

    def test_identifier_mapping(self):
        self.assertSaysWhatHelps(_failure(lambda: _mapGenes(_stuck)), GENES)

    def test_compound_name_mapping(self):
        self.assertSaysWhatHelps(_failure(lambda: _mapCompounds(_stuck)), COMPOUNDS)

    def test_a_mapping_that_finishes_in_time_is_not_stopped(self):
        found, matched, notMatched = _mapGenes(_quickGeneWorker, limit=30)
        self.assertEqual((found, matched, notMatched), ({"entrezgene": 0}, [], NAMES))


class Step2Test(_Wording, _OrganismInCache):

    def test_pathway_enrichment(self):
        def worker(jobInstance, share, *rest):
            if FIRST_PATHWAY in share:
                _stuck()
            _realMatchPathways(jobInstance, share, *rest)

        def run():
            with mock.patch("src.classes.JobInstances.PathwayAcquisitionJob.MAX_WAIT_THREADS", 1):
                self.run_step2(worker)
        self.assertSaysWhatHelps(_failure(run), ENRICHMENT)


class TheLimitIsNamedAsItIsSetTest(unittest.TestCase):

    def test_the_configured_limit_reads_15_minutes(self):
        from src.conf.serverconf import MAX_WAIT_THREADS
        self.assertEqual(MAX_WAIT_THREADS, 900)
        timeLimitText = getattr(mapper, "timeLimitText", None)
        self.assertIsNotNone(timeLimitText, "no timeLimitText on this tree")
        self.assertEqual(timeLimitText(MAX_WAIT_THREADS), "15 minutes")

    def test_other_limits(self):
        timeLimitText = getattr(mapper, "timeLimitText", None)
        self.assertIsNotNone(timeLimitText, "no timeLimitText on this tree")
        for seconds, text in ((60, "1 minute"), (120, "2 minutes"), (90, "90 seconds"),
                              (1, "1 second"), (0.5, "0.5 seconds"), (3600, "60 minutes")):
            with self.subTest(seconds=seconds):
                self.assertEqual(timeLimitText(seconds), text)


def _step1Answer():
    """The Step 1 queue function, with the job's file reading stood in for by
    the real identifier mapper and a worker that never finishes."""
    from src.paintomicsserver import Response
    from src.servlets import PathwayAcquisitionServlet as servlet

    job = mock.MagicMock()
    job.getJobID.return_value = "timeLimitJob"
    job.processFilesContent.side_effect = lambda: _mapGenes(_stuck)
    manager = mock.MagicMock()
    with mock.patch.object(servlet, "JobInformationManager", manager), \
            mock.patch.object(servlet, "JobProgress"):
        response = servlet.pathwayAcquisitionStep1_PART2(job, "u1", False, Response())
    manager.return_value.storeJobInstance.assert_not_called()
    return response.content


class TheAnswerTest(unittest.TestCase):

    def test_step1_answers_with_the_message_as_an_internal_error(self):
        answer = _step1Answer()
        self.assertIs(answer["success"], False)
        self.assertIs(answer["extra"]["input_error"], False)
        self.assertIn("ERROR MESSAGE: " + GENES, answer["message"])


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheDialogSaysOneThingTest(unittest.TestCase):
    """What the browser shows: the message, then the dialog's own line."""

    @classmethod
    def setUpClass(cls):
        from src.tests.test_an_invalid_session_is_told_by_its_own_answer import _answerTo, runDialogs
        compounds, _ = _failure(lambda: _mapCompounds(_stuck))
        cls.results = runDialogs({"step1": _step1Answer(), "compounds": _answerTo(compounds)})

    def test_it_is_an_internal_error_with_report(self):
        for name in ("step1", "compounds"):
            with self.subTest(name=name):
                dialog = self.results[name]
                self.assertIn("Oops..Internal error!", dialog["title"])
                self.assertEqual(dialog["report"], "inline-block")

    def test_the_message_and_the_dialog_line_agree(self):
        for name, expected in (("step1", GENES), ("compounds", COMPOUNDS)):
            with self.subTest(name=name):
                body = self.results[name]["body"]
                self.assertIn(expected, body)
                self.assertIn(DIALOG_LINE, body)
                # The one line about retrying is the dialog's.
                self.assertNotIn("try again", body.lower())
                self.assertNotIn("try it again", body.lower())
                self.assertEqual(body.lower().count("retry"), 1, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
