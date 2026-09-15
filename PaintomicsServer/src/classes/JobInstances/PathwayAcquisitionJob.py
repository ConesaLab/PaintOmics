# ***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomics4@outlook.com
# **************************************************************
import glob
import logging
import math
import os
import numpy as np
import re

from os import path as os_path, makedirs as os_makedirs
from csv import reader as csv_reader
from zipfile import ZipFile as zipFile

from subprocess import STDOUT, CalledProcessError

from src.common.Util import unifyAndSort

from collections import defaultdict, Counter
from itertools import chain

from src.common.Statistics import calculateSignificance, calculateCombinedSignificancePvalues, adjustPvalues
from src.common.Util import chunks, getImageSize
from src.common.ReplicateDetection import detect_replicates, aggregate_replicates
from src.common.DesignFile import parse_design, derive_groupings

from src.common.KeggInformationManager import KeggInformationManager
from src.common.FeatureNamesToKeggIDsMapper import _joinAllWithinDeadline
from src.common import JobProgress

from src.classes.Job import Job
from src.classes.Feature import Gene, Compound
from src.classes.Pathway import Pathway
from src.classes.PathwayGraphicalData import PathwayGraphicalData

from src.conf.serverconf import KEGG_DATA_DIR, MAX_THREADS, MAX_WAIT_THREADS, MAX_NUMBER_FEATURES
# Defensive: a deployment whose serverconf predates the setting must not lose
# the whole job class to an ImportError (see adding-a-serverconf-setting).
try:
    from src.conf.serverconf import CLASS_ACTIVITY_PERMUTATIONS
except ImportError:
    CLASS_ACTIVITY_PERMUTATIONS = int(os.getenv("PAINTOMICS_CLASS_ACTIVITY_PERMUTATIONS", "2000"))


# ensure_utf8 lives in src/common/Util.py so the data-management jobs can
# use it too -- Bed2GeneJob and MiRNA2GeneJob read uploads with the same
# bare open() this was written to protect. Imported here rather than moved
# out of reach, so the three call sites below and the existing test that
# imports it from this module are unaffected.
from src.common.Util import ensure_utf8


# Small dict fields safe to persist in the main MongoDB document
PAINTOMICS4_DICT_FIELDS = {
    "mappingComp", "classificationDict", "pValueInDict",
    "adjustPvalue", "totalRelevantFeaturesInCategory", "featureSummary",
    # Class-map metadata: the BRITE level-1 parent per tested class, and the
    # null proportion each condition was actually judged against. Both were
    # computed and discarded before; both are needed to draw an honest chart.
    "classificationMeta",
    # MORE rpc table. Bounded at 100k rows by parseRegulationPerCondition,
    # so worst-case ~5 MB — well under the 16 MB Mongo doc limit and an order
    # of magnitude smaller than the LARGE_FIELDS set's compoundRegulateFeatures.
    "regulationPerConditionData",
    # Metabolite hub analysis. Was in LARGE_FIELDS, but it is not large: it is
    # parsed one-to-one from hub_result.csv, and across all 45 jobs on this
    # machine that file never exceeds 3819 bytes — 0.02% of the 16 MB limit.
    # It sat beside compoundRegulateFeatures and inherited a "too large to
    # store" justification that was only ever true of its neighbours.
    #
    # It belongs here rather than in LARGE_FIELDS for a second reason: its keys
    # are the integer row indices hubAnalysis assigns (`hubResult[i] = line`),
    # and Mongo rejects those outright —
    #     InvalidDocument: documents must have only string keys, key was 0
    # This branch stringifies them. Adding the field to step 2's update without
    # moving it here would fail the store for every job that selects compounds.
    "hubAnalysisResult",
    # Metabolite expression per condition: {compoundID: [value, ...]}, keyed by
    # KEGG id. Measured on a six-omic example job with 96 compounds selected:
    # 96 entries, 11126 bytes — 0.07% of the 16 MB limit. It was in
    # LARGE_FIELDS on the same inherited assumption hubAnalysisResult was, and
    # it is the field the Step 3 metabolite panels are gated on, so dropping it
    # cost the whole section on cold recovery.
    "exprssionMetabolites",
    # Class activity at BRITE levels 1-3 with the test that ran (binomial on
    # the relevant list, or the permutation test on replicates), per-class
    # statistics and per-metabolite F/p. 58 metabolites x 3 levels is ~40 KB.
    "classActivity",
}

# Large dict fields that stay in-memory cache only (too large for a single
# MongoDB document). Sizes measured on the six-omic example, same job:
#   compoundRegulateFeatures  55 entries, 2.67 MB
#   globalExpressionData       2 entries, 4.29 MB
# Together ~7 MB before the rest of the document, so these two genuinely earn
# their place — unlike the two small fields that used to sit beside them.
# On cold recovery the safe_* defaults in the servlet return {}/[].
PAINTOMICS4_LARGE_FIELDS = {
    "compoundRegulateFeatures", "globalExpressionData"
}


# _loadCompoundNeighbourMap and its single-slot cache of kegg_interaction.json
# were deleted with the R hub path. The graph comes from
# src.common.KeggGraph.store now, which derives it from the organism's KGML in
# ~1 s and holds an LRU of four organisms at ~30 MB each -- against the 393 MB
# peak that one JSON parse measured, in a cache with room for a single species.

def _metagenesParallelism():
    """How many omics' R scripts run at once. Each Rscript peaks near 260 MB
    (cluster + mclust + factoextra/ggplot2), so this is bounded by memory
    before cores; 3 is safe on the 8 GB production host. 1 restores the old
    strictly sequential behaviour."""
    try:
        return max(1, int(os.getenv("PAINTOMICS_METAGENES_PARALLEL", "3")))
    except ValueError:
        return 3


def mappedTotal(omicSummary):
    """The number of features an omic mapped, from its omicSummary.

    First position: a dictionary per database ("Total" is the maximum) for
    gene-based omics, a bare int for compound-based ones. Shared with
    getMappedRatios, which used to carry this rule alone.
    """
    if not omicSummary:
        return 0
    first = omicSummary[0]
    if isinstance(first, int):
        return first
    if isinstance(first, dict):
        total = first.get("Total")
        if total is None:
            total = next(iter(first.values()), 0)
        try:
            return int(total or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def hasDataRows(path):
    """Whether a mapping file holds at least one non-blank line."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    return True
    except OSError:
        return False
    return False


_ENSEMBL_VERSIONED = re.compile(r"^ENS[A-Z]*[GTP]\d{5,}\.\d+$")


def explainEmptyMapping(organism, geneOmics, sampleUnmatchedFor, detect=None):
    """Why step 2 has nothing to work on, or None.

    An omic whose identifiers matched nothing passed step 1 as a success
    (omicSummary {KEGG: 0}) and then step 2 died INSIDE R -- generateMetaGenes.R
    was handed an empty <omic>_matched.txt and reported "no lines available in
    input", which reached the user as a metagenes failure. Measured on three
    real files (2026-08-27): a GEO count matrix with versioned Ensembl ids
    (ENSMUSG00000102693.2), PacBio transcript ids, and a human file run as
    mouse. None of them is a metagenes problem; all three are "nothing
    matched", and that is what this says, with the identifiers in question.
    """
    if not geneOmics:
        return None
    if any(mappedTotal(omic.get("omicSummary")) > 0 for omic in geneOmics):
        return None
    parts, samples = [], []
    for omic in geneOmics:
        name = omic.get("omicName") or "omic"
        summary = omic.get("omicSummary") or []
        unmatched = summary[1] if len(summary) > 1 and isinstance(summary[1], int) else None
        sample = list(sampleUnmatchedFor(name) or [])[:3]
        samples.extend(sample)
        parts.append("'%s'%s%s" % (
            name,
            (" (%d identifiers)" % unmatched) if unmatched is not None else "",
            (", e.g. " + ", ".join(sample)) if sample else ""))
    lines = ["None of the identifiers in your data matched %s's KEGG genes: %s "
             "matched 0, so there is nothing to analyse." % (organism, "; ".join(parts))]
    if samples and all(_ENSEMBL_VERSIONED.match(x) for x in samples):
        lines.append("These carry an Ensembl version suffix (the .%s in %s); KEGG "
                     "knows them without it, so strip the suffix and run again."
                     % (samples[0].rsplit(".", 1)[1], samples[0]))
    else:
        lines.append("Check the organism you chose and the kind of identifier "
                     "in the first column: the mapping summary on this page "
                     "says which identifiers PaintOmics recognised.")
    # Which organism the identifiers DO fit, when that can be said. The
    # message above told three users in one fortnight to "check the organism"
    # and none of them could tell which one to check; the detector answers
    # from the same xref index the mapping used (common/OrganismDetector.py).
    # Optional and fail-safe: no detector, or a detector that raises, leaves
    # the message exactly as it was.
    if detect is not None:
        probe = []
        for omic in geneOmics:
            name = omic.get("omicName") or "omic"
            try:
                probe.extend(sampleUnmatchedFor(name, ORGANISM_PROBE_SIZE) or [])
            except TypeError:           # a sampler that takes the name alone
                probe.extend(sampleUnmatchedFor(name) or [])
        try:
            hint = detect(probe, organism) if probe else None
        except Exception as ex:
            logging.warning("explainEmptyMapping: organism hint failed (%s: %s)",
                            type(ex).__name__, ex)
            hint = None
        if hint:
            lines.append(hint)
    return " ".join(lines)


# Identifiers handed to the organism hint per omic: enough to tell mouse from
# rat on gene symbols (five cannot), few enough for one indexed lookup.
ORGANISM_PROBE_SIZE = 150


def organismHint(identifiers, organism):
    """The detector's one-sentence verdict for a step 2 failure, or None."""
    from src.common.OrganismDetector import detectOrganism, describeHint
    return describeHint(detectOrganism(identifiers, selected=organism))


def _runMetagenesScripts(omicNames, databases, commands):
    """
    Run one generateMetaGenes.R per (omic, database) -- `commands[(omic, db)]`
    is (argv, kClusters) -- with the OMICS in parallel and each omic's databases
    one after another (see generateMetagenesList for why), and return
    {(omic, db): (returncode, output bytes)} exactly as check_output would have
    observed each run. A launch failure (e.g. no Rscript) is stored as
    (None, OSError) and re-raised where the sequential loop would have met it,
    so the caller's error handling is unchanged.
    """
    from concurrent.futures import ThreadPoolExecutor
    from subprocess import Popen, PIPE

    def runOmic(omicName):
        omicResults = {}
        for dbname in databases:
            argv = commands[(omicName, dbname)][0]
            try:
                process = Popen(argv, stdout=PIPE, stderr=STDOUT)
                output, _ = process.communicate()
                omicResults[(omicName, dbname)] = (process.returncode, output)
            except OSError as ex:
                omicResults[(omicName, dbname)] = (None, ex)
        return omicResults

    results = {}
    workers = min(_metagenesParallelism(), max(1, len(omicNames)))
    if workers <= 1:
        for omicName in omicNames:
            results.update(runOmic(omicName))
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for omicResults in pool.map(runOmic, omicNames):
            results.update(omicResults)
    return results


def _inputFeatureLookups(inputGenes, inputCompounds):
    """The per-job lookups every enrichment worker needs: features indexed by
    lower-cased ID, and whether the job carries more than one condition.

    Computed ONCE by the parent (they depend only on the input) and handed to
    the workers, which used to rebuild them six times over.
    """
    inputGenesDict = {g.getID().lower(): g for g in inputGenes}
    inputCompoundsDict = {c.getID().lower(): c for c in inputCompounds}

    max_conditions = 1
    for feature in chain(inputGenes, inputCompounds):
        for ov in feature.getOmicsValues():
            if isinstance(ov.relevant, list):
                max_conditions = max(max_conditions, len(ov.relevant))
    return inputGenesDict, inputCompoundsDict, max_conditions > 1


def _matchPathways(jobInstance, pathwaysList, genesInAllPathways, compoundsInAllPathways, inputGenes,
                    inputCompounds, totalFeaturesByOmic, totalRelevantFeaturesByOmic, matchedPathways,
                    mappedRatiosByOmic, enrichmentByOmic, lookups=None):
    """Module-level wrapper so multiprocessing.Process can pickle the target."""
    keggInformationManager = KeggInformationManager()

    if lookups is None:
        lookups = _inputFeatureLookups(inputGenes, inputCompounds)
    inputGenesDict, inputCompoundsDict, has_multi_cond = lookups

    # Collected locally and handed to the parent in ONE update at the end.
    # `matchedPathways` is a Manager dict proxy in the forked path: every
    # per-pathway assignment on it was a pickle of the whole Pathway plus a
    # socket round trip to the manager process, ~900 of them per job.
    localMatched = {}

    for pathwayID in pathwaysList:
        genesInPathway = genesInAllPathways.get(pathwayID)
        compoundsInPathway = compoundsInAllPathways.get(pathwayID)
        sourceDB = keggInformationManager.getPathwaySourceByID(jobInstance.getOrganism(), pathwayID)

        if "Unknown Pathway" in sourceDB:
            sourceDB = 'Reactome'

        if sourceDB not in totalFeaturesByOmic:
            continue

        isValidPathway, pathway = jobInstance.testPathwaySignificance(
            genesInPathway, compoundsInPathway, inputGenesDict, inputCompoundsDict,
            totalFeaturesByOmic.get(sourceDB), totalRelevantFeaturesByOmic.get(sourceDB),
            mappedRatiosByOmic, enrichmentByOmic, sourceDB, has_multi_cond)

        if isValidPathway:
            pathway.setID(pathwayID)
            pathway.setName(keggInformationManager.getPathwayNameByID(jobInstance.getOrganism(), pathwayID))
            pathway.setClassification(
                keggInformationManager.getPathwayClassificationByID(jobInstance.getOrganism(), pathwayID))
            pathway.setSource(sourceDB)
            localMatched[pathwayID] = pathway

    matchedPathways.update(localMatched)


class PathwayAcquisitionJob(Job):
    # ******************************************************************************************************************
    # CONSTRUCTORS
    # ******************************************************************************************************************
    def __init__(self, jobID, userID, CLIENT_TMP_DIR):
        super(PathwayAcquisitionJob, self).__init__(jobID, userID, CLIENT_TMP_DIR)
        # TODO: OPTION TO CHANGE THESE VALUES
        self.test = "fisher"
        self.combinedTest = "fisher-combined"
        self.summary = None
        # In this table we save all the matched pathways and for each pathways the associated selected compounds and genes.
        self.matchedPathways = {}
        self.foundCompounds = []

        # PaintOmics 4
        self.mappingComp = None
        self.pValueInDict = None
        self.classificationDict = None
        self.exprssionMetabolites = None
        self.adjustPvalue = None
        self.totalRelevantFeaturesInCategory = None
        self.featureSummary = None
        self.classificationMeta = None
        self.classActivity = None
        self.compoundRegulateFeatures = None

        self.globalExpressionData = None
        self.hubAnalysisResult = None
        # MORE RegulationPerCondition table (populated by parseRegulationPerCondition
        # in Step 4 of the implementation). Stays None if MORE wasn't run, so the
        # Step 3 client panel hides itself. Shape when populated:
        #   {"columns": [...], "rows": [[...], ...], "truncated": bool}
        self.regulationPerConditionData = None

        # AI Interpretation
        self.aiConsent = False
        self.experimentDesign = ""

        self.matchedClass = {}

    # ******************************************************************************************************************
    # GETTERS AND SETTER
    # ******************************************************************************************************************
    def getTest(self):
        return self.test

    #PaintOmics 4
    def setMatchedClass(self, matchedClass):
        self.matchedClass = matchedClass

    def getMatchedClass(self):
        return self.matchedClass

    def addMatchedClass(self, matchedClass):
        """Counterpart of addMatchedPathway, used when reloading from storage."""
        self.matchedClass[matchedClass.getID()] = matchedClass

    def setMatchedPathways(self, matchedPathways):
        self.matchedPathways = matchedPathways

    def getMatchedPathways(self):
        return self.matchedPathways

    def addMatchedPathway(self, matchedPathway):
        self.matchedPathways[matchedPathway.getID()] = matchedPathway

    def addFoundCompound(self, foundCompound):
        self.foundCompounds.append(foundCompound)

    def getFoundCompounds(self):
        return self.foundCompounds

    def getAIConsent(self):
        return self.aiConsent
    def setAIConsent(self, v):
        self.aiConsent = (v == True or v == "true" or v == "True")
    def getExperimentDesign(self):
        return self.experimentDesign
    def setExperimentDesign(self, v):
        self.experimentDesign = str(v) if v else ""

    def getJobDescription(self, generate=False, isExampleJob=False):
        if generate:
            if isExampleJob:
                self.description = "Example Job;"
            from os.path import basename

            self.description += "Input data:;"

            # The client requires to have the config options inside double square brackets and separated by '!!'.
            # The different omics must use ';' as separator, and the mainFile/relevantFiles should be inside the same
            # simple square brackets, separated by '!!'.
            for omicAux in self.geneBasedInputOmics:
                omic_files = [basename(omicAux.get("inputDataFile"))]

                self.description += omicAux.get("omicName")

                if omicAux.get("relevantFeaturesFile"):
                    omic_files.append(basename(omicAux.get("relevantFeaturesFile")))

                if omicAux.get("configOptions"):
                    self.description += " [[" + omicAux.get("configOptions").replace(";", "!!") + "]] "

                self.description += " [" + '!!'.join(omic_files) + "]; "
            for omicAux in self.compoundBasedInputOmics:
                self.description += omicAux.get("omicName") + " [" + basename(omicAux.get("inputDataFile")) + "]; "

        return self.description

    def sampleUnmatched(self, omicName, limit=3):
        """The first identifiers of <omic>_unmatched.txt in the mapping zip."""
        try:
            with zipFile(self.getOutputDir() + "/mapping_results_" + self.getJobID() + ".zip") as mappingZip:
                member = omicName + "_unmatched.txt"
                if member not in mappingZip.namelist():
                    return []
                sample = []
                with mappingZip.open(member) as handle:
                    for raw in handle:
                        first = raw.decode("utf-8", "replace").split("\t")[0].strip()
                        if first and not first.startswith("#"):
                            sample.append(first)
                        if len(sample) >= limit:
                            break
                return sample
        except Exception:
            return []

    def explainEmptyMapping(self, selectedCompounds=None):
        """See explainEmptyMapping(): None unless every gene-based omic mapped
        nothing and no compound was selected either."""
        if selectedCompounds:
            return None
        return explainEmptyMapping(self.getOrganism(), self.getGeneBasedInputOmics(),
                                   self.sampleUnmatched, detect=organismHint)

    def getMappedRatios(self):
        # Calculate the mapped/unmapped ratio of each omic
        mapped_ratios = {}

        for genericOmic in self.getGeneBasedInputOmics() + self.getCompoundBasedInputOmics():
            omicSummary = genericOmic.get("omicSummary")

            # First position: dictionary with identifiers.
            # With multiple databases "Total" is the maximum
            # Compounds omics only have one value (no dict)
            # Lazy fallback: .get(key, expr) evaluates expr even when the key
            # exists, and an empty dict made list({}.values())[0] raise.
            totalMapped = mappedTotal(omicSummary)

            # Second position: considering total if it exists
            totalUnmapped = omicSummary[1]

            try:
                ratio = float(totalMapped) / float(totalMapped + totalUnmapped)
            except (ZeroDivisionError, TypeError, ValueError):
                ratio = 0

            # A mapped ratio is a proportion of the input, so it cannot leave
            # [0, 1]. It is not cosmetic: generatePathwaysList hands this
            # dictionary to the Stouffer/Fisher combination as the per-omic
            # weight, so a ratio above 1 silently over-weights that omic in
            # every combined pathway p-value. This is the last line of defence
            # -- the counting that produced it is fixed at source in
            # updateSubmitedCompoundsList -- and it is loud, because a clamp
            # here means some summary is still wrong somewhere upstream.
            if ratio < 0.0 or ratio > 1.0 or ratio != ratio:
                logging.warning(
                    "IMPOSSIBLE MAPPED RATIO %s FOR OMIC '%s' (mapped=%s, unmapped=%s); "
                    "CLAMPING TO [0, 1]" % (
                        ratio, genericOmic.get("omicName"), totalMapped, totalUnmapped))
                ratio = 0.0 if ratio != ratio else min(max(ratio, 0.0), 1.0)

            mapped_ratios[genericOmic.get("omicName")] = ratio

        return mapped_ratios

    # ******************************************************************************************************************
    # OTHER FUNCTIONS
    # ******************************************************************************************************************

    # VALIDATION FUNCTIONS  ----------------------------------------------------------------------------------------------------
    def validateInput(self):
        """
        This function check the content for files and returns an error message in case of invalid content

        @returns True if not error
        """
        error = ""

        nConditions = -1
        # Which omic fixed nConditions for the whole job. A file that disagrees
        # with it is told so by name -- see _conditionsDisagreement.
        self._conditionsFixedBy = None

        # Establish nConditions from the first available data file
        all_omics = self.geneBasedInputOmics + self.compoundBasedInputOmics
        for inputOmic in all_omics:
            valuesFileName = inputOmic.get("inputDataFile")
            if not inputOmic.get("isExample", False) and valuesFileName:
                valuesFileName = "{path}/{file}".format(path=self.getInputDir(), file=valuesFileName)
                if os_path.isfile(valuesFileName):
                    # Normalise the encoding before the first read of the file.
                    # This loop runs ahead of validateFile(), which is where the
                    # conversion used to live, so a non-UTF-8 upload raised
                    # UnicodeDecodeError here before anything could transcode it.
                    encodingError = ensure_utf8(valuesFileName)
                    if encodingError is not None:
                        error += " - Errors detected while processing " + \
                                 inputOmic.get("inputDataFile", "") + ": " + encodingError + ".\n"
                        continue
                    values_delimiter = Job.detect_delimiter(valuesFileName)
                    with open(valuesFileName, 'r', encoding='utf-8-sig', newline='') as f:
                        for line in csv_reader(f, delimiter=values_delimiter):
                            if len(line) > 1:
                                try:
                                    float(line[1])
                                    nConditions = len(line)
                                    self._rememberConditionsFixedBy(inputOmic, nConditions)
                                    break
                                except ValueError:
                                    continue
                if nConditions != -1:
                    break

        logging.info("VALIDATING GENE BASED FILES...")
        for inputOmic in self.geneBasedInputOmics:
            nConditions, error = self.validateFile(inputOmic, nConditions, error)

        logging.info("VALIDATING COMPOUND BASED FILES...")
        for inputOmic in self.compoundBasedInputOmics:
            nConditions, error = self.validateFile(inputOmic, nConditions, error)

        if error != "":
            logging.info("VALIDATING ERRORS. RAISING EXCEPTION. Error: " + error)
            raise Exception(
                "[b]Errors detected in input files, please fix the following issues and try again:[/b][br]" + error)

        return True

    def _rememberConditionsFixedBy(self, inputOmic, nConditions):
        """Record the omic whose values file set the job-wide condition count."""
        self._conditionsFixedBy = {
            "omicName": inputOmic.get("omicName") or "the first omic",
            "file": inputOmic.get("inputDataFile") or "",
            "nConditions": nConditions,
        }

    @staticmethod
    def _conditionCount(nColumns):
        """'1 condition column' / '14 condition columns' from a line width."""
        conditions = max(0, int(nColumns) - 1)
        return "%d condition column%s" % (conditions, "" if conditions == 1 else "s")

    def _conditionsDisagreement(self, inputOmic, fileWidth):
        """The one sentence a uniformly-wider (or narrower) omic is refused with.

        PaintOmics paints every omic on ONE set of conditions, so validateInput
        fixes nConditions from the first values file it reads and every other
        omic must match it. A job whose omics are individually well-formed but
        of different widths -- one fold-change column beside twelve samples and
        two means, the shape of the 2026-08-27 guest report -- used to be
        refused with ten copies of "Line N: Expected 2 columns but found 15",
        which names neither the rule nor the omic the 2 came from. The reader
        was left to guess which file was "wrong", when neither is: they
        disagree with each other.

        Returns None when the disagreement is not with ANOTHER omic (the width
        was fixed by this very file, so the file is ragged and the per-line
        report is the right one), or when nothing fixed it at all.
        """
        fixedBy = getattr(self, "_conditionsFixedBy", None)
        if not fixedBy or fixedBy.get("file") == inputOmic.get("inputDataFile"):
            return None
        return ("Every omic in one run must have the same number of conditions: "
                "[b]" + str(fixedBy["omicName"]) + "[/b] (" + str(fixedBy["file"]) + ") has "
                + self._conditionCount(fixedBy["nConditions"]) + ", but [b]"
                + str(inputOmic.get("omicName") or "this omic") + "[/b] ("
                + str(inputOmic.get("inputDataFile") or "") + ") has "
                + self._conditionCount(fileWidth) + ". Bring them to the same "
                "conditions -- for instance one fold change per contrast in both -- "
                "or run them as separate analyses.\n")

    def validateFile(self, inputOmic, nConditions, error):
        """
        This function...

        @param {type}
        @returns
        """
        valuesFileName = inputOmic.get("inputDataFile")
        relevantFileName = inputOmic.get("relevantFeaturesFile", "")
        associationsFileName = inputOmic.get("associationsFile", "")
        relevantAssociationsFileName = inputOmic.get("relevantAssociationsFile", "")
        omicName = inputOmic.get("omicName")

        if inputOmic.get("isExample", False):
            return nConditions, error
        else:
            valuesFileName = "{path}/{file}".format(path=self.getInputDir(), file=valuesFileName)
            relevantFileName = "{path}/{file}".format(path=self.getInputDir(), file=relevantFileName)
            associationsFileName = "{path}/{file}".format(path=self.getInputDir(), file=associationsFileName)
            relevantAssociationsFileName = "{path}/{file}".format(path=self.getInputDir(),
                                                                  file=relevantAssociationsFileName)

        # *************************************************************************
        # STEP 1. VALIDATE THE ASSOCIATIONS AND RELEVANT ASSOCIATIONS FILES
        # *************************************************************************
        logging.info("VALIDATING ASSOCIATION FILE (" + omicName + ")...")
        if os_path.isfile(associationsFileName):
            encodingError = ensure_utf8(associationsFileName)
            if encodingError is not None:
                error += " - Errors detected while processing " + \
                         inputOmic.get("associationsFile", "") + ": " + encodingError + ".\n"
                return nConditions, error
            nLine = -1
            assoc_delimiter = Job.detect_delimiter(associationsFileName)
            with open(associationsFileName, 'r', encoding='utf-8-sig', newline='') as associationDataFile:
                for line in csv_reader(associationDataFile, delimiter=assoc_delimiter):
                    nLine = nLine + 1

                    if nLine > MAX_NUMBER_FEATURES:
                        error += " - Errors detected while processing " + inputOmic.get("associationsFile", "") + \
                                 ": The file exceeds the maximum number of features allowed (" + str(
                            MAX_NUMBER_FEATURES) + ")." + "\n"
                        break

                    if len(line) != 2:
                        error += " - Errors detected while processing " + inputOmic.get("associationsFile",
                                                                                        "") + ": The file does not look like an associations file (some lines do not have 2 columns)." + "\n"
                        break

        logging.info("VALIDATING RELEVANT ASSOCIATION FILE (" + omicName + ")...")
        if os_path.isfile(relevantAssociationsFileName):
            # This was the one call site that dropped the reason: an unreadable
            # relevant-associations file then crashed two lines later inside
            # detect_delimiter instead of becoming a validation message.
            encodingError = ensure_utf8(relevantAssociationsFileName)
            if encodingError is not None:
                error += " - Errors detected while processing " + \
                         inputOmic.get("relevantAssociationsFile", "") + ": " + encodingError + ".\n"
                return nConditions, error
            nLine = -1
            rel_assoc_delimiter = Job.detect_delimiter(relevantAssociationsFileName)
            with open(relevantAssociationsFileName, 'r', encoding='utf-8-sig', newline='') as relevantAssociationDataFile:
                for line in csv_reader(relevantAssociationDataFile, delimiter=rel_assoc_delimiter):
                    nLine = nLine + 1

                    if nLine > MAX_NUMBER_FEATURES:
                        error += " - Errors detected while processing " + inputOmic.get("relevantAssociationsFile",
                                                                                        "") + \
                                 ": The file exceeds the maximum number of features allowed (" + str(
                            MAX_NUMBER_FEATURES) + ")." + "\n"
                        break

                    if len(line) != 2 and len(line) != 1:
                        error += " - Errors detected while processing " + inputOmic.get("relevantAssociationsFile",
                                                                                        "") + ": The file does not look like a relevant associations file (expected 1 or 2 columns)." + "\n"
                        break

        # *************************************************************************
        # STEP 1. VALIDATE THE RELEVANT FEATURES FILE
        # *************************************************************************
        logging.info("VALIDATING RELEVANT FEATURES FILE (" + omicName + ")...")
        if os_path.isfile(relevantFileName):
            encodingError = ensure_utf8(relevantFileName)
            if encodingError is not None:
                error += " - Errors detected while processing " + \
                         inputOmic.get("relevantFeaturesFile", "") + ": " + encodingError + ".\n"
                return nConditions, error
            f = open(relevantFileName, 'r', encoding='utf-8-sig')
            lines = f.readlines()

            # Ensure that relevant features files does not exceed the max number of features
            if len(lines) > MAX_NUMBER_FEATURES:
                error += " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile",
                                                                                "") + ": The file exceeds the maximum number of features allowed (" + str(
                    MAX_NUMBER_FEATURES) + ")." + "\n"
            else:
                # Check column count if multi-condition
                if len(lines) > 0:
                    rf_delimiter = Job.detect_delimiter(relevantFileName)
                    first_line = lines[0].strip().split(rf_delimiter)
                    rf_conditions = len(first_line)
                    
                    # If nConditions is already set (from a previous values file or previous RF file), check for match
                    if nConditions != -1:
                        # nConditions is the number of columns in the DATA file (ID + Conditions)
                        # rf_conditions is the number of columns in the RF file (Conditions only)
                        # So we expect nConditions == rf_conditions + 1
                        if rf_conditions > 1 and rf_conditions != (nConditions - 1):
                             # A 2-col file is the legacy [TARGET, REGULATOR] pair-list that
                             # MiRNA2GenesServlet emits for the Regulatory Omics workflow,
                             # regardless of how many conditions the values file declares.
                             # parseSignificativeFeaturesFile (Job.py:740) detects this shape
                             # via its isLegacyTwoCol branch and produces GENE:::REGULATOR keys.
                             if rf_conditions != 2:
                                 error += " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile", "") + \
                                          ": The number of columns (" + str(rf_conditions) + ") does not match the number of conditions in the data file (" + str(nConditions - 1) + ").\n"

                # Guard against a file that is not a list of identifiers at all.
                #
                # This used to reject any line over 80 characters, which was
                # written when a relevant-features file had one column and a
                # line was a single ID. A multi-condition file has one column
                # per condition, so with Ensembl IDs (18 characters) it exceeds
                # 80 at five conditions and is impossible to satisfy at six --
                # the column-count check above demands exactly N columns while
                # this demanded a width only a shorter file could have. The two
                # rules could not both be met, so multi-condition relevant files
                # were unusable at realistic condition counts.
                #
                # The intent is "every cell should look like an identifier", so
                # it is now applied per field. Uploading the wrong file is still
                # caught: a values file has one more column than the conditions
                # it declares, which the check above rejects.
                rfDelimiter = Job.detect_delimiter(relevantFileName)
                for line in lines:
                    if any(len(field) > 80 for field in line.strip().split(rfDelimiter)):
                        error += " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile",
                                                                                        "") + ": The file does not look like a Relevant Features file (some identifiers are longer than 80 characters)." + "\n"
                        break
            f.close()

        # *************************************************************************
        # STEP 2. VALIDATE THE VALUES FILE
        # *************************************************************************
        logging.info("VALIDATING VALUES FILE (" + omicName + ")...")

        # IF THE USER UPLOADED VALUES FOR GENE EXPRESSION
        if os_path.isfile(valuesFileName):
            encodingError = ensure_utf8(valuesFileName)
            if encodingError is not None:
                error += " - Errors detected while processing " + \
                         inputOmic.get("inputDataFile", "") + ": " + encodingError + ".\n"
                return nConditions, error

            values_delimiter = Job.detect_delimiter(valuesFileName)
            with open(valuesFileName, newline='', encoding='utf-8-sig' ) as inputDataFile:
                nLine = -1
                erroneousLines = {}
                # The width faults on their own, and every width this file's
                # data lines had, so that a file that is uniformly a different
                # width from the job can be told apart from a ragged one.
                widthFaults = {}
                widthsSeen = set()
                for line in csv_reader(inputDataFile, delimiter=values_delimiter):
                    nLine = nLine + 1
                    # TODO: HACER ALGO CON EL HEADER?
                    # *************************************************************************
                    # STEP 2.1 CHECK IF IT IS HEADER, IF SO, IGNORE LINE
                    # *************************************************************************
                    if nLine == 0:
                        try:
                            float(line[1])
                        except Exception:
                            continue

                    if nConditions == -1:
                        if len(line) < 2:
                            erroneousLines[nLine] = "Expected at least 2 columns, but found one."
                            break
                        nConditions = len(line)
                        self._rememberConditionsFixedBy(inputOmic, nConditions)

                    # *************************************************************************
                    # STEP 2.2 CHECK IF IT EXCEEDS THE MAX NUMBER OF FEATURES ALLOWED
                    # *************************************************************************
                    if nLine > MAX_NUMBER_FEATURES:
                        error += " - Errors detected while processing " + inputOmic.get("inputDataFile", "") + \
                                 ": The file exceeds the maximum number of features allowed (" + str(
                            MAX_NUMBER_FEATURES) + ")." + "\n"
                        break

                    # **************************************************************************************
                    # STEP 2.3 IF LINE LENGTH DOES NOT MATCH WITH EXPECTED NUMBER OF CONDITIONS, ADD ERROR
                    # **************************************************************************************
                    if len(line) > 0:
                        widthsSeen.add(len(line))
                    if nConditions != len(line) and len(line) > 0:
                        widthFaults[nLine] = "Expected " + str(nConditions) + " columns but found " + str(
                            len(line)) + ";"
                        erroneousLines[nLine] = widthFaults[nLine]

                    # **************************************************************************************
                    # STEP 2.4 IF CONTAINS NOT VALID VALUES, ADD ERROR
                    # **************************************************************************************
                    try:
                        list(map(float, line[1:len(line)]))
                    except:
                        if " ".join(line[1:len(line)]).count(",") > 0:
                            erroneousLines[nLine] = erroneousLines.get(nLine,
                                                                       "") + "Perhaps you are using commas instead of dots as decimal mark?"
                        else:
                            erroneousLines[nLine] = erroneousLines.get(nLine,
                                                                       "") + "Line contains invalid values or symbols."

                    if len(erroneousLines) > 9:
                        break

            inputDataFile.close()

            # A file whose every line (of the ten read before the cap) is the
            # SAME width, and that width is not the job's, is not a broken
            # file: it is a well-formed omic that disagrees with another one.
            # Say that once, by name, instead of ten times by line number. A
            # file whose widths vary is ragged and keeps the per-line report;
            # so does a value fault on any of those lines.
            if widthFaults and len(widthsSeen) == 1:
                disagreement = self._conditionsDisagreement(inputOmic, next(iter(widthsSeen)))
                if disagreement is not None:
                    for k, fault in widthFaults.items():
                        rest = erroneousLines.get(k, "").replace(fault, "", 1)
                        if rest.strip():
                            erroneousLines[k] = rest
                        else:
                            erroneousLines.pop(k, None)
                    error += disagreement

            # *************************************************************************
            # STEP 3. CHECK THE ERRORS AND RETURN
            # *************************************************************************
            if len(erroneousLines) > 0:
                error += "Errors detected while processing " + inputOmic.get("inputDataFile") + ":\n"
                error += "[ul]"
                for k in sorted(erroneousLines.keys()):
                    error += "[li]Line " + str(k) + ":" + erroneousLines.get(k) + "[/li]"
                error += "[/ul]"

                if len(erroneousLines) > 9:
                    error += "Too many errors detected while processing " + inputOmic.get(
                        "inputDataFile") + ", skipping remaining lines...\n"
            elif nLine < 1:
                error += "The file " + inputOmic.get(
                    "inputDataFile") + " <b>does not seem to have any feature lines</b>. Maybe the association process returned empty files, check the files and configuration options just in case."

        else:
            error += " - Error while processing " + omicName + ": File " + inputOmic.get(
                "inputDataFile") + "not found.\n"

        return nConditions, error

    def _detectReplicatesForOmic(self, omicName, omicHeader):
        """
        Run replicate detection on a single omic's column labels.

        ``omicHeader`` is the raw header captured by the parsers — list[str]
        where index 0 is the ID column and indices 1..n are sample columns.
        We pass only the sample slice to ``detect_replicates``; an absent or
        malformed header degrades safely to ``status="none"`` so the rest of
        the pipeline (which only reads the dict shape) keeps working.
        """
        replicateHeader = omicHeader[1:] if omicHeader and len(omicHeader) > 1 else []
        result = detect_replicates(replicateHeader)
        logging.info(
            "REPLICATE DETECTION (%s): status=%s, samples=%d, unmatched=%d",
            omicName, result["status"], len(result["sampleHeader"]), len(result["unmatched"])
        )
        return result

    def _designFileCandidatesFor(self, inputOmic):
        """Design files in this job's input dir that might describe ``inputOmic``.

        MORE writes ``MORE_design_<date>.tab`` next to the ``MORE_output_<omic>_
        <date>.tab`` it hands to step 1, so the omic's own filename names its
        design exactly. The input dir is per USER rather than per job
        (``Job.setDirectories``), so it also accumulates designs from earlier
        runs -- the dated match is tried first and the rest, newest first, only
        as a fallback. A stale one is not a silent hazard: ``parse_design``
        rejects any design that does not cover every column of this omic.
        """
        inputDir = self.getInputDir()
        if not inputDir or not os.path.isdir(inputDir):
            return []

        candidates = []
        # The omic's OWN design first: uploaded beside its values file in
        # step 1, or declared by an example scenario (absolute path). It
        # states the grouping for exactly this omic, so nothing else is
        # tried before it.
        own = inputOmic.get("designFile")
        if own:
            path = own if os.path.isabs(own) else os.path.join(inputDir, own)
            if os.path.exists(path):
                candidates.append(path)
        dataFile = os.path.basename(inputOmic.get("inputDataFile") or "")
        match = re.match(r"^MORE_output_.*_(\d+)\.tab$", dataFile)
        if match:
            dated = os.path.join(inputDir, "MORE_design_%s.tab" % match.group(1))
            if os.path.exists(dated):
                candidates.append(dated)

        others = glob.glob(os.path.join(inputDir, "MORE_design_*.tab"))
        others.sort(key=os.path.getmtime, reverse=True)
        candidates.extend(path for path in others if path not in candidates)
        return candidates

    def _applyDesignGroupingForOmic(self, inputOmic):
        """Collapse one omic's columns using a design file. True when applied.

        Stores every grouping the design supports on the omic
        (``columnGroupings``) so the interface can offer the coarser ones --
        for a ``Ctr_0H … Ik_24H`` design that is the 12 conditions, the 2
        treatment levels and the 6 timepoints -- and applies the design itself
        as the default, which is the one that is always meaningful.

        Never raises. A design that is absent, unreadable, or does not cover
        this omic's columns leaves the omic exactly as it was.
        """
        omicHeader = inputOmic.get("omicHeader") or []
        replicateHeader = omicHeader[1:] if len(omicHeader) > 1 else []
        if len(replicateHeader) < 2:
            return False

        for path in self._designFileCandidatesFor(inputOmic):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    body = handle.read()
                sampleHeader, mapping, groups = parse_design(body, replicateHeader)
            except Exception as ex:
                logging.info("DESIGN GROUPING (%s): %s did not apply (%s).",
                             inputOmic.get("omicName"), os.path.basename(path), str(ex))
                continue

            if len(sampleHeader) >= len(replicateHeader):
                # A design that groups nothing is not worth applying: it would
                # pin the job into "samples" mode for an identical picture.
                continue

            try:
                inputOmic["columnGroupings"] = derive_groupings(
                    replicateHeader, sampleHeader, mapping)
                res = self.applyReplicateMappingForOmic(
                    inputOmic["omicName"], "manual",
                    sampleHeader=sampleHeader, mapping=mapping, groups=groups)
                logging.info(
                    "DESIGN GROUPING (%s): %s collapsed %d column(s) to %d condition(s), "
                    "%d feature(s) updated.",
                    inputOmic["omicName"], os.path.basename(path), len(replicateHeader),
                    len(res["sampleHeader"]), res["featuresUpdated"])
                return True
            except Exception as ex:
                logging.warning(
                    "DESIGN GROUPING (%s) failed to apply %s: %s — continuing without it.",
                    inputOmic.get("omicName"), os.path.basename(path), str(ex))
                return False

        return False

    def _findInputOmicByName(self, omicName):
        """
        Locate an inputOmic + its feature dict + feature type by omic name.

        Returns ``(inputOmic, featureDict, featureType)`` or ``(None, None, None)``.
        Used by both the auto-apply path (this file) and the servlet apply
        endpoint (PathwayAcquisitionServlet) — kept on the job so the two
        callers share a single source of truth for the lookup convention.
        """
        for inputOmic in self.getGeneBasedInputOmics():
            if inputOmic.get("omicName") == omicName:
                return inputOmic, self.getInputGenesData(), "Gene"
        for inputOmic in self.getCompoundBasedInputOmics():
            if inputOmic.get("omicName") == omicName:
                return inputOmic, self.getInputCompoundsData(), "Compound"
        return None, None, None

    def applyReplicateMappingForOmic(self, omicName, mode, sampleHeader=None,
                                     mapping=None, groups=None):
        """
        Apply (or clear) a replicate→sample mapping for one omic.

        This is the single source of truth for the aggregation step: invoked
        both by the auto-apply path inside ``processFilesContent`` (mode=auto,
        no extra args) and by the servlet ``/pa_apply_replicate_mapping``
        endpoint (mode auto/manual/off). For ``mode="manual"`` the caller is
        responsible for parsing the design file and supplying ``sampleHeader``,
        ``mapping`` and ``groups``.

        Mutates:
        - ``inputOmic`` dict: writes ``replicateSource``, ``sampleHeader``,
          ``replicateMapping``.
        - Each affected ``OmicValue``: writes ``sampleValues`` /
          ``sampleRelevant`` (or clears them when mode="off").

        Returns ``{omicName, status, mode, sampleHeader, mapping,
        featuresUpdated, featureType}`` for the caller to persist / serialize.
        """
        inputOmic, featureDict, featureType = self._findInputOmicByName(omicName)
        if inputOmic is None:
            raise ValueError("Omic '%s' not found in this job." % omicName)

        if mode == "off":
            inputOmic["replicateSource"]   = "off"
            inputOmic["sampleHeader"]      = []
            inputOmic["replicateMapping"]  = []
            n_touched = self._walkAndAggregateOmicValues(
                featureDict, omicName, mapping=[], groups=[], n_samples=0, clear=True
            )
            return {
                "omicName":         omicName,
                "status":           "cleared",
                "mode":             mode,
                "sampleHeader":     [],
                "mapping":          [],
                "featuresUpdated":  n_touched,
                "featureType":      featureType,
            }

        if mode == "auto":
            detection = inputOmic.get("replicateDetection") or {}
            if detection.get("status") != "complete":
                raise ValueError(
                    "Auto-apply not possible for omic '%s' (detection status=%s)."
                    % (omicName, detection.get("status"))
                )
            sampleHeader = detection["sampleHeader"]
            mapping      = detection["mapping"]
            groups       = detection["groups"]
        elif mode == "manual":
            if not (sampleHeader and mapping is not None and groups is not None):
                raise ValueError("Manual apply requires sampleHeader, mapping and groups.")
        else:
            raise ValueError("Invalid mode '%s'." % mode)

        inputOmic["replicateSource"]   = mode
        inputOmic["sampleHeader"]      = sampleHeader
        inputOmic["replicateMapping"]  = mapping

        n_touched = self._walkAndAggregateOmicValues(
            featureDict, omicName,
            mapping=mapping, groups=groups, n_samples=len(sampleHeader),
            clear=False,
        )
        return {
            "omicName":         omicName,
            "status":           "applied",
            "mode":             mode,
            "sampleHeader":     sampleHeader,
            "mapping":          mapping,
            "featuresUpdated":  n_touched,
            "featureType":      featureType,
        }

    def _walkAndAggregateOmicValues(self, featureDict, omicName, mapping, groups,
                                    n_samples, clear):
        """
        Walk every Feature, find its OmicValue for ``omicName``, and either
        compute / clear ``sampleValues`` / ``sampleRelevant``. Returns the
        number of OmicValues touched.
        """
        n_touched = 0
        for feature in featureDict.values():
            for ov in feature.getOmicsValues():
                if ov.getOmicName() != omicName:
                    continue
                if clear:
                    ov.setSampleValues(None)
                    ov.setSampleRelevant(None)
                else:
                    sampleValues, sampleRelevant = aggregate_replicates(
                        values=ov.getValues() or [],
                        relevant=ov.relevant,
                        groups=groups,
                        n_samples=n_samples,
                    )
                    ov.setSampleValues(sampleValues)
                    ov.setSampleRelevant(sampleRelevant)
                n_touched += 1
        return n_touched

    def _progressWeightsByOmic(self):
        """Relative cost of each input omic, for the step-1 progress bar.

        Uses the data file's size on disk. os.path.getsize() is a stat() call, so
        this is free even for very large uploads, and it degrades safely: a file
        that cannot be stat'd (missing, permissions) contributes a nominal weight
        rather than raising, because progress reporting must never fail a job.
        Omics are keyed by id() since the dicts themselves are not hashable and
        omic names are not guaranteed unique.

        @returns {Dict} {"sizes": {id(omic): bytes}, "total": int, "count": int}
        """
        sizes = {}
        for inputOmic in (self.geneBasedInputOmics + self.compoundBasedInputOmics):
            fileName = inputOmic.get("inputDataFile") or ""
            if not inputOmic.get("isExample", False) and fileName:
                fileName = "{path}/{file}".format(path=self.getInputDir(), file=fileName)
            try:
                # Floor of 1 so an empty or unreadable file still advances the bar
                # at its boundary instead of contributing nothing.
                sizes[id(inputOmic)] = max(1, os_path.getsize(fileName))
            except OSError:
                sizes[id(inputOmic)] = 1

        return {"sizes": sizes, "total": sum(sizes.values()) or 1, "count": len(sizes)}

    def processFilesContent(self):
        """
        This function processes all the files and returns a checkboxes list to show to the user

        @returns list of matched Metabolites
        """
        if not os_path.exists(self.getTemporalDir()):
            os_makedirs(self.getTemporalDir())

        omicSummary = None

        logging.info("CREATING THE TEMPORAL CACHE FOR JOB " + self.getJobID() + "...")
        KeggInformationManager().createTranslationCache(self.getJobID())

        # Progress inside this phase is weighted by input file SIZE rather than by
        # omic count, because omics differ ~7x in cost: on the bundled example
        # DNase-seq (10,274 rows) takes 31.9s while Proteomics (1,110 rows) takes
        # 4.9s. Per-row cost is stable across omics (2.5-4.5 ms), so bytes are a
        # good proxy — and os.path.getsize() is O(1), where counting rows would
        # mean reading every file twice.
        omicWork = self._progressWeightsByOmic()
        JobProgress.units(self.getJobID(), 0, total=omicWork["total"],
                          detail="0 of %d omics" % omicWork["count"])
        doneWork = 0

        try:
            logging.info("PROCESSING GENE BASED FILES...")
            for inputOmic in self.geneBasedInputOmics:
                # span = this omic's own weight, so the mapper children's anchors
                # interpolate inside it. Without it the bar would step once per
                # omic — five jumps across an 80s phase.
                JobProgress.units(self.getJobID(), doneWork,
                                  span=omicWork["sizes"].get(id(inputOmic), 0),
                                  detail="mapping " + str(inputOmic.get("omicName", "")))
                [omicName, omicSummary, omicHeader] = self.parseGeneBasedFiles(inputOmic)
                doneWork += omicWork["sizes"].get(id(inputOmic), 0)
                JobProgress.units(self.getJobID(), doneWork)
                logging.info("   * PROCESSED " + omicName + "...")
                inputOmic["omicSummary"] = omicSummary
                inputOmic["omicHeader"] = omicHeader
                # Replicate detection runs once per omic on the column labels
                # (omicHeader[1:] — index column stripped). Result is surfaced
                # to the Step-2 UI so the user can confirm/override; no
                # aggregation happens here, the values stay per-replicate.
                inputOmic["replicateDetection"] = self._detectReplicatesForOmic(omicName, omicHeader)
            logging.info("PROCESSING GENE BASED FILES...DONE")

            logging.info("PROCESSING COMPOUND BASED FILES...")
            checkBoxesData = []
            for inputOmic in self.compoundBasedInputOmics:
                JobProgress.units(self.getJobID(), doneWork,
                                  span=omicWork["sizes"].get(id(inputOmic), 0),
                                  detail="mapping " + str(inputOmic.get("omicName", "")))
                [omicName, checkBoxesData, omicSummary, omicHeader] = self.parseCompoundBasedFile(inputOmic,
                                                                                                  checkBoxesData)
                doneWork += omicWork["sizes"].get(id(inputOmic), 0)
                JobProgress.units(self.getJobID(), doneWork)
                logging.info("   * PROCESSED " + omicName + "...")
                inputOmic["omicSummary"] = omicSummary
                inputOmic["omicHeader"] = omicHeader
                inputOmic["replicateDetection"] = self._detectReplicatesForOmic(omicName, omicHeader)
            # REMOVE REPETITIONS AND ORDER ALPHABETICALLY
            checkBoxesData = unifyAndSort(checkBoxesData, lambda checkBoxData: checkBoxData.getTitle().lower())

            logging.info("PROCESSING COMPOUND BASED FILES...DONE")

            # AUTO-APPLY complete replicate detections so users get the
            # collapsed view without an extra click in Step 2. The Step-2 panel
            # still surfaces the detection (and the user can switch back to
            # "Show all replicates" or upload a custom design), but the default
            # is the average — most jobs with `_R1/_R2`-style headers want this.
            for inputOmic in (self.geneBasedInputOmics + self.compoundBasedInputOmics):
                detection = inputOmic.get("replicateDetection") or {}

                # A design file beats the name-based detector whenever one is
                # present: it STATES the grouping instead of inferring it. This
                # is the only route for a MORE run, whose columns carry their
                # replicate tag as a prefix (`Batch_1_Ctr_0H`) that the suffix
                # regex cannot see -- detection comes back "none" and the
                # heatmaps draw every replicate.
                if self._applyDesignGroupingForOmic(inputOmic):
                    continue

                if detection.get("status") != "complete":
                    continue
                try:
                    res = self.applyReplicateMappingForOmic(inputOmic["omicName"], "auto")
                    logging.info(
                        "REPLICATE AUTO-APPLY (%s): %d sample(s), %d feature(s) updated.",
                        inputOmic["omicName"], len(res["sampleHeader"]), res["featuresUpdated"]
                    )
                except Exception as ex:
                    # Auto-apply must never break Step-1: log and continue, the
                    # user can still pick a mode in the Step-2 panel.
                    logging.warning(
                        "REPLICATE AUTO-APPLY (%s) failed: %s — continuing without aggregation.",
                        inputOmic.get("omicName"), str(ex)
                    )

            # GENERATE THE COMPRESSED FILE WITH MATCHING, COPY THE FILE AT RESULTS DIR AND CLEAN TEMPORAL FILES
            # COMPRESS THE RESULTING FILES AND CLEAN TEMPORAL DATA
            # TODO: MOVE THIS CODE TO JOBINFORMATIONMANAGER
            logging.info("COMPRESSING RESULTS...")
            fileName = "mapping_results_" + self.getJobID()
            logging.info("OUTPUT FILES IS " + self.getOutputDir() + fileName)
            logging.info("TEMPORAL DIR IS " + self.getTemporalDir() + "/")

            self.compressDirectory(self.getOutputDir() + fileName, "zip", self.getTemporalDir() + "/")

            logging.info("COMPRESSING RESULTS...DONE")

            # Save the metabolites matching data to allow recovering the job
            self.foundCompounds = checkBoxesData

            return checkBoxesData

        except Exception as ex:
            raise ex
        finally:
            logging.info("REMOVING THE TEMPORAL CACHE FOR JOB " + self.getJobID() + "...")
            KeggInformationManager().clearTranslationCache(self.getJobID())

    # GENERATE PATHWAYS LIST FUNCTIONS -----------------------------------------------------------------------------------------
    def updateSubmitedCompoundsList(self, selectedCompounds):
        """
        This function is used to generate the final list of selected compounds

        @param selectedCompounds, list of selected compounds in format originalName#comopundCode
        """

        # 1. GET THE PREVIOUS COMPOUND TABLE
        initialCompounds = self.getInputCompoundsData()

        # 2. CLEAN THE COMPOUNDS TABLE FOR THE JOB INSTANCE
        self.setInputCompoundsData({})

        # 3. FOR EACH SELECTED COMPOUND
        #   The input includes the ID for the selected compound followed by the name
        #   this is important because some compounds could appear in several boxes with different name (but same ID)
        #   and we need to distinguish which one the user selected
        #   e.g. C00075#Uridine 5'-triphosphate, Uridine triphosphate
        #   e.g. C00075#UTP
        compoundID = compoundName = initialCompound = newCompound = None
        malformedSelections = []
        for selectedCompound in selectedCompounds:
            # All three parts are required: one compound ID can appear under
            # several names, so (name, originalName) is what says which box the
            # user actually ticked.
            #
            # Indexing [1] and [2] unconditionally meant a single malformed
            # entry aborted the whole of step 2 with
            #     IndexError: list index out of range
            # throwing away an analysis that had already cost minutes of
            # enrichment, and naming neither the offending entry nor the field.
            # An unparseable entry identifies no compound -- the same situation
            # as an ID that is not in this job, which is skipped a few lines
            # below -- so it is skipped the same way and reported together.
            parts = selectedCompound.split("#")
            if len(parts) < 3:
                malformedSelections.append(selectedCompound)
                continue

            compoundID = parts[0]
            compoundName = parts[1]
            originalName = parts[2]
            initialCompound = initialCompounds.get(compoundID)
            newCompound = self.getInputCompoundsData().get(compoundID, None)

            if initialCompound is None:
                continue
            # If there is not any entry for the current compound yet, add a new empty compound
            if newCompound is None:
                newCompound = initialCompound.clone()
                newCompound.setOmicsValues([])  # Clean the entry
                self.addInputCompoundData(newCompound)

            # TODO: this could ignore multiple values of different omics types for the same feature
            for i in sorted(range(len(initialCompound.omicsValues)), reverse=True):
                omicValue = initialCompound.omicsValues[i]

                if omicValue.inputName in compoundName.split(
                        ", ") and omicValue.originalName.lower() == originalName.lower():  # Some compounds can have combined names, separated by commas
                    newCompound.addOmicValue(omicValue)
                    del initialCompound.omicsValues[i]

        if malformedSelections:
            # Logged rather than raised: the analysis is still valid for every
            # entry that did parse, and losing it wholesale is the failure this
            # replaced. The entries are listed so a misbehaving client can be
            # identified from the log.
            logging.warning(
                "STEP2 - IGNORED %d MALFORMED COMPOUND SELECTION(S); EXPECTED "
                "'ID#name#originalName': %s" % (
                    len(malformedSelections), ", ".join(
                        repr(entry) for entry in malformedSelections[:10])))

        # A compound is added to the table empty and filled only when one of its
        # omic values matches the selection:
        #
        #   newCompound.setOmicsValues([])                      # added empty
        #   if omicValue.inputName in compoundName.split(", ") \
        #      and omicValue.originalName.lower() == originalName.lower():
        #       newCompound.addOmicValue(omicValue)             # only then
        #
        # so a selection whose name does not match leaves the compound in the
        # table carrying nothing. Nineteen places then read `omicsValues[0]`,
        # and the first of them ends step 2 with
        #     IndexError: list index out of range
        # after enrichment has already been computed -- the same expensive loss
        # the malformed-selection branch above exists to prevent. Reproduced
        # with one selection, "C00075#SomeOtherName#UTP" against a C00075 whose
        # value is named UTP: the compound survives with omicsValues == [] and
        # getGlobalExpressionData raises.
        #
        # Two of those nineteen readers already guard (`if feature.omicsValues`,
        # `if comp and comp.omicsValues`), so this has been met before and
        # patched where it surfaced rather than where it originates.
        #
        # Dropped after the loop rather than inside it. One compound ID
        # legitimately appears in several selections under different names --
        # the comment at the top of this function gives C00075 as exactly that
        # case -- so "carries no value" is only meaningful once every selection
        # has been seen. Pruning inside the loop happens to reach the same
        # answer, because a compound removed on a non-matching pass is cloned
        # again from initialCompound on the next one; this is checked, not
        # assumed. Doing it once over the final state simply does not depend on
        # that recovery.
        #
        # Dropping rather than keeping, because a compound with no omic value
        # carries no measurement: it cannot be drawn, scored or summarised, and
        # every consumer here assumes at least one value.
        emptyCompounds = [compoundID for compoundID, compound
                          in self.getInputCompoundsData().items()
                          if not compound.omicsValues]
        if emptyCompounds:
            logging.warning(
                "STEP2 - DROPPED %d SELECTED COMPOUND(S) THAT MATCHED NO OMIC "
                "VALUE: %s" % (len(emptyCompounds), ", ".join(emptyCompounds[:10])))
            for compoundID in emptyCompounds:
                del self.getInputCompoundsData()[compoundID]

        # Update the omicSummary for the compoundOmic
        #
        # "mapped" here means: how many of the input metabolites of this omic
        # still carry a measurement after the user's selection. It is counted
        # from the final table, once, rather than accumulated during the
        # selection loop, because only the final table is what the rest of the
        # job (and the browser) sees.
        #
        # The count used to be taken inside the loop above, on every omic value
        # of every selected compound and *before* the name-match test that
        # decides whether the value is kept. That counted on a different scale
        # from the summary it then overwrote:
        #
        #   * values belonging to boxes the user did not tick were counted;
        #   * originalName is stored lower-cased for a main compound and in the
        #     input's own case for an "other" compound (see
        #     FeatureNamesToKeggIDsMapper.mapCompoundsIdentifiers), so the same
        #     metabolite could enter the set twice.
        #
        # Measured on the six-omic STATegra example (58 input metabolites, 51 of
        # them matched): the old code reported mapped=62, unmapped=-4, and
        # getMappedRatios turned that into 62/58 = 1.069 -- which
        # generatePathwaysList passes straight into the Stouffer/Fisher weights,
        # over-weighting metabolomics in every combined pathway p-value, and
        # which PathwayAcquisitionServlet ships to the browser as
        # "62 mapped of 58".
        #
        # Names are normalised (stripped, lower-cased) so the two casings of one
        # metabolite count once, and attributed per omic through
        # omicValue.getOmicName() so two metabolomics files no longer share a
        # single count. With a single compound omic -- the case the old TODO
        # assumed -- the union is used, which also covers omic values whose
        # omicName was never set.
        mappedNamesByOmic = defaultdict(set)
        for finalCompound in self.getInputCompoundsData().values():
            for omicValue in finalCompound.getOmicsValues():
                featureName = (omicValue.getOriginalName() or omicValue.getInputName() or "")
                featureName = featureName.strip().lower()
                if featureName:
                    mappedNamesByOmic[omicValue.getOmicName()].add(featureName)

        compoundOmics = self.getCompoundBasedInputOmics()
        allMappedNames = set()
        for names in mappedNamesByOmic.values():
            allMappedNames |= names

        for cpdOmic in compoundOmics:
            # Get the original number of CPDs
            cpdSummary = cpdOmic.get("omicSummary")

            # [mapped, unmapped, ...distribution]; both counts must be plain
            # integers or this omic was not summarised by parseCompoundBasedFile
            # and there is nothing meaningful to rewrite.
            if not isinstance(cpdSummary, list) or len(cpdSummary) < 2 or \
                    not isinstance(cpdSummary[0], int) or not isinstance(cpdSummary[1], int):
                logging.warning(
                    "STEP2 - SKIPPING SUMMARY UPDATE FOR COMPOUND OMIC '%s': "
                    "UNEXPECTED omicSummary %r" % (cpdOmic.get("omicName"), cpdSummary))
                continue

            # mapped + unmapped is the number of input features of this omic and
            # does not change with the selection, so the rewrite is idempotent.
            cpdTotal = max(cpdSummary[0] + cpdSummary[1], 0)

            mapped = len(allMappedNames) if len(compoundOmics) == 1 else \
                len(mappedNamesByOmic.get(cpdOmic.get("omicName"), set()))

            if mapped > cpdTotal:
                # Cannot happen with the counting above -- every name counted
                # came from a feature of this omic's input file -- so if it ever
                # does, the invariant is restored and the discrepancy is loud
                # rather than shipped as a negative "unmapped".
                logging.warning(
                    "STEP2 - COMPOUND OMIC '%s' COUNTED %d MAPPED FEATURES OF %d INPUT "
                    "FEATURES; CLAMPING" % (cpdOmic.get("omicName"), mapped, cpdTotal))
                mapped = cpdTotal

            # Change the summary stats to reflect the user provided options
            cpdSummary[0] = mapped
            cpdSummary[1] = cpdTotal - mapped

        return True

    def filterPathwaysBySelectedDatabases(self, pathwaysList):
        """Keep only the pathways coming from a database this job selected.

        The organism collection holds every database PaintOmics knows for that
        species in one dict -- for mmu, 888 pathways = 364 KEGG + 524 Reactome.
        A job that selected only KEGG can never match the Reactome half:
        `_matchPathways` skips any pathway whose source is not in
        `totalFeaturesByOmic`, and that dictionary is keyed by the job's own
        databases. Counting them anyway made the denominator dishonest -- the
        log line and `summary[0]`, which the client shows as "N of M matched
        pathways", said 888 when only 364 were reachable -- and made every
        thread pay for feature lookups on pathways it was about to skip.

        @param {dict} pathwaysList, pathwayID -> pathway document
        @returns {dict} the same mapping restricted to the selected databases
        """
        selectedDatabases = set(self.databases or [])

        if not pathwaysList or not selectedDatabases:
            # No database recorded on the job: keep the previous behaviour
            # rather than reporting zero pathways.
            return pathwaysList

        # "KEGG" is the default source, matching getPathwaySourceByID, so a
        # pathway document written before the field existed still counts.
        filteredPathways = {
            pathwayID: pathway for pathwayID, pathway in pathwaysList.items()
            if (pathway.get("source", "KEGG") if hasattr(pathway, "get") else "KEGG")
            in selectedDatabases}

        if not filteredPathways:
            # Every pathway filtered out means the source names and the job's
            # database names disagree (a data problem, not a user one). Falling
            # back to the unfiltered list keeps the job running with the old,
            # inflated denominator instead of silently matching nothing.
            logging.warning(
                "NO PATHWAY OF %s MATCHES THE SELECTED DATABASES %s; USING ALL %d PATHWAYS" % (
                    self.getOrganism(), sorted(selectedDatabases), len(pathwaysList)))
            return pathwaysList

        if len(filteredPathways) != len(pathwaysList):
            logging.info(
                "PATHWAY UNIVERSE FOR %s RESTRICTED TO %s: %d OF %d PATHWAYS" % (
                    self.getOrganism(), "+".join(sorted(selectedDatabases)),
                    len(filteredPathways), len(pathwaysList)))

        return filteredPathways

    def generatePathwaysList(self):
        """selectedCompounds
        This function gets a list of selected compounds and the list of matched genes and
        find out all the pathways which contain at least one feature.

        @param {type}
        @returns
        """
        from multiprocessing import Process, Manager
        from math import ceil
        from time import time as _now

        # Step 2 is the slowest part of a run and its cost was being guessed at
        # rather than measured. Each stage is timed so the breakdown can be read
        # straight out of the log.
        _stageStart = _now()
        _stageTimings = []

        def _markStage(name):
            nonlocal _stageStart
            elapsed = _now() - _stageStart
            _stageTimings.append((name, elapsed))
            _stageStart = _now()

        # ****************************************************************
        # Step 1. GET THE KEGG DATA AND PREPARE VARIABLES
        # ****************************************************************
        inputGenes = list(self.getInputGenesData().values())
        inputCompounds = list(self.getInputCompoundsData().values())
        # if there is multi database make compounds available for both database
        if len(self.databases) >= 2:
            # Every selected database, not a hardcoded pair.
            #
            # This used to be `if "MapMan" ... elif "Reactome" ...`, each
            # assigning a fixed two-element list. With all three selected
            # (KEGG + MapMan + Reactome) the elif never ran, so features were
            # marked eligible for ["KEGG", "MapMan"] only and the downstream
            # `sourceDB in matchingDB` test rejected every Reactome pathway --
            # they would all report zero matched features while still being
            # counted, silently producing an empty Reactome half.
            #
            # Note this is an eligibility flag, not a per-database mapping: the
            # identifier mapping runs once per feature. The per-database loops
            # later on compute separate enrichment backgrounds, which KEGG and
            # Reactome genuinely need because their pathway universes differ.
            selectedDatabases = list(self.databases)

            for metabolite in self.inputCompoundsData:
                self.inputCompoundsData[metabolite].matchingDB = selectedDatabases

            for gene_id in self.inputGenesData:
                self.inputGenesData[gene_id].matchingDB = selectedDatabases

        else:
            # make sure the database in the inputs is the as the one in the self.databases
            for compound in inputCompounds:
                compound.matchingDB = self.databases[0]
            for gene in inputGenes:
                gene.matchingDB = self.databases[0]

        self.inputCompunds = inputCompounds

        pathwaysList = self.filterPathwaysBySelectedDatabases(
            KeggInformationManager().getAllPathwaysByOrganism(self.getOrganism()))

        enrichmentByOmic = {x.get("omicName"): x.get("enrichment", "genes") for x in
                            self.getGeneBasedInputOmics() + self.getCompoundBasedInputOmics()}

        # Retrieve all features per pathway in order to calculate the total amount
        organismGenes = defaultdict(lambda: defaultdict(set))
        organismCompounds = defaultdict(lambda: defaultdict(set))

        _markStage("setup")

        # GET THE IDS FOR ALL PATHWAYS FOR CURRENT SPECIE
        for pathwayID, pathway in pathwaysList.items():
            organismGenes[pathway["source"]][pathwayID], organismCompounds[pathway["source"]][
                pathwayID] = KeggInformationManager().getAllFeatureIDsByPathwayID(self.getOrganism(), pathwayID)

        _markStage("feature_ids_per_pathway")

        # Add new function to classify Reactome pathways based on category: PaintOmics 4
        reactomeClass = defaultdict(set)

        if 'Reactome' in self.databases:
            reactomePathways = organismGenes['Reactome'].keys()
            for pathwayID in reactomePathways:
                className = KeggInformationManager().getPathwayClassificationByID(self.organism, pathwayID).split(';')[0]
                reactomeClass[className].add(pathwayID)


            classGene = defaultdict(set)
            classComp = defaultdict(set)
            for key,pathwaySetName in enumerate(reactomeClass):
                classGene[pathwaySetName] = set()
                classComp[pathwaySetName] = set()
                for pathwayName in reactomeClass[pathwaySetName]:
                    classGene[pathwaySetName].update(organismGenes['Reactome'][pathwayName])


        _markStage("reactome_classification")

        # Calculate the total number of genes and compounds per database
        totalGenes = {sourceDB: set(chain.from_iterable(pathways.values())) for sourceDB, pathways in
                      organismGenes.items()}
        totalCompounds = {sourceDB: set(chain.from_iterable(pathways.values())) for sourceDB, pathways in
                          organismCompounds.items()}

        totalFeaturesByOmic, totalRelevantFeaturesByOmic = self.calculateTotalFeaturesByOmic(enrichmentByOmic,
                                                                                             totalGenes, totalCompounds)
        totalInputMatchedCompounds = len(self.getInputCompoundsData())
        totalInputMatchedGenes = len(self.getInputGenesData())
        totalKeggPathways = len(pathwaysList)

        mappedRatiosByOmic = self.getMappedRatios()

        _markStage("totals_and_backgrounds")

        nThreads = MAX_THREADS
        logging.info("USING " + str(nThreads) + " THREADS")

        manager = Manager()
        matchedPathways = manager.dict()  # WILL STORE THE OUTPUT FROM THE THREADS
        nPathwaysPerThread = int(
            ceil(len(pathwaysList) / nThreads)) + 1  # GET THE NUMBER OF PATHWAYS TO BE PROCESSED PER THREAD

        pathwaysListParts = chunks(list(pathwaysList.keys()), nPathwaysPerThread)  # SPLIT THE ARRAY IN n PARTS
        threadsList = []

        # Flattened dict
        allGenesInPathway = {pathwayID: pathway for dbSource, dbPathways in organismGenes.items() for
                             pathwayID, pathway in
                             dbPathways.items()}

        allCompoundsInPathway = {pathwayID: pathway for dbSource, dbPathways in organismCompounds.items() for
                                 pathwayID, pathway in
                                 dbPathways.items()}

        # Once here, inherited by every forked worker, instead of once per worker.
        lookups = _inputFeatureLookups(inputGenes, inputCompounds)

        # LAUNCH THE THREADS
        for pathwayIDsList in pathwaysListParts:
            thread = Process(target=_matchPathways, args=(
                self, pathwayIDsList, allGenesInPathway, allCompoundsInPathway, inputGenes, inputCompounds,
                totalFeaturesByOmic, totalRelevantFeaturesByOmic, matchedPathways, mappedRatiosByOmic,
                enrichmentByOmic, lookups))
            threadsList.append(thread)
            thread.start()

        # Add class enrichment for PaintOmics 4
        if 'Reactome' in self.databases:
            matchedClass = manager.dict()  # WILL STORE THE OUTPUT FROM THE THREADS
            nClassPerThread = int(
                ceil(
                    len( classGene.keys() ) / nThreads ) ) + 1  # GET THE NUMBER OF PATHWAYS TO BE PROCESSED PER THREAD
            classListParts = chunks( list( classGene.keys() ), nClassPerThread )  # SPLIT THE ARRAY IN n PARTS
            for classNameList in classListParts:
                threadClass = Process( target=_matchPathways, args=(
                    self, classNameList, classGene, classComp, inputGenes, inputCompounds,
                    totalFeaturesByOmic, totalRelevantFeaturesByOmic, matchedClass, mappedRatiosByOmic,
                    enrichmentByOmic, lookups) )
                threadsList.append( threadClass )
                threadClass.start()

        # WAIT UNTIL ALL THREADS FINISH -- one shared budget, not one per
        # worker (the same rule the identifier mapper applies).
        _joinAllWithinDeadline(threadsList, MAX_WAIT_THREADS)

        isFinished = True
        for thread in threadsList:
            if thread.is_alive():
                isFinished = False
                thread.terminate()
                logging.info("THREAD TERMINATED IN generatePathwaysList")

        if not isFinished:
            manager.shutdown()
            raise Exception(
                'Your data took too long to process and it was killed. Try again later or upload smaller files if it persists.')

        _markStage("enrichment_threads")

        self.setMatchedPathways(dict(matchedPathways))
        totalMatchedKeggPathways = len(self.getMatchedPathways())

        #PaintOmics 4
        if 'Reactome' in self.databases:
            self.setMatchedClass(dict(matchedClass))

        # The manager server is a forked process holding a copy of every
        # matched pathway; release it now instead of when the GC gets round to it.
        manager.shutdown()

        pvalues_list = defaultdict(dict)
        combined_pvalues_list = defaultdict(dict)

        for pathway_id, pathway in self.getMatchedPathways().items():
            for omic, pvalue in pathway.getSignificanceValues().items():
                # Multi-condition support: use global p-value if available, else first condition
                globalP = pathway.getGlobalOmicPvalues().get(omic)
                if globalP is not None:
                    pvalues_list[omic][pathway_id] = globalP
                elif len(pvalue) > 0:
                    pvalues_list[omic][pathway_id] = pvalue[0][2]
                else:
                    pvalues_list[omic][pathway_id] = 1.0

            for method, combined_pvalue in pathway.getCombinedSignificancePvalues().items():
                if not isinstance(combined_pvalue, list):
                    combined_pvalue = [combined_pvalue]
                nCond = len(combined_pvalue)
                for c in range(nCond):
                    combined_pvalues_list[method + "_c" + str(c)][pathway_id] = combined_pvalue[c]

        adjusted_pvalues = {omic: adjustPvalues(omicPvalues) for omic, omicPvalues in pvalues_list.items()}
        
        adjusted_combined_pvalues = {}
        for method_cond, methodPvalues in combined_pvalues_list.items():
            adjusted_combined_pvalues[method_cond] = adjustPvalues(methodPvalues)

        # Set the adjusted p-value on a pathway basis
        for pathway_id, pathway in self.getMatchedPathways().items():
            # Update omic adjusted p-values (using global/first condition as before)
            for omic, pvalue in pathway.getSignificanceValues().items():
                pathway.setOmicAdjustedSignificanceValues(omic,
                                                          {adjust_method: pvalues[pathway_id] for adjust_method, pvalues
                                                           in adjusted_pvalues[omic].items()})

            # Update combined adjusted p-values per condition.
            # Storage shape: pathway.adjustedCombinedSignificanceValues[method][adjMethod]
            #   - scalar (single-condition jobs) — preserved for back-compat with old jobs.
            #   - list[float] (multi-condition jobs) — one entry per condition, ordered by
            #     condition index. Frontend (PA_Step3Views.js:3515-3520) accepts both shapes
            #     and emits per-condition keys when a list is detected.
            for method, combined_pvalue in pathway.getCombinedSignificancePvalues().items():
                if not isinstance(combined_pvalue, list):
                    combined_pvalue = [combined_pvalue]
                nCond = len(combined_pvalue)

                first_cond_key = method + "_c0"
                if first_cond_key not in adjusted_combined_pvalues:
                    continue
                adj_methods = adjusted_combined_pvalues[first_cond_key].keys()
                if nCond == 1:
                    # Single-condition: keep scalars (back-compat).
                    pathway.setMethodAdjustedCombinedSignificanceValues(method, {
                        adj: adjusted_combined_pvalues[first_cond_key][adj][pathway_id]
                        for adj in adj_methods
                    })
                else:
                    # Multi-condition: per-condition list, padded with 1.0 if a condition
                    # had no entry in adjusted_combined_pvalues.
                    pathway.setMethodAdjustedCombinedSignificanceValues(method, {
                        adj: [
                            adjusted_combined_pvalues.get(method + "_c" + str(c), {})
                                                    .get(adj, {})
                                                    .get(pathway_id, 1.0)
                            for c in range(nCond)
                        ]
                        for adj in adj_methods
                    })

        _markStage("pvalue_adjustment")

        _stageTotal = sum(seconds for _, seconds in _stageTimings)
        logging.info(
            "STEP2 TIMING generatePathwaysList %.1fs total over %d pathways (%s) -- %s" % (
                _stageTotal, len(pathwaysList), "+".join(self.databases),
                ", ".join("%s=%.1fs (%.0f%%)" % (name, seconds, 100 * seconds / max(_stageTotal, 1e-9))
                          for name, seconds in _stageTimings)))

        logging.info("SUMMARY: " + str(totalMatchedKeggPathways) + " Matched Pathways of " + str(
            totalKeggPathways) + "in KEGG; Total input Genes = " + str(
            totalInputMatchedGenes) + "; SUMMARY: Total input Compounds  = " + str(totalInputMatchedCompounds))

        for key in totalFeaturesByOmic:
            logging.info("SUMMARY: Total " + key + " Features = " + str(totalFeaturesByOmic.get(key)))
            logging.info("SUMMARY: Total " + key + " Relevant Features = " + str(totalRelevantFeaturesByOmic.get(key)))

        # PaintOmics 4
        self.summary = [totalKeggPathways, totalMatchedKeggPathways, totalInputMatchedGenes, totalInputMatchedCompounds,
                        totalFeaturesByOmic, totalRelevantFeaturesByOmic]

        # TODO: REVIEW THE SUMMARY GENERATION
        return self.summary

    def calculateTotalFeaturesByOmic(self, enrichmentByOmic, totalGenes, totalCompounds):
        """
        This function...

        @param {type}
        @returns
        """
        totalFeaturesID = set()
        totalFeaturesIDSig = set()
        totalFeaturesByOmic = defaultdict(Counter)
        totalRelevantFeaturesByOmic = defaultdict(Counter)

        # Three enrichment methods available: gene, feature and association enrichment.
        # By default use gene enrichment unless specified otherwise.
        enrichments = {
            'genes': lambda x: x.getInputName(),
            'features': lambda x: x.getOriginalName(),
            'associations': lambda x: ':::'.join([x.getInputName(), x.getOriginalName()])
        }

        # Determine the maximum number of conditions across all omics
        max_conditions = 1
        for feature in chain(self.getInputCompoundsData().values(), self.getInputGenesData().values()):
            for ov in feature.getOmicsValues():
                if isinstance(ov.relevant, list):
                    max_conditions = max(max_conditions, len(ov.relevant))
        
        has_multi_cond = max_conditions > 1

        # counterNames[db][omicName][enrichmentProperty] = [isRelevant_C1, isRelevant_C2, ...]
        #
        # The key MUST be the enrichment property (the input identifier, the
        # original name, or the association -- whichever the user selected),
        # never feature.getID(). This dict is the *background* of the
        # hypergeometric test, and testPathwaySignificance builds the *sample*
        # keyed by exactly that enrichment property. Keying the two sides
        # differently mixes units, because mapFeatureIdentifiers clones one
        # input feature once per target ID it resolves to and addInputGeneData
        # merges every input that resolved to the same target ID into a single
        # Gene:
        #   * one input on several KEGG ids inflated the background (counted
        #     once per clone) while the pathway counted it once;
        #   * several inputs on one KEGG id deflated the background to 1 while
        #     the pathway counted each input, which makes the sample larger
        #     than the population and hypergeom.sf returns NaN -- the NaN that
        #     Statistics._usablePvalues documents and that takes the whole JSON
        #     response down with it.
        # Measured on a real job: 11359 KEGG mapping rows for 10406 distinct
        # inputs, 272 inputs on >1 KEGG gene, worst case 43 inputs on one id.
        counterNames = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

        # Total features depends on the source DB
        totalFeatures = {dbSource: dbGenes.union(totalCompounds.get(dbSource)) for dbSource, dbGenes in
                         totalGenes.items()}

        # Accumulated so the features that matched no pathway can be reported
        # once, as a proportion, instead of a line each.
        unmappedFeatureIDs = []
        totalInputFeatures = 0

        for feature in chain(self.getInputCompoundsData().values(), self.getInputGenesData().values()):
            totalInputFeatures += 1
            # Count only those present in at least one pathway
            if type(feature.getMatchingDB()) is str:
                dbList = [feature.getMatchingDB()]
            else:
                dbList = feature.getMatchingDB()
            found_in_any_db = False
            for db in dbList:
                db_features = totalFeatures.get(db, set())
                if feature.getID() in db_features or feature.getName() in db_features:
                    found_in_any_db = True
                    for omicValue in feature.getOmicsValues():
                        # Select the appropriate enrichment property
                        enrichmentType = enrichmentByOmic[omicValue.getOmicName()]
                        enrichmentProperty = enrichments.get(enrichmentType)(omicValue).lower()

                        # relevantValue is now a list [True, False, ...]
                        relevantValue = [omicValue.isRelevantAssociation()] if enrichmentType == 'associations' else omicValue.relevant
                        if not isinstance(relevantValue, list):
                            relevantValue = [relevantValue]
                        elif len(relevantValue) == 0:
                            relevantValue = [False] * max_conditions
                        
                        if not has_multi_cond:
                            # Scalar fast-path
                            is_rel = relevantValue[0]
                            old_val = counterNames[db][omicValue.getOmicName()].get(enrichmentProperty)
                            if old_val is None:
                                counterNames[db][omicValue.getOmicName()][enrichmentProperty] = is_rel
                            else:
                                counterNames[db][omicValue.getOmicName()][enrichmentProperty] = old_val or is_rel
                        else:
                            # List-based multi-condition path
                            if not isinstance(relevantValue, list):
                                relevantValue = [relevantValue]

                            old_val = counterNames[db][omicValue.getOmicName()].get(enrichmentProperty)
                            if not old_val:
                                counterNames[db][omicValue.getOmicName()][enrichmentProperty] = list(relevantValue)
                            else:
                                # Combine lists with OR logic
                                nCond = max(len(old_val), len(relevantValue))
                                combined = [False] * nCond
                                for i in range(nCond):
                                    v1 = old_val[i] if i < len(old_val) else False
                                    v2 = relevantValue[i] if i < len(relevantValue) else False
                                    combined[i] = v1 or v2
                                counterNames[db][omicValue.getOmicName()][enrichmentProperty] = combined

                        # Deliberately still keyed by the target ID: these two are
                        # sets of KEGG identifiers, not enrichment counters, so
                        # they do not share the units of counterNames above.
                        if db == 'KEGG':
                            totalFeaturesID.add(feature.getID())
                            is_any_rel = any(relevantValue) if isinstance(relevantValue, list) else relevantValue
                            if is_any_rel:
                                totalFeaturesIDSig.add(feature.getID())
            if not found_in_any_db:
                # Counted, not logged one line at a time. A feature in no
                # pathway of any database is the ordinary case -- most measured
                # genes are not annotated to a pathway -- so this was never an
                # error, and at logging.error it produced 6480 ERROR lines for a
                # single run of the six-omic example. That buries the real
                # errors: grepping the log for ERROR to find out why a job
                # failed returns thousands of lines about healthy features.
                # Kept at debug for the one case it helps with, diagnosing a
                # mapping problem for a named feature.
                unmappedFeatureIDs.append(feature.getID())
                logging.debug("STEP2 - Feature not present in any pathway " + feature.getID())

        if unmappedFeatureIDs:
            # One line an operator can actually act on: a proportion tells you
            # whether the identifiers are mismatched, which a per-feature list
            # never did.
            logging.info("STEP2 - %d of %d features matched no pathway in any "
                         "database (e.g. %s)" % (
                             len(unmappedFeatureIDs),
                             totalInputFeatures,
                             ", ".join(unmappedFeatureIDs[:5])))

        for sourceDB, countersDB in counterNames.items():
            for omicName, featureRelevanceLists in countersDB.items():
                totalFeaturesByOmic[sourceDB][omicName] = len(featureRelevanceLists.keys())
                
                # Calculate counts per condition
                all_vals = list(featureRelevanceLists.values())
                if not all_vals:
                    totalRelevantFeaturesByOmic[sourceDB][omicName] = []
                    continue

                if not has_multi_cond:
                    # Scalar fast-path: sum the booleans
                    totalRelevantFeaturesByOmic[sourceDB][omicName] = [sum(all_vals)]
                else:
                    # List-based multi-condition path
                    nConditions = max((len(v) for v in all_vals if isinstance(v, list)), default=1)
                    condition_counts = [0] * nConditions
                    for rel_val in all_vals:
                        if isinstance(rel_val, list):
                            for i in range(min(nConditions, len(rel_val))):
                                if rel_val[i]:
                                    condition_counts[i] += 1
                        elif rel_val:
                            condition_counts[0] += 1
                    totalRelevantFeaturesByOmic[sourceDB][omicName] = condition_counts

        return totalFeaturesByOmic, totalRelevantFeaturesByOmic

    def _setCrossOmicSignificance(self, pathwayInstance, mappedRatiosByOmic, has_multi_cond):
        """Cross-omic integration for one pathway: combined p-values per
        condition, then the total global p-value over the omics' globals,
        both weighted by each omic's mapped ratio."""
        omicSignificanceValues = pathwayInstance.getSignificanceValues()
        keyOrder = list(omicSignificanceValues.keys())

        if keyOrder:
            nConditions = len(omicSignificanceValues[keyOrder[0]])
            combined_pvalues_per_condition = defaultdict(list)

            # Integration optimization
            if not has_multi_cond:
                omicPvalues_cond = [omicSignificanceValues[omicName][0][2] for omicName in keyOrder]
                weights_cond = [mappedRatiosByOmic.get(omicName, 0) for omicName in keyOrder]
                integratedP_dict = calculateCombinedSignificancePvalues(omicPvalues_cond, weights_cond)
                for method, val in integratedP_dict.items():
                    combined_pvalues_per_condition[method] = [val]
            else:
                for i in range(nConditions):
                    omicPvalues_cond = []
                    weights_cond = []
                    for omicName in keyOrder:
                        if i < len(omicSignificanceValues[omicName]):
                            omicPvalues_cond.append(omicSignificanceValues[omicName][i][2])
                        else:
                            omicPvalues_cond.append(1.0)
                        weights_cond.append(mappedRatiosByOmic.get(omicName, 0))

                    integratedP_dict = calculateCombinedSignificancePvalues(omicPvalues_cond, weights_cond)
                    for method, val in integratedP_dict.items():
                        combined_pvalues_per_condition[method].append(val)

            pathwayInstance.setCombinedSignificancePvalues(combined_pvalues_per_condition)

            # Step 4.1: Total Global P-value (Combine global p-values of each omic)
            globalOmicPvalues = pathwayInstance.getGlobalOmicPvalues()
            if globalOmicPvalues:
                globalKeyOrder = list(globalOmicPvalues.keys())
                globalWeights = [mappedRatiosByOmic.get(omicName, 0) for omicName in globalKeyOrder]
                globalPvalues = [globalOmicPvalues[omicName] for omicName in globalKeyOrder]

                totalGlobalP_dict = calculateCombinedSignificancePvalues(globalPvalues, globalWeights)
                pathwayInstance.setTotalGlobalPvalues(totalGlobalP_dict)

    def _setPerOmicSignificance(self, pathwayInstance, totalFeaturesByOmic,
                                totalRelevantFeaturesByOmic, has_multi_cond):
        """Per-omic p-values for one pathway: significance per condition, then
        the omic's global p-value (Fisher over the conditions)."""
        # significanceValues format: OmicName -> [[totalMatched, totalRelevant, pValue], ...] (one per condition)
        for omicName, conditionValues in pathwayInstance.getSignificanceValues().items():
            pvalues_per_condition = []
            total_features = totalFeaturesByOmic.get(omicName, 0)
            total_relevant_list = totalRelevantFeaturesByOmic.get(omicName, [])

            # Optimization: if single condition, just do one calculation
            if not has_multi_cond:
                total_relevant_cond = total_relevant_list[0] if total_relevant_list else 0
                val = conditionValues[0]
                pValue = calculateSignificance(self.getTest(), total_features, total_relevant_cond, val[0], val[1])
                pvalues_per_condition = [pValue]
            else:
                for i, values in enumerate(conditionValues):
                    total_relevant_cond = total_relevant_list[i] if i < len(total_relevant_list) else 0

                    pValue = calculateSignificance(self.getTest(),
                                                   total_features,
                                                   total_relevant_cond,
                                                   values[0], # totalMatched in pathway
                                                   values[1]) # totalRelevant in pathway for this condition
                    pvalues_per_condition.append(pValue)

            pathwayInstance.setSignificancePvalue(omicName, pvalues_per_condition)

            # Global Omic P-value: combine p-values from all conditions
            if pvalues_per_condition:
                if not has_multi_cond:
                    pathwayInstance.setGlobalOmicPvalue(omicName, pvalues_per_condition[0])
                else:
                    # We use uniform weights for conditions
                    weights_cond = [1] * len(pvalues_per_condition)
                    globalP_dict = calculateCombinedSignificancePvalues(pvalues_per_condition, weights_cond)
                    pathwayInstance.setGlobalOmicPvalue(omicName, globalP_dict.get("Fisher", 1.0))

    def _accumulatePathwayMatches(self, featureIDsInPathway, inputFeaturesDict, addMatchedID,
                                  counterNames, enrichmentByOmic, enrichments, max_conditions,
                                  has_multi_cond, sourceDB):
        """One half of testPathwaySignificance: walk the pathway's feature IDs
        (genes or compounds -- the loop was duplicated verbatim for both),
        record every input feature that matches under sourceDB, and fold its
        per-condition relevance into counterNames. Returns True if anything
        matched."""
        matched = False
        featureIDsInPathway = set([x.lower() for x in featureIDsInPathway])

        for featureID in featureIDsInPathway:
            feature = inputFeaturesDict.get(featureID)
            if feature:
                matchingDB = feature.getMatchingDB()
                db_matches = sourceDB in matchingDB if isinstance(matchingDB, list) else matchingDB == sourceDB
                if db_matches:
                    matched = True
                    addMatchedID(feature.getID())
                    for omicValue in feature.getOmicsValues():
                        enrichmentType = enrichmentByOmic[omicValue.getOmicName()]
                        enrichmentProperty = enrichments.get(enrichmentType)(omicValue).lower()

                        # relevantValue is now a list [True, False, ...]
                        relevantValue = [omicValue.isRelevantAssociation()] if enrichmentType == 'associations' else omicValue.relevant
                        if not isinstance(relevantValue, list):
                            relevantValue = [relevantValue]
                        elif len(relevantValue) == 0:
                            relevantValue = [False] * max_conditions

                        if not has_multi_cond:
                            # Scalar fast-path
                            is_rel = relevantValue[0]
                            old_val = counterNames[omicValue.getOmicName()].get(enrichmentProperty)
                            if old_val is None:
                                counterNames[omicValue.getOmicName()][enrichmentProperty] = is_rel
                            else:
                                counterNames[omicValue.getOmicName()][enrichmentProperty] = old_val or is_rel
                        else:
                            # List-based multi-condition path
                            old_val = counterNames[omicValue.getOmicName()].get(enrichmentProperty)
                            if not old_val:
                                counterNames[omicValue.getOmicName()][enrichmentProperty] = list(relevantValue)
                            else:
                                nCond = max(len(old_val), len(relevantValue))
                                combined = [False] * nCond
                                for i in range(nCond):
                                    v1 = old_val[i] if i < len(old_val) else False
                                    v2 = relevantValue[i] if i < len(relevantValue) else False
                                    combined[i] = v1 or v2
                                counterNames[omicValue.getOmicName()][enrichmentProperty] = combined
        return matched

    def testPathwaySignificance(self, genesInPathway, compoundsInPathway, inputGenesDict, inputCompoundsDict,
                                totalFeaturesByOmic, totalRelevantFeaturesByOmic, mappedRatiosByOmic, enrichmentByOmic,
                                sourceDB, has_multi_cond):
        """
        This function takes a list of genes and compounds from the input and check if those features are at the
        list of feautures involved into a specific pathway.
        After that, the function calculates the significance for each omic type for the current pathway

        @param {List} genesInPathway, list of gene IDs in the pathway (ordered)
        @param {List} compoundsInPathway, list of compound IDs in the pathway (ordered)
        @param {Dict} inputGenesDict, dictionary of genes (class Gene) indexed by lowercase ID
        @param {Dict} inputCompoundsDict, dictionary of compounds (class Compound) indexed by lowercase ID
        @param {Dict} totalFeaturesByOmic, contains the total features for each omic type (for statistics)
        @param {Dict} totalRelevantFeaturesByOmic, contains the total relevant features for each omic type (for statistics)
        @param {Boolean} has_multi_cond, True if the job has multiple experimental conditions
        @returns {Boolean} isValidPathway, True if at least one feature was matched, False in other cases.
        @returns {Pathway} pathwayInstance a new Pathway instance containing the matched info. None if pathway is not valid.
        """
        isValidPathway = False
        pathwayInstance = Pathway("")

        # Derive max_conditions from totalRelevantFeaturesByOmic for proper padding
        max_conditions = max(
            (len(v) for v in (totalRelevantFeaturesByOmic or {}).values() if isinstance(v, list)),
            default=1
        )

        # counterNames[omicName][enrichmentProperty] = [isRelevant_C1, isRelevant_C2, ...]
        counterNames = defaultdict(lambda: defaultdict(list))

        # Three enrichment methods available: gene, feature and association enrichment.
        # By default use gene enrichment unless specified otherwise.
        enrichments = {
            'genes': lambda x: x.getInputName(),
            'features': lambda x: x.getOriginalName(),
            'associations': lambda x: ':::'.join([x.getInputName(), x.getOriginalName()])
        }

        # The gene and compound halves are the same accumulation; only the
        # input dictionary and which matched-ID list gets the hit differ.
        # TODO: RETURN AS A SET IN KEGG INFORMATION MANAGER
        matchedGenes = self._accumulatePathwayMatches(
            genesInPathway, inputGenesDict, pathwayInstance.addMatchedGeneID,
            counterNames, enrichmentByOmic, enrichments, max_conditions,
            has_multi_cond, sourceDB)
        matchedCompounds = self._accumulatePathwayMatches(
            compoundsInPathway, inputCompoundsDict, pathwayInstance.addMatchedCompoundID,
            counterNames, enrichmentByOmic, enrichments, max_conditions,
            has_multi_cond, sourceDB)
        isValidPathway = matchedGenes or matchedCompounds

        for omicName, featureNames in counterNames.items():
            for rel_val in featureNames.values():
                pathwayInstance.addSignificanceValues(omicName, rel_val)

        if isValidPathway:
            # How big the pathway is, by feature class. This is the only place
            # that sees both full feature sets and builds the Pathway, and the
            # sets arrive from getAllFeatureIDsByPathwayID already deduplicated.
            # The network view needs them because the count it used instead --
            # total_features in the installed pathways_network.json -- is the
            # GENE count, so on a compound-only job it divided matched compounds
            # by the pathway's genes and excluded everything.
            pathwayInstance.setTotalGenes(len(genesInPathway or []))
            pathwayInstance.setTotalCompounds(len(compoundsInPathway or []))

            self._setPerOmicSignificance(pathwayInstance, totalFeaturesByOmic,
                                         totalRelevantFeaturesByOmic, has_multi_cond)
            self._setCrossOmicSignificance(pathwayInstance, mappedRatiosByOmic, has_multi_cond)

        else:
            pathwayInstance = None

        return isValidPathway, pathwayInstance

    #: Sources that ship no pathway diagram at all. OmniPath is a molecular
    #: interaction network -- signed, directed edges with no canvas and no node
    #: coordinates anywhere in the resource -- so its pathways are laid out as a
    #: graph in the client instead of painted over a raster. Handing PIL a PNG
    #: that was never downloaded raises FileNotFoundError and takes the whole
    #: paint request down with it, so the lookup is skipped rather than caught.
    DIAGRAMLESS_SOURCES = frozenset(["OmniPath"])

    def getPathwayImageSize(self, keggInformationManager, source, pathwayID):
        """Return the (width, height) of a pathway diagram, or (0, 0) if there is none.

        A zero size is the client's signal to lay the pathway out itself rather
        than to scale features onto a background image.
        """
        if source in self.DIAGRAMLESS_SOURCES:
            # No PNG to measure: the installer laid the pathway out and stored
            # the canvas it used, which is the coordinate space its gene boxes
            # are already expressed in.
            stored = keggInformationManager.getPathwayCanvasSizeByID(
                self.getOrganism(), pathwayID)
            if stored is not None:
                return stored
            logging.warning("STEP3 - %s PATHWAY %s HAS NO STORED CANVAS SIZE",
                            source, pathwayID)
            return (0, 0)

        imagePath = (keggInformationManager.getDataDir(source) + 'png/'
                     + pathwayID.replace(self.getOrganism(), "map") + ".png")
        try:
            return getImageSize(imagePath)
        except (IOError, OSError) as ex:
            # A partially installed source is a missing file, not a broken job.
            # The features carry their own coordinates either way, so painting
            # degrades to an unscaled layout instead of failing the request.
            logging.warning("STEP3 - NO DIAGRAM FOR PATHWAY %s (source %s): %s",
                            pathwayID, source, ex)
            return (0, 0)

    def generateSelectedPathwaysInformation(self, selectedPathways, visibleOmics, toBSON=False):
        """
        This function...

        @param {type}
        @returns
        """

        # ************************************************************************
        # Step 1. Prepare the variables
        # ************************************************************************
        pathwayInstance = None
        selectedPathwayInstances = []
        graphicalOptionsInstancesBSON = []
        omicsValuesSubset = {}
        bsonAux = None

        keggInformationManager = KeggInformationManager()

        if (len(visibleOmics) > 0):
            # TODO: IN PREVIOUS STEPS THE USER COULD SPECIFY THE DEFAULT OMICS TO SHOW
            pass
        else:
            # By default try to show 3 genes based omics and 1 Compound based omic
            visibleOmics = [inputData.get("omicName") + "#genebased" for inputData in
                            self.getGeneBasedInputOmics()[0:3]]
            visibleOmics.extend(
                [inputData.get("omicName") + "#compoundbased" for inputData in self.getCompoundBasedInputOmics()[0:1]])

        # ************************************************************************
        # Step 2. For each provided pathway, get the graphical information
        # ************************************************************************
        # A pathway the user asks to paint but which this job never matched has
        # no instance, and the code below dereferences it immediately
        # (pathwayInstance.getSource()), so an unknown ID raised
        #     AttributeError: 'NoneType' object has no attribute 'getSource'
        # naming neither the pathway nor the job. That is reachable from a
        # stale bookmark or a shared link pointing at a pathway set that has
        # since changed.
        #
        # Unresolvable IDs are skipped so that a mixed selection still paints
        # what it can; if nothing at all resolved, the request is reported
        # rather than answered with a silently empty result.
        unknownPathways = []

        for pathwayID in selectedPathways:
            pathwayInstance = self.getMatchedPathways().get(pathwayID)

            if pathwayInstance is None:
                unknownPathways.append(pathwayID)
                logging.warning(
                    "STEP3 - IGNORING PATHWAY " + str(pathwayID) +
                    ", NOT AMONG THE PATHWAYS MATCHED BY JOB " + str(self.getJobID()))
                continue

            # AQUI RECORRER PARA CADA ELEMENTO DE LA PATHWAY Y VER SI
            #  SI ES GEN Y ESTA EN LA LISTA DE GENES METIDOS -> GUARDAR VALORES, POSICIONES, SIGNIFICATIVO
            #  SI ES COMPOUND Y ESTA EN LA LISTA DE COMPOUND METIDOS -> GUARDAR VALORES, POSICIONES, SIGNIFICATIVO
            #  ...

            # ************************************************************************
            # Step 2.1 Create the graphical information object -> features coordinates,
            #          box height,...
            # ************************************************************************
            genesInPathway, compoundsInPathway = keggInformationManager.getAllFeaturesByPathwayID(self.getOrganism(),
                                                                                                  pathwayID)

            graphicalOptions = PathwayGraphicalData()
            graphicalOptions.setFeaturesGraphicalData(genesInPathway + compoundsInPathway)
            graphicalOptions.setImageSize(self.getPathwayImageSize(
                keggInformationManager, pathwayInstance.getSource(), pathwayID))
            graphicalOptions.setVisibleOmics(visibleOmics)

            # Set the graphical options for the pathway
            pathwayInstance.setGraphicalOptions(graphicalOptions)

            # ************************************************************************
            # Step 2.2 Get the subset of genes and compounds that are in the current
            #          pathway and add them to the list of features that will be send
            #          to the client side with the expression values
            # ************************************************************************
            # TODO: MEJORABLE, MULTHREADING U OTRAS OPCIONES
            auxDict = self.getInputGenesData()

            for geneID in pathwayInstance.getMatchedGenes():
                if toBSON:
                    omicsValuesSubset[geneID] = auxDict.get(geneID).toBSON()
                else:
                    omicsValuesSubset[geneID] = auxDict.get(geneID)

            auxDict = self.getInputCompoundsData()

            for compoundID in pathwayInstance.getMatchedCompounds():
                if toBSON:
                    omicsValuesSubset[compoundID] = auxDict.get(compoundID).toBSON()
                else:
                    omicsValuesSubset[compoundID] = auxDict.get(compoundID)

            if toBSON:
                bsonAux = pathwayInstance.getGraphicalOptions().toBSON()
                bsonAux["pathwayID"] = pathwayID
                graphicalOptionsInstancesBSON.append(bsonAux)
            # Add the pathway to the list
            selectedPathwayInstances.append(pathwayInstance)

        # Pathways were requested and not one of them belongs to this job:
        # answering with an empty result would look like a successful paint of
        # nothing, so say what happened instead.
        if unknownPathways and not selectedPathwayInstances:
            raise UserWarning(
                "None of the requested pathways belong to this job: " +
                ", ".join(str(pathway) for pathway in unknownPathways[:10]) + ".")

        return [selectedPathwayInstances, graphicalOptionsInstancesBSON, omicsValuesSubset]

    # GENERATE METAGENES LIST FUNCTIONS -----------------------------------------------------------------------------------------
    def generateMetagenesList(self, ROOT_DIRECTORY: object, clusterNumber: object, omicList: object = None,
                              database: object = None) -> object:
        """
        This function obtains the metagenes for each pathway in KEGG based on the input values.

        @param {type}
        @returns
        """
        # STEP 1. EXTRACT THE MAPPING FILES THE R SCRIPT READS
        # Only the `<omic>_matched.txt` members: they are the R script's only
        # input, and re-inflating every omic's matched AND unmatched files (the
        # whole zip) on every call made the single-omic slider recompute pay for
        # the entire job.
        filtered_omics = self.geneBasedInputOmics
        filtered_databases = self.getDatabases()

        if omicList:
            filtered_omics = [inputOmic for inputOmic in self.geneBasedInputOmics if
                              inputOmic.get("omicName") in omicList]

        if database:
            filtered_databases = set(database).intersection(set(filtered_databases))

        with zipFile(self.getOutputDir() + "/mapping_results_" + self.getJobID() + ".zip") as mappingZip:
            wanted = {inputOmic.get("omicName") + "_matched.txt" for inputOmic in filtered_omics}
            for member in mappingZip.namelist():
                if member in wanted:
                    mappingZip.extract(member, path=self.getTemporalDir())

        # An omic that matched nothing has an empty <omic>_matched.txt, and

        # generateMetaGenes.R read it as "no lines available in input" -- a

        # crash reported to the user as a metagenes failure. There is nothing

        # to cluster; the omic is skipped here and step 2 says why (see

        # explainEmptyMapping) when it was the only one.

        eligible = []

        for inputOmic in filtered_omics:

            matchedFile = self.getTemporalDir() + "/" + inputOmic.get("omicName") + "_matched.txt"

            if hasDataRows(matchedFile):

                eligible.append(inputOmic)

            else:

                logging.warning("STEP2 - omic '%s' matched no feature; metagenes skipped.",

                                inputOmic.get("omicName"))

        filtered_omics = eligible


        # STEP 2. GENERATE THE DATA FOR EACH OMIC DATA TYPE

        # This loop is the whole of the metagenes phase and its size is known
        # exactly before it starts, so the bar here is a real count rather than a
        # clock-based guess. It is ~50% of step 2, and without this it showed a
        # single frozen number for the entire phase.
        metageneUnits = len(filtered_omics) * max(1, len(filtered_databases))
        metagenesDone = 0
        JobProgress.units(self.getJobID(), 0, total=metageneUnits,
                          detail="0 of %d" % metageneUnits)

        # One Rscript per (omic, database) pair, each paying R start-up plus the
        # cluster/mclust/factoextra library load plus the annotation read before
        # any work -- ~1.3 s here, several seconds on the production CPU, times
        # 10-18 pairs, run one after another. The R processes are independent
        # (their own seeds, their own output files, named by omic and database),
        # so the OMICS run concurrently; the databases of one omic stay
        # sequential because the elbow plot `<omic>_elbow.png` carries no
        # database suffix and the last database written must keep winning.
        # Results are consumed below in the original loop order, so logging,
        # error handling, pathway updates and progress ticks are unchanged.
        commands = {}
        for inputOmic in filtered_omics:
            for dbname in filtered_databases:
                inputFile = self.getTemporalDir() + "/" + inputOmic.get("omicName") + '_matched.txt'
                kClusters = str(dict(clusterNumber).get(inputOmic.get("omicName"), "dynamic"))
                commands[(inputOmic.get("omicName"), dbname)] = ([
                    "Rscript",
                    ROOT_DIRECTORY + "common/bioscripts/generateMetaGenes.R",
                    '--specie=' + self.getOrganism(),
                    '--input_file=' + inputFile,
                    '--output_prefix=' + inputOmic.get("omicName"),
                    '--data_dir=' + self.getTemporalDir(),
                    '--kegg_dir=' + KEGG_DATA_DIR,
                    '--sources_dir=' + ROOT_DIRECTORY + 'common/bioscripts/',
                    '--kclusters=' + kClusters if kClusters.isdigit() else '',
                    '--database=' + dbname if dbname != "KEGG" else ''], kClusters)

        results = _runMetagenesScripts(
            [inputOmic.get("omicName") for inputOmic in filtered_omics],
            list(filtered_databases), commands)

        for inputOmic in filtered_omics:
            # STEP 2.1 EXECUTE THE R SCRIPT FOR EACH DATABASE
            for dbname in filtered_databases:
                try:
                    logging.info("GENERATING METAGENES INFORMATION FOR " + str(dbname) + "...CALLING")
                    kClusters = commands[(inputOmic.get("omicName"), dbname)][1]
                    logging.info("kClusters=" + str(kClusters))
                    logging.info(str(ROOT_DIRECTORY))

                    logging.info("dbname is " + str(dbname))

                    try:
                        returncode, output = results[(inputOmic.get("omicName"), dbname)]
                        if returncode is None:
                            raise output  # the OSError check_output would have raised here
                        if returncode != 0:
                            raise CalledProcessError(returncode, "Rscript", output=output)
                    except CalledProcessError as ex:
                        error_detail = ex.output.decode('utf-8') if ex.output else str(ex)
                        logging.error("STEP2 - Error while generating metagenes information for " + inputOmic.get("omicName") + " db: " + str(dbname))
                        logging.error(f"Subprocess output: {error_detail}")
                        # Too few mapped features for clustering is a property
                        # of a small upload, not a broken server: mclust reports
                        # "no available data for fitting" on numeric(0). The
                        # metagene trend charts are an enhancement, so skip this
                        # omic/database pair instead of failing the whole of
                        # step 2 for a dataset that mapped a handful of genes.
                        # "more cluster centers than distinct data points" is the
                        # k-means flavour of the same situation (e.g. 2-condition
                        # designs collapse every centred profile onto 2 points).
                        degenerate_data_messages = (
                            "no available data for fitting",
                            "more cluster centers than distinct data points",
                        )
                        if any(m in error_detail for m in degenerate_data_messages):
                            logging.warning(
                                "STEP2 - degenerate metagene geometry, cannot cluster "
                                "for omic '%s' (%s); continuing without metagenes.",
                                inputOmic.get("omicName"), dbname)
                            continue
                        raise RuntimeError(f"Metagenes generation failed for omic '{inputOmic.get('omicName')}' and database '{dbname}'. Details: {error_detail}")

                    # STEP 2.2 PROCESS THE RESULTING FILE

                    # Reset all pathways metagenes for the omic
                    for pathway in self.matchedPathways.values():
                        # Only reset metagenes for current DB
                        if pathway.getSource().lower() == str(dbname).lower():
                            pathway.resetMetagenes(inputOmic.get("omicName"))

                    metagenesFileName: object = self.getTemporalDir() + "/" + inputOmic.get("omicName") + "_metagenes" + \
                                                ("_" + str(dbname).lower() + ".tab" if dbname != "KEGG" else ".tab")

                    if os_path.exists(metagenesFileName):
                        metageneRows = 0
                        with open(metagenesFileName, 'r') as inputDataFile:
                            for line in csv_reader(inputDataFile, delimiter="\t"):
                                if line[0] in self.matchedPathways:
                                    self.matchedPathways.get(line[0]).addMetagenes(inputOmic.get("omicName"),
                                                                                   {"metagene": line[1], "cluster": line[2],
                                                                                    "values": line[3:]})
                                    metageneRows += 1
                        # One line per (omic, database) rather than one per
                        # metagene row: the per-row form was hundreds to
                        # thousands of formatted writes per job.
                        logging.info("METAGENES - %d rows added for omic '%s' (%s) from %s",
                                     metageneRows, inputOmic.get("omicName"), dbname, metagenesFileName)
                    else:
                        logging.warning(f"Metagenes file {metagenesFileName} not found. This is expected if no matches were found for db {dbname}.")

                except IOError:
                    logging.error("STEP2 - File not found or read error for metagenes " + inputOmic.get("omicName") + " db: " + str(dbname))

                # Counted here rather than on the success path: an omic/database
                # pair that produced no file is still a unit of work that is over,
                # and the R script exits cleanly without writing when there are no
                # matches. Counting only successes would stall the bar on exactly
                # the jobs where it is least obvious what is happening.
                metagenesDone += 1
                JobProgress.units(self.getJobID(), metagenesDone,
                                  detail="%d of %d" % (metagenesDone, metageneUnits))

        # The metagene thumbnails move from the temporal dir into the output
        # dir, replacing last time's; os calls instead of `rm`/`mv` through a
        # shell (two process spawns and a shell glob per call).
        for oldPNG in glob.glob(os_path.join(self.getOutputDir(), "*.png")):
            try:
                os.remove(oldPNG)
            except OSError:
                pass
        for newPNG in glob.glob(os_path.join(self.getTemporalDir(), "*.png")):
            try:
                os.replace(newPNG, os_path.join(self.getOutputDir(), os_path.basename(newPNG)))
            except OSError as ex:
                logging.warning("METAGENES - could not move %s: %s", newPNG, ex)
        return self

    # JSON <-> BSON FUNCTIONS ------------------------------------------------------------------------------------------------------
    def parseBSON(self, bsonData):
        """
        This function...

        @param {type}
        @returns
        """
        bsonData.pop("_id")
        for (attr, value) in bsonData.items():
            if attr == "matchedPathways":
                pathwayInstance = None
                self.matchedPathways.clear()
                for (pathwayID, pathwayData) in value.items():
                    pathwayInstance = Pathway(pathwayID)
                    pathwayInstance.parseBSON(pathwayData)
                    self.addMatchedPathway(pathwayInstance)
            if attr == "foundCompounds":
                self.foundCompounds[:] = []
                for foundCompoundID in value:
                    self.addFoundCompound({
                        'mainCompounds': [Compound(compoundData["ID"]).parseBSON(compoundData) for compoundData in
                                          value.getMainCompounds()],
                        'otherCompounds': [Compound(compoundData["ID"]).parseBSON(compoundData) for compoundData in
                                           value.getOtherCompounds()]
                    })
            elif attr == "inputCompoundsData":
                compoundInstance = None
                self.inputCompoundsData.clear()
                for (compoundID, compoundData) in value.items():
                    compoundInstance = Compound(compoundID)
                    compoundInstance.parseBSON(compoundData)
                    self.addInputCompoundData(compoundInstance)
            elif attr == "inputGenesData":
                geneInstance = None
                self.inputGenesData.clear()
                for (geneID, genData) in value.items():
                    geneInstance = Gene(geneID)
                    geneInstance.parseBSON(genData)
                    self.addInputGeneData(geneInstance)
            elif attr == "userID":
                setattr(self, attr, value if value != 'None' else None)
            elif attr in PAINTOMICS4_DICT_FIELDS or attr in PAINTOMICS4_LARGE_FIELDS:
                setattr(self, attr, value)
            elif not isinstance(value, dict):
                setattr(self, attr, value)

    def toBSON(self, recursive=True):
        """
        This function...

        @param recursive:
        @return:
        """
        bson = {}
        # A snapshot, not the live mapping: checkJobStatus writes
        # `jobArgs.maxEstimatedTotal` onto this same instance while the job is
        # running, and that attribute is not declared anywhere in the class --
        # so the first poll of a job *adds* a key rather than reassigning one.
        # Landing inside this walk, that is
        #     RuntimeError: dictionary changed size during iteration
        # and the store fails, losing a step the job had already computed.
        #
        # Narrow -- the walk is microseconds and only the first poll changes the
        # size -- but free to close, and taking a copy covers anything else that
        # sets a field on a job while it is being written out, not just this one
        # attribute.
        for attr, value in list(self.__dict__.items()):
            # Special case: "foundCompounds" is a list (not a dict) that contains recursive object data
            if not isinstance(value, dict) and (
                    ["svgDir", "inputDir", "outputDir", "temporalDir", "foundCompounds"].count(attr) == 0):
                bson[attr] = value

            elif attr in PAINTOMICS4_DICT_FIELDS:
                # Ensure all dict keys are strings for MongoDB compatibility
                if isinstance(value, dict):
                    bson[attr] = {str(k): v for k, v in value.items()}
                else:
                    bson[attr] = value

            elif recursive:
                if attr == "matchedPathways":
                    matchedPathways = {}
                    for (pathwayID, pathwayInstance) in value.items():
                        matchedPathways[pathwayID] = pathwayInstance.toBSON()
                    value = matchedPathways
                elif attr == "inputCompoundsData":
                    compounds = {}
                    for (compoundID, compoundInstance) in value.items():
                        compounds[compoundID] = compoundInstance.toBSON()
                    value = compounds
                elif attr == "inputGenesData":
                    genes = {}
                    for (geneID, geneInstance) in value.items():
                        genes[geneID] = geneInstance.toBSON()
                    value = genes
                elif attr == "foundCompounds":
                    compounds = []
                    for compoundCB in value:
                        compounds.append({
                            'mainCompounds': [compoundInstance.toBSON() for compoundInstance in
                                              compoundCB.getMainCompounds()],
                            'otherCompounds': [compoundInstance.toBSON() for compoundInstance in
                                               compoundCB.getOtherCompounds()]
                        })
                    value = compounds
                bson[attr] = value
        return bson

    def compundsClassification(self,metaboliteClassThreshold):

        import json, os
        from collections import defaultdict

        brPath = os.path.dirname(__file__) + "/../../common/br08001.json"

        # Load classification File
        with open(brPath, 'r') as f:
            temp = json.loads(f.read())

        # The compound -> neighbour map for this job's compounds, derived from
        # the organism's cached KEGG graph; {} when the species has neither KGML
        # nor a legacy hubData file -- already warned by the store.
        # One implementation, in getCompoundRegulateFeatures(), because the
        # recovery path needs the same derivation: the field is cache-only, so
        # reading the attribute there gave a reopened job nothing. Cleared first
        # so re-running step 2 with a different compound selection recomputes
        # rather than returning what the previous run cached.
        self.compoundRegulateFeatures = None
        compoundRegulateFeatures = self.getCompoundRegulateFeatures()

        temp2 = temp["children"]

        keggCompondsList = defaultdict(set)
        # The level-1 group each tested class belongs to. The walk below always
        # had it in hand as i['name'] and dropped it, which is why the nine
        # steroid classes reach the client as "18-Carbon atoms" .. "30-Carbon
        # atoms" with nothing saying they are steroids.
        classParents = {}
        for i in temp2:
            for j in i['children']:
                classParents[j['name']] = i['name']
                for w in j['children']:
                    for t in w['children']:
                        subt = t['name'].split()[0]
                        keggCompondsList[j['name']].add(subt)

        # Creat a non-redundant compound set
        compoundIDSet = set()

        for key, inputCompound in self.inputCompoundsData.items():
            compoundIDSet.add(key)

        # Only keep compounds in the classification file
        classificationDict = defaultdict(list)
        for compoundID in compoundIDSet:
            for key, IDs in keggCompondsList.items():
                if compoundID in IDs:
                    classificationDict[key].append(compoundID)

        # Prepare values to test category significance.
        # Distinct compounds, not the sum of the per-class lists: br08001 files
        # 33 of its 621 compounds under more than one level-2 class (ATP,
        # glycine, glutamate and aspartate among them), and summing the lists
        # counted each of those twice. This denominator is the null proportion
        # p0 under "generate automatically", so the inflation went straight
        # into every class's p-value.
        # A trial is a MEASURED feature, not a KEGG id. One name ticked under
        # two ids in step 2 (Alanine -> C00041 and C01401, both "Amino acids")
        # is one measurement carried twice, with perfectly correlated
        # relevance: counted as two it adds 2 to n and 2 to k and halves the
        # p-value for nothing (3/4 at p0 = .5 is .312; the same data as 6/8
        # is .145). Ids are collapsed on the feature they came from.
        def featureKeyOf(compoundID):
            comp = self.inputCompoundsData.get(compoundID)
            omicValue = comp.omicsValues[0] if comp and comp.omicsValues else None
            return (getattr(omicValue, "originalName", None)
                    or getattr(omicValue, "inputName", None)
                    or compoundID)

        totalFeatures = len({featureKeyOf(compoundID)
                             for compoundIDs in classificationDict.values()
                             for compoundID in compoundIDs})
        totalFeaturesInCategory = defaultdict(int)
        
        # Determine number of conditions
        nConditions = 1
        for feature in self.inputCompoundsData.values():
            if feature.omicsValues and isinstance(feature.omicsValues[0].relevant, list):
                nConditions = max(nConditions, len(feature.omicsValues[0].relevant))
        
        # totalRelevantFeaturesInCategory[conditionIndex][category] = count.
        # Per class, and it stays per class -- it is the k of that class's
        # binomial, where a compound filed under two classes genuinely counts
        # in both.
        totalRelevantFeaturesInCategory_cond = [defaultdict(int) for _ in range(nConditions)]
        # The OVERALL relevant count is a property of the compound set, not of
        # the classes, so it must count each compound once -- the same
        # correction as totalFeatures above. Both halves of the derived null
        # were inflated before, which partly cancelled; correcting only the
        # denominator drives p0 above 1.0, and binomtest rejects that, which
        # the bare except-branch below would have turned into p = 1.0 for
        # every class in the job.
        relevantCompoundIDs_cond = [set() for _ in range(nConditions)]
        # pValueInDict[conditionIndex][category] = pValue
        pValueInDict_cond = [{} for _ in range(nConditions)]

        for key, items in classificationDict.items():
            featuresInClass = set()
            relevantInClass = [set() for _ in range(nConditions)]
            for item in items:
                featureKey = featureKeyOf(item)
                featuresInClass.add(featureKey)
                comp = self.inputCompoundsData.get(item)
                if comp and comp.omicsValues:
                    rel = comp.omicsValues[0].relevant
                    if not isinstance(rel, list):
                        rel = [rel]
                    for c in range(min(nConditions, len(rel))):
                        if rel[c]:
                            relevantInClass[c].add(featureKey)
                            relevantCompoundIDs_cond[c].add(featureKey)
            totalFeaturesInCategory[key] = len(featuresInClass)
            for c in range(nConditions):
                if relevantInClass[c]:
                    totalRelevantFeaturesInCategory_cond[c][key] = len(relevantInClass[c])

        totalRelevantFeatures_cond = [len(compoundIDs) for compoundIDs in relevantCompoundIDs_cond]

        from scipy import stats
        threshold = metaboliteClassThreshold.get("thresholdMetaboliteClass")
        if threshold:
            try:
                threshold = float(threshold)
            except:
                threshold = None

        # Strictly below 1: at p0 = 1 every class scores p = 1.0 and the
        # analysis says nothing; the step-2 combo dropped 1.0 for that reason
        # and a typed "1" used to get through. Anything else falls back to
        # the derived null, and the caption says which ran.
        usingUserThreshold = bool(threshold and 0 < threshold < 1)
        nullProportion_cond = []

        for c in range(nConditions):
            totalRel = totalRelevantFeatures_cond[c]
            if usingUserThreshold:
                p_param = threshold
            else:
                p_param = totalRel / totalFeatures if totalFeatures > 0 else 0
            nullProportion_cond.append(p_param)

            for key in classificationDict:
                try:
                    pValueInDict_cond[c][key] = stats.binomtest(
                        totalRelevantFeaturesInCategory_cond[c].get(key, 0),
                        n=totalFeaturesInCategory.get(key),
                        p=p_param, alternative='greater').pvalue
                except Exception:
                    pValueInDict_cond[c][key] = 1.0

        featureSummary = [totalFeatures, totalRelevantFeatures_cond]

        # adjustPvalue[conditionIndex] = {category: adjustedPValue}
        adjustPvalue_cond = [adjustPvalues(p_dict) for p_dict in pValueInDict_cond]

        # Deliberately NOT rounded. round(p, 4) turned every adjusted p-value
        # below 1e-4 into exactly 0.0, so -log10(FDR) is +Inf for precisely the
        # classes with the strongest evidence -- a significance axis built on
        # this field breaks on its own best result. The client formats for
        # display; the transport keeps full precision.

        # Save the expression values
        valuesSet = set()
        for items in classificationDict.values():
            for item in items:
                valuesSet.add(item)

        expressionValueComp = defaultdict(list)
        mappingComp = {}
        for value in self.inputCompoundsData:
            expressionValueComp[value] = self.inputCompoundsData.get(value).omicsValues[0].values
            mappingComp[value] = self.inputCompoundsData.get(value).omicsValues[0].inputName

        # Save the expression values of Metabolites
        exprssionMetabolites = {}
        for i in self.inputCompoundsData:
            exprssionMetabolites[i] = self.inputCompoundsData[i].omicsValues[0].values

        self.mappingComp = dict(mappingComp)
        # For multi-condition, we store the lists/dicts of per-condition data
        self.pValueInDict = pValueInDict_cond
        self.classificationDict = dict(classificationDict)
        self.exprssionMetabolites = dict(exprssionMetabolites)
        self.adjustPvalue = adjustPvalue_cond
        self.totalRelevantFeaturesInCategory = totalRelevantFeaturesInCategory_cond
        self.featureSummary = featureSummary
        # Only the classes this job actually populated, so the client does not
        # have to carry all 36 br08001 names to look up nine steroid parents.
        self.classificationMeta = {
            "parents": {className: classParents.get(className, "")
                        for className in classificationDict},
            "nullProportion": nullProportion_cond,
            "thresholdSource": "user" if usingUserThreshold else "auto",
            # What the fixed number MEANS: a typed threshold is the alpha of
            # the user's own per-metabolite test (H0: no member of the class
            # changed), the derived one is the panel's relevant rate (H0: the
            # class is like the rest of the panel). Different hypotheses.
            "nullKind": "alpha" if usingUserThreshold else "relative",
            "alpha": threshold if usingUserThreshold else None,
        }
        self.compoundRegulateFeatures = compoundRegulateFeatures

        # The full analysis: every BRITE level, and the replicate-based
        # permutation test when the compound omic carries a design. Never
        # fatal -- the level-2 fields above are the contract every older
        # client and test relies on, and a failure here must not cost them.
        try:
            relevantByFeature = {}
            for key, items in classificationDict.items():
                for item in items:
                    comp = self.inputCompoundsData.get(item)
                    if not (comp and comp.omicsValues):
                        continue
                    rel = comp.omicsValues[0].relevant
                    rel = list(rel) if isinstance(rel, list) else [bool(rel)]
                    relevantByFeature.setdefault(featureKeyOf(item), [bool(r) for r in rel])
            self.classActivity = self._buildClassActivity(
                metaboliteClassThreshold, featureKeyOf, nConditions,
                relevantByFeature, nullProportion_cond,
                "alpha" if usingUserThreshold else "relative",
                threshold if usingUserThreshold else None)
            self.classificationMeta["test"] = self.classActivity.get("test")
        except Exception as ex:
            logging.exception("CLASS ACTIVITY: the multi-level analysis failed (%s); "
                              "the level-2 binomial result stands.", ex)
            self.classActivity = None

        return self.mappingComp, self.pValueInDict, self.classificationDict, self.exprssionMetabolites, self.adjustPvalue, self.totalRelevantFeaturesInCategory, self.featureSummary, self.compoundRegulateFeatures

    # ------------------------------------------------------------------
    # Class activity at every BRITE level
    # ------------------------------------------------------------------
    def _compoundOmicForClassActivity(self):
        """The compound omic whose values the class test reads, or None.

        The omic that carries a replicate design comes first: it is the one
        the permutation test can read, and which file the user uploaded
        first must not decide whether their design is honoured. Without a
        design anywhere, the omic of the first parsed compound, as before.
        """
        omics = getattr(self, "compoundBasedInputOmics", None) or []
        if not omics:
            return None
        for omic in omics:
            if self._replicateDesignFor(omic) is not None:
                return omic
        for compound in self.inputCompoundsData.values():
            if compound.omicsValues:
                name = getattr(compound.omicsValues[0], "omicName", None)
                for omic in omics:
                    if omic.get("omicName") == name:
                        return omic
                break
        return omics[0]

    def _measuredCompoundNames(self, inputOmic):
        """Every name in the first column of the omic's values file, lowercased.

        The job keeps only the compounds KEGG matched; the ones it did not
        match vanish from every later view. The class analysis lists them so
        a reader can see what never had a chance to join a class.
        """
        if not inputOmic:
            return []
        fileName = inputOmic.get("inputDataFile") or ""
        if not inputOmic.get("isExample", False) and fileName:
            fileName = os.path.join(self.getInputDir(), fileName)
        if not fileName or not os_path.isfile(fileName):
            return []
        names = []
        try:
            delimiter = Job.detect_delimiter(fileName)
            with open(fileName, "r", encoding="utf-8-sig", newline="") as handle:
                first = True
                for line in csv_reader(handle, delimiter=delimiter):
                    if not line or not line[0].strip():
                        continue
                    # The header is the first NON-BLANK row (a file may open
                    # with an empty line), and only if its second cell is not
                    # a number; a `#` row is a comment either way.
                    if first or line[0].lstrip().startswith("#"):
                        first = False
                        try:
                            float(line[1])
                        except (IndexError, ValueError):
                            continue
                    names.append(line[0].strip().lower())
        except Exception as ex:
            logging.warning("CLASS ACTIVITY: could not read %s for the unmatched list (%s)", fileName, ex)
        return names

    def _replicateDesignFor(self, inputOmic):
        """``(sampleHeader, mapping, replicateHeader)`` when the omic carries an
        applied design that covers its columns, else None."""
        if not inputOmic:
            return None
        mapping = inputOmic.get("replicateMapping") or []
        sampleHeader = inputOmic.get("sampleHeader") or []
        header = inputOmic.get("omicHeader") or []
        replicateHeader = header[1:] if len(header) > 1 else []
        if not mapping or not sampleHeader or len(mapping) != len(replicateHeader):
            return None
        # A design that collapses nothing (one column per condition) is still
        # returned: the replicate check in _buildClassActivity says WHY the
        # permutation test cannot run, instead of this returning None in
        # silence.
        return list(sampleHeader), [int(m) for m in mapping], list(replicateHeader)

    def _buildClassActivity(self, metaboliteClassThreshold, featureKeyOf, nConditions,
                            relevantByFeature, nullPerCondition, nullKind, alpha):
        """The class-activity payload the Step 3 ladder reads.

        Shape::

            {"test": "permutation" | "binomial", "nullKind": "alpha" | "relative",
             "alpha": float | None, "nullProportion": [per condition],
             "conditions": [labels of the direction columns],
             "levels": {"1": [entry, ...], "2": [...], "3": [...]},
             "features": {featureKey: {"name", "kegg", "relevant", "eff", ...}},
             "excluded": {"unmatched": [names], "unclassified": [featureKeys]},
             "factors": [...], "factor": id, "nPerm": int, "warnings": [str]}

        Every level entry carries the binomial counts (``k``, ``binomial.p``,
        ``binomial.bh`` per condition); under the permutation test it also
        carries ``meanF``, ``p``, ``bh``, ``nullQ95`` and the direction strip.
        """
        import zlib
        from src.common import ClassActivity as CA

        brite = CA.loadBrite()
        inputOmic = self._compoundOmicForClassActivity()
        testedOmicName = (inputOmic or {}).get("omicName")
        compoundIDsByFeature = defaultdict(list)
        valuesByFeature = {}
        namesByFeature = {}
        # The omics each feature was measured in. The permutation test reads
        # ONE omic's columns under ONE design, so only that omic's features
        # may have a row in Y: a second panel of the same width would
        # otherwise be fitted under labels that are not its own.
        omicsByFeature = defaultdict(set)
        for compoundID, compound in self.inputCompoundsData.items():
            if not compound.omicsValues:
                continue
            key = featureKeyOf(compoundID)
            compoundIDsByFeature[key].append(compoundID)
            # Attribute access, like featureKeyOf above: the OmicValue contract
            # is its fields, and the older class-activity tests drive this
            # method with bare stand-ins that carry the fields and no getters.
            omicValue = compound.omicsValues[0]
            omicName = getattr(omicValue, "omicName", None)
            omicsByFeature[key].add(omicName)
            if omicName == testedOmicName:
                # The tested omic's values win for a name measured in both.
                valuesByFeature[key] = list(getattr(omicValue, "values", None) or [])
            else:
                valuesByFeature.setdefault(key, list(getattr(omicValue, "values", None) or []))
            namesByFeature.setdefault(key, getattr(omicValue, "originalName", None)
                                      or getattr(omicValue, "inputName", None) or key)
            relevant = getattr(omicValue, "relevant", [])
            relevantByFeature.setdefault(key, [bool(r) for r in (relevant if isinstance(relevant, list) else [relevant])])
        levels = CA.membershipsByLevel(compoundIDsByFeature, brite)
        classified = set()
        for entry in levels[2].values():
            classified |= entry["members"]
        unclassified = sorted(k for k in compoundIDsByFeature if k not in classified)

        matchedNames = {k.lower() for k in compoundIDsByFeature}
        unmatched = [n for n in self._measuredCompoundNames(inputOmic) if n not in matchedNames]

        result = {
            "test": "binomial", "nullKind": nullKind, "alpha": alpha,
            "nullProportion": list(nullPerCondition), "nConditions": nConditions,
            "conditions": [], "levels": {}, "features": {}, "warnings": [],
            "excluded": {"unmatched": unmatched, "unclassified": unclassified},
            "levelNames": {str(k): v for k, v in CA.LEVEL_NAMES.items()},
            "factors": [], "factor": None, "nPerm": 0,
        }

        # Direction with no replicates: the values themselves (ratios), one
        # column per condition of the values file.
        header = (inputOmic or {}).get("omicHeader") or []
        conditionLabels = [str(h) for h in header[1:]]
        for key in compoundIDsByFeature:
            values = [CA._finite(v) for v in valuesByFeature.get(key, [])]
            result["features"][key] = {
                "name": namesByFeature[key], "kegg": sorted(compoundIDsByFeature[key]),
                "relevant": relevantByFeature.get(key, []), "values": values,
            }

        binomialByLevel = {level: CA.binomialClassTest(levels[level], relevantByFeature,
                                                       nConditions, nullPerCondition)
                           for level in (1, 2, 3)}

        # ---- the permutation test, when the omic carries replicates + design
        design = self._replicateDesignFor(inputOmic)
        perm = None
        factor = None
        if design is not None:
            sampleHeader, mapping, replicateHeader = design
            factors = CA.designFactors(sampleHeader, mapping)
            wanted = (metaboliteClassThreshold or {}).get("thresholdMetaboliteClassFactor")
            factor = next((f for f in factors if f["id"] == wanted), None) \
                or min(factors, key=lambda f: len(f["levels"]))
            result["factors"] = [{"id": f["id"], "label": f["label"], "levels": f["levels"],
                                  "nStrata": len(f["strataLabels"])} for f in factors]
            result["factor"] = factor["id"]
            # Every level x stratum cell needs two replicates or there is no
            # residual variance to test against; and a factor with one level
            # has nothing to compare (a design mapping every column to one
            # condition passed the replicate count with 36 "replicates").
            cells = defaultdict(int)
            for lvl, st in zip(factor["columnLevel"], factor["strata"]):
                cells[(lvl, st)] += 1
            thin = [(factor["levels"][lvl], factor["strataLabels"][st])
                    for (lvl, st), count in cells.items() if count < 2]
            if len(factor["levels"]) < 2:
                result["warnings"].append(
                    "The design names a single condition (%s), so there is nothing to compare. "
                    "The binomial test on your relevant list ran instead."
                    % ", ".join(factor["levels"]))
            elif thin:
                result["warnings"].append(
                    "The permutation test needs at least two replicates per condition; "
                    + ", ".join(("%s %s" % (l, s)).strip() for l, s in thin[:4])
                    + (" and more" if len(thin) > 4 else "")
                    + " have one. The binomial test on your relevant list ran instead.")
            else:
                others = [o.get("omicName") for o in (getattr(self, "compoundBasedInputOmics", None) or [])
                          if o is not inputOmic and o.get("omicName") != inputOmic.get("omicName")]
                if others:
                    # One design, one omic: the test reads this omic's columns
                    # and only its features get a row in Y below, so a second
                    # compound omic's classes carry the binomial result only.
                    result["warnings"].append(
                        "The permutation test reads %s; %s carries its own columns and was not "
                        "tested -- its classes show the binomial result only."
                        % (inputOmic.get("omicName"), ", ".join(str(o) for o in others)))
                keys = sorted(k for k in compoundIDsByFeature if testedOmicName in omicsByFeature[k])
                rows = {k: i for i, k in enumerate(keys)}
                width = len(mapping)
                Y = np.full((len(keys), width), np.nan)
                for k in keys:
                    values = valuesByFeature.get(k) or []
                    if len(values) == width:
                        Y[rows[k]] = [v if isinstance(v, (int, float)) and math.isfinite(v)
                                      else np.nan for v in values]
                nPerm = max(100, int(CLASS_ACTIVITY_PERMUTATIONS))
                seed = zlib.crc32(str(getattr(self, "jobID", "")).encode("utf-8"))
                # "Responds individually" at the cut-off the reader chose for
                # their own list, when they chose one; 0.05 otherwise.
                sigAlpha = alpha if isinstance(alpha, (int, float)) and 0 < alpha < 1 else 0.05
                perm = CA.permutationClassTest(Y, factor, levels, rows, nPerm=nPerm, seed=seed, alpha=sigAlpha)
                result.update({"test": "permutation", "nPerm": nPerm,
                               "conditions": list(perm["effects"]["labels"]),
                               "transformed": bool(perm["transformed"]),
                               "design": {"samples": width, "conditions": len(sampleHeader),
                                          "strata": factor["strataLabels"],
                                          "levels": factor["levels"]}})
                if perm["transformed"]:
                    result["warnings"].append(
                        "Values looked like raw intensities (all positive, spanning more than "
                        "50-fold) and were log2-transformed before testing.")
                feats = perm["features"]
                effects = perm["effects"]["values"]
                for k, i in rows.items():
                    entry = result["features"][k]
                    entry.update({
                        "F": CA._finite(feats["F"][i]), "p": CA._finite(feats["p"][i]),
                        "bh": CA._finite(feats["bh"][i]),
                        "sig": bool(np.isfinite(feats["bh"][i]) and feats["bh"][i] < sigAlpha),
                        "eff": [CA._finite(v) for v in (effects[i].tolist() if effects.shape[1] else [])],
                    })
                    # The client paints the strip from `eff` on this route; the
                    # replicate columns themselves already live on the omic
                    # values, and a wide panel would store them twice.
                    entry.pop("values", None)
        if perm is None:
            result["conditions"] = conditionLabels

        # ---- assemble the levels
        for level in (1, 2, 3):
            entries = []
            for key, cls in levels[level].items():
                binom = binomialByLevel[level][key]
                members = sorted(cls["members"])
                entry = {
                    "key": key, "name": cls["name"], "parent": cls["parent"], "path": cls["path"],
                    "level": level, "n": len(members), "members": members,
                    "k": binom["k"], "binomial": {"p": binom["p"], "bh": binom["bh"]},
                }
                if perm is not None:
                    p = perm["levels"][level][key]
                    entry.update({"meanF": CA._finite(p["meanF"]), "p": CA._finite(p["p"]),
                                  "bh": CA._finite(p["bh"]), "nullQ95": CA._finite(p["nullQ95"]),
                                  "nullMedian": CA._finite(p["nullMedian"]),
                                  "nullMax": CA._finite(p["nullMax"]), "nsig": p["nsig"],
                                  "tested": p["tested"], "eff": p["eff"], "E": p["E"]})
                else:
                    # Direction from the ratios: per condition, the mean of
                    # the members' values.
                    columns = list(zip(*[valuesByFeature.get(m) or [] for m in members
                                         if len(valuesByFeature.get(m) or []) == len(conditionLabels)]))
                    eff = []
                    for column in columns:
                        finite = [v for v in column if isinstance(v, (int, float)) and math.isfinite(v)]
                        eff.append(float(np.mean(finite)) if finite else None)
                    absolute = [abs(v) for v in eff if v is not None]
                    entry.update({"eff": eff, "E": float(np.mean(absolute)) if absolute else None})
                entries.append(entry)
            result["levels"][str(level)] = entries
        return result

    def getCompoundRegulateFeatures(self):
        """
        {compoundID: {"1": [geneID, ...], ..., "4": [...]}} for this job's
        compounds -- the input to Step 4's "Neighbouring features" panel and to
        the Step 3 hub-analysis Paint column.

        Derived, not stored. The field is in PAINTOMICS4_LARGE_FIELDS (2.67 MB
        on the six-omic example), so it is never written to MongoDB and the
        attribute is None on any process that did not run step 2 itself -- i.e.
        on every job opened from its URL after a restart. Returning the
        attribute there made both panels dead, silently.

        Nothing needs storing: the neighbourhoods are a pure function of the
        organism's KEGG graph (static, cached per process by
        src.common.KeggGraph.store) intersected with `inputCompoundsData`, and
        inputCompoundsData is persisted. This is the same arrangement
        getGlobalExpressionData() has always had, which is why that field -- in
        the same LARGE_FIELDS set -- survives a reopen and this one did not.

        A job with no compounds returns {} without deriving anything, and
        gene-only jobs are the common case.

        The dict check is the whole point of the guard, not defensiveness:
        DAO.adaptBSON turns every None leaf into the STRING "None" -- documented
        there, depended on across the database -- so a reopened job arrives with
        `compoundRegulateFeatures == "None"`, which is truthy. Testing
        truthiness alone returned that string, the servlet's _as_dict() turned
        it into {}, and the recovery path was exactly as dead as before.
        Measured: `repr(job.compoundRegulateFeatures)` after loadJobInstance is
        `'None'`, four characters.
        """
        if isinstance(self.compoundRegulateFeatures, dict) and self.compoundRegulateFeatures:
            return self.compoundRegulateFeatures

        inputCompoundIDs = set((self.inputCompoundsData or {}).keys())
        if not inputCompoundIDs:
            return {}

        from src.common.KeggGraph import store
        graph = store.get_graph(self.organism)
        if graph is None:
            return {}

        # Cumulative balls keyed by radius as a string -- the shape the Step 3
        # Paint handler and the Step 4 Neighbouring-features panel already read.
        result = {}
        for compoundID in inputCompoundIDs:
            rings = graph.rings(compoundID, 4)
            if not any(rings):
                continue
            cumulative, seen = {}, []
            for radius, ring in enumerate(rings, start=1):
                seen = seen + ring
                cumulative[str(radius)] = list(seen)
            result[compoundID] = cumulative

        if not result:
            logging.warning("HUB ANALYSIS - none of the %d input compounds "
                            "appear in the %s graph; check that both use KEGG "
                            "compound IDs.", len(inputCompoundIDs), self.organism)
        self.compoundRegulateFeatures = result
        return self.compoundRegulateFeatures

    @staticmethod
    def _addSampleMeans(expressionDetail, omicValue):
        """Copy the per-condition means onto a globalExpressionData entry.

        A design or the replicate detector leaves ``sampleValues`` /
        ``sampleRelevant`` on the omic value (applyReplicateMappingForOmic),
        and every Step 3 chart prefers them in "samples" mode. Built without
        them, the class activity members' heatmap drew one cell per replicate
        -- 36 columns of "Condition n" on a job whose design names 12. Only
        set when present: a job without a mapping keeps the payload it always
        had, byte for byte (tests/baseline compares it).
        """
        for field in ("sampleValues", "sampleRelevant"):
            value = getattr(omicValue, field, None)
            if value is not None:
                expressionDetail[field] = value

    def getGlobalExpressionData(self):
        globalExpressionDataGene = defaultdict(dict)
        globalExpressionDataComp = defaultdict(dict)
        globalExpressionData = defaultdict(dict)


        for j in self.inputCompoundsData:
            expressionID = self.inputCompoundsData[j].ID
            expressionDetail = {
                'keggName': self.inputCompoundsData[j].name,
                'inputName': self.inputCompoundsData[j].omicsValues[0].inputName,
                # The omic these values came from. globalExpressionData is built
                # from omicsValues[0] and is therefore ONE omic, but it never said
                # which, so the client could not look up the distribution summary
                # that omic's own heatmap is scaled against -- and a node could not
                # be painted in the same colours as the figure below it.
                'omicName': self.inputCompoundsData[j].omicsValues[0].omicName,
                'originalName': self.inputCompoundsData[j].omicsValues[0].originalName,
                'relevant': self.inputCompoundsData[j].omicsValues[0].relevant,
                'relevantAssociation': self.inputCompoundsData[j].omicsValues[0].relevantAssociation,
                'values': self.inputCompoundsData[j].omicsValues[0].values
            }
            self._addSampleMeans(expressionDetail, self.inputCompoundsData[j].omicsValues[0])
            globalExpressionDataComp[expressionID] = expressionDetail

        for i in self.inputGenesData:
            expressionID = self.inputGenesData[i].ID
            expressionDetail = {
                'keggName': self.inputGenesData[i].name,
                'inputName': self.inputGenesData[i].omicsValues[0].inputName,
                # The omic these values came from. globalExpressionData is built
                # from omicsValues[0] and is therefore ONE omic, but it never said
                # which, so the client could not look up the distribution summary
                # that omic's own heatmap is scaled against -- and a node could not
                # be painted in the same colours as the figure below it.
                'omicName': self.inputGenesData[i].omicsValues[0].omicName,
                'originalName': self.inputGenesData[i].omicsValues[0].originalName,
                'relevant': self.inputGenesData[i].omicsValues[0].relevant,
                'relevantAssociation': self.inputGenesData[i].omicsValues[0].relevantAssociation,
                'values': self.inputGenesData[i].omicsValues[0].values
            }
            self._addSampleMeans(expressionDetail, self.inputGenesData[i].omicsValues[0])
            globalExpressionDataGene[expressionID] = expressionDetail

        globalExpressionData["inputGene"] = globalExpressionDataGene
        globalExpressionData["inputCompound"] = globalExpressionDataComp
        self.globalExpressionData = globalExpressionData
        return self.globalExpressionData

    def hubAnalysis(self, ROOT_DIRECTORY=None):
        """Metabolite hub analysis. Pure Python since 2026-08.

        Was: write two CSVs, fork `Rscript hubAnalysis.R`, read a headerless
        8-column TSV back. The R side re-read a 13 MB CSV and 1,865 .RData files
        on every job -- I/O proportional to the species, not to the dataset --
        for a measured 2.7-3.0 s. The graph is derived from KGML and cached per
        organism now, so a warm job costs ~0.09 s.

        ROOT_DIRECTORY is unused -- there is no script to locate any more -- and
        is kept optional only so the step-2 call site need not change.
        """
        from src.common.KeggGraph import store
        from src.common.KeggGraph.scorer import score

        # ANY gene-based omic counts, and DE in any one of them makes the gene
        # relevant.
        #
        # This asked for `omicName == 'Gene expression'` and skipped everything
        # else. That name is only the default the upload form suggests for the
        # first omic: a job whose omics are called "RNA-seq" and "Proteomics"
        # left `measured` empty and scored the binomial test on compounds
        # alone -- a wrong table, not an absent one. On a job that DOES have an
        # omic by that name, three quarters of this one's gene data were being
        # ignored by a test whose whole claim is "how much of the differential
        # expression sits near this metabolite".
        #
        # isRelevant() rather than testing `relevant` for truth: it is a LIST,
        # and a list of all-False is truthy. It happens to be [] for non-DE
        # features today, so the old form was right by accident.
        measured, relevant = set(), set()
        for geneID in self.inputGenesData:
            values = self.inputGenesData[geneID].omicsValues or []
            if not values:
                continue
            measured.add(geneID)
            for omicValue in values:
                if omicValue.isRelevant() or omicValue.isRelevantAssociation():
                    relevant.add(geneID)
                    break
        for compoundID in self.inputCompoundsData:
            values = self.inputCompoundsData[compoundID].omicsValues or []
            if not values:
                continue
            measured.add(compoundID)
            for omicValue in values:
                if omicValue.isRelevant() or omicValue.isRelevantAssociation():
                    relevant.add(compoundID)
                    break

        if not relevant:
            return False

        graph = store.get_graph(self.organism)
        if graph is None:
            logging.warning("HUB ANALYSIS - no interaction graph available for "
                            "%s; skipping hub analysis.", self.organism)
            return False

        try:
            rows = score(graph, measured, relevant)
        except Exception as ex:
            # An enhancement panel must not take down the pathway results that
            # step 2 exists to produce.
            logging.warning("HUB ANALYSIS - failed for %s (%s); continuing "
                            "without hub results.", self.organism, str(ex))
            return False

        if not rows:
            return False
        self.hubAnalysisResult = {index: row for index, row in enumerate(rows)}
        return self.hubAnalysisResult


    def parseRegulationPerCondition(self):
        """Load MORE's RegulationPerCondition table for the Step 3 panel.

        The R side writes one combined file per MORE run named
        MORE_rpc_<YYYYMMDDHHMM>.tab into the user-scoped inputData/ directory
        (see runMORE.R and MOREServlet.fromMOREtoGenes_STEP2). We detect MORE
        was used by scanning this job's geneBasedInputOmics for any file
        matching MORE_<kind>_<omic>_<date>.tab, extracting the date_seed,
        and resolving the rpc filename deterministically.

        Self-skips (leaves self.regulationPerConditionData = None) if MORE
        wasn't run or the file is missing — the Step 3 client panel hides
        itself in that case.

        Memory note: dtype=str on read avoids pandas' default object/float64
        churn; Group_* columns are converted to numeric vectorised. itertuples
        is used instead of iterrows to avoid the per-row Series allocation
        forbidden by the project's CLAUDE.md guidance.
        """
        import re

        # 1. Detect MORE-produced filenames already on this job.
        pattern = re.compile(
            r"^MORE_(?:output|relevant_pairs|relevant_assoc|relevant_reg)_.+_(\d{12})\.tab$"
        )
        date_seed = None
        for omic in self.geneBasedInputOmics:
            for key in ("inputDataFile", "relevantFeaturesFile",
                        "associationsFile", "relevantAssociationsFile"):
                fname = omic.get(key)
                if not fname:
                    continue
                m = pattern.match(os_path.basename(fname))
                if m:
                    date_seed = m.group(1)
                    break
            if date_seed:
                break

        if not date_seed:
            return  # No MORE in this job.

        rpc_path = os_path.join(self.getInputDir(), f"MORE_rpc_{date_seed}.tab")
        if not os_path.exists(rpc_path):
            logging.warning(
                f"MORE rpc file expected but missing: {rpc_path}"
            )
            return

        # Optional sidecar with the MORE filter settings the user picked at
        # configuration time (filter_r2, alpha, vip, method). Written by
        # MOREServlet.fromMOREtoGenes_STEP2. Absent for rpc files produced
        # before that sidecar existed — the Step-3 view falls back to defaults.
        filters_meta = None
        filters_path = os_path.join(
            self.getInputDir(), f"MORE_filters_{date_seed}.json"
        )
        if os_path.exists(filters_path):
            try:
                import json
                with open(filters_path) as fh:
                    filters_meta = json.load(fh)
            except (OSError, ValueError) as ex:
                logging.warning(
                    f"MORE filters sidecar present but unreadable "
                    f"({filters_path}): {ex}"
                )

        # 2. Parse (keep pandas import local — it's heavy and not used elsewhere
        # in this class).
        try:
            import pandas as pd
        except ImportError:
            logging.error(
                "pandas not available; cannot parse RegulationPerCondition."
            )
            return

        try:
            df = pd.read_csv(
                rpc_path, sep="\t", dtype=str,
                keep_default_na=False, na_values=[""]
            )
        except Exception as ex:
            logging.error(f"Failed to parse RegulationPerCondition file: {ex}")
            return

        if df.empty:
            self.regulationPerConditionData = {
                "columns": list(df.columns), "rows": [], "truncated": False,
                "filters": filters_meta,
            }
            return

        # 3. Vectorised numeric coercion for the Group_* columns only.
        group_cols = [c for c in df.columns if c.startswith("Group_")]
        for col in group_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 4. Sanitise: drop rows missing either of the two mandatory fields.
        # The Step-3 panel joins on them, so a blank one renders an anonymous
        # node/edge rather than failing visibly.
        #
        # notna() is load-bearing. read_csv above passes na_values=[""], which
        # overrides keep_default_na=False *for empty strings specifically*, so a
        # blank cell arrives as NaN and not as "". `NaN != ""` is True, so the
        # bare inequality this used to be kept every offending row -- the whole
        # step was a no-op. Both checks are kept so the filter holds whichever
        # representation pandas yields.
        if "targetF" in df.columns and "regulator" in df.columns:
            df = df[df["targetF"].notna() & (df["targetF"] != "")
                    & df["regulator"].notna() & (df["regulator"] != "")]

        # 5. Defensive cap so a runaway model can't bloat the Mongo doc.
        MAX_ROWS = 100_000
        truncated = len(df) > MAX_ROWS
        if truncated:
            df = df.head(MAX_ROWS)

        # 6. JSON-clean: distinguish missing cells by column type.
        #   - String columns (Target/Regulator/Omic/Area/Representative): emit ""
        #     for NaN. We can't use None here because Paintomics' DAO layer
        #     (DAO.adaptBSON -> Util.adapt_string) runs str() on every non-
        #     collection, non-numeric value on read, turning Python None into
        #     the literal string "None" — confusing in the UI and indistinguish-
        #     able from a real value.
        #   - Numeric columns (Group_*): emit None for NaN. Round-tripping to
        #     "None" through adaptBSON would still happen for these, but NaN
        #     coefficients are rare/absent in practice and the frontend renderer
        #     handles both None and "None" as missing.
        # itertuples beats iterrows ~30x (CLAUDE.md performance contract).
        numeric_col_idxs = {
            i for i, c in enumerate(df.columns) if c.startswith("Group_")
        }
        rows = []
        for record in df.itertuples(index=False, name=None):
            row = []
            for i, v in enumerate(record):
                if isinstance(v, float) and math.isnan(v):
                    row.append(None if i in numeric_col_idxs else "")
                else:
                    row.append(v)
            rows.append(row)

        # 7. Build a symbol lookup for Target/Regulator columns.
        #
        # Two distinct ID populations live in the rpc:
        #   - targetF column: target gene IDs in whatever shape R wrote
        #     them out — which is the rownames of the user-uploaded gene
        #     expression file, i.e. the user-input form (Ensembl for mmu,
        #     AGI for ath, …). NOT necessarily the KEGG canonical ID.
        #   - regulator column: TF / miRNA / methylation IDs, again in
        #     the user-input form after runMORE.R's prefix-strip.
        #
        # On the Python side, self.inputGenesData is keyed by Gene.ID —
        # the KEGG canonical (EntrezGene for mmu, AGI for ath, …). The
        # user-input form for each feature lives one level deeper, on
        # OmicValue.inputName. For organisms where Gene.ID and the user
        # upload happen to be the same string (AGI-style species), the
        # canonical-keyed map hits the rpc directly. For organisms where
        # they diverge (mmu, hsa, rno, dre — anything where KEGG uses
        # EntrezGene but biologists upload Ensembl/Symbol/RefSeq), the
        # canonical-keyed entry never matches the rpc's targetF and
        # every target rendered as a raw Ensembl ID. We therefore emit
        # TWO entries per gene where applicable: one keyed by Gene.ID
        # (canonical) and one keyed by OmicValue.inputName (user form).
        #
        # Regulator symbols ride on a separate path: each Gene's
        # omicsValues[i] carries the regulator metadata for a
        # `target:::regulator` row (see Feature.OmicValue.isRegulator,
        # regulatorID, originalName, inputName populated in
        # Job.parseGeneBasedFiles). Earlier versions only walked the
        # top-level dict, so regulator symbols were missed wholesale
        # (typically >50% of the rpc IDs).
        #
        # Restrict the emitted map to IDs that actually appear in the rpc —
        # keeps the payload bounded for organisms with huge inputGenesData.
        symbols = {}
        try:
            genes = self.inputGenesData or {}

            if "targetF" in df.columns and "regulator" in df.columns:
                # Collect rpc IDs once, upper-cased. The rpc is verbatim from
                # R, so we case-fold here and lookups happen on the client
                # with the same fold.
                rpc_ids = set()
                rpc_ids.update(df["targetF"].astype(str).str.upper().unique())
                rpc_ids.update(df["regulator"].astype(str).str.upper().unique())
                # Empty-string can sneak in via sanitised NaN cells; drop it
                # so we never emit a "" -> "" entry.
                rpc_ids.discard("")

                # 7.a Top-level pass: targets (and any regulator that also
                # happens to be a gene-expression feature). Emits both
                # canonical and user-input forms — see header comment.
                # 7.b Inner pass: regulator omicsValues. One gene can carry
                # many regulator rows when a single target has many TFs/miRNAs
                # mapped to it; we still scan each omicsValue once. Worst
                # case ~rows-in-rpc iterations, which the 100k cap above
                # already bounds.
                for gene_id, gene in genes.items():
                    name = gene.getName() if hasattr(gene, "getName") else None
                    name_up = str(name).upper() if name else ""

                    # --- 7.a-i canonical-keyed symbol (Gene.ID -> name) ---
                    if gene_id and name:
                        gid_up = str(gene_id).upper()
                        # Skip identity mappings — FeatureNamesToKeggIDsMapper
                        # leaves Gene.name == ID when no symbol was found.
                        if (gid_up in rpc_ids
                                and gid_up not in symbols
                                and name_up != gid_up):
                            symbols[gid_up] = name

                    # --- 7.a-ii user-input-keyed symbol -------------------
                    # OmicValue.inputName holds the target ID as the user
                    # typed it (e.g. "ENSMUSG00000029650" while Gene.ID is
                    # "71706" for mmu). The rpc's targetF column carries
                    # this user-input form for any non-AGI-style species,
                    # so without this entry the lookup never hits and the
                    # Target column renders as a raw Ensembl/RefSeq ID.
                    # We sweep all omicValues — regulator slots also carry
                    # the target's inputName (see comment in 7.b), so
                    # including them is harmless and catches targets that
                    # only appear via regulator associations.
                    if name:
                        omic_values = getattr(gene, "omicsValues", None) or []
                        for omic_val in omic_values:
                            input_name = getattr(omic_val, "inputName", "") or ""
                            if not input_name:
                                continue
                            in_up = input_name.upper()
                            if (in_up in rpc_ids
                                    and in_up not in symbols
                                    and in_up != name_up):
                                symbols[in_up] = name

                    # --- 7.b regulator symbols from omicsValues ---------
                    # Important: on a regulator OmicValue (see
                    # Job.parseGeneBasedFiles), the field naming is misleading
                    # for our purposes:
                    #   - omic_val.inputName  == TARGET id (columnID[0])
                    #   - omic_val.originalName == regulator's display symbol
                    #     (or, if no symbol was resolved, the raw regulator ID
                    #     — i.e. an identity mapping with regulatorID == "")
                    #   - omic_val.regulatorID == canonical regulator ID
                    #     (e.g. AGI) when the mapper resolved a symbol;
                    #     empty string otherwise.
                    # The rpc's `regulator` column carries the regulator's
                    # canonical/raw form (runMORE.R prefix-strips back to the
                    # user-uploaded shape, which equals regulatorID when the
                    # user uploaded canonical IDs — the common case for AGI /
                    # Ensembl-style organisms).
                    # We therefore map ONLY regulatorID -> originalName. Using
                    # inputName here would map the TARGET id to the regulator's
                    # symbol — surfacing rows like "AT1G19000 (AT4G01310)" in
                    # the Target column.
                    omic_values = getattr(gene, "omicsValues", None) or []
                    for omic_val in omic_values:
                        if not getattr(omic_val, "isRegulator", False):
                            continue
                        symbol = getattr(omic_val, "originalName", "") or ""
                        reg_id = getattr(omic_val, "regulatorID", "") or ""
                        if not symbol or not reg_id:
                            # Unresolved regulator (regulatorID == "") would
                            # produce an identity entry only — skip.
                            continue
                        reg_up = reg_id.upper()
                        if (reg_up in rpc_ids
                                and reg_up != symbol.upper()
                                and reg_up not in symbols):
                            symbols[reg_up] = symbol
        except Exception as ex:
            # Symbol lookup is purely cosmetic; never let it kill the panel.
            logging.warning(f"RegulationPerCondition symbol lookup failed: {ex}")

        self.regulationPerConditionData = {
            "columns": list(df.columns),
            "rows": rows,
            "truncated": truncated,
            "symbols": symbols,
            "filters": filters_meta,
        }
        logging.info(
            f"Parsed RegulationPerCondition: {len(rows)} rows, "
            f"{len(df.columns)} cols, {len(symbols)} symbols, "
            f"truncated={truncated}"
        )
