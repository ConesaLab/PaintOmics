#!/usr/bin/env python3
"""From miRNA to Genes shows its notice, says what Report does, and checks its score method.

What happened
-------------
1. A run that finished without doing all it was asked answers with a notice: no
   score without gene expression (#221), the retired selection "fc" run as positive
   correlation (#233). Neither the tool page nor Step 1 showed it. The tool page said
   "miRNA2Genes finished successfully" and nothing else, so the explanation reached
   the user only as the first line of genesToMiRNAFile.tab inside the archive.
2. With Report "Only relevant features (e.g. DE)" the miRNAs that are not relevant
   are still in regulator_associations.tab and regulator2Gene_output.tab, while the
   tool page said the rest were ignored and the Report help said "just those
   features that are differentially expressed". The files are meant to hold every
   pair: the first commit (54ff9dc3) turned both the Report filter and the cutoff
   filter into marks, leaving their 'continue' commented out side by side, and the
   pairs that are not relevant are the background the Step 1 enrichment counts. So
   the help is what changes, and a run where Report can mark nothing (no relevant
   regulators file, or none of the paired miRNAs in it) now says so. The tool page
   and the user guide said the same of the cutoff ("used for filtering the matched
   targets", "the genes that are finally reported"), which also only marks pairs.
3. The score method was never checked ("TODO: CHECK VALID SCORE METHODS"). Any value
   but the three correlations came back from getScore as None, was written as the
   score "None", and float("None") crashed the job with Report.

What this file asserts
----------------------
1. conversionNoticeAsHtml shows a notice as text; the tool page shows it first, as a
   warning; a Step 1 conversion puts it on the omic's card and says it again once
   Step 2 opens; a run with no notice reads as before;
2. under both Report choices, and under any cutoff, every pair is in the values and
   associations files and only the relevant associations differ; the help, the tool
   page and the user guide say so for both; a run where Report "DE" can mark nothing
   says why, in the answer and in the results;
3. an unknown score method is refused as an input problem that names kendall,
   spearman and pearson, before anything runs, and the four accepted values pass.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_mirna_tool_tells_and_reports_de
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob
from src.common.ServerErrorManager import InputFileError
from src.servlets import MiRNA2GenesServlet

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CLIENT = os.path.join(REPO, "PaintomicsClient", "public_html", "app")
JOB_CONTROLLER = os.path.join(CLIENT, "controller", "JobController.js")
STEP1_VIEWS = os.path.join(CLIENT, "view", "PathwayAcquisitionViews", "PA_Step1Views.js")
TOOL_PAGE = os.path.join(CLIENT, "view", "DataManagementViews", "DM_miRNA2GenesViews.js")

VALUES = [["#miRNA", "C1", "C2", "C3"],
          ["hsa-miR-1", "0.1", "0.4", "0.2"],
          ["hsa-miR-2", "0.3", "0.1", "0.9"]]
TARGETS = [["hsa-miR-1", "G1"], ["hsa-miR-1", "G2"], ["hsa-miR-2", "G2"], ["hsa-miR-2", "G3"]]
GENES = [["#gene", "C1", "C2", "C3"],
         ["G1", "1.0", "2.0", "0.5"],
         ["G2", "2.0", "1.0", "3.0"],
         ["G3", "0.4", "0.9", "0.1"]]
# Kendall on these three conditions: miR-1 with G1 +1/3, with G2 -1/3; miR-2 with G2
# +1, with G3 -1. So abs_correlation above 0.3 keeps all four pairs.
EVERY_PAIR = ["G1\tHSA-MIR-1", "G2\tHSA-MIR-1", "G2\tHSA-MIR-2", "G3\tHSA-MIR-2"]
MIR2_PAIRS = ["G2\tHSA-MIR-2", "G3\tHSA-MIR-2"]

DE_NO_LIST = (
    "Report is \"Only relevant features (e.g. DE)\", which lets only the pairs of the "
    "miRNAs in the relevant regulators file be marked as relevant, and no such file was "
    "given, so no pair was marked as relevant. Every pair read is in the results. To "
    "select pairs, add the list of relevant miRNAs, or set Report to \"All features\".")


# -- the server ----------------------------------------------------------------

class _Response(object):
    def __init__(self):
        self.status, self.content = None, None

    def setStatus(self, status):
        self.status = status

    def setContent(self, content):
        self.content = content


def write(path, rows):
    with io.open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("\t".join(row) + "\n")


def runTool(relevant=None, targets=TARGETS, genes=GENES, **options):
    """The real job and its real queue step, validation included. Storing the job is
    stubbed. relevant=None sends no relevant regulators file.

    Returns the answer and the results archive as {member without its date and
    random part: content}.
    """
    root = tempfile.mkdtemp(prefix="paintomics_mirna_tells_") + "/"
    try:
        job = MiRNA2GeneJob("tellsjob", None, root)
        job.selection_method, job.cutoff = "abs_correlation", 0.3
        for name, value in options.items():
            setattr(job, name, value)
        job.initializeDirectories()
        os.makedirs(job.getInputDir(), exist_ok=True)
        write(os.path.join(job.getInputDir(), "values.tab"), VALUES)
        write(os.path.join(job.getInputDir(), "targets.tab"), targets)
        miRNA = {"omicName": "miRNA-seq", "inputDataFile": "values.tab", "associationsFile": "targets.tab"}
        if relevant is not None:
            write(os.path.join(job.getInputDir(), "relevant.txt"), [[r] for r in relevant])
            miRNA["relevantFeaturesFile"] = "relevant.txt"
        job.geneBasedInputOmics = [miRNA]
        if genes is not None:
            write(os.path.join(job.getInputDir(), "genes.tab"), genes)
            job.geneBasedInputOmics.append({"omicName": "Gene expression", "inputDataFile": "genes.tab"})
        answer = _Response()
        with mock.patch.object(MiRNA2GenesServlet, "JobInformationManager"):
            MiRNA2GenesServlet.fromMiRNAtoGenes_STEP2(job, None, False, answer, {})
        files = {}
        if answer.content.get("success"):
            path = os.path.join(job.getOutputDir(), answer.content["compressedFileName"])
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    short = re.sub(r"[_0-9]+\.tab$", ".tab", name.replace("tellsjob_", ""))
                    files[short] = archive.read(name).decode("utf-8")
        return answer, files
    finally:
        shutil.rmtree(root, ignore_errors=True)


def pairs(text):
    return sorted(line for line in text.splitlines() if not line.startswith("#"))


def valueRows(text):
    """regulator2Gene_output.tab's rows as "GENE<tab>MIRNA", without their values."""
    return sorted("\t".join(line.split("\t")[0].split(":::"))
                  for line in text.splitlines() if not line.startswith("#"))


class ReportKeepsEveryPairTest(unittest.TestCase):
    """Report "DE" changes which pairs are marked relevant, and nothing else."""

    def test_both_reports_write_every_pair_and_differ_only_in_the_relevant_ones(self):
        everyMiRNA, allFiles = runTool(relevant=["hsa-miR-2"], report="all")
        onlyDE, deFiles = runTool(relevant=["hsa-miR-2"], report="DE")
        self.assertIs(everyMiRNA.content["success"], True, everyMiRNA.content)
        self.assertIs(onlyDE.content["success"], True, onlyDE.content)
        for name in ("regulator2Gene_output.tab", "regulator_associations.tab",
                     "regulator2Gene_relevant.tab", "genesToMiRNAFile.tab"):
            self.assertEqual(allFiles[name], deFiles[name], name)
        self.assertEqual(valueRows(deFiles["regulator2Gene_output.tab"]), EVERY_PAIR)
        self.assertEqual(pairs(deFiles["regulator_associations.tab"]), EVERY_PAIR)
        self.assertEqual(pairs(allFiles["regulator_relevant_associations.tab"]), EVERY_PAIR)
        self.assertEqual(pairs(deFiles["regulator_relevant_associations.tab"]), MIR2_PAIRS)
        # The DE column marks miR-2 under both.
        self.assertEqual(pairs(deFiles["regulator2Gene_relevant.tab"]), MIR2_PAIRS)
        self.assertIsNone(onlyDE.content["notice"])

    def test_report_de_with_no_relevant_regulators_file_says_why_nothing_is_relevant(self):
        """It answered success with an empty relevant associations file and no word."""
        answer, files = runTool(report="DE")
        self.assertIs(answer.content["success"], True, answer.content)
        self.assertEqual(files["regulator_relevant_associations.tab"], "")
        self.assertEqual(answer.content["notice"], DE_NO_LIST)
        self.assertEqual(files["genesToMiRNAFile.tab"].splitlines()[0], "# " + DE_NO_LIST)
        self.assertEqual(pairs(files["regulator_associations.tab"]), EVERY_PAIR)

    def test_a_list_that_names_none_of_the_paired_miRNAs_is_said_with_the_count(self):
        answer, _files = runTool(relevant=["hsa-miR-9"], report="DE")
        self.assertIn("and none of the 2 miRNAs paired with a target is in that file, so no pair "
                      "was marked as relevant. Every pair read is in the results. Check that the "
                      "file names the miRNAs as the quantification file does, or set Report to "
                      "\"All features\".", answer.content["notice"])
        answer, _files = runTool(relevant=[], report="DE")
        self.assertIn("none of the 2 miRNAs paired with a target is in that file", answer.content["notice"])

    def test_one_miRNA_is_said_in_agreement(self):
        answer, _files = runTool(relevant=["hsa-miR-9"], report="DE", targets=[["hsa-miR-1", "G1"]])
        self.assertIn("and the one miRNA paired with a target is not in that file,", answer.content["notice"])

    def test_nothing_is_said_where_report_could_mark_pairs(self):
        for relevant, report in ((None, "all"), (["hsa-miR-9"], "all"), (["hsa-miR-1"], "DE")):
            with self.subTest(relevant=relevant, report=report):
                answer, _files = runTool(relevant=relevant, report=report)
                self.assertIs(answer.content["success"], True, answer.content)
                self.assertIsNone(answer.content["notice"])

    def test_it_joins_a_notice_already_there(self):
        """selection "fc" is run as positive correlation and says so; both are said."""
        answer, files = runTool(report="DE", selection_method="fc")
        self.assertTrue(answer.content["notice"].startswith("The selection method \"fc\""), answer.content)
        self.assertTrue(answer.content["notice"].endswith(" " + DE_NO_LIST), answer.content)
        self.assertEqual(files["genesToMiRNAFile.tab"].splitlines()[0], "# " + answer.content["notice"])

    def test_a_run_with_no_score_keeps_its_own_notice_alone(self):
        """That notice already says no pair was marked as relevant, and why."""
        answer, _files = runTool(report="DE", genes=None)
        self.assertTrue(answer.content["notice"].startswith("No score could be computed"), answer.content)
        self.assertNotIn("Report is", answer.content["notice"])


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _reportHelp():
    source = _read(STEP1_VIEWS)
    start = source.index('itemId: "reportMethodField"')
    end = source.index('itemId: "scoreMethodField"', start)
    block = re.sub(r"//[^\n]*", "", source[start:end])
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block[block.index("helpTip:"):]))


class TheHelpSaysWhatReportDoesTest(unittest.TestCase):

    def test_the_report_help_does_not_say_the_others_are_left_out(self):
        text = _reportHelp()
        self.assertNotIn("just those features", text)
        self.assertIn("Either way every pair stays in the results with its values: the other "
                      "miRNAs are not dropped, they are never marked relevant.", text)
        self.assertIn("without that file no pair is marked.", text)

    def test_the_tool_page_does_not_say_the_rest_is_ignored(self):
        # What the browser shows: the comment that quotes the old wording goes.
        page = re.sub(r"/\*.*?\*/", "", _read(TOOL_PAGE), flags=re.S)
        self.assertFalse("ignoring the rest" in page, "the tool page still says the rest is ignored")
        self.assertIn("The other miRNAs are not dropped: their pairs stay in the results with "
                      "their values, and are never marked relevant.", page)


def _withoutComments(source):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _helpOf(source, itemId):
    """The helpTip string of the field with this itemId, comments left out."""
    start = source.index('itemId: "%s"' % itemId)
    at = source.index("helpTip:", start)
    end = source.index("\n\t\t\t\t\t\t}", at)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', _withoutComments(source[at:end])))


class TheHelpSaysWhatTheCutoffDoesTest(unittest.TestCase):
    """The cutoff, like Report, marks pairs as relevant and drops none (STEP 5.2), while
    the tool page said the gene expression file was "used for filtering the matched
    targets" and that the cutoff "determines the selection of the genes that are
    finally reported", one bullet below the one saying no pair is dropped."""

    def test_runs_with_any_cutoff_write_the_same_pairs(self):
        """What the help now says, checked on a run: a cutoff nothing passes and one
        everything passes write the same pairs to every file but the relevant ones."""
        none, noneFiles = runTool(cutoff=0.99)
        every, everyFiles = runTool(cutoff=0.0)
        self.assertIs(none.content["success"], True, none.content)
        self.assertIs(every.content["success"], True, every.content)
        self.assertEqual(valueRows(noneFiles["regulator2Gene_output.tab"]), EVERY_PAIR)
        self.assertEqual(pairs(noneFiles["regulator_associations.tab"]), EVERY_PAIR)
        self.assertEqual(noneFiles["regulator_associations.tab"], everyFiles["regulator_associations.tab"])
        self.assertEqual(pairs(noneFiles["regulator_relevant_associations.tab"]), MIR2_PAIRS)
        self.assertEqual(pairs(everyFiles["regulator_relevant_associations.tab"]), EVERY_PAIR)

    def test_the_tool_page_does_not_say_the_cutoff_drops_targets(self):
        page = _withoutComments(_read(TOOL_PAGE))
        for wording in ("used for filtering the matched targets", "finally reported",
                        "kept or dropped"):
            with self.subTest(wording=wording):
                self.assertNotIn(wording, page)
        self.assertIn("the cutoff on the correlation decides which pairs are marked as relevant. "
                      "The other pairs are not dropped: they stay in the results with their scores.",
                      page)

    def test_the_step1_card_does_not_say_the_cutoff_drops_pairs(self):
        source = _read(STEP1_VIEWS)
        geneExpression = _helpOf(source, "rnaseqauxFileSelector")
        cutoff = _helpOf(source, "cutoffField")
        self.assertNotIn("filter", geneExpression)
        self.assertIn("every pair stays in the results.", geneExpression)
        self.assertNotIn("is kept", cutoff)
        self.assertIn("With negative correlation a pair is marked relevant when its correlation is "
                      "below the cutoff", cutoff)
        self.assertTrue(cutoff.endswith("A pair that does not pass stays in the results with its score."),
                        cutoff)

    def test_the_user_guide_does_not_say_the_targets_are_filtered(self):
        guide = _read(os.path.join(REPO, "docs", "2_1_accepted_input.md"))
        for wording in ("used to filter the predicted targets", "filtered on it",
                        "default selection keeps"):
            with self.subTest(wording=wording):
                self.assertNotIn(wording, guide)
        self.assertIn("the other pairs are not dropped: they\n  stay in the results with their scores.",
                      guide)


class TheScoreMethodIsCheckedTest(unittest.TestCase):

    def test_an_unknown_score_method_is_refused_by_name_before_anything_runs(self):
        """On master "max_r" reached getScore, which answered None, and the job then
        crashed on float("None") with Report error."""
        for method in ("max_r", "Kendall", "none", ""):
            with self.subTest(method=method):
                answer, files = runTool(score_method=method)
                self.assertEqual(answer.status, 400)
                self.assertIs(answer.content["success"], False)
                self.assertIs((answer.content.get("extra") or {}).get("input_error"), True, answer.content)
                self.assertIn("The score method %s is not one this tool has. Use kendall, "
                              "spearman or pearson." % ("\"%s\"" % method if method else "sent"),
                              answer.content["message"])
                self.assertNotIn("Errors detected in input files", answer.content["message"])
                self.assertEqual(files, {})

    def test_it_is_refused_where_no_score_is_computed_too(self):
        """Without gene expression the value did not crash, but it is no method this
        tool has, as #233 decided for the selection method."""
        answer, _files = runTool(score_method="max_r", genes=None)
        self.assertIs((answer.content.get("extra") or {}).get("input_error"), True, answer.content)

    def test_a_score_method_that_is_not_plain_text_is_not_repeated(self):
        answer, _files = runTool(score_method="<img src=x onerror=alert(1)>")
        self.assertIn("The score method sent is not one this tool has.", answer.content["message"])
        self.assertNotIn("<img", answer.content["message"])

    def test_the_accepted_score_methods_pass_validation(self):
        """The three the form offers, and "fc", which the form never offered (its
        option was commented out from the first commit) but miRNA2Target has always
        taken, and which runs with no score and says so, as #221 made it."""
        job = MiRNA2GeneJob("scorejob", None, tempfile.gettempdir() + "/")
        job.geneBasedInputOmics = [{"omicName": "miRNA-seq", "inputDataFile": "values.tab",
                                    "isExample": True}]
        for method in ("kendall", "spearman", "pearson", "fc"):
            with self.subTest(method=method):
                job.score_method = method
                self.assertTrue(job.validateInput())

    def test_every_accepted_method_runs(self):
        for method in ("kendall", "spearman", "pearson", "fc"):
            with self.subTest(method=method):
                answer, _files = runTool(score_method=method)
                self.assertIs(answer.content["success"], True, answer.content)

    def test_the_form_offers_exactly_the_three_correlations(self):
        source = _read(STEP1_VIEWS)
        start = source.index('itemId: "scoreMethodField"')
        block = source[start:source.index("helpTip:", start)]
        self.assertEqual(sorted(re.findall(r'\[\s*"[^"]*"\s*,\s*"([^"]*)"\s*\]', block)),
                         ["kendall", "pearson", "spearman"])

    def test_the_refusal_is_typed_as_one(self):
        """On its own, as the selection method's is: no file is at fault."""
        job = MiRNA2GeneJob("scorejob", None, tempfile.gettempdir() + "/")
        job.geneBasedInputOmics = [{"omicName": "miRNA-seq", "inputDataFile": "values.tab",
                                    "isExample": True}]
        job.score_method = "max_r"
        with self.assertRaises(InputFileError) as refused:
            job.validateInput()
        self.assertEqual(str(refused.exception), "The score method \"max_r\" is not one this tool "
                         "has. Use kendall, spearman or pearson.")


# -- the pages -----------------------------------------------------------------

def _block(source, header, start=0):
    """`header` and the brace-matched block after it, searched from `start`."""
    at = source.find(header, start)
    if at == -1:
        raise AssertionError("%r is missing from JobController.js" % header)
    depth = 0
    for index in range(source.index("{", at + len(header) - 1), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[at:index + 1]
    raise AssertionError("unbalanced braces after %r" % header)


HOSTILE = "No score: <img src=x onerror=alert(1)> & [b]bold[/b] \"q\" 'a'"
HOSTILE_HTML = ("No score: &lt;img src=x onerror=alert(1)&gt; &amp; &#91;b]bold&#91;/b] "
                "&quot;q&quot; &#39;a&#39;")

HARNESS = r"""
const window = {location: {pathname: "/", host: "paintomics.org"}};
const console = {log() {}, info() {}, warn() {}, error() {}};
const SERVER_URL_PA_STEP1 = "/pa_step1", SERVER_URL_PA_EXAMPLE_STEP1 = "/pa_step1/example";
const SERVER_URL_DM_FROMBED2GENES = "/dm_fromBEDtoGenes", SERVER_URL_DM_FROMMORE2GENES = "/dm_MORE";
const SERVER_URL_DM_FROMMIRNA2GENES = "/dm_fromMiRNAtoGenes", SERVER_URL_DM_DOWNLOAD_FILE = "dm_downloadFile";
const SERVER_URL_DM_EXAMPLE_FROMBED2GENES = "/x", SERVER_URL_DM_EXAMPLE_FROMMIRNA2GENES = "/x",
      SERVER_URL_DM_EXAMPLE_FROMMORE2GENES = "/x";
let shown = [];
const show = (type) => (title, data) => shown.push({type, title, message: (data || {}).message || "",
                                                    closeTimeout: (data || {}).closeTimeout || 0,
                                                    showButton: !!(data || {}).showButton});
const showInfoMessage = show("info"), showSuccessMessage = show("success"),
      showWarningMessage = show("warning"), showErrorMessage = show("error");
function withExampleScenario(url) { return url; }
function extJSErrorHandler() { shown.push({type: "extjs-error"}); }
function ajaxErrorHandler() { shown.push({type: "ajax-error"}); }
function showInvalidStep1FormMessage() { shown.push({type: "invalid-form"}); }
function showStep1PreparationFailure() { shown.push({type: "preparation-failure"}); }
function CompoundSet() { this.loadFromJSON = function () {}; }
const Ext = {widget: (config) => config.xtype === "form" ? {
                 getForm: () => ({submit: (o) => o.success(null, {response: {responseText: JSON.stringify({jobID: "C1"})}})}),
                 queryById: () => null} : config,
             destroy() {}, String: {htmlEncode: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")},
             form: {action: {Action: {CLIENT_INVALID: "client"}}}};

%(functions)s

function Controller() {
%(methods)s
}

// Any setter the Step 1 callback calls on the job model is a no-op.
const model = () => new Proxy({}, {get: (t, k) => (k in t ? t[k] : () => {}),
                                   set: (t, k, v) => { t[k] = v; return true; }});

function toolPage(answer) {
    shown = [];
    const c = new Controller();
    c.checkJobStatus = (jobID, jobView, callback) => callback(answer, jobID, jobView);
    const form = {submit: (o) => o.success(null, {response: {responseText: JSON.stringify({jobID: "T1"})}})};
    c.fromMiRNA2GenesOnFormSubmitHandler({isExampleMode: () => false, checkForm: () => true,
        getComponent: () => ({down: () => ({getForm: () => form})})});
    return shown.filter((m) => m.type !== "info");
}

// A Step 1 form with one miRNA card: its conversion job C1, then the pathway job P1.
function step1(conversion, options) {
    shown = [];
    options = options || {};
    const added = [], removed = [];
    const nameField = {getValue: () => "miRNA <b>seq</b>"};
    const itemsContainer = {query: () => [], queryById: (id) => id === "omicNameField" ? nameField : null};
    // Only the boxes a handler adds to the card are kept; _restoreElements puts the
    // form's own container back too.
    const subview = {cls: "omicbox miRNABasedOmic", setContent: () => {},
                     add: (w) => { if (w && w.xtype === "box") { added.push(w); } },
                     remove: (w) => removed.push(w),
                     queryById: (id) => id === "itemsContainer" ? itemsContainer
                                        : (added.find((w) => w.itemId === id) || null)};
    const jobView = {isExampleMode: () => false, checkForm: () => true, getModel: model,
                     getComponent: () => ({down: () => ({getForm: () => ({
                         submit: (o) => o.success(null, {response: {responseText: JSON.stringify({jobID: "P1"})}})})})})};
    if (options.stale) { jobView.step1ConversionNotices = ["<b>old</b>: stale"]; }
    const c = new Controller();
    c.updateStoredApplicationData = () => {};
    c.showJobInstance = () => {};
    c.checkJobStatus = (jobID, view, callback, other) =>
        callback(jobID === "C1" ? conversion : {jobID: "P1", matchedMetabolites: []}, jobID, view, other);
    if (options.direct) {
        c.step1OnFormSubmitHandler(jobView);
    } else {
        c.step1ComplexFormSubmitHandler(jobView, [subview]);
    }
    return {shown, card: added.map((w) => w.html || ""), cardIds: added.map((w) => w.itemId),
            removed: removed.length, left: jobView.step1ConversionNotices};
}

const NOTICE = %(hostile)s;
const out = {
    asHtml: [null, undefined, "", "  ", NOTICE, "Plain words."].map((n) => conversionNoticeAsHtml({notice: n})),
    asHtmlNoAnswer: [conversionNoticeAsHtml(null), conversionNoticeAsHtml(undefined), conversionNoticeAsHtml({})],
    toolWithNotice: toolPage({jobID: "T1", compressedFileName: "r.zip", notice: NOTICE}),
    toolWithout: toolPage({jobID: "T1", compressedFileName: "r.zip", notice: null}),
    step1WithNotice: step1({jobID: "C1", mainOutputFileName: "m.tab", notice: NOTICE}),
    step1Without: step1({jobID: "C1", mainOutputFileName: "m.tab", notice: null}),
    step1Direct: step1(null, {direct: true, stale: true}),
};
console.log = globalThis.console.log;
console.log(JSON.stringify(out));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class ThePagesShowTheNoticeTest(unittest.TestCase):
    """The real handlers, lifted from JobController.js and run in node with stubs."""

    @classmethod
    def setUpClass(cls):
        source = _read(JOB_CONTROLLER)
        functions = [_block(source, "function %s(" % name)
                     for name in ("conversionNoticeAsHtml", "step1OmicLabelAsHtml")]
        methods = [_block(source, "this.%s = function" % name)
                   for name in ("beginStep1Submission", "endStep1Submission",
                                "step1ComplexFormSubmitHandler", "step1OnFormSubmitHandler",
                                "fromMiRNA2GenesOnFormSubmitHandler")]
        script = HARNESS % {"functions": "\n\n".join(functions),
                            "methods": ";\n".join(methods) + ";",
                            "hostile": json.dumps(HOSTILE)}
        directory = tempfile.mkdtemp(prefix="paintomics-mirna-notice-")
        try:
            path = os.path.join(directory, "check.js")
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise AssertionError("node failed:\n%s" % done.stderr)
            cls.out = json.loads(done.stdout)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_a_notice_is_shown_as_text(self):
        self.assertEqual(self.out["asHtml"], ["", "", "", "", HOSTILE_HTML, "Plain words."])
        self.assertEqual(self.out["asHtmlNoAnswer"], ["", "", ""])

    def test_the_tool_page_says_the_notice_first_as_a_warning(self):
        """It said "miRNA2Genes finished successfully" and nothing about the notice."""
        [dialog] = self.out["toolWithNotice"]
        self.assertEqual((dialog["type"], dialog["title"]), ("warning", "miRNA2Genes finished, with a note"))
        self.assertTrue(dialog["message"].startswith(HOSTILE_HTML + "</br></br>Click on the link"), dialog)
        self.assertIn("Download files.</a>", dialog["message"])
        self.assertNotIn("<img", dialog["message"])

    def test_the_tool_page_without_a_notice_reads_as_before(self):
        [dialog] = self.out["toolWithout"]
        self.assertEqual((dialog["type"], dialog["title"]), ("success", "miRNA2Genes finished successfully"))
        self.assertTrue(dialog["message"].startswith("Click on the link below to download your files."))

    def test_step1_puts_the_notice_on_the_card(self):
        run = self.out["step1WithNotice"]
        self.assertEqual(run["cardIds"], ["conversionNotice"])
        self.assertIn("<b>Note:</b> " + HOSTILE_HTML + "</p>", run["card"][0])
        self.assertNotIn("<img", run["card"][0])
        conversion = [m for m in run["shown"] if m["type"] == "info" and "conversion job C1" in m["title"]]
        self.assertTrue(conversion[-1]["title"].endswith("finished successfully, with a note."), conversion)

    def test_step1_says_it_again_once_step2_opens(self):
        """The analysis goes on by itself, and Step 2 replaces the card."""
        run = self.out["step1WithNotice"]
        last = run["shown"][-1]
        self.assertEqual((last["type"], last["title"]), ("warning", "Your files were prepared, with a note"))
        self.assertEqual(last["message"], "<b>miRNA &lt;b&gt;seq&lt;/b&gt;</b>: " + HOSTILE_HTML)
        self.assertTrue(last["showButton"])
        self.assertEqual(last["closeTimeout"], 0)
        self.assertEqual(run["left"], [])

    def test_step1_without_a_notice_reads_as_before(self):
        run = self.out["step1Without"]
        self.assertEqual(run["cardIds"], [])
        last = run["shown"][-1]
        self.assertEqual((last["type"], last["title"], last["closeTimeout"]), ("success", "Done", 0.5))
        self.assertFalse([m for m in run["shown"] if m["type"] == "warning"])

    def test_a_submission_that_converts_nothing_repeats_no_old_notice(self):
        run = self.out["step1Direct"]
        self.assertEqual(run["shown"][-1]["title"], "Done")
        self.assertEqual(run["left"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
