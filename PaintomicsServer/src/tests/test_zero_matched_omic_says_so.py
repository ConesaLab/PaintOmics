#!/usr/bin/env python3
"""An omic that matched nothing is refused with its identifiers, not a crash in R.

Measured 2026-08-27 on three real files pushed through steps 1-3 over HTTP:
a GEO count matrix with versioned Ensembl ids (ENSMUSG00000102693.2), a PacBio
transcript table and a human file run as mouse. All three passed step 1 as a
success with `omicSummary {KEGG: 0}` and then step 2 failed inside
generateMetaGenes.R -- "no lines available in input" -- because the metagenes
step was handed an empty <omic>_matched.txt. The user read that as a
metagenes bug; it was "nothing matched".

Usage:
    cd PaintomicsServer
    python -m src.tests.test_zero_matched_omic_says_so
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("PAINTOMICS_KEGG_DATA", tempfile.mkdtemp(prefix="kegg-"))
os.environ.setdefault("PAINTOMICS_CLIENT_TMP", tempfile.mkdtemp(prefix="client-"))

from src.classes.JobInstances.PathwayAcquisitionJob import (  # noqa: E402
    explainEmptyMapping, hasDataRows, mappedTotal)


def omic(name, mapped, unmapped):
    return {"omicName": name, "omicSummary": [{"KEGG": mapped, "Total": mapped}, unmapped]}


class ZeroMatchedOmicSaysSoTest(unittest.TestCase):

    def test_mapped_total_reads_both_summary_shapes(self):
        self.assertEqual(mappedTotal([{"KEGG": 12, "Total": 15}, 3]), 15)
        self.assertEqual(mappedTotal([{"KEGG": 12}, 3]), 12)
        self.assertEqual(mappedTotal([18, 4]), 18)
        self.assertEqual(mappedTotal([{}, 0]), 0)
        self.assertEqual(mappedTotal(None), 0)

    def test_an_omic_that_matched_something_is_not_refused(self):
        self.assertIsNone(explainEmptyMapping("mmu", [omic("Gene expression", 0, 100),
                                                        omic("Proteomics", 5, 20)],
                                              lambda name: []))

    def test_no_gene_omic_is_not_refused(self):
        self.assertIsNone(explainEmptyMapping("mmu", [], lambda name: []))

    def test_nothing_matched_names_the_identifiers(self):
        message = explainEmptyMapping(
            "mmu", [omic("Gene expression", 0, 11116)],
            lambda name: ["PB.1.1", "PB.1.2", "PB.2.1"])
        self.assertIn("matched mmu's KEGG genes", message)
        self.assertIn("'Gene expression' (11116 identifiers), e.g. PB.1.1, PB.1.2, PB.2.1", message)
        self.assertIn("Check the organism", message)

    def test_a_versioned_ensembl_id_gets_the_suffix_hint(self):
        message = explainEmptyMapping(
            "mmu", [omic("Gene expression", 0, 56953)],
            lambda name: ["ENSMUSG00000102693.2", "ENSMUSG00000064842.3", "ENSMUSG00000051951.6"])
        self.assertIn("Ensembl version suffix", message)
        self.assertIn(".2 in ENSMUSG00000102693.2", message)

    def test_a_transcript_id_of_an_accepted_genome_version_is_told_to_lose_its_suffix(self):
        """Gene ids of these two versions are in the alias tables, and their transcript ids never are."""
        cases = {
            ".1 in Psat.cameor.v2.1g306350.1":
                ["Psat.cameor.v2.1g306350.1", "Psat.cameor.v2.3g10100.1", "Psat.cameor.v2.0s1040g10400.2"],
            "_T001 in Zm00001d027230_T001":
                ["Zm00001d027230_T001", "Zm00001d002345_T002", "Zm00001d045677_T001"],
        }
        for expected, samples in cases.items():
            message = explainEmptyMapping("hsa", [omic("Gene expression", 0, 5000)], lambda name: samples)
            self.assertIn(expected, message)
            self.assertIn("remove the suffix and run again", message)
            self.assertNotIn("Check the organism", message)

    def test_the_transcript_advice_needs_every_sample_to_be_one(self):
        for samples in (["Psat.cameor.v2.1g306350", "Psat.cameor.v2.3g10100", "Psat.cameor.v2.7g337900"],
                        ["Zm00001d027230", "Zm00001d002345", "Zm00001d045677"],
                        ["Zm00001d027230_T001", "ENSMUSG00000102693", "Zm00001d002345_T001"],
                        ["Zm00001eb000010_T001", "Zm00001eb000020_T001", "Zm00001eb000050_T001"]):
            message = explainEmptyMapping("hsa", [omic("Gene expression", 0, 5000)], lambda name: samples)
            self.assertNotIn("remove the suffix", message, samples[0])
            self.assertIn("Check the organism", message)

    def test_has_data_rows(self):
        directory = tempfile.mkdtemp(prefix="matched-")
        try:
            empty = os.path.join(directory, "a_matched.txt")
            open(empty, "w").close()
            blank = os.path.join(directory, "b_matched.txt")
            with open(blank, "w") as handle:
                handle.write("\n\n")
            full = os.path.join(directory, "c_matched.txt")
            with open(full, "w") as handle:
                handle.write("11416\tGene expression\t1.0\n")
            self.assertFalse(hasDataRows(empty))
            self.assertFalse(hasDataRows(blank))
            self.assertTrue(hasDataRows(full))
            self.assertFalse(hasDataRows(os.path.join(directory, "missing.txt")))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_servlet_asks_before_the_pathways(self):
        source = open(os.path.join(os.path.dirname(__file__), "..", "servlets",
                                   "PathwayAcquisitionServlet.py")).read()
        asks = source.index("jobInstance.explainEmptyMapping(selectedCompounds)")
        pathways = source.index("summary = jobInstance.generatePathwaysList()")
        self.assertLess(asks, pathways)

    def test_the_metagenes_step_skips_an_empty_matched_file(self):
        source = open(os.path.join(os.path.dirname(__file__), "..", "classes",
                                   "JobInstances", "PathwayAcquisitionJob.py")).read()
        self.assertIn("if hasDataRows(matchedFile):", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
