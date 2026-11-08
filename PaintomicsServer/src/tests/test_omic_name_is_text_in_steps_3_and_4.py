"""The omic's name is text wherever Steps 3 and 4 put it into the page.

The name is whatever the user typed in Step 1, and a job opened with "allow sharing"
shows it to anyone holding the link. Steps 3 and 4 wrote it into HTML as it came:
the colour-by options of the pathway network, the pathway table's column headers and
the tooltips of their cells, the pathway details panel, the Stouffer weights, the
visual settings, the global heatmap's panel and headings, and the row labels and
tooltips of the feature heatmaps. A name such as "<img src=x onerror=...>" ran there.
The class map's axis title and the feature popup's legend are plain chart labels, but
the bundled Highcharts 6 reads a link in one and makes it clickable script.

Each check runs the real view code under node, with a stub page that records the HTML
it is handed, and parses that HTML here: the hostile name must create no element and
no attribute, read back as itself where it is text, and decode to itself where the
code reads it back (a radio's value, a checkbox's id). Ids and classes made from the
name carry paDomKey's safe key. The replicate card of Step 2 is checked for the column
headers it quotes, and the dialog an upload refusal opens for its wording.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_omic_name_is_text_in_steps_3_and_4
"""
import json
import os
import re
import shutil
import sys
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from src.tests.test_echoed_request_values_are_text import DIALOG_HARNESS, _node
from src.tests.test_status_poll_retries_unreadable_answers import UTIL, extract_function, read
from src.tests.test_status_poll_retries_unreadable_answers import extract_block as extract_plain
from src.tests.test_step1_invalid_form_names_the_field import extract_block

CLIENT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "PaintomicsClient", "public_html"))
VIEWS = os.path.join(CLIENT_ROOT, "app", "view", "PathwayAcquisitionViews")
STEP2 = os.path.join(VIEWS, "PA_Step2Views.js")
STEP3 = os.path.join(VIEWS, "PA_Step3Views.js")
STEP4 = os.path.join(VIEWS, "PA_Step4Views.js")

# Closes a double-quoted attribute, opens an element, and carries a single quote too.
HOSTILE = "<img src=x onerror=console.log(1)> \"autofocus onfocus=\"x' &amp;"
HOSTILE_CONDITION = "<img src=x onerror=console.log(2)> T0"
# A link in a chart label: see _Checks.assertPlainChartLabel().
HOSTILE_LINK = "<a href=\"javascript:console.log('link')\">Metabolomics"

# The entities Highcharts 6.2 turns back into characters when it draws a label, in
# its own order (SVGRenderer.escapes in js/libs/highcharts/highcharts.js).
HIGHCHARTS_ENTITIES = (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"'))

HAS_NODE = shutil.which("node") is not None


def method(source, constructor, header, what):
    """The method `header` of the view `constructor`, as written in the file."""
    start = source.find("function %s(" % constructor)
    if start == -1:
        raise AssertionError("%s is missing from %s" % (constructor, what))
    return extract_block(source[start:], header, what)


def helper(source, name, what):
    """A file-level `var name = function (...) {...};` helper."""
    return extract_plain(source, "var %s = function" % name, what) + ";"


class _Parsed(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements, self.text = [], []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag

    def handle_data(self, data):
        self.text.append(data)

    def tags(self):
        return [tag for tag, _ in self.elements]

    def attributeNames(self):
        return [name for _, attrs in self.elements for name in attrs]

    def joined(self):
        return "".join(self.text)


class _Checks(unittest.TestCase):

    def assertInert(self, html, allowed=None):
        """No element and no attribute came from the name: no img, no handler."""
        parsed = _Parsed(html)
        self.assertNotIn("img", parsed.tags(), "the omic's name made an element: %s" % html[:600])
        for name in parsed.attributeNames():
            self.assertFalse(name.startswith("on") or name == "autofocus",
                             "the omic's name made an attribute %r: %s" % (name, html[:600]))
        if allowed is not None:
            self.assertEqual(set(parsed.tags()) - set(allowed), set(), html[:600])
        return parsed

    def assertPlainChartLabel(self, label, name):
        """A label Highcharts 6.2 draws without useHTML carries no markup and reads `name`.

        Such a label is SVG text, but SVGRenderer.buildText still reads "<...>" in it
        and copies the class, style and href it finds onto the text (an href becomes an
        onclick that sets location.href). It looks for them up to the first ">", in
        quotes, so a label with none of < > " ' carries nothing; Highcharts then turns
        its entities back into characters, and the name reads as typed, once.
        """
        self.assertFalse(set(label) & set("<>\"'"), "a chart label holds markup: %r" % label)
        for entity, char in HIGHCHARTS_ENTITIES:
            label = label.replace(entity, char)
        self.assertEqual(label, name, "encoded twice, or not the name")


PRELUDE = r"""
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const captured = [];
function anything(label) {
    return new Proxy(function () {}, {
        get(t, k) {
            if (k === Symbol.toPrimitive) return function () { return 0; };
            if (k === "then") return undefined;
            if (k === "length") return 0;
            return anything(label + "." + String(k));
        },
        apply() { return anything(label + "()"); }
    });
}
function $(selector) {
    const sel = typeof selector === "string" ? selector : "<node>";
    const self = new Proxy(function () {}, {
        get(t, k) {
            if (k === Symbol.toPrimitive) return function () { return 0; };
            if (k === "then") return undefined;
            if (k === "length") return 1;
            if (["html", "append", "prepend"].indexOf(k) !== -1) {
                return function (value) {
                    if (typeof value === "string") captured.push({selector: sel, how: k, html: value});
                    return self;
                };
            }
            if (k === "val" || k === "attr") return function () { return arguments.length > 1 ? self : undefined; };
            if (k === "is") return function () { return false; };
            return function () { return self; };
        },
        apply() { return self; }
    });
    return self;
}
$.each = function (list, fn) { for (var i = 0; i < list.length; i++) fn(i, list[i]); };
const created = [];
const Ext = {
    String: {htmlEncode(text) { return String(text).replace(/[&<>"']/g, function (c) { return entities[c]; }); }},
    Object: {isEmpty(o) { return !o || Object.keys(o).length === 0; }},
    apply: Object.assign,
    widget(cfg) { captured.push({selector: "Ext.widget", how: "config", cfg: cfg}); return anything("widget"); },
    create(cls, cfg) { created.push({cls: cls, cfg: cfg}); return anything("created"); },
    ComponentQuery: {query() { return []; }}
};
function initializeTooltips() {}
function paStep3NormaliseColorBy() {}
function paScaleThumb() { return ""; }
function getMinMax() { return {min: 0, max: 1, absMin: 0, absMax: 1}; }
function getColor() { return "#888"; }
function paCellGap() { return {borderColor: "#fff", borderWidth: 1}; }
function paConditionAxis(maxX, header) { return {categories: (header || []).slice(1)}; }
function paSharedOmicHeader() { return []; }
function paJobModel() { return null; }
function paMetagenesForDisplay(jobModel, omicName, metagenes) { return metagenes; }
function paInkOnCell() { return "#000"; }
const charts = [];
const Highcharts = {Chart: function (cfg) { charts.push(cfg); this.chartHeight = 100; }};
const H = %(hostile)s, HC = %(condition)s, L = %(link)s;
%(helpers)s
"""


def _helpers(step3):
    code = [helper(step3, name, "PA_Step3Views.js") for name in
            ("paEscapeAttribute", "paTruncateTail", "paTruncateHead", "paRowLabel")]
    # Absent before this change; left out then, so the views run as they were and
    # the checks below fail on what they render rather than on a missing helper.
    for name in ("paDomKey", "paOmicFieldSuffix"):
        if "var %s = function" % name in step3:
            code.append(helper(step3, name, "PA_Step3Views.js"))
    return "\n".join(code)


def _run(script, step3):
    return _node((PRELUDE % {"hostile": json.dumps(HOSTILE), "condition": json.dumps(HOSTILE_CONDITION),
                             "link": json.dumps(HOSTILE_LINK), "helpers": _helpers(step3)}) + script)


@unittest.skipUnless(HAS_NODE, "node is not installed")
class Step3Test(_Checks):

    @classmethod
    def setUpClass(cls):
        step3 = read(STEP3)
        cls.colorBy = _run(r"""
const view = {dbid: "KEGG", database: "KEGG",
    getParent() { return {getVisualOptions() { return {colorBy: H, minFeatures: 0.5}; }, component: anything("panel")}; },
    getModel() { return {getGeneBasedInputOmics() { return [{omicName: "Gene expression"}, {omicName: H}]; },
                         getMultiplePvaluesMethods() { return ["FDR BH"]; }}; },
    getComponent() { return anything("component"); }, getController() { return anything("controller"); }};
(function () { %(block)s; }).call(view);
view.updateObserver();
process.stdout.write(JSON.stringify(captured.filter(function (c) { return c.selector === "#colorByContainer_KEGG"; })));
""" % {"block": method(step3, "PA_Step3PathwayNetworkView", "this.updateObserver = function() {", "PA_Step3Views.js")},
            step3)

        cls.details = _run(r"""
const omics = [H + " a", H + " b", H + " c"];
const significance = {}; significance[omics[0]] = [[3, 1, 0.01], [2, 1, 0.2]];
const metagenes = {}; metagenes[omics[1]] = []; metagenes[omics[2]] = [{values: [1, 2]}];
const matched = {}; matched[omics[0]] = {Hk1: {isRelevant: true, isRelevantAssociation: false, inputNames: ["15275"]}};
const parents = {
    PA_Step3JobView: {getModel() { return {conditionNames: [HC, "T1"]}; }},
    PA_Step4PathwayView: {getMatchedFeatures() { return matched; }}};
const view = {
    getComponent() { return anything("component"); },
    getModel() { return {getName() { return "Glycolysis"; }, getClassification() { return "Metabolism;Carbohydrate"; },
                         getSignificanceValues() { return significance; }, getGlobalOmicPvalues() { return {}; },
                         metagenes: metagenes, getSource() { return "KEGG"; }, getID() { return "mmu00010"; }}; },
    getParent(name) { return name ? (parents[name] || null) : {getName() { return "PA_Step3PathwayDetailsPanel"; }}; },
    generateHeatmap() {}, generatePlot() {}, fitToContent() { return this; }};
(function () { %(block)s; }).call(view);
view.updateObserver(omics, {}, {});
process.stdout.write(JSON.stringify(captured));
""" % {"block": method(step3, "PA_Step3PathwayDetailsView",
                       "this.updateObserver = function(omicDataType, dataDistributionSummaries, visualOptions)",
                       "PA_Step3Views.js")}, step3)

        cls.table = _run(r"""
const fieldsSeen = [];
function job(conditionNames) {
    const summary = [null, null, null, null, {KEGG: {}}, {KEGG: {}}];
    summary[4].KEGG[H] = 10; summary[5].KEGG[H] = 3;
    const view = {model: {getPathways() { return {}; }, conditionNames: conditionNames, summary: summary},
                  getParent() { return {getVisualOptions() { return {selectedAdjustedMethod: "FDR BH"}; }}; }};
    (function () { %(block)s; }).call(view);
    const columns = [], rowModel = {};
    view.generateColumns([{omicName: H}], columns, rowModel, false, ["FDR BH"], []);
    Object.keys(rowModel).forEach(function (key) { fieldsSeen.push(key, rowModel[key].name); });
    const record = {get(key) { return key === "source" ? "KEGG" : 1; }};
    const flat = [];
    columns.forEach(function (c) { flat.push(c); (c.columns || []).forEach(function (s) { flat.push(s); }); });
    return flat.map(function (column) {
        const filled = {column: column}, empty = {column: column};
        if (column.renderer) { column.renderer(0.01, filled, record); column.renderer("-", empty, record); }
        return {text: column.text, cls: column.cls || "", omic: column.omic, dataIndex: column.dataIndex || "",
                tipFilled: filled.tdAttr || "", tipEmpty: empty.tdAttr || ""};
    });
}
const result = {one: job([]), conditions: job([HC, "T1"])};
result.fields = fieldsSeen;
process.stdout.write(JSON.stringify(result));
""" % {"block": method(step3, "PA_Step3PathwayTableView",
                       "this.generateColumns = function(omics, columns, rowModel, hidden, adjustedPvaluesMethods, combinedPvaluesMethods)",
                       "PA_Step3Views.js")}, step3)

        cls.regulation = _run(r"""
(function () { %(isBlank)s; %(renderer)s; global.render = _stringRenderer; })();
process.stdout.write(JSON.stringify(render(H)));
""" % {"isBlank": extract_block(step3, "var _isBlank = function", "PA_Step3Views.js"),
       "renderer": extract_block(step3, "var _stringRenderer = function", "PA_Step3Views.js")}, step3)

        # The compound class map's line chart, titled with the omic's name.
        cls.classPlot = _run(r"""
function paScaleClipLine() { return {}; }
%(values)s;
%(axis)s;
%(plot)s;
const row = {keggName: "C00031", inputName: "glucose", values: [0.2, 3],
             isRelevant() { return true; }, isRelevantAssociation() { return false; }};
[H, L].forEach(function (name) {
    generatePlot("classDetail_pl", name, [row], {}, null, {}, ["#id", "T0", "T1"], {yAxisTitle: name});
});
process.stdout.write(JSON.stringify(charts.map(function (chart) { return chart.yAxis.title.text; })));
""" % {"values": helper(step3, "paValuesForHeader", "PA_Step3Views.js"),
       "axis": extract_block(step3, "var paPlotYAxis = function", "PA_Step3Views.js"),
       "plot": extract_block(step3, "let generatePlot = function", "PA_Step3Views.js")}, step3)
        cls.source = step3

    def test_the_colour_by_option_is_text_and_its_value_is_the_name(self):
        self.assertEqual(len(self.colorBy), 1)
        parsed = self.assertInert(self.colorBy[0]["html"], {"div", "input", "label"})
        radios = [attrs for tag, attrs in parsed.elements if tag == "input"]
        self.assertEqual([r["value"] for r in radios], ["classification", "Gene expression", HOSTILE],
                         ".val() reads the value decoded, and colorBy is compared to the name")
        self.assertIn("checked", radios[2], "the stored colouring is still the one checked")
        self.assertIn(HOSTILE, parsed.joined())
        ids = [r["id"] for r in radios]
        for value in ids:
            self.assertRegex(value, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(len(set(ids)), 3)
        labels = [attrs["for"] for tag, attrs in parsed.elements if tag == "label"]
        self.assertEqual(labels, ids)

    def test_the_cluster_images_ask_for_the_omic_by_an_encoded_path(self):
        self.assertIn('"/output/" + encodeURIComponent(visualOptions.colorBy) + "_cluster_"', self.source)

    def test_the_details_panel_shows_the_names_as_text(self):
        table = [c["html"] for c in self.details if "pathwaySummaryTable" in c["selector"]]
        self.assertEqual(len(table), 2, "the table and the matched-features list")
        parsed = self.assertInert("".join(table))
        self.assertIn(HOSTILE + " a", parsed.joined())
        self.assertIn("Matched features: " + HOSTILE + " a", parsed.joined())
        self.assertIn(HOSTILE_CONDITION, parsed.joined(), "a condition name is a header of the user's file")
        for tag, attrs in parsed.elements:
            for name in ("data-omic", "data-id", "id"):
                if name in attrs:
                    self.assertRegex(attrs[name], r"^[A-Za-z0-9_-]+$")
            if tag == "tr" and "condition-row" in attrs.get("class", ""):
                self.assertRegex(attrs["class"], r"^condition-row cond-row-[A-Za-z0-9_-]+$")
        # The expand icons find their rows and list by the same key.
        keys = {attrs["data-omic"] for tag, attrs in parsed.elements if "data-omic" in attrs}
        rows = {attrs["class"].split("cond-row-")[1] for tag, attrs in parsed.elements
                if "cond-row-" in attrs.get("class", "")}
        lists = {attrs["id"][len("matchedlist_"):] for tag, attrs in parsed.elements
                 if attrs.get("id", "").startswith("matchedlist_")}
        self.assertEqual(keys, rows)
        self.assertEqual(keys, lists)

    def test_the_details_panel_headings_are_text(self):
        plots = "".join(c["html"] for c in self.details if "pathwayPlotwrappers" in c["selector"])
        parsed = self.assertInert(plots)
        for suffix in (" a", " b", " c"):
            self.assertIn(HOSTILE + suffix, parsed.joined())
        for tag, attrs in parsed.elements:
            if "id" in attrs:
                self.assertRegex(attrs["id"], r"^[A-Za-z0-9_-]+$")

    def test_the_table_headers_are_text(self):
        for job in ("one", "conditions"):
            for column in self.table[job]:
                with self.subTest(job=job, header=column["text"][:40]):
                    parsed = self.assertInert(column["text"], {"i", "br"})
                    for tag, attrs in parsed.elements:
                        if "data-omic" in attrs:
                            self.assertRegex(attrs["data-omic"], r"^[A-Za-z0-9_-]+$")
                    for cls in column["cls"].split():
                        self.assertRegex(cls, r"^[A-Za-z0-9_-]+$")
        headers = [_Parsed(c["text"]).joined() for c in self.table["one"]]
        self.assertTrue(any(HOSTILE.replace(" ", "", 1) in text for text in headers), headers)
        self.assertTrue(any(HOSTILE_CONDITION in _Parsed(c["text"]).joined() for c in self.table["conditions"]))

    def test_the_tables_field_names_hold_no_quote(self):
        """ExtJS compiles field names into the source of its record extractor: a quote
        in one ended a string literal there, and Step 3's table failed to draw
        ("Invalid left-hand side in assignment") or ran the rest of the name."""
        self.assertTrue(self.table["fields"])
        for name in self.table["fields"] + [c["dataIndex"] for c in self.table["one"] + self.table["conditions"]]:
            if name:
                self.assertRegex(name, r"^[A-Za-z0-9_ -]+$")
        # The table's data, its columns and the p-value updates name the fields one way.
        for old in ('"-" + j.toLowerCase().replace(/ /g, "-")', '"adjpval%fdrterm%-" + ',
                    'rowData["pValue-" + ', '"-" + omic.omicName.toLowerCase()'):
            self.assertNotIn(old, self.source)

    def test_the_expand_icon_opens_its_own_condition_columns(self):
        columns = self.table["conditions"]
        key = [attrs["data-omic"] for c in columns for tag, attrs in _Parsed(c["text"]).elements
               if "data-omic" in attrs][0]
        conditions = [c for c in columns if ("condition-column-" + key) in c["cls"].split()]
        self.assertEqual(len(conditions), 2)

    def test_the_cell_tips_are_text_after_the_attribute_is_read(self):
        """data-qtip is read decoded and shown as HTML: the name must be encoded twice
        in the attribute's source and once in what the tip shows."""
        seen = 0
        for job in ("one", "conditions"):
            for column in self.table[job]:
                for tip in (column["tipFilled"], column["tipEmpty"]):
                    if not tip:
                        continue
                    seen += 1
                    cell = _Parsed("<td " + tip + ">")
                    self.assertEqual(cell.tags(), ["td"], tip)
                    shown = cell.elements[0][1]["data-qtip"]
                    parsed = self.assertInert(shown, {"b", "i", "table", "thead", "th", "tr", "td", "br"})
                    self.assertNotIn("&lt;", parsed.joined(), "encoded twice on screen")
        self.assertGreater(seen, 4)
        filled = [c["tipFilled"] for c in self.table["one"] if c["tipFilled"]]
        self.assertTrue(any("contingencyTable" in tip for tip in filled),
                        "the counts are looked up by the omic's name, not by its encoded header")
        # The tip's title is the header with its line break turned back into the space.
        self.assertTrue(any(HOSTILE in _Parsed(_Parsed("<td " + tip + ">").elements[0][1]["data-qtip"]).joined()
                            for tip in filled))

    def test_the_regulation_tables_omic_column_is_text(self):
        self.assertInert(self.regulation)
        self.assertEqual(_Parsed(self.regulation).joined(), HOSTILE)

    def test_the_class_maps_axis_title_is_text(self):
        self.assertEqual(len(self.classPlot), 2)
        for title, name in zip(self.classPlot, (HOSTILE, HOSTILE_LINK)):
            with self.subTest(name=name[:20]):
                self.assertPlainChartLabel(title, name)

    def test_the_stouffer_weights_are_keyed_by_the_name_not_the_label(self):
        code = re.sub(r"//[^\n]*", "", self.source)
        self.assertIn("fieldLabel: Ext.String.htmlEncode(omic),", code)
        self.assertIn("stoufferWeigths[omicSlider.omicName] = ", code)
        self.assertIn("omicSlider.setValue(defaultValues[omicSlider.omicName]);", code)
        self.assertNotIn("getFieldLabel()", code)


@unittest.skipUnless(HAS_NODE, "node is not installed")
class Step4Test(_Checks):

    @classmethod
    def setUpClass(cls):
        step3, step4 = read(STEP3), read(STEP4)
        cls.settings = _run(r"""
const visual = {visibleOmics: [H + "#genebased"], colorReferences: {}, colorScale: "bwr"};
visual.colorReferences[H] = "custom";
const pathway = {getDataDistributionSummaries() { return {}; }, visualOptions: {}};
const parent = {getVisualOptions() { return visual; },
                getGeneBasedInputOmics() { return [{omicName: H}]; },
                getCompoundBasedInputOmics() { return [{omicName: H + " compounds"}]; }};
const view = {getParent(name) { return name ? (name === "PA_Step4PathwayView" ? pathway : null) : parent; },
              getModel() { return anything("model"); }, toggle() {}, applyVisualSettings() {}};
(function () { %(block)s; }).call(view);
view.initComponent();
const box = captured.filter(function (c) { return c.selector === "Ext.widget"; })[0].cfg.items[0];
box.listeners.boxready.call(box);
process.stdout.write(JSON.stringify({html: box.html, sliders: created.map(function (c) { return c.cfg; })}));
""" % {"block": method(step4, "PA_Step4VisualOptionsView", "this.initComponent = function()", "PA_Step4Views.js")},
            step3)

        cls.configurator = _run(r"""
const significance = {}; significance[H] = [5, 2, 0.01]; significance["Gene expression"] = [4, 1, 0.2];
const model = {getSignificanceValues() { return significance; }};
const view = {model: model, getModel() { return model; }, showConfigurator: false,
              getParent() { return anything("parent"); }};
(function () { %(block)s; }).call(view);
view.initComponent();
process.stdout.write(JSON.stringify(captured.filter(function (c) { return c.selector === "Ext.widget"; })[0].cfg.html));
""" % {"block": method(step4, "PA_Step4GlobalHeatmapView", "this.initComponent = function()", "PA_Step4Views.js")},
            step3)

        cls.feature = _run(r"""
const values = {isRelevant() { return true; }, isRelevantAssociation() { return false; },
                getValues() { return [1, 2]; }, inputName: "15275", originalName: undefined};
const feature = {name: "Hk1", getOmicValues(omic) { return omic === H ? [values] : null; }};
const headers = {}; headers[H] = ["#id", HC, "T1"];
const jobModel = {getReplicateMode() { return "replicates"; }, getOmicHeaders() { return headers; }};
const view = {getModel() { return {getFeature() { return feature; }}; },
              getParent(name) { return name === "PA_Step4JobView" ? {getModel() { return jobModel; }} : null; }};
(function () { %(block)s; }).call(view);
view.generateHeatmap("tip", [H + "#genebased", H + " none#genebased"], {}, {colorReferences: {}, colorScale: "bwr"});
const chart = charts[0];
const rows = chart.series.filter(Boolean);
process.stdout.write(JSON.stringify({
    tips: rows.map(function (s) { return chart.tooltip.formatter.call({point: {series: {name: s.name}, index: 0, value: 1.5}}); }),
    labels: chart.yAxis.categories.map(function (value) { return chart.yAxis.labels.formatter.call({value: value}); })
}));
""" % {"block": method(step4, "PA_Step4KeggDiagramFeatureView",
                       "this.generateHeatmap = function(divID, visibleOmics, dataDistributionSummaries, visualOptions)",
                       "PA_Step4Views.js")}, step3)

        # The feature popup's line chart: one legend entry per omic, or per row of
        # it, named after the omic and the feature.
        cls.featurePlot = _run(r"""
function scaleValue(value) { return value; }
function paReferenceLine() { return {}; }
function paFitReferenceLabels() {}
Highcharts.Chart.prototype.yAxis = [{setExtremes() {}}];
const one = {getValues() { return [1, 2]; }, inputName: "15275", originalName: undefined};
const two = [{getValues() { return [1, 2]; }, inputName: "C00031", originalName: L},
             {getValues() { return [3, 4]; }, inputName: "C00032", originalName: undefined}];
const feature = {getOmicValues(omic) { return omic === H ? [one] : (omic === L ? two : null); }};
const jobModel = {getReplicateMode() { return "replicates"; }};
const view = {getModel() { return {getFeature() { return feature; }}; },
              getParent(name) { return name === "PA_Step4JobView" ? {getModel() { return jobModel; }} : null; }};
(function () { %(block)s; }).call(view);
view.generatePlot("plot", [H + "#genebased", L + "#compoundbased"], {}, {colorReferences: {}});
const chart = charts[0];
// Highcharts' own default when the chart names none.
const format = (chart.legend && chart.legend.labelFormatter) || function () { return this.name; };
process.stdout.write(JSON.stringify({enabled: !chart.legend || chart.legend.enabled !== false,
    entries: chart.series.map(function (s) { return {name: s.name, label: format.call({name: s.name})}; })}));
""" % {"block": method(step4, "PA_Step4KeggDiagramFeatureView",
                       "this.generatePlot = function(divID, visibleOmics, dataDistributionSummaries, visualOptions)",
                       "PA_Step4Views.js")}, step3)
        cls.source = step4

    def test_the_visual_settings_show_the_names_as_text(self):
        parsed = self.assertInert(self.settings["html"])
        for name in (HOSTILE, HOSTILE + " compounds"):
            self.assertIn(name, parsed.joined())
        ids = {attrs.get("id") for tag, attrs in parsed.elements}
        names = {attrs.get("name") for tag, attrs in parsed.elements}
        # Read back with .attr(), decoded: visibleOmics and colorReferences keep the names.
        self.assertIn(HOSTILE + "#genebased", ids)
        self.assertIn(HOSTILE + " compounds#compoundbased", ids)
        self.assertIn("colorByCheckbox_" + HOSTILE, names)
        checked = [attrs for tag, attrs in parsed.elements
                   if attrs.get("name") == "colorByCheckbox_" + HOSTILE and "checked" in attrs]
        self.assertEqual([attrs["value"] for attrs in checked], ["custom"])

    def test_each_custom_slider_finds_its_own_box(self):
        ids = {attrs.get("id") for tag, attrs in _Parsed(self.settings["html"]).elements}
        self.assertEqual(len(self.settings["sliders"]), 2)
        for slider in self.settings["sliders"]:
            self.assertIn(slider["renderTo"], ids, "renderTo looks the box up by its decoded id")

    def test_no_selector_quotes_the_name(self):
        code = re.sub(r"//[^\n]*", "", self.source)
        self.assertNotIn("[name=\"colorByCheckbox_' + omic", code)
        self.assertNotIn("[name=\"customslider_' + omicName", code)

    def test_the_global_heatmap_configurator_is_text(self):
        parsed = self.assertInert(self.configurator)
        self.assertIn(HOSTILE + ":", parsed.joined(), "the k-means label")
        boxes = [attrs for tag, attrs in parsed.elements if tag == "input" and attrs.get("type") == "checkbox"
                 and attrs.get("id", "").startswith("lateralOptionsSelector-")]
        self.assertIn(HOSTILE, [b["value"] for b in boxes], ".val() gives updateObserver the name")
        for tag, attrs in parsed.elements:
            for name in ("id", "name", "for"):
                if attrs.get(name, "").startswith(("lateralOptionsSelector-", "kMeansSelector-")):
                    self.assertRegex(attrs[name], r"^[A-Za-z0-9_-]+$",
                                     "updateObserver selects input[name=...] unquoted")

    def test_the_feature_heatmap_tooltip_and_row_labels_are_text(self):
        self.assertEqual(len(self.feature["tips"]), 2)
        for html in self.feature["tips"]:
            parsed = self.assertInert(html, {"b", "br", "i"})
            self.assertIn(HOSTILE, parsed.joined())
        self.assertTrue(any(HOSTILE_CONDITION in _Parsed(html).joined() for html in self.feature["tips"]),
                        "the column header the tooltip names is the user's too")
        self.assertEqual(len(self.feature["labels"]), 2)
        for html in self.feature["labels"]:
            parsed = self.assertInert(html, {"span", "i", "br"})
            self.assertNotIn("&lt;", parsed.joined(), "encoded twice on screen")

    def test_the_feature_plots_legend_is_text(self):
        self.assertTrue(self.featurePlot["enabled"], "the legend is drawn")
        entries = self.featurePlot["entries"]
        self.assertEqual([e["name"] for e in entries],
                         [HOSTILE, HOSTILE_LINK + " [" + HOSTILE_LINK + "]", HOSTILE_LINK + " [C00032 - 2]"])
        for entry in entries:
            with self.subTest(name=entry["name"][:20]):
                self.assertPlainChartLabel(entry["label"], entry["name"])

    def test_the_heatmap_ids_and_headings_go_through_the_key_and_the_encoder(self):
        code = re.sub(r"//[^\n]*", "", self.source)
        self.assertEqual(code.count('"globalHeatmapContainer-" + paDomKey('), 3)
        self.assertIn('"<h3>" + Ext.String.htmlEncode(omicName) + "</h3>"', code)
        self.assertEqual(code.count('paDomKey(omicNames[i].replace(" ", "_"))'), 1)
        self.assertEqual(code.count('paDomKey(omicName.replace(" ", "_"))'), 1)
        self.assertIn("\"  <h3>\" + Ext.String.htmlEncode(omicNames[i].replace(\"#\", \" \")) + \"<span>", code)
        self.assertIn("value='\"+ Ext.String.htmlEncode(omicNames[i]) +\"'", code)


@unittest.skipUnless(HAS_NODE, "node is not installed")
class TheDomKeyTest(_Checks):

    def test_ordinary_names_keep_their_ids_and_others_get_safe_distinct_ones(self):
        step3 = read(STEP3)
        names = ["gene-expression", "Gene_expression", "proteomics-(tmt)", "rna.seq", "rna,seq", HOSTILE, "ARN-séq"]
        keys = _node("%s\nprocess.stdout.write(JSON.stringify(%s.map(paDomKey)));"
                     % (helper(step3, "paDomKey", "PA_Step3Views.js"), json.dumps(names)))
        self.assertEqual(keys[:2], ["gene-expression", "Gene_expression"])
        for key in keys:
            self.assertRegex(key, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(len(set(keys)), len(keys))


@unittest.skipUnless(HAS_NODE, "node is not installed")
class Step2CardTest(_Checks):

    def test_the_replicate_card_quotes_the_files_headers_as_text(self):
        source = read(STEP2)
        card = _node(r"""
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const Ext = {String: {htmlEncode(t) { return String(t).replace(/[&<>"']/g, function (c) { return entities[c]; }); }}};
const H = %s, HC = %s;
const view = {};
(function () { %s; }).call(view);
process.stdout.write(JSON.stringify(view._renderOmicCard({
    omicName: H, omicHeader: ["#id", HC + "_1", HC + "_2", HC + " odd"],
    replicateDetection: {sampleHeader: [HC], mapping: [0, 0, -1], groups: [[0, 1]], status: "partial", unmatched: [2]}})));
""" % (json.dumps(HOSTILE), json.dumps(HOSTILE_CONDITION),
       extract_block(source, "this._renderOmicCard = function(omic)", "PA_Step2Views.js")))
        parsed = self.assertInert(card)
        self.assertIn(HOSTILE_CONDITION + " ← " + HOSTILE_CONDITION + "_1, " + HOSTILE_CONDITION + "_2", parsed.joined())
        self.assertIn("(" + HOSTILE_CONDITION + " odd)", parsed.joined())


@unittest.skipUnless(HAS_NODE, "node is not installed")
class TheRefusalDialogTest(unittest.TestCase):
    """An upload refusal arrives at both dialogs a Step 1 or converter submit uses."""

    def test_the_form_submit_dialog_says_check_your_input_files(self):
        name = "../" + "<img src=x onerror=console.log(1)>" + ".tab"
        body = {"success": False, "message": "InputFileError: AT DataManagementServlet.py: saveFile. "
                "ERROR MESSAGE: The uploaded file name '%s' cannot be used: it contains '/' or '\\'. "
                "A file name must be one name, without '/' or '\\'. Rename the file and try again." % name,
                "extra": {"exc_type": "x", "file_name": "Util.py", "exc_line": "1", "input_error": True}}
        util = read(UTIL)
        results = _node(DIALOG_HARNESS % {
            "client": "\n".join(extract_function(util, fn, "Util.js") for fn in
                                ("showMessage", "showErrorMessage", "showWarningMessage", "serverTextAsHtml",
                                 "ajaxErrorHandler", "extJSErrorHandler", "splitServerError",
                                 "isInvalidSessionAnswer", "readableAnswer", "connectionFailed",
                                 "showConnectionFailedMessage", "describeUnansweredRequest")),
            "bodies": json.dumps({"refusal": body})})
        for how in ("ajax", "extjs"):
            with self.subTest(handler=how):
                result = results["refusal." + how]
                self.assertIn("Please check your input files", result["title"])
                self.assertNotIn("<img", result["body"])
                self.assertIn("&lt;img src=x onerror=console.log(1)&gt;", result["body"])
                self.assertNotIn("&amp;lt;", result["body"])
                self.assertNotIn("Please try again later", result["body"])


if __name__ == "__main__":
    unittest.main()
