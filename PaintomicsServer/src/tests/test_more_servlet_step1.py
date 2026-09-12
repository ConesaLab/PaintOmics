#!/usr/bin/env python3
"""Cover for fromMOREtoGenes_STEP1 in src/servlets/MOREServlet.py.

This is the last MORE function with no test. It normally needs a Mongo-backed
session, a writable job directory and a job queue, but each of those is reached
through a module-level name, so all three can be swapped for a double and the
handler exercised for real: form parsing, the regulatory-omic loop, file
saving, parameter coercion, queueing and the error path.

Job directories are created under a temporary CLIENT_TMP_DIR, so nothing is
written into the real client data tree.

What this pins down
-------------------
* The regulatory-omic loop indexes on omic_name_<i>. The client only ever emits
  contiguous indices (panels are added with a monotonic counter and there is no
  removal handler), but a gap silently truncates the list rather than erroring,
  so the behaviour is recorded here deliberately -- if a remove button is ever
  added to the panel, this test is the thing that says what breaks.
* A [MyData] file location is accepted in place of an upload, and the
  "[MyData]/" prefix is stripped before it reaches the job.
* An exception is reported as {"success": False, "message": ...} rather than
  escaping to Flask -- these jobs are queued, and a raw traceback here is what
  the user sees.

Engine availability is pinned, not inherited from the host
----------------------------------------------------------
STEP1 refuses a job whose engine this host cannot run (engineRefusal), and
nothing here is about that: this suite covers form handling, so every test
runs on a host where all four engines are available. Left to the real host
the suite reported the machine, not the servlet -- 18 tests failed on any
box without a more-rs binary, and the MLR test then passed on CI (binary,
R without MORE) only because a fallback was too wide and let an unnamed MLR
through to an Rscript that could not run it. Which host can run what is
test_more_engine_choice's subject, with every host state mocked there.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_more_servlet_step1
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.servlets import MOREServlet


class FakeUserSessionManager(object):
    """isValidUser is the only thing STEP1 uses; record the call."""
    calls = []
    raises = None

    def isValidUser(self, userID, sessionToken):
        FakeUserSessionManager.calls.append((userID, sessionToken))
        if FakeUserSessionManager.raises:
            raise FakeUserSessionManager.raises
        return True


class FakeUpload(object):
    def __init__(self, filename):
        self.filename = filename


class FakeRequest(object):
    def __init__(self, form=None, files=None, cookies=None):
        self.form = form or {}
        self.files = files or {}
        self.cookies = cookies or {"userID": "u1", "sessionToken": "tok"}


class FakeResponse(object):
    def __init__(self):
        self.content = None

    def setContent(self, content):
        self.content = content


class FakeQueue(object):
    def __init__(self):
        self.enqueued = []

    def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)


class Step1TestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="moretest_")
        self._realSession = MOREServlet.UserSessionManager
        self._realSaveFile = MOREServlet.saveFile
        self._realTmpDir = MOREServlet.CLIENT_TMP_DIR

        FakeUserSessionManager.calls = []
        FakeUserSessionManager.raises = None
        MOREServlet.UserSessionManager = FakeUserSessionManager
        MOREServlet.CLIENT_TMP_DIR = self.tmp + os.sep

        self.saved = []

        def fakeSaveFile(userID, fileName, fields, fileObj, inputDir):
            self.saved.append({"userID": userID, "fileName": fileName,
                               "fields": fields, "inputDir": inputDir})
            return fileName

        MOREServlet.saveFile = fakeSaveFile
        self.queue = FakeQueue()
        self.response = FakeResponse()

        # A host with the engine installed, so no test here can be refused for
        # what the machine running it happens to lack (see the module
        # docstring). mock.patch.stopall undoes it on cleanup.
        mock.patch.object(MOREServlet, "moreBinary",
                          return_value="/x/more-rs").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        MOREServlet.UserSessionManager = self._realSession
        MOREServlet.saveFile = self._realSaveFile
        MOREServlet.CLIENT_TMP_DIR = self._realTmpDir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_step1(self, form, files=None, jobID="JOB1"):
        MOREServlet.fromMOREtoGenes_STEP1(
            FakeRequest(form=form, files=files), self.response, self.queue, jobID)
        return self.response.content

    def queuedJob(self):
        self.assertTrue(self.queue.enqueued, "nothing was queued")
        return self.queue.enqueued[0]["args"][0]

    @staticmethod
    def validForm(**overrides):
        form = {
            "omic_name_0": "TF",
            "omic_type_0": "TF",
            "more_method": "PLS1",
            "more_alpha": "0.05",
            "more_vip": "0.8",
            "more_filter_r2": "0",
            "more_minvar_0": "0",
        }
        form.update(overrides)
        return form

    @staticmethod
    def validFiles():
        return {
            "rnaseqaux_file": FakeUpload("GeneExpression.tab"),
            "conditions_file": FakeUpload("Conditions.tab"),
            "file_0_file": FakeUpload("TFExpression.tab"),
        }


class HappyPathTest(Step1TestCase):

    def test_reports_success_with_the_job_id(self):
        content = self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(content, {"success": True, "jobID": "JOB1"})

    def test_validates_the_session_before_doing_anything(self):
        self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(FakeUserSessionManager.calls, [("u1", "tok")])

    def test_saves_every_uploaded_file(self):
        self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(sorted(s["fileName"] for s in self.saved),
                         ["Conditions.tab", "GeneExpression.tab", "TFExpression.tab"])

    def test_queues_step2_with_the_job(self):
        self.run_step1(self.validForm(), self.validFiles())
        entry = self.queue.enqueued[0]
        self.assertIs(entry["fn"], MOREServlet.fromMOREtoGenes_STEP2)
        self.assertEqual(entry["job_id"], "JOB1")
        self.assertEqual(self.queuedJob().getJobID(), "JOB1")

    def test_records_the_regulatory_omic(self):
        self.run_step1(self.validForm(), self.validFiles())
        omics = self.queuedJob().regulatoryOmics
        self.assertEqual(len(omics), 1)
        self.assertEqual(omics[0]["name"], "TF")
        self.assertEqual(omics[0]["file"], "TFExpression.tab")

    def test_carries_the_model_parameters_onto_the_job(self):
        job = None
        self.run_step1(self.validForm(more_method="MLR", more_filter_r2="0.3"),
                       self.validFiles())
        job = self.queuedJob()
        self.assertEqual(job.method, "MLR")
        self.assertEqual(job.alpha, 0.05)
        self.assertEqual(job.vip, 0.8)
        self.assertEqual(job.filter_r2, 0.3)


class ParameterCoercionTest(Step1TestCase):
    """The _toFloat guard, exercised through the handler rather than directly."""

    def test_blank_numeric_fields_fall_back_to_defaults(self):
        self.run_step1(
            self.validForm(more_alpha="", more_vip="", more_filter_r2=""),
            self.validFiles())
        job = self.queuedJob()
        self.assertEqual((job.alpha, job.vip, job.filter_r2), (0.05, 0.8, 0.0))
        self.assertEqual(self.response.content["success"], True)

    def test_junk_numeric_fields_do_not_fail_the_request(self):
        self.run_step1(self.validForm(more_alpha="abc", more_vip="--"),
                       self.validFiles())
        self.assertEqual(self.response.content["success"], True)
        self.assertEqual(self.queuedJob().alpha, 0.05)

    def test_blank_method_and_enrichment_fall_back(self):
        self.run_step1(self.validForm(more_method="", more_enrichment=""),
                       self.validFiles())
        job = self.queuedJob()
        self.assertEqual(job.method, "PLS1")
        self.assertEqual(job.enrichment, "genes")

    def test_minvar_auto_sentinel_reaches_the_job(self):
        self.run_step1(self.validForm(more_minvar_0="auto"), self.validFiles())
        self.assertEqual(self.queuedJob().regulatoryOmics[0]["minVariation"], "NA")

    def test_minvar_numeric_reaches_the_job(self):
        self.run_step1(self.validForm(more_minvar_0="0.25"), self.validFiles())
        self.assertEqual(self.queuedJob().regulatoryOmics[0]["minVariation"], 0.25)


class RegulatoryOmicLoopTest(Step1TestCase):

    def test_reads_several_contiguous_omics(self):
        form = self.validForm(omic_name_1="miRNA", omic_type_1="miRNA",
                              more_minvar_1="0")
        files = self.validFiles()
        files["file_1_file"] = FakeUpload("miRNA.tab")
        self.run_step1(form, files)
        self.assertEqual([o["name"] for o in self.queuedJob().regulatoryOmics],
                         ["TF", "miRNA"])

    def test_a_gap_in_the_indices_truncates_silently(self):
        """Recorded, not endorsed. The client cannot currently produce a gap --
        panels are added with a monotonic counter and never removed -- but if a
        remove button is added, omic_name_2 below is dropped with no error."""
        form = self.validForm(omic_name_2="miRNA", omic_type_2="miRNA")
        files = self.validFiles()
        files["file_2_file"] = FakeUpload("miRNA.tab")
        self.run_step1(form, files)
        self.assertEqual([o["name"] for o in self.queuedJob().regulatoryOmics], ["TF"])

    def test_no_regulatory_omics_still_queues(self):
        """Validation of this is STEP2's job; STEP1 must not crash."""
        form = {k: v for k, v in self.validForm().items()
                if not k.startswith(("omic_name_", "omic_type_", "more_minvar_"))}
        self.run_step1(form, self.validFiles())
        self.assertEqual(self.queuedJob().regulatoryOmics, [])

    def test_omic_name_is_stripped(self):
        self.run_step1(self.validForm(omic_name_0="  TF  "), self.validFiles())
        self.assertEqual(self.queuedJob().regulatoryOmics[0]["name"], "TF")


class FileLocationTest(Step1TestCase):
    """Re-runs reference already-uploaded files instead of posting them."""

    def test_mydata_prefix_is_stripped(self):
        form = self.validForm(
            rnaseqaux_filelocation="[MyData]/GeneExpression.tab",
            conditions_filelocation="[MyData]/Conditions.tab",
            file_0_filelocation="[MyData]/TFExpression.tab")
        self.run_step1(form, {})
        job = self.queuedJob()
        self.assertEqual(job.targetExpressionFile, "GeneExpression.tab")
        self.assertEqual(job.conditionsFile, "Conditions.tab")
        self.assertEqual(job.regulatoryOmics[0]["file"], "TFExpression.tab")

    def test_absent_file_and_location_becomes_none_not_empty_string(self):
        """STEP2 tests these with `not omic.get("file")`; "" would pass an
        os.path.join and fail later with a confusing message."""
        self.run_step1(self.validForm(), {})
        job = self.queuedJob()
        self.assertIsNone(job.targetExpressionFile)
        self.assertIsNone(job.conditionsFile)
        self.assertIsNone(job.regulatoryOmics[0]["file"])

    def test_an_upload_wins_over_a_location(self):
        form = self.validForm(rnaseqaux_filelocation="[MyData]/Stale.tab")
        self.run_step1(form, self.validFiles())
        self.assertEqual(self.queuedJob().targetExpressionFile, "GeneExpression.tab")


class ErrorPathTest(Step1TestCase):

    def test_an_invalid_session_is_reported_not_raised(self):
        FakeUserSessionManager.raises = Exception("Invalid session")
        content = self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(content["success"], False)
        self.assertIn("Invalid session", content["message"])

    def test_nothing_is_queued_when_the_session_is_invalid(self):
        FakeUserSessionManager.raises = Exception("Invalid session")
        self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(self.queue.enqueued, [])

    def test_a_save_failure_is_reported_not_raised(self):
        def boom(*a, **k):
            raise IOError("disk full")
        MOREServlet.saveFile = boom
        content = self.run_step1(self.validForm(), self.validFiles())
        self.assertEqual(content["success"], False)
        self.assertIn("disk full", content["message"])

    def test_the_response_object_is_always_returned(self):
        FakeUserSessionManager.raises = Exception("nope")
        result = MOREServlet.fromMOREtoGenes_STEP1(
            FakeRequest(form=self.validForm()), self.response, self.queue, "JOB1")
        self.assertIs(result, self.response)


class UploadedAuxiliaryFilesTest(Step1TestCase):
    """Associations and relevant-features can arrive as uploads, not just as
    "[MyData]/..." references.

    Line coverage put these two branches among the only uncovered statements in
    the servlet, and they are the ones most users actually take: the reference
    form is for files already in the workspace. The upload branch is also the
    only route by which a relevant-features file reaches
    _parseRelevantRegulators, and a mistake there is silent -- the job
    succeeds and simply has no red stars.
    """

    def filesWithAux(self):
        files = self.validFiles()
        files["assoc_file_0_file"] = FakeUpload("TFAssociations.tab")
        files["relevant_file_0_file"] = FakeUpload("TFSignificant.txt")
        return files

    def savedFor(self, fileName):
        hits = [s for s in self.saved if s["fileName"] == fileName]
        self.assertEqual(len(hits), 1, f"{fileName} was not saved exactly once")
        return hits[0]

    def test_an_uploaded_association_file_is_saved(self):
        self.run_step1(self.validForm(), self.filesWithAux())
        self.savedFor("TFAssociations.tab")

    def test_an_uploaded_association_file_is_tagged_as_associations(self):
        self.run_step1(self.validForm(), self.filesWithAux())
        fields = self.savedFor("TFAssociations.tab")["fields"]
        self.assertEqual(fields["dataType"], "Associations")
        self.assertEqual(fields["omicType"], "TF")

    def test_an_uploaded_relevant_file_is_saved(self):
        self.run_step1(self.validForm(), self.filesWithAux())
        self.savedFor("TFSignificant.txt")

    def test_an_uploaded_relevant_file_is_tagged_as_relevant_features(self):
        self.run_step1(self.validForm(), self.filesWithAux())
        fields = self.savedFor("TFSignificant.txt")["fields"]
        self.assertEqual(fields["dataType"], "Relevant Features")
        self.assertEqual(fields["omicType"], "TF")

    def test_both_reach_the_queued_job(self):
        self.run_step1(self.validForm(), self.filesWithAux())
        omic = self.queuedJob().regulatoryOmics[0]
        self.assertEqual(omic["associations"], "TFAssociations.tab")
        self.assertEqual(omic["relevant"], "TFSignificant.txt")

    def test_an_upload_wins_over_a_filelocation_for_the_same_omic(self):
        # Both supplied: the servlet checks the upload first, so the reference
        # must not silently override the file the user just sent.
        form = self.validForm(assoc_file_0_filelocation="[MyData]/Old.tab",
                              relevant_file_0_filelocation="[MyData]/OldRel.txt")
        self.run_step1(form, self.filesWithAux())
        omic = self.queuedJob().regulatoryOmics[0]
        self.assertEqual(omic["associations"], "TFAssociations.tab")
        self.assertEqual(omic["relevant"], "TFSignificant.txt")

    def test_without_either_form_the_omic_carries_no_auxiliary_paths(self):
        self.run_step1(self.validForm(), self.validFiles())
        omic = self.queuedJob().regulatoryOmics[0]
        self.assertIsNone(omic["associations"])
        self.assertIsNone(omic["relevant"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
