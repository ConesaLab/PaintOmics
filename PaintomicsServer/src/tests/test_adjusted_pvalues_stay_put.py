#!/usr/bin/env python3
"""Step 2 corrects the p-values over the pathways the Step 3 table lists.

Why this exists
---------------
Step 3's FDR columns (FDR BH, FDR BY) are corrected over a family of tests, and
two places compute that correction:

  * Step 2 (PathwayAcquisitionJob.generatePathwaysList), once, when the job
    runs: these are the values a job is stored with and opens with.
  * /pa_adjust_pvalues, whenever a category filter hides rows: the browser
    sends the rows the table still shows (getPvaluesFromStore in
    PA_Step3Views.js) and the server corrects over those.

The table never lists KEGG's global map, Metabolic pathways (<organism>01100;
paStep3IsGlobalMap), so the second family never held it. The first one did.
A job's FDR columns therefore moved as soon as anything was filtered, and
hiding only the map, which no row shows, was enough to move them: the browser
counted it as a filter and asked for a correction over a family one test
smaller.

The two families also split differently by database: Step 2 corrects every
database's pathways together, and /pa_adjust_pvalues corrected each database
on its own. On a job with KEGG and Reactome, the default when both are
installed, the first filter moved every Reactome FDR value even when it hid
only one KEGG pathway, and by far more than the map ever did.

The rules pinned here:

  * Step 2 leaves the map out of the family, by the same rule as the table
    (isGlobalMap mirrors paStep3IsGlobalMap);
  * /pa_adjust_pvalues corrects every database it is sent as one family, as
    Step 2 does (_adjustAcrossDatabases);
  * so the stored values are what /pa_adjust_pvalues gives for the unfiltered
    table, bit for bit, on one database or two: filtering nothing changes
    nothing; and the answer for a filtered table is what Step 2 gives for the
    pathways left;
  * hiding only the map is not a filter (FilteredIsAskedOfEveryPathwayTest in
    test_step3_counts_what_the_table_lists runs the browser's side);
  * the map still carries adjusted values, as a family of its own, so every
    pathway has the same keys;
  * raw p-values do not move.

These jobs use one condition. The same rules on a job with one relevant
column per condition, where the adjusted combined column is the correction of the raw combined
column (the combination of each omic's Global p-value) and not of any one
condition's, are pinned in test_combined_fdr_corrects_its_column.

Step 2 runs for real here, workers included, on a small organism held in the
KeggInformationManager's cache, so no MongoDB is needed (the same set-up as
test_a_lost_enrichment_worker_fails_the_job).

Usage:
    cd PaintomicsServer
    python -m src.tests.test_adjusted_pvalues_stay_put
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
from src.classes.Pathway import Pathway
from src.common.KeggInformationManager import KeggInformationManager
from src.common.Statistics import adjustPvalues

multiprocessing.set_start_method("fork", force=True)

ORGANISM = "adjt"
GLOBAL_MAP = ORGANISM + "01100"
OMIC = "Gene expression"
METHODS = ("FDR BH", "FDR BY")
N_GENES = 60

STEP3_VIEWS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html", "app", "view", "PathwayAcquisitionViews",
    "PA_Step3Views.js"))

# The map holds every relevant gene and few others, so it is the most
# significant pathway of the job, as it is on many real jobs; the others range
# from all relevant to none. Gene i is relevant when i < 20.
PATHWAY_GENES = {
    GLOBAL_MAP: range(0, 30),
    ORGANISM + "00010": range(0, 6),
    ORGANISM + "00020": range(3, 11),
    ORGANISM + "00030": range(12, 22),
    ORGANISM + "00040": range(15, 27),
    ORGANISM + "00051": range(18, 24),
    ORGANISM + "00052": range(25, 33),
    ORGANISM + "04110": range(30, 40),
    ORGANISM + "04210": range(40, 52),
    ORGANISM + "04310": range(45, 60),
    # Another organism's map is an ordinary pathway (isGlobalMap): it is listed.
    "hsa01100": range(14, 26),
}

# A second database for the same genes, so that a correction made database by
# database gives other values than one made over both.
REACTOME_GENES = {
    "R-ADJT-0001": range(0, 8),
    "R-ADJT-0002": range(2, 14),
    "R-ADJT-0003": range(10, 18),
    "R-ADJT-0004": range(16, 30),
    "R-ADJT-0005": range(21, 35),
    "R-ADJT-0006": range(35, 50),
}


def _source(pathwayID):
    return "Reactome" if pathwayID.startswith("R-") else "KEGG"


def _organism(pathwayGenes):
    return {"name": ORGANISM, "pathways": {
        pathwayID: {"name": "Pathway " + pathwayID, "source": _source(pathwayID),
                    "classification": "Metabolism;Carbohydrate metabolism",
                    "genes": [{"id": "gene%02d" % i} for i in genes], "compounds": []}
        for pathwayID, genes in pathwayGenes.items()}}


def _job(databases):
    job = paj.PathwayAcquisitionJob("ADJUSTEDPVALUESTEST", None, "/tmp/")
    job.setOrganism(ORGANISM)
    job.setDatabases(list(databases))
    job.addGeneBasedInputOmic({"omicName": OMIC, "omicSummary": [{"Total": N_GENES}, 0],
                               "enrichment": "genes"})
    for i in range(N_GENES):
        gene = Gene("gene%02d" % i)
        gene.setName("gene%02d" % i)
        value = OmicValue("input%02d" % i)
        value.setOmicName(OMIC)
        value.setOriginalName("input%02d" % i)
        value.setValues([float(i)])
        value.setRelevant([i < 20])
        gene.addOmicValue(value)
        job.addInputGeneData(gene)
    return job


def _omicPvalue(pathway):
    """What the table's omic column shows, and what both families take."""
    globalP = pathway.getGlobalOmicPvalues().get(OMIC)
    return globalP if globalP is not None else pathway.getSignificanceValues()[OMIC][0][2]


def _tableRows(pathways):
    """The body getPvaluesFromStore sends for an unfiltered table: every row
    the table lists (no global map), under its database, with the omic's
    p-value and each combined method's p-value as the row shows them."""
    rows = {}
    for pathwayID, pathway in pathways.items():
        if pathwayID == GLOBAL_MAP:          # the one row paStep3IsGlobalMap drops
            continue
        row = {OMIC: _omicPvalue(pathway)}
        for method, values in pathway.getCombinedSignificancePvalues().items():
            total = pathway.getTotalGlobalPvalues().get(method)
            row[method] = total if total is not None else (values[0] if isinstance(values, list) else values)
        rows.setdefault(pathway.getSource(), {})[pathwayID] = row
    return rows


class _Request:
    def __init__(self, body):
        self.body = body

    def get_json(self):
        return self.body


class _Step2Run(unittest.TestCase):
    """Step 2 run once for the class, on the small organism above."""

    DATABASES = ("KEGG",)
    PATHWAY_GENES = PATHWAY_GENES

    @classmethod
    def setUpClass(cls):
        organism = _organism(cls.PATHWAY_GENES)
        manager = KeggInformationManager()
        manager.lastOrganisms.append(organism)
        try:
            with mock.patch.object(paj, "MAX_THREADS", 2), mock.patch.object(paj, "MAX_WAIT_THREADS", 60):
                job = _job(cls.DATABASES)
                job.generatePathwaysList()
        finally:
            manager.lastOrganisms.remove(organism)
        cls.pathways = job.getMatchedPathways()
        cls.listed = {pathwayID: pathway for pathwayID, pathway in cls.pathways.items()
                      if pathwayID != GLOBAL_MAP}


class _TwoDatabaseStep2Run(_Step2Run):
    """The same, on a job with KEGG and Reactome."""

    DATABASES = ("KEGG", "Reactome")
    PATHWAY_GENES = dict(PATHWAY_GENES, **REACTOME_GENES)


class TheJobCorrectsOverTheListedPathwaysTest(_Step2Run):

    def test_the_fixture_has_the_map_and_it_would_move_the_values(self):
        """Guards the test itself: the map matched, it is the most significant
        pathway, and correcting with it in the family gives other values."""
        self.assertEqual(sorted(self.pathways), sorted(PATHWAY_GENES))
        smallest = min(self.pathways, key=lambda pathwayID: _omicPvalue(self.pathways[pathwayID]))
        self.assertEqual(GLOBAL_MAP, smallest)
        withMap = adjustPvalues({pathwayID: _omicPvalue(p) for pathwayID, p in self.pathways.items()})
        withoutMap = adjustPvalues({pathwayID: _omicPvalue(p) for pathwayID, p in self.listed.items()})
        for method in METHODS:
            self.assertTrue(any(withMap[method][pathwayID] != withoutMap[method][pathwayID]
                                for pathwayID in self.listed), method)

    def test_each_listed_pathway_is_corrected_without_the_map(self):
        omicFamily = adjustPvalues({pathwayID: _omicPvalue(p) for pathwayID, p in self.listed.items()})
        for pathwayID, pathway in self.listed.items():
            for method in METHODS:
                self.assertEqual(omicFamily[method][pathwayID],
                                 pathway.adjustedSignificanceValues[OMIC][method],
                                 "%s %s: Step 2 corrected over a family that is not the "
                                 "table's rows" % (pathwayID, method))
        for combined in next(iter(self.listed.values())).getCombinedSignificancePvalues():
            family = adjustPvalues({pathwayID: p.getCombinedSignificancePvalues()[combined][0]
                                    for pathwayID, p in self.listed.items()})
            for pathwayID, pathway in self.listed.items():
                for method in METHODS:
                    self.assertEqual(family[method][pathwayID],
                                     pathway.adjustedCombinedSignificanceValues[combined][method],
                                     "%s %s %s" % (pathwayID, combined, method))

    def test_another_organisms_map_stays_in_the_family(self):
        self.assertFalse(paj.isGlobalMap("hsa01100", ORGANISM))
        self.assertIn("hsa01100", self.listed)

    def test_the_map_keeps_well_formed_values_of_its_own(self):
        """A family of one: BH and BY are the raw p-value. The browser reads
        the list of FDR methods off the first pathway it holds."""
        theMap = self.pathways[GLOBAL_MAP]
        raw = _omicPvalue(theMap)
        self.assertEqual({method: raw for method in METHODS}, theMap.adjustedSignificanceValues[OMIC])
        for combined, values in theMap.getCombinedSignificancePvalues().items():
            self.assertEqual({method: values[0] for method in METHODS},
                             theMap.adjustedCombinedSignificanceValues[combined])


class _FilteringChecks:
    """/pa_adjust_pvalues on the rows of the unfiltered table gives back the
    values the job was stored with, so a filter that hides nothing the table
    lists cannot move them; on a filtered table it gives what Step 2 gives
    for the pathways left. Run on one database and on two."""

    def adjust(self, body):
        from src.paintomicsserver import Response
        from src.servlets.PathwayAcquisitionServlet import pathwayAcquisitionAdjustPvalues
        # Through JSON, as the browser sends it.
        response = pathwayAcquisitionAdjustPvalues(_Request(json.loads(json.dumps(body))), Response())
        self.assertIs(True, response.content.get("success"), response.content)
        return response.content

    def assertAnswerIs(self, expected, answer, what):
        """``expected``: {pathwayID: Pathway} carrying the adjusted values the
        answer must hold; ``answer``: the route's adjustedPvalues."""
        for pathwayID, pathway in expected.items():
            byDatabase = answer[pathway.getSource()]
            for method in METHODS:
                self.assertEqual(pathway.adjustedSignificanceValues[OMIC][method],
                                 byDatabase[OMIC][method][pathwayID],
                                 "%s (%s) %s %s" % (pathwayID, pathway.getSource(), method, what))
                for combined in pathway.getCombinedSignificancePvalues():
                    self.assertEqual(pathway.adjustedCombinedSignificanceValues[combined][method],
                                     byDatabase[combined][method][pathwayID],
                                     "%s (%s) %s %s %s" % (pathwayID, pathway.getSource(), combined,
                                                            method, what))

    def test_the_fixture_spans_the_job_databases(self):
        self.assertEqual(set(self.DATABASES), {p.getSource() for p in self.listed.values()})

    def test_the_unfiltered_table_corrects_to_the_stored_values(self):
        answer = self.adjust({"pValues": _tableRows(self.pathways)})["adjustedPvalues"]
        self.assertEqual(set(self.DATABASES), set(answer))
        self.assertAnswerIs(self.listed, answer, "moves when the unfiltered table is corrected again")

    def test_the_default_stouffer_weights_correct_to_the_stored_values(self):
        """The Stouffer panel's path: every row sent, the visible ones corrected."""
        rows = _tableRows(self.pathways)
        answer = self.adjust({"pValues": rows, "stoufferWeights": {OMIC: 1.0},
                              "visiblePathways": sorted(self.listed)})
        for pathwayID, pathway in self.listed.items():
            for method in METHODS:
                self.assertEqual(pathway.adjustedCombinedSignificanceValues["Stouffer"][method],
                                 answer["adjustedStoufferPvalues"][pathway.getSource()][method][pathwayID],
                                 "%s (%s) %s" % (pathwayID, pathway.getSource(), method))

    def test_a_filter_corrects_as_step2_would_over_the_pathways_left(self):
        """Hide one pathway of the first database, the least significant: the
        answer for every row left, in every database, is Step 2's correction
        (adjustPathwayPvalues) of the job without that pathway."""
        first = [p for p in self.listed.values() if p.getSource() == self.DATABASES[0]]
        hidden = max(first, key=_omicPvalue).getID()
        left = {pathwayID: copy.deepcopy(pathway) for pathwayID, pathway in self.pathways.items()
                if pathwayID != hidden}
        paj.adjustPathwayPvalues(left, ORGANISM)
        rows = _tableRows(self.pathways)
        del rows[self.DATABASES[0]][hidden]
        answer = self.adjust({"pValues": rows})["adjustedPvalues"]
        self.assertAnswerIs({pathwayID: pathway for pathwayID, pathway in left.items()
                             if pathwayID != GLOBAL_MAP}, answer,
                            "is not what Step 2 gives without %s" % hidden)


class FilteringNothingChangesNothingTest(_FilteringChecks, _Step2Run):
    pass


class FilteringNothingChangesNothingAcrossDatabasesTest(_FilteringChecks, _TwoDatabaseStep2Run):

    def test_one_family_per_database_would_give_other_values(self):
        """Guards the test itself: corrected database by database, as the
        route did, the stored values would not come back."""
        perDatabase = {}
        for database in self.DATABASES:
            perDatabase.update(adjustPvalues({pathwayID: _omicPvalue(p) for pathwayID, p in self.listed.items()
                                              if p.getSource() == database})["FDR BH"])
        self.assertTrue(any(perDatabase[pathwayID] != p.adjustedSignificanceValues[OMIC]["FDR BH"]
                            for pathwayID, p in self.listed.items()))


def _pathway(pathwayID, pvalues, source="KEGG"):
    """A matched pathway as Step 2 leaves it before the correction, one entry
    in ``pvalues`` per condition."""
    pathway = Pathway(pathwayID)
    pathway.setSource(source)
    pathway.significanceValues = {OMIC: [[10, 3, p] for p in pvalues]}
    if len(pvalues) > 1:
        pathway.setGlobalOmicPvalue(OMIC, min(pvalues))
    pathway.setCombinedSignificancePvalues({"Fisher": list(pvalues), "Stouffer": list(pvalues)})
    return pathway


class AdjustPathwayPvaluesTest(unittest.TestCase):
    """The correction on its own, on pathways built by hand."""

    def pathways(self, conditions):
        values = {GLOBAL_MAP: [1e-9, 2e-9], ORGANISM + "00010": [0.001, 0.2],
                  ORGANISM + "00020": [0.004, 0.03], ORGANISM + "00030": [0.02, 0.5],
                  ORGANISM + "04110": [0.3, 0.004], "R-ADJ-0001": [0.01, 0.6]}
        return {pathwayID: _pathway(pathwayID, ps[:conditions],
                                    "Reactome" if pathwayID.startswith("R-") else "KEGG")
                for pathwayID, ps in values.items()}

    def test_raw_pvalues_do_not_move(self):
        for conditions in (1, 2):
            pathways = self.pathways(conditions)
            before = {pathwayID: copy.deepcopy((p.significanceValues, p.globalOmicPvalues,
                                                p.combinedSignificancePvalues, p.totalGlobalPvalues))
                      for pathwayID, p in pathways.items()}
            paj.adjustPathwayPvalues(pathways, ORGANISM)
            for pathwayID, pathway in pathways.items():
                self.assertEqual(before[pathwayID],
                                 (pathway.significanceValues, pathway.globalOmicPvalues,
                                  pathway.combinedSignificancePvalues, pathway.totalGlobalPvalues))

    def test_every_condition_is_corrected_without_the_map(self):
        pathways = self.pathways(2)
        paj.adjustPathwayPvalues(pathways, ORGANISM)
        listed = [pathwayID for pathwayID in pathways if pathwayID != GLOBAL_MAP]
        for condition in (0, 1):
            family = adjustPvalues({pathwayID: pathways[pathwayID].combinedSignificancePvalues["Fisher"][condition]
                                    for pathwayID in listed})
            for pathwayID in listed:
                for method in METHODS:
                    self.assertEqual(family[method][pathwayID],
                                     pathways[pathwayID].adjustedCombinedSignificanceValues["Fisher"][method][condition])
        # The omic family takes the global p-value where there is one.
        family = adjustPvalues({pathwayID: pathways[pathwayID].globalOmicPvalues[OMIC] for pathwayID in listed})
        for pathwayID in listed:
            self.assertEqual({method: family[method][pathwayID] for method in METHODS},
                             pathways[pathwayID].adjustedSignificanceValues[OMIC])

    def test_every_database_is_still_one_family(self):
        """Unchanged by this rule: Step 2 corrects every database's pathways
        together (4_1_pathway_enrichment.md says so)."""
        pathways = self.pathways(1)
        paj.adjustPathwayPvalues(pathways, ORGANISM)
        listed = [pathwayID for pathwayID in pathways if pathwayID != GLOBAL_MAP]
        family = adjustPvalues({pathwayID: pathways[pathwayID].significanceValues[OMIC][0][2]
                                for pathwayID in listed})
        self.assertEqual(family["FDR BH"]["R-ADJ-0001"],
                         pathways["R-ADJ-0001"].adjustedSignificanceValues[OMIC]["FDR BH"])

    def test_a_job_without_the_map_or_with_only_the_map(self):
        pathways = self.pathways(1)
        theMap = {GLOBAL_MAP: pathways.pop(GLOBAL_MAP)}
        family = adjustPvalues({pathwayID: p.significanceValues[OMIC][0][2] for pathwayID, p in pathways.items()})
        paj.adjustPathwayPvalues(pathways, ORGANISM)
        for pathwayID, pathway in pathways.items():
            self.assertEqual({method: family[method][pathwayID] for method in METHODS},
                             pathway.adjustedSignificanceValues[OMIC])
        paj.adjustPathwayPvalues(theMap, ORGANISM)
        self.assertEqual({method: 1e-9 for method in METHODS},
                         theMap[GLOBAL_MAP].adjustedSignificanceValues[OMIC])

    def test_no_organism_leaves_nothing_out(self):
        """Without an organism code there is no map to recognise (as on the client)."""
        pathways = self.pathways(1)
        family = adjustPvalues({pathwayID: p.significanceValues[OMIC][0][2] for pathwayID, p in pathways.items()})
        paj.adjustPathwayPvalues(pathways, "")
        self.assertEqual(family["FDR BH"][ORGANISM + "00010"],
                         pathways[ORGANISM + "00010"].adjustedSignificanceValues[OMIC]["FDR BH"])


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class OneRuleOnBothSidesTest(unittest.TestCase):
    """isGlobalMap (server) and paStep3IsGlobalMap (browser) name the same map."""

    CASES = [["mmu01100", "mmu"], ["dosa01100", "dosa"], ["thaz01100", "thaz"],
             ["osa01100", "dosa"], ["mmu01100", ""], ["mmu01100", None],
             ["mmu00010", "mmu"], ["R-MMU-01100", "mmu"], ["hsa01100", "mmu"],
             ["map01100", "mmu"], ["mmu011000", "mmu"], ["path:mmu01100", "mmu"]]

    def test_the_two_predicates_agree(self):
        from src.tests.test_step3_counts_what_the_table_lists import HELPER, extract_block
        with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
            helper = extract_block(handle.read(), HELPER)
        self.assertIsNotNone(helper, "paStep3IsGlobalMap is missing from PA_Step3Views.js")
        script = (helper + ";\nconsole.log(JSON.stringify(%s.map(function (c) { "
                  "return paStep3IsGlobalMap(c[0], c[1]); })));\n" % json.dumps(self.CASES))
        directory = tempfile.mkdtemp(prefix="paintomics-adjusted-pvalues-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            browser = json.loads(done.stdout)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        server = [paj.isGlobalMap(pathwayID, organism) for pathwayID, organism in self.CASES]
        self.assertEqual(browser, server)
        self.assertEqual([True, True, True] + [False] * 9, server)


if __name__ == "__main__":
    unittest.main(verbosity=2)
