"""Every species with an Ensembl genebuild must install its Ensembl identifiers.

Run from `PaintomicsServer/`:

    PYTHONPATH=. python src/tests/test_ensembl_genebuilds_are_installed.py

Measured on paintomics.org on 2026-09-10: 157 of 171 installed species had no
ensembl_gene table. 72 of them are eukaryotes whose organism Ensembl or
Ensembl Genomes annotates, so an Ensembl gene id -- the id every hosted GTF
emits and most expression matrices carry -- resolved 0 features on soybean,
maize, grape, rice, goat and sixty others, while the job reported success.
Three causes, three invariants here:

  * 148 species had no <code>_resources/ directory, so scripts/default/ built
    them from KEGG's conversion lists alone. The genebuild registry
    (scripts/common_resources/ensembl_genebuilds.json) now tells the default
    scripts where the dumps live; this test pins that the registry is
    well-formed, that the default scripts consult it, and that every gap the
    census found is covered by the registry or by a directory of its own.
  * Species that did have a directory sometimes ran passes that could not
    work: spo and ddi called processUniProtData without any Ensembl or RefSeq
    pass before it, and that pass links ONLY through transcripts and peptides
    an earlier pass created -- every row failed and five tables were
    registered with 0 rows. dosa parsed the TAB-separated Ensembl mapping as
    COMMA-separated. The ordering rules below fail on both shapes.
  * Ensembl Genomes genebuilds imported from GenBank carry type prefixes
    (`gene-Solyc01g080690.3`); nobody uploads those. The parser stores the
    bare spelling alongside, in the same mate group.

Needs no network and no database.
"""
import glob
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(SRC, "AdminTools", "scripts")
COMMON_BUILDER = os.path.join(SCRIPTS, "common_build_database.py")
REGISTRY = os.path.join(SCRIPTS, "common_resources", "ensembl_genebuilds.json")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ensembl_genebuild_gaps_2026-09-10.json")

DIVISIONS = {"vertebrates", "plants", "fungi", "protists", "metazoa"}
DUMPS = {"entrez", "uniprot"}

#: Passes that create transcript groups; processUniProtData can only join those.
GROUP_CREATING_PASSES = ("processEnsemblData", "processRefSeqData")


def liveCalls(scriptPath):
    """The COMMON_BUILD_DB_TOOLS.<processor>() calls in file order, comments excluded."""
    calls = []
    with open(scriptPath, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"\s*(?:COMMON_BUILD_DB_TOOLS\.)?(process[A-Za-z]+|dumpDatabase|createDatabase)\(\)", line)
            if match:
                calls.append(match.group(1))
    return calls


def resourceDirs():
    """Every per-species directory; common_resources holds shared inputs, not a species."""
    return sorted(path for path in glob.glob(os.path.join(SCRIPTS, "*_resources"))
                  if os.path.isfile(os.path.join(path, "build_database.py")))


def speciesOf(resourceDir):
    return os.path.basename(resourceDir)[:-len("_resources")]


def loadBuilder():
    """A fresh common_build_database module: it keeps its tables in module globals."""
    spec = importlib.util.spec_from_file_location("common_build_database_under_test", COMMON_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declaredKeys(resourceDir):
    """Top-level keys of the directory's EXTERNAL_RESOURCES, by the builder's own reader."""
    return loadBuilder().declaredResourceKeys(os.path.join(resourceDir, "download_conf.py"))


class RegistryTests(unittest.TestCase):

    def setUp(self):
        with open(REGISTRY, encoding="utf-8") as handle:
            self.registry = json.load(handle)

    def test_registry_is_well_formed(self):
        genebuilds = self.registry.get("genebuilds")
        self.assertTrue(genebuilds, "registry has no genebuilds")
        for base in ("vertebrates-base", "genomes-base"):
            self.assertTrue(self.registry.get(base, "").startswith("https://"), base)
        for code, entry in genebuilds.items():
            self.assertRegex(code, r"^[a-z]{3,5}$", code)
            self.assertIn(entry.get("division"), DIVISIONS, code)
            self.assertRegex(entry.get("species-path", ""), r"^[a-z0-9_]+(/[a-z0-9_]+)?$", code)
            self.assertIsInstance(entry.get("taxid"), int, code)
            self.assertTrue(entry.get("xrefs"), code + " declares no dump")
            self.assertTrue(set(entry["xrefs"]) <= DUMPS, code)

    def test_default_scripts_consult_the_registry(self):
        """A species without a directory is served by scripts/default/, so both halves must read it."""
        download = os.path.join(SCRIPTS, "default", "download_others.py")
        build = os.path.join(SCRIPTS, "default", "build_database.py")
        self.assertTrue(os.path.isfile(download), "scripts/default/download_others.py is missing")
        with open(download, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("ensemblResourcesFor(", source)
        self.assertIn("downloadEnsemblResources(", source,
                      "the default download must go through the shared helper, not a private copy of the loop")
        with open(build, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("ensemblResourcesFor(", source)
        calls = liveCalls(build)
        for needed in ("processEnsemblData", "processKEGGMappingData", "processEnsemblUniProtData"):
            self.assertIn(needed, calls, "default build never calls " + needed)
        self.assertLess(calls.index("processEnsemblData"), calls.index("processKEGGMappingData"))
        self.assertLess(calls.index("processKEGGMappingData"), calls.index("processEnsemblUniProtData"))

    def test_dbmanager_falls_back_to_the_default_download_script(self):
        with open(os.path.join(SRC, "AdminTools", "DBManager.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"default/download_others.py"', source,
                      "DBManager only runs <code>_resources/download_others.py; a species without one gets no Ensembl data")

    def test_every_census_gap_is_covered(self):
        """Each species the census found is served by the registry or by its own directory."""
        with open(FIXTURE, encoding="utf-8") as handle:
            gaps = json.load(handle)["species"]
        genebuilds = self.registry["genebuilds"]
        uncovered = []
        for gap in gaps:
            if not gap["dumps"]:
                continue  # Ensembl publishes neither dump; nothing can link it
            code = gap["code"]
            ownDir = os.path.join(SCRIPTS, code + "_resources")
            if os.path.isdir(ownDir):
                keys = declaredKeys(ownDir)
                wanted = {"entrez": "ensembl", "uniprot": "ensembl_uniprot"}
                missing = [wanted[d] for d in gap["dumps"] if wanted[d] not in keys]
                if missing:
                    uncovered.append("%s has its own directory but declares no %s" % (code, ", ".join(missing)))
            elif code not in genebuilds:
                uncovered.append(code + " is in neither the registry nor a resources directory")
            elif set(genebuilds[code]["xrefs"]) != set(gap["dumps"]):
                uncovered.append("%s registry lists %s, Ensembl publishes %s"
                                 % (code, genebuilds[code]["xrefs"], gap["dumps"]))
        self.assertEqual([], uncovered, "\n  ".join([""] + uncovered))

    def test_registered_species_have_no_competing_directory_without_ensembl(self):
        """A directory wins over the registry, so it must not silently drop the dumps."""
        for code in self.registry["genebuilds"]:
            ownDir = os.path.join(SCRIPTS, code + "_resources")
            if os.path.isdir(ownDir):
                keys = declaredKeys(ownDir)
                self.assertTrue(keys & {"ensembl", "ensembl_uniprot"},
                                code + " is registered but its own directory declares no Ensembl dump")


class BuildOrderTests(unittest.TestCase):

    def test_species_scripts_take_their_paths_from_the_arguments(self):
        """dre's scripts hard-coded /home/tian/... (paintomics.uv.es only): dead on every other host."""
        hardcoded = re.compile(r"""^\s*(SPECIE|ROOT_DIR|DATA_DIR|DESTINATION|LOG_FILE)\s*=\s*['"]""", re.M)
        broken = []
        for resourceDir in resourceDirs():
            for name in ("download_others.py", "build_database.py"):
                path = os.path.join(resourceDir, name)
                if not os.path.isfile(path):
                    continue
                with open(path, encoding="utf-8") as handle:
                    for match in hardcoded.finditer(handle.read()):
                        broken.append("%s/%s assigns %s a literal" % (speciesOf(resourceDir), name, match.group(1)))
        self.assertEqual([], broken, "\n  ".join([""] + broken))

    def test_ensembl_dumps_are_fetched_through_the_shared_helper(self):
        """One loop in common_build_database; a private copy in a species script drifts (dre's did)."""
        broken = []
        for resourceDir in resourceDirs():
            keys = declaredKeys(resourceDir)
            if not keys & {"ensembl_uniprot"}:
                continue  # directories predating the helper only fetch the entrez dump; left as they are
            with open(os.path.join(resourceDir, "download_others.py"), encoding="utf-8") as handle:
                source = handle.read()
            if "downloadEnsemblResources(" not in source:
                broken.append(speciesOf(resourceDir) + " declares ensembl_uniprot but does not call downloadEnsemblResources")
        self.assertEqual([], broken, "\n  ".join([""] + broken))

    def test_ensembl_dump_declared_means_ensembl_pass_called(self):
        broken = []
        for resourceDir in resourceDirs():
            keys = declaredKeys(resourceDir)
            calls = liveCalls(os.path.join(resourceDir, "build_database.py"))
            if "ensembl" in keys and "processEnsemblData" not in calls:
                broken.append(speciesOf(resourceDir) + " declares the entrez dump but never calls processEnsemblData")
            if "ensembl_uniprot" in keys and "processEnsemblUniProtData" not in calls:
                broken.append(speciesOf(resourceDir) + " declares the uniprot dump but never calls processEnsemblUniProtData")
        self.assertEqual([], broken, "\n  ".join([""] + broken))

    def test_uniprot_pass_follows_a_group_creating_pass(self):
        """processUniProtData links only through transcripts/peptides an earlier pass made."""
        broken = []
        for resourceDir in resourceDirs():
            calls = liveCalls(os.path.join(resourceDir, "build_database.py"))
            if "processUniProtData" not in calls:
                continue
            before = calls[:calls.index("processUniProtData")]
            if not any(pass_ in before for pass_ in GROUP_CREATING_PASSES):
                broken.append(speciesOf(resourceDir) + " calls processUniProtData with no Ensembl or RefSeq pass before it")
        self.assertEqual([], broken, "\n  ".join([""] + broken))

    def test_ensembl_uniprot_pass_follows_the_kegg_mapping(self):
        """It joins the groups KEGG's uniprot list created; before them it has nothing to join."""
        broken = []
        for resourceDir in resourceDirs():
            calls = liveCalls(os.path.join(resourceDir, "build_database.py"))
            if "processEnsemblUniProtData" not in calls:
                continue
            if "processKEGGMappingData" not in calls or \
                    calls.index("processKEGGMappingData") > calls.index("processEnsemblUniProtData"):
                broken.append(speciesOf(resourceDir) + " runs processEnsemblUniProtData before processKEGGMappingData")
        self.assertEqual([], broken, "\n  ".join([""] + broken))


class CensusToolTests(unittest.TestCase):
    """The registry builder must not mistake a failed listing for an empty one."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("ensembl_census_under_test",
                                                      os.path.join(SCRIPTS, "ensembl_census.py"))
        self.census = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.census)

    def test_a_failed_listing_is_unknown_not_empty(self):
        def failing(url, tries=3):
            raise Exception("connection reset")
        self.census.fetch = failing
        self.assertIsNone(self.census.dumpsPublished("fungi", "fungi_x_collection/some_species", "release-116"))

    def test_a_listing_reports_exactly_the_dumps_it_carries(self):
        def listing(url, tries=3):
            return ('<a href="Some_species.ASM1.63.ena.tsv.gz">x</a>'
                    '<a href="Some_species.ASM1.63.uniprot.tsv.gz">x</a>'
                    '<a href="Some_species.ASM1.63.karyotype.tsv.gz">x</a>')
        self.census.fetch = listing
        self.assertEqual(["uniprot"], self.census.dumpsPublished("plants", "some_species", "release-116"))
        self.census.fetch = lambda url, tries=3: "<a href=\"README\">x</a>"
        self.assertEqual([], self.census.dumpsPublished("plants", "some_species", "release-116"))


class ParserTests(unittest.TestCase):
    """Drive the real processors on tiny mapping files in a temporary DATA_DIR."""

    def setUp(self):
        self.builder = loadBuilder()
        self.dataDir = tempfile.mkdtemp(prefix="ensembl-parser-")
        os.makedirs(os.path.join(self.dataDir, "mapping"))
        self.builder.DATA_DIR = self.dataDir + "/"
        self.builder.SPECIE = "sly"
        self.builder.VERBOSE = False

    def tearDown(self):
        shutil.rmtree(self.dataDir, ignore_errors=True)

    def writeMapping(self, name, rows):
        with open(os.path.join(self.dataDir, "mapping", name), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write("\t".join(row) + "\n")

    def tableId(self, name):
        return self.builder.ALL_DBS[name].getID()

    def groupsOf(self, display_id, table):
        item = self.builder.findXREF(display_id, self.tableId(table))
        self.assertIsNotNone(item, display_id + " was not inserted into " + table)
        return set(self.builder.xref2transcript["global"].get(item.getID(), ()))

    def test_bare_form_of_a_prefixed_identifier_shares_the_group(self):
        self.builder.EXTERNAL_RESOURCES = {"ensembl": [{"output": "ensembl_mapping.list", "description": "t"}]}
        self.writeMapping("ensembl_mapping.list", [
            ("gene-Solyc01g080690.3", "101247556", "CDS-Solyc01g080690.3.1", "mRNA-Solyc01g080690.3.1"),
            ("ENSMUSG00000000001", "14679", "ENSMUSP00000000001", "ENSMUST00000000001"),
        ])
        self.builder.processEnsemblData()

        prefixed = self.groupsOf("gene-Solyc01g080690.3", "ensembl_gene")
        bare = self.groupsOf("Solyc01g080690.3", "ensembl_gene")
        self.assertTrue(prefixed, "the prefixed gene has no transcript group")
        self.assertEqual(prefixed, bare, "the bare spelling must sit in the same mate group")
        self.assertEqual(prefixed, self.groupsOf("101247556", "entrezgene"))
        self.assertEqual(prefixed, self.groupsOf("Solyc01g080690.3.1", "ensembl_transcript"))
        self.assertEqual(prefixed, self.groupsOf("Solyc01g080690.3.1", "ensembl_peptide"))
        # An unprefixed id gains no phantom twin.
        self.assertIsNone(self.builder.bareEnsemblIdentifier("ENSMUSG00000000001"))
        self.assertEqual(1, sum(1 for key in self.builder.ALL_ENTRIES if key.startswith("ENSMUSG00000000001#")))

    def test_ensembl_uniprot_pass_joins_the_kegg_group(self):
        """An Ensembl gene reaches kegg_id in ONE hop through a KEGG-mapped accession."""
        self.builder.SPECIE = "spo"
        self.builder.EXTERNAL_RESOURCES = {
            "ensembl_uniprot": [{"output": "ensembl_uniprot.list", "description": "t"}]}
        self.writeMapping("uniprot2kegg.list", [("up:P0CT33", "spo:SPAC212.11"), ("up:Q9HGQ0", "spo:SPAC212.02")])
        self.writeMapping("ensembl_uniprot.list", [
            ("SPAC212.11", "P0CT33", "SPAC212.11.1:pep", "SPAC212.11.1"),
            ("SPAC977.02", "A0A000UNKNOWN", "SPAC977.02.1:pep", "SPAC977.02.1"),
        ])
        self.builder.processKEGGMappingData()
        keggGroupsBefore = self.groupsOf("SPAC212.11", "kegg_id")
        self.builder.processEnsemblUniProtData()

        geneGroups = self.groupsOf("SPAC212.11", "ensembl_gene")
        self.assertTrue(geneGroups)
        self.assertTrue(geneGroups <= self.groupsOf("SPAC212.11", "kegg_id"),
                        "kegg_id did not join the Ensembl transcript's group")
        self.assertTrue(geneGroups <= self.groupsOf("P0CT33", "uniprot_acc"))
        self.assertFalse(geneGroups & keggGroupsBefore, "the Ensembl ids must not be written into KEGG's group")
        # An accession KEGG does not map stays reachable from its own transcript only.
        unknownGroups = self.groupsOf("A0A000UNKNOWN", "uniprot_acc")
        self.assertEqual(unknownGroups, self.groupsOf("SPAC977.02", "ensembl_gene"))
        self.assertFalse(unknownGroups & keggGroupsBefore)

    def test_genes_sharing_an_accession_do_not_become_mates(self):
        """Paralogues and homoeologs routinely share one UniProt accession; each may
        reach the kegg_id KEGG ties to that accession, neither may reach the other."""
        self.builder.SPECIE = "spo"
        self.builder.EXTERNAL_RESOURCES = {
            "ensembl_uniprot": [{"output": "ensembl_uniprot.list", "description": "t"}]}
        self.writeMapping("uniprot2kegg.list", [("up:P0CT33", "spo:SPAC212.11")])
        self.writeMapping("ensembl_uniprot.list", [
            ("GENE_A", "P0CT33", "GENE_A.1:pep", "GENE_A.1"),
            ("GENE_B", "P0CT33", "GENE_B.1:pep", "GENE_B.1"),
            ("GENE_C", "P0CT33", "GENE_C.1:pep", "GENE_C.1"),
        ])
        self.builder.processKEGGMappingData()
        self.builder.processEnsemblUniProtData()

        groups = {gene: self.groupsOf(gene, "ensembl_gene") for gene in ("GENE_A", "GENE_B", "GENE_C")}
        keggGroups = self.groupsOf("SPAC212.11", "kegg_id")
        for gene, own in groups.items():
            self.assertEqual(1, len(own), gene + " must sit in exactly its own transcript group")
            self.assertTrue(own <= keggGroups, gene + " does not reach kegg_id")
        self.assertFalse(groups["GENE_A"] & groups["GENE_B"])
        self.assertFalse(groups["GENE_B"] & groups["GENE_C"])
        self.assertFalse(groups["GENE_A"] & groups["GENE_C"])

    def test_ensembl_uniprot_pass_is_a_no_op_without_the_resource(self):
        self.builder.EXTERNAL_RESOURCES = {}
        self.assertEqual(0, self.builder.processEnsemblUniProtData())
        self.assertNotIn("ensembl_gene", self.builder.ALL_DBS)

    def test_dump_translation_keeps_every_uniprot_label_and_drops_the_rest(self):
        """Collection genebuilds label UniProt rows UniProtKB_all only; cci and cgi
        were refused as 'no usable rows' until that label was accepted."""
        import io
        dump = io.StringIO("\n".join([
            "gene_stable_id\ttranscript_stable_id\tprotein_stable_id\txref\tdb_name\tinfo_type",
            "CC1G_00001\tCC1G_00001T0\tCC1G_00001P0\tA8N0A1\tUniProtKB_all\tDEPENDENT",
            "SPAC212.11\tSPAC212.11.1\tSPAC212.11.1:pep\tP0CT33\tUniprot/SWISSPROT\tSEQUENCE_MATCH",
            "SPAC212.11\tSPAC212.11.1\tSPAC212.11.1:pep\tP0CT33\tUniProtKB_all\tDEPENDENT",
            "GLYMA_01G000100\tKRH00001\t-\tGLYMA_01G000100-T1\tUniprot_gn_trans_name\tDEPENDENT",
            "SPAC1.01\t-\t-\tQ00000\tUniprot/SPTREMBL\tDEPENDENT",
        ]) + "\n")
        out = io.StringIO()
        written, skipped = self.builder.translateEnsemblDump(dump, out, set(self.builder.ENSEMBL_UNIPROT_DBS))
        rows = [line.split("\t") for line in out.getvalue().splitlines()]
        self.assertEqual(3, written, rows)
        self.assertEqual(1, skipped, "the transcript-name row must be skipped for its label")
        self.assertEqual(["CC1G_00001", "A8N0A1", "CC1G_00001P0", "CC1G_00001T0"], rows[0])
        self.assertEqual(2, sum(1 for row in rows if row[1] == "P0CT33"),
                         "the same accession under two labels is written twice and de-duplicated at insert")
        self.assertNotIn("Q00000", out.getvalue(), "a row without a transcript has nothing to key on")

    def test_a_dump_that_cannot_be_fetched_is_reported_not_raised(self):
        """One moved collection or a network blip must not fail the whole species."""
        calls = []

        def fetch(resource, outputName, delay, maxTries):
            calls.append(resource["output"])
            if resource["output"] == "ensembl_uniprot.list":
                raise Exception("No *.uniprot.tsv.gz found in https://example.invalid/")

        self.builder.downloadEnsemblMapping = fetch
        resources = self.builder.ensemblResourcesFor("gmx")
        fetched, failed = self.builder.downloadEnsemblResources(resources, self.dataDir + "/mapping/", 0, 1)
        self.assertEqual(["ensembl_mapping.list", "ensembl_uniprot.list"], calls, "both dumps must be attempted")
        self.assertEqual(1, fetched)
        self.assertEqual(["ensembl_uniprot.list"], [name for name, _ in failed])
        self.assertIn("uniprot.tsv.gz", failed[0][1])

    def test_registry_resources_have_the_shape_the_downloader_reads(self):
        resources = self.builder.ensemblResourcesFor("gmx")
        self.assertIn("ensembl", resources)
        entrez = resources["ensembl"][0]
        for key in ("url", "species-dir", "division", "output", "description"):
            self.assertIn(key, entrez)
        self.assertEqual("ensembl_mapping.list", entrez["output"])
        uniprot = resources["ensembl_uniprot"][0]
        self.assertEqual("uniprot", uniprot["xref-type"])
        self.assertEqual("ensembl_uniprot.list", uniprot["output"])
        self.assertEqual({}, self.builder.ensemblResourcesFor("eco"), "an unregistered species gets nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
