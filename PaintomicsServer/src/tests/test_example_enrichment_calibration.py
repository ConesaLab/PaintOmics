#!/usr/bin/env python3
"""Does the simulated example data behave like data, or like a fixture?

`test_example_scenarios_validate.py` asks whether the files are well formed.
This asks whether the *statistics* they produce are believable, which is a
different failure and one that nothing else here would catch: a scenario can be
perfectly well formed, load without error, and still call two thirds of KEGG
significant -- at which point the enrichment view is a wall of red and the
example teaches the opposite of what enrichment is for.

The yardstick is the real STATegra data shipped alongside, measured through the
same pipeline:

    omic              global relevant   mean per-pathway   ratio   significant
    Gene expression        82.4%              49.9%         0.61     54/846  6.4%
    Proteomics             13.3%               9.2%         0.69      5/657  0.8%
    miRNA-seq              22.1%               5.7%         0.26     24/816  2.9%
    DNase-seq              49.6%              52.5%         1.06     98/869   11%
    TF                     83.2%              21.4%         0.26     36/740  4.9%
    Metabolomics           70.7%              44.5%         0.63      0/366    0%

So: 0.8% to 11% of pathways significant, and a mean per-pathway relevant rate
between a quarter of the global rate and a little above it. The simulated
scenarios used to sit at 41-68% significant with a ratio of 3.2-4.7, for one
reason -- the planted signal did not stay in the pathways it was planted in.
KEGG pathways share genes freely (a mouse gene is in 3.85 of them on average),
so planting 70% of a hub pathway marks a slice of a hundred others, and the
hypergeometric background becomes the planted signal itself.

Measured, before and after the generator was changed to plant only in
*peripheral* pathways and to scatter a diffuse 5% of relevance over the
background (`python -m src.AdminTools.scripts.exampledata`):

    scenario                          before            after
    gene-single-condition          207/364  56.9%    26/364   7.1%
    gene-multi-condition           196/364  53.8%    24/364   6.6%
    gene-multi-condition-relevance 233/364  64.0%    17/364   4.7%
    multiomics-integration         228/364  62.6%    39/364  10.7%
    regulatory-mirna               179/359  49.9%    14/356   3.9%
    region-based (through B2G)     212/351  60.4%    27/360   7.5%

The hypergeometric recomputation here is offline -- pathway membership from the
KEGG snapshot, relevance from the shipped files -- rather than a pipeline run,
because a run of all six takes minutes and this takes seconds. It was checked
against the real thing: for gene-single-condition it reports 26 significant
pathways and PathwayAcquisitionJob.generatePathwaysList reports 26, and before
the change both reported 207.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_example_enrichment_calibration
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import ExampleDatasets                                  # noqa: E402
from src.common.Statistics import calculateFisher                       # noqa: E402

EXAMPLE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "examplefiles")) + os.sep
DATASETS_DIR = os.path.join(EXAMPLE_DIR, "datasets")

# The band the real data occupies, widened at the top by one point so a
# scenario that legitimately drifts to 12% is a review item and not a failure.
MIN_SIGNIFICANT_FRACTION = 0.005
MAX_SIGNIFICANT_FRACTION = 0.12

# mean(per-pathway relevant rate) / (global relevant rate), over the pathways
# that were NOT planted in. 1.0 means relevance is spread as thinly inside a
# pathway as outside it, which is what a background should look like. Real
# STATegra spans 0.26 to 1.06.
MIN_BACKGROUND_RATIO = 0.20
MAX_BACKGROUND_RATIO = 1.30

# A declared target must be recoverable. Ranked by p-value among all matched
# pathways, every one of them has to be inside this many places.
TARGET_RANK_CEILING = 30

# Share of a declared target pathway's members that must actually carry the
# planted signal. The point of the number is not that 0.5 is special: it is
# that a scenario declaring a pathway it planted almost nothing in -- which
# `regulatory-more` did, at 0, 0, 1 and 2 genes out of 31, 52, 21 and 147 --
# ships a ground-truth file that cannot be met.
MIN_TARGET_COVERAGE = 0.50


def keggSource():
    """The installed snapshot, or None if this checkout has no KEGG data."""
    try:
        from src.AdminTools.scripts.exampledata.keggsource import (
            KeggSource, SpeciesNotInstalled)
        from src.conf.serverconf import KEGG_DATA_DIR
    except ImportError:
        return None
    try:
        return KeggSource(KEGG_DATA_DIR, "mmu")
    except (SpeciesNotInstalled, OSError):
        return None


def readColumn(path, columns=1, skipHeader=False):
    """Identifiers from a relevant-features or values file, as a set."""
    found = set()
    with open(path, encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            if skipHeader and number == 0:
                continue
            cells = line.split("\t")
            for cell in (cells[:columns] if columns > 1 else cells[:1]):
                if cell.strip():
                    found.add(cell.strip())
    return found


def measuredFeatures(scenario):
    """The features a scenario actually submits, or None if it submits the lot.

    Only MORE needs this. It fits one model per target gene and a run costs
    ~0.29 s a gene, so it measures a fixed 250 of them and cannot cover a whole
    pathway; every other simulated scenario ships a values file over the entire
    gene universe. The hypergeometric's population is the submission, so the
    share that has to be dense in a declared target is the planted share of that
    pathway's *submitted* members -- against full membership, a MORE target
    reads as 0.18 covered when 0.75 of what it measures there is planted.
    """
    if not scenario.get("target"):
        return None
    path = ExampleDatasets.absolutePath(EXAMPLE_DIR, scenario["target"]["dataFile"])
    return readColumn(path, skipHeader=True)


def declaredTargets(scenario):
    """[(pathwayID, plantedShare)] from the scenario's expected-pathways file."""
    expected = scenario.get("expected", {})
    path = ExampleDatasets.absolutePath(EXAMPLE_DIR, expected["pathwaysFile"])
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            rows.append(line.rstrip("\n").split("\t")[0].strip())
    return rows


# `95/136 planted` or, for MORE, `7/9 planted (of 20 in the pathway)`.
_RECORDED_SHARE = re.compile(r"^\s*(\d+)/(\d+) planted")


def recordedTargets(scenario):
    """{pathwayID: (name, planted, members)} as the generator wrote them.

    Each line of the expected-pathways file carries the KEGG name and the
    planted share the generator measured on the KEGG it built the scenario on:
    `mmu04142<TAB>Lysosome<TAB>95/136 planted`. That line is the only record of
    what a target pathway was at build time, so it is what tells a generator
    that planted too little apart from a KEGG release that changed the pathway
    after the scenario was built.
    """
    expected = scenario.get("expected", {})
    path = ExampleDatasets.absolutePath(EXAMPLE_DIR, expected["pathwaysFile"])
    recorded = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            cells = [cell.strip() for cell in line.rstrip("\n").split("\t")]
            name = cells[1] if len(cells) > 1 else None
            share = _RECORDED_SHARE.match(cells[2]) if len(cells) > 2 else None
            recorded[cells[0]] = (name,
                                  int(share.group(1)) if share else None,
                                  int(share.group(2)) if share else None)
    return recorded


def renamedSinceBuild(recordedName, installedName):
    """Has KEGG renamed this pathway since the scenario was built?

    A new name is KEGG saying the map now stands for something else. mmu04142
    was "Lysosome", 136 mouse genes, on the KEGG the bundled examples were
    planted on; on paintomics.org's KEGG (downloaded 2026-08-13) it is
    "Lysosome biogenesis", 253 genes, and the 95 planted genes are 38% of it.
    A gene or two of drift under the same name is not a rename: those targets
    are still held to the floor. Case and spacing are ignored, and a name
    missing on either side is no evidence of a rename.
    """
    if not recordedName or not installedName:
        return False

    def norm(name):
        return " ".join(name.split()).casefold()
    return norm(recordedName) != norm(installedName)


def enrichmentShape(kegg, background, relevant, targets):
    """The three numbers this module is about, computed the way the job does.

    `background` is the submitted identifiers, restricted to those KEGG can
    place in a pathway -- which is what calculateTotalFeaturesByOmic counts.
    """
    background = {gene for gene in background if gene in kegg.geneToPathways}
    relevant = set(relevant) & background
    total, totalRelevant = len(background), len(relevant)
    if not total or not totalRelevant:
        return None

    rows = []
    for pathway, members in kegg.pathwayToGenes.items():
        inPathway = [gene for gene in members if gene in background]
        if not inPathway:
            continue
        hits = sum(1 for gene in inPathway if gene in relevant)
        rows.append((pathway, len(inPathway), hits,
                     calculateFisher(total, len(inPathway), totalRelevant, hits)))

    rows.sort(key=lambda row: row[3])
    rank = {row[0]: index + 1 for index, row in enumerate(rows)}
    targetSet = set(targets)
    outside = [hits / float(size) for pathway, size, hits, _p in rows
               if pathway not in targetSet]
    globalRate = totalRelevant / float(total)
    return {
        "pathways": len(rows),
        "globalRate": globalRate,
        "significant": sum(1 for row in rows if row[3] <= 0.05),
        "significantFraction": sum(1 for row in rows if row[3] <= 0.05) / float(len(rows)),
        "backgroundRatio": (sum(outside) / len(outside)) / globalRate if outside else 0.0,
        "targetRanks": sorted(rank.get(pathway, len(rows)) for pathway in targets),
    }


# Scenario -> (folder, values file, relevant file, relevant-file column count).
# Only the gene-based omics whose relevance reaches enrichment directly; the
# region scenario is covered separately because its identifiers are coordinates
# until Bed2Genes has run.
GENE_SCENARIOS = [
    ("gene-single-condition", "01-gene-single-condition",
     "gene_expression_values.tab", "gene_expression_relevant.tab", 1),
    ("gene-multi-condition", "02-gene-multi-condition",
     "gene_expression_values.tab", "gene_expression_relevant.tab", 1),
    ("gene-multi-condition-relevance", "03-gene-multi-condition-relevance",
     "gene_expression_values.tab", "gene_expression_relevant.tab", 6),
    ("multiomics-integration", "04-multiomics-integration",
     "gene_expression_values.tab", "gene_expression_relevant.tab", 1),
    ("regulatory-mirna", "05-regulatory-mirna",
     "gene_expression_values.tab", "gene_expression_relevant.tab", 1),
]


class EnrichmentCalibrationTest(unittest.TestCase):
    """The planted signal has to be findable AND the background quiet."""

    @classmethod
    def setUpClass(cls):
        cls.kegg = keggSource()
        if cls.kegg is None:
            raise unittest.SkipTest(
                "no installed KEGG snapshot; this checks the shipped data "
                "against pathway membership and cannot run without it")

    def _shape(self, scenarioId, folder, valuesName, relevantName, columns):
        scenario = ExampleDatasets.getScenario(EXAMPLE_DIR, scenarioId)
        data = os.path.join(DATASETS_DIR, folder, "data")
        return enrichmentShape(
            self.kegg,
            readColumn(os.path.join(data, valuesName)),
            readColumn(os.path.join(data, relevantName), columns=columns,
                       skipHeader=columns > 1),
            declaredTargets(scenario))

    def test_no_scenario_calls_most_of_kegg_significant(self):
        for scenarioId, folder, values, relevant, columns in GENE_SCENARIOS:
            shape = self._shape(scenarioId, folder, values, relevant, columns)
            self.assertIsNotNone(shape, "%s has no usable relevance" % scenarioId)
            self.assertLessEqual(
                shape["significantFraction"], MAX_SIGNIFICANT_FRACTION,
                "%s calls %d of %d pathways significant (%.1f%%). The real "
                "STATegra example calls 0.8-11%%. The usual cause is a planted "
                "signal that leaked out of its target pathways: check "
                "KeggSource.pathwayLeakage and the peripheral-pathway pool."
                % (scenarioId, shape["significant"], shape["pathways"],
                   100 * shape["significantFraction"]))

    def test_every_scenario_still_has_something_to_find(self):
        """The opposite failure. A quiet background is easy to get by making
        nothing significant at all, and that fixture is just as useless."""
        for scenarioId, folder, values, relevant, columns in GENE_SCENARIOS:
            shape = self._shape(scenarioId, folder, values, relevant, columns)
            self.assertGreaterEqual(
                shape["significantFraction"], MIN_SIGNIFICANT_FRACTION,
                "%s calls only %d of %d pathways significant; the planted "
                "signal is no longer recoverable"
                % (scenarioId, shape["significant"], shape["pathways"]))

    def test_the_declared_target_pathways_rank_at_the_top(self):
        """The whole purpose of these fixtures. Without this the two tests
        above could both be satisfied by data with no signal in it."""
        for scenarioId, folder, values, relevant, columns in GENE_SCENARIOS:
            shape = self._shape(scenarioId, folder, values, relevant, columns)
            worst = shape["targetRanks"][-1]
            self.assertLessEqual(
                worst, TARGET_RANK_CEILING,
                "%s declares a pathway that ranks %d of %d by p-value "
                "(ranks: %s). The expected-pathways file promises these are "
                "the ones enrichment finds."
                % (scenarioId, worst, shape["pathways"], shape["targetRanks"]))

    def test_the_background_is_as_relevant_as_the_foreground_is_not(self):
        """mean per-pathway relevance over the NON-planted pathways, against
        the global rate. Above ~1.3 the planted signal is leaking into
        everything; far below 0.2 there is no diffuse relevance at all and the
        background is the signal's own shadow."""
        for scenarioId, folder, values, relevant, columns in GENE_SCENARIOS:
            shape = self._shape(scenarioId, folder, values, relevant, columns)
            self.assertGreaterEqual(shape["backgroundRatio"], MIN_BACKGROUND_RATIO,
                                    "%s: background ratio %.2f" % (scenarioId,
                                                                  shape["backgroundRatio"]))
            self.assertLessEqual(
                shape["backgroundRatio"], MAX_BACKGROUND_RATIO,
                "%s: non-target pathways are %.2fx as relevant as the "
                "submission as a whole; real data sits at 0.26-1.06"
                % (scenarioId, shape["backgroundRatio"]))


class TargetCoverageTest(unittest.TestCase):
    """A declared target pathway must actually hold the planted signal."""

    @classmethod
    def setUpClass(cls):
        cls.kegg = keggSource()
        if cls.kegg is None:
            raise unittest.SkipTest("no installed KEGG snapshot")
        cls.scenarios = [scenario for scenario
                         in ExampleDatasets.loadManifest(EXAMPLE_DIR)["scenarios"]
                         if scenario.get("simulated")]

    def test_every_declared_target_carries_a_real_share_of_the_signal(self):
        """Checked against the KEGG_DATA installed here, one target at a time.

        A target KEGG has renamed since the build is skipped with the reason,
        not failed: the share was planted on the old pathway, and holding it
        to the new one blames the generator for a KEGG release. Every other
        target on the same scenario is still checked.
        """
        for scenario in self.scenarios:
            expected = scenario.get("expected", {})
            signalPath = ExampleDatasets.absolutePath(
                EXAMPLE_DIR, expected["signalFeaturesFile"])
            signal = readColumn(signalPath)
            measured = measuredFeatures(scenario)
            recorded = recordedTargets(scenario)
            for pathway in declaredTargets(scenario):
                with self.subTest(scenario=scenario["id"], pathway=pathway):
                    self._checkTarget(scenario["id"], pathway, signal, measured,
                                      recorded.get(pathway, (None, None, None)))

    def _checkTarget(self, scenarioId, pathway, signal, measured, recorded):
        recordedName, recordedPlanted, recordedMembers = recorded
        members = self.kegg.pathwayToGenes.get(pathway, ())
        self.assertTrue(members, "%s declares unknown pathway %s"
                        % (scenarioId, pathway))
        installedName = self.kegg.pathwayNames.get(pathway)
        if renamedSinceBuild(recordedName, installedName):
            self.skipTest(
                "KEGG renamed %s after %s was built: %r with %s genes then, "
                "%r with %d genes in this KEGG_DATA. The planted share was "
                "measured on the old pathway; rebuild the scenario on this "
                "KEGG (python -m src.AdminTools.scripts.exampledata) to check "
                "it again." % (pathway, scenarioId, recordedName,
                               recordedMembers, installedName, len(members)))
        if measured is not None:
            members = [gene for gene in members if gene in measured]
            self.assertTrue(
                members,
                "%s declares %s as a target and measures none of its "
                "genes" % (scenarioId, pathway))
        planted = sum(1 for gene in members if gene in signal)
        self.assertGreaterEqual(
            planted / float(len(members)), MIN_TARGET_COVERAGE,
            "%s declares %s as a target but planted %d of its %d "
            "genes (%s/%s when it was built). `expected_pathways.txt` says "
            "enrichment should rank it highly; nothing in the data makes "
            "that true."
            % (scenarioId, pathway, planted, len(members),
               recordedPlanted, recordedMembers))

    def test_the_manifest_records_the_coverage_it_achieved(self):
        """So a reader of the manifest can see the invariant without
        recomputing it, and a regeneration that breaks it is visible in the
        diff rather than only in this test."""
        for scenario in self.scenarios:
            expected = scenario.get("expected", {})
            self.assertIn("targetCoverage", expected, scenario["id"])
            self.assertEqual(len(expected["targetCoverage"]),
                             expected["targetPathways"], scenario["id"])
            self.assertGreaterEqual(expected["minTargetCoverage"],
                                    MIN_TARGET_COVERAGE, scenario["id"])


class _StandInKegg(object):
    """The two indexes TargetCoverageTest reads from a KeggSource."""

    def __init__(self, pathwayToGenes, pathwayNames):
        self.pathwayToGenes = pathwayToGenes
        self.pathwayNames = pathwayNames


class TargetCoverageAcrossKeggReleasesTest(unittest.TestCase):
    """TargetCoverageTest's verdict on a stand-in KEGG, so it runs everywhere.

    The KEGG-backed classes in this file skip wherever the KEGG_DATA has no
    Ensembl mapping, and CI's snapshot ships none, so there they never run.
    On paintomics.org's KEGG they ran and failed on mmu04142, which KEGG had
    renamed and nearly doubled after the example was planted. These build a
    stand-in from the shipped gene-single-condition files: every declared
    target holding only planted genes, then mmu04142 padded with twice as many
    unplanted ones, once under a new name and once under its old one.
    """

    SCENARIO = "gene-single-condition"
    PATHWAY = "mmu04142"

    @classmethod
    def setUpClass(cls):
        cls.scenario = ExampleDatasets.getScenario(EXAMPLE_DIR, cls.SCENARIO)
        cls.signal = sorted(readColumn(ExampleDatasets.absolutePath(
            EXAMPLE_DIR, cls.scenario["expected"]["signalFeaturesFile"])))
        cls.recorded = recordedTargets(cls.scenario)

    def _kegg(self, padded=None):
        """Every target = the planted genes; `padded` = (pathway, new name)."""
        pathwayToGenes, pathwayNames = {}, {}
        for pathway in declaredTargets(self.scenario):
            pathwayToGenes[pathway] = list(self.signal)
            pathwayNames[pathway] = self.recorded[pathway][0]
        if padded:
            pathway, name = padded
            pathwayToGenes[pathway] = list(self.signal) + [
                "ENSMUSG9%010d" % index for index in range(2 * len(self.signal))]
            pathwayNames[pathway] = name
        return _StandInKegg(pathwayToGenes, pathwayNames)

    def _verdict(self, kegg):
        """(failure texts, skip reasons) from TargetCoverageTest on `kegg`."""
        class StandIn(TargetCoverageTest):
            @classmethod
            def setUpClass(cls):
                pass
        StandIn.kegg = kegg
        StandIn.scenarios = [self.scenario]
        result = unittest.TestResult()
        StandIn("test_every_declared_target_carries_a_real_share_of_the_signal").run(result)
        return ([text for _test, text in result.failures + result.errors],
                [reason for _test, reason in result.skipped])

    def test_the_build_records_the_name_this_relies_on(self):
        self.assertEqual(self.recorded[self.PATHWAY], ("Lysosome", 95, 136))

    def test_a_kegg_that_matches_the_build_passes_with_nothing_skipped(self):
        self.assertEqual(self._verdict(self._kegg()), ([], []))

    def test_a_target_kegg_renamed_and_enlarged_is_skipped_not_failed(self):
        """paintomics.org's KEGG: "Lysosome biogenesis", 253 genes."""
        failures, skips = self._verdict(
            self._kegg(padded=(self.PATHWAY, "Lysosome biogenesis")))
        self.assertEqual(failures, [])
        self.assertEqual(len(skips), 1, skips)
        self.assertIn(self.PATHWAY, skips[0])
        self.assertIn("'Lysosome biogenesis'", skips[0])

    def test_a_target_enlarged_under_its_old_name_still_fails(self):
        """No rename, no excuse: a short-planted target is the generator's."""
        failures, skips = self._verdict(
            self._kegg(padded=(self.PATHWAY, "Lysosome")))
        self.assertEqual(skips, [])
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("planted %d of its %d genes" % (
            len(self.signal), 3 * len(self.signal)), failures[0])

    def test_a_change_of_case_or_spacing_is_not_a_rename(self):
        self.assertFalse(renamedSinceBuild("Lysosome", "  lysosome "))
        self.assertFalse(renamedSinceBuild(None, "Lysosome biogenesis"))
        self.assertFalse(renamedSinceBuild("Lysosome", None))
        self.assertTrue(renamedSinceBuild("Lysosome", "Lysosome biogenesis"))


class CompoundSignalTest(unittest.TestCase):
    """The multi-omic scenario's compound layer has to agree with its genes.

    Its manifest lists "Gene- and compound-based enrichment side by side" as a
    tested function, and its summary says the layers "share one planted signal
    so the layers agree". They did not: `compoundsIn` was looking mouse pathway
    ids up in a file keyed by KEGG's reference maps, found nothing every time,
    and the caller fell through to a random sample of the compound universe. Two
    of 329 relevant compounds landed in any declared target and six of the eight
    targets had a Metabolomics p-value of exactly 1.0.
    """

    @classmethod
    def setUpClass(cls):
        cls.kegg = keggSource()
        if cls.kegg is None:
            raise unittest.SkipTest("no installed KEGG snapshot")
        cls.scenario = ExampleDatasets.getScenario(EXAMPLE_DIR,
                                                   "multiomics-integration")
        omic = next(entry for entry in cls.scenario["omics"]
                    if entry["omicType"] == "compound")
        cls.relevant = readColumn(
            ExampleDatasets.absolutePath(EXAMPLE_DIR, omic["relevantFile"]))
        cls.values = readColumn(
            ExampleDatasets.absolutePath(EXAMPLE_DIR, omic["dataFile"]))

    def test_the_species_pathway_lookup_returns_anything_at_all(self):
        """The bug underneath: a species id against a `map`-keyed file."""
        anyPathway = sorted(self.kegg.pathwayToGenes)[0]
        found = [pathway for pathway in sorted(self.kegg.pathwayToGenes)
                 if self.kegg.compoundsIn([pathway])]
        self.assertTrue(
            found,
            "compoundsIn returned nothing for every one of the %d %s pathways; "
            "pathway2compound.list is keyed by reference map id"
            % (len(self.kegg.pathwayToGenes), anyPathway[:3]))

    def test_every_declared_target_has_planted_compounds(self):
        for pathway in declaredTargets(self.scenario):
            compounds = self.kegg.compoundsIn([pathway])
            self.assertTrue(compounds,
                            "%s was declared a target of a scenario that plants "
                            "a compound signal, but has no compounds" % pathway)
            planted = [compound for compound in compounds
                       if compound in self.relevant]
            self.assertGreaterEqual(
                len(planted) / float(len(compounds)), 1.0 / 3,
                "target %s has %d of its %d compounds in the relevant list; "
                "its Metabolomics p-value is decided by chance"
                % (pathway, len(planted), len(compounds)))

    def test_the_compound_values_file_holds_the_relevant_compounds(self):
        missing = sorted(self.relevant - self.values)
        self.assertEqual(missing[:5], [],
                         "%d relevant compounds are not in the values file"
                         % len(missing))


class CompoundNameFileTest(unittest.TestCase):
    """The name-keyed variant must be keyed by names KEGG knows.

    Its note tells a user to load it "instead of the ID-keyed file ... to
    exercise the matched-metabolites selection step". It used to be keyed
    `Compound 00001` ... `Compound 00400`, which resolves to nothing: mapped=0,
    unmapped=400, no compound panel and no hub analysis. The step was exercised
    only in the sense that it rejected the whole file.
    """

    @classmethod
    def setUpClass(cls):
        cls.kegg = keggSource()
        if cls.kegg is None:
            raise unittest.SkipTest("no installed KEGG snapshot")
        scenario = ExampleDatasets.getScenario(EXAMPLE_DIR,
                                               "multiomics-integration")
        extras = [extra for extra in scenario.get("extraFiles", [])
                  if "name" in extra["role"]]
        if not extras:
            raise unittest.SkipTest("no name-keyed metabolomics file declared")
        cls.path = ExampleDatasets.absolutePath(EXAMPLE_DIR, extras[0]["path"])
        cls.names = sorted(readColumn(cls.path))
        cls.known = {}
        for compound, synonyms in cls.kegg.compoundNames.items():
            for synonym in synonyms:
                cls.known.setdefault(synonym.lower(), compound)

    def test_every_row_is_a_real_kegg_compound_name(self):
        if not self.known:
            self.skipTest("compounds_all.list is not in this snapshot")
        unknown = [name for name in self.names if name.lower() not in self.known]
        self.assertEqual(
            unknown[:5], [],
            "%d of %d rows are not KEGG compound names, so they resolve to "
            "nothing and the file maps 0 metabolites"
            % (len(unknown), len(self.names)))

    def test_no_name_would_be_read_as_a_regular_expression(self):
        """findCompoundIDByFeatureName splices the name into `.*<name>.*`."""
        import re
        for name in self.names:
            self.assertIsNotNone(
                re.match(r"^[A-Za-z][A-Za-z0-9 '\-]*$", name),
                "%r carries a regex metacharacter; the lookup would either "
                "match the wrong compounds or raise inside the driver" % name)

    def test_the_names_are_unique(self):
        self.assertEqual(len(self.names), len(set(name.lower() for name
                                                  in self.names)),
                         "two rows share an identifier; the second overwrites "
                         "the first")


class MoreTargetGenesTest(unittest.TestCase):
    """MORE's modelled gene set is a signal AND a background.

    Both halves have been missing, one at a time.

    `_pickTargets` used to hand back the planted signal, find it smaller than
    MORE_TARGETS, and silently widen the pool to the entire mouse gene
    universe -- so the 250 modelled genes were a random draw from 10406 and the
    four declared pathways held 0, 0, 1 and 2 of them. The run reported 0 of
    289 matched pathways significant while the ground-truth file claimed four.

    The correction confined every modelled gene to the declared targets, and
    the test that pinned it asserted exactly that. It made the coverage claim
    true and the fixture useless: with no gene outside the targets there is no
    population to draw a hypergeometric sample from, 90.4% of modelled genes
    came back relevant, and a full run reported 1 significant pathway of 96 --
    mmu01100, not a declared target -- with the declared ones at p 0.109 to
    0.976. A pathway fixture needs both halves, so this asserts both.
    """

    @classmethod
    def setUpClass(cls):
        cls.kegg = keggSource()
        if cls.kegg is None:
            raise unittest.SkipTest("no installed KEGG snapshot")
        cls.scenario = ExampleDatasets.getScenario(EXAMPLE_DIR, "regulatory-more")
        cls.genes = measuredFeatures(cls.scenario)
        cls.inside = set()
        for pathway in declaredTargets(cls.scenario):
            cls.inside.update(cls.kegg.pathwayToGenes.get(pathway, ()))

    def test_the_declared_pathways_are_well_represented_among_the_models(self):
        """Enough modelled genes inside each target that it can be enriched."""
        for pathway in declaredTargets(self.scenario):
            members = self.kegg.pathwayToGenes.get(pathway, ())
            modelled = [gene for gene in members if gene in self.genes]
            self.assertGreaterEqual(
                len(modelled), 4,
                "%s is a declared target with only %d of its %d genes "
                "modelled; a hypergeometric over so few cannot reach 0.05 "
                "however relevant they are" % (pathway, len(modelled),
                                               len(members)))

    def test_most_modelled_genes_are_background(self):
        """The contrast the enrichment is measured against has to exist."""
        outside = self.genes - self.inside
        share = len(outside) / float(len(self.genes))
        self.assertGreater(
            share, 0.5,
            "only %d of %d modelled genes lie outside every declared target "
            "pathway (%.0f%%). The enrichment background would be the planted "
            "signal itself." % (len(outside), len(self.genes), 100 * share))

    def test_the_manifest_agrees_with_the_files(self):
        expected = self.scenario["expected"]
        outside = self.genes - self.inside
        self.assertEqual(expected["modelledTargets"], len(self.genes))
        self.assertEqual(expected["backgroundGenes"], len(outside))
        self.assertEqual(expected["inPathwayGenes"],
                         len(self.genes) - len(outside))


if __name__ == "__main__":
    unittest.main(verbosity=2)
