#!/usr/bin/env python3
"""Step 3's adjusted combined column is the correction of the raw combined
column beside it, on a job with one relevant column per condition too.

Why this exists
---------------
The enrichment table shows, per combined method (Fisher, Stouffer), a raw
column and an adjusted one (FDR BH, FDR BY). The raw column is the
combination of each omic's Global p-value (totalGlobalPvalues). On a job
whose relevant features file has one column per condition, Step 2 also tests
each condition, and the combined p-value has one value per condition. (A job
with several conditions and a single relevant list has one test and one
combined value, the raw column itself.) Two places compute the adjusted
column:

  * Step 2, once, which gives the value a job is stored with. It corrected
    each condition's combined p-value as its own family and the table showed
    the first condition's correction, the correction of a number the table
    does not show.
  * /pa_adjust_pvalues, whenever a category filter hides rows: the browser
    sends the raw combined column of the rows left (getPvaluesFromStore) and
    the server corrects that.

So the column changed meaning on the first filter, even one that hid a single
pathway: on example dataset 03, by up to 1.0, and 56 FDR BH values crossed
0.05 (PR #234, "Left as is").

The rules pinned here, on a job with three conditions, two omics and two
databases, Step 2 run for real:

  * what the table shows in the adjusted combined column is BH / BY of the
    raw combined column, over the rows the table lists (every database, the
    global map left out), as Step 2 stores it (adjustedTotalGlobalPvalues);
  * with nothing hidden, /pa_adjust_pvalues gives back exactly those values,
    bit for bit, and with a pathway hidden it gives what Step 2 gives for the
    pathways left;
  * each condition's correction is still stored, for the network, which reads
    the smallest of them;
  * a job stored before this rule keeps the value it was stored with;
  * a copy of a job the browser cached before this rule is not restored: the
    client before it dropped adjustedTotalGlobalPvalues when it cached a job,
    so its copy of a job computed after the rule would show the first
    condition's correction again. app.js's PA_JOB_CACHE_SCHEMA moved from 3
    to 4, which makes the boot fetch such a job from the server.

The table's cells come from the real paStep3CombinedCells in PA_Step3Views.js
and the real Pathway model in PathwayModels.js, run under node on what Step 2
sends; the Python checks read the stored values the same way
(_tableShows), so they hold without node too.

Step 2 runs on a small organism held in the KeggInformationManager's cache, so
no MongoDB is needed (as in test_adjusted_pvalues_stay_put).

Usage:
    cd PaintomicsServer
    python -m src.tests.test_combined_fdr_corrects_its_column
"""
import copy
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene, OmicValue
from src.classes.JobInstances import PathwayAcquisitionJob as paj
from src.common.KeggInformationManager import KeggInformationManager
from src.common.Statistics import adjustPvalues
from src.tests.test_adjusted_pvalues_stay_put import (GLOBAL_MAP, METHODS, ORGANISM, PATHWAY_GENES,
                                                      REACTOME_GENES, _organism, _Request)
from src.tests.test_cached_job_model_restore import APP_JS
from src.tests.test_cached_job_model_restore import extract_block as extract_app_block
from src.tests.test_step3_counts_what_the_table_lists import extract_block

multiprocessing.set_start_method("fork", force=True)

CLIENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                      "PaintomicsClient", "public_html", "app"))
STEP3_VIEWS = os.path.join(CLIENT, "view", "PathwayAcquisitionViews", "PA_Step3Views.js")
PATHWAY_MODELS = os.path.join(CLIENT, "model", "PathwayModels.js")
HELPER = "var paStep3CombinedCells = function"

DATABASES = ("KEGG", "Reactome")
N_GENES = 60
CONDITIONS = 3
# Two omics on the same genes, each relevant in other genes per condition, so
# that every condition, the Global p-values and their combination all differ.
# Proteomics maps three quarters of its features, so Stouffer weighs it less.
OMICS = {
    "Gene expression": {"summary": [{"Total": N_GENES}, 0],
                        "relevant": (lambda i: i < 20, lambda i: 20 <= i < 40, lambda i: i % 3 == 0)},
    "Proteomics": {"summary": [{"Total": 30}, 10],
                   "relevant": (lambda i: 10 <= i < 16, lambda i: i >= 45, lambda i: i % 4 == 1)},
}


def _job():
    job = paj.PathwayAcquisitionJob("COMBINEDFDRTEST", None, "/tmp/")
    job.setOrganism(ORGANISM)
    job.setDatabases(list(DATABASES))
    for omic, spec in OMICS.items():
        job.addGeneBasedInputOmic({"omicName": omic, "omicSummary": spec["summary"],
                                   "enrichment": "genes"})
    for i in range(N_GENES):
        gene = Gene("gene%02d" % i)
        gene.setName("gene%02d" % i)
        for omic, spec in OMICS.items():
            value = OmicValue("%s-input%02d" % (omic[:4], i))
            value.setOmicName(omic)
            value.setOriginalName("%s-input%02d" % (omic[:4], i))
            value.setValues([float(i + c) for c in range(CONDITIONS)])
            value.setRelevant([bool(rule(i)) for rule in spec["relevant"]])
            gene.addOmicValue(value)
        job.addInputGeneData(gene)
    return job


def _rawCombined(pathway, method):
    """The raw combined column, as the table shows it (paStep3CombinedCells)."""
    total = (pathway.getTotalGlobalPvalues() or {}).get(method)
    if total is not None:
        return total
    values = pathway.getCombinedSignificancePvalues()[method]
    return values[0] if isinstance(values, list) else values


def _tableShows(pathway, method, adjust):
    """The adjusted combined column, as the table shows it
    (paStep3CombinedCells): the job's correction of the raw column where it
    stored one, else the stored per-condition correction, first condition."""
    stored = (pathway.getAdjustedTotalGlobalPvalues() if hasattr(pathway, "getAdjustedTotalGlobalPvalues")
              else {}).get(method, {}).get(adjust)
    if stored is not None:
        return stored
    value = pathway.adjustedCombinedSignificanceValues[method][adjust]
    return value[0] if isinstance(value, list) else value


def _omicPvalue(pathway, omic):
    globalP = pathway.getGlobalOmicPvalues().get(omic)
    return globalP if globalP is not None else pathway.getSignificanceValues()[omic][0][2]


def _tableRows(pathways):
    """The body getPvaluesFromStore sends for the table's rows: the global map
    is never a row; an omic with no p-value in a pathway reads "-"."""
    rows = {}
    for pathwayID, pathway in pathways.items():
        if pathwayID == GLOBAL_MAP:
            continue
        row = {omic: (_omicPvalue(pathway, omic) if omic in pathway.getSignificanceValues() else "-")
               for omic in OMICS}
        for method in pathway.getCombinedSignificancePvalues():
            row[method] = _rawCombined(pathway, method)
        rows.setdefault(pathway.getSource(), {})[pathwayID] = row
    return rows


def _adjust(body):
    from src.paintomicsserver import Response
    from src.servlets.PathwayAcquisitionServlet import pathwayAcquisitionAdjustPvalues
    # Through JSON, as the browser sends it.
    response = pathwayAcquisitionAdjustPvalues(_Request(json.loads(json.dumps(body))), Response())
    assert response.content.get("success") is True, response.content
    return response.content["adjustedPvalues"]


_STEP2 = {}


def _step2Pathways():
    """Step 2, run once for the module by the first class that asks: three
    conditions, two omics, KEGG and Reactome, the global map matched."""
    if "pathways" not in _STEP2:
        organism = _organism(dict(PATHWAY_GENES, **REACTOME_GENES))
        manager = KeggInformationManager()
        manager.lastOrganisms.append(organism)
        try:
            with mock.patch.object(paj, "MAX_THREADS", 2), mock.patch.object(paj, "MAX_WAIT_THREADS", 60):
                job = _job()
                job.generatePathwaysList()
        finally:
            manager.lastOrganisms.remove(organism)
        _STEP2["pathways"] = job.getMatchedPathways()
    return _STEP2["pathways"]


class _MultiConditionStep2Run(unittest.TestCase):
    """A copy of the module's Step 2 run for each class."""

    @classmethod
    def setUpClass(cls):
        cls.pathways = copy.deepcopy(_step2Pathways())
        cls.listed = {pathwayID: pathway for pathwayID, pathway in cls.pathways.items()
                      if pathwayID != GLOBAL_MAP}
        cls.combinedMethods = sorted(next(iter(cls.listed.values())).getCombinedSignificancePvalues())


class TheStoredColumnIsTheCorrectionOfItsRawColumnTest(_MultiConditionStep2Run):

    def test_the_fixture_is_a_job_where_the_two_rules_differ(self):
        """Guards the test itself: more than one condition, both databases,
        the map, and the correction of the first condition is not the
        correction of the raw column for some row of each method."""
        self.assertIn(GLOBAL_MAP, self.pathways)
        self.assertEqual(set(DATABASES), {p.getSource() for p in self.listed.values()})
        self.assertEqual(["Fisher", "Stouffer"], self.combinedMethods)
        for method in self.combinedMethods:
            for pathway in self.listed.values():
                self.assertEqual(CONDITIONS, len(pathway.getCombinedSignificancePvalues()[method]))
            firstCondition = adjustPvalues({pathwayID: p.getCombinedSignificancePvalues()[method][0]
                                            for pathwayID, p in self.listed.items()})
            rawColumn = adjustPvalues({pathwayID: _rawCombined(p, method) for pathwayID, p in self.listed.items()})
            for adjust in METHODS:
                self.assertTrue(any(firstCondition[adjust][pathwayID] != rawColumn[adjust][pathwayID]
                                    for pathwayID in self.listed), (method, adjust))

    def test_the_table_shows_the_correction_of_the_raw_combined_column(self):
        for method in self.combinedMethods:
            family = adjustPvalues({pathwayID: _rawCombined(p, method) for pathwayID, p in self.listed.items()})
            for pathwayID, pathway in sorted(self.listed.items()):
                for adjust in METHODS:
                    self.assertEqual(family[adjust][pathwayID], _tableShows(pathway, method, adjust),
                                     "%s %s %s: the adjusted combined column is not BH/BY of the raw "
                                     "combined column %r" % (pathwayID, method, adjust,
                                                             _rawCombined(pathway, method)))

    def test_each_condition_is_still_corrected_for_the_network(self):
        for method in self.combinedMethods:
            for condition in range(CONDITIONS):
                family = adjustPvalues({pathwayID: p.getCombinedSignificancePvalues()[method][condition]
                                        for pathwayID, p in self.listed.items()})
                for pathwayID, pathway in self.listed.items():
                    for adjust in METHODS:
                        self.assertEqual(family[adjust][pathwayID],
                                         pathway.adjustedCombinedSignificanceValues[method][adjust][condition],
                                         "%s %s %s condition %d" % (pathwayID, method, adjust, condition))

    def test_the_map_is_a_family_of_its_own(self):
        """A family of one: BH and BY are the raw combined value."""
        theMap = self.pathways[GLOBAL_MAP]
        for method in self.combinedMethods:
            raw = _rawCombined(theMap, method)
            self.assertEqual({adjust: raw for adjust in METHODS},
                             {adjust: _tableShows(theMap, method, adjust) for adjust in METHODS})

    def test_raw_values_are_not_touched(self):
        """Correcting again changes no raw p-value and gives the same answer."""
        again = {pathwayID: copy.deepcopy(p) for pathwayID, p in self.pathways.items()}
        paj.adjustPathwayPvalues(again, ORGANISM)
        for pathwayID, pathway in self.pathways.items():
            self.assertEqual((pathway.significanceValues, pathway.globalOmicPvalues,
                              pathway.combinedSignificancePvalues, pathway.totalGlobalPvalues),
                             (again[pathwayID].significanceValues, again[pathwayID].globalOmicPvalues,
                              again[pathwayID].combinedSignificancePvalues, again[pathwayID].totalGlobalPvalues))
            for method in self.combinedMethods:
                for adjust in METHODS:
                    self.assertEqual(_tableShows(pathway, method, adjust),
                                     _tableShows(again[pathwayID], method, adjust))


class FilteringNothingChangesTheCombinedColumnTest(_MultiConditionStep2Run):

    def assertAnswerIs(self, expected, answer, what):
        for pathwayID, pathway in sorted(expected.items()):
            for method in self.combinedMethods:
                for adjust in METHODS:
                    self.assertEqual(_tableShows(pathway, method, adjust),
                                     answer[pathway.getSource()][method][adjust][pathwayID],
                                     "%s (%s) %s %s %s" % (pathwayID, pathway.getSource(), method, adjust, what))

    def test_the_unfiltered_table_corrects_to_the_stored_values(self):
        answer = _adjust({"pValues": _tableRows(self.pathways)})
        self.assertEqual(set(DATABASES), set(answer))
        self.assertAnswerIs(self.listed, answer, "moves when the unfiltered table is corrected again")

    def test_a_filter_corrects_as_step2_would_over_the_pathways_left(self):
        """Hide the least significant KEGG pathway: the answer for every row
        left, in both databases, is Step 2's correction of the job without it."""
        kegg = [p for p in self.listed.values() if p.getSource() == "KEGG"]
        hidden = max(kegg, key=lambda p: (_rawCombined(p, "Fisher"), p.getID())).getID()
        left = {pathwayID: copy.deepcopy(p) for pathwayID, p in self.pathways.items() if pathwayID != hidden}
        paj.adjustPathwayPvalues(left, ORGANISM)
        rows = _tableRows(self.pathways)
        del rows["KEGG"][hidden]
        answer = _adjust({"pValues": rows})
        self.assertAnswerIs({pathwayID: p for pathwayID, p in left.items() if pathwayID != GLOBAL_MAP},
                            answer, "is not what Step 2 gives without %s" % hidden)


# The real Pathway model and the real cell builder, on what Step 2 sends.
# Model and Observable live in Util.js, which cannot run outside a browser, so
# only those two base declarations are stubbed (as test_cached_job_model_restore).
NODE_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const ctx = {console: {log: console.error, info: console.error, warn: console.error, error: console.error}};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext("function Observable(){}; function Model(){}; Model.prototype = new Observable();", ctx);
vm.runInContext(fs.readFileSync(%(models)s, 'utf8'), ctx, {filename: %(models)s});
vm.runInContext(%(helper)s, ctx);
ctx.sent = JSON.parse(fs.readFileSync(%(sent)s, 'utf8'));
vm.runInContext(
    "var cells = {};" +
    "for (var id in sent) { cells[id] = paStep3CombinedCells(new Pathway(id).loadFromJSON(sent[id])); }", ctx);
process.stdout.write(JSON.stringify(ctx.cells));
"""


def _cellsInTheBrowser(sentPathways):
    """paStep3CombinedCells for each of ``sentPathways`` ({id: what the server
    sends for the pathway}), run under node."""
    with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
        helper = extract_block(handle.read(), HELPER)
    if helper is None:
        raise AssertionError("paStep3CombinedCells is missing from PA_Step3Views.js")
    directory = tempfile.mkdtemp(prefix="paintomics-combined-fdr-")
    try:
        sent = os.path.join(directory, "sent.json")
        with open(sent, "w", encoding="utf-8") as handle:
            json.dump(sentPathways, handle)
        script = os.path.join(directory, "check.js")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(NODE_SCRIPT % {"models": json.dumps(PATHWAY_MODELS), "helper": json.dumps(helper),
                                        "sent": json.dumps(sent)})
        done = subprocess.run(["node", script], capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError("node failed:\n%s" % done.stderr)
        return json.loads(done.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheTableCellsInTheBrowserTest(_MultiConditionStep2Run):
    """The same rules on the cells the browser builds."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # As the Step 2 response carries them (pathwaysInfo: pathway.toBSON()),
        # through JSON.
        cls.sent = json.loads(json.dumps({pathwayID: p.toBSON() for pathwayID, p in cls.listed.items()}))
        cls.cells = _cellsInTheBrowser(cls.sent)

    def rawCell(self, pathwayID, method):
        return self.cells[pathwayID]["combinedSignificancePvalue" + method]

    def adjustedCell(self, pathwayID, method, adjust):
        return self.cells[pathwayID]["adjustedCombinedSignificancePvalue" + method + adjust]

    def test_the_raw_cell_is_the_combination_of_the_global_pvalues(self):
        for pathwayID, pathway in self.listed.items():
            for method in self.combinedMethods:
                self.assertEqual(pathway.getTotalGlobalPvalues()[method], self.rawCell(pathwayID, method))

    def test_the_adjusted_cell_is_the_correction_of_the_raw_cell(self):
        for method in self.combinedMethods:
            family = adjustPvalues({pathwayID: self.rawCell(pathwayID, method) for pathwayID in self.listed})
            for pathwayID in sorted(self.listed):
                for adjust in METHODS:
                    self.assertEqual(family[adjust][pathwayID], self.adjustedCell(pathwayID, method, adjust),
                                     "%s %s %s" % (pathwayID, method, adjust))

    def test_the_route_gives_back_the_cells_when_nothing_is_hidden(self):
        """The body getPvaluesFromStore builds from these cells, corrected by
        /pa_adjust_pvalues: the cells the table already shows, bit for bit."""
        rows = {}
        for pathwayID, pathway in self.listed.items():
            row = {omic: (_omicPvalue(pathway, omic) if omic in pathway.getSignificanceValues() else "-")
                   for omic in OMICS}
            row.update({method: self.rawCell(pathwayID, method) for method in self.combinedMethods})
            rows.setdefault(pathway.getSource(), {})[pathwayID] = row
        answer = _adjust({"pValues": rows})
        for pathwayID, pathway in sorted(self.listed.items()):
            for method in self.combinedMethods:
                for adjust in METHODS:
                    self.assertEqual(self.adjustedCell(pathwayID, method, adjust),
                                     answer[pathway.getSource()][method][adjust][pathwayID],
                                     "%s %s %s: an unfiltered table's column moves when the filter "
                                     "path corrects it" % (pathwayID, method, adjust))

    def test_each_condition_keeps_its_own_cell(self):
        for pathwayID, pathway in self.listed.items():
            for method in self.combinedMethods:
                for adjust in METHODS:
                    for condition in range(CONDITIONS):
                        self.assertEqual(pathway.adjustedCombinedSignificanceValues[method][adjust][condition],
                                         self.cells[pathwayID]["adjustedCombinedSignificancePvalue%s%s_c%d"
                                                               % (method, adjust, condition)])

    def test_a_job_stored_before_keeps_its_stored_value(self):
        """Without adjustedTotalGlobalPvalues, as a job stored before this rule
        comes back from the database: the first condition's correction, as it
        was stored, until a filter recomputes it."""
        old = copy.deepcopy(self.sent)
        for entry in old.values():
            entry.pop("adjustedTotalGlobalPvalues", None)
        cells = _cellsInTheBrowser(old)
        for pathwayID, pathway in self.listed.items():
            for method in self.combinedMethods:
                for adjust in METHODS:
                    self.assertEqual(pathway.adjustedCombinedSignificanceValues[method][adjust][0],
                                     cells[pathwayID]["adjustedCombinedSignificancePvalue" + method + adjust])


# The shipped Application.restoreCachedModel() from app.js, on a cached copy of
# the Step 2 job above. The copy is what JobController writes: the job's
# Pathway models through JSON.stringify, stamped with a cache schema. A client
# before adjustedTotalGlobalPvalues had no such field on its Pathway model, so
# its copy lacks the key and carries its schema, 3.
LAST_SCHEMA_WITHOUT_THE_FIELD = 3

CACHE_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const ctx = {console: {log: console.error, info: console.error, warn: console.error, error: console.error}};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext("function Observable(){}; function Model(){}; Model.prototype = new Observable();"
    + "Date.logFormat = function(){ return ''; };", ctx);
vm.runInContext(fs.readFileSync(%(models)s, 'utf8'), ctx, {filename: %(models)s});
vm.runInContext(%(schema)s, ctx);
ctx.sent = JSON.parse(fs.readFileSync(%(sent)s, 'utf8'));
ctx.stamp = %(stamp)s;
ctx.oldClient = %(oldClient)s;
ctx.cells = %(cells)s;
vm.runInContext(
    "var discarded = [];" +
    "function discardCachedJobModel(jobID) { discarded.push(jobID === undefined ? null : jobID); }" +
    "var app = {};" +
    "(function(){ " + %(restore)s + " }).call(app);" +
    "var cached = {};" +
    "for (var id in sent) {" +
    "  cached[id] = JSON.parse(JSON.stringify(new Pathway(id).loadFromJSON(sent[id])));" +
    "  if (oldClient) { delete cached[id].adjustedTotalGlobalPvalues; }" +
    "}" +
    "var payload = {jobID: 'COMBINEDFDRTEST', pathways: cached," +
    "               cacheSchema: stamp === null ? PA_JOB_CACHE_SCHEMA : stamp};" +
    // Without cells the stub only notes whether the copy holds the field, so
    // that it cannot throw: a throw would be discarded as unreadable and pass
    // for a refused stamp.
    "var shown = null;" +
    "var model = {loadFromJSON: function (json) {" +
    "  shown = {};" +
    "  for (var id in json.pathways) {" +
    "    shown[id] = cells ? paStep3CombinedCells(new Pathway(id).loadFromJSON(json.pathways[id]))" +
    "                      : ('adjustedTotalGlobalPvalues' in json.pathways[id]);" +
    "  }" +
    "}};" +
    "var restored = app.restoreCachedModel(model, payload);" +
    "result = {restored: restored, discarded: discarded, shown: shown};", ctx);
process.stdout.write(JSON.stringify(ctx.result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class ACachedCopyWithoutTheFieldIsNotRestoredTest(_MultiConditionStep2Run):
    """The boot restores a job the browser cached instead of asking the server
    again, when the copy's stamp matches app.js's PA_JOB_CACHE_SCHEMA. A copy
    written by a client before this rule has no adjustedTotalGlobalPvalues, so
    restoring it would show the first condition's correction in the adjusted
    combined column of a job computed after the rule."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sent = json.loads(json.dumps({pathwayID: p.toBSON() for pathwayID, p in cls.listed.items()}))
        with open(APP_JS, "r", encoding="utf-8") as handle:
            app = handle.read()
        cls.restore = extract_app_block(app, "this.restoreCachedModel = function",
                                        "the boot's cached job restore")
        declaration = [line for line in app.splitlines() if line.startswith("var PA_JOB_CACHE_SCHEMA = ")]
        if len(declaration) != 1:
            raise AssertionError("PA_JOB_CACHE_SCHEMA is not declared once in app.js")
        cls.schema = declaration[0]

    def restoreCopy(self, stamp, oldClient, cells):
        helper = ""
        if cells:
            with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
                helper = extract_block(handle.read(), HELPER)
            if helper is None:
                raise AssertionError("paStep3CombinedCells is missing from PA_Step3Views.js")
        directory = tempfile.mkdtemp(prefix="paintomics-combined-fdr-cache-")
        try:
            sent = os.path.join(directory, "sent.json")
            with open(sent, "w", encoding="utf-8") as handle:
                json.dump(self.sent, handle)
            script = os.path.join(directory, "check.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(CACHE_SCRIPT % {
                    "models": json.dumps(PATHWAY_MODELS), "schema": json.dumps(self.schema + helper),
                    "sent": json.dumps(sent), "stamp": json.dumps(stamp), "oldClient": json.dumps(oldClient),
                    "cells": json.dumps(cells), "restore": json.dumps(self.restore)})
            done = subprocess.run(["node", script], capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise AssertionError("node failed:\n%s" % done.stderr)
            return json.loads(done.stdout)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_a_copy_from_the_client_before_is_fetched_again(self):
        result = self.restoreCopy(LAST_SCHEMA_WITHOUT_THE_FIELD, oldClient=True, cells=False)
        self.assertFalse(result["restored"],
                         "a cached copy stamped %d, written by a client that dropped "
                         "adjustedTotalGlobalPvalues, was restored: the adjusted combined column "
                         "shows the first condition's correction again. Bump PA_JOB_CACHE_SCHEMA "
                         "in app.js." % LAST_SCHEMA_WITHOUT_THE_FIELD)
        self.assertEqual(["COMBINEDFDRTEST"], result["discarded"])
        self.assertIsNone(result["shown"])

    def test_this_clients_copy_keeps_the_correction_of_the_raw_column(self):
        """The copy this client writes, stamped with today's schema, is
        restored with the field, and the table cells read from it are the
        ones the server's answer gives."""
        result = self.restoreCopy(None, oldClient=False, cells=True)
        self.assertTrue(result["restored"])
        self.assertEqual([], result["discarded"])
        fresh = _cellsInTheBrowser(self.sent)
        self.assertEqual(sorted(self.listed), sorted(result["shown"]))
        for pathwayID in sorted(self.listed):
            for method in self.combinedMethods:
                for adjust in METHODS:
                    key = "adjustedCombinedSignificancePvalue" + method + adjust
                    self.assertEqual(fresh[pathwayID][key], result["shown"][pathwayID][key],
                                     "%s %s %s" % (pathwayID, method, adjust))
                    self.assertEqual(self.listed[pathwayID].getAdjustedTotalGlobalPvalues()[method][adjust],
                                     result["shown"][pathwayID][key])


if __name__ == "__main__":
    unittest.main(verbosity=2)
