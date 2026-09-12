#!/usr/bin/env python3
"""The real STATegra data, registered rather than generated.

This is the mouse Ikaros time course PaintOmics has always shipped: six time
points of real published measurements. Nothing here rewrites a byte of it. The
files move into the same `datasets/` layout as the simulated scenarios and gain
manifest entries, so the loader has one way to find every example instead of one
way plus three hardcoded special cases.

There are three entries, not one, because the old code had three separate
hardcoded examples over this same dataset -- one per entry point:

    PathwayAcquisitionServlet  six omics, already mapped to genes
    Bed2GenesServlet           the DNase regions, before mapping
    MiRNA2GenesServlet         the miRNA table, before mapping

`stategra-multiomics` stays the **default**: clicking "Load example" without
choosing gives the real data, which is the behaviour that exists today.

Why no expected counts
----------------------
The same job yields 888/44 matched pathways on the deploy VM and 877/41 locally
because the two carry different KEGG snapshots. These entries record what the
files *are*, never what analysing them produces.
"""
import os


MULTIOMICS_FOLDER = "08-stategra-multiomics"
REGIONS_FOLDER = "09-stategra-regions"
MIRNA_FOLDER = "10-stategra-mirna"
MORE_FOLDER = "11-stategra-more"

# The omics in the order the old hardcoded example built them, with the
# filenames it derived by mangling the omic name ("DNase-seq" -> "dnase").
# Explicit here so that mangling can be deleted from the servlet.
#
# There were six. "Transcription factor" was retired on 2026-08-11: all 2,889 of
# its value tuples appear verbatim in gene_expression_values.tab -- the same
# measurements re-keyed from ENSMUSG to MGI symbol -- so it scored one layer
# twice in every pathway statistic. STATegra ran no TF-expression assay, and the
# layer's pre-reorganisation source is named `factor_expression_fake.txt`. The
# files and the full argument are in `examplefiles/archive/retired/`.
MULTIOMICS = [
    ("Gene expression",      "gene_expression",       "gene",     "genes"),
    ("Metabolomics",         "metabolomics",          "compound", "features"),
    ("Proteomics",           "proteomics",            "gene",     "features"),
    ("miRNA-seq",            "mirna",                 "gene",     "genes"),
    ("DNase-seq",            "dnase",                 "gene",     "genes"),
]

CONDITIONS = ["Ikaros/Control_0h", "Ikaros/Control_2h", "Ikaros/Control_6h",
              "Ikaros/Control_12h", "Ikaros/Control_18h", "Ikaros/Control_24h"]

# The MORE scenario keeps the two arms apart instead of shipping their ratio.
# The other three entries carry one column per timepoint, already reduced to
# Ikaros-over-control; MORE fits per-sample values against an experimental
# design, so it needs the 12 groups and the three replicates in each.
MORE_CONDITIONS = ["Ctr_0H", "Ctr_2H", "Ctr_6H", "Ctr_12H", "Ctr_18H", "Ctr_24H",
                   "Ik_0H", "Ik_2H", "Ik_6H", "Ik_12H", "Ik_18H", "Ik_24H"]

# Deliberately carries no numbers. It is shared by all four legacy entries, and
# the pair it used to quote (888/44 on the deploy VM, 877/41 locally) was both
# specific to the multi-omics job and stale the moment that job's inputs changed
# -- a recorded count that silently rots is worse than no count, because the
# next reader treats it as ground truth.
_ENVIRONMENT_NOTE = ("Matched-pathway counts depend on the KEGG snapshot the "
                     "host carries, so the same job yields different totals on "
                     "the deploy VM and locally. None are asserted.")

# What these files actually are. Until 2026-08-15 they were a reduced copy
# whose reduction predated this repository; they are now the full published
# scale, rebuilt from the deposited data by `stategrafull.py`, which is also
# where every derivation rule below is implemented and argued for.
MULTIOMICS_PROVENANCE = [
    "Published as Gomez-Cabrero et al., *STATegra, a comprehensive multi-omics "
    "dataset of B-cell differentiation in mouse*, Sci Data 6:256 (2019), "
    "[doi:10.1038/s41597-019-0202-7](https://doi.org/10.1038/s41597-019-0202-7). "
    "Mouse B3 pre-B cell line, Ikaros induced by tamoxifen, Ikaros-over-control "
    "log2 ratios at six time points, mean of three biological replicates per arm.",
    "",
    "**Full published scale since 2026-08-15**, rebuilt from the deposited data "
    "by `src/AdminTools/scripts/exampledata/stategrafull.py` (the shipped files "
    "were previously a reduced copy -- 6,336 of 12,762 genes, 1,109 of 2,396 "
    "protein groups, 5,000 capped miRNA pairs, 10,273 gene rows of DNase):",
    "",
    "| omic | shipped | source |",
    "| --- | --- | --- |",
    "| Metabolomics | 58 compounds — complete | MetaboLights MTBLS283 |",
    "| Gene expression | 12,762 genes — complete | GSE75417, published CQN+ComBat pipeline |",
    "| Proteomics | 2,384 of 2,396 groups (9 lack a gene symbol, 3 duplicate one) | PXD003263 |",
    "| miRNA-seq | 42,421 gene–miRNA pairs: each gene's 5 strongest negative regulators ∪ each miRNA's 20 strongest targets (Kendall τ over the six-ratio profiles) out of all 194,881 annotation pairs; all 333 target-annotated measured miRNAs kept | GSE75394 |",
    "| DNase-seq | 23,273 gene rows from all 52,788 consensus DHS regions | GSE75390 |",
    "",
    "The values are a re-derivation, not the pre-2026 numbers rescaled: the old "
    "files carried ratios from an unrecorded normalisation that the public "
    "releases do not reproduce. Each omic follows its published preprocessing "
    "script from [STATegraData/STATegraData](https://github.com/STATegraData/"
    "STATegraData), then per-time-point Ikaros-minus-Control means. Relevance "
    "is the induction contrast (Welch 18-vs-18, BH FDR, effect floor; "
    "per-time-point Fisher-combined for proteomics), stated fully in "
    "`stategrafull.py`. The gene-level miRNA and DNase layers are the server's "
    "own tools run at full scale: miRNA2Target pairing over the miRBase target "
    "table, and RGmatch with Bed2GeneJob's defaults collapsing regions onto "
    "genes. The region form is the `stategra-regions` scenario.",
    "",
    "A sixth layer, \"Transcription factor\", was retired on 2026-08-11 as a "
    "duplicate of gene expression; see `examplefiles/archive/retired/`.",
]


def _dataDir(context, folder):
    return os.path.join(context.outputRoot, folder, "data")


def _fileEntry(context, folder, name):
    """Relative path for `name`, or None when the file is not there.

    Returning None rather than raising keeps the generator usable on a checkout
    where the reorganisation has not been applied: the simulated scenarios still
    build, and the affected legacy entry is simply left out of the manifest.
    """
    path = os.path.join(_dataDir(context, folder), name)
    return context.relative(path) if os.path.isfile(path) else None


def buildStategraMultiomics(context):
    omics = []
    for omicName, stem, omicType, enrichment in MULTIOMICS:
        values = _fileEntry(context, MULTIOMICS_FOLDER, stem + "_values.tab")
        if values is None:
            return None
        entry = {
            "omicName": omicName,
            "omicType": omicType,
            "enrichment": enrichment,
            "dataFile": values,
        }
        relevant = _fileEntry(context, MULTIOMICS_FOLDER, stem + "_relevant.tab")
        if relevant:
            entry["relevantFile"] = relevant
        omics.append(entry)

    return {
        "id": "stategra-multiomics",
        "title": "STATegra — real mouse Ikaros time course (5 omics)",
        # Plain text, no markdown: the dataset picker renders this string as-is,
        # so emphasis markers show up literally on the card.
        "summary": ("Five omics over six time points from the published STATegra "
                    "mouse Ikaros time course. Real measurements, not simulated — "
                    "the reference every simulated scenario is shaped against. "
                    "The full published release: 12,762 genes, 2,384 protein "
                    "groups, the strongest 42,421 of 194,881 gene–miRNA pairs "
                    "(every measured miRNA kept), all 52,788 DNase regions "
                    "collapsed onto genes. See the Provenance section of the "
                    "README for how each layer is derived."),
        "tests": ["The full multi-omic pipeline on real data",
                  "Metabolite hub analysis", "Pathway network", "AI interpretation"],
        "pipeline": "pathway-acquisition",
        "organism": "mmu",
        "databases": ["KEGG", "Reactome"],
        "conditions": CONDITIONS,
        "simulated": False,
        "omics": omics,
        "references": [],
        "provenance": MULTIOMICS_PROVENANCE,
        "expected": {"note": _ENVIRONMENT_NOTE},
    }


def buildStategraRegions(context):
    """The DNase regions *before* mapping -- input for Regions2Genes.

    Note the reference GTF: this scenario points at `GTF/sorted_mmu.gtf`, which
    a fresh checkout does not have (`examplefiles/GTF/` holds a zero-byte
    `.dummy`; the real annotation is fetched by a manual deploy step). The
    loader drops a scenario whose files are missing, so on a fresh checkout this
    one is simply not offered -- and the simulated `region-based` scenario,
    which carries its own annotation, is what users get instead.
    """
    values = _fileEntry(context, REGIONS_FOLDER, "dnase_unmapped_values.tab")
    relevant = _fileEntry(context, REGIONS_FOLDER, "dnase_unmapped_relevant.tab")
    if values is None:
        return None

    entry = {
        "id": "stategra-regions",
        "title": "STATegra — DNase regions (real, needs the mouse GTF)",
        "summary": ("All 52,788 consensus DNase-seq regions from the STATegra "
                    "time course, unmapped, for the Regions2Genes step. "
                    "Requires the full mouse annotation, which is fetched by a "
                    "deploy step and is absent from a fresh checkout."),
        "tests": ["RGmatch on real regions", "Region-to-gene assignment at scale"],
        "pipeline": "regions2genes",
        "organism": "mmu",
        "databases": ["KEGG"],
        "conditions": CONDITIONS,
        "simulated": False,
        "omics": [{
            "omicName": "DNase unmapped",
            "omicType": "region",
            "enrichment": "genes",
            "dataFile": values,
        }],
        "references": [{
            "omicName": "DNase unmapped",
            "fileType": "Reference file",
            "dataFile": "GTF/sorted_mmu.gtf",
        }],
        "expected": {"note": _ENVIRONMENT_NOTE},
    }
    if relevant:
        entry["omics"][0]["relevantFile"] = relevant
    return entry


def buildStategraMirna(context):
    """The miRNA table plus the 31 MB miRBase->Ensembl map, for MiRNA2Genes."""
    values = _fileEntry(context, MIRNA_FOLDER, "mirna_unmapped_values.tab")
    relevant = _fileEntry(context, MIRNA_FOLDER, "mirna_unmapped_relevant.tab")
    reference = _fileEntry(context, MIRNA_FOLDER, "mmu_mirBase_to_ensembl.tab")
    geneValues = _fileEntry(context, MULTIOMICS_FOLDER, "gene_expression_values.tab")
    if values is None or reference is None or geneValues is None:
        return None

    omics = [{
        "omicName": "miRNA unmapped",
        "omicType": "gene",
        "enrichment": "genes",
        "role": "regulator",
        "dataFile": values,
    }, {
        "omicName": "Gene expression",
        "omicType": "gene",
        "enrichment": "genes",
        "role": "target",
        "dataFile": geneValues,
    }]
    if relevant:
        omics[0]["relevantFile"] = relevant

    return {
        "id": "stategra-mirna",
        "title": "STATegra — miRNA to genes (real)",
        "summary": ("The unmapped miRNA quantification from the STATegra time "
                    "course with the full miRBase-to-Ensembl target table, for "
                    "the Regulatory Omics step."),
        "tests": ["miRNA-to-gene association on real data",
                  "Correlation filtering across a large prediction table"],
        "pipeline": "mirna2genes",
        "organism": "mmu",
        "databases": ["KEGG"],
        "conditions": CONDITIONS,
        "simulated": False,
        "omics": omics,
        "references": [{
            "omicName": "miRNA unmapped",
            "fileType": "Reference file",
            "dataFile": reference,
        }],
        "expected": {"note": _ENVIRONMENT_NOTE},
    }


def buildStategraMore(context):
    """Real expression against a real TF network, for the MORE joint model.

    The counterpart to the simulated `regulatory-more`. That one plants a known
    driver behind every target so recall can be asserted; this one has no ground
    truth at all, which is the point -- it is what MORE does to an experiment
    rather than to a fixture.

    Built by `stategramore.py` from GSE75417 and TFLink v1.0; see that module
    for why the network is TFLink's small-scale subset rather than "All", which
    is the difference between a pathway step that separates and one that
    cannot. The files are committed, so a checkout without the source dataset
    still serves this scenario -- it simply cannot regenerate it.
    """
    target = _fileEntry(context, MORE_FOLDER, "gene_expression_targets.tab")
    design = _fileEntry(context, MORE_FOLDER, "experimental_design.tab")
    values = _fileEntry(context, MORE_FOLDER, "transcription_factor_regulators.tab")
    associations = _fileEntry(
        context, MORE_FOLDER, "transcription_factor_associations.tab")
    relevant = _fileEntry(
        context, MORE_FOLDER, "transcription_factor_relevant_regulators.tab")
    if target is None or design is None or values is None or associations is None:
        return None

    omic = {
        "omicName": "Transcription factor",
        "omicType": "regulator",
        "dataFile": values,
        "associationsFile": associations,
        # NA, not 0: MORE picks the low-variation threshold from the data. On a
        # real matrix a fixed 0 keeps every non-constant regulator, including
        # the ones whose variation is indistinguishable from measurement noise.
        "minVariation": "NA",
    }
    if relevant:
        omic["relevantFile"] = relevant

    return {
        "id": "stategra-more",
        "title": "STATegra — real expression against a real TF network (MORE)",
        "summary": ("The STATegra Ikaros induction time course (GSE75417) "
                    "against the literature-curated half of the TFLink v1.0 "
                    "mouse network: 957 genes, 387 transcription factors, 36 "
                    "samples and 12 groups, with nothing subsampled. Real "
                    "measurements and no planted signal, which is what "
                    "separates it from the simulated MORE example."),
        "tests": ["MORE on real per-sample data",
                  "All four regulatory engines (Rust PLS1, R PLS1, R MLR, Rust MLR)",
                  "12-group experimental design",
                  "Automatic minVariation threshold",
                  "GENE:::REGULATOR hand-off to pathway analysis",
                  "Pathway enrichment on a real regulatory hand-off"],
        "pipeline": "more",
        "organism": "mmu",
        "databases": ["KEGG"],
        "conditions": MORE_CONDITIONS,
        "simulated": False,
        "target": {"omicName": "Gene expression", "dataFile": target},
        "design": {"dataFile": design},
        "omics": [omic],
        "references": [],
        "parameters": {"method": "PLS1", "alpha": 0.05, "vip": 0.8,
                       "filter_r2": 0.0, "enrichment": "genes"},
        "expected": {
            "note": _ENVIRONMENT_NOTE,
            # Measured, not estimated: one run of each engine over exactly
            # these files, on an M-series laptop under other load, so treat
            # them as ratios rather than as absolutes.
            #
            # The equivalence is the load-bearing half. `cmp` on all four
            # result files -- values, relevant associations, relevant pairs and
            # the RegulationPerCondition table -- reports them byte-identical
            # between the two PLS1 engines. That is what makes offering the
            # port as the default legitimate; a 473x speed claim with no
            # equivalence behind it would just be a different answer, faster.
            # All four fit inside the 1800 s job timeout, which is the
            # property that makes this dataset usable as the example for the
            # whole engine choice rather than only for the default.
            #
            # rust-mlr measured 2026-09-12: the median of three consecutive
            # runs on an idle machine (24.6, 26.5, 26.7 s) against the binary
            # scripts/ci/build-more-rs.sh pins, d261f9c. It is the only one of
            # the four this table had no entry for, which left the scenario
            # advertising an engine whose cost nothing here recorded.
            "measuredRuntimeSeconds": {"rust-pls1": 0.1, "r-pls1": 234.4,
                                       "r-mlr": 739.8, "rust-mlr": 26.5},
            "enginesAgree": ("rust-pls1 and r-pls1 byte-identical on all four "
                             "output files; r-mlr is a different model and is "
                             "not expected to agree with either"),
            "targets": 957,
            "regulators": 387,
            "associations": 2910,
            "flaggedRegulators": 56,
            "flaggedRule": ("Welch t-test of the 18 induced samples against "
                            "the 18 controls, Benjamini-Hochberg FDR < 0.01, "
                            "and at least a two-fold difference of arm means"),
            # The number this scenario was rebuilt around, and the one worth
            # asserting: it depends only on the shipped association and
            # relevant-regulator files, so unlike the pathway counts it does
            # not move with the KEGG snapshot.
            #
            # MOREServlet stars a gene when ANY of its regulators is flagged,
            # so this is the share of the submission that carries a red star --
            # and therefore the ceiling on what pathway enrichment can resolve.
            # Against TFLink "All" it was 100.0%, which makes every
            # hypergeometric p exactly 1.0. See stategramore.py.
            "starredTargets": 301,
            "starredTargetRate": 0.315,
            "source": {
                "expression": "GEO GSE75417 (STATegra RNA-seq, CQN + ComBat)",
                "network": ("TFLink v1.0, Mus musculus, restricted to "
                            "interactions flagged Small-scale.evidence = Yes"),
                "subsample": ("none -- every gene with a measured profile and "
                              "at least one small-scale association is "
                              "included"),
            },
        },
    }


CATALOGUE = [
    buildStategraMultiomics,
    buildStategraRegions,
    buildStategraMirna,
    buildStategraMore,
]
