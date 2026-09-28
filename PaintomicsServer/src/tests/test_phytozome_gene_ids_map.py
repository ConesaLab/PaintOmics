"""A table of Phytozome gene ids must map on the organism it belongs to.

Phytozome (JGI) and Ensembl Plants publish the same gene models under two
names: Sobic.001G215100 in Phytozome, SORBI_3001G215100 -- the ENA locus tag
the model was submitted under -- in Ensembl, which is what the species
databases were built from. The mapper's lookup is exact, so a table straight
out of a Phytozome pipeline mapped none of its genes.

2026-09-26, paintomics.org, organism sbi: a sorghum DESeq2 table of 20
Sobic.* ids found 0 of them in the xref (all 20 once written as SORBI_3*;
11 of those have a KEGG gene). The user sent two organism requests asking
for "ID mapping for these types", with no organism in them, before giving up.

The rename is for the job's own organism only, for gene ids only (a
transcript or protein id is a different thing), case-insensitive on the way
in (Mercator writes these ids in lower case), and the counts, the unmatched
list and the name a gene is shown under keep the id as uploaded.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_phytozome_gene_ids_map
"""
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene
from src.common.FeatureNamesToKeggIDsMapper import (
    PHYTOZOME_LOCUS_TAGS, mapFeatureIdentifiers, phytozomeToLocusTags)

# The first eight ids of the reported file, in its order.
REPORTED = ["Sobic.001G215100", "Sobic.001G262200", "Sobic.007G085966", "Sobic.007G070300",
            "Sobic.007G080600", "Sobic.007G143400", "Sobic.007G133400", "Sobic.007G062600"]


def genes(names):
    featureList = []
    for name in names:
        gene = Gene("")
        gene.setName(name)
        featureList.append(gene)
    return featureList


class PhytozomeToLocusTags(unittest.TestCase):

    def names(self, names, organism):
        featureList = genes(names)
        renamed = phytozomeToLocusTags(featureList, organism)
        return [f.getName() for f in featureList], renamed

    def test_the_reported_sorghum_ids_become_ensembl_locus_tags(self):
        self.assertEqual(self.names(REPORTED[:3], "sbi"),
                         (["SORBI_3001G215100", "SORBI_3001G262200", "SORBI_3007G085966"], 3))

    def test_each_listed_organism_gets_its_own_locus_tag(self):
        cases = {"gmx": ("Glyma.01G000100", "GLYMA_01G000100"),
                 "pper": ("Prupe.1G000100", "PRUPE_1G000100"),
                 "phai": ("Pahal.1G000100", "PAHAL_1G000100"),
                 "sbi": ("Sobic.009G242200", "SORBI_3009G242200")}
        self.assertEqual(set(cases), set(PHYTOZOME_LOCUS_TAGS))
        for organism, (phytozome, locusTag) in cases.items():
            self.assertEqual(self.names([phytozome], organism), ([locusTag], 1), organism)

    def test_mercators_lower_case_and_phytozomes_version_suffix_are_read(self):
        self.assertEqual(self.names(["sobic.001g215100", "SOBIC.001G215100", "Sobic.001G215100.v3.2",
                                     "Sobic.001G215100.v3"], "sbi"),
                         (["SORBI_3001G215100"] * 4, 4))

    def test_the_organism_code_is_matched_case_insensitively(self):
        self.assertEqual(self.names(["Sobic.001G215100"], "SBI"), (["SORBI_3001G215100"], 1))

    def test_transcripts_and_proteins_are_left_as_uploaded(self):
        # Sobic.001G215100.1 is a transcript: reading it as its gene would be
        # a guess about which isoform the user meant, so it stays unmatched.
        names = ["Sobic.001G215100.1", "Sobic.001G215100.1.p", "Sobic.001G215100.1.v3.2"]
        self.assertEqual(self.names(names, "sbi"), (names, 0))

    def test_another_organisms_ids_are_left_alone(self):
        # Soybean ids in a sorghum job must still fail to map, visibly.
        self.assertEqual(self.names(["Glyma.01G000100"], "sbi"), (["Glyma.01G000100"], 0))
        self.assertEqual(self.names(["Sobic.001G215100"], "zma"), (["Sobic.001G215100"], 0))

    def test_anything_else_is_left_alone(self):
        names = ["SORBI_3001G215100", "Sobic.", "Sobic", "Sobic.001G 215100", "Sobic.-1", "", None]
        self.assertEqual(self.names(names, "sbi"), (names, 0))

    def test_no_organism_changes_nothing(self):
        self.assertEqual(self.names(["Sobic.001G215100"], ""), (["Sobic.001G215100"], 0))
        self.assertEqual(self.names(["Sobic.001G215100"], None), (["Sobic.001G215100"], 0))


def organismInstalled(organism):
    try:
        from pymongo import MongoClient
        from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
        client = MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=2000)
        names = client.list_database_names()
        client.close()
        return (organism + "-paintomics") in names
    except Exception:
        return False


@unittest.skipUnless(organismInstalled("sbi"), "local MongoDB with sbi not available")
class PhytozomeIdsMapLikeLocusTags(unittest.TestCase):

    def mapNames(self, names, found=None):
        matched, notMatched, found = [], [], found if found is not None else []
        mapFeatureIdentifiers("TEST" + uuid.uuid4().hex[:8], "sbi", ["KEGG"],
                              genes(names), matched, notMatched, found, "genes")
        flatten = lambda slots: [f for slot in slots for f in (slot if isinstance(slot, list) else [slot])]
        return flatten(matched), flatten(notMatched)

    def test_the_reported_ids_map_to_the_same_genes_as_their_locus_tags(self):
        tagged = [name.replace("Sobic.", "SORBI_3") for name in REPORTED]
        taggedMatched, _ = self.mapNames(tagged)
        self.assertTrue(taggedMatched, "the control locus tags no longer map locally")
        matched, _ = self.mapNames(REPORTED)
        self.assertEqual(sorted(f.getID() for f in matched),
                         sorted(f.getID() for f in taggedMatched))

    def test_counts_the_unmatched_list_and_the_shown_name_keep_the_uploaded_ids(self):
        found = []
        matched, notMatched = self.mapNames(REPORTED, found)
        self.assertTrue(found[0]["KEGG"])
        self.assertTrue(all(name.startswith("Sobic.") for name in found[0]["KEGG"]), found[0]["KEGG"])
        self.assertTrue(all(f.getName().startswith("Sobic.") for f in notMatched))
        # sbi has KEGG symbols for a handful of genes; none of these has one,
        # so each is shown under the name the user's file gives it.
        self.assertTrue(all(f.getName() in REPORTED for f in matched),
                        [f.getName() for f in matched])


if __name__ == "__main__":
    unittest.main()
