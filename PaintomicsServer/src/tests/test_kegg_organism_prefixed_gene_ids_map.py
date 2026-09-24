"""A gene list copied from KEGG ("<organism>:<gene>") must map.

KEGG writes its own gene identifiers with the organism code in front:
fox:FOXG_00001, hsa:7157, mmu:14679. The species databases store the bare
gene (FOXG_00001), and the mapper's lookup is an exact match, so a list in
KEGG's own notation matched nothing at all.

2026-09-16, paintomics.org, organism fox: a Gene expression file of 16,248
identifiers (fox:FOXG_00001, fox:FOXG_00002, ...) was refused at Step 2 with
"None of the identifiers in your data matched fox's KEGG genes ... matched 0",
and the user filed an error report. The same genes without the prefix, five
minutes later, mapped 2,728 features onto all 152 fox pathways.

The prefix is dropped for the job's own organism only, case-insensitively,
and never down to an empty name.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_kegg_organism_prefixed_gene_ids_map
"""
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene
from src.common.FeatureNamesToKeggIDsMapper import (
    mapFeatureIdentifiers, stripOrganismPrefix)

ORGANISM = "mmu"
# KEGG's mouse genes are Entrez ids: mmu:14679 is Gnai3, mmu:12544 is Cdc45.
PREFIXED = ["mmu:14679", "MMU:12544"]


def genes(names):
    featureList = []
    for name in names:
        gene = Gene("")
        gene.setName(name)
        featureList.append(gene)
    return featureList


class StripOrganismPrefix(unittest.TestCase):

    def names(self, names, organism):
        featureList = genes(names)
        renamed = stripOrganismPrefix(featureList, organism)
        return [f.getName() for f in featureList], renamed

    def test_the_reported_ids_lose_the_prefix(self):
        self.assertEqual(self.names(["fox:FOXG_00001", "fox:FOXG_00002"], "fox"),
                         (["FOXG_00001", "FOXG_00002"], 2))

    def test_the_prefix_is_matched_case_insensitively(self):
        self.assertEqual(self.names(["FOX:FOXG_00001", "Fox:FOXG_00002"], "fox"),
                         (["FOXG_00001", "FOXG_00002"], 2))

    def test_another_organisms_prefix_is_left_alone(self):
        # A human id in a mouse job must still fail to map, visibly, rather
        # than be quietly re-read as a mouse Entrez id.
        self.assertEqual(self.names(["hsa:7157"], "mmu"), (["hsa:7157"], 0))

    def test_names_that_only_contain_a_colon_are_left_alone(self):
        names = ["FOXG_00001", "chr1:1000-2000", "foxp:1", "fox:", "fo:x", "", None]
        self.assertEqual(self.names(names, "fox"), (names, 0))

    def test_no_organism_changes_nothing(self):
        self.assertEqual(self.names(["fox:FOXG_00001"], ""), (["fox:FOXG_00001"], 0))


def mongoAvailable():
    try:
        from pymongo import MongoClient
        from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
        client = MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=2000)
        names = client.list_database_names()
        client.close()
        return (ORGANISM + "-paintomics") in names
    except Exception:
        return False


@unittest.skipUnless(mongoAvailable(), "local MongoDB with mmu not available")
class PrefixedIdsMapLikeBareIds(unittest.TestCase):

    def mapNames(self, names):
        matched, notMatched, found = [], [], []
        mapFeatureIdentifiers("TEST" + uuid.uuid4().hex[:8], ORGANISM, ["KEGG"],
                              genes(names), matched, notMatched, found, "genes")
        flatten = lambda slots: [f for slot in slots for f in (slot if isinstance(slot, list) else [slot])]
        return flatten(matched), flatten(notMatched)

    def test_kegg_notation_maps_to_the_same_genes_as_the_bare_ids(self):
        bareMatched, bareNotMatched = self.mapNames(["14679", "12544"])
        self.assertEqual(bareNotMatched, [], "the control ids no longer map locally")
        prefixedMatched, prefixedNotMatched = self.mapNames(PREFIXED)
        self.assertEqual(prefixedNotMatched, [])
        self.assertEqual(sorted(f.getID() for f in prefixedMatched),
                         sorted(f.getID() for f in bareMatched))


if __name__ == "__main__":
    unittest.main()
