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

That message quotes the first column of the user's file, so the dialog encodes it:
an id such as <NA> has to be seen, and one with markup in it must not run. So these
messages carry [b]-style markup and no raw HTML.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_input_file_refusal_is_not_an_internal_error
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from src import paintomicsserver as server
from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob, emptyResultError
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob, explainEmptyMapping
from src.common.ServerErrorManager import InputFileError, handleException
from src.conf.serverconf import SERVER_SUBDOMAIN
from src.paintomicsserver import Response
from src.servlets import MiRNA2GenesServlet
from src.servlets import PathwayAcquisitionServlet as servlet
from src.tests import fake_omics
from src.tests.test_status_poll_retries_unreadable_answers import UTIL, extract_function, read

SERVER_SRC = os.path.join(REPO, "PaintomicsServer", "src")
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

    def test_a_file_with_no_feature_lines_is_refused_in_the_markup_the_dialog_keeps(self):
        path = os.path.join(self.inputDir, "only_header.tab")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("# name\tcondition\n")
        self.job.geneBasedInputOmics = [fake_omics.omicInput(path, omicName="Gene expression")]
        message = str(self.refusal())
        self.assertIn("[b]does not seem to have any feature lines[/b]", message)
        self.assertNotIn("<b>", message)

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

    def test_no_refusal_is_written_in_raw_html(self):
        """The dialog encodes these messages, so a tag would be read as text."""
        tag = re.compile(r"</?(?:b|br|i|a|ul|li|p|em|strong|span|div)\b[^>]*>", re.I)
        for relative in ("classes/JobInstances/PathwayAcquisitionJob.py",
                         "classes/JobInstances/Bed2GeneJob.py",
                         "classes/JobInstances/MiRNA2GeneJob.py",
                         "common/OrganismDetector.py"):
            with io.open(os.path.join(SERVER_SRC, relative), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            found = [node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str) and tag.search(node.value)]
            self.assertEqual([], found, relative)


class Step2EmptyMappingIsARefusalTest(unittest.TestCase):
    """The reports of 2026-09-15/16 (yeast ids run as fgr) and 2026-10-25 (mouse ids
    run as hsa): identifiers that match nothing reach Step 2, which runs on the job
    queue, so the refusal travels the status poll rather than a request's own
    error. PR #186 had changed Step 1 only and this one still said "Oops..Internal
    error!" with a Report button.

    Each half is the real code: the servlet raises, a real worker runs it off the
    queue, and the real route renders the body the browser parses."""

    REFUSAL = ("None of the identifiers in your data matched hsa's KEGG genes: "
               "'Gene expression' (150 identifiers), e.g. ENSMUSG00000000001 matched 0, "
               "so there is nothing to analyse.")

    def setUp(self):
        self.job = mock.MagicMock()
        self.job.getJobID.return_value = "emptymap"
        self.job.explainEmptyMapping.return_value = self.REFUSAL

    def runStep2(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = self.job
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"):
            return servlet.pathwayAcquisitionStep2_PART2(
                "emptymap", "u1", [], {}, Response(), "/nowhere/", {})

    def test_the_servlet_refuses_with_input_file_error(self):
        response = self.runStep2()
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["success"], False)
        self.assertIs(response.content["extra"]["input_error"], True)
        self.assertIn("ERROR MESSAGE: " + self.REFUSAL, response.content["message"])
        self.job.generatePathwaysList.assert_not_called()

    def test_the_flag_survives_the_status_poll(self):
        """check_job_status answers a finished job with the response the job
        returned, which is the branch this takes: the servlet catches its own
        exception, so the worker sees a finished job and not a failed one. The
        failed branch builds a new body and would drop `extra`."""
        with mock.patch.object(server, "configureLogging"), \
                mock.patch.object(server, "KeggInformationManager"), \
                mock.patch.object(server, "JobInformationManager"), \
                mock.patch.object(server.Application, "ensureIndexes"), \
                mock.patch.object(server.Application, "startScheludeTasks"):
            application = server.Application()

        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = self.job
        with mock.patch.object(servlet, "JobInformationManager", manager), \
                mock.patch.object(servlet, "JobProgress"):
            application.queue.enqueue(
                fn=servlet.pathwayAcquisitionStep2_PART2,
                args=("emptymap", "u1", [], {}, Response(), "/nowhere/", {}),
                job_id="emptymap")
            client = application.app.test_client()
            url = SERVER_SUBDOMAIN + "/check_job_status/emptymap"
            deadline = time.monotonic() + 30
            answer = client.post(url)
            while answer.status_code == 200 and time.monotonic() < deadline:
                time.sleep(0.01)
                answer = client.post(url)

        self.assertEqual(answer.status_code, 400)
        body = json.loads(answer.get_data(as_text=True))
        self.assertIs(body["extra"]["input_error"], True)
        self.assertIn(self.REFUSAL, body["message"])


DIALOG_HARNESS = r"""
const nodes = {};
function element(selector) {
    const self = {
        html(value) { nodes[selector + ".html"] = value; return self; },
        text(value) { nodes[selector + ".text"] = value; return self; },
        css(value) { nodes[selector + ".display"] = value.display; return self; },
        removeClass() { return self; }, addClass() { return self; }, hasClass() { return false; },
        show() { return self; }, hide() { return self; }, focus() { return self; },
        outerHeight() { return 100; }
    };
    return self;
}
const $ = element;
$.wait = function (fn) { fn(); };
Date.logFormat = function () { return ""; };
const dialog = {setWidth() {}, setHeight() {}, getHeight() { return 100; }, center() {}, show() {}, hide() {},
                isVisible() { return true; }, body: null, el: {focus() {}}};
/* What Ext 4.2 does; ext-all.js itself needs a DOM. The browser check runs the real one. */
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const Ext = {getCmp() { return dialog; },
             String: {htmlEncode(text) { return String(text).replace(/[&<>"']/g, function (c) { return entities[c]; }); }}};
console.error = console.warn = console.info = function () {};
var debugging = false, ignoreOtherErrors = false;
function unreadableAnswerMessage() { return "unreadable"; }
function deleteCredentials() {}
%(client)s
const results = {split: {}};
const bodies = %(bodies)s;
for (const name of Object.keys(bodies)) {
    for (const key of Object.keys(nodes)) { delete nodes[key]; }
    ignoreOtherErrors = false;
    ajaxErrorHandler({responseText: JSON.stringify(bodies[name])});
    results[name] = {
        title: nodes["#messageDialogTitle.html"],
        body: nodes["#messageDialogBody.html"],
        hidden: nodes["#hiddenMessageDialogBody.text"],
        report: nodes["#reportErrorButton.display"],
        close: nodes["#messageDialogButton.display"]
    };
}
const lines = %(lines)s;
for (const name of Object.keys(lines)) {
    results.split[name] = splitServerError(lines[name]);
}
process.stdout.write(JSON.stringify(results));
"""


HOSTILE_MIRNA = "<img src=x onerror=console.log(1)>"
ROWS = {"values": [["#miRNA", "C1", "C2"], [HOSTILE_MIRNA, "0.1", "0.4"], ["<NA>", "0.2", "0.9"]],
        "targets": [["other-miR-1", "G1"]],
        "genes": [["#gene", "C1", "C2"], ["G1", "1.0", "2.0"]]}


def _miRNAToolAnswer(rows, relevant=()):
    """What the real tool answers, on the queue, for three files.

    The real job, its real validation and the real miRNA2Target script, reached
    through the servlet's real second step. Nothing is stored, because refusing
    comes first.
    """
    root = tempfile.mkdtemp(prefix="paintomics_mirna_") + "/"
    try:
        job = MiRNA2GeneJob("mirnajob", None, root)
        os.makedirs(job.getInputDir(), exist_ok=True)
        os.makedirs(job.getTemporalDir(), exist_ok=True)
        names = {"values": "values.tab", "targets": "targets.tab", "genes": "genes.tab", "relevant": "relevant.txt"}
        for key, content in dict(rows, relevant=[[name] for name in relevant]).items():
            with io.open(os.path.join(job.getInputDir(), names[key]), "w", encoding="utf-8") as handle:
                for row in content:
                    handle.write("\t".join(row) + "\n")
        job.geneBasedInputOmics = [
            {"omicName": "miRNA-seq", "inputDataFile": names["values"], "relevantFeaturesFile": names["relevant"],
             "associationsFile": names["targets"]},
            {"omicName": "Gene expression", "inputDataFile": names["genes"]}]
        answer = _Response()
        MiRNA2GenesServlet.fromMiRNAtoGenes_STEP2(job, None, False, answer, {})
        return answer
    finally:
        shutil.rmtree(root, ignore_errors=True)


class TheMiRNAToolRefusesItsFilesTest(unittest.TestCase):
    """explainEmptyResult quotes the first column of the uploaded file."""

    def test_identifiers_that_never_join(self):
        answer = _miRNAToolAnswer(ROWS)
        self.assertEqual(answer.status, 400)
        self.assertIs(answer.content["extra"]["input_error"], True)
        self.assertIn("none of them appears in the first column", answer.content["message"])
        self.assertIn("<NA>", answer.content["message"])

    def test_scores_that_are_all_undefined(self):
        answer = _miRNAToolAnswer({
            "values": [["#miRNA", "C1", "C2"], ["mmu-miR-1", "0.1", "0.1"]],
            "targets": [["mmu-miR-1", "G1"]],
            "genes": [["#gene", "C1", "C2"], ["G1", "1.0", "1.0"]]})
        self.assertEqual(answer.status, 400)
        self.assertIs(answer.content["extra"]["input_error"], True)
        self.assertIn("undefined correlation", answer.content["message"])

    def test_the_message_reads_as_a_sentence_in_the_dialog(self):
        """The dialog does not strip the " - " step1FailureReason does."""
        message = _miRNAToolAnswer(ROWS).content["message"]
        self.assertIn("ERROR MESSAGE: No miRNA was matched", message)

    def test_a_scoring_step_that_crashed_is_not_a_refusal(self):
        """It says it is not a problem with the identifiers, so it keeps Report error.

        float() quotes the cell it could not read, and this answer reaches a dialog
        that takes HTML, so the text is escaped here. The quotes are left: the Step 1
        dialog encodes this text again, and would show an entity.
        """
        message = self._crashed().content["message"]
        self.assertIn("The scoring step stopped", message)
        self.assertIn("not a problem with your identifiers", message)
        self.assertNotIn("<img", message)
        self.assertIn("float: '&lt;img src=x onerror=console.log(1)&gt;'", message)

    def test_that_crash_is_marked_as_ours(self):
        answer = self._crashed()
        self.assertEqual(answer.status, 400)
        self.assertIs(answer.content["extra"]["input_error"], False)

    def test_stats_that_are_not_there_are_still_a_refusal(self):
        self.assertIsInstance(emptyResultError(None), InputFileError)

    def _crashed(self):
        """A target named as the gene file's header row is scored on its cell text."""
        return _miRNAToolAnswer({
            "values": [["#miRNA", "C1", "C2"], ["mmu-miR-1", "0.1", "0.4"]],
            "targets": [["mmu-miR-1", "#gene"]],
            "genes": [["#gene", HOSTILE_MIRNA, "C2"], ["G1", "1.0", "2.0"]]})


def _answerTo(exception, userID=None):
    """The body handleException really writes for this exception."""
    response = _Response()
    try:
        raise exception
    except Exception as ex:
        handleException(response, ex, "PathwayAcquisitionServlet.py", "step", userID=userID)
    return response.content


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheBrowserWordsItAsARefusal(unittest.TestCase):
    """The real ajaxErrorHandler, splitServerError and showMessage on the server's
    real answers, down to what goes into the dialog's elements."""

    IMG = "<img src=x onerror=console.log(1)>"
    MARKUP = ("[b]Errors detected in input files, please fix the following issues and try again:[/b][br]"
              "Errors detected while processing a.tab:\n[ul][li]Line 3:Line contains invalid values or symbols.[/li][/ul]")

    @classmethod
    def setUpClass(cls):
        hostile = explainEmptyMapping(
            "hsa", [{"omicName": "Gene expression", "omicSummary": [{"KEGG": 0}, 3]}],
            lambda name, size=None: [cls.IMG, "<NA>", "ENSX1"])
        cls.hostileText = hostile
        util = read(UTIL)
        script = DIALOG_HARNESS % {
            "client": "\n".join(extract_function(util, name, "Util.js") for name in
                                ("showMessage", "showErrorMessage", "ajaxErrorHandler", "splitServerError")),
            "bodies": json.dumps({
                "hostileRefusal": _answerTo(InputFileError(hostile), userID="17"),
                "markedUpRefusal": _answerTo(InputFileError(cls.MARKUP), userID="17"),
                "internalError": _answerTo(ValueError("The table is broken."), userID="17"),
                "anonymousError": _answerTo(ValueError("The table is broken.")),
                "miRNATool": _miRNAToolAnswer(ROWS).content,
            }),
            "lines": json.dumps({
                "fullStop": "InputFileError: AT S.py: f. ERROR MESSAGE: PaintOmics recognised.. User ID: 17",
                "noFullStop": "UserWarning: AT S.py: f. ERROR MESSAGE: Job 'x'. User ID: 17",
                "noSuffix": "InputFileError: AT S.py: f. ERROR MESSAGE: PaintOmics recognised.",
                "noMarker": "Your job is not on the queue anymore. User ID: 17",
                "midSentence": "ValueError: AT S.py: f. ERROR MESSAGE: Bad. User ID: 17 is not a number",
                "onlyTheSuffix": "ValueError: AT S.py: f. ERROR MESSAGE: . User ID: 17",
            }),
        }
        directory = tempfile.mkdtemp(prefix="paintomics-refusal-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_the_flag_gets_its_own_dialog_and_no_report_button(self):
        dialog = self.results["hostileRefusal"]
        self.assertIn("Please check your input files", dialog["title"])
        self.assertEqual(dialog["report"], "none")
        self.assertEqual(dialog["close"], "inline-block")
        self.assertNotIn("retrying in the browser", dialog["body"])

    def test_any_other_exception_stays_an_internal_error_with_a_report_button(self):
        dialog = self.results["internalError"]
        self.assertIn("Oops..Internal error!", dialog["title"])
        self.assertEqual(dialog["report"], "inline-block")
        self.assertIn("retrying in the browser", dialog["body"])

    def test_the_ids_in_a_refusal_are_text(self):
        self.assertIn(self.IMG, self.hostileText, "the fixture no longer quotes the id")
        body = self.results["hostileRefusal"]["body"]
        self.assertNotIn("<img", body)
        self.assertNotIn("<NA>", body)
        self.assertIn("&lt;img src=x onerror=console.log(1)&gt;", body)
        self.assertIn("&lt;NA&gt;", body, "an id the message relies on has vanished")

    def test_the_mirna_tool_refusal_is_text_with_no_report_button(self):
        result = self.results["miRNATool"]
        self.assertIn("Please check your input files", result["title"])
        self.assertEqual(result["report"], "none")
        self.assertEqual(result["close"], "inline-block")
        self.assertNotIn("<img", result["body"])
        self.assertNotIn("<NA>", result["body"])
        self.assertIn("&lt;img src=x", result["body"])
        self.assertIn("&lt;NA&gt;", result["body"])

    def test_the_markup_a_refusal_is_written_in_still_works(self):
        body = self.results["markedUpRefusal"]["body"]
        self.assertIn("<b>Errors detected", body)
        self.assertIn("</b></br>", body)
        self.assertIn("<ul><li>Line 3:", body)
        self.assertIn("</li></ul>", body)
        self.assertNotIn("[b]", body)

    def test_the_user_id_leaves_the_text_of_a_refusal(self):
        body = self.results["hostileRefusal"]["body"]
        self.assertNotIn("User ID", body)
        self.assertNotIn("..", body)
        self.assertTrue(body.endswith("."), body)

    def test_the_user_id_stays_in_what_a_report_sends(self):
        dialog = self.results["internalError"]
        self.assertNotIn("User ID", dialog["body"])
        self.assertIn("The table is broken.", dialog["body"])
        self.assertIn("User ID: 17", dialog["hidden"])
        self.assertIn("AT PathwayAcquisitionServlet.py", dialog["hidden"])

    def test_a_caller_with_no_session_has_no_suffix_to_move(self):
        dialog = self.results["anonymousError"]
        self.assertIn("The table is broken.", dialog["body"])
        self.assertNotIn("User ID", dialog["hidden"])

    def test_split_server_error(self):
        split = self.results["split"]
        self.assertEqual(split["fullStop"], {
            "readable": "PaintOmics recognised.",
            "technical": "InputFileError: AT S.py: f. User ID: 17"})
        self.assertEqual(split["noFullStop"], {
            "readable": "Job 'x'",
            "technical": "UserWarning: AT S.py: f. User ID: 17"})
        self.assertEqual(split["noSuffix"], {
            "readable": "PaintOmics recognised.",
            "technical": "InputFileError: AT S.py: f."})
        self.assertEqual(split["noMarker"], {
            "readable": "Your job is not on the queue anymore. User ID: 17",
            "technical": ""})
        self.assertEqual(split["midSentence"]["readable"], "Bad. User ID: 17 is not a number")
        self.assertNotEqual(split["onlyTheSuffix"]["readable"], "", "a dialog with nothing in it")


PREPARATION_HARNESS = r"""
const shown = [];
function showErrorMessage(title, data) { shown.push({title: title, data: data}); }
%(client)s
const jobView = {failedRequests: 1};
const response = {responseText: JSON.stringify(%(answer)s)};
if (step1FailureIsInputRefusal(response)) { jobView.step1InputRefusals = 1; }
jobView.step1FailureReasons = ["<b>miRNA-seq</b>: " + step1FailureReason(response)];
showStep1PreparationFailure(jobView);
console.log(JSON.stringify(shown[0]));
"""


class ThePreparationDialogWordsItToo(unittest.TestCase):
    """Region and miRNA files are prepared by their own jobs in Step 1; a refusal
    of those files reaches showStep1PreparationFailure, not ajaxErrorHandler."""

    def setUp(self):
        with io.open(JOB_CONTROLLER_JS, encoding="utf-8") as handle:
            self.source = handle.read()

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_a_refused_mirna_file_in_step_1(self):
        script = PREPARATION_HARNESS % {
            "client": "\n".join(extract_function(self.source, name, "JobController.js") for name in
                                ("step1FailureReason", "step1FailureIsInputRefusal", "showStep1PreparationFailure")),
            "answer": json.dumps(_miRNAToolAnswer(ROWS).content),
        }
        directory = tempfile.mkdtemp(prefix="paintomics-preparation-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        dialog = json.loads(done.stdout)
        self.assertEqual(dialog["title"], "Please check your input files")
        self.assertIs(dialog["data"]["showReportButton"], False)
        self.assertIn("<b>miRNA-seq</b>: No miRNA was matched", dialog["data"]["message"])
        self.assertIn("&lt;NA&gt;", dialog["data"]["message"])
        self.assertNotIn("<NA>", dialog["data"]["message"])
        self.assertNotIn("<img", dialog["data"]["message"])

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
