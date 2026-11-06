#!/usr/bin/env python3
"""Gene ids from another assembly map, keep mapping after a rebuild, and never merge genes.

Run from `PaintomicsServer/`:

    python -m src.tests.test_identifier_aliases

Why this exists
---------------
Three users of paintomics.org asked in 2026 for gene ids the installed species
do not carry: maize B73 v4 (`Zm00001d...`, 0 of 50 mapped; zma holds v5), pea
Cameor v2 (`Psat.cameor.v2...`, 0 of 50; psat holds v1a) and sorghum, where 3,192
Ensembl genes have an NCBI locus tag naming a GeneID KEGG knows and no link in
Ensembl's dump. Inserting them into MongoDB does not last: createDatabase imports
xref and dbname with --drop and dumpDatabase mints every _id at random. So the
tables ship in the tree, and processIdentifierAliases applies them during a build.

What is pinned here
-------------------
* An alias joins exactly the groups of its target, so it reaches what the target
  reaches, through the real mapper on both of its hops.
* A target this build lacks is counted and skipped, a species without a table
  gets a no-op, and a table that cannot be read costs the aliases and no more.
* `gene_alias` is never a bridge, and a row that would make paralogues mates is
  refused: the warning in processEnsemblUniProtData's docstring, as a check.
* The shipped tables' schema, order, uniqueness and the rows users asked for.
* The regenerating script's selection rules, on tiny inputs.
* The organism hint recognises Cameor v2 and everything it recognised before.

Needs no network and no database.
"""
import gzip
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("PAINTOMICS_KEGG_DATA", tempfile.mkdtemp(prefix="kegg-"))
os.environ.setdefault("PAINTOMICS_CLIENT_TMP", tempfile.mkdtemp(prefix="client-"))

from src.common import FeatureNamesToKeggIDsMapper as mapper  # noqa: E402
from src.common import OrganismDetector as od  # noqa: E402

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(SRC, "AdminTools", "scripts")
ALIASES = os.path.join(SCRIPTS, "common_resources", "identifier_aliases")
COMMON_BUILDER = os.path.join(SCRIPTS, "common_build_database.py")
REGENERATOR = os.path.join(ALIASES, "build_identifier_aliases.py")
REGISTRY = os.path.join(SCRIPTS, "common_resources", "ensembl_genebuilds.json")
DEFAULT_BUILD = os.path.join(SCRIPTS, "default", "build_database.py")

COLUMNS = ["alias", "alias_dbname", "target_dbname", "target_display_id", "evidence"]
SHIPPED_ROWS = {"zma": 27798, "psat": 22200, "sbi": 3192}
ALIAS_SHAPE = {"zma": r"Zm00001d\d{6}", "psat": r"Psat\.cameor\.v2\.\d+(?:s\d+)?g\d+",
               "sbi": r"SORBI_3(?:\d{3}G|K)\d{6}"}
EVIDENCE = {"zma": {"ncbi_locus_tag", "maizegdb_1to1"},
            "psat": {"published_table_rbh+coord", "published_table_rbh+coord+refseq_rbh", "refseq_rbh_only"},
            "sbi": {"ncbi_locus_tag"}}


def loadModule(path, name):
    """A fresh copy of a module: the builder keeps its tables in module globals."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def readShipped(code):
    with gzip.open(os.path.join(ALIASES, code + ".tsv.gz"), "rt", encoding="utf-8") as handle:
        return [line.rstrip("\n").split("\t") for line in handle]


# --- The mapper, on what the builder holds --------------------------------------

class _FakeCollection(object):
    def __init__(self, documents):
        self._documents = documents

    def find(self, query, projection=None, **cursorOptions):
        return [document for document in self._documents if self._matches(document, query)]

    @staticmethod
    def _matches(document, query):
        for field, condition in query.items():
            value = document.get(field)
            if isinstance(condition, dict) and "$in" in condition:
                if value not in condition["$in"]:
                    return False
            elif value != condition:
                return False
        return True


class _FakeDB(object):
    """xref and dbname as dumpDatabase would write them from the builder's tables."""

    name = "fake-identifier-aliases-paintomics"

    def __init__(self, builder):
        groupsOf = builder.xref2transcript["global"]
        members = builder.transcript2xref["global"]
        self.dbname = _FakeCollection([{"_id": item.getID(), "dbname": item.dbname}
                                       for item in builder.dbname.values()])
        documents = []
        for entry in builder.xref["global"].values():
            mates = set()
            for group in groupsOf.get(entry.getID(), ()):
                mates |= members[group]
            documents.append({"_id": entry.getID(), "dbname_id": entry.dbname_id,
                              "display_id": entry.display_id, "mates": sorted(mates)})
        self.xref = _FakeCollection(documents)


class _NoCache(object):
    def findBatchInTranslationCache(self, *args, **kwargs):
        return {}

    def updateTranslationCache(self, *args, **kwargs):
        return None


class BuilderCase(unittest.TestCase):
    """Drive the real processors on tiny mapping files, with tables in a temporary directory."""

    def setUp(self):
        self.builder = loadModule(COMMON_BUILDER, "common_build_database_under_test")
        self.dataDir = tempfile.mkdtemp(prefix="identifier-aliases-")
        os.makedirs(os.path.join(self.dataDir, "mapping"))
        self.tablesDir = os.path.join(self.dataDir, "tables")
        os.makedirs(self.tablesDir)
        self.builder.DATA_DIR = self.dataDir + "/"
        self.builder.SPECIE = "zma"
        self.builder.VERBOSE = False
        self.builder.IDENTIFIER_ALIASES_DIR = self.tablesDir
        self.builder.EXTERNAL_RESOURCES = {"ensembl": [{"output": "ensembl_mapping.list", "description": "t"}]}

        mapper._bridgeIDCache.clear()
        self._realManager = mapper.KeggInformationManager
        mapper.KeggInformationManager = lambda: _NoCache()

    def tearDown(self):
        mapper.KeggInformationManager = self._realManager
        mapper._bridgeIDCache.clear()
        shutil.rmtree(self.dataDir, ignore_errors=True)

    def buildSpecies(self, ensembl=(), kegg=()):
        """The passes that run before the aliases: ensembl rows, then (GeneID, KEGG id) pairs."""
        def write(name, lines):
            with open(os.path.join(self.dataDir, "mapping", name), "w", encoding="utf-8") as handle:
                handle.write("".join(line + "\n" for line in lines))

        write("ncbi-geneid2kegg.list", ["ncbi-geneid:%s\tzma:%s" % pair for pair in kegg])
        if ensembl:
            write("ensembl_mapping.list", ["\t".join(row) for row in ensembl])
            self.builder.processEnsemblData()
        self.builder.processKEGGMappingData()

    def writeTable(self, rows, code="zma", header=COLUMNS):
        with gzip.open(os.path.join(self.tablesDir, code + ".tsv.gz"), "wt", encoding="utf-8") as handle:
            handle.write("\t".join(header) + "\n")
            for row in rows:
                handle.write("\t".join(row) + "\n")

    def entry(self, displayId, table):
        return self.builder.findXREF(displayId, self.builder.ALL_DBS[table].getID())

    def groupsOf(self, displayId, table):
        item = self.entry(displayId, table)
        self.assertIsNotNone(item, displayId + " is not in " + table)
        return set(self.builder.xref2transcript["global"].get(item.getID(), ()))

    def mapped(self, names):
        """What the real mapper answers for these names, translated to kegg_id."""
        keggId = self.builder.ALL_DBS["kegg_id"].getID()
        answer = mapper.findIDsByFeaturesName("job", list(names), _FakeDB(self.builder), keggId)
        return {name: sorted(answer.get(name, [])) for name in names}


class AliasesJoinTheirTargetsGroupsTest(BuilderCase):

    def test_an_alias_reaches_the_same_groups_as_its_target(self):
        self.buildSpecies(
            ensembl=[("Zm00001eb000010", "1001", "Zm00001eb000010_P001", "Zm00001eb000010_T001"),
                     ("Zm00001eb000010", "1001", "Zm00001eb000010_P002", "Zm00001eb000010_T002")],
            kegg=[("1001", "1001"), ("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag"),
                         ("Zm00001d000002", "gene_alias", "ensembl_gene", "Zm00001eb000010", "maizegdb_1to1")])

        self.assertEqual(2, self.builder.processIdentifierAliases())

        viaGeneId = self.groupsOf("Zm00001d000001", "gene_alias")
        self.assertTrue(viaGeneId)
        self.assertEqual(self.groupsOf("2002", "ncbi_geneid"), viaGeneId)
        viaEnsembl = self.groupsOf("Zm00001d000002", "gene_alias")
        self.assertEqual(2, len(viaEnsembl), "both transcripts of the target")
        self.assertEqual(self.groupsOf("Zm00001eb000010", "ensembl_gene"), viaEnsembl)

    def test_the_real_mapper_finds_them_on_either_hop(self):
        """2002 is one hop (its group holds kegg_id); the Ensembl target needs the second, through entrezgene."""
        self.buildSpecies(
            ensembl=[("Zm00001eb000010", "1001", "Zm00001eb000010_P001", "Zm00001eb000010_T001")],
            kegg=[("1001", "1001"), ("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag"),
                         ("Zm00001d000002", "gene_alias", "ensembl_gene", "Zm00001eb000010", "maizegdb_1to1")])
        self.builder.processIdentifierAliases()

        answer = self.mapped(["Zm00001d000001", "Zm00001d000002", "Zm00001eb000010", "Zm00001d999999"])
        self.assertEqual(["2002"], answer["Zm00001d000001"])
        self.assertEqual(answer["Zm00001eb000010"], answer["Zm00001d000002"])
        self.assertEqual(["1001"], answer["Zm00001d000002"])
        self.assertEqual([], answer["Zm00001d999999"])

    def test_an_alias_of_a_gene_on_two_kegg_entries_reaches_both_like_its_target(self):
        """A gene_alias is reached, never walked through, so what its target spans is no merge of its making."""
        self.buildSpecies(kegg=[("3003", "A3"), ("3003", "B3")])
        self.writeTable([("Zm00001d000003", "gene_alias", "ncbi_geneid", "3003", "ncbi_locus_tag")])

        self.assertEqual(1, self.builder.processIdentifierAliases())
        self.assertEqual(["A3", "B3"], self.mapped(["Zm00001d000003"])["Zm00001d000003"])

    def test_an_ensembl_gene_with_no_link_joins_the_group_without_a_second_document(self):
        """Sorghum: the gene is already installed, only the way to KEGG is missing."""
        self.buildSpecies(ensembl=[("SORBI_3001G262200", "", "EES00001", "EES00001T")],
                          kegg=[("110433423", "110433423")])
        self.assertEqual({"SORBI_3001G262200": []}, self.mapped(["SORBI_3001G262200"]))
        ownGroups = self.groupsOf("SORBI_3001G262200", "ensembl_gene")
        documentsBefore = len(self.builder.xref["global"])

        self.writeTable([("SORBI_3001G262200", "ensembl_gene", "ncbi_geneid", "110433423", "ncbi_locus_tag")])
        self.assertEqual(1, self.builder.processIdentifierAliases())

        self.assertEqual(documentsBefore, len(self.builder.xref["global"]), "insertXREF must de-duplicate")
        self.assertNotIn("gene_alias", self.builder.ALL_DBS, "nothing was filed under the new table")
        self.assertEqual(ownGroups | self.groupsOf("110433423", "ncbi_geneid"),
                         self.groupsOf("SORBI_3001G262200", "ensembl_gene"))
        answer = self.mapped(["SORBI_3001G262200", "EES00001T"])
        self.assertEqual(["110433423"], answer["SORBI_3001G262200"])
        self.assertEqual(["110433423"], answer["EES00001T"], "its transcript follows, through the gene")

    def test_each_doc_records_where_the_alias_came_from(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        self.writeTable([("Psat.cameor.v2.0s1040g10400", "gene_alias", "ncbi_geneid", "2002", "refseq_rbh_only")])
        self.builder.processIdentifierAliases()
        self.assertIn("refseq_rbh_only", self.entry("Psat.cameor.v2.0s1040g10400", "gene_alias").description)


class FailSoftTest(BuilderCase):

    def test_a_species_with_no_table_is_a_no_op(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        tablesBefore = set(self.builder.ALL_DBS)
        documentsBefore = len(self.builder.xref["global"])

        self.assertEqual(0, self.builder.processIdentifierAliases())

        self.assertEqual(tablesBefore, set(self.builder.ALL_DBS))
        self.assertEqual(documentsBefore, len(self.builder.xref["global"]))
        self.assertEqual([], self.builder.SKIPPED_SOURCES)
        self.assertNotIn("IDENTIFIER-ALIASES", self.builder.FAILED_LINES)

    def test_a_missing_target_is_counted_and_skipped_without_aborting(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag"),
                         ("Zm00001d000002", "gene_alias", "ncbi_geneid", "404", "ncbi_locus_tag"),
                         ("Zm00001d000003", "gene_alias", "ensembl_gene", "Zm00001eb404404", "maizegdb_1to1"),
                         ("Zm00001d000004", "gene_alias", "no_such_table", "2002", "ncbi_locus_tag")])

        self.assertEqual(1, self.builder.processIdentifierAliases())

        self.assertEqual(4, self.builder.TOTAL_FEATURES["IDENTIFIER-ALIASES"])
        dropped = self.builder.FAILED_LINES["IDENTIFIER-ALIASES"]
        self.assertEqual(["Zm00001d000002", "Zm00001d000003", "Zm00001d000004"], sorted(row[1] for row in dropped))
        self.assertIsNone(self.entry("Zm00001d000002", "gene_alias"), "no document for a skipped row")
        self.assertEqual([], self.builder.SKIPPED_SOURCES, "losing a few rows is a count, not a lost source")
        self.builder.dumpErrors()  # every dropped row is in the shape dumpErrors writes

    def test_a_target_with_no_group_is_as_good_as_missing(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        table = self.builder.ALL_DBS["ncbi_geneid"].getID()
        self.builder.insertXREF(self.builder.XREF_Entry("5005", table, "t"))
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "5005", "ncbi_locus_tag")])
        self.assertEqual(0, self.builder.processIdentifierAliases())

    def test_a_table_none_of_whose_rows_apply_is_reported_as_a_lost_source(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "404", "ncbi_locus_tag")])

        self.assertEqual(0, self.builder.processIdentifierAliases())

        self.assertEqual(["IDENTIFIER-ALIASES"], [label for label, _, _ in self.builder.SKIPPED_SOURCES])
        self.assertNotIn("gene_alias", self.builder.ALL_DBS, "an empty table must not be registered")

    def test_a_table_that_cannot_be_read_costs_the_aliases_and_nothing_else(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        path = os.path.join(self.tablesDir, "zma.tsv.gz")

        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag")],
                        header=["alias", "current_xref_id"])
        self.assertEqual(0, self.builder.processIdentifierAliases())

        with open(path, "wb") as handle:
            handle.write(b"not a gzip stream")
        self.assertEqual(0, self.builder.processIdentifierAliases())

        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag")])
        with open(path, "rb") as handle:
            whole = handle.read()
        with open(path, "wb") as handle:
            handle.write(whole[:len(whole) // 2])
        self.assertEqual(0, self.builder.processIdentifierAliases())

        self.assertEqual(3, len(self.builder.SKIPPED_SOURCES))
        self.assertNotIn("gene_alias", self.builder.ALL_DBS)

    def test_a_malformed_row_is_dropped_and_the_rest_apply(self):
        self.buildSpecies(kegg=[("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag"),
                         ("Zm00001d000002", "gene_alias", "ncbi_geneid"),
                         ("Zm00001d000003", "gene_alias", "ncbi_geneid", "", "ncbi_locus_tag"),
                         ("Zm00001d000004", "kegg_id", "ncbi_geneid", "2002", "ncbi_locus_tag")])
        self.assertEqual(1, self.builder.processIdentifierAliases())
        self.assertEqual(3, len(self.builder.FAILED_LINES["IDENTIFIER-ALIASES"]))


class ParaloguesAreNotMergedTest(BuilderCase):
    """processEnsemblUniProtData's warning: paralogues in one group inherit each other's kegg_id."""

    def assertRefused(self, alias, table, groupsBefore, applied=0):
        self.assertEqual(applied, self.builder.processIdentifierAliases())
        self.assertEqual(groupsBefore, self.groupsOf(alias, table), alias + " must be left as it was")
        reasons = [row[0] for row in self.builder.FAILED_LINES["IDENTIFIER-ALIASES"]]
        self.assertTrue(reasons and all(reason.startswith("IDENTIFIER ALIAS NOT APPLIED") for reason in reasons))

    def test_a_gene_is_not_filed_into_a_target_that_names_several_kegg_genes(self):
        self.buildSpecies(ensembl=[("GENE_A", "", "PEP_A", "TR_A")], kegg=[("3003", "A3"), ("3003", "B3")])
        before = self.groupsOf("GENE_A", "ensembl_gene")
        self.writeTable([("GENE_A", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertRefused("GENE_A", "ensembl_gene", before)
        self.assertEqual({"GENE_A": []}, self.mapped(["GENE_A"]))

    def test_two_genes_claiming_one_target_do_not_become_mates(self):
        self.buildSpecies(ensembl=[("GENE_A", "", "PEP_A", "TR_A"), ("GENE_B", "", "PEP_B", "TR_B")],
                          kegg=[("3003", "3003"), ("4004", "4004")])
        beforeA = self.groupsOf("GENE_A", "ensembl_gene")
        beforeB = self.groupsOf("GENE_B", "ensembl_gene")
        self.writeTable([("GENE_A", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag"),
                         ("GENE_B", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertRefused("GENE_A", "ensembl_gene", beforeA)
        self.assertEqual(beforeB, self.groupsOf("GENE_B", "ensembl_gene"))
        self.assertFalse(self.groupsOf("GENE_A", "ensembl_gene") & self.groupsOf("GENE_B", "ensembl_gene"))

    def test_a_gene_that_already_reaches_another_kegg_gene_keeps_only_that(self):
        self.buildSpecies(ensembl=[("GENE_A", "1001", "PEP_A", "TR_A")], kegg=[("1001", "1001"), ("3003", "3003")])
        before = self.groupsOf("GENE_A", "ensembl_gene")
        self.writeTable([("GENE_A", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertRefused("GENE_A", "ensembl_gene", before)
        self.assertEqual(["1001"], self.mapped(["GENE_A"])["GENE_A"],
                         "reached through its entrezgene, on the second hop")

    def test_an_alias_naming_two_targets_is_refused_whichever_table_it_is_in(self):
        self.buildSpecies(ensembl=[("GENE_A", "", "PEP_A", "TR_A")], kegg=[("3003", "3003"), ("4004", "4004")])
        before = self.groupsOf("GENE_A", "ensembl_gene")
        self.writeTable([("GENE_A", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag"),
                         ("GENE_A", "ensembl_gene", "ncbi_geneid", "4004", "ncbi_locus_tag")])
        self.assertRefused("GENE_A", "ensembl_gene", before)

        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "3003", "ncbi_locus_tag"),
                         ("Zm00001d000001", "gene_alias", "ncbi_geneid", "4004", "ncbi_locus_tag")])
        self.assertEqual(0, self.builder.processIdentifierAliases())
        self.assertNotIn("gene_alias", self.builder.ALL_DBS)

    def test_a_name_that_already_means_another_gene_gets_no_alias(self):
        self.buildSpecies(ensembl=[("GENE_A", "1001", "PEP_A", "TR_A")], kegg=[("1001", "1001"), ("3003", "3003")])
        self.writeTable([("GENE_A", "gene_alias", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertEqual(0, self.builder.processIdentifierAliases())
        self.assertEqual(["1001"], self.mapped(["GENE_A"])["GENE_A"])

    def test_several_gene_aliases_may_name_one_gene(self):
        """Two v4 models merged into one v5 gene: each reaches it, and neither is walked through."""
        self.buildSpecies(kegg=[("3003", "3003")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "3003", "ncbi_locus_tag"),
                         ("Zm00001d000002", "gene_alias", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertEqual(2, self.builder.processIdentifierAliases())
        self.assertEqual({"Zm00001d000001": ["3003"], "Zm00001d000002": ["3003"]},
                         self.mapped(["Zm00001d000001", "Zm00001d000002"]))

    def test_the_decision_does_not_depend_on_the_rows_order(self):
        """Decided on the tables as the earlier passes left them, before any row is applied."""
        self.buildSpecies(ensembl=[("GENE_A", "", "PEP_A", "TR_A"), ("GENE_B", "", "PEP_B", "TR_B")],
                          kegg=[("3003", "3003"), ("4004", "4004")])
        self.writeTable([("GENE_B", "ensembl_gene", "ncbi_geneid", "4004", "ncbi_locus_tag"),
                         ("GENE_A", "ensembl_gene", "ncbi_geneid", "3003", "ncbi_locus_tag")])
        self.assertEqual(2, self.builder.processIdentifierAliases())
        self.assertEqual({"GENE_A": ["3003"], "GENE_B": ["4004"]}, self.mapped(["GENE_A", "GENE_B"]))


class BridgesTest(BuilderCase):

    def test_aliases_stay_out_of_the_bridges(self):
        self.assertNotIn(self.builder.GENE_ALIAS_DBNAME, mapper.GENE_LEVEL_BRIDGE_DATABASES)
        self.assertEqual(set(mapper.GENE_LEVEL_BRIDGE_DATABASES) - {"kegg_id"},
                         set(self.builder.ALIAS_BRIDGE_DATABASES),
                         "the refusals follow the mapper's bridge list; keep the two in step")

        self.buildSpecies(kegg=[("2002", "2002")])
        self.writeTable([("Zm00001d000001", "gene_alias", "ncbi_geneid", "2002", "ncbi_locus_tag")])
        self.builder.processIdentifierAliases()
        database = _FakeDB(self.builder)
        aliasTable = self.builder.ALL_DBS["gene_alias"].getID()
        self.assertNotIn(aliasTable, mapper.resolveBridgeDatabaseIds(database))
        self.assertIn(self.builder.ALL_DBS["ncbi_geneid"].getID(), mapper.resolveBridgeDatabaseIds(database))

    def test_the_reach_the_refusals_use_is_the_mappers(self):
        """keggGenesReachedBy answers what findIDsByFeaturesName answers, on both hops."""
        self.buildSpecies(
            ensembl=[("GENE_HOP2", "1001", "PEP_1", "TR_1"), ("GENE_NONE", "", "PEP_2", "TR_2")],
            kegg=[("1001", "1001"), ("3003", "A3"), ("3003", "B3")])
        for name, table in (("GENE_HOP2", "ensembl_gene"), ("GENE_NONE", "ensembl_gene"), ("3003", "ncbi_geneid")):
            reached = self.builder.keggGenesReachedBy(self.entry(name, table).getID())
            self.assertEqual(self.mapped([name])[name], sorted(reached), name)


class BuildScriptTest(unittest.TestCase):

    def test_the_default_build_applies_the_aliases_once_in_the_right_place(self):
        calls = []
        with open(DEFAULT_BUILD, encoding="utf-8") as handle:
            for line in handle:
                match = re.match(r"\s*COMMON_BUILD_DB_TOOLS\.(process[A-Za-z]+|dumpDatabase)\(\)", line)
                if match:
                    calls.append(match.group(1))
        self.assertEqual(1, calls.count("processIdentifierAliases"))
        position = calls.index("processIdentifierAliases")
        self.assertLess(calls.index("processEnsemblData"), position)
        self.assertLess(calls.index("processKEGGMappingData"), position)
        self.assertLess(calls.index("processEnsemblUniProtData"), position,
                        "the aliases join the groups the Ensembl-UniProt pass finished")
        self.assertLess(position, calls.index("processKEGGPathwaysData"))
        self.assertLess(position, calls.index("dumpDatabase"))


class ShippedTablesTest(unittest.TestCase):

    def test_exactly_the_three_tables_ship_and_their_species_use_the_default_build(self):
        tables = sorted(name[:-len(".tsv.gz")] for name in os.listdir(ALIASES) if name.endswith(".tsv.gz"))
        self.assertEqual(sorted(SHIPPED_ROWS), tables)
        with open(REGISTRY, encoding="utf-8") as handle:
            genebuilds = json.load(handle)["genebuilds"]
        for code in tables:
            self.assertIn(code, genebuilds)
            self.assertFalse(os.path.isdir(os.path.join(SCRIPTS, code + "_resources")),
                             "a directory wins over the default build, which is what applies the table")

    def test_schema_order_uniqueness_and_row_counts(self):
        for code, expected in SHIPPED_ROWS.items():
            lines = readShipped(code)
            self.assertEqual(COLUMNS, lines[0], code)
            rows = lines[1:]
            self.assertEqual(expected, len(rows), code)
            self.assertEqual(sorted(rows), rows, code + " is not sorted")
            self.assertEqual(len(rows), len({row[0] for row in rows}), code + " names an alias twice")
            shape = re.compile(ALIAS_SHAPE[code])
            for row in rows:
                self.assertEqual(5, len(row), row)
                self.assertTrue(all(row), row)
                self.assertTrue(shape.fullmatch(row[0]), row[0])

    def test_dbnames_and_evidence_are_valid(self):
        allowed = loadModule(COMMON_BUILDER, "common_build_database_tables").IDENTIFIER_ALIAS_DATABASES
        targets = {"ncbi_geneid", "ensembl_gene"}
        for code in SHIPPED_ROWS:
            for _, aliasDb, targetDb, targetId, evidence in readShipped(code)[1:]:
                self.assertIn(aliasDb, allowed)
                self.assertIn(targetDb, targets)
                self.assertIn(evidence, EVIDENCE[code])
                if targetDb == "ncbi_geneid":
                    self.assertTrue(targetId.isdigit(), targetId)

    def test_the_rows_users_asked_for_are_there(self):
        wanted = {
            "psat": [["Psat.cameor.v2.0s1040g10400", "gene_alias", "ncbi_geneid", "127107715", "refseq_rbh_only"]],
            "zma": [["Zm00001d038291", "gene_alias", "ncbi_geneid", "541612", "ncbi_locus_tag"]],
            "sbi": [["SORBI_3001G262200", "ensembl_gene", "ncbi_geneid", "110433423", "ncbi_locus_tag"]],
        }
        for code, rows in wanted.items():
            shipped = readShipped(code)
            for row in rows:
                self.assertIn(row, shipped, code)

    def test_transcript_ids_are_not_aliases(self):
        for code in SHIPPED_ROWS:
            transcripts = [row[0] for row in readShipped(code)[1:] if re.search(r"_T\d+$|\.\d+$", row[0])]
            self.assertEqual([], transcripts, code)

    def test_the_files_are_deterministic_gzip(self):
        for code in SHIPPED_ROWS:
            with open(os.path.join(ALIASES, code + ".tsv.gz"), "rb") as handle:
                head = handle.read(10)
            self.assertEqual(b"\x1f\x8b", head[:2], code)
            self.assertEqual(0, head[3], code + " embeds a file name or other optional header")
            self.assertEqual(b"\x00\x00\x00\x00", head[4:8], code + " embeds a timestamp")

    def test_the_readme_records_sources_and_attribution(self):
        with open(os.path.join(ALIASES, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        for needed in ("10.57745/5MDE99", "Kreplak", "Etalab", "MaizeGDB", "All_Plants.gene_info", "GCF_024323335.1",
                       "refseq_rbh_only", "DIAMOND"):
            self.assertIn(needed, readme)

    def test_the_tables_apply_to_a_build_that_has_their_targets(self):
        """The real file, through the real reader, in the real function: one row's worth of species."""
        builder = loadModule(COMMON_BUILDER, "common_build_database_real_table")
        builder.SPECIE = "psat"
        keggTable = builder.insertDatabase(builder.DBNAME_Entry("kegg_id", "KEGG", "Identifier"))
        ncbiTable = builder.insertDatabase(builder.DBNAME_Entry("ncbi_geneid", "NCBI", "Identifier"))
        for item in (builder.insertXREF(builder.XREF_Entry("127107715", keggTable, "t")),
                     builder.insertXREF(builder.XREF_Entry("127107715", ncbiTable, "t"))):
            builder.insertTR_XREF(item, "GROUP")

        self.assertEqual(1, builder.processIdentifierAliases())

        alias = builder.findXREF("Psat.cameor.v2.0s1040g10400", builder.ALL_DBS["gene_alias"].getID())
        self.assertEqual({"127107715"}, builder.keggGenesReachedBy(alias.getID()))
        self.assertEqual(22199, len(builder.FAILED_LINES["IDENTIFIER-ALIASES"]))


class _StubSpecies(object):
    """The Species methods the selection rules use, over plain dictionaries."""

    def __init__(self, kegg, tables, reach):
        self.kegg = set(kegg)
        self.tables = tables
        self.reached = reach

    def displayIds(self, table):
        return self.kegg if table == "kegg_id" else set(self.tables.get(table, ()))

    def has(self, table, value):
        return value in self.tables.get(table, ())

    def reach(self, table, value):
        return set(self.reached.get((table, value), ()))


class RegeneratingScriptTest(unittest.TestCase):

    def setUp(self):
        self.script = loadModule(REGENERATOR, "build_identifier_aliases_under_test")
        self.workDir = tempfile.mkdtemp(prefix="alias-tables-")

    def tearDown(self):
        shutil.rmtree(self.workDir, ignore_errors=True)

    def test_the_output_is_sorted_unique_and_byte_identical(self):
        rows = [("b", "gene_alias", "ncbi_geneid", "2", "x"), ("a", "gene_alias", "ncbi_geneid", "1", "x"),
                ("b", "gene_alias", "ncbi_geneid", "2", "x")]
        first = os.path.join(self.workDir, "first.tsv.gz")
        second = os.path.join(self.workDir, "second.tsv.gz")
        self.script.writeTable(first, rows)
        os.utime(first, (0, 0))
        self.script.writeTable(second, list(reversed(rows)))
        with open(first, "rb") as one, open(second, "rb") as other:
            self.assertEqual(one.read(), other.read())
        with gzip.open(first, "rt") as handle:
            self.assertEqual(["alias", "a", "b"], [line.split("\t")[0] for line in handle])

    def test_maize_ncbi_wins_and_maizegdb_only_counts_when_strictly_one_to_one(self):
        species = _StubSpecies(kegg={"100", "200"},
                               tables={"ncbi_geneid": {"100", "200"}},
                               reach={("ensembl_gene", "V5A"): {"900"}, ("ensembl_gene", "V5B"): {"901"},
                                      ("ensembl_gene", "V5C"): {"902"}, ("ensembl_gene", "V5E"): {"903"},
                                      ("ensembl_gene", "V5F"): {"904"}})
        tags = self.script.readLocusTags(
            ["4577\t100\tLOC100\tZEAMMB73_Zm00001d000001\t-\n",     # NCBI, in KEGG
             "4577\t300\tLOC300\tZEAMMB73_Zm00001d000002\t-\n",     # NCBI, not in KEGG
             "4577\t200\tLOC200\tZEAMMB73_Other01\t-\n",            # not a v4 gene id
             "4577\t400\tLOC400\t-\t-\n"], "ZEAMMB73_")
        v4ToV5 = {"Zm00001d000001": ["V5D"],             # NCBI wins over this pair
                  "Zm00001d000002": ["V5A"],             # NCBI has nothing to offer: 1:1
                  "Zm00001d000003": ["V5B", "V5C"],      # split between assemblies
                  "Zm00001d000004": ["V5E"],             # merged with Zm00001d000005
                  "Zm00001d000005": ["V5E"],
                  "Zm00001d000006": ["V5G"]}             # 1:1 but its gene reaches no KEGG gene
        v5ToV4 = {"V5A": ["Zm00001d000002"], "V5D": ["Zm00001d000001"], "V5B": ["Zm00001d000003"],
                  "V5C": ["Zm00001d000003"], "V5E": ["Zm00001d000004", "Zm00001d000005"], "V5G": ["Zm00001d000006"]}

        rows = sorted(self.script.maizeRows(species, tags, v4ToV5, v5ToV4))

        self.assertEqual([("Zm00001d000001", "gene_alias", "ncbi_geneid", "100", "ncbi_locus_tag"),
                          ("Zm00001d000002", "gene_alias", "ensembl_gene", "V5A", "maizegdb_1to1")], rows)

    def test_pea_published_pairs_need_best_hits_both_ways_and_a_genome_overlap(self):
        v1OnV2 = [["P1.1", "V2A.1"], ["P2.1", "V2B.1"], ["P3.1", "V2Z.1"], ["P4.1", "V2D.1"]]
        v2OnV1 = [["V2A.1", "P1.1"], ["V2B.1", "P2.1"], ["V2C.1", "P3.1"], ["V2D.1", "P4.1"]]
        intersection = [["chr1", "1", "100", "+", "V2A", "chr1", "1", "100", "+", "P1", "90"],     # overlap 0.9
                        ["chr1", "1", "100", "+", "V2B", "chr1", "1", "400", "+", "P2", "100"],    # 0.25 of the longer
                        ["chr1", "1", "100", "+", "V2C", "chr1", "1", "100", "+", "P3", "100"]]    # hits not reciprocal
        self.assertEqual({"V2A": "P1"}, self.script.publishedPeaPairs(v1OnV2, v2OnV1, intersection))

    def test_pea_routes_merge_recording_the_evidence_and_dropping_conflicts(self):
        species = _StubSpecies(kegg={"100", "200", "300", "400"},
                               tables={"ncbi_geneid": {"100", "200", "300", "400"}},
                               reach={("ensembl_gene", "P1"): {"100"}, ("ensembl_gene", "P2"): {"200"},
                                      ("ensembl_gene", "P3"): {"300"}})
        published = {"V2A": "P1", "V2B": "P2", "V2C": "P3", "V2N": "P404"}
        refseq = {"V2A": "100", "V2C": "400", "V2D": "400", "V2E": "999"}

        rows, conflicts = self.script.peaRows(species, published, refseq)

        self.assertEqual([("V2A", "gene_alias", "ensembl_gene", "P1", "published_table_rbh+coord+refseq_rbh"),
                          ("V2B", "gene_alias", "ensembl_gene", "P2", "published_table_rbh+coord"),
                          ("V2D", "gene_alias", "ncbi_geneid", "400", "refseq_rbh_only")], rows)
        self.assertEqual([("V2C", "P3", "400")], conflicts)

    def test_refseq_hits_must_clear_identity_and_coverage_both_ways(self):
        header = "\t".join(self.script.RBH_HEADER)
        lines = [header,
                 "V2A\t100\t99.0\t10\t10\t10\t100\t100\t0\t1\t1\t1",
                 "V2B\t200\t94.9\t10\t10\t10\t100\t100\t0\t1\t1\t1",
                 "V2C\t300\t99.0\t10\t10\t10\t79.9\t100\t0\t1\t1\t1",
                 "V2D\t400\t99.0\t10\t10\t10\t100\t79.9\t0\t1\t1\t1"]
        self.assertEqual({"V2A": "100"}, self.script.readRefseqReciprocal(lines))

    def test_sorghum_takes_only_genes_with_no_way_to_kegg(self):
        species = _StubSpecies(kegg={"100", "200", "300"},
                               tables={"ncbi_geneid": {"100", "200"}, "ensembl_gene": {"G1", "G2", "G3", "G4"}},
                               reach={("ensembl_gene", "G2"): {"200"}})
        tags = {"G1": {"100"}, "G2": {"200"}, "G3": {"300"}, "G4": {"100", "200"}}
        self.assertEqual([("G1", "ensembl_gene", "ncbi_geneid", "100", "ncbi_locus_tag")],
                         self.script.sorghumRows(species, tags))

    def test_the_diamond_step_works_on_genes_not_proteins(self):
        genes = self.script.proteinGenes(
            ["# feature\tclass\n",
             "CDS\twith_protein\ta\tb\tc\t1\tNC\t1\t2\t+\tXP_1\t\t\t\tLOC100\t100\t\t3\t1\t\n",
             "CDS\twith_protein\ta\tb\tc\t1\tNC\t1\t2\t+\tXP_2\t\t\t\tLOC100\t100\t\t3\t1\t\n",
             "CDS\twith_protein\ta\tb\tc\t1\tNC\t1\t2\t+\tXP_3\t\t\t\tLOC300\t300\t\t3\t1\t\n",
             "gene\tprotein_coding\ta\tb\tc\t1\tNC\t1\t2\t+\t\t\t\t\tLOC300\t300\t\t3\t\t\n"])
        self.assertEqual({"XP_1": "100", "XP_2": "100", "XP_3": "300"}, genes)

        records = list(self.script.fastaRecords([">XP_1 first\n", "MA*\n", ">XP_2 longer\n", "MAA\n", "AA*\n",
                                                 ">XP_3 other\n", "MC\n"]))
        self.assertEqual(("XP_1", "MA"), records[0], "a trailing stop is not a residue")
        self.assertEqual({"XP_2": "MAAAA", "XP_3": "MC"}, self.script.longestProteinPerGene(records, genes))

        def hit(query, subject, score):
            return "\t".join([query, subject, "99", "10", "10", "10", "100", "100", "0", str(score)]) + "\n"

        forward = [hit("V2A.1", "XP_2", 90), hit("V2A.1", "XP_3", 50),  # V2A's best is gene 100
                   hit("V2B.1", "XP_2", 80),                            # V2B also likes gene 100, less
                   hit("V2C.1", "XP_3", 70), hit("V2C.1", "XP_2", 70)]  # a tie goes to the hit listed first
        backward = [hit("XP_2", "V2A.1", 90), hit("XP_2", "V2B.1", 80),
                    hit("XP_3", "V2C.1", 70)]
        rows = self.script.reciprocalHits(forward, backward, genes)
        self.assertEqual([("V2A", "100", "1"), ("V2C", "300", "2")], [(row[0], row[1], row[10]) for row in rows],
                         "V2B loses gene 100 to V2A; V2C's tie is recorded")
        self.assertEqual(len(self.script.RBH_HEADER), len(rows[0]))

    def test_the_workbook_reader_needs_only_the_standard_library(self):
        import zipfile
        path = os.path.join(self.workDir, "book.xlsx")
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rels = "http://schemas.openxmlformats.org/package/2006/relationships"
        office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/sharedStrings.xml",
                             '<sst xmlns="%s"><si><t>query</t></si><si><t>Psat1.1</t></si></sst>' % main)
            archive.writestr("xl/workbook.xml",
                             '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="wanted" r:id="rId7"/>'
                             '<sheet name="other" r:id="rId8"/></sheets></workbook>' % (main, office))
            archive.writestr("xl/_rels/workbook.xml.rels",
                             '<Relationships xmlns="%s"><Relationship Id="rId7" Target="worksheets/sheet2.xml"/>'
                             '<Relationship Id="rId8" Target="worksheets/sheet1.xml"/></Relationships>' % rels)
            archive.writestr("xl/worksheets/sheet2.xml",
                             '<worksheet xmlns="%s"><sheetData>'
                             '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                             '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="C2"><v>7</v></c></row>'
                             '</sheetData></worksheet>' % main)
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="%s"><sheetData/></worksheet>' % main)
        self.assertEqual({"wanted": [["Psat1.1", "", "7"]]}, self.script.readWorkbook(path, ("wanted",)))


class _Lookup(object):
    def __init__(self, installed):
        self._installed = set(installed)

    def installed(self):
        return self._installed

    def hits(self, code, identifiers):
        return 0


class OrganismHintTest(unittest.TestCase):

    CAMEOR = ["Psat.cameor.v2.0s1040g10400", "Psat.cameor.v2.1g00050", "Psat.cameor.v2.7g337900",
              "Psat.cameor.v2.0s0921g00050", "Psat.cameor.v2.3g10100.1"]

    def test_cameor_v2_is_recognised_and_nothing_else_stopped_being(self):
        cases = {
            tuple(self.CAMEOR): ("psat",),
            ("Zm00001d027230", "Zm00001d002345", "Zm00001d045677", "Zm00001d038291", "Zm00001d001865"): ("zma",),
            ("Sobic.001G215100", "Sobic.001G262200", "Sobic.007G085966", "Sobic.007G070300",
             "Sobic.009G242200"): ("sbi",),
            ("AT1G30814", "AT5G64350", "AT3G04615", "AT2G36130", "AT1G04537"): ("ath",),
            ("ENSG00000230523", "ENSG00000141510", "ENSG00000012048", "ENSG00000139618",
             "ENSG00000171862"): ("hsa",),
        }
        for identifiers, codes in cases.items():
            match = od.matchPattern(list(identifiers))
            self.assertIsNotNone(match, identifiers[0])
            self.assertEqual(codes, match[0], identifiers[0])

    def test_a_cameor_file_run_as_the_wrong_organism_is_told_pea(self):
        result = od.detectOrganism(self.CAMEOR, selected="ath", lookup=_Lookup({"ath", "psat", "zma"}), names={})
        self.assertEqual("pattern", result["method"])
        self.assertEqual("psat", result["confident"]["code"])

    def test_every_shipped_pea_alias_fits_the_pattern(self):
        pattern = re.compile(next(regex for regex, codes, _ in od._PATTERN_TABLE if codes == ("psat",)))
        for row in readShipped("psat")[1:]:
            self.assertTrue(pattern.fullmatch(row[0]), row[0])
        # A v1a id or a lookalike is not Cameor v2.
        for identifier in ("Psat7g133800", "Psat.cameor.v1.1g00050", "Psat.cameor.v2.", "psat.cameor.v2.1g00050"):
            self.assertIsNone(pattern.fullmatch(identifier), identifier)


if __name__ == "__main__":
    unittest.main(verbosity=2)
