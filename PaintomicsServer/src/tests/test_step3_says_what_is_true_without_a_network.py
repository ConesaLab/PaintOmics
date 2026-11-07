#!/usr/bin/env python3
"""A network that does not arrive is said so, and a walk that cannot run is too.

Why this exists
---------------
A real browser job on each of the five organisms installed by
AdminTools/customSpeciesInstaller.py (nben, thaz, tobl, paxi, vsub; 2026-11-06)
found two faults they share.

The Step 3 network card said "Building network..." under a spinner for as long
as the tab stayed open. Those species have no pathways_network.json, so
GET /kegg_data/pathway_network/<code> answers 404, and the four $.getJSON calls
in JobController.step3GetPathwaysNetworkDataHandler had a success callback and
nothing else: generateNetwork never ran and nothing else was going to.

The walk's refusal reached the page with a status its Walk column did not know
and a poll chain that went on asking. The server side of that is pinned in
test_walk_service_and_routes; this file holds the page's half.

The rule pinned here: a 404 says no network is available for the organism, on any
database, stated in the card, in words that also hold for a normal species whose
file is away during a reinstall. Any other failed answer (a 500, a dropped
connection, a body that is not JSON) says the network could not be loaded and to
reload, and over a graph already drawn every failed answer says that. A failed
answer ends the wait and stores nothing, and the row IndexedDB may hold for the
organism decides nothing.

One wait is left as it was. A 200 whose body generateNetwork cannot read throws
inside the success callback, no failure handler runs, and the card goes on saying
"Building network...". No installer writes such a file.

The real view and the real handler run under node against a stand-in for the
page: no DOM library, which this repository does not have.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_step3_says_what_is_true_without_a_network
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html"))
JOB_CONTROLLER = os.path.join(CLIENT_ROOT, "app", "controller", "JobController.js")
VIEWS = os.path.join(CLIENT_ROOT, "app", "view", "PathwayAcquisitionViews")
STEP3_VIEWS = os.path.join(VIEWS, "PA_Step3Views.js")
STEP4_WALK = os.path.join(VIEWS, "PA_Step4WalkView.js")

HANDLER = "this.step3GetPathwaysNetworkDataHandler = function (jobView)"
DATABASES = ("KEGG", "Reactome", "MapMan", "OmniPath")
GRAPH_TOOLS = ("fullscreenSettingsPanelButton", "resumeLayoutButton", "saveNodePositionsButton",
               "toggleTooltipsButton", "downloadNetworkTool", "downloadNetworkToolSVG")


def extract_block(source, header):
    """`header` plus the brace-matched block that follows it."""
    start = source.find(header)
    if start == -1:
        raise AssertionError("%s is missing" % header)
    opening = source.index("{", start + len(header) - 1)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces after %s" % header)


# A record per element the code names, holding what a reader of the page would
# read: its markup and whether it shows. Any jQuery call the record has no use
# for chains on and does nothing. A selector the code has not named before starts
# out visible, as every control in the card does. `inline` is the element's
# style.display and `sheetHides` a stylesheet rule that hides it, with show() doing
# what jQuery's does: it writes a display of its own over such a rule.
PAGE = r"""
const fs = require("fs");
const vm = require("vm");

function makePage(db) {
    const records = {};
    const record = (selector) => records[selector] ||
        (records[selector] = {
            selector: selector, html: "", inline: "", sheetHides: false,
            get visible() { return this.inline === "none" ? false : (this.inline !== "" || !this.sheetHides); },
        });
    const showOne = (r) => {
        if (r.inline === "none") { r.inline = ""; }
        if (r.inline === "" && r.sheetHides) { r.inline = "inline"; }
    };
    const SPINNER = '<i class="fa fa-cog fa-spin"></i> Building network...';
    record("#pathwayNetworkWaitBox_" + db).html = SPINNER;
    record("#step3-network-subtitle_" + db).html = "Building network&hellip;";

    const chain = (list) => {
        const api = {
            0: list[0],
            length: list.length,
            stop() { return wrapped; },
            hide() { list.forEach((r) => { r.inline = "none"; }); return wrapped; },
            show() { list.forEach(showOne); return wrapped; },
            fadeIn() { return api.show(); },
            fadeOut() { return api.hide(); },
            toggle(on) { return on ? api.show() : api.hide(); },
            css(name, value) {
                if (name === "display" && value !== undefined) { list.forEach((r) => { r.inline = value; }); }
                return wrapped;
            },
            text(value) {
                if (value === undefined) { return list[0] ? list[0].html : ""; }
                list.forEach((r) => { r.html = String(value); });
                return wrapped;
            },
            html(value) {
                if (value === undefined) { return list[0] ? list[0].html : ""; }
                list.forEach((r) => { r.html = String(value); });
                return wrapped;
            },
            empty() { list.forEach((r) => { r.html = ""; }); return wrapped; },
            each(fn) { list.forEach((r) => fn.call(r)); return wrapped; },
            attr() { return undefined; },
        };
        const wrapped = new Proxy(api, {
            get(target, name) {
                if (name in target) { return target[name]; }
                return typeof name === "symbol" ? undefined : () => wrapped;
            },
        });
        return wrapped;
    };
    const $ = (what) => {
        if (typeof what !== "string") { return chain([what]); }
        return chain(what.split(/,\s*/).map(record));
    };
    return {$: $, records: records, record: record, spinner: SPINNER};
}

function separatorOf(page, db) {
    return page.record("#step3-network-toolbar_" + db + " .pa-net-toolbar-sep");
}

function say(page, db) {
    const wait = page.record("#pathwayNetworkWaitBox_" + db);
    const subtitle = page.record("#step3-network-subtitle_" + db);
    return {
        waitBox: wait.html, waitBoxVisible: wait.visible,
        subtitle: subtitle.html, subtitleVisible: subtitle.visible,
        details: page.record("#networkview_" + db + " .paNetRailTab[data-pane=details]").visible,
        separator: separatorOf(page, db).visible, separatorInline: separatorOf(page, db).inline,
        configure: page.record("#showNetworkSettingsPanelButton_" + db).visible,
        tools: TOOLS.reduce((all, id) => { all[id] = page.record("#" + id + "_" + db).visible; return all; }, {}),
    };
}
"""

CARD_HARNESS = PAGE + r"""
const TOOLS = __TOOLS__;
const STOP = new Error("the harness stops generateNetwork after its STEP 0");

/* One visit to the handler. `answers` is what the server says, in order:
   {status: n} for a failed answer, {body: {...}} for a good one. */
function visit(db, answers, options) {
    options = options || {};
    const page = makePage(db);
    const urls = [], stored = [], reads = [];
    let drawn = 0;
    const jqXHR = (status) => ({status: status});
    page.$.getJSON = function (url, success) {
        urls.push(url);
        const answer = answers[urls.length - 1];
        const failed = answer.body === undefined;
        if (!failed) { success(answer.body); }
        const self = {
            fail(handler) { if (failed) { handler(jqXHR(answer.status)); } return self; },
            done() { return self; },
            always() { return self; },
        };
        return self;
    };
    const context = vm.createContext({
        $: page.$,
        console: {log() {}, warn() {}, error() {}, info() {}},
        document: {getElementById: () => null},
        setTimeout: () => 0, clearTimeout: () => {},
        View: function View() {},
        sigma: {layouts: {killForceLink() { throw STOP; }, stopForceLink() {}}},
        SERVER_URL_GET_PATHWAY_NETWORK: "/net/KEGG",
        SERVER_URL_GET_PATHWAY_NETWORK_REACTOME: "/net/Reactome",
        SERVER_URL_GET_PATHWAY_NETWORK_MAPMAN: "/net/MapMan",
        SERVER_URL_GET_PATHWAY_NETWORK_OMNIPATH: "/net/OmniPath",
    });
    vm.runInContext(fs.readFileSync(__VIEWS__, "utf8"), context, {filename: "PA_Step3Views.js"});
    const handler = vm.runInContext("(function () { " + __HANDLER__ + "; return this; })", context);
    const controller = handler.call({
        getStoredApplicationDataIndexDB(store, id, callback) {
            reads.push(store + ":" + id);
            callback(options.cachedRow);
        },
        updateStoredApplicationDataIndexDB(store, row) { stored.push({store: store, id: row.id}); },
    });
    const view = vm.runInContext('new PA_Step3PathwayNetworkView("' + db + '")', context);
    const options_ = {colorBy: "classification", pathwaysPositions: ["a# 1#2"]};
    view.getModel = () => ({getOrganism: () => "vsub", getClusterNumber: () => ({}), getJobID: () => "J"});
    view.getController = () => controller;
    view.getParent = () => ({getVisualOptions: () => options_, getIndexedPathways: () => ({a: {networkCoordX: 1}}),
                             getOmicClasses: () => ({})});
    const realGenerate = view.generateNetwork;
    view.generateNetwork = function (data, forceStop) {
        drawn += 1;
        try { realGenerate.call(view, data, forceStop); } catch (error) { if (error !== STOP) { throw error; } }
        if (options.draws) { view.network = {drawn: true}; }
    };
    if (options.drawn) { view.network = {drawn: true}; }
    if (options.narrowWindow) { separatorOf(page, db).sheetHides = true; }
    (options.before || (() => {}))(view, page);
    const turns = [];
    for (let i = 0; i < answers.length; i++) {
        controller.step3GetPathwaysNetworkDataHandler(view);
        turns.push(say(page, db));
    }
    return Object.assign(say(page, db), {urls: urls, stored: stored, reads: reads, drawn: drawn, turns: turns,
        hasNetwork: view.network !== null, spinner: page.spinner});
}

const results = {notFound: {}, otherFailures: {}, goodAnswer: {}};
__DATABASES__.forEach((db) => {
    results.notFound[db] = visit(db, [{status: 404}]);
    results.otherFailures[db] = {};
    [500, 502, 0, 200].forEach((status) => { results.otherFailures[db][status] = visit(db, [{status: status}]); });
    results.goodAnswer[db] = visit(db, [{body: {nodes: [], edges: []}}]);
});
results.cachedThenNotFound = visit("KEGG", [{status: 404}],
    {cachedRow: {id: "vsub", timestamp: 1, nodes: [{data: {id: "stale"}}], edges: []}});
results.refreshOverDrawn = visit("KEGG", [{status: 500}], {drawn: true});
results.failedThenGood = visit("KEGG", [{status: 500}, {body: {nodes: [], edges: []}}]);
results.goodThenFailed = {};
[404, 500, 0].forEach((status) => {
    results.goodThenFailed[status] = visit("KEGG", [{body: {nodes: [], edges: []}}, {status: status}], {draws: true});
});
results.narrowWindow = {
    good: visit("KEGG", [{body: {nodes: [], edges: []}}], {narrowWindow: true}),
    failedThenGood: visit("KEGG", [{status: 404}, {body: {nodes: [], edges: []}}], {narrowWindow: true}),
};
results.wideWindow = visit("KEGG", [{status: 404}, {body: {nodes: [], edges: []}}]);

/* Apply with "Save node positions" ticked goes through updateNodePositions. */
(function () {
    const page = makePage("KEGG");
    const context = vm.createContext({$: page.$, View: function View() {}});
    vm.runInContext(fs.readFileSync(__VIEWS__, "utf8"), context, {filename: "PA_Step3Views.js"});
    const view = vm.runInContext('new PA_Step3PathwayNetworkView("KEGG")', context);
    const visualOptions = {pathwaysPositions: ["a# 1#2"]};
    const indexed = {a: {networkCoordX: 1, networkCoordY: 2}};
    view.getParent = () => ({getVisualOptions: () => visualOptions, getIndexedPathways: () => indexed});
    let threw = null;
    try { view.updateNodePositions(false); } catch (error) { threw = String(error); }
    results.savePositions = {threw: threw, kept: visualOptions.pathwaysPositions, coordX: indexed.a.networkCoordX};
})();

console.log(JSON.stringify(results));
"""

# PA_Step4WalkView against FakeNodes, the way PaintomicsClient/tests/walk does it.
WALK_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function FakeNode(tag) {
    this.tagName = tag; this.childNodes = []; this.attributes = {}; this.className = ""; this.text = "";
}
FakeNode.prototype.appendChild = function (child) { this.childNodes.push(child); return child; };
FakeNode.prototype.setAttribute = function (name, value) { this.attributes[name] = String(value); };
Object.defineProperty(FakeNode.prototype, "textContent", {
    get() { return this.text + this.childNodes.map((c) => c.textContent).join(""); },
    set(value) { this.text = String(value); this.childNodes = []; },
});

const scheduled = [];
const context = vm.createContext({
    document: {
        createElement: (tag) => new FakeNode(tag),
        createTextNode: (text) => ({textContent: String(text), childNodes: []}),
    },
    View: function View() {},
    setTimeout: (fn, delay) => { scheduled.push(delay); return 1; },
    clearTimeout: () => {},
    AI_POLL_INTERVAL: 3000,
});
const root = new FakeNode("div");
const body = {0: root, empty() { root.childNodes = []; return body; }};
context.$ = () => ({find: () => body, on() { return this; }});
vm.runInContext(fs.readFileSync(__WALK__, "utf8"), context, {filename: "PA_Step4WalkView.js"});
const view = vm.runInContext("new PA_Step4WalkView()", context);
view.component = {isDestroyed: false, el: {dom: {}}};
view.onStatus({success: true, status: "unavailable", percent: 100, detail: "The AI walk is not available for it."});

const classes = [];
(function walk(node) {
    if (node.className) { classes.push(node.className); }
    (node.childNodes || []).forEach(walk);
})(root);
console.log(JSON.stringify({text: root.textContent, classes: classes, scheduled: scheduled.length}));
"""


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def run_node(script):
    directory = tempfile.mkdtemp(prefix="paintomics-step3-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        completed = subprocess.run(["node", path], capture_output=True, text=True, timeout=120)
        if completed.returncode != 0:
            raise AssertionError("node failed:\n%s" % completed.stderr)
        return json.loads(completed.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class NetworkCardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = run_node(
            CARD_HARNESS
            .replace("__TOOLS__", json.dumps(list(GRAPH_TOOLS)))
            .replace("__DATABASES__", json.dumps(list(DATABASES)))
            .replace("__VIEWS__", json.dumps(STEP3_VIEWS))
            .replace("__HANDLER__", json.dumps(extract_block(read(JOB_CONTROLLER), HANDLER))))

    def assertNoSpinner(self, shown):
        for field in ("waitBox", "subtitle"):
            self.assertNotIn("Building network", shown[field],
                             "the card still says it is building a network nothing will deliver")
        self.assertNotIn("fa-spin", shown["waitBox"], "the spinner is still turning")

    def test_a_404_says_no_network_is_available_on_every_database(self):
        """The same 404 answers a species that never has a network and a normal
        species whose file is away during a reinstall, so the words hold for both:
        none is available, not that the organism has none."""
        for database in DATABASES:
            with self.subTest(database=database):
                shown = self.results["notFound"][database]
                self.assertNoSpinner(shown)
                self.assertTrue(shown["waitBoxVisible"], "the message is not on screen")
                self.assertEqual(shown["waitBox"], "No %s pathway network is available for this organism. "
                                                   "The pathway table and maps work as usual." % database)
                self.assertEqual(shown["urls"], ["/net/%s/vsub" % database])

    def test_any_other_failed_answer_says_it_could_not_be_loaded_and_to_reload(self):
        for database in DATABASES:
            for status, shown in self.results["otherFailures"][database].items():
                with self.subTest(database=database, status=status):
                    self.assertNoSpinner(shown)
                    self.assertTrue(shown["waitBoxVisible"], "the message is not on screen")
                    self.assertIn("The %s pathway network could not be loaded." % database, shown["waitBox"])
                    self.assertIn("Reload the page", shown["waitBox"])
                    self.assertNotIn("is available", shown["waitBox"],
                                     "a server fault was reported as an organism with no network")

    def test_a_failed_answer_draws_nothing_and_stores_nothing(self):
        answers = [self.results["notFound"]["KEGG"], self.results["otherFailures"]["KEGG"]["500"]]
        for shown in answers:
            self.assertEqual(shown["drawn"], 0, "generateNetwork ran on an answer with no network in it")
            self.assertEqual(shown["stored"], [], "a failed answer was written to IndexedDB")

    def test_a_row_cached_in_indexeddb_decides_nothing(self):
        """The "networks" store is written on every good answer and read back by
        nobody: the handler's callback ignores its row. A stale row for this
        organism must not stand in for the server's answer."""
        shown = self.results["cachedThenNotFound"]
        self.assertEqual(shown["reads"], ["networks:vsub"])
        self.assertEqual(shown["drawn"], 0, "a network the server does not have was drawn from the browser's copy")
        self.assertIn("No KEGG pathway network is available for this organism.", shown["waitBox"])

    def test_no_control_is_left_that_needs_a_graph(self):
        """Resume layout threw on this.network.renderers, Save positions on
        this.network.graph, and the downloads on a canvas that was never made."""
        shown = self.results["notFound"]["KEGG"]
        for tool, on_screen in shown["tools"].items():
            with self.subTest(tool=tool):
                self.assertFalse(on_screen, "%s acts on a graph that is not there" % tool)
        self.assertFalse(shown["details"], "Details holds the placeholder text generateNetwork never replaced")
        self.assertTrue(shown["configure"], "the Tools rail drives the table too, so it must stay reachable")

    def test_ticking_save_positions_with_no_graph_does_not_stop_apply(self):
        outcome = self.results["savePositions"]
        self.assertIsNone(outcome["threw"], "Apply died in updateNodePositions, before it reached the table")
        self.assertEqual(outcome["kept"], ["a# 1#2"], "positions saved earlier were lost")
        self.assertEqual(outcome["coordX"], 1, "positions saved earlier were invalidated")

    def test_a_refresh_that_fails_keeps_the_graph_already_drawn(self):
        """Apply on a normal species asks again; a 500 then must not take away
        the graph on screen, nor leave the wait box over it."""
        shown = self.results["refreshOverDrawn"]
        self.assertFalse(shown["waitBoxVisible"], "the wait box still covers a graph that is there")
        self.assertTrue(shown["hasNetwork"])
        self.assertIn("could not be loaded", shown["subtitle"])
        self.assertIn("pa-net-warn", shown["subtitle"], "the status line's own warning style")
        self.assertTrue(all(shown["tools"].values()), "the tools work on the graph that is still there")

    def test_a_failed_refresh_over_a_drawn_graph_never_says_there_is_no_network(self):
        """A 200 draws the graph, then Apply asks again while a reinstall has the
        file away. A 404 there must not write "no network is available" directly
        above the network, so every status gets the could-not-load wording."""
        for status, shown in self.results["goodThenFailed"].items():
            with self.subTest(status=status):
                self.assertEqual(shown["drawn"], 1, "the first answer drew the graph")
                self.assertTrue(shown["hasNetwork"], "the graph already drawn was taken away")
                self.assertFalse(shown["waitBoxVisible"], "the wait box covers a graph that is there")
                self.assertIn("The KEGG pathway network could not be loaded. Reload the page to try again.",
                              shown["subtitle"])
                self.assertNotIn("is available", shown["subtitle"], "the line denies the network under it")
                self.assertTrue(all(shown["tools"].values()), "the tools work on the graph that is still there")

    def test_showing_the_tools_leaves_the_stylesheet_in_charge(self):
        """network-views.css hides the toolbar separator at 900 px or narrower, and
        .toggle(true) wrote a display of its own over that on every draw."""
        for label, shown in self.results["narrowWindow"].items():
            with self.subTest(label):
                self.assertEqual(shown["separatorInline"], "", "showing wrote an inline display")
                self.assertFalse(shown["separator"], "the rule that hides the separator was overridden")
                self.assertTrue(all(shown["tools"].values()))
        wide = self.results["wideWindow"]
        self.assertFalse(wide["turns"][0]["separator"], "no graph, so nothing for the separator to group")
        self.assertTrue(wide["separator"], "at full width the separator comes back with the tools")
        self.assertEqual(wide["separatorInline"], "")

    def test_a_good_answer_after_a_failed_one_brings_everything_back(self):
        shown = self.results["failedThenGood"]
        self.assertIn("could not be loaded", shown["turns"][0]["waitBox"])
        self.assertEqual(shown["drawn"], 1)
        self.assertEqual(shown["waitBox"], shown["spinner"], "generateNetwork keeps the failure's words over the graph")
        self.assertTrue(shown["waitBoxVisible"])
        self.assertTrue(shown["subtitleVisible"], "updateNetworkSubtitle writes into a hidden line")
        self.assertTrue(all(shown["tools"].values()))
        self.assertTrue(shown["details"])

    def test_a_good_answer_still_draws_and_caches_on_every_database(self):
        """The control: a species with a network behaves as it always did."""
        for database in DATABASES:
            with self.subTest(database=database):
                shown = self.results["goodAnswer"][database]
                self.assertEqual(shown["drawn"], 1)
                self.assertEqual(shown["stored"], [{"store": "networks", "id": "vsub"}])
                self.assertEqual(shown["urls"], ["/net/%s/vsub" % database])
                self.assertEqual(shown["waitBox"], shown["spinner"])
                self.assertTrue(all(shown["tools"].values()))


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class WalkColumnTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shown = run_node(WALK_HARNESS.replace("__WALK__", json.dumps(STEP4_WALK)))

    def test_it_states_the_refusal_and_offers_no_walk(self):
        self.assertIn("The AI walk is not available for it.", self.shown["text"],
                      "the column drew nothing for a status it does not know")
        self.assertIn("pa-walk-note", self.shown["classes"])
        self.assertFalse([name for name in self.shown["classes"] if "pa-walk-start" in name],
                         "the column offers a walk the server would refuse")

    def test_it_does_not_ask_again(self):
        self.assertEqual(self.shown["scheduled"], 0, "no later answer differs, so there is nothing to poll for")


if __name__ == "__main__":
    unittest.main()
