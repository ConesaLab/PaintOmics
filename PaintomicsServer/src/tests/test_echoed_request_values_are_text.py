"""What a request carries is shown as text, in every message that repeats it.

PR #218 escaped the job ids, omic names and modes that some routes repeat in their
messages. These were left, each reachable by a hand-made request, and each inserted
into the page as HTML:

- the dataset id of /pa_step1/example/<id> and of the converters' example/<id>;
- the example mode of /dm_fromBEDtoGenes, /dm_fromMiRNAtoGenes and
  /dm_fromMOREtoGenes, and MORE's model name (more_method);
- the pathway ids /pa_step3 is asked for;
- the name of an uploaded part, which Step 1 repeats when the omic name that
  goes with it is missing;
- a job id the AI routes and the AI compound selection could not find, and the
  compound selection's own failure text;
- the hub panel's errors and the cluster number of /pa_get_clusters, which
  int() quotes ("invalid literal for int() with base 10: '...'").

A value goes one of three ways. Where every page that shows the message inserts it
as HTML, the server escapes it, as #218 did. Where one page sets it as text and
another inserts it as HTML (the AI refusals; the example dataset id, which the
experiment design note shows as text), escaping would show "&lt;" on the first, so
the value is repeated only when it is letters, digits, '.', '_' and '-', as every
real id is, and left out otherwise. And a converter's answer (regions, miRNA, MORE
to genes) is marked as text with extra.plain_text and escaped by nobody but the
page: Step 1 encodes it where it shows it, and the server escaped a crash of the
miRNA scoring step as well, so Step 1 showed "&lt;" where the file had "<".

The client half runs the real functions under node on the server's real answers;
the omic name, which is raw in Step 2's cluster setting, is checked the same way.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_echoed_request_values_are_text
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from html import escape
from unittest import mock
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from src import paintomicsserver as server
from src.classes.JobInstances import PathwayAcquisitionJob as jobModule
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
from src.common import ExampleDatasets
from src.common.PySiQ import JobStatus
from src.common.ServerErrorManager import InputFileError, handleException, quotable
from src.conf.serverconf import SERVER_SUBDOMAIN
from src.paintomicsserver import Response
from src.servlets import AIInterpretServlet
from src.servlets import Bed2GenesServlet
from src.servlets import CompoundSuggestionServlet
from src.servlets import MiRNA2GenesServlet
from src.servlets import MOREServlet
from src.servlets import PathwayAcquisitionServlet
from src.tests.test_input_file_refusal_is_not_an_internal_error import _miRNAToolAnswer
from src.tests.test_status_poll_retries_unreadable_answers import UTIL, extract_function, read
from src.tests.test_step1_invalid_form_names_the_field import extract_block

CLIENT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "PaintomicsClient", "public_html"))
JOB_CONTROLLER_JS = os.path.join(CLIENT_ROOT, "app", "controller", "JobController.js")
STEP2_VIEWS_JS = os.path.join(CLIENT_ROOT, "app", "view", "PathwayAcquisitionViews", "PA_Step2Views.js")

HOSTILE = "<img src=x onerror=console.log(1)>"
# The same value as the two encoders on the page write it.
STEP1_ENCODED = "&lt;img src=x onerror=console.log(1)&gt;"      # step1FailureReason: & < >
SERVER_ESCAPED = escape(HOSTILE)                                # html.escape, the server
assert SERVER_ESCAPED == STEP1_ENCODED                          # no quote in HOSTILE

# A target named after the gene file's header row: the scoring step reads that
# row's cell as a number and crashes, quoting it.
CRASH_ROWS = {"values": [["#miRNA", "C1", "C2"], ["mmu-miR-1", "0.1", "0.4"]],
              "targets": [["mmu-miR-1", "#gene"]],
              "genes": [["#gene", HOSTILE, "C2"], ["G1", "1.0", "2.0"]]}

HAS_NODE = shutil.which("node") is not None


def _node(script):
    directory = tempfile.mkdtemp(prefix="paintomics-echo-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    if done.returncode != 0:
        raise AssertionError("the harness failed:\n" + done.stderr)
    return json.loads(done.stdout)


class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status
        return self

    def setContent(self, content):
        self.content = content
        return self


# ----------------------------------------------------------------------------
# The server, through the real routes
# ----------------------------------------------------------------------------

class _Routes(object):
    """The real application's routes, with the session check passed and job
    directories under a temporary folder. Built once: Application starts workers."""

    application = None

    @classmethod
    def get(cls):
        if cls.application is None:
            with mock.patch.object(server, "configureLogging"), \
                    mock.patch.object(server, "KeggInformationManager"), \
                    mock.patch.object(server, "JobInformationManager"), \
                    mock.patch.object(server.Application, "ensureIndexes"), \
                    mock.patch.object(server.Application, "startScheludeTasks"):
                cls.application = server.Application()
        return cls.application


class HandMadeRequestsTest(unittest.TestCase):
    """Each request a person could type, and what the server answers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="paintomics_echo_") + "/"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for module in (PathwayAcquisitionServlet, Bed2GenesServlet, MiRNA2GenesServlet,
                       MOREServlet, AIInterpretServlet):
            self._patch(module, "UserSessionManager", mock.MagicMock())
        for module in (PathwayAcquisitionServlet, Bed2GenesServlet, MiRNA2GenesServlet, MOREServlet):
            self._patch(module, "CLIENT_TMP_DIR", self.tmp)
        self.client = _Routes.get().app.test_client()

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, data=None):
        answer = self.client.post(SERVER_SUBDOMAIN + path, data=data or {})
        return answer.status_code, json.loads(answer.get_data(as_text=True))

    def assertNoMarkup(self, text):
        self.assertNotIn("<img", text)
        self.assertNotIn("<", text.replace("&lt;", ""), "a raw tag is left: %s" % text)

    def test_an_example_dataset_id_is_not_repeated_unless_it_is_plain(self):
        for route in ("/pa_step1/example/", "/dm_fromBEDtoGenes/example/",
                      "/dm_fromMiRNAtoGenes/example/", "/dm_fromMOREtoGenes/example/"):
            with self.subTest(route=route):
                status, body = self.post(route + quote(HOSTILE, safe=""))
                self.assertIs(body["success"], False)
                self.assertIn("There is no example dataset by that name. Available datasets:", body["message"])
                self.assertNotIn("img", body["message"])
                self.assertNoMarkup(body["message"])

    def test_a_plain_mistyped_id_is_still_named(self):
        """The message is there to show what was typed beside what exists."""
        status, body = self.post("/pa_step1/example/region-basd")
        self.assertIn("There is no example dataset called 'region-basd'. Available datasets:", body["message"])
        self.assertIn("region-based", body["message"])

    def test_an_unrecognised_example_mode_is_escaped(self):
        for route in ("/dm_fromBEDtoGenes/", "/dm_fromMiRNAtoGenes/", "/dm_fromMOREtoGenes/"):
            with self.subTest(route=route):
                status, body = self.post(route + quote(HOSTILE, safe=""))
                self.assertIs(body["success"], False)
                self.assertIn("Unrecognised example mode %s" % escape(repr(HOSTILE)), body["message"])
                # The sentence's own placeholder used to vanish as an unknown element.
                self.assertIn("'example/&lt;dataset-id&gt;'", body["message"])
                self.assertNoMarkup(body["message"])

    def test_the_more_model_name_is_escaped(self):
        with mock.patch.object(MOREServlet, "moreBinary", return_value="/x/more-rs"):
            status, body = self.post("/dm_fromMOREtoGenes", {"more_method": HOSTILE})
        self.assertIs(body["success"], False)
        self.assertEqual("'%s' is not a regulatory model this server offers." % SERVER_ESCAPED, body["message"])

    def test_the_pathways_step_3_is_asked_for_are_escaped(self):
        job = PathwayAcquisitionJob("echoJob01", None, self.tmp)
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        self._patch(PathwayAcquisitionServlet, "JobInformationManager", manager)
        self._patch(jobModule, "KeggInformationManager", mock.MagicMock())
        status, body = self.post("/pa_step3", {"jobID": "echoJob01", "selectedPathways": [HOSTILE, "mmu00010"]})
        self.assertEqual(status, 400)
        self.assertIn("None of the requested pathways belong to this job: %s, mmu00010." % SERVER_ESCAPED,
                      body["message"])
        self.assertNoMarkup(body["message"])
        self.assertIs(body["extra"]["plain_text"], False, "an HTML answer whose values the server escaped")

    def test_the_name_of_an_uploaded_part_is_escaped(self):
        """saveFiles names the form field it is missing after the part's own name."""
        databases = mock.MagicMock()
        databases.resolveDatabases.return_value = ["KEGG"]
        self._patch(PathwayAcquisitionServlet, "DatabaseAvailability", databases)
        status, body = self.post("/pa_step1", {"specie": "mmu",
                                               HOSTILE + "_file": (io.BytesIO(b"#id\tc1\nA\t1\n"), "a.tab")})
        self.assertEqual(status, 400)
        self.assertIn("the form field '%s_omic_name' is missing" % SERVER_ESCAPED, body["message"])
        self.assertIn("the file '%s_file' belongs to" % SERVER_ESCAPED, body["message"])
        self.assertNoMarkup(body["message"])

    def test_a_cluster_number_that_is_not_a_number_is_not_quoted(self):
        """int() quoted it: "invalid literal for int() with base 10: '<img ...>'"."""
        job = PathwayAcquisitionJob("echoJob01", None, self.tmp)
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        self._patch(PathwayAcquisitionServlet, "JobInformationManager", manager)
        status, body = self.post("/pa_get_clusters", {"jobID": "echoJob01", "omic": "Gene expression",
                                                      "number": HOSTILE, "database": "KEGG"})
        self.assertEqual(status, 400)
        self.assertTrue(body["message"].endswith("ERROR MESSAGE: The number of clusters must be a whole number."),
                        body["message"])

    def test_the_ai_routes_leave_out_an_id_they_cannot_repeat(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = None
        self._patch(AIInterpretServlet, "JobInformationManager", manager)
        status, body = self.post("/ai_interpret_status", {"jobID": HOSTILE})
        self.assertEqual(status, 400)
        self.assertTrue(body["message"].endswith("ERROR MESSAGE: This job was not found."), body["message"])
        self.assertNotIn("img", body["message"])
        # A real id is still named, and the client's test for this answer still matches.
        status, body = self.post("/ai_interpret_status", {"jobID": "r0l53602VQ"})
        self.assertTrue(body["message"].endswith("ERROR MESSAGE: Job r0l53602VQ was not found."), body["message"])

    def test_the_hub_panel_errors_are_escaped(self):
        status, body = self.post("/pa_hub_subgraph", {"jobID": "echoJob01", "compoundID": "C00031", "level": HOSTILE})
        self.assertIs(body["success"], False)
        self.assertIn("invalid literal for int() with base 10: " + escape("'%s'" % HOSTILE), body["errorMessage"])
        self.assertNoMarkup(body["errorMessage"])


class TheAIRefusalsTest(unittest.TestCase):
    """_requireJobAccess's two refusals, which the walk panel sets as text."""

    def test_a_signed_in_stranger_still_reads_invalid_job_id(self):
        job = mock.MagicMock()
        job.getUserID.return_value = "owner"
        job.getAllowSharing.return_value = False
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = job
        with mock.patch.object(AIInterpretServlet, "JobInformationManager", manager):
            with self.assertRaises(UserWarning) as caught:
                AIInterpretServlet._requireJobAccess("r0l53602VQ", "stranger")
        self.assertEqual(str(caught.exception), "Invalid Job ID (r0l53602VQ) for current user.")

    def test_quotable(self):
        self.assertEqual(quotable("r0l53602VQ"), "r0l53602VQ")
        self.assertEqual(quotable("stategra-multiomics"), "stategra-multiomics")
        self.assertEqual(quotable("R-MMU-1234.5_x"), "R-MMU-1234.5_x")
        for value in (HOSTILE, "a b", "a'b", 'a"b', "a&b", "", None, "-leading", "x" * 101, "a\nb"):
            with self.subTest(value=value):
                self.assertIsNone(quotable(value))


class TheExampleIdMessageTest(unittest.TestCase):
    """getScenario's message is shown as text by the experiment design note
    (aiGenerateExpDesign, through _exampleHeaderOmics) and as HTML by Step 1."""

    def test_a_hostile_id_is_left_out_and_logged(self):
        exampleFiles = os.path.join(HERE, "..", "examplefiles") + "/"
        with self.assertLogs(level="INFO") as logs, self.assertRaises(ExampleDatasets.UnknownScenario) as caught:
            ExampleDatasets.getScenario(exampleFiles, HOSTILE)
        self.assertNotIn("img", str(caught.exception))
        self.assertIn("stategra-multiomics", str(caught.exception))
        self.assertTrue(any(HOSTILE in line for line in logs.output), "the id was not logged")


class TheCompoundSelectionTest(unittest.TestCase):

    def test_a_job_removed_while_it_waited_is_not_quoted(self):
        manager = mock.MagicMock()
        manager.return_value.loadJobInstance.return_value = None
        with mock.patch.object(CompoundSuggestionServlet, "JobInformationManager", manager):
            with self.assertRaises(UserWarning) as caught:
                CompoundSuggestionServlet.runCompoundSuggestion(HOSTILE)
        self.assertEqual(str(caught.exception), "This job was not found at database.")

    def test_its_failure_text_is_escaped(self):
        """errorMessage reaches showErrorMessage, which inserts it as HTML."""
        queue = mock.MagicMock()
        queue.check_status.return_value = JobStatus.FAILED
        queue.get_error_message.return_value = "no card for " + HOSTILE
        request = mock.MagicMock()
        request.form = {"jobID": "echoJob01"}
        request.cookies = {}
        response = _Response()
        with mock.patch.object(CompoundSuggestionServlet, "UserSessionManager", mock.MagicMock()), \
                mock.patch.object(CompoundSuggestionServlet, "_requireJobAccess", mock.MagicMock()):
            CompoundSuggestionServlet.compoundSuggestionStatus(request, response, queue)
        self.assertEqual(response.content["errorMessage"],
                         "AI compound selection failed: no card for " + SERVER_ESCAPED)


# ----------------------------------------------------------------------------
# A converter's answer is text, escaped once, by the page
# ----------------------------------------------------------------------------

def _answer(exception, **kwargs):
    response = _Response()
    try:
        raise exception
    except Exception as ex:
        handleException(response, ex, "S.py", "f", **kwargs)
    return response.content


class AConverterAnswerIsTextTest(unittest.TestCase):

    def test_the_flag(self):
        self.assertIs(_answer(InputFileError("x"))["extra"]["plain_text"], True)
        self.assertIs(_answer(ValueError("x"))["extra"]["plain_text"], False)
        self.assertIs(_answer(ValueError("x"), plainText=True)["extra"]["plain_text"], True)

    def test_every_converter_marks_its_answer(self):
        bed = Response()
        job = mock.MagicMock()
        job.validateInput.side_effect = Exception("Distance " + HOSTILE)
        Bed2GenesServlet.fromBEDtoGenes_STEP2(job, None, False, bed)
        mirna = Response()
        MiRNA2GenesServlet.fromMiRNAtoGenes_STEP2(job, None, False, mirna, {})
        more = Response()
        job.targetExpressionFile = None
        MOREServlet.fromMOREtoGenes_STEP2(job, None, more, {})
        for name, answer in (("regions", bed), ("miRNA", mirna), ("MORE", more)):
            with self.subTest(converter=name):
                self.assertIs(answer.content["success"], False)
                self.assertIs(answer.content["extra"]["plain_text"], True)
        self.assertIn("Distance " + HOSTILE, bed.content["message"], "the text is left to the page")

    def test_a_crash_of_the_mirna_scoring_step_is_not_escaped_on_the_server(self):
        """The page encodes it, once: Step 1 always has, and the tool page does by the flag."""
        answer = _miRNAToolAnswer(CRASH_ROWS)
        self.assertEqual(answer.status, 400)
        self.assertIs(answer.content["extra"]["input_error"], False, "a crash keeps Report error")
        self.assertIs(answer.content["extra"]["plain_text"], True)
        self.assertIn("could not convert string to float: '%s'" % HOSTILE, answer.content["message"])
        self.assertNotIn("&lt;", answer.content["message"])


# ----------------------------------------------------------------------------
# The client, under node
# ----------------------------------------------------------------------------

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
/* What Ext 4.2's htmlEncode does; ext-all.js itself needs a DOM. */
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const Ext = {getCmp() { return dialog; },
             form: {action: {Action: {CLIENT_INVALID: "client"}}},
             String: {htmlEncode(text) { return String(text).replace(/[&<>"']/g, function (c) { return entities[c]; }); }}};
console.error = console.warn = console.info = function () {};
var debugging = false, ignoreOtherErrors = false;
function unreadableAnswerMessage() { return "unreadable"; }
function deleteCredentials() {}
%(client)s
const results = {};
const bodies = %(bodies)s;
for (const name of Object.keys(bodies)) {
    for (const how of ["ajax", "extjs"]) {
        for (const key of Object.keys(nodes)) { delete nodes[key]; }
        ignoreOtherErrors = false;
        const responseText = JSON.stringify(bodies[name]);
        if (how === "ajax") { ajaxErrorHandler({responseText: responseText}); }
        else { extJSErrorHandler(null, {failureType: "server", response: {responseText: responseText}}); }
        results[name + "." + how] = {title: nodes["#messageDialogTitle.html"], body: nodes["#messageDialogBody.html"]};
    }
}
process.stdout.write(JSON.stringify(results));
"""

STEP1_HARNESS = r"""
%(client)s
const answers = %(answers)s;
const results = {};
for (const name of Object.keys(answers)) {
    results[name] = step1FailureReason({responseText: JSON.stringify(answers[name])});
}
process.stdout.write(JSON.stringify(results));
"""

STEP2_HARNESS = r"""
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const Ext = {create() { return {}; },
             String: {htmlEncode(text) { return String(text).replace(/[&<>"']/g, function (c) { return entities[c]; }); }}};
const omicName = %(omicName)s;
const numberOfClusters = [];
%(combo)s);
const view = {};
(function () {
    %(card)s;
}).call(view);
const card = view._renderOmicCard({
    omicName: omicName, omicHeader: ["#id", "A_1", "A_2"],
    replicateDetection: {sampleHeader: ["A"], mapping: [0, 0], groups: [[0, 1]], status: "complete", unmatched: []}});
process.stdout.write(JSON.stringify({combo: numberOfClusters[0], card: card}));
"""


@unittest.skipUnless(HAS_NODE, "node is not installed")
class ThePageShowsItAsTextTest(unittest.TestCase):
    """The real ajaxErrorHandler, extJSErrorHandler, splitServerError and showMessage
    on the server's real answers, down to what goes into the dialog's body."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="paintomics_echo_dialog_") + "/"
        try:
            bedJob = mock.MagicMock()
            bedJob.validateInput.side_effect = Exception("The search used a Distance of " + HOSTILE + " kb.")
            bed = Response()
            Bed2GenesServlet.fromBEDtoGenes_STEP2(bedJob, None, False, bed)
            mode = Response()
            with mock.patch.object(Bed2GenesServlet, "UserSessionManager", mock.MagicMock()), \
                    mock.patch.object(Bed2GenesServlet, "CLIENT_TMP_DIR", tmp):
                Bed2GenesServlet.fromBEDtoGenes_STEP1(mock.MagicMock(cookies={}), mode, None, "echoJob02",
                                                      tmp, exampleMode=HOSTILE)
            more = _Response()
            MOREServlet.fromMOREtoGenes_STEP1(mock.MagicMock(cookies={}), more, None, "echoJob03", tmp,
                                              exampleMode=HOSTILE)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        cls.bodies = {
            "mirnaCrash": _miRNAToolAnswer(CRASH_ROWS).content,
            "bedFormValue": bed.content,
            "bedMode": mode.content,
            "moreMode": more.content,
        }
        util = read(UTIL)
        cls.results = _node(DIALOG_HARNESS % {
            "client": "\n".join(extract_function(util, name, "Util.js") for name in
                                ("showMessage", "showErrorMessage", "serverTextAsHtml", "ajaxErrorHandler",
                                 "extJSErrorHandler", "splitServerError")),
            "bodies": json.dumps(cls.bodies),
        })

    def assertTextOnce(self, body, encoded):
        self.assertIn(encoded, body)
        self.assertNotIn("<img", body)
        self.assertNotIn("&amp;lt;", body, "encoded twice: the reader sees &lt;")

    def test_a_crash_of_the_mirna_tool_is_text_on_its_own_page(self):
        for how in ("ajax", "extjs"):
            with self.subTest(handler=how):
                result = self.results["mirnaCrash." + how]
                self.assertIn("Oops..Internal error!", result["title"])
                self.assertTextOnce(result["body"], "float: &#39;" + STEP1_ENCODED + "&#39;")

    def test_a_form_value_a_converter_repeats_is_text_on_its_page(self):
        """The regions tool page read Distance and report raw from the answer."""
        for how in ("ajax", "extjs"):
            with self.subTest(handler=how):
                self.assertTextOnce(self.results["bedFormValue." + how]["body"],
                                    "Distance of " + STEP1_ENCODED + " kb.")

    def test_an_answer_the_server_escaped_is_not_encoded_again(self):
        """A converter's first step answers the form's submit, as HTML."""
        for name in ("bedMode", "moreMode"):
            with self.subTest(answer=name):
                body = self.results[name + ".extjs"]["body"]
                self.assertTextOnce(body, "Unrecognised example mode " + escape(repr(HOSTILE)))
                self.assertIn("'example/&lt;dataset-id&gt;'", body)


@unittest.skipUnless(HAS_NODE, "node is not installed")
class Step1EscapesItOnceTest(unittest.TestCase):
    """A converter's answer in the Step 1 preparation dialog (step1FailureReason)."""

    def test_a_crash_of_the_mirna_scoring_step_reads_as_the_file_had_it(self):
        source = read(JOB_CONTROLLER_JS)
        results = _node(STEP1_HARNESS % {
            "client": extract_function(source, "step1FailureReason", "JobController.js"),
            "answers": json.dumps({"mirnaCrash": _miRNAToolAnswer(CRASH_ROWS).content}),
        })
        text = results["mirnaCrash"]
        self.assertIn("could not convert string to float: '" + STEP1_ENCODED + "'", text)
        self.assertNotIn("&amp;", text, "escaped on the server and again here, so it read &lt;img")
        self.assertNotIn("<img", text)


@unittest.skipUnless(HAS_NODE, "node is not installed")
class Step2ShowsTheOmicNameAsTextTest(unittest.TestCase):
    """The cluster setting's label and input name, and the replicate card."""

    @classmethod
    def setUpClass(cls):
        source = read(STEP2_VIEWS_JS)
        cls.result = _node(STEP2_HARNESS % {
            "omicName": json.dumps(HOSTILE + '" autofocus onfocus="console.log(2)'),
            "combo": extract_block(source, "numberOfClusters.push(", "PA_Step2Views.js"),
            "card": extract_block(source, "this._renderOmicCard = function(omic)", "PA_Step2Views.js"),
        })
        cls.name = HOSTILE + '" autofocus onfocus="console.log(2)'
        cls.encoded = "&lt;img src=x onerror=console.log(1)&gt;&quot; autofocus onfocus=&quot;console.log(2)"

    def test_the_label_is_text(self):
        self.assertEqual(self.result["combo"]["fieldLabel"], self.encoded)

    def test_the_name_attribute_is_text_and_the_posted_name_is_the_omic(self):
        """ExtJS writes subTplData.name into name="..."; getValues() posts .name."""
        self.assertEqual(self.result["combo"]["subTplData"]["name"], "clusterNumber:" + self.encoded)
        self.assertEqual(self.result["combo"]["name"], "clusterNumber:" + self.name)

    def test_the_replicate_card_is_text(self):
        card = self.result["card"]
        self.assertIn('data-omic="%s"' % self.encoded, card)
        self.assertIn("<h3>%s</h3>" % self.encoded, card)
        self.assertNotIn("<img", card)
        self.assertNotIn('" autofocus', card)


if __name__ == "__main__":
    unittest.main()
