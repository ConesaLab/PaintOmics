#!/usr/bin/env python3
"""Every metabolite class gets its own row, inside the chart, in the same card
as its compounds.

Why this exists
---------------
The class map placed classes by their BRITE parent along x and by -log10 FDR
along y, then nudged siblings sideways by +-0.27, +-0.54 band widths to keep
them apart. Seen on paintomics.uv.es (job 11n0VMC305, stored before the
server sent parents, so every class was "Unclassified"): all eight classes
landed in ONE band, the fourth nudge is 0.54 of the band -- past its edge --
and that mark was drawn half outside the plot. Every FDR was 1.0, so all
eight also sat on the x axis, on top of the rotated parent label.

Clicking a mark then opened the class's compounds in a SECOND contentbox
appended below the card, titled "Expression Value", which read as a different
analysis that happened to share a name.

Now the ranking is the layout -- one row per class, evidence first -- and
the selected class's heatmap and line chart open inside the same card. These
tests run the shipped functions (extracted from PA_Step3Views.js, not
re-implemented) in node against a stub DOM.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_class_ranking_keeps_every_class_in_its_row
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

CLIENT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "PaintomicsClient", "public_html"))
STEP3_VIEWS = os.path.join(CLIENT, "app", "view", "PathwayAcquisitionViews", "PA_Step3Views.js")
UTIL = os.path.join(CLIENT, "app", "view", "common", "Util.js")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def balanced(source, start):
    """Index just past the block whose first '{' is at or after `start`."""
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("unbalanced braces")


def method(source, name, after=0):
    """`this.<name> = function (...) {...};` -- the first one after `after`."""
    match = re.compile(r"this\.%s\s*=\s*function" % re.escape(name)).search(source, after)
    if match is None:
        raise AssertionError("%s() is not defined in %s" % (name, STEP3_VIEWS))
    return source[match.start():balanced(source, match.end())] + ";"


def function(source, name, after=0):
    """`function <name>(...) {...}` -- the first one after `after`."""
    match = re.compile(r"function %s\s*\(" % re.escape(name)).search(source, after)
    if match is None:
        raise AssertionError("function %s is not defined" % name)
    return source[match.start():balanced(source, match.end())]


def var_function(source, name):
    """`var <name> = function (...) {...};`"""
    match = re.compile(r"var %s\s*=\s*function" % re.escape(name)).search(source)
    if match is None:
        raise AssertionError("var %s is not defined" % name)
    return source[match.start():balanced(source, match.end())] + ";"


# A DOM with what the renderer touches: createElementNS, attributes, class
# lists, children, listeners, and a querySelector that understands the
# selectors the view uses ("g.paClassMapMark", "[data-class]", ".cls",
# 'g.paClassMapMark[data-class="X"]'). innerHTML replaces the children, so a
# host written with a string loses whatever was appended before.
STUB_DOM = r"""
function matches(node, sel) {
    var tag = null, classes = [], attrs = [];
    var rest = sel.replace(/\[([\w-]+)(?:="([^"]*)")?\]/g, function (m, n, v) {
        attrs.push([n, v === undefined ? null : v]); return "";
    });
    var parts = rest.split(".");
    if (parts[0]) tag = parts[0];
    classes = parts.slice(1);
    if (tag && node.tagName !== tag) return false;
    for (var i = 0; i < classes.length; i++) if (!node.classList.contains(classes[i])) return false;
    for (var j = 0; j < attrs.length; j++) {
        var have = node.getAttribute(attrs[j][0]);
        if (have === null) return false;
        if (attrs[j][1] !== null && have !== attrs[j][1]) return false;
    }
    return true;
}
function makeNode(tag) {
    var node = {
        tagName: tag, attrs: {}, children: [], style: {}, handlers: {}, _html: "",
        textContent: "", clientWidth: 900,
        classList: {
            _set: {},
            add: function (c) { this._set[c] = true; },
            remove: function (c) { delete this._set[c]; },
            contains: function (c) { return !!this._set[c]; },
            toggle: function (c, force) {
                var on = (force === undefined) ? !this._set[c] : !!force;
                if (on) { this._set[c] = true; } else { delete this._set[c]; }
                return on;
            }
        },
        setAttribute: function (n, v) {
            this.attrs[n] = String(v);
            if (n === "class") { var self = this; self.classList._set = {};
                String(v).split(/\s+/).forEach(function (c) { if (c) self.classList._set[c] = true; }); }
        },
        getAttribute: function (n) {
            if (n === "class") return Object.keys(this.classList._set).join(" ");
            return this.attrs.hasOwnProperty(n) ? this.attrs[n] : null;
        },
        appendChild: function (c) { c.parentNode = this; this.children.push(c); return c; },
        addEventListener: function (t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); },
        getBoundingClientRect: function () { return {x: 0, y: 0, width: 900, height: 0, top: 0}; },
        querySelectorAll: function (sel) {
            var out = [];
            (function walk(n) { n.children.forEach(function (c) { if (matches(c, sel)) out.push(c); walk(c); }); })(this);
            return out;
        },
        querySelector: function (sel) { return this.querySelectorAll(sel)[0] || null; }
    };
    Object.defineProperty(node, "innerHTML", {
        get: function () { return this._html; },
        set: function (v) { this._html = String(v); this.children = []; }
    });
    Object.defineProperty(node, "firstChild", {
        get: function () { return this.children[0] || (this._html ? {} : null); }
    });
    return node;
}
var registry = {};
["classActivityMap", "classActivityMapSummary", "classActivityMapControls",
 "classActivityKeys", "classActivityDetail"].forEach(function (id) { registry[id] = makeNode("div"); });
var document = {
    getElementById: function (id) { return registry[id] || null; },
    createElementNS: function (ns, tag) { return makeNode(tag); }
};
var Ext = { apply: function (target, source) {
    for (var k in source) if (source.hasOwnProperty(k)) target[k] = source[k]; return target; } };
function OmicValue(o) { this.keggName = o.keggName; this.inputName = o.inputName;
    this.originalName = o.originalName; this.values = o.values; this.relevant = !!o.relevant; }
OmicValue.loadFromJSON = function (o) { return new OmicValue(o); };
OmicValue.prototype.isRelevant = function () { return this.relevant; };
OmicValue.prototype.isRelevantAssociation = function () { return false; };
OmicValue.prototype.getValues = function () { return this.values; };
var drawn = [];
var generateHeatmap = function (target, omicName, values) {
    drawn.push({kind: "heatmap", target: target, omic: omicName,
        names: values.map(function (v) { return v.keggName; })}); };
var generatePlot = function (target, omicName, values) {
    drawn.push({kind: "plot", target: target, omic: omicName,
        names: values.map(function (v) { return v.keggName; })}); };
var paColorLegend = function () { return '<div class="paColorLegend"></div>'; };
var getMinMax = function () { return {min: -0.3, max: 0.3, absMin: -2, absMax: 2}; };
var paColourReferenceLabel = function () { return "10th-90th percentile"; };
var paOmicHeaders = function () { return ["#", "c1", "c2"]; };
var paValuesForHeader = function (ov) { return ov.values; };
var PA_DEFAULT_COLOR_REFERENCE = "p10p90", PA_DEFAULT_COLOR_SCALE = "bwr";
var console = { warn: function () {}, log: function () {} };
"""

# The live shape: eight classes, no parents sent (an old job), every FDR 1.0.
SCENARIO = r"""
var CLASSES = [
    ["Amino acids", 40, 23, 0.3833], ["Carboxylic acids", 6, 5, 0.4721], ["Amines", 4, 3, 0.6927],
    ["Neurotransmitters", 4, 3, 0.6927], ["Monosaccharides", 5, 3, 0.8452], ["27-Carbon atoms", 1, 1, 0.723],
    ["Bases", 3, 1, 0.9788], ["Nucleosides", 1, 0, 1.0]
];
var classificationDictRef = {}, dataFinal = {}, globalExpressionComp = {};
var relObj = {}, pObj = {}, bhObj = {}, byObj = {};
CLASSES.forEach(function (c, ci) {
    var ids = [];
    for (var i = 0; i < c[1]; i++) {
        var id = "C" + ci + "_" + i;
        ids.push(id);
        globalExpressionComp[id] = {keggName: id, inputName: id, values: [0.1 * i, -0.2], relevant: i < c[2]};
    }
    classificationDictRef[c[0]] = ids;
    dataFinal[c[0]] = {ID: ids};
    if (c[2]) relObj[c[0]] = c[2];
    pObj[c[0]] = c[3]; bhObj[c[0]] = 1.0; byObj[c[0]] = 1.0;
});
var tableData = {totalRelevantFeaturesInCategory_list: [relObj], pValueClassification_list: [pObj],
                 adjustPValueBH_list: [bhObj], adjustPValueBY_list: [byObj]};
var classMapMeta = {parents: {}, nullProportion: [0.723], thresholdSource: "auto"};
var classMapCondition = 0, classMapHasDirection = false, nCond = 1, conditionNames = [];
var classifiableTotal = 47, classifiableRelevant = [34];
var classMapRowsCache = [], classMapSelected = null, classDetailDismissed = false;
var classDetailOnlyRelevant = false, classDetailKey = null, compoundOmicName = "Metabolomics";
var distributionSummaries = {Metabolomics: [1, 0, -2, -0.3, -0.1, 0, 0.1, 0.3, 2]};
var visualOptions = {};
var CLASSMAP_NS = "http://www.w3.org/2000/svg";
var CLASSMAP_FDR_FLOOR = 1e-4, PA_CLASS_CHART_FURNITURE = 132;
var me = {};
"""


def view_code():
    source = read(STEP3_VIEWS)
    start = source.index("function PA_Step3MetaboliteView() {")
    helpers = [function(source, name, start) for name in
               ("classMapEl", "classMapText", "classMapEscape", "classMapFormatP",
                "classMapRank", "classMapTruncate", "bindClassDetailControls")]
    methods = [method(source, name, start) for name in
               ("buildClassMapRows", "compoundDirection", "focusClass", "renderClassMapControls",
                "drawClassMap", "paintClassCompounds", "selectClass", "markSelectedClass",
                "renderClassDetail")]
    return "\n".join(helpers) + "\n(function () {\n" + "\n".join(methods) + "\n}).call(me);\n"


def run_in_node(body):
    script = "\n".join([STUB_DOM, SCENARIO, view_code(), body])
    directory = tempfile.mkdtemp(prefix="paintomics-classrank-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        completed = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("node failed:\n%s" % completed.stderr)
        return json.loads(completed.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


GEOMETRY = r"""
me.drawClassMap();
var svg = registry.classActivityMap.children[0];
var width = Number(svg.getAttribute("width"));
var groups = svg.querySelectorAll("g.paClassMapMark");
var out = groups.map(function (g) {
    var dot = g.querySelector("circle.paClassRankDot");
    var disc = g.querySelector("circle.paClassMapDisc");
    var hit = g.querySelector("rect.paClassRankHit");
    return {name: g.getAttribute("data-class"), dotX: Number(dot.getAttribute("cx")),
            discX: Number(disc.getAttribute("cx")), discR: Number(disc.getAttribute("r")),
            y: Number(hit.getAttribute("y")), h: Number(hit.getAttribute("height")),
            selected: g.classList.contains("paIsSelected")};
});
console.log = function () {};
process.stdout.write(JSON.stringify({width: width, height: Number(svg.getAttribute("height")), rows: out,
    detail: registry.classActivityDetail.innerHTML, open: registry.classActivityDetail.classList.contains("paIsOpen"),
    drawn: drawn, summary: registry.classActivityMapSummary.innerHTML}));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class RankingGeometryTest(unittest.TestCase):

    def setUp(self):
        self.result = run_in_node(GEOMETRY)

    def test_every_class_is_drawn_inside_the_chart(self):
        width = self.result["width"]
        for row in self.result["rows"]:
            self.assertTrue(0 <= row["dotX"] <= width,
                            "%s's dot is at x=%s in a chart %spx wide -- the class map used to "
                            "push the outer siblings of a crowded parent group past the plot edge"
                            % (row["name"], row["dotX"], width))
            self.assertTrue(row["discX"] - row["discR"] >= 0 and row["discX"] + row["discR"] <= width,
                            "%s's mark overflows the chart" % row["name"])

    def test_each_class_has_its_own_row_and_no_two_share_one(self):
        rows = self.result["rows"]
        self.assertEqual(len(rows), 8)
        ys = [r["y"] for r in rows]
        self.assertEqual(len(set(ys)), 8, "two classes were laid on the same row: %s" % ys)
        ordered = sorted(rows, key=lambda r: r["y"])
        pitches = {round(b["y"] - a["y"]) for a, b in zip(ordered, ordered[1:])}
        self.assertEqual(pitches, {30}, "rows are not evenly pitched: %s" % pitches)
        for row in rows:
            self.assertTrue(row["y"] >= 0 and row["y"] + row["h"] <= self.result["height"])

    def test_rows_are_ranked_by_evidence(self):
        ordered = [r["name"] for r in sorted(self.result["rows"], key=lambda r: r["y"])]
        self.assertEqual(ordered[:3], ["Amino acids", "Carboxylic acids", "Amines"])
        self.assertEqual(ordered[-1], "Nucleosides")

    def test_the_top_class_opens_in_the_same_card(self):
        self.assertTrue(self.result["open"], "the detail did not open with the chart")
        self.assertIn("<h4>Amino acids</h4>", self.result["detail"])
        self.assertIn("classDetail_hm", self.result["detail"])
        # No ladder in this harness, so the heatmap's companion is the line
        # chart; with a ladder it is the member table (checked in the browser).
        self.assertIn("classDetail_pl", self.result["detail"])
        selected = [r["name"] for r in self.result["rows"] if r["selected"]]
        self.assertEqual(selected, ["Amino acids"])
        heatmaps = [d for d in self.result["drawn"] if d["kind"] == "heatmap"]
        self.assertEqual(len(heatmaps), 1)
        self.assertEqual(heatmaps[0]["target"], "classDetail_hm")
        self.assertEqual(len(heatmaps[0]["names"]), 40, "the heatmap did not get the class's 40 compounds")

    def test_the_caption_says_what_could_have_passed(self):
        # ceil(log(0.05 / 8) / log(0.723)) = 16 at this p0 with eight classes
        self.assertIn("at least <b>16</b> measured compounds", self.result["summary"])


INTERACTION = r"""
me.drawClassMap();
var detail = registry.classActivityDetail;
var svg = registry.classActivityMap.children[0];
function selectedNames() {
    return svg.querySelectorAll("g.paClassMapMark").filter(function (g) {
        return g.classList.contains("paIsSelected"); }).map(function (g) { return g.getAttribute("data-class"); });
}
var steps = [];
me.paintClassCompounds("Bases");
steps.push({step: "click Bases", head: (detail.innerHTML.match(/<h4>([^<]*)<\/h4>/) || [])[1],
            selected: selectedNames(), lastHeatmap: drawn.filter(function (d) { return d.kind === "heatmap"; }).pop().names});
me.selectClass(null);
steps.push({step: "close", open: detail.classList.contains("paIsOpen"), html: detail.innerHTML, selected: selectedNames()});
me.drawClassMap();
steps.push({step: "redraw after close", open: registry.classActivityDetail.classList.contains("paIsOpen"),
            html: registry.classActivityDetail.innerHTML});
process.stdout.write(JSON.stringify(steps));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class SamePanelInteractionTest(unittest.TestCase):

    def test_clicking_a_row_swaps_the_detail_and_closing_it_sticks(self):
        steps = run_in_node(INTERACTION)
        click, close, redraw = steps
        self.assertEqual(click["head"], "Bases")
        self.assertEqual(click["selected"], ["Bases"])
        self.assertEqual(sorted(click["lastHeatmap"]), ["C6_0", "C6_1", "C6_2"],
                         "the heatmap was not redrawn with the clicked class's compounds")
        self.assertFalse(close["open"])
        self.assertEqual(close["html"], "")
        self.assertEqual(close["selected"], [])
        # A resize redraws the chart; it must not reopen what the user closed.
        self.assertFalse(redraw["open"])
        self.assertEqual(redraw["html"], "")


class SourceStructureTest(unittest.TestCase):
    """Holds with or without a JavaScript runtime available."""

    def test_the_compounds_open_inside_the_card_not_in_a_second_box(self):
        source = read(STEP3_VIEWS)
        start = source.index("function PA_Step3MetaboliteView() {")
        init = method(source, "initComponent", start)
        self.assertIn('id="classActivityDetail"', init)
        self.assertNotIn("classificationPlotPanel", source,
                         "the second 'Expression Value' contentbox is back")

    def test_the_orphaned_panel_helpers_are_gone(self):
        util = read(UTIL)
        for name in ("revealPlotPanel", "fitPlotPanel"):
            self.assertNotIn("function " + name, util,
                             "%s has no caller left in the client" % name)

    def test_the_view_still_parses(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed")
        for path in (STEP3_VIEWS, UTIL):
            completed = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, "%s does not parse:\n%s" % (path, completed.stderr))


Y_AXIS = r"""
var Ext = { apply: function (t, s) { for (var k in s) t[k] = s[k]; return t; },
    String: { htmlEncode: function (s) { return String(s).replace(/[&<>"']/g, function (c) {
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); } } };
%s
%s
var crossing = paPlotYAxis({min: -0.3, max: 0.3, absMin: -2, absMax: 2}, -0.5, 0.25, {yAxisTitle: "Metabolomics"});
var positive = paPlotYAxis({min: 0.6, max: 1.5, absMin: 0, absMax: 3}, 0.1, 2.0, {});
var empty = paPlotYAxis({min: -0.3, max: 0.3, absMin: -2, absMax: 2}, Infinity, -Infinity, {});
function shape(a) { return {min: a.min === undefined ? null : a.min, max: a.max === undefined ? null : a.max, title: a.title.text,
    zero: a.plotLines.filter(function (l) { return l.value === 0; }).length,
    bands: a.plotBands.map(function (b) { return [+b.from.toFixed(3), +b.to.toFixed(3)]; })}; }
process.stdout.write(JSON.stringify({crossing: shape(crossing), positive: shape(positive), empty: shape(empty)}));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class PlotYAxisTest(unittest.TestCase):
    """The line chart's y axis beside the heatmap."""

    def setUp(self):
        source = read(STEP3_VIEWS)
        script = Y_AXIS % (var_function(source, "paScaleClipLine"), var_function(source, "paPlotYAxis"))
        directory = tempfile.mkdtemp(prefix="paintomics-yaxis-")
        try:
            path = os.path.join(directory, "axis.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            completed = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            if completed.returncode != 0:
                raise AssertionError("node failed:\n%s" % completed.stderr)
            self.result = json.loads(completed.stdout)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_a_series_that_crosses_zero_gets_a_symmetric_axis_with_a_zero_line(self):
        axis = self.result["crossing"]
        self.assertAlmostEqual(axis["min"], -axis["max"], places=9,
                               msg="-0.4 .. 0.2 read as if down were bigger than up")
        self.assertEqual(axis["zero"], 1)
        self.assertEqual(axis["title"], "Metabolomics")
        # Only the low side runs past the colour scale (-0.5 < -0.3), so only it is shaded.
        self.assertEqual(len(axis["bands"]), 1)
        self.assertEqual(axis["bands"][0][1], -0.3)
        self.assertEqual(axis["bands"][0][0], axis["min"])

    def test_an_all_positive_series_is_not_forced_through_zero(self):
        axis = self.result["positive"]
        self.assertGreater(axis["min"], -0.3)
        self.assertEqual(axis["zero"], 0)
        self.assertEqual(len(axis["bands"]), 2, "both ends run past the 0.6..1.5 colour scale")

    def test_no_data_leaves_the_axis_alone(self):
        axis = self.result["empty"]
        self.assertIsNone(axis.get("min"))
        self.assertEqual(axis["bands"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
