#!/usr/bin/env python3
"""Read the bundled example catalogue and wire a scenario into a job.

Before this module there was exactly one example, and it was hardcoded in six
places: three servlets each rebuilt filenames by mangling an omic name
("DNase-seq" -> "dnase_values.tab"), and three ExtJS `setExampleMode` functions
each repeated the literal paths. Adding a second example meant editing all six,
so nobody did, and whole features -- MORE, per-condition relevance -- shipped
with no example that reached them.

Now `examplefiles/datasets/manifest.json` is the single source of truth. The
servlets ask this module for a scenario; the client asks it for the list.

Deliberately free of Flask imports so it can be unit-tested directly, and free
of any write path: it only ever reads the bundled tree.

Failure policy
--------------
Example mode disappearing is worse than an example being stale, so every
failure degrades rather than raises:

  * no manifest, unreadable manifest, wrong version -> LEGACY_SCENARIOS, the
    hardcoded bundle this module replaced
  * a scenario whose files are missing -> omitted from listScenarios, so the
    picker cannot offer something that would fail
  * an unknown scenario id -> UserWarning naming the valid ids, which the
    servlets' error handling already renders as a readable message

The one thing it will not do is silently substitute a different scenario for
the one that was asked for.
"""
import json
import logging
import os
import re
import threading

from src.common.ServerErrorManager import quotable


MANIFEST_NAME = os.path.join("datasets", "manifest.json")

# Bumped only for a breaking schema change; an older server reading a newer
# manifest falls back rather than misreading it.
SUPPORTED_VERSION = 1

# What the three servlets hardcoded before the manifest existed. Used only when
# the manifest cannot be read at all -- a deploy that shipped the code but not
# the data still has a working "Load example".
LEGACY_SCENARIOS = {
    "stategra-multiomics": {
        "id": "stategra-multiomics",
        "title": "STATegra — real mouse Ikaros time course (6 omics)",
        "summary": "The bundled example (legacy fallback: manifest unavailable).",
        "pipeline": "pathway-acquisition",
        "organism": "mmu",
        "databases": ["KEGG", "Reactome"],
        "simulated": False,
        "omics": [
            {"omicName": "Gene expression", "omicType": "gene", "enrichment": "genes",
             "dataFile": "gene_expression_values.tab",
             "relevantFile": "gene_expression_relevant.tab"},
            {"omicName": "Metabolomics", "omicType": "compound", "enrichment": "features",
             "dataFile": "metabolomics_values.tab",
             "relevantFile": "metabolomics_relevant.tab"},
            {"omicName": "Proteomics", "omicType": "gene", "enrichment": "features",
             "dataFile": "proteomics_values.tab",
             "relevantFile": "proteomics_relevant.tab"},
            {"omicName": "miRNA-seq", "omicType": "gene", "enrichment": "genes",
             "dataFile": "mirna_values.tab", "relevantFile": "mirna_relevant.tab"},
            {"omicName": "DNase-seq", "omicType": "gene", "enrichment": "genes",
             "dataFile": "dnase_values.tab", "relevantFile": "dnase_relevant.tab"},
            {"omicName": "Transcription factor", "omicType": "gene", "enrichment": "genes",
             "dataFile": "transcription_factor_values.tab",
             "relevantFile": "transcription_factor_relevant.tab"},
        ],
        "references": [],
    },
    "stategra-regions": {
        "id": "stategra-regions",
        "title": "STATegra — DNase regions",
        "summary": "The bundled region example (legacy fallback).",
        "pipeline": "regions2genes",
        "organism": "mmu",
        "databases": ["KEGG"],
        "simulated": False,
        "omics": [
            {"omicName": "DNase unmapped", "omicType": "region", "enrichment": "genes",
             "dataFile": "dnase_unmapped_values.tab",
             "relevantFile": "dnase_unmapped_relevant.tab"},
        ],
        "references": [{"omicName": "DNase unmapped", "fileType": "Reference file",
                        "dataFile": "GTF/sorted_mmu.gtf"}],
    },
    "stategra-mirna": {
        "id": "stategra-mirna",
        "title": "STATegra — miRNA to genes",
        "summary": "The bundled miRNA example (legacy fallback).",
        "pipeline": "mirna2genes",
        "organism": "mmu",
        "databases": ["KEGG"],
        "simulated": False,
        "omics": [
            {"omicName": "miRNA unmapped", "omicType": "gene", "enrichment": "genes",
             "role": "regulator", "dataFile": "mirna_unmapped_values.tab",
             "relevantFile": "mirna_unmapped_relevant.tab"},
            {"omicName": "Gene expression", "omicType": "gene", "enrichment": "genes",
             "role": "target", "dataFile": "gene_expression_values.tab"},
        ],
        "references": [{"omicName": "miRNA unmapped", "fileType": "Reference file",
                        "dataFile": "mmu_mirBase_to_ensembl.tab"}],
    },
}

LEGACY_DEFAULT = "stategra-multiomics"

_cache = {}
_cacheLock = threading.Lock()


class UnknownScenario(UserWarning):
    """Raised for an id that is not in the catalogue, naming the valid ones.

    Subclasses UserWarning because ServerErrorManager renders that as the
    message the user sees, rather than as an internal error.
    """


# ---------------------------------------------------------------------------
# Reading the manifest
# ---------------------------------------------------------------------------

def loadManifest(exampleFilesDir):
    """The parsed manifest, cached until the file's mtime or size changes.

    Cached because every step-1 submission in example mode reads it, and it is
    re-read on change because a developer regenerating the datasets should not
    have to restart the server to see them.

    The cache is keyed on (path, mtime, size) rather than path alone: mtime
    alone has one-second granularity on some filesystems, so two regenerations
    within the same second could otherwise serve a stale catalogue.
    """
    path = os.path.join(exampleFilesDir, MANIFEST_NAME)
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime, stat.st_size)
    except OSError:
        return _fallbackManifest("no manifest at %s" % path)

    with _cacheLock:
        cached = _cache.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]

    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (ValueError, OSError) as error:
        return _fallbackManifest("could not read %s: %s" % (path, error))

    version = manifest.get("version")
    if version != SUPPORTED_VERSION:
        return _fallbackManifest(
            "manifest at %s declares version %r, this server supports %d"
            % (path, version, SUPPORTED_VERSION))

    if not isinstance(manifest.get("scenarios"), list) or not manifest["scenarios"]:
        return _fallbackManifest("manifest at %s lists no scenarios" % path)

    with _cacheLock:
        _cache[path] = (key, manifest)
    return manifest


def _fallbackManifest(reason):
    logging.warning("EXAMPLE DATASETS - %s; falling back to the legacy bundle", reason)
    return {
        "version": SUPPORTED_VERSION,
        "defaultScenario": LEGACY_DEFAULT,
        "isFallback": True,
        "scenarios": [LEGACY_SCENARIOS[key] for key in sorted(LEGACY_SCENARIOS)],
    }


def clearCache():
    """For tests, which write manifests faster than mtime granularity."""
    with _cacheLock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------

def defaultScenarioId(exampleFilesDir):
    return loadManifest(exampleFilesDir).get("defaultScenario", LEGACY_DEFAULT)


def defaultScenarioFor(exampleFilesDir, pipeline):
    """The scenario a bare `/…/example` should load for one entry point.

    The manifest's `defaultScenario` is the *global* default and belongs to the
    pathway-acquisition pipeline, so `/dm_fromBEDtoGenes/example` cannot use it:
    it would hand a six-omic gene-based job to the region converter. Each entry
    point therefore resolves its own default -- the global one when it happens
    to match, otherwise the first available scenario for that pipeline.

    Prefers real data over simulated, matching the behaviour that exists today
    (the bundled example has always been STATegra). Returns None when nothing is
    available, which the caller surfaces through the usual unknown-scenario path.
    """
    manifest = loadManifest(exampleFilesDir)
    globalDefault = manifest.get("defaultScenario", LEGACY_DEFAULT)

    available = listScenarios(exampleFilesDir, pipeline=pipeline)
    for scenario in available:
        if scenario.get("id") == globalDefault:
            return globalDefault

    real = [s for s in available if not s.get("simulated")]
    ordered = real or available
    return ordered[0]["id"] if ordered else None


def getScenario(exampleFilesDir, scenarioId=None):
    """One scenario by id, or the default when `scenarioId` is falsy.

    A blank id means "the default" rather than "not found", because that is what
    the bare `/pa_step1/example` route sends and what it has always meant.
    """
    manifest = loadManifest(exampleFilesDir)
    wanted = scenarioId or manifest.get("defaultScenario", LEGACY_DEFAULT)

    for scenario in manifest["scenarios"]:
        if scenario.get("id") == wanted:
            return scenario

    # The id comes off the URL (/pa_step1/example/<id>, ?scenario=<id>), and this
    # message is shown as HTML by the Step 1 dialogs and as text by the experiment
    # design note, so it repeats the id only when no page can misread it.
    shown = quotable(wanted)
    if shown is None:
        logging.info("EXAMPLE DATASETS - unknown scenario %r requested", wanted)
    raise UnknownScenario(
        "There is no example dataset %s. Available datasets: %s."
        % ("called '%s'" % shown if shown is not None else "by that name",
           ", ".join(sorted(entry.get("id", "?")
                            for entry in manifest["scenarios"]))))


# Leading "NN-" of a scenario's directory, e.g. "07-region-based" -> 7.
_ORDER_FROM_DIRECTORY = re.compile(r"^(\d+)-")

# Where a scenario sorts when it declares neither `order` nor a numbered
# directory. Large rather than negative so an unnumbered scenario lands after
# the curated sequence instead of in front of lesson one; ties then break on
# id, so the result is still deterministic.
UNORDERED_SCENARIO = 10 ** 6


def scenarioOrder(scenario):
    """Where a scenario belongs in the picker: its `order`, else its NN-.

    The dataset directories are numbered 01-..10- in the order they are meant
    to be worked through, but the manifest was written sorted by *id*, so the
    picker offered "Gene expression — six conditions" before "— single
    condition": alphabetical order of ids that were never meant to be read
    alphabetically.

    The number is read back off the directory rather than only from an `order`
    field so the manifest that is already committed sorts correctly without
    being regenerated -- regenerating needs a KEGG snapshot and a mouse GTF a
    fresh checkout does not have. New builds also write `order` explicitly,
    which then wins, so the derivation is a fallback and not the contract.
    """
    declared = scenario.get("order")
    if isinstance(declared, int) and not isinstance(declared, bool):
        return declared

    for path in declaredFiles(scenario):
        # "datasets/<NN-folder>/<subdir>/<file>": the folder is parts[1].
        parts = str(path).split("/")
        if len(parts) < 3:
            continue
        match = _ORDER_FROM_DIRECTORY.match(parts[1])
        if match:
            return int(match.group(1))

    return UNORDERED_SCENARIO


def listScenarios(exampleFilesDir, pipeline=None, includeSuperseded=False):
    """Scenarios whose files are all present, optionally filtered by pipeline.

    Presence is checked rather than assumed because two scenarios legitimately
    depend on files a fresh checkout does not have: `stategra-regions` needs the
    full mouse GTF, which a manual deploy step fetches. Offering it and failing
    is worse than not offering it.

    A simulated stand-in hides behind its real counterpart: most simulated
    scenarios duplicate the shape of a real STATegra scenario, so offering
    both shows every pipeline twice. A scenario whose `supersededBy` names
    another offered scenario of the same pipeline is omitted -- and comes back
    the moment the counterpart's files are missing, which keeps the failure
    policy intact (a checkout without the mouse GTF still gets a region
    example). Supersession is honoured only within one pipeline so a
    misconfigured mapping can never empty an entry point's offer, and it trims
    only the *offer*: `includeSuperseded=True` is for callers that must
    recognise every loadable scenario (chained-conversion matching), because a
    hidden scenario is still reachable by deep link and its conversion output
    still arrives.

    Real data leads: the published STATegra scenarios are the reason to trust
    the tool, so they are offered before the simulated lessons rather than
    after them. Within each block the teaching order holds (see
    `scenarioOrder`) -- one condition before six, six before per-condition
    relevance. This is what the picker and every per-pipeline default read.
    """
    manifest = loadManifest(exampleFilesDir)
    candidates = []
    for scenario in manifest["scenarios"]:
        if pipeline and scenario.get("pipeline") != pipeline:
            continue
        missing = missingFiles(exampleFilesDir, scenario)
        if missing:
            logging.info("EXAMPLE DATASETS - '%s' not offered, %d file(s) absent "
                         "(first: %s)", scenario.get("id"), len(missing), missing[0])
            continue
        candidates.append(scenario)

    if includeSuperseded:
        available = candidates
    else:
        offeredPipelines = {entry.get("id"): entry.get("pipeline")
                            for entry in candidates}
        available = []
        for scenario in candidates:
            counterpart = scenario.get("supersededBy")
            if (counterpart and counterpart != scenario.get("id")
                    and counterpart in offeredPipelines
                    and offeredPipelines[counterpart] == scenario.get("pipeline")):
                logging.info("EXAMPLE DATASETS - '%s' not offered, superseded "
                             "by '%s'", scenario.get("id"), counterpart)
                continue
            available.append(scenario)

    available.sort(key=lambda entry: (bool(entry.get("simulated")),
                                      scenarioOrder(entry),
                                      str(entry.get("id") or "")))
    return available


def declaredFiles(scenario):
    """Every path a scenario declares, as manifest-relative strings."""
    paths = []
    for omic in scenario.get("omics", []):
        for key in ("dataFile", "relevantFile", "associationsFile", "designFile"):
            if omic.get(key):
                paths.append(omic[key])
    for section in ("target", "design"):
        if scenario.get(section, {}).get("dataFile"):
            paths.append(scenario[section]["dataFile"])
    for reference in scenario.get("references", []):
        if reference.get("dataFile"):
            paths.append(reference["dataFile"])
    return paths


def missingFiles(exampleFilesDir, scenario):
    return [path for path in declaredFiles(scenario)
            if not os.path.isfile(absolutePath(exampleFilesDir, path))]


def absolutePath(exampleFilesDir, relativePath):
    """Resolve a manifest path, refusing anything that escapes the tree.

    Manifest paths are data, and this one is reachable from a URL-supplied
    scenario id, so `../../etc/passwd` has to be impossible rather than
    unlikely. Everything resolves under `exampleFilesDir` or it is rejected.
    """
    root = os.path.realpath(exampleFilesDir)
    candidate = os.path.realpath(os.path.join(root, relativePath))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise UnknownScenario(
            "The example dataset refers to a file outside the example "
            "directory (%r), which is not allowed." % relativePath)
    return candidate


def scenarioIdFromMode(exampleMode):
    """Split the URL's `exampleMode` segment into (isExample, scenarioId).

    The routes are `/pa_step1/<path:exampleMode>` and friends. `<path:>` already
    accepts slashes, so extending the existing `example` sentinel to
    `example/<id>` needs no new route and keeps every old URL working:

        "example"                       -> (True, None)   the default scenario
        "example/region-based"          -> (True, "region-based")
        False / None / ""               -> (False, None)  an ordinary upload
        anything else                   -> (None, None)   caller raises

    Returning a three-state first element rather than raising here keeps the
    "unrecognised mode" message next to the pipeline it belongs to; each servlet
    words it for its own entry point.
    """
    if not exampleMode:
        return False, None

    text = str(exampleMode).strip().strip("/")
    if text == "example":
        return True, None
    if text.startswith("example/"):
        scenarioId = text[len("example/"):].strip("/")
        # "example/" with nothing after it is the default, not an empty id --
        # a trailing slash is the kind of thing a copied URL grows.
        return True, (scenarioId or None)
    return None, None


# ---------------------------------------------------------------------------
# Wiring a scenario into a job
# ---------------------------------------------------------------------------

def applyScenario(jobInstance, exampleFilesDir, scenarioId=None):
    """Register a scenario's inputs on `jobInstance` and return the scenario.

    Every omic is flagged `isExample: True`, which is what makes the job read
    the bundled file in place instead of expecting a copy in its own input
    directory -- and which also makes `validateFile` return early, so these
    files are validated by the test suite rather than at submission time.

    Works for the pathway-acquisition, regions2genes and mirna2genes job
    classes, which share `addGeneBasedInputOmic` / `addReferenceInput`. MORE has
    a different job shape and is handled by `applyMoreScenario`.
    """
    scenario = getScenario(exampleFilesDir, scenarioId)

    missing = missingFiles(exampleFilesDir, scenario)
    if missing:
        raise UnknownScenario(
            "The example dataset '%s' is incomplete on this server: %d file(s) "
            "are missing, starting with '%s'."
            % (scenario["id"], len(missing), missing[0]))

    for omic in scenario.get("omics", []):
        entry = {
            "omicName": omic["omicName"],
            "inputDataFile": absolutePath(exampleFilesDir, omic["dataFile"]),
            "isExample": True,
        }
        if omic.get("relevantFile"):
            entry["relevantFeaturesFile"] = absolutePath(
                exampleFilesDir, omic["relevantFile"])
        # An experimental design (column -> condition) for a values file with
        # one column per sample. The job applies it at step 1, which is what
        # turns replicate columns into conditions for painting and lets the
        # class activity test run on the replicates.
        if omic.get("designFile"):
            entry["designFile"] = absolutePath(exampleFilesDir, omic["designFile"])
        if omic.get("enrichment"):
            entry["enrichment"] = omic["enrichment"]
        # Carried onto the job so the regulator/target pairing survives past
        # this call. The manifest has declared `role` since the miRNA scenario
        # was added, but nothing read it, so a chained pipeline had no way to
        # tell which of its two omics was the one to hand on.
        if omic.get("role"):
            entry["role"] = omic["role"]

        if omic.get("omicType") == "compound":
            jobInstance.addCompoundBasedInputOmic(entry)
        else:
            jobInstance.addGeneBasedInputOmic(entry)

    for reference in scenario.get("references", []):
        jobInstance.addReferenceInput({
            "omicName": reference["omicName"],
            "fileType": reference.get("fileType", "Reference file"),
            "inputDataFile": absolutePath(exampleFilesDir, reference["dataFile"]),
            "isExample": True,
        })

    jobInstance.setOrganism(scenario.get("organism", "mmu"))
    if scenario.get("databases") and hasattr(jobInstance, "setDatabases"):
        jobInstance.setDatabases(list(scenario["databases"]))

    return scenario


def applyMoreScenario(jobInstance, exampleFilesDir, scenarioId=None):
    """MORE's inputs, which do not fit the gene/compound omic shape.

    MORE takes a target matrix, a design matrix and N regulatory omics, each
    with its own association file and `minVariation`. Paths are absolute:
    MOREServlet joins them against the job's input directory, and
    `os.path.join` returns an absolute second argument unchanged, so the
    bundled files are read in place without being copied.
    """
    scenario = getScenario(exampleFilesDir, scenarioId)
    if scenario.get("pipeline") != "more":
        raise UnknownScenario(
            "The example dataset '%s' is not a MORE dataset (it is a '%s' "
            "dataset)." % (scenario["id"], scenario.get("pipeline")))

    missing = missingFiles(exampleFilesDir, scenario)
    if missing:
        raise UnknownScenario(
            "The MORE example dataset '%s' is incomplete on this server: %d "
            "file(s) are missing, starting with '%s'."
            % (scenario["id"], len(missing), missing[0]))

    jobInstance.targetExpressionFile = absolutePath(
        exampleFilesDir, scenario["target"]["dataFile"])
    jobInstance.conditionsFile = absolutePath(
        exampleFilesDir, scenario["design"]["dataFile"])

    for omic in scenario.get("omics", []):
        jobInstance.addRegulatoryOmic(
            omic["omicName"],
            absolutePath(exampleFilesDir, omic["dataFile"]),
            omic.get("omicType", "regulator"),
            absolutePath(exampleFilesDir, omic["associationsFile"])
            if omic.get("associationsFile") else None,
            absolutePath(exampleFilesDir, omic["relevantFile"])
            if omic.get("relevantFile") else None,
            minVariation=omic.get("minVariation", "NA"))

    parameters = scenario.get("parameters", {})
    jobInstance.method = parameters.get("method", "PLS1")
    jobInstance.alpha = float(parameters.get("alpha", 0.05))
    jobInstance.vip = float(parameters.get("vip", 0.8))
    jobInstance.filter_r2 = float(parameters.get("filter_r2", 0.0))
    jobInstance.enrichment = parameters.get("enrichment", "genes")

    return scenario


# ---------------------------------------------------------------------------
# Putting a chained example back together
# ---------------------------------------------------------------------------

# Every values file MiRNA2GeneJob.fromMiRNA2Genes writes for the pathway step
# is named regulator2Gene_output_<date>_<n>.tab, so this substring is what says
# "this omic is the OUTPUT of a regulator-to-gene conversion" rather than
# something a user uploaded. No other pipeline writes it: Bed2GeneJob writes
# bed2genes*, MOREServlet writes MORE_output_*.
CONVERSION_OUTPUT_MARKER = "regulator2Gene_output"

# Only mirna2genes hands a role="target" omic on to the pathway analysis.
# regions2genes declares no target at all, and MORE's target is a per-sample
# expression matrix under scenario["target"] -- an input to the joint model,
# never a pathway omic -- so neither can match here even by accident.
CHAINED_TARGET_PIPELINES = ("mirna2genes",)

# Characters that can be part of a filename, used as the LEFT boundary when
# looking a basename up inside a description. There is deliberately no right
# boundary: MiRNA2GeneJob.getJobDescription concatenates its fields without
# separators, and the client's single-line text field drops the newlines it
# does use, so the string that actually arrives reads
#   "Input data:mirna_values.tabInput targets:mirna_relevant.tab...Params:;..."
# -- every basename but the first runs straight into the next label.
_FILENAME_CHARACTER = r"[A-Za-z0-9_.\-]"


def attachChainedExampleTargets(jobInstance, exampleFilesDir):
    """Give a chained example back the target omic its conversion step dropped.

    A miRNA example runs in two requests: `/dm_fromMiRNAtoGenes/example/<id>`
    converts the regulator into GENE:::miRNA values, and then step 1 posts that
    output as an ORDINARY upload. That second post is deliberate -- see the
    guard in step1OnFormSubmitHandler and the test that pins it -- but it means
    the request carries one omic, the conversion output, while the manifest
    declares two: the regulator and a role="target" gene expression omic that
    has no form field to travel in. Measured: job 3Z1q20I1rC registered
    ['miRNA-seq'] alone and produced 357 pathways, 0 of them significant.

    So the target is re-attached here, from the manifest, on the server side.

    Never raises: a failure to recognise an example must not fail an ordinary
    submission, which is the same policy as the rest of this module.

    @returns {List} names of the omics attached; empty for every other job.
    """
    try:
        return _attachChainedExampleTargets(jobInstance, exampleFilesDir)
    except Exception as error:
        logging.warning("EXAMPLE DATASETS - could not re-attach a chained "
                        "example target omic: %s", error)
        return []


def _attachChainedExampleTargets(jobInstance, exampleFilesDir):
    geneBased = jobInstance.getGeneBasedInputOmics() or []
    conversionOutputs = [
        omic for omic in geneBased
        if CONVERSION_OUTPUT_MARKER in
        os.path.basename(str(omic.get("inputDataFile") or ""))]
    if not conversionOutputs:
        return []

    # An omic name the job already carries is never replaced: the user's own
    # data wins over anything the manifest would add under the same name.
    taken = {str(omic.get("omicName", "")).strip().lower()
             for omic in list(geneBased) +
             list(jobInstance.getCompoundBasedInputOmics() or [])}

    attached = []
    for omic in conversionOutputs:
        scenario = _chainedScenarioFor(exampleFilesDir, omic)
        if scenario is None:
            continue

        for target in scenario.get("omics", []):
            if target.get("role") != "target":
                continue
            name = str(target.get("omicName", "")).strip()
            if not name or name.lower() in taken:
                continue

            entry = {
                "omicName": name,
                "inputDataFile": absolutePath(exampleFilesDir, target["dataFile"]),
                "isExample": True,
                "role": "target",
            }
            if target.get("relevantFile"):
                entry["relevantFeaturesFile"] = absolutePath(
                    exampleFilesDir, target["relevantFile"])
            if target.get("enrichment"):
                entry["enrichment"] = target["enrichment"]

            if target.get("omicType") == "compound":
                jobInstance.addCompoundBasedInputOmic(entry)
            else:
                jobInstance.addGeneBasedInputOmic(entry)

            taken.add(name.lower())
            attached.append(name)
            logging.info("EXAMPLE DATASETS - re-attached '%s' from example "
                         "'%s' after the %s conversion",
                         name, scenario.get("id"), scenario.get("pipeline"))

    return attached


def _chainedScenarioFor(exampleFilesDir, inputOmic):
    """Which example produced this conversion output, or None for a real one.

    Nothing in the step-1 request names the dataset: it is an ordinary upload
    on purpose. What does arrive is the *conversion job's own description* --
    the client posts it back verbatim as `<omic>_config_args_N` and saveFiles
    stores it as `configOptions`. MiRNA2GeneJob.getJobDescription builds that
    string out of the basenames of the four files it read, and for an example
    those are the manifest's own files.

    So the fingerprint is: every file the conversion would have named for this
    scenario is mentioned. All of them, not any one -- "gene_expression_values
    .tab" on its own belongs to both miRNA scenarios and would attach the wrong
    directory's copy. Each name must also start on a filename boundary, so an
    upload saved as "<jobID>_mirna_values.tab" -- which is what a guest's own
    upload of the same file is called -- does not count as "mirna_values.tab".
    """
    configOptions = str(inputOmic.get("configOptions") or "")
    if not configOptions:
        return None

    # includeSuperseded: a scenario hidden from the picker behind its real
    # counterpart is still loadable by deep link, so its conversion output
    # must still be recognised here or its target omic is silently dropped.
    for scenario in listScenarios(exampleFilesDir, includeSuperseded=True):
        if scenario.get("pipeline") not in CHAINED_TARGET_PIPELINES:
            continue
        fingerprint = _chainedFingerprint(scenario)
        if fingerprint and all(_mentionsFile(configOptions, name)
                               for name in fingerprint):
            return scenario
    return None


def _mentionsFile(text, basename):
    return re.search("(?<!%s)%s" % (_FILENAME_CHARACTER, re.escape(basename)),
                     text) is not None


def _chainedFingerprint(scenario):
    """The basenames a conversion of this scenario is bound to have mentioned.

    Empty for a scenario that does not declare both a regulator and a target,
    which is every scenario that never chains one -- and the reason
    `regulatory-more` (target under scenario["target"], not an omic role) and
    `region-based` (no target at all) can never be matched.
    """
    omics = scenario.get("omics", [])
    regulator = next((omic for omic in omics
                      if omic.get("role") == "regulator"), None)
    target = next((omic for omic in omics if omic.get("role") == "target"), None)
    if regulator is None or target is None:
        return frozenset()

    names = [regulator.get("dataFile"), regulator.get("relevantFile"),
             target.get("dataFile")]
    names.extend(reference.get("dataFile")
                 for reference in scenario.get("references", []))
    return frozenset(os.path.basename(name) for name in names if name)


# ---------------------------------------------------------------------------
# The client's view of the catalogue
# ---------------------------------------------------------------------------

def catalogueForClient(exampleFilesDir, resolveDatabases=None):
    """What `GET /example_datasets` returns.

    Trimmed to what the picker draws. File paths are deliberately omitted: the
    client never opens these files -- it posts a scenario id and the server
    resolves it -- and publishing server paths to the browser would be a leak
    for no gain.

    @param resolveDatabases an optional `organism -> [databases]` callable. The
           manifest's own `databases` list is a property of the dataset as
           authored and cannot know what the host running it installed, but the
           picker card prints it as a promise about the job that is about to
           run. Passing DatabaseAvailability.resolveDatabases makes that promise
           true; omitting it -- as the tests do -- reports the manifest
           unchanged and keeps this module free of MongoDB, which is the reason
           it is a parameter and not an import.
    """
    manifest = loadManifest(exampleFilesDir)
    scenarios = []
    for scenario in listScenarios(exampleFilesDir):
        databases = scenario.get("databases", [])
        if resolveDatabases is not None:
            try:
                databases = resolveDatabases(scenario.get("organism"))
            except Exception as ex:
                # The picker showing a stale database list is a smaller failure
                # than the picker not opening.
                logging.warning(
                    "Could not resolve the installed databases for example '%s' "
                    "(%s: %s); reporting the manifest's list",
                    scenario.get("id"), type(ex).__name__, ex)
        scenarios.append({
            "id": scenario.get("id"),
            "title": scenario.get("title", scenario.get("id")),
            "summary": scenario.get("summary", ""),
            "tests": scenario.get("tests", []),
            "pipeline": scenario.get("pipeline"),
            "organism": scenario.get("organism"),
            "databases": databases,
            "conditions": scenario.get("conditions", []),
            "simulated": bool(scenario.get("simulated")),
            "omicNames": [omic.get("omicName")
                          for omic in scenario.get("omics", [])],
            # The omics that ship an experimental design, so Step 1 labels the
            # design field only where the example really carries one.
            "designOmics": [omic.get("omicName")
                            for omic in scenario.get("omics", []) if omic.get("designFile")],
        })
    return {
        "defaultScenario": manifest.get("defaultScenario", LEGACY_DEFAULT),
        "isFallback": bool(manifest.get("isFallback")),
        "scenarios": scenarios,
    }
