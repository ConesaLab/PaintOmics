#!/usr/bin/env python3
"""The From miRNA to Genes tool page shows its correlation box, as a Step 1 card does.

Why this exists
---------------
The tool page (DM_miRNA2GenesViews.js) builds the same card as a Step 1 Pairwise
miRNA card, MiRNAOmicSubmittingPanel, with allowToogle false, from the first commit
(54ff9dc3). That option hid "My features are already mapped to Gene IDs", which has
no place on a tool that maps to genes, and also the correlation box ("Automatically
select relevant associations using correlation"). The settings that box turns on,
the gene expression file, Report, Score method, Selection method and Filter cutoff,
stayed on the page greyed out, with no way to turn them on, while the page's help
described them. Only "Load example" ticked the box, by script.

The card now takes allowCorrelationToogle on its own; unset, it follows allowToogle,
so Step 1 cards are unchanged. The tool page passes it true, and passes
allowOtherOmicFile false (see 3 below). The box starts
unticked, as on Step 1, so a run whose box is left alone sends what it always sent
(its settings are disabled and not submitted, and a relevant associations file is
required). The page's help now names the box, the relevant associations file and
the settings by the form's labels.

What this file asserts
----------------------
1. Run in node with Ext stubbed, the real card and the real tool page: the tool page's
   card shows the correlation box and not the "already mapped" box, in both the
   upload and the example form; Step 1 cards show both, as before;
2. the tool page's card has the same settings, with the same defaults, as a Step 1
   card, and they start disabled;
3. the tool page's gene expression row has no "Use a file from other omic", which
   Step 1 cards keep. It picks a file chosen on another card, which only Step 1's
   submit copies into the request; the tool page submits its form as it is, so the
   server read the card's own miRNA values file instead and refused the run;
4. the help names the box by its label, the relevant associations file, and Report,
   Score method, Selection method and Filter cutoff, and no longer calls the gene
   expression file one of "Two optional files".

Usage:
    cd PaintomicsServer
    python -m src.tests.test_mirna_tool_page_shows_its_settings
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CLIENT = os.path.join(REPO, "PaintomicsClient", "public_html", "app")
STEP1_VIEWS = os.path.join(CLIENT, "view", "PathwayAcquisitionViews", "PA_Step1Views.js")
TOOL_PAGE = os.path.join(CLIENT, "view", "DataManagementViews", "DM_miRNA2GenesViews.js")

BOX_LABEL = "Automatically select relevant associations using correlation"
OTHER_OMIC = "Use a file from other omic"


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _block(source, header):
    """`header` and the brace-matched block after it."""
    at = source.index(header)
    depth = 0
    for index in range(source.index("{", at + len(header) - 1), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[at:index + 1]
    raise AssertionError("unbalanced braces after %r" % header)


HARNESS = r"""
const window = {location: {pathname: "/"}};
const SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD = "/examples";
const Ext = {
    widget: (config) => config,
    create: () => ({}),
    String: {htmlEncode: (s) => String(s)},
    form: {field: {Base: {prototype: {listeners: {boxready: function () {}}}}}},
};
const $ = () => ({scrollTop() {}});
function setExampleLabel() {}
function View() {
    this.component = null;
    this.getComponent = function () {
        if (this.component === null) { this.initComponent(); }
        return this.component;
    };
}
function DefaultSubmittingPanel() {}
DefaultSubmittingPanel.prototype = new View();

%(panel)s
MiRNAOmicSubmittingPanel.prototype = new DefaultSubmittingPanel();
const RealPanel = MiRNAOmicSubmittingPanel;

// Every item of a widget config, depth first.
function walk(config, out) {
    out = out || [];
    if (config && typeof config === "object") {
        out.push(config);
        (config.items || []).forEach((item) => walk(item, out));
    }
    return out;
}

function describe(card) {
    const items = walk(card);
    const byId = (id) => items.find((item) => item.itemId === id) || null;
    const value = (id) => (byId(id) || {}).value;
    const box = byId("toogleCorrOptions");
    return {
        correlationBoxHidden: box ? !!box.hidden : null,
        correlationBoxHtml: box ? box.html : null,
        mappedBoxHidden: byId("toogleMapRegions") ? !!byId("toogleMapRegions").hidden : null,
        settingsDisabled: !!(byId("itemsContainerCorrOptions") || {}).disabled,
        geneExpressionMenu: ((byId("rnaseqauxFileSelector") || {}).extraButtons || []).map((b) => b.text),
        defaults: {report: value("reportMethodField"), score: value("scoreMethodField"),
                   selection: value("selectionMethodField"), cutoff: value("cutoffField")},
        names: items.filter((item) => typeof item.name === "string" && /_(report|score_method|selection_method|cutoff)$/.test(item.name))
                    .map((item) => item.name),
    };
}

// The tool page, its own file, run as the browser runs it.
const passed = [];
MiRNAOmicSubmittingPanel = function (nElem, options) {
    passed.push(options);
    return new RealPanel(nElem, options);
};
%(toolPage)s
const page = new DM_miRNA2GenesJobView();
const pageConfig = page.initComponent();
const toolCard = walk(pageConfig).find((item) => item.cls === "omicbox miRNABasedOmic");

// "Load example" builds a new card with the options it passes; only those are kept.
const examplePassed = [];
MiRNAOmicSubmittingPanel = function (nElem, options) {
    examplePassed.push(options);
    const real = new RealPanel(nElem, options);
    real.setExampleMode = function () {};
    return real;
};
const omicForm = {items: {get: () => null}, remove() {}, add() {}, doLayout() {},
                  getEl: () => ({dom: {offsetTop: 0}})};
page.getComponent = () => ({queryById: () => omicForm});
page.setExampleModeHandler();

const out = {
    tool: describe(toolCard),
    toolOptions: passed,
    example: describe(new RealPanel(0, examplePassed[0]).getComponent()),
    step1: describe(new RealPanel(1).getComponent()),
    step1Pairwise: describe(new RealPanel(2, {regulatoryMethod: "pairwise"}).getComponent()),
    // An explicit false still hides it, and unset follows allowToogle as before.
    toggledOff: describe(new RealPanel(3, {allowCorrelationToogle: false}).getComponent()),
    followsAllowToogle: describe(new RealPanel(4, {allowToogle: false}).getComponent()),
    otherOmicFileOff: describe(new RealPanel(5, {allowOtherOmicFile: false}).getComponent()),
};
console.log(JSON.stringify(out));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheToolPageShowsTheBoxTest(unittest.TestCase):
    """The real card from PA_Step1Views.js and the real tool page, run in node."""

    @classmethod
    def setUpClass(cls):
        script = HARNESS % {"panel": _block(_read(STEP1_VIEWS), "function MiRNAOmicSubmittingPanel("),
                            "toolPage": _read(TOOL_PAGE)}
        directory = tempfile.mkdtemp(prefix="paintomics-mirna-settings-")
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

    def test_the_tool_page_shows_the_correlation_box(self):
        """It was hidden, so Report, score method and cutoff could not be turned on."""
        self.assertIs(self.out["tool"]["correlationBoxHidden"], False)
        self.assertIn(BOX_LABEL, self.out["tool"]["correlationBoxHtml"])

    def test_the_tool_page_still_hides_the_already_mapped_box(self):
        self.assertIs(self.out["tool"]["mappedBoxHidden"], True)

    def test_the_example_form_shows_it_too(self):
        self.assertEqual(len(self.out["toolOptions"]), 1)
        self.assertIs(self.out["example"]["correlationBoxHidden"], False)
        self.assertIs(self.out["example"]["mappedBoxHidden"], True)

    def test_step1_cards_are_unchanged(self):
        for card in ("step1", "step1Pairwise"):
            with self.subTest(card=card):
                self.assertIs(self.out[card]["correlationBoxHidden"], False)
                self.assertIs(self.out[card]["mappedBoxHidden"], False)

    def test_the_option_can_still_hide_it_and_follows_allowToogle_when_unset(self):
        self.assertIs(self.out["toggledOff"]["correlationBoxHidden"], True)
        self.assertIs(self.out["followsAllowToogle"]["correlationBoxHidden"], True)

    def test_the_settings_and_defaults_are_the_step1_cards(self):
        self.assertEqual(self.out["tool"]["defaults"], self.out["step1"]["defaults"])
        self.assertEqual(self.out["tool"]["defaults"],
                         {"report": "all", "score": "kendall", "selection": "negative_correlation",
                          "cutoff": -0.5})
        self.assertEqual(self.out["tool"]["names"],
                         ["omic0_report", "omic0_score_method", "omic0_selection_method", "omic0_cutoff"])

    def test_the_tool_page_has_no_file_from_other_omic(self):
        """Its plain form submit never sends a file picked from another card: the
        server read the card's own miRNA values file instead and refused the run."""
        for card in ("tool", "example", "otherOmicFileOff"):
            with self.subTest(card=card):
                self.assertEqual(self.out[card]["geneExpressionMenu"], [])

    def test_step1_cards_keep_the_file_from_other_omic(self):
        for card in ("step1", "step1Pairwise", "followsAllowToogle"):
            with self.subTest(card=card):
                self.assertEqual(self.out[card]["geneExpressionMenu"], [OTHER_OMIC])

    def test_the_settings_start_disabled_so_an_untouched_run_sends_what_it_did(self):
        """Disabled fields are not submitted: with the box left unticked the tool
        sends no Report, score method, selection method or cutoff, as before."""
        self.assertIs(self.out["tool"]["settingsDisabled"], True)
        self.assertIs(self.out["step1"]["settingsDisabled"], True)


def _helpText():
    """What the tool page's help says, without comments or tags."""
    page = re.sub(r"/\*.*?\*/", "", _read(TOOL_PAGE), flags=re.S)
    page = re.sub(r"(?m)^\s*//[^\n]*$", "", page)
    # From the quote that opens the help's first string, to the form.
    start = page.rindex("'", 0, page.index('<div id="about"'))
    text = "".join(re.findall(r"'((?:[^'\\]|\\.)*)'", page[start:page.index("xtype: 'form'")]))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text.replace("&rarr;", "->").replace("\\'", "'")))


class TheHelpMatchesTheFormTest(unittest.TestCase):

    def setUp(self):
        self.help = _helpText()

    def test_it_names_the_box_by_its_label(self):
        self.assertIn("after ticking " + BOX_LABEL + ".", self.help)
        self.assertIn("With " + BOX_LABEL + " ticked, these settings of the form decide which pairs "
                      "are marked relevant.", self.help)
        # The same words as the box itself.
        self.assertIn('for="\' + this.namePrefix + \'_corrOptions">' + BOX_LABEL + ".</label>",
                      _read(STEP1_VIEWS))

    def test_it_says_one_of_the_two_is_needed(self):
        """The form refuses a run with neither (isValid); the help called the gene
        expression one of "Two optional files" and never named the other."""
        self.assertNotIn("Two optional files", self.help)
        self.assertIn("To decide which miRNA -> target pairs are relevant, it also needs one of these:",
                      self.help)
        self.assertIn("A list of the pairs you already know to be relevant (Relevant associations file). "
                      "The pairs it lists are marked relevant, and the correlation settings are not used.",
                      self.help)
        self.assertIn("A mRNA-Seq quantification file (Gene expression dataset), after ticking", self.help)

    def test_it_names_each_setting_by_its_label(self):
        for label in ("Report:", "Score method, Selection method and Filter cutoff:"):
            with self.subTest(label=label):
                self.assertIn(label, self.help)
        self.assertIn("(All features, the default)", self.help)
        self.assertIn("by Kendall (the default), Spearman or Pearson.", self.help)
        self.assertIn("By default a pair is marked when its correlation is below -0.5;", self.help)


if __name__ == "__main__":
    unittest.main(verbosity=2)
