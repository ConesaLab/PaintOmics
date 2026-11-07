#!/usr/bin/env python3
"""An identifier reachable only two hops away in the xref graph must still map.

Why this exists
---------------
`mates` is built by *shared transcript* (AdminTools/scripts/common_build_database.py
line ~3203: the mate set of an xref is the union of the xref sets of every
transcript it appears on). Ensembl transcripts and RefSeq transcripts are
near-disjoint, so a species' xref graph splits into an Ensembl-anchored side and
a RefSeq/UniProt-anchored side, joined only through the gene-level identifiers
that appear on both.

`findIDsByFeaturesName` walked exactly one hop. So for input keyed on Ensembl
gene ids -- which is what every bundled example dataset uses -- a database keyed
on `uniprot_acc` was unreachable for roughly half the genes, although the
accession is demonstrably present in the database one hop further on. Measured
on the local mmu install, 2026-08-19:

    OmniPath (uniprot_acc)   ds01  54.9%  ->  87.7% reachable
                             STATegra 43.5%  ->  84.8% reachable
    Reactome was hit the same way (57.0% / 45.0%).

Concretely, ENSMUSG00000000037 (Scml2): its own mate group carries entrezgene
107815 and no UniProt at all; the mate group of entrez 107815 carries
B1AVB4/I6L9E4/Q8BYC8.

The fix bridges one further hop, and only for the names the first hop missed.
The bridge is restricted to GENE_LEVEL_BRIDGE_DATABASES -- identifiers that name
a gene and only ever name one gene -- so the extra hop is an identity step, not
a similarity step. Bridging through a transcript or peptide identifier would be
unsound: a shared peptide can join two paralogues, which would map a feature
onto its family member and report it as a match.

That soundness claim was measured, not assumed, on 3,000 mmu features:
  * 0 cases where the two-hop answer contradicted the one-hop answer;
  * 1,558 recovered (name, accession) pairs, of which 1,558 carried a gene
    symbol equal to the input feature's own symbol, and 0 disagreed.

These tests use a hand-built fake xref collection rather than a live MongoDB, so
they run anywhere and pin the *behaviour* (which hops are taken, what is
bridged, what is refused) rather than one species' data.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_uniprot_two_hop_mapping
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import FeatureNamesToKeggIDsMapper as mapper


# --------------------------------------------------------------------------
# A minimal stand-in for the pymongo collections the mapper touches.
# --------------------------------------------------------------------------
class _FakeCollection(object):
    def __init__(self, documents):
        self._documents = documents
        self.queries = []

    def find(self, query, projection=None, **cursorOptions):
        # pymongo's find takes cursor options too (the mapper passes
        # batch_size); they change how a result is delivered, not what it is.
        self.queries.append(query)
        for document in self._documents:
            if self._matches(document, query):
                yield document

    def _matches(self, document, query):
        for field, condition in query.items():
            value = document.get(field)
            if isinstance(condition, dict) and "$in" in condition:
                if value not in condition["$in"]:
                    return False
            elif value != condition:
                return False
        return True


class _FakeDB(object):
    """Two xref groups for one gene, exactly the shape the real build produces."""

    name = "fake-paintomics"

    def __init__(self, includeBridge=True, bridgeName="entrezgene"):
        # Group A: the Ensembl-anchored side. Carries the gene-level bridge but
        # NOT the UniProt accession.
        # Group B: the RefSeq/UniProt-anchored side, reachable only via the bridge.
        self.dbname = _FakeCollection([
            {"_id": "DB_ENSG", "dbname": "ensembl_gene"},
            {"_id": "DB_ENTREZ", "dbname": "entrezgene"},
            {"_id": "DB_UNIPROT", "dbname": "uniprot_acc"},
            {"_id": "DB_PEPTIDE", "dbname": "ensembl_peptide"},
        ])
        groupAMates = ["X_ENSG", "X_PEPTIDE"] + (["X_ENTREZ_A"] if includeBridge else [])
        self.xref = _FakeCollection([
            # -- group A --
            {"_id": "X_ENSG", "dbname_id": "DB_ENSG",
             "display_id": "ENSMUSG00000000037", "mates": groupAMates},
            {"_id": "X_PEPTIDE", "dbname_id": "DB_PEPTIDE",
             "display_id": "ENSMUSP00000326344", "mates": groupAMates},
            {"_id": "X_ENTREZ_A", "dbname_id": "DB_ENTREZ",
             "display_id": "107815", "mates": groupAMates},
            # -- group B, the bridge document's own group --
            {"_id": "X_ENTREZ_B", "dbname_id": "DB_ENTREZ",
             "display_id": "107815", "mates": ["X_ENTREZ_B", "X_UNIPROT"]},
            {"_id": "X_UNIPROT", "dbname_id": "DB_UNIPROT",
             "display_id": "Q8BYC8", "mates": ["X_ENTREZ_B", "X_UNIPROT"]},
        ])


class _NoCache(object):
    """The translation cache, neutralised: every name is a miss, writes ignored."""

    def findBatchInTranslationCache(self, *args, **kwargs):
        return {}

    def updateTranslationCache(self, *args, **kwargs):
        return None


class TwoHopMappingTest(unittest.TestCase):
    def setUp(self):
        mapper._bridgeIDCache.clear()
        self._realManager = mapper.KeggInformationManager
        mapper.KeggInformationManager = lambda: _NoCache()

    def tearDown(self):
        mapper.KeggInformationManager = self._realManager
        mapper._bridgeIDCache.clear()

    def testUniprotIsReachedThroughTheGeneLevelBridge(self):
        """The whole point: one hop cannot see Q8BYC8, two hops can."""
        db = _FakeDB()
        result = mapper.findIDsByFeaturesName(
            "job", ["ENSMUSG00000000037"], db, "DB_UNIPROT")
        self.assertEqual(result.get("ENSMUSG00000000037"), ["Q8BYC8"],
                         "the accession one hop past the bridge was not recovered")

    def testNoBridgeMeansNoRecovery(self):
        """Without a gene-level identifier in the group there is nothing to bridge."""
        db = _FakeDB(includeBridge=False)
        result = mapper.findIDsByFeaturesName(
            "job", ["ENSMUSG00000000037"], db, "DB_UNIPROT")
        self.assertEqual(result.get("ENSMUSG00000000037"), [],
                         "a miss must stay a miss, recorded as an empty list")

    def testPeptideIsNeverUsedAsABridge(self):
        """A shared peptide can join paralogues; it must not be a bridge.

        The fake's group A contains a peptide whose own group would reach
        UniProt if peptides were bridged. Removing the gene-level identifier
        must therefore still produce no match -- which is what the previous
        test asserts -- and `ensembl_peptide` must not be in the bridge set.
        """
        self.assertNotIn("ensembl_peptide", mapper.GENE_LEVEL_BRIDGE_DATABASES)
        # `ncbi_geneid` is the other name the installers give the SAME NCBI gene
        # space as `entrezgene`, so it is gene-level by construction and joined
        # the set in #85. The list is pinned rather than filtered because the
        # property under test is membership, not shape: a peptide- or
        # transcript-level database appearing here is the bug this catches, and
        # only an explicit list makes that visible when someone adds one.
        self.assertEqual(tuple(mapper.GENE_LEVEL_BRIDGE_DATABASES),
                         ("entrezgene", "ncbi_geneid", "ensembl_gene", "kegg_id"))

    def testFirstHopAnswerIsNotDisturbed(self):
        """A name the first hop resolves must not be re-resolved or re-ordered."""
        db = _FakeDB()
        result = mapper.findIDsByFeaturesName(
            "job", ["ENSMUSG00000000037"], db, "DB_ENTREZ")
        self.assertEqual(result.get("ENSMUSG00000000037"), ["107815"])

    def testSecondHopIsSkippedWhenEverythingResolved(self):
        """No unresolved names => no extra queries. The hop must be free."""
        db = _FakeDB()
        mapper.findIDsByFeaturesName("job", ["ENSMUSG00000000037"], db, "DB_ENTREZ")
        bridgeQueries = [q for q in db.xref.queries
                         if isinstance(q.get("dbname_id"), dict)]
        self.assertEqual(bridgeQueries, [],
                         "the bridge was queried although the first hop resolved everything")

    def testBridgeExcludesTheTargetDatabase(self):
        """Bridging through the target itself can only re-find what hop 1 had."""
        db = _FakeDB()
        self.assertNotIn("DB_ENTREZ",
                         mapper.resolveBridgeDatabaseIds(db, "DB_ENTREZ"))
        self.assertIn("DB_ENTREZ", mapper.resolveBridgeDatabaseIds(db, "DB_UNIPROT"))

    def testBridgeIdsAreResolvedOncePerDatabase(self):
        """The dbname lookup is cached; mapping forks run thousands of batches."""
        db = _FakeDB()
        mapper.resolveBridgeDatabaseIds(db, "DB_UNIPROT")
        before = len(db.dbname.queries)
        mapper.resolveBridgeDatabaseIds(db, "DB_UNIPROT")
        self.assertEqual(len(db.dbname.queries), before,
                         "resolveBridgeDatabaseIds re-queried dbname instead of caching")

    def test_a_rebuilt_species_is_not_bridged_through_its_old_ids(self):
        """A rebuild replaces every dbname _id and keeps the database's name.

        The ids used to be cached on that name alone, so a worker that had mapped
        this species once went on bridging through ids the rebuild had removed.
        Names needing the second hop came back unmatched, with no error, until the
        worker restarted: 666 of 3,001 sorghum genes, on real MongoDB. The target
        id is resolved afresh per mapping call, which is what moves the key.
        """
        before = _FakeDB()
        self.assertEqual(mapper.findIDsByFeaturesName("job", ["ENSMUSG00000000037"], before, "DB_UNIPROT")
                         .get("ENSMUSG00000000037"), ["Q8BYC8"])

        rebuilt = _FakeDB()
        for table in (rebuilt.dbname, rebuilt.xref):
            for document in table._documents:
                document["_id"] = "NEW_" + document["_id"]
                if "dbname_id" in document:
                    document["dbname_id"] = "NEW_" + document["dbname_id"]
                if "mates" in document:
                    document["mates"] = ["NEW_" + mate for mate in document["mates"]]

        self.assertEqual(sorted(mapper.resolveBridgeDatabaseIds(rebuilt, "NEW_DB_UNIPROT")),
                         ["NEW_DB_ENSG", "NEW_DB_ENTREZ"])
        self.assertEqual(mapper.findIDsByFeaturesName("job", ["ENSMUSG00000000037"], rebuilt, "NEW_DB_UNIPROT")
                         .get("ENSMUSG00000000037"), ["Q8BYC8"],
                         "the second hop looked in the tables of the old build")
        self.assertEqual(mapper.findIDsByFeaturesNameForDatabases(
            "job", ["ENSMUSG00000000037"], rebuilt, ["NEW_DB_UNIPROT"])["NEW_DB_UNIPROT"].get("ENSMUSG00000000037"),
            ["Q8BYC8"])

    def testNoAggregationIsUsed(self):
        """mongod 4.4 on production cannot index a $lookup sub-pipeline."""
        self.assertFalse(hasattr(_FakeDB().xref, "aggregate"),
                         "the fake has no aggregate, so a call would have raised")
        with open(mapper.__file__.replace(".pyc", ".py"), encoding="utf-8") as handle:
            source = handle.read()
        bridge = source[source.index("def _bridgeSecondHop"):source.index("def findIDsByFeaturesName")]
        self.assertNotIn("aggregate", bridge,
                         "the second hop must use plain indexed finds, not an aggregation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
