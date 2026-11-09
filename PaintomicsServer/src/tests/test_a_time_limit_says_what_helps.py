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
helps, and to report it when the files are not large, with no advice to run it
again. Why those causes and not a retry: timeLimitMessage in
FeatureNamesToKeggIDsMapper, with the figures from paintomics.org's logs.

What helps is not the same in the two steps. Step 2's enrichment tests every
omic of the job in one go, within one limit, so smaller files or fewer omics
help. Step 1 maps one omic at a time (processFilesContent calls
parseGeneBasedFiles or parseCompoundBasedFile for each in turn), and each
mapping has a limit of its own: a job with fewer omics reaches the limit again
on the same file. So Step 1's message names the omic, says its limit is its
own, and gives a smaller file for that omic as what helps
(omicTimeLimitMessage). The first wording of this change gave Step 1 "fewer
omics in one job" too, and did not say which omic it was.

The tests run the real mappers with forked workers, the real Step 1 file
processing of a job with three omics, and the real Step 2 enrichment on a small
organism held in the cache; only the worker is a stand-in that never finishes
(or takes 2.5 seconds), and the limit is 1 second (4 seconds). The answer goes
through the real Step 1 queue function and handleException, and under node
through the real ajaxErrorHandler, to the text the dialog shows.

On master 10 of the 14 tests fail. The four that pass are the controls: a
mapping that finishes in time, Step 1 of two omics that each finish within
their own limit but not within one between them (the fact Step 1's wording
rests on), Step 1 of a job whose omics all finish, and the answer being an
internal error with Report.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_time_limit_says_what_helps
"""
import multiprocessing
import os
import shutil
import sys
import tempfile
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
# Step 1: one omic at a time, each with a limit of its own.
STEP1_REST = (" This happens with a very large file, more so while the server is busy with "
              "other jobs, and it can also be a fault on the server. Each omic is mapped on "
              "its own, with a limit of its own, so what helps is a smaller file for this "
              "omic. If that file is not large, please report this error.")
# Step 2: every omic of the job in one go, within one limit.
STEP2_REST = (" This happens with very large files, more so while the server is busy with "
              "other jobs, and it can also be a fault on the server. Smaller files, or fewer "
              "omics in one job, take less time. If your files are not large, please report "
              "this error.")


def genesStopped(omicName, limit="1 second"):
    return ("Identifier mapping for omic '%s' took longer than its limit of %s, so it was "
            "stopped." % (omicName, limit)) + STEP1_REST


def compoundsStopped(omicName, limit="1 second"):
    return ("Compound name mapping for omic '%s' took longer than its limit of %s, so it was "
            "stopped." % (omicName, limit)) + STEP1_REST


GENES = genesStopped("Gene expression")
COMPOUNDS = compoundsStopped("Metabolomics")
ENRICHMENT = "Pathway enrichment took longer than its limit of 1 second, so it was stopped." + STEP2_REST

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


def _named(omicName):
    """The mapper's omicName argument, left out when there is none, so the
    controls call the mappers as master's callers did."""
    return {} if omicName is None else {"omicName": omicName}


def _mapGenes(worker, limit=1, omicName=None):
    with mock.patch.object(mapper, "mapFeatureIdentifiers", worker), \
            mock.patch.object(mapper, "resolveDatabaseIds", return_value={}), \
            mock.patch.object(mapper, "MAX_THREADS", 3), \
            mock.patch.object(mapper, "MAX_WAIT_THREADS", limit):
        return mapper.mapFeatureNamesToKeggIDs("time-limit-test", "hsa", ["KEGG"], list(NAMES), False,
                                               **_named(omicName))


def _mapCompounds(worker, limit=1, omicName=None):
    with mock.patch.object(mapper, "mapCompoundsIdentifiers", worker), \
            mock.patch.object(mapper, "getCompoundNameTable", return_value=None), \
            mock.patch.object(mapper, "MAX_THREADS", 3), \
            mock.patch.object(mapper, "MAX_WAIT_THREADS", limit):
        return mapper.mapFeatureNamesToCompoundsIDs("time-limit-test", list(NAMES), **_named(omicName))


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

    def assertNamesTheOmic(self, failure, expected):
        self.assertSaysWhatHelps(failure, expected)
        # Fewer omics would not help: each omic has a limit of its own.
        self.assertNotIn("fewer omics", str(failure[0]))

    def test_identifier_mapping(self):
        self.assertNamesTheOmic(_failure(lambda: _mapGenes(_stuck, omicName="Gene expression")), GENES)

    def test_compound_name_mapping(self):
        self.assertNamesTheOmic(_failure(lambda: _mapCompounds(_stuck, omicName="Metabolomics")),
                                COMPOUNDS)

    def test_a_caller_that_names_no_omic(self):
        message = getattr(mapper, "omicTimeLimitMessage", None)
        self.assertIsNotNone(message, "no omicTimeLimitMessage on this tree")
        self.assertEqual(message("Identifier mapping", None, 900),
                         "Identifier mapping for one omic took longer than its limit of 15 minutes, "
                         "so it was stopped." + STEP1_REST)

    def test_a_mapping_that_finishes_in_time_is_not_stopped(self):
        found, matched, notMatched = _mapGenes(_quickGeneWorker, limit=30)
        self.assertEqual((found, matched, notMatched), ({"entrezgene": 0}, [], NAMES))


def _slowGeneWorker(*args, **kwargs):
    """_quickGeneWorker after 2.5 seconds."""
    time.sleep(2.5)
    _quickGeneWorker(*args, **kwargs)


def _stuckOnProteomics(jobID, organism, databases, featureList, *rest, **kwargs):
    """Never finishes a share of the Proteomics file (names "prot.."); maps the
    other omics' shares as _quickGeneWorker does."""
    if any(feature.getName().startswith("prot") for feature in featureList):
        _stuck()
    _quickGeneWorker(jobID, organism, databases, featureList, *rest, **kwargs)


class Step1OfAJobTest(_Wording, unittest.TestCase):
    """Step 1's real file processing (processFilesContent) of a job with two
    gene based omics and one compound based omic, each a file of its own."""

    OMICS = (("Gene expression", "gene", True), ("Proteomics", "prot", True),
             ("Metabolomics", "metab", False))

    def setUp(self):
        from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
        root = tempfile.mkdtemp() + "/"
        self.addCleanup(shutil.rmtree, root, True)
        self.job = PathwayAcquisitionJob(jobID="timeLimitOmics", userID=None, CLIENT_TMP_DIR=root)
        self.job.setOrganism("hsa")
        self.job.setDatabases(["KEGG"])
        for folder in (self.job.getInputDir(), self.job.getOutputDir()):
            os.makedirs(folder, exist_ok=True)
        self.files = {}
        for omicName, prefix, geneBased in self.OMICS:
            fileName = prefix + ".tab"
            rows = ["%s%02d\t%d\t%d" % (prefix, i, i, i + 1) for i in range(12)]
            with open(os.path.join(self.job.getInputDir(), fileName), "w") as handle:
                handle.write("\n".join(["#id\tCond1\tCond2"] + rows) + "\n")
            self.files[omicName] = {"omicName": omicName, "inputDataFile": fileName,
                                    "relevantFeaturesFile": "", "associationsFile": "",
                                    "relevantAssociationsFile": "", "configOptions": None,
                                    "enrichment": "genes" if geneBased else "features"}

    def _process(self, omics, geneWorker=_quickGeneWorker, compoundWorker=None, limit=1):
        for omicName in omics:
            geneBased = dict((name, gene) for name, _, gene in self.OMICS)[omicName]
            if geneBased:
                self.job.addGeneBasedInputOmic(dict(self.files[omicName]))
            else:
                self.job.addCompoundBasedInputOmic(dict(self.files[omicName]))
        with mock.patch.object(mapper, "mapFeatureIdentifiers", geneWorker), \
                mock.patch.object(mapper, "mapCompoundsIdentifiers", compoundWorker or _stuck), \
                mock.patch.object(mapper, "resolveDatabaseIds", return_value={}), \
                mock.patch.object(mapper, "getCompoundNameTable", return_value=None), \
                mock.patch.object(mapper, "MAX_THREADS", 3), \
                mock.patch.object(mapper, "MAX_WAIT_THREADS", limit):
            return self.job.processFilesContent()

    def test_the_message_names_the_omic_that_reached_the_limit(self):
        failure = _failure(lambda: self._process(["Gene expression", "Proteomics"],
                                                 geneWorker=_stuckOnProteomics))
        self.assertSaysWhatHelps(failure, genesStopped("Proteomics"))

    def test_a_compound_based_omic_is_named_too(self):
        failure = _failure(lambda: self._process(["Gene expression", "Metabolomics"]))
        self.assertSaysWhatHelps(failure, compoundsStopped("Metabolomics"))

    def test_each_omic_has_a_limit_of_its_own(self):
        """Why Step 1's message does not say fewer omics help: two omics that
        take 2.5 seconds each both finish within a limit of 4 seconds, which
        the two of them together are over."""
        began = time.monotonic()
        self._process(["Gene expression", "Proteomics"], geneWorker=_slowGeneWorker, limit=4)
        self.assertGreater(time.monotonic() - began, 5)
        self.assertEqual([omic["omicSummary"][0] for omic in self.job.getGeneBasedInputOmics()],
                         [{"entrezgene": 0}, {"entrezgene": 0}])

    def test_a_job_whose_omics_finish_is_not_stopped(self):
        self._process(["Gene expression", "Proteomics"])
        self.assertEqual(len(self.job.getGeneBasedInputOmics()), 2)


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
    job.processFilesContent.side_effect = lambda: _mapGenes(_stuck, omicName="Gene expression")
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
        compounds, _ = _failure(lambda: _mapCompounds(_stuck, omicName="Metabolomics"))
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
