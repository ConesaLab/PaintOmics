#!/usr/bin/env python3
"""The pathway network must count metabolites, not only genes.

Every node in `pathways_network*.json` carries `total_features`, and the
installer sets it from the pathway's GENE list alone -- `len(gene_ids)` for
Reactome, `pathway2gene.list` for KEGG, the 20-character gene ids for MapMan.
The client then filtered with

    total_features * minFeatures > matchedGenes + matchedCompounds

which divides a count of matched *compounds* by a count of *genes*. It is not a
ratio of anything, and on a compound-only job the numerator has no genes at all
while the denominator is the pathway's whole gene set, so every pathway fails.

Measured on job 2J4u1qN5pm (STATegra metabolomics, mmu, KEGG+Reactome, no
gene-based omic): 141 pathways reached the test, 137 failed it, 4 failed only on
p-value, 0 were drawn. `mmu01210` had 16 matched compounds at p = 0.0063 and was
dropped because 34 genes x 0.5 = 17 > 16. The header read
"0 of 141 KEGG pathways . 0 edges".

The same file's shared-feature edges ("Shared biological features" in the view)
were built from shared GENES only, so two pathways sharing metabolites and no
gene were never connected.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_pathway_network_counts_compounds
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

from src.classes.Pathway import Pathway

CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))),
    "PaintomicsClient", "public_html")
STEP3_VIEWS = os.path.join(CLIENT, "app", "view", "PathwayAcquisitionViews",
                           "PA_Step3Views.js")
PATHWAY_MODELS = os.path.join(CLIENT, "app", "model", "PathwayModels.js")
BUILD_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "AdminTools", "scripts", "common_build_database.py")


# ---------------------------------------------------------------------------
# The server hands the client the counts, so no reinstall is needed
# ---------------------------------------------------------------------------

class PathwayCarriesItsOwnTotalsTest(unittest.TestCase):
    """`total_features` in the installed file is gene-only and can only be
    corrected by reinstalling every species. The counts the filter needs are
    already in hand while the job runs -- getAllFeatureIDsByPathwayID returns
    both sets -- so the Pathway carries them and every existing install is
    fixed the moment a job is run."""

    def test_a_new_pathway_has_no_counts_and_no_keys_for_them(self):
        """Absent, not zero and not None. Zero is a real answer the filter
        would act on, and a None reaches the client as the STRING "None"
        (DAO.adaptBSON turns every None leaf into one)."""
        pathway = Pathway("mmu00010")
        self.assertIsNone(pathway.getTotalGenes())
        self.assertIsNone(pathway.getTotalCompounds())
        self.assertNotIn("totalGenes", pathway.toBSON())
        self.assertNotIn("totalCompounds", pathway.toBSON())

    def test_a_pathway_stored_before_the_counts_existed_stays_unknown(self):
        """The regression this guards: if the counts defaulted to 0, a job
        stored before them would hand the client a denominator of 0, and
        `0 * minFeatures > matched` is never true -- so reopening an old
        GENE-based job would silently switch its coverage filter off."""
        revived = Pathway("")
        revived.parseBSON({"_id": "x", "ID": "mmu00010", "matchedGenes": ["1"]})

        self.assertIsNone(revived.getTotalGenes())
        self.assertIsNone(revived.getTotalCompounds())

    def test_the_counts_survive_the_trip_to_mongo(self):
        pathway = Pathway("mmu00010")
        pathway.setTotalGenes(67)
        pathway.setTotalCompounds(31)

        bson = pathway.toBSON()
        self.assertEqual(67, bson["totalGenes"])
        self.assertEqual(31, bson["totalCompounds"])

        revived = Pathway("")
        revived.parseBSON(dict(bson, _id="x"))
        self.assertEqual(67, revived.getTotalGenes())
        self.assertEqual(31, revived.getTotalCompounds())


class SignificanceRecordsTheTotalsTest(unittest.TestCase):
    """testPathwaySignificance already receives both feature lists; it is the
    one place that sees a pathway's full size and builds the Pathway."""

    def _run(self, genes, compounds):
        from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob

        job = PathwayAcquisitionJob(jobID="counts", userID=None,
                                    CLIENT_TMP_DIR="/tmp/paintomics-test/")
        job.setOrganism("mmu")
        job.setDatabases(["KEGG"])

        class _Value(object):
            def __init__(self):
                self.relevant = [True]

            def getOmicName(self):
                return "Metabolomics"

            def getInputName(self):
                return "C00033"

            def getOriginalName(self):
                return "C00033"

            def isRelevantAssociation(self):
                return True

        class _Feature(object):
            def __init__(self, identifier):
                self.identifier = identifier

            def getID(self):
                return self.identifier

            def getMatchingDB(self):
                return "KEGG"

            def getOmicsValues(self):
                return [_Value()]

        inputCompounds = {"c00033": _Feature("C00033")}
        return job.testPathwaySignificance(
            genes, compounds, {}, inputCompounds,
            {"Metabolomics": 100}, {"Metabolomics": [10]},
            {"Metabolomics": 1.0}, {"Metabolomics": "genes"},
            "KEGG", False)

    def test_both_totals_are_recorded_on_a_matched_pathway(self):
        isValid, pathway = self._run(
            {"11674", "11676", "230163"},
            {"C00033", "C00031", "C00103"})

        self.assertTrue(isValid, "the input compound C00033 is in the pathway")
        self.assertEqual(3, pathway.getTotalGenes())
        self.assertEqual(3, pathway.getTotalCompounds(),
                         "a compound-only job needs the compound denominator")

    def test_a_pathway_with_no_compounds_records_zero(self):
        isValid, pathway = self._run({"11674"}, set())
        self.assertFalse(isValid)


# ---------------------------------------------------------------------------
# The installer: shipped nodes and edges must know about compounds
# ---------------------------------------------------------------------------

def _loadBuilder():
    """`common_build_database` at import time only defines functions and
    constants, but it does `from src.conf.serverconf import ...`; import it the
    way the AdminTools scripts do."""
    import importlib
    return importlib.import_module(
        "src.AdminTools.scripts.common_build_database")


class SharedFeatureMatrixTest(unittest.TestCase):
    """The three builders each filled a pathway-pair matrix from a gene ->
    pathways mapping and emitted one 's' edge per non-zero cell. Compounds
    were never counted, in any of the three."""

    def test_two_pathways_sharing_only_a_compound_are_linked(self):
        builder = _loadBuilder()
        matrix = {"a": {"b": 0, "c": 0}, "b": {"c": 0}, "c": {}}

        builder.accumulateSharedFeatures(matrix, {"C00033": {"a", "b"}})

        self.assertEqual(1, matrix["a"]["b"],
                         "a shared metabolite is a shared biological feature")
        self.assertEqual(0, matrix["a"]["c"])

    def test_the_count_accumulates_over_features(self):
        builder = _loadBuilder()
        matrix = {"a": {"b": 3}, "b": {}}

        builder.accumulateSharedFeatures(
            matrix, {"C00033": {"a", "b"}, "C00031": {"a", "b"}})

        self.assertEqual(5, matrix["a"]["b"],
                         "compound sharing adds to the gene weight, not over it")

    def test_a_pair_missing_from_the_matrix_is_not_invented(self):
        """The matrix is diagonal: only one of (a,b)/(b,a) exists. Writing the
        missing direction would double every weight."""
        builder = _loadBuilder()
        matrix = {"a": {"b": 0}, "b": {}}

        builder.accumulateSharedFeatures(matrix, {"C1": {"b", "a"}})

        self.assertEqual(1, matrix["a"]["b"])
        self.assertEqual({}, matrix["b"])

    def test_an_unknown_pathway_is_ignored_not_raised(self):
        builder = _loadBuilder()
        matrix = {"a": {"b": 0}, "b": {}}

        builder.accumulateSharedFeatures(matrix, {"C1": {"a", "zzz"}})

        self.assertEqual(0, matrix["a"]["b"])


class CompoundIndexTest(unittest.TestCase):
    def test_the_index_maps_each_compound_to_its_pathways(self):
        builder = _loadBuilder()
        allPathways = {
            "mmu00010": {"compounds": [{"id": "C00031"}, {"id": "C00033"},
                                       {"id": "C00031"}]},
            "mmu00020": {"compounds": [{"id": "C00033"}]},
            "mmu00030": {"compounds": []},
        }

        index = builder.indexCompoundsByPathway(allPathways)

        self.assertEqual({"mmu00010", "mmu00020"}, index["C00033"])
        self.assertEqual({"mmu00010"}, index["C00031"])
        self.assertNotIn("", index, "a blank id joins every pathway to every other")

    def test_blank_identifiers_are_dropped(self):
        builder = _loadBuilder()
        index = builder.indexCompoundsByPathway(
            {"p": {"compounds": [{"id": ""}, {"id": None}, {}]}})
        self.assertEqual({}, dict(index))


class InstalledNodesDeclareCompoundsTest(unittest.TestCase):
    """`total_features` keeps meaning what every existing reader thinks it
    means (the gene count -- clusters.py reads it, as do clients that have not
    reloaded). The compound count is a new, unambiguous field beside it."""

    def test_all_three_builders_set_total_compounds(self):
        with open(BUILD_DB, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(
            3, len(re.findall(r'"total_compounds"\]\s*=', source)),
            "KEGG, Reactome and MapMan each write their own node data")

    def test_no_builder_still_ignores_compound_sharing(self):
        with open(BUILD_DB, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(3, source.count("\n    accumulateSharedFeatures("),
                         "each of the three network builders must fold compounds in")


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

def extract(source, name):
    """The text of `var <name> = function ... };`, brace-matched."""
    match = re.search(r"var\s+%s\s*=\s*function" % re.escape(name), source)
    if match is None:
        raise AssertionError("%s() is not defined" % name)
    opening = source.index("{", match.end())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1] + ";"
    raise AssertionError("unbalanced braces in %s()" % name)


def run_in_node(names, body):
    with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
        source = handle.read()
    script = "\n".join(extract(source, name) for name in names) + "\n" + body
    directory = tempfile.mkdtemp(prefix="paintomics-network-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        done = subprocess.run(["node", path], capture_output=True, text=True,
                              timeout=60)
        if done.returncode != 0:
            raise AssertionError("node failed:\n%s" % done.stderr)
        return json.loads(done.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class CoverageDenominatorTest(unittest.TestCase):
    """The denominator must count the feature classes the input actually
    contains. Anything else compares one kind of thing to another."""

    def coverage(self, counts, nodeData, omics):
        return run_in_node(
            ["paNetworkCoverageTotal"],
            "console.log(JSON.stringify({total: paNetworkCoverageTotal(%s, %s, %s)}));"
            % (json.dumps(counts), json.dumps(nodeData), json.dumps(omics)))["total"]

    def test_a_compound_only_job_divides_by_the_compounds(self):
        total = self.coverage({"genes": 34, "compounds": 144},
                              {"total_features": 34},
                              {"genes": False, "compounds": True})
        self.assertEqual(144, total,
                         "mmu01210: 16 matched compounds is 11% of 144, not 47% of 34")

    def test_a_gene_only_job_is_unchanged(self):
        total = self.coverage({"genes": 67, "compounds": 31},
                              {"total_features": 67},
                              {"genes": True, "compounds": False})
        self.assertEqual(67, total, "gene-based jobs must see exactly what they see today")

    def test_a_mixed_job_counts_both(self):
        total = self.coverage({"genes": 67, "compounds": 31},
                              {"total_features": 67},
                              {"genes": True, "compounds": True})
        self.assertEqual(98, total)

    def test_an_old_job_falls_back_to_the_installed_gene_count(self):
        """Jobs stored before the counts existed carry neither; total_features
        IS the gene count, so a gene-based job behaves exactly as before."""
        total = self.coverage({}, {"total_features": 67},
                              {"genes": True, "compounds": False})
        self.assertEqual(67, total)

    def test_a_reinstalled_species_rescues_an_old_compound_job(self):
        total = self.coverage({}, {"total_features": 34, "total_compounds": 144},
                              {"genes": False, "compounds": True})
        self.assertEqual(144, total)

    def test_an_unknown_denominator_disables_the_filter(self):
        """OmniPath ships no totals at all. Filtering on a number nobody knows
        would hide the whole network; returning null means 'do not filter'."""
        total = self.coverage({}, {}, {"genes": False, "compounds": True})
        self.assertIsNone(total)

    def test_a_database_that_never_had_the_filter_does_not_gain_one(self):
        """The regression this guards, and the reason the installed node is
        the gate rather than just a fallback.

        `total_features * minFeatures > matched` can never be true when
        total_features is absent (NaN) or zero, so those databases have no
        coverage filter and never have had one: OmniPath writes no such field,
        all 70 ath MapMan bins carry 0 (the installer counts only 20-character
        gene ids), and so do 220 of mmu's 584 KEGG maps. The job's own counts
        are a real number for every one of them, so using them unconditionally
        would switch a 50% filter on for the first time -- on ath MapMan bins
        holding 21,446 genes, which is a blank canvas again."""
        mapman = self.coverage({"genes": 21446, "compounds": 0},
                               {"total_features": 0},
                               {"genes": True, "compounds": False})
        self.assertIsNone(mapman, "MapMan has never had a coverage filter")

        omnipath = self.coverage({"genes": 20, "compounds": 0}, {},
                                 {"genes": True, "compounds": False})
        self.assertIsNone(omnipath, "OmniPath ships no total_features at all")

        kegg_without_genes = self.coverage({"genes": 0, "compounds": 128},
                                           {"total_features": 0},
                                           {"genes": False, "compounds": True})
        self.assertIsNone(kegg_without_genes,
                          "a KEGG map with no genes in pathway2gene.list was "
                          "never filtered on coverage either")

    def test_zero_is_a_known_answer_not_a_missing_one(self):
        """A pathway with no compounds in a compound-only job is genuinely
        uncoverable, but 0 * anything is 0 so it can never be excluded."""
        total = self.coverage({"genes": 40, "compounds": 0}, {"total_features": 40},
                              {"genes": False, "compounds": True})
        self.assertEqual(0, total)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class DefaultMinFeaturesTest(unittest.TestCase):
    def defaults(self, hasGenes, hasCompounds):
        return run_in_node(
            ["paNetworkDefaultMinFeatures"],
            "console.log(JSON.stringify({v: paNetworkDefaultMinFeatures(%s, %s)}));"
            % (json.dumps(hasGenes), json.dumps(hasCompounds)))["v"]

    def test_a_gene_based_job_keeps_the_shipped_half(self):
        self.assertEqual(0.5, self.defaults(True, False))
        self.assertEqual(0.5, self.defaults(True, True))

    def test_a_compound_only_job_starts_where_metabolomics_lives(self):
        """Transcriptomics measures ~100% of a pathway's genes; a metabolomics
        platform covers a tenth of its compounds. Median compound coverage on
        job 2J4u1qN5pm was 10.1%, and at 50% only 6 of 140 pathways cleared the
        bar against 75 at 10%."""
        self.assertEqual(0.1, self.defaults(False, True))

    def test_a_job_with_no_omic_at_all_keeps_the_shipped_default(self):
        self.assertEqual(0.5, self.defaults(False, False))


class OmicClassFallbackTest(unittest.TestCase):
    """A job recording neither class is read as gene-based.

    The client used to report {genes: false, compounds: false}, which leaves the
    denominator unknown for every class, switches the coverage filter off, and
    still shows the slider at 50% -- a control that visibly does nothing."""

    def test_the_client_agrees(self):
        with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("this.omicClasses = {\n\t\t\t/*")
        window = source[start:start + 900]
        self.assertIn("|| !hasCompoundOmics", window,
                      "the client must fall back to genes when a job records no omic class")


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class SharedFeatureEdgesTest(unittest.TestCase):
    """The 's' edge set shipped in the install file lists pathway pairs sharing
    a GENE. The weight the view draws is already recomputed from the job's
    matched genes AND compounds, so the file only ever decided which pairs were
    allowed to exist -- and it decided that on genes alone."""

    def edges(self, features, minimum):
        return run_in_node(
            ["paNetworkSharedFeatureEdges"],
            "console.log(JSON.stringify(paNetworkSharedFeatureEdges(%s, %s, %s)));"
            % (json.dumps(sorted(features)), json.dumps(features),
               json.dumps(minimum)))

    def test_pathways_sharing_only_compounds_are_connected(self):
        edges = self.edges({
            "mmu00250": {"genes": [], "compounds": ["C00025", "C00026", "C00064"]},
            "mmu00220": {"genes": [], "compounds": ["C00025", "C00026", "C00062"]},
        }, 0.1)

        self.assertEqual(1, len(edges))
        self.assertEqual("s", edges[0]["class"])
        # Sorensen-Dice: 2 * 2 / (3 + 3)
        self.assertAlmostEqual(2 / 3.0, edges[0]["weight"])

    def test_genes_and_compounds_are_pooled_into_one_coefficient(self):
        edges = self.edges({
            "a": {"genes": ["g1"], "compounds": ["C1"]},
            "b": {"genes": ["g1"], "compounds": ["C2"]},
        }, 0.1)
        self.assertAlmostEqual(0.5, edges[0]["weight"])

    def test_a_pair_below_the_threshold_is_dropped(self):
        edges = self.edges({
            "a": {"genes": ["g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8", "g9"],
                  "compounds": []},
            "b": {"genes": ["g1", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8"],
                  "compounds": []},
        }, 0.5)
        self.assertEqual([], edges)

    def test_pathways_sharing_nothing_get_no_edge(self):
        edges = self.edges({
            "a": {"genes": ["g1"], "compounds": []},
            "b": {"genes": ["g2"], "compounds": []},
        }, 0)
        self.assertEqual([], edges,
                         "an edge with a zero coefficient is not a relationship")

    def test_each_pair_appears_once(self):
        edges = self.edges({
            "a": {"genes": ["g1"], "compounds": []},
            "b": {"genes": ["g1"], "compounds": []},
            "c": {"genes": ["g1"], "compounds": []},
        }, 0.1)
        self.assertEqual(3, len(edges))
        self.assertEqual(3, len({tuple(sorted([e["source"], e["target"]]))
                                 for e in edges}))

    def test_an_empty_pathway_never_divides_by_zero(self):
        edges = self.edges({
            "a": {"genes": [], "compounds": []},
            "b": {"genes": [], "compounds": []},
        }, 0)
        self.assertEqual([], edges)


class ClientWiringTest(unittest.TestCase):
    """The helpers above are only worth anything if the view actually calls
    them, and if the model carries the numbers they need."""

    def test_the_model_carries_both_counts(self):
        with open(PATHWAY_MODELS, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("getTotalGenes", source)
        self.assertIn("getTotalCompounds", source)
        self.assertIn("jsonObject.totalCompounds", source)

    def test_the_node_filter_no_longer_reads_total_features_directly(self):
        with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("elem.data.total_features * visualOptions.minFeatures",
                         source,
                         "the gene-only denominator must be gone from the filter")
        self.assertIn("paNetworkCoverageTotal(", source)

    def test_the_cache_marker_was_bumped_with_app_js(self):
        """PA_JOB_CACHE_SCHEMA went 2 -> 3 so a browser holding a job model
        without the counts discards it. app.js is the ONE file loaded with a
        static ?v= (the view/model files go through $.ajax dataType:"script",
        which cache-busts itself), so without the bump a returning browser
        keeps the old app.js, keeps schema 2, keeps its stored model, and the
        fix never reaches it."""
        index = os.path.join(CLIENT, "index.html")
        with open(index, "r", encoding="utf-8") as handle:
            markup = handle.read()
        self.assertNotIn('src="app.js?v=0.4"', markup,
                         "app.js changed (PA_JOB_CACHE_SCHEMA) but its ?v= did not")

    def test_the_help_no_longer_promises_something_it_does_not_do(self):
        with open(STEP3_VIEWS, "r", encoding="utf-8") as handle:
            source = handle.read()
        # The helpTip span now opens the <h5> (so its float lands on the
        # label's first line), so the tip sits BEFORE the label text. The
        # "</span>" anchor skips the doc comments that name the slider.
        end = source.index("</span>Min features in pathway (")
        start = source.rindex("<h5>", 0, end)
        tip = source[start:end]
        self.assertIn('class="helpTip"', tip)
        self.assertNotIn("(genes + compounds) of a pathway found at the input", tip)


if __name__ == "__main__":
    unittest.main(verbosity=2)
