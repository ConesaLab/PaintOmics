#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Guards for the step-2 compounds panel (PA_Step2Views.js).

Step 2 used to build one Ext container per matched compound name, one Ext box
per candidate inside it and one Ext.tip.ToolTip per candidate. On a job with
6592 matched names that is tens of thousands of components, and the tab froze
for minutes before the first paint. The panel now renders HTML and shares one
delegated tooltip, and it only draws a card for the names that still need a
decision.

Three properties of that filter are load bearing and are what this file pins:

  1. A set is only skipped when its single candidate is *already selected*.
     JobController de-duplicates the same KEGG compound across input names, so
     a lone candidate can arrive unselected; skipping that one would make it
     permanently unselectable.
  2. checkForm() must read the model. Only ambiguous sets have checkboxes now,
     so counting rendered checkboxes would report "nothing was chosen" for a
     job whose compounds all resolved automatically.
  3. The metabolite-class-activity threshold combo must NOT be gated on the
     number of cards. Its value is posted as thresholdMetaboliteClass and fed
     to compundsClassification(); gating it on the filtered list would drop it
     from the request.
  4. The "See compounds disambiguation" link IS gated on the cards, because the
     panel it jumps to is: a job that matched nothing, or whose compounds all
     resolved by themselves, has nothing for it to go to.

The behavioural half runs the real view file inside node with stubs for Ext,
jQuery and View, so the assertions are about what the code does rather than
about what it looks like. It is skipped when node is unavailable.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", ".."))
STEP2_VIEW = os.path.join(
    REPO_ROOT, "PaintomicsClient", "public_html", "app", "view",
    "PathwayAcquisitionViews", "PA_Step2Views.js")

NODE = shutil.which("node")

# The harness loads the real file into a fresh V8 context, feeds it fake
# CompoundSet/Compound models shaped like FeatureModels.js, and prints what the
# view produced as JSON.
HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.argv[2], 'utf8');

function htmlEncode(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const created = [];
const widgets = [];
const captions = [];

// The real boxready runs chained jQuery calls; only the caption's text matters.
function chain(selector) {
    const self = new Proxy(function () {}, {
        get: function (target, name) {
            if (name === 'length') { return 0; }
            if (name === 'html') {
                return function (value) {
                    if (String(selector).indexOf('mapping_summary_caption') !== -1) { captions.push(value); }
                    return self;
                };
            }
            return function () { return self; };
        }
    });
    return self;
}
chain.extend = Object.assign;

const sandbox = {
    console: {log: function () {}, info: function () {}, warn: function () {}, error: function () {}},
    // Mirrors app/view/common/Util.js: View owns the model accessors that the
    // views inherit through `Foo.prototype = new View()`.
    View: function View() {
        this.model = null;
        this.component = null;
        this.getModel = function () { return this.model; };
        this.getComponent = function () { return this.component; };
        this.loadModel = function (model) { this.model = model; return this; };
    },
    Ext: {
        String: {htmlEncode: htmlEncode},
        widget: function (config) { widgets.push(config); return {}; },
        create: function (name) { created.push(name); return {}; },
        getBody: function () { return {getViewSize: function () { return {width: 1500}; }}; }
    },
    $: chain,
    initializeTooltips: function () {},
    showWarningMessage: function () {},
    setTimeout: function () {}
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(source + "\n;globalThis.__api = {" +
    "countLabel: countLabel," +
    "renderCompoundCandidate: renderCompoundCandidate," +
    "mappingSummaryCaption: mappingSummaryCaption," +
    "PA_Step2CompoundSetView: PA_Step2CompoundSetView," +
    "PA_Step2JobView: PA_Step2JobView};", sandbox, {filename: 'PA_Step2Views.js'});

const api = sandbox.__api;

function compound(id, name, selected) {
    return {
        ID: id, name: name, selected: selected,
        getID: function () { return this.ID; },
        getName: function () { return this.name; },
        isSelected: function () { return this.selected; }
    };
}

function compoundSet(title, main, other) {
    return {
        title: title, mainCompounds: main, otherCompounds: other,
        getTitle: function () { return this.title; },
        getMainCompounds: function () { return this.mainCompounds; },
        getOtherCompounds: function () { return this.otherCompounds; },
        findMainCompound: function (id) {
            for (var i = 0; i < this.mainCompounds.length; i++) {
                if (this.mainCompounds[i].ID === id) { return this.mainCompounds[i]; }
            }
            return null;
        },
        findOtherCompound: function (id) {
            for (var i = 0; i < this.otherCompounds.length; i++) {
                if (this.otherCompounds[i].ID === id) { return this.otherCompounds[i]; }
            }
            return null;
        },
        addObserver: function () {}
    };
}

function setView(set) {
    var view = new api.PA_Step2CompoundSetView();
    view.loadModel(set);
    return view;
}

function jobView(sets) {
    var view = new api.PA_Step2JobView();
    view.loadModel({
        getFoundCompounds: function () { return sets; },
        deleteObserver: function () {},
        getJobID: function () { return "job1"; }
    });
    return view;
}

// --- the sets used across the checks ------------------------------------
var resolved      = compoundSet("Adenosine",  [compound("C00212", "Adenosine", true)], []);
var loneUnchecked = compoundSet("Alanine",    [compound("C00041", "L-Alanine", false)], []);
var twoMain       = compoundSet("Glucose",    [compound("C00031", "D-Glucose", true),
                                               compound("C00267", "alpha-D-Glucose", false)], []);
var onlyOthers    = compoundSet("Xyz",        [], [compound("C00001", "Water", false)]);
var empty         = compoundSet("Nothing",    [], []);
var withAlts      = compoundSet("Serine",     [compound("C00065", "L-Serine", true)],
                                              [compound("C00716", "D-Serine", false),
                                               compound("C00740", "DL-Serine", false)]);

var out = {
    extCreateCalls: created.slice(),

    needsDisambiguation: {
        resolved: setView(resolved).needsDisambiguation(),
        loneUnchecked: setView(loneUnchecked).needsDisambiguation(),
        twoMain: setView(twoMain).needsDisambiguation(),
        onlyOthers: setView(onlyOthers).needsDisambiguation(),
        empty: setView(empty).needsDisambiguation()
    },

    cardSingle: setView(loneUnchecked).renderCard(0),
    cardWithAlternatives: setView(withAlts).renderCard(3),
    cardNoAlternatives: setView(twoMain).renderCard(1),
    otherCompoundsHTML: setView(withAlts).renderOtherCompounds(),

    countLabels: {
        one: api.countLabel(1, "KEGG match", "KEGG matches"),
        four: api.countLabel(4, "KEGG match", "KEGG matches"),
        oneAlt: api.countLabel(1, "more match", "more matches"),
        manyAlt: api.countLabel(101, "more match", "more matches")
    },

    escaping: api.renderCompoundCandidate(
        compound("C00001", '<img src=x onerror="alert(1)">', false)),

    captions: {
        partial: api.mappingSummaryCaption(890, 110),
        allMapped: api.mappingSummaryCaption(500, 0),
        noneAtAll: api.mappingSummaryCaption(0, 0),
        dictShaped: api.mappingSummaryCaption(undefined, 100)
    }
};

// --- what survives the filter, and what checkForm makes of it -----------
var everySetResolved = jobView([resolved, compoundSet("Urea", [compound("C00086", "Urea", true)], [])]);
out.allResolved = {
    cards: everySetResolved.items.length,
    checkForm: everySetResolved.checkForm(),
    selected: everySetResolved.getSelectedCompounds()
};

var mixed = jobView([resolved, loneUnchecked, withAlts]);
out.mixed = {
    cards: mixed.items.length,
    cardTitles: mixed.items.map(function (v) { return v.getModel().getTitle(); }),
    checkForm: mixed.checkForm()
};

var nothingSelected = jobView([loneUnchecked]);
out.nothingSelected = {
    cards: nothingSelected.items.length,
    checkForm: nothingSelected.checkForm()
};

var noCompoundsAtAll = jobView([]);
out.noCompoundsAtAll = {
    cards: noCompoundsAtAll.items.length,
    checkForm: noCompoundsAtAll.checkForm()
};

// Re-loading the same view must not stack a second copy of every card.
var reloaded = jobView([withAlts]);
reloaded.loadModel({
    getFoundCompounds: function () { return [withAlts]; },
    deleteObserver: function () {},
    getJobID: function () { return "job1"; }
});
out.reloadedCards = reloaded.items.length;

// --- the jump link, through the real initComponent and the card's real boxready
var RealSummaryPanel = sandbox.PA_OmicSummaryPanel;
function captionsOf(sets) {
    widgets.length = 0;
    captions.length = 0;
    sandbox.PA_OmicSummaryPanel = function (omicName, distribution, isCompound, hasPanel) {
        var panel = new RealSummaryPanel(omicName, distribution, isCompound, hasPanel);
        panel.initComponent();
        widgets[widgets.length - 1].listeners.boxready();
        this.getComponent = function () { return {}; };
    };
    var view = new api.PA_Step2JobView();
    view.loadModel({
        getFoundCompounds: function () { return sets; },
        deleteObserver: function () {},
        getJobID: function () { return "job1"; },
        getDatabases: function () { return ["KEGG"]; },
        getCompoundBasedInputOmics: function () { return [{omicName: "Metabolomics"}]; },
        getGeneBasedInputOmics: function () { return [{omicName: "Gene expression"}]; },
        getDataDistributionSummaries: function () {
            return {"Gene expression": [{KEGG: 5, Total: 5}, 3], "Metabolomics": [sets.length, 3]};
        }
    });
    view.initComponent();
    sandbox.PA_OmicSummaryPanel = RealSummaryPanel;
    var page = widgets[widgets.length - 1];
    var modules = page.items[1].items;
    return {
        captions: captions.slice(),
        panelOnPage: modules.some(function (module) { return module && module.itemId === "compoundsPanelsContainer"; })
    };
}
out.jumpLink = {
    nothingMatched: captionsOf([]),
    allResolved: captionsOf([resolved]),
    someToDecide: captionsOf([resolved, withAlts])
};

process.stdout.write(JSON.stringify(out));
"""


def run_harness():
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(HARNESS)
        harness_path = handle.name
    try:
        completed = subprocess.run(
            [NODE, harness_path, STEP2_VIEW],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            raise AssertionError(
                "node harness failed:\n" + completed.stderr.decode("utf-8", "replace"))
        return json.loads(completed.stdout.decode("utf-8"))
    finally:
        os.unlink(harness_path)


class Step2StaticGuardsTest(unittest.TestCase):
    """Checks that read the source, so they hold even without node."""

    @classmethod
    def setUpClass(cls):
        with open(STEP2_VIEW) as handle:
            cls.source = handle.read()

    def test_the_threshold_combo_is_not_gated_on_the_filtered_cards(self):
        # thresholdMetaboliteClass reaches compundsClassification() on the
        # server. Gating its box on me.items.length would silently drop it for
        # a job whose compounds all resolved automatically.
        marker = "if (me.getModel().getFoundCompounds().length > 0) {"
        self.assertIn(marker, self.source)
        threshold_box = self.source.index('id: "threshold_box"')
        guard = self.source.rindex(marker, 0, threshold_box)
        between = self.source[guard:threshold_box]
        self.assertNotIn("me.items.length", between)

    def test_check_form_does_not_count_rendered_checkboxes(self):
        self.assertNotIn('$(".compoundsPanelsContainer input[type=checkbox]")', self.source)
        self.assertIn("this.model.getFoundCompounds().length === 0 || this.getSelectedCompounds().length > 0",
                      self.source)

    def test_only_one_tooltip_component_is_ever_created(self):
        # One delegated tooltip for the whole panel. One per candidate is what
        # made the panel unopenable.
        self.assertEqual(1, self.source.count("Ext.create('Ext.tip.ToolTip'"))
        self.assertIn("delegate: '.metaboliteCompound'", self.source)

    def test_no_component_is_built_per_compound(self):
        # PA_Step2CompoundView (an Ext box + tooltip per candidate) is gone.
        self.assertNotIn("function PA_Step2CompoundView(", self.source)
        self.assertNotIn("new PA_Step2CompoundView(", self.source)

    def test_the_two_second_rebinding_timeout_is_gone(self):
        # The delegated handlers cover lazily inserted alternatives, so the
        # "wait 2s then bind the new checkboxes" hack has nothing left to do.
        self.assertNotIn("}, 2000);", self.source)

    def test_the_donut_no_longer_relies_on_its_data_labels(self):
        self.assertIn("dataLabels: {enabled: false}", self.source)
        self.assertIn("mapping_summary_caption", self.source)

    def test_grammar(self):
        # "N compounds founds" -> "N compounds found", in code not just prose.
        self.assertNotIn("compounds founds</h4>", self.source)
        self.assertNotIn("' compounds founds'", self.source)


@unittest.skipUnless(NODE, "node is not on PATH")
class Step2BehaviourTest(unittest.TestCase):
    """Runs the real view file and inspects what it produces."""

    @classmethod
    def setUpClass(cls):
        cls.out = run_harness()

    def test_the_jump_link_appears_only_with_the_panel_it_jumps_to(self):
        LINK = "See compounds disambiguation"
        for name, expected in (("nothingMatched", False), ("allResolved", False), ("someToDecide", True)):
            with self.subTest(job=name):
                job = self.out["jumpLink"][name]
                geneCard, compoundCard = job["captions"]
                self.assertEqual(job["panelOnPage"], expected)
                self.assertEqual(LINK in compoundCard, expected)
                self.assertNotIn(LINK, geneCard)

    def test_a_resolved_set_is_skipped(self):
        self.assertFalse(self.out["needsDisambiguation"]["resolved"])

    def test_a_lone_but_unselected_candidate_keeps_its_card(self):
        # The cross-box de-duplicator in JobController unselects the losing
        # copy of a repeated compound. Skipping it would leave the user with no
        # way to switch it back on.
        self.assertTrue(self.out["needsDisambiguation"]["loneUnchecked"])

    def test_ambiguous_sets_keep_their_cards(self):
        self.assertTrue(self.out["needsDisambiguation"]["twoMain"])
        self.assertTrue(self.out["needsDisambiguation"]["onlyOthers"])

    def test_a_set_that_matched_nothing_draws_no_card(self):
        self.assertFalse(self.out["needsDisambiguation"]["empty"])

    def test_only_ambiguous_sets_are_rendered(self):
        self.assertEqual(0, self.out["allResolved"]["cards"])
        self.assertEqual(2, self.out["mixed"]["cards"])
        self.assertEqual(["Alanine", "Serine"], self.out["mixed"]["cardTitles"])

    def test_a_fully_resolved_job_still_passes_check_form(self):
        # Every compound resolved automatically: no cards, no checkboxes, but
        # the selection is real and must not be reported as missing.
        self.assertTrue(self.out["allResolved"]["checkForm"])
        self.assertEqual(2, len(self.out["allResolved"]["selected"]))

    def test_check_form_still_rejects_a_job_with_nothing_selected(self):
        self.assertEqual(1, self.out["nothingSelected"]["cards"])
        self.assertFalse(self.out["nothingSelected"]["checkForm"])

    def test_a_job_without_compounds_passes_check_form(self):
        self.assertEqual(0, self.out["noCompoundsAtAll"]["cards"])
        self.assertTrue(self.out["noCompoundsAtAll"]["checkForm"])

    def test_reloading_the_view_does_not_duplicate_the_cards(self):
        self.assertEqual(1, self.out["reloadedCards"])

    def test_the_card_carries_the_hooks_the_delegated_handlers_need(self):
        card = self.out["cardWithAlternatives"]
        self.assertIn('class="contentbox metaboliteBox"', card)
        self.assertIn('data-compoundset="3"', card)
        self.assertIn('class="metaboliteCompound"', card)
        self.assertIn('data-compound-id="C00065"', card)
        self.assertIn('name="metabolite" value="C00065"', card)

    def test_the_show_control_only_appears_when_there_are_alternatives(self):
        self.assertIn("showOtherCompoundsButton", self.out["cardWithAlternatives"])
        self.assertNotIn("showOtherCompoundsButton", self.out["cardNoAlternatives"])
        self.assertNotIn("more match", self.out["cardNoAlternatives"])

    def test_alternatives_are_not_rendered_until_asked_for(self):
        self.assertIn('class="otherCompoundsPanel"', self.out["cardWithAlternatives"])
        # The panel ships empty; renderOtherCompounds fills it on first open.
        self.assertNotIn("C00716", self.out["cardWithAlternatives"])
        self.assertIn("C00716", self.out["otherCompoundsHTML"])
        self.assertIn("C00740", self.out["otherCompoundsHTML"])

    def test_counts_read_as_english(self):
        labels = self.out["countLabels"]
        self.assertEqual("1 KEGG match", labels["one"])
        self.assertEqual("4 KEGG matches", labels["four"])
        self.assertEqual("1 more match", labels["oneAlt"])
        self.assertEqual("101 more matches", labels["manyAlt"])
        self.assertIn("1 KEGG match", self.out["cardSingle"])
        # The id is on the row: it is how a reader tells the three alanines apart.
        self.assertIn('<code class="metaboliteId">C00065</code>', self.out["cardWithAlternatives"])

    def test_compound_names_are_escaped(self):
        # Compound titles come from the user's input file; the old markup
        # concatenated them raw.
        self.assertNotIn("<img src=x", self.out["escaping"])
        self.assertIn("&lt;img src=x", self.out["escaping"])

    def test_the_caption_states_both_numbers(self):
        partial = self.out["captions"]["partial"]
        self.assertIn("890", partial)
        self.assertIn("110", partial)
        self.assertIn("89%", partial)
        self.assertIn("11%", partial)

    def test_a_fully_mapped_omic_still_shows_a_number(self):
        # The case the donut drew as a blank green ring.
        all_mapped = self.out["captions"]["allMapped"]
        self.assertIn("100%", all_mapped)
        self.assertIn("0%", all_mapped)
        self.assertIn("500", all_mapped)

    def test_the_caption_never_prints_nan(self):
        for caption in self.out["captions"].values():
            self.assertNotIn("NaN", caption)
            self.assertNotIn("Infinity", caption)
        # An omic with no features at all: 0/0 must not divide.
        self.assertIn("0%", self.out["captions"]["noneAtAll"])

    def test_no_tooltip_is_created_while_rendering(self):
        # Ext.create is only reached from initCompoundsPanelHandlers, which
        # runs once per panel, not once per candidate.
        self.assertEqual([], self.out["extCreateCalls"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
