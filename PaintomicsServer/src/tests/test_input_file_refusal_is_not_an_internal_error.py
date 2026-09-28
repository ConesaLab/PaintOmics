"""A refusal of the user's files must not read as a fault of ours.

Step 1's input validation raised a plain Exception, so the browser titled the
refusal "Oops..Internal error!", added "This happened on the server, so retrying
in the browser will not help" and offered Report error. Both error reports of
the week of 2026-09-21 on paintomics.org were exactly that:

- 2026-09-23, job iXe6W0Pl60: Proteomics with 6 sample columns beside
  Metabolomics with 10 ("Every omic in one run must have the same number of
  conditions ...");
- 2026-09-26, job 44jczfH717: a DESeq2 export whose last column is up/down
  ("Line contains invalid values or symbols"), and a Mercator mapping file
  dropped on the Relevant features row.

The message named the problem correctly each time; the frame around it told the
user it was ours. The validation now raises InputFileError, the error response
says so (extra.input_error) and ajaxErrorHandler words it as what it is, with no
Report button. Any other exception keeps the internal-error dialog.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_input_file_refusal_is_not_an_internal_error
"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
from src.common.ServerErrorManager import InputFileError, handleException
from src.tests import fake_omics

SERVER_SRC = os.path.join(REPO, "PaintomicsServer", "src")
UTIL_JS = os.path.join(REPO, "PaintomicsClient", "public_html", "app", "view", "common", "Util.js")
JOB_CONTROLLER_JS = os.path.join(REPO, "PaintomicsClient", "public_html", "app", "controller", "JobController.js")


class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


class _JobCase(unittest.TestCase):

    def setUp(self):
        self._tmpRoot = tempfile.mkdtemp(prefix="paintomics_refusal_") + "/"
        self.job = PathwayAcquisitionJob(jobID="refusal", userID=None, CLIENT_TMP_DIR=self._tmpRoot)
        self.inputDir = self.job.getInputDir()
        os.makedirs(self.inputDir, exist_ok=True)
        self.job.geneBasedInputOmics = []
        self.job.compoundBasedInputOmics = []

    def tearDown(self):
        shutil.rmtree(self._tmpRoot, ignore_errors=True)

    def refusal(self):
        with self.assertRaises(InputFileError) as caught:
            self.job.validateInput()
        return caught.exception


class ValidationRaisesInputFileError(_JobCase):

    def test_omics_of_different_widths(self):
        """The 2026-09-23 shape: 6 sample columns beside 10."""
        proteomics = fake_omics.proteomicsFile(self.inputDir, "proteomics_values.tab",
                                               nFeatures=12, nConditions=6)
        metabolites = fake_omics.metabolomicsFile(self.inputDir, "metabolomica_positiva_values.tab",
                                                  nFeatures=12, nConditions=10)
        self.job.geneBasedInputOmics = [fake_omics.omicInput(proteomics, omicName="Proteomics")]
        self.job.compoundBasedInputOmics = [fake_omics.omicInput(metabolites, omicName="Metabolomics")]
        self.assertIn("same number of conditions", str(self.refusal()))

    def test_a_table_with_a_text_column(self):
        """The 2026-09-26 shape: DESeq2's up/down column in the values file."""
        path = os.path.join(self.inputDir, "MM.txt")
        rows = ["Name\tControl-1_fpkm\tTreat-1_fpkm\tlog2FoldChange\tregulated"]
        rows += ["Sobic.00%dG21510%d\t1.59\t0.17\t-3.16\tdown" % (i % 9 + 1, i) for i in range(12)]
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("\r\n".join(rows) + "\r\n")
        self.job.geneBasedInputOmics = [fake_omics.omicInput(path, omicName="Gene expression")]
        self.assertIn("invalid values or symbols", str(self.refusal()))

    def test_acceptable_files_raise_nothing(self):
        genes = fake_omics.geneExpressionFile(self.inputDir, nFeatures=6)
        self.job.geneBasedInputOmics = [fake_omics.omicInput(genes)]
        self.assertTrue(self.job.validateInput())


class TheErrorResponseSaysWhichItIs(unittest.TestCase):

    def respond(self, exception):
        response = _Response()
        try:
            raise exception
        except Exception as ex:
            handleException(response, ex, "PathwayAcquisitionServlet.py", "pathwayAcquisitionStep1_PART2")
        return response.content

    def test_an_input_refusal_is_flagged(self):
        content = self.respond(InputFileError("[b]Errors detected in input files[/b]"))
        self.assertIs(False, content["success"])
        self.assertIs(True, content["extra"]["input_error"])
        # The client splits the log half off on this marker; the class name in
        # front of it changed, the marker did not.
        self.assertIn("ERROR MESSAGE: [b]Errors detected in input files[/b]", content["message"])

    def test_any_other_exception_is_not(self):
        for exception in (Exception("boom"), KeyError("x"), ValueError("y")):
            self.assertIs(False, self.respond(exception)["extra"]["input_error"], repr(exception))


class EveryInputValidationRaisesIt(unittest.TestCase):
    """Bed2Gene and miRNA2Gene refuse their files with the same sentence."""

    def test_no_input_refusal_is_a_plain_exception(self):
        for relative in ("classes/JobInstances/PathwayAcquisitionJob.py",
                         "classes/JobInstances/Bed2GeneJob.py",
                         "classes/JobInstances/MiRNA2GeneJob.py",
                         "servlets/PathwayAcquisitionServlet.py"):
            with io.open(os.path.join(SERVER_SRC, relative), encoding="utf-8") as handle:
                source = handle.read()
            plain = re.findall(r'raise Exception\(\s*"(?:\[b\])?Errors detected in input files', source)
            self.assertEqual([], plain, relative)
            self.assertNotRegex(source, r'Exception\("\[b\]One of the uploaded files is not UTF-8', relative)


class TheBrowserWordsItAsARefusal(unittest.TestCase):
    """Source assertions: the client has no test harness. Verified in Chrome."""

    def setUp(self):
        with io.open(UTIL_JS, encoding="utf-8") as handle:
            util = handle.read()
        match = re.search(r"function ajaxErrorHandler\(responseObj\) \{.*?\n\}", util, re.S)
        self.assertTrue(match, "ajaxErrorHandler is gone")
        self.handler = match.group(0)

    def test_the_flag_gets_its_own_dialog_before_the_internal_error_one(self):
        flag = self.handler.index("err.extra.input_error === true")
        self.assertLess(flag, self.handler.index('showErrorMessage("Oops..Internal error!"'))
        branch = self.handler[flag:self.handler.index("return;", flag)]
        self.assertIn('"Please check your input files"', branch)
        self.assertIn("showReportButton: false", branch)
        self.assertNotIn("retrying in the browser", branch)


class ThePreparationDialogWordsItToo(unittest.TestCase):
    """Region and miRNA files are prepared by their own jobs in Step 1; a refusal
    of those files reaches showStep1PreparationFailure, not ajaxErrorHandler."""

    def setUp(self):
        with io.open(JOB_CONTROLLER_JS, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_a_refused_preparation_is_counted(self):
        self.assertRegex(self.source, r"function step1FailureIsInputRefusal\(response\)")
        self.assertRegex(self.source, r"extra\.input_error === true")
        self.assertRegex(self.source, r"if \(step1FailureIsInputRefusal\(response\)\) \{\s*"
                                      r"jobView\.step1InputRefusals = ")
        self.assertRegex(self.source, r"jobView\.failedRequests = 0;\s*jobView\.step1InputRefusals = 0;")

    def test_all_failures_refused_means_no_report_button(self):
        match = re.search(r"function showStep1PreparationFailure\(jobView\) \{.*?\n\}", self.source, re.S)
        self.assertTrue(match, "showStep1PreparationFailure is gone")
        dialog = match.group(0)
        self.assertIn('"Please check your input files"', dialog)
        self.assertIn("showReportButton: !refusedInput", dialog)
        self.assertRegex(dialog, r"refusedInput = .*step1InputRefusals.*>= failed")


if __name__ == "__main__":
    unittest.main()
