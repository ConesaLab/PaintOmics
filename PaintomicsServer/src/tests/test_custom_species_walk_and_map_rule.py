#!/usr/bin/env python3
"""A species from customSpeciesInstaller.py gets a graph to walk, and only maps that apply.

What this guards
----------------
1. The AI walk. Every species AdminTools/customSpeciesInstaller.py put on
   paintomics.org (nben, thaz, tobl, paxi, vsub) answered "unavailable": the
   walk reads its graph from ``current/<code>/kgml/*.kgml`` and the installer
   wrote none, though it reads KEGG's reference KO KGML for every map it
   installs, relations and reactions included (143 and 117 of the 181 maps in
   paintomics.org's cache). It now writes that KGML as the species' own, with a
   box carrying one of its KOs as a ``gene`` entry named ``<code>:K00001``, the
   id the pathway documents carry, so the walk, the metabolite hub and the
   evidence relations key their nodes the way the documents do; and the KO
   symbols, so a node reads ``HK``, not ``K00844``.

2. The maps. KEGG lists a map for an organism as soon as one of its genes has a
   KO on it, so the scope organism's list gave the fungus, the alga and the
   plants Toll-like receptor signalling, Neuroactive ligand signalling,
   Cornified envelope formation, Bacterial invasion of epithelial cells and Viral
   life cycle - HIV-1. A plant, fungus or protist now loses the classes of animal
   organs, animal cell signalling, viruses and human disease and the maps of what
   only animals or bacteria make, and every species the maps that draw fewer than
   ``--min-kos`` of its KOs (two by default: one is a single box).

3. ``--rebuild-maps`` applies both to a species installed before, from the KOs
   its database holds, and leaves its identifiers as they were.

No MongoDB and no network: the KEGG tables are written to a temporary cache,
the installer's database calls go to an in-memory stand-in, and urlopen raises.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_custom_species_walk_and_map_rule
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from AdminTools import customSpeciesInstaller as installer        # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod      # noqa: E402
from src.classes.AIInterpret.walker import service                 # noqa: E402
from src.common.KeggGraph import parser as kgml_parser             # noqa: E402
from src.tests import walker_fixture as fx                         # noqa: E402

CODE, SCOPE = "tsp", "tsco"

# map -> (name, br08901 class, KOs drawn on it). Every map draws three of the
# species' KOs except Caffeine metabolism, which draws two, as it did on four of
# the species installed by 2026-11-06, and Staurosporine biosynthesis, one.
MAPS = {
    "map00010": ("Glycolysis / Gluconeogenesis", "Metabolism;Carbohydrate metabolism",
                 ["K00844", "K01810", "K00850"]),
    "map00232": ("Caffeine metabolism", "Metabolism;Biosynthesis of other secondary metabolites",
                 ["K00106", "K00365"]),
    "map00404": ("Staurosporine biosynthesis", "Metabolism;Biosynthesis of other secondary metabolites",
                 ["K19818"]),
    "map04620": ("Toll-like receptor signaling pathway", "Organismal Systems;Immune system",
                 ["K04371", "K04440", "K04441"]),
    "map04082": ("Neuroactive ligand signaling",
                 "Environmental Information Processing;Signaling molecules and interaction",
                 ["K04630", "K04631", "K04632"]),
    "map04820": ("Cytoskeleton in muscle cells", "Cellular Processes;Cell motility",
                 ["K05692", "K10352", "K12567"]),
    "map05100": ("Bacterial invasion of epithelial cells", "Human Diseases;Infectious disease: bacterial",
                 ["K05765", "K04392", "K04393"]),
    "map03250": ("Viral life cycle - HIV-1", "Genetic Information Processing;Information processing in viruses",
                 ["K90001", "K90002", "K90003"]),
    "map00604": ("Glycosphingolipid biosynthesis - ganglio series", "Metabolism;Glycan biosynthesis and metabolism",
                 ["K90004", "K90005"]),
    "map00261": ("Monobactam biosynthesis", "Metabolism;Biosynthesis of other secondary metabolites",
                 ["K90006", "K90007", "K90008"]),
    "map04712": ("Circadian rhythm - plant", "Organismal Systems;Environmental adaptation",
                 ["K12124", "K12125", "K12126"]),
    "map04144": ("Endocytosis", "Cellular Processes;Transport and catabolism",
                 ["K12471", "K12472", "K12473"]),
    # Hit by the species' KOs but not in the scope organism's list.
    "map00999": ("Biosynthesis of various plant secondary metabolites",
                 "Metabolism;Biosynthesis of other secondary metabolites", ["K00844", "K13065", "K13066"]),
}
SYMBOLS = {"K00844": "HK; hexokinase [EC:2.7.1.1]", "K01810": "GPI, pgi; glucose-6-phosphate isomerase",
           "K00850": "PFK; 6-phosphofructokinase 1"}
NON_ANIMAL_KEPT = {"map00010", "map00232", "map04712", "map04144"}

GLYCOLYSIS_KGML = """<?xml version="1.0"?>
<!DOCTYPE pathway SYSTEM "https://www.kegg.jp/kegg/xml/KGML_v0.7.2_.dtd">
<pathway name="path:ko00010" org="ko" number="00010" title="Glycolysis / Gluconeogenesis"
         image="https://www.kegg.jp/kegg/pathway/ko/ko00010.png">
  <entry id="1" name="ko:K00844 ko:K12407" type="ortholog" reaction="rn:R01786">
    <graphics name="HK" type="rectangle" x="100" y="100" width="46" height="17"/></entry>
  <entry id="2" name="ko:K01810" type="ortholog" reaction="rn:R02740">
    <graphics name="GPI" type="rectangle" x="200" y="100" width="46" height="17"/></entry>
  <entry id="3" name="ko:K00850" type="ortholog">
    <graphics name="PFK" type="rectangle" x="300" y="100" width="46" height="17"/></entry>
  <entry id="4" name="ko:K99998" type="ortholog">
    <graphics name="ABSENT" type="rectangle" x="400" y="100" width="46" height="17"/></entry>
  <entry id="5" name="cpd:C00031" type="compound">
    <graphics name="C00031" type="circle" x="100" y="200" width="8" height="8"/></entry>
  <entry id="6" name="cpd:C00668" type="compound">
    <graphics name="C00668" type="circle" x="200" y="200" width="8" height="8"/></entry>
  <entry id="7" name="cpd:C05345" type="compound">
    <graphics name="C05345" type="circle" x="300" y="200" width="8" height="8"/></entry>
  <entry id="8" name="path:map00020" type="map">
    <graphics name="TCA cycle" type="roundrectangle" x="500" y="300" width="80" height="20"/></entry>
  <entry id="9" name="undefined" type="group"><graphics type="rectangle" x="250" y="300" width="92" height="17"/>
    <component id="2"/><component id="4"/></entry>
  <entry id="10" name="undefined" type="group"><graphics type="rectangle" x="450" y="300" width="46" height="17"/>
    <component id="4"/></entry>
  <entry id="11" name="cpd:C00118" type="compound">
    <graphics name="C00118" type="circle" x="400" y="200" width="8" height="8"/></entry>
  <relation entry1="1" entry2="2" type="ECrel"><subtype name="compound" value="6"/></relation>
  <relation entry1="2" entry2="3" type="ECrel"><subtype name="compound" value="7"/></relation>
  <relation entry1="3" entry2="4" type="ECrel"><subtype name="compound" value="7"/></relation>
  <relation entry1="3" entry2="8" type="maplink"><subtype name="compound" value="7"/></relation>
  <relation entry1="9" entry2="3" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <relation entry1="10" entry2="1" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <reaction id="1" name="rn:R01786" type="irreversible">
    <substrate id="5" name="cpd:C00031"/><product id="6" name="cpd:C00668"/></reaction>
  <reaction id="2" name="rn:R02740" type="reversible">
    <substrate id="6" name="cpd:C00668"/><product id="7" name="cpd:C05345"/></reaction>
  <reaction id="4" name="rn:R99998" type="irreversible">
    <substrate id="7" name="cpd:C05345"/><product id="11" name="cpd:C00118"/></reaction>
</pathway>
"""


def chain_kgml(number, kos):
    """A reference map drawing ``kos`` as a chain of activations."""
    entries = "".join('  <entry id="%d" name="ko:%s" type="ortholog"><graphics name="%s" type="rectangle" '
                      'x="%d" y="100" width="46" height="17"/></entry>\n' % (i + 1, ko, ko, 100 * (i + 1))
                      for i, ko in enumerate(kos))
    relations = "".join('  <relation entry1="%d" entry2="%d" type="PPrel"><subtype name="activation" '
                        'value="--&gt;"/></relation>\n' % (i + 1, i + 2) for i in range(len(kos) - 1))
    return ('<?xml version="1.0"?>\n<pathway name="path:ko%s" org="ko" number="%s" title="t">\n%s%s</pathway>\n'
            % (number, number, entries, relations))


def write_cache(cache_dir, scope_maps=None):
    """The KEGG tables load_reference and build_pathway_docs read, so the
    installer never asks KEGG for anything."""
    os.makedirs(cache_dir, exist_ok=True)

    def write(name, text):
        with io.open(os.path.join(cache_dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)
    write("list_pathway.tsv", "".join("path:%s\t%s\n" % (m, v[0]) for m, v in MAPS.items()))
    write("link_pathway_ko.tsv", "".join("ko:%s\tpath:%s\nko:%s\tpath:ko%s\n" % (ko, m, ko, m[3:])
                                         for m, v in MAPS.items() for ko in v[2]))
    kos = sorted({ko for v in MAPS.values() for ko in v[2]})
    write("list_ko.tsv", "".join("ko:%s\t%s\n" % (ko, SYMBOLS.get(ko, "S%s; protein %s" % (ko[1:], ko)))
                                 for ko in kos))
    lines, last_a, last_b = [], None, None
    for m, (name, cls, _kos) in sorted(MAPS.items(), key=lambda item: item[1][1]):
        a, b = cls.split(";")
        if a != last_a:
            lines.append("A" + a)
            last_a, last_b = a, None
        if b != last_b:
            lines.append("B  " + b)
            last_b = b
        lines.append("C    %s  %s" % (m[3:], name))
    write("br08901.txt", "\n".join(lines) + "\n")
    in_scope = scope_maps if scope_maps is not None else [m for m in MAPS if m != "map00999"]
    write("list_pathway_%s.tsv" % SCOPE, "".join("%s%s\tx - Test scope\n" % (SCOPE, m[3:]) for m in in_scope))
    for m, (_name, _cls, map_kos) in MAPS.items():
        write("ko%s.xml" % m[3:], GLYCOLYSIS_KGML if m == "map00010" else chain_kgml(m[3:], map_kos))


def make_data_dir(root, scope_lineage="Eukaryotes;Fungi;Dikarya;Ascomycota",
                  own_lineage="Eukaryotes;Fungi;Dikarya;Ascomycota"):
    """KEGG_DATA with the organism lists, and every diagram already present so
    ensure_diagrams fetches nothing."""
    common = os.path.join(root, "current", "common")
    os.makedirs(os.path.join(common, "png", "thumbnails"))
    with io.open(os.path.join(common, "organisms_all.list"), "w", encoding="utf-8") as handle:
        handle.write("T00001\t%s\tTest scope organism\t%s\n" % (SCOPE, scope_lineage))
        handle.write("T00002\tmmu\tMus musculus (house mouse)\tEukaryotes;Metazoa;Chordata;Craniata\n")
    with io.open(os.path.join(common, "organisms_custom.list"), "w", encoding="utf-8") as handle:
        handle.write("T90001\t%s\tTest species\t%s\n" % (CODE, own_lineage))
    for m in MAPS:
        for path in (os.path.join(common, "png", m + ".png"),
                     os.path.join(common, "png", "thumbnails", m + "_thumb.png")):
            open(path, "wb").close()
    with io.open(os.path.join(root, "current", "species.json"), "w", encoding="utf-8") as handle:
        handle.write('{"species": [{"name": "Test species", "value": "%s"}]}' % CODE)
    return root


def annotated():
    return {ko for v in MAPS.values() for ko in v[2]}


# ----------------------------------------------------------------------------
# A MongoDB stand-in for the calls the installer makes
# ----------------------------------------------------------------------------
class FakeCollection(object):
    def __init__(self, db, name):
        self._db, self._name = db, name

    @property
    def _docs(self):
        return self._db.data.setdefault(self._name, [])

    @staticmethod
    def _matches(doc, query):
        return all(doc.get(key) == value for key, value in (query or {}).items())

    def drop(self):
        self._db.data.pop(self._name, None)
        self._db.indexes.pop(self._name, None)

    def insert_many(self, docs):
        docs = list(docs)
        if not docs:
            raise ValueError("insert_many with no documents")      # pymongo refuses it too
        self._docs.extend(dict(doc) for doc in docs)

    def create_index(self, keys):
        entry = [tuple(k) for k in keys]
        if entry not in self._db.indexes.setdefault(self._name, []):
            self._db.indexes[self._name].append(entry)

    def rename(self, new_name, dropTarget=False):
        if new_name in self._db.data and not dropTarget:
            raise RuntimeError("target exists")
        self._db.data[new_name] = self._db.data.pop(self._name, [])
        self._db.indexes[new_name] = self._db.indexes.pop(self._name, [])

    def find_one(self, query=None, projection=None):
        return next(iter(self.find(query, projection)), None)

    def find(self, query=None, projection=None):
        return [dict(doc) for doc in self._docs if self._matches(doc, query)]

    def update_one(self, query, update, upsert=False):
        for doc in self._docs:
            if self._matches(doc, query):
                doc.update(update["$set"])
                return
        if upsert:
            self._docs.append(dict(query, **update["$set"]))

    def aggregate(self, pipeline):
        """The one pipeline the installer runs, Step 1's (verify): the docs
        named in the first $match, through their mates, to the mates of the
        dbname the later $match names."""
        wanted = pipeline[0]["$match"]["display_id"]["$in"]
        target = pipeline[4]["$match"]["unwind_mate.dbname_id"]
        by_id = {doc["_id"]: doc for doc in self._docs}
        return [{"display_id": by_id[mate]["display_id"]}
                for doc in self._docs if doc.get("display_id") in wanted
                for mate in doc.get("mates", []) if by_id.get(mate, {}).get("dbname_id") == target]


class FakeDB(object):
    def __init__(self):
        self.data, self.indexes = {}, {}

    def __getitem__(self, name):
        return FakeCollection(self, name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return FakeCollection(self, name)


class FakeClient(object):
    databases = {}

    def __init__(self, *args, **kwargs):
        pass

    def __getitem__(self, name):
        return FakeClient.databases.setdefault(name, FakeDB())

    def list_database_names(self):
        return list(FakeClient.databases)

    def close(self):
        pass


def _no_network(*args, **kwargs):
    raise AssertionError("the installer asked KEGG for %r; the cache should have held it" % (args[:1],))


class InstallerTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="custom-species-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cache_dir = os.path.join(self.root, "cache")
        write_cache(self.cache_dir)
        self.data_dir = make_data_dir(os.path.join(self.root, "KEGG_DATA"))
        self.cache = installer.KeggCache(self.cache_dir, min_interval=0)
        FakeClient.databases = {}
        for target, replacement in (("pymongo.MongoClient", FakeClient),
                                    ("urllib.request.urlopen", _no_network)):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, *argv):
        out = io.StringIO()
        argv = ["customSpeciesInstaller.py", "--code=%s" % CODE, "--kegg-data-dir=%s" % self.data_dir,
                "--cache-dir=%s" % self.cache_dir, "--mongo-host=fake", "--mongo-port=1"] + list(argv)
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out):
            try:
                installer.main()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise AssertionError("installer exited %r:\n%s" % (exc.code, out.getvalue()))
        return out.getvalue()

    def plan(self, taxon, min_kos=None, scope=SCOPE):
        min_kos = installer.MIN_KOS if min_kos is None else min_kos
        with redirect_stdout(io.StringIO()) as out:
            docs, kgml, _names = installer.plan_maps(CODE, self.cache, annotated(), scope, taxon, min_kos)
        return {doc["ID"].replace(CODE, "map", 1) for doc in docs}, kgml, out.getvalue()


# ----------------------------------------------------------------------------
# 2. Which maps a species gets
# ----------------------------------------------------------------------------
class TheMapRuleTest(InstallerTestCase):
    def test_a_plant_fungus_or_protist_loses_animal_and_disease_maps(self):
        kept, _kgml, log = self.plan("non-animal")
        self.assertEqual(NON_ANIMAL_KEPT, kept)
        for m in ("map04620", "map04082", "map05100", "map04820", "map03250"):
            self.assertRegex(log, r"dropped %s .*(does not apply|does not have)" % m)
        self.assertIn("dropped map04620 Toll-like receptor signaling pathway: class Organismal Systems;"
                      "Immune system does not apply to a plant, fungus or protist", log)
        self.assertIn("dropped map03250 Viral life cycle - HIV-1: class Genetic Information Processing;"
                      "Information processing in viruses does not apply to a plant, fungus or protist", log)

    def test_what_only_animals_or_bacteria_make_goes_though_enough_enzymes_land_on_it(self):
        """Shared enzymes put these on KEGG's lists, and they pass the count."""
        kept, _kgml, log = self.plan("non-animal")
        self.assertNotIn("map00604", kept)
        self.assertNotIn("map00261", kept)
        self.assertIn("dropped map00604 Glycosphingolipid biosynthesis - ganglio series: "
                      "only animals make these glycolipids", log)
        self.assertIn("dropped map00261 Monobactam biosynthesis: only bacteria make this antibiotic", log)
        kept, _kgml, _log = self.plan("animal")
        self.assertIn("map00604", kept, "an animal keeps its own glycolipids")

    def test_an_animal_and_an_unread_lineage_keep_every_class(self):
        for taxon in ("animal", None):
            kept, _kgml, _log = self.plan(taxon)
            self.assertEqual(set(MAPS) - {"map00404", "map00999"}, kept, taxon)

    def test_a_map_drawing_fewer_than_min_kos_is_dropped_and_said_so(self):
        kept, _kgml, log = self.plan("non-animal")
        self.assertEqual(2, installer.MIN_KOS)
        self.assertNotIn("map00404", kept)
        self.assertIn("map00232", kept)
        self.assertIn("dropped map00404 Staurosporine biosynthesis: only 1 of the species' KOs is drawn on it "
                      "(fewer than 2)", log)
        kept, _kgml, log = self.plan("non-animal", min_kos=3)
        self.assertNotIn("map00232", kept)
        self.assertIn("dropped map00232 Caffeine metabolism: only 2 of the species' KOs are drawn on it "
                      "(fewer than 3)", log)

    def test_the_count_is_of_distinct_kos_drawn_not_of_links(self):
        """K00844 is linked to Staurosporine biosynthesis here, but the diagram
        does not draw it: the link table's two KOs are one on the map."""
        with io.open(os.path.join(self.cache_dir, "link_pathway_ko.tsv"), "a", encoding="utf-8") as handle:
            handle.write("ko:K00844\tpath:map00404\n")
        kept, _kgml, _log = self.plan("non-animal")
        self.assertNotIn("map00404", kept)

    def test_a_map_outside_the_scope_list_is_never_offered(self):
        kept, _kgml, log = self.plan(None)
        self.assertNotIn("map00999", kept)
        self.assertNotIn("map00999", log)

    def test_the_lineage_is_read_as_kegg_and_people_write_it(self):
        for lineage, taxon in (("Eukaryotes;Fungi;Dikarya;Ascomycota", "non-animal"),
                               ("Eukaryotes;Viridiplantae;Chlorophyta;core chlorophytes", "non-animal"),
                               ("Eukaryotes;Plants;Eudicots;Nightshade family", "non-animal"),
                               ("Eukaryotes;Sar;Stramenopiles", "non-animal"),
                               ("Eukaryotes;Metazoa;Chordata;Craniata", "animal"),
                               ("Eukaryotes;Animals;Vertebrates;Mammals", "animal"),
                               ("Prokaryotes;Bacteria;Pseudomonadati", None),
                               ("Eukaryotes;Choanoflagellata", None),
                               ("Eukaryotes;Custom", None), ("", None), (None, None)):
            self.assertEqual(taxon, installer.taxon_of(lineage), lineage)

    def test_an_unread_lineage_falls_back_to_the_scope_organism(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual("non-animal", installer.species_taxon("Eukaryotes;Custom", self.data_dir, SCOPE))
            self.assertEqual("animal", installer.species_taxon("Eukaryotes;Custom", self.data_dir, "mmu"))
            # The species' own lineage wins over its scope organism's.
            self.assertEqual("animal", installer.species_taxon("Eukaryotes;Metazoa", self.data_dir, SCOPE))
            self.assertIsNone(installer.species_taxon("Eukaryotes;Custom", self.data_dir, None))


# ----------------------------------------------------------------------------
# 1. The graph the walk reads
# ----------------------------------------------------------------------------
class TheSpeciesKgmlTest(InstallerTestCase):
    def setUp(self):
        super(TheSpeciesKgmlTest, self).setUp()
        with open(os.path.join(self.cache_dir, "ko00010.xml"), "rb") as handle:
            raw = handle.read()
        self.path = os.path.join(self.root, "%s00010.kgml" % CODE)
        with open(self.path, "wb") as handle:
            handle.write(installer.species_kgml(raw, CODE, annotated()))

    def test_a_box_with_the_species_ko_is_its_gene_and_one_without_stays_an_ortholog(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(self.path).getroot()
        self.assertEqual(("path:%s00010" % CODE, CODE), (root.get("name"), root.get("org")))
        entries = {entry.get("id"): (entry.get("type"), entry.get("name")) for entry in root.findall("entry")}
        self.assertEqual(("gene", "%s:K00844" % CODE), entries["1"], "K12407 is not the species'")
        self.assertEqual(("gene", "%s:K01810" % CODE), entries["2"])
        self.assertEqual(("ortholog", "ko:K99998"), entries["4"])
        self.assertEqual(("compound", "cpd:C00031"), entries["5"])
        self.assertEqual(("map", "path:map00020"), entries["8"])
        self.assertEqual(["2"], [c.get("id") for c in root.find("entry[@id='9']").findall("component")])
        self.assertNotIn("10", entries, "a group of KOs the species lacks")

    def test_nothing_is_drawn_on_a_box_the_species_lacks(self):
        """As in a KEGG organism's own KGML: no relation and no reaction on an
        ortholog entry, so its compounds do not join a graph with no edge."""
        import xml.etree.ElementTree as ET
        root = ET.parse(self.path).getroot()
        self.assertEqual([("1", "2"), ("2", "3"), ("3", "8"), ("9", "3")],
                         [(r.get("entry1"), r.get("entry2")) for r in root.findall("relation")])
        self.assertEqual(["1", "2"], [r.get("id") for r in root.findall("reaction")])

    def test_the_kgml_parser_keys_genes_by_the_ids_the_pathway_documents_carry(self):
        edges, types = kgml_parser.parse_pathway(self.path)
        self.assertEqual("gene", types["K00844"])
        self.assertEqual("ortholog", types["K99998"])
        pairs = {(edge.a, edge.b): edge for edge in edges}
        self.assertEqual("%s00010" % CODE, pairs[("K00844", "K01810")].pathway)
        self.assertIn(("K00844", "C00668"), pairs, "the reaction's enzyme to its product")
        genes, _compounds, _related = installer.parse_ko_kgml(open(os.path.join(self.cache_dir, "ko00010.xml"),
                                                                   "rb").read(), annotated())
        self.assertEqual({"K00844", "K01810", "K00850"}, {g["id"] for g in genes})
        self.assertEqual({"K00844", "K01810", "K00850"}, {n for n, t in types.items() if t == "gene"})


class FeatureJob(fx.FakeJob):
    def getOrganism(self):
        return CODE

    def getJobID(self):
        return "CS1"


def ko_job():
    ge = "Gene expression"
    return FeatureJob({
        "K00844": fx.FakeFeature("K00844", [fx.FakeOmicValue(ge, "g1", [True, True, True], [0.4, 1.8, 2.5])]),
        "K01810": fx.FakeFeature("K01810", [fx.FakeOmicValue(ge, "g2", [True, True, False], [0.2, 1.1, 1.9])]),
        "K00850": fx.FakeFeature("K00850", [fx.FakeOmicValue(ge, "g3", [False, False, False], [0.0, 0.1, 0.2])]),
        "K12124": fx.FakeFeature("K12124", [fx.FakeOmicValue(ge, "g4", [True, False, False], [1.5, 0.3, 0.1])]),
        "K12125": fx.FakeFeature("K12125", [fx.FakeOmicValue(ge, "g5", [False, False, False], [0.1, 0.0, 0.0])]),
    })


class AnInstallIsWalkedTest(InstallerTestCase):
    """The whole install, through main(), then the walk on what it wrote."""

    def setUp(self):
        super(AnInstallIsWalkedTest, self).setUp()
        annotation = os.path.join(self.root, "annotation.tsv")
        with io.open(annotation, "w", encoding="utf-8") as handle:
            handle.write("gene\tKEGG_ko\n")
            for i, ko in enumerate(sorted(annotated())):
                handle.write("TSP_%04d.1\tko:%s\n" % (i, ko))
        self.log = self.run_main("--name=Test species", "--annotation=%s" % annotation,
                                 "--scope-organism=%s" % SCOPE)
        self.org_dir = os.path.join(self.data_dir, "current", CODE)

    def test_every_installed_pathway_has_its_kgml_and_nothing_else_does(self):
        ids = {doc["ID"] for doc in FakeClient.databases["%s-paintomics" % CODE].data["kegg"]}
        self.assertEqual({m.replace("map", CODE) for m in NON_ANIMAL_KEPT}, ids, self.log)
        self.assertIn("verify: 5/5 sample genes resolve to KOs", self.log)
        self.assertIn("SUCCESS", self.log)
        kegg_version = FakeClient()["%s-paintomics" % CODE]["versions"].find_one({"name": "KEGG"})
        self.assertEqual((SCOPE, 2), (kegg_version["scope_organism"], kegg_version["min_kos"]))
        self.assertEqual({i + ".kgml" for i in ids}, set(os.listdir(os.path.join(self.org_dir, "kgml"))))
        with open(os.path.join(self.org_dir, "pathways.list")) as handle:
            self.assertIn("path:%s00010\tGlycolysis / Gluconeogenesis\n" % CODE, handle.read())

    def test_the_walk_can_run_on_it(self):
        self.assertTrue(net_mod.has_graph_source(CODE, self.data_dir))
        self.assertIsNone(service.no_graph_reason(CODE, self.data_dir, use_mongo=False))
        rec, network, graph, tag = service.run(ko_job(), "CS1", "pathway:%s00010" % CODE, policy="greedy",
                                               data_dir=self.data_dir, use_mongo=False)
        self.assertEqual("KEGG:%s00010" % CODE, tag)
        self.assertTrue(rec["walk"]["chain"], "the walk took no step")
        self.assertIn("g:K00844", graph.nodes)
        self.assertNotIn("g:K99998", network.nodes, "a KO the species lacks became a gene")
        self.assertNotIn("c:C00118", network.nodes, "a compound only a missing enzyme reaches")
        linked = {end for pair in network.edges for end in pair}
        self.assertEqual(set(), set(network.nodes) - linked, "nodes with no edge")
        self.assertEqual("HK", network.nodes["g:K00844"]["label"])
        self.assertEqual("GPI", network.nodes["g:K01810"]["label"])
        self.assertEqual("K00844", network.symbol_to_kegg["HK"])
        self.assertEqual("Glycolysis / Gluconeogenesis", network.pathway_name("KEGG:%s00010" % CODE))
        rec, _network, _graph, tag = service.run(ko_job(), "CS1", "network", policy="greedy",
                                                 data_dir=self.data_dir, use_mongo=False)
        self.assertEqual("network", tag)
        self.assertTrue(rec["walk"]["chain"])


# ----------------------------------------------------------------------------
# 3. --rebuild-maps on a species installed before
# ----------------------------------------------------------------------------
class RebuildMapsTest(InstallerTestCase):
    def setUp(self):
        super(RebuildMapsTest, self).setUp()
        db = FakeClient()["%s-paintomics" % CODE]
        db.data["dbname"] = [{"_id": "d-ko", "dbname": "kegg_id"}, {"_id": "d-sym", "dbname": "kegg_gene_symbol"},
                             {"_id": "d-gene", "dbname": "external_gene_id"}]
        kos = sorted(annotated())
        db.data["xref"] = ([{"_id": "x-" + ko, "display_id": ko, "dbname_id": "d-ko", "mates": ["x-" + ko]}
                            for ko in kos]
                           + [{"_id": "g-%d" % i, "display_id": "TSP_%04d" % i, "dbname_id": "d-gene",
                               "mates": ["g-%d" % i, "x-" + ko]} for i, ko in enumerate(kos)])
        # What the 2026-11-06 install wrote: every map in the scope list.
        db.data["kegg"] = [{"ID": m.replace("map", CODE), "name": v[0], "source": "KEGG", "genes": []}
                           for m, v in MAPS.items() if m != "map00999"]
        db.data["versions"] = [{"name": "KEGG", "date": "20261106 2027"},
                               {"name": "MAPPING", "date": "20261106 2027"}]
        self.db = db
        self.xref_before = [dict(doc) for doc in db.data["xref"]]
        self.org_dir = os.path.join(self.data_dir, "current", CODE)
        os.makedirs(self.org_dir)
        with open(os.path.join(self.org_dir, "gene2pathway.list"), "w") as handle:
            handle.write("%s:K04371\tpath:%s04620\n" % (CODE, CODE))

    def test_an_old_install_reads_as_one_with_no_graph(self):
        self.assertFalse(net_mod.has_graph_source(CODE, self.data_dir))
        problems = installer.verify(CODE, self.data_dir, "fake", 1, [])
        self.assertTrue(any("kgml" in p and "--rebuild-maps" in p for p in problems), problems)

    def test_it_applies_the_rule_writes_the_graph_and_keeps_the_identifiers(self):
        log = self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps")
        kept = {m.replace("map", CODE) for m in NON_ANIMAL_KEPT}
        self.assertEqual(kept, {doc["ID"] for doc in self.db.data["kegg"]}, log)
        self.assertEqual(self.xref_before, self.db.data["xref"], "the identifier graph changed")
        versions = {doc["name"]: doc for doc in self.db.data["versions"]}
        self.assertEqual({"name": "MAPPING", "date": "20261106 2027"}, versions["MAPPING"])
        self.assertNotEqual("20261106 2027", versions["KEGG"]["date"])
        self.assertEqual((SCOPE, 2), (versions["KEGG"]["scope_organism"], versions["KEGG"]["min_kos"]))
        self.assertIn([("source", 1)], self.db.indexes["kegg"])
        self.assertNotIn("kegg_incoming", self.db.data)
        self.assertEqual({i + ".kgml" for i in kept}, set(os.listdir(os.path.join(self.org_dir, "kgml"))))
        with open(os.path.join(self.org_dir, "gene2pathway.list")) as handle:
            rows = handle.read()
        self.assertNotIn("04620", rows)
        self.assertIn("%s:K00844\tpath:%s00010" % (CODE, CODE), rows)
        self.assertIn("taxon: non-animal (from lineage Eukaryotes;Fungi;Dikarya;Ascomycota)", log)
        self.assertIn("SUCCESS", log)
        self.assertIn("Restart the server", log, "a running process keeps the old pathway list until then")
        self.assertFalse(os.path.exists(os.path.join(self.org_dir, "gene2pathway.list.tmp")))
        self.assertEqual([], installer.verify(CODE, self.data_dir, "fake", 1, []))
        self.assertTrue(net_mod.has_graph_source(CODE, self.data_dir))

    def test_a_second_rebuild_with_another_threshold_removes_the_files_of_maps_it_drops(self):
        self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps")
        self.assertIn("%s00232.kgml" % CODE, os.listdir(os.path.join(self.org_dir, "kgml")))
        self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps", "--min-kos=3")
        self.assertNotIn("%s00232.kgml" % CODE, os.listdir(os.path.join(self.org_dir, "kgml")))
        self.assertNotIn("%s00232" % CODE, {doc["ID"] for doc in self.db.data["kegg"]})
        self.assertFalse(os.path.exists(os.path.join(self.org_dir, "kgml.incoming")))
        self.assertFalse(os.path.exists(os.path.join(self.org_dir, "kgml.outgoing")))

    def test_the_scope_organism_is_needed_once_then_read_back(self):
        """An install before the rule recorded no scope organism, and without
        one every map outside the excluded classes would be offered."""
        with self.assertRaises(AssertionError) as caught:
            self.run_main("--rebuild-maps")
        self.assertIn("Pass --scope-organism", str(caught.exception))
        self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps")
        log = self.run_main("--rebuild-maps", "--min-kos=3")
        self.assertIn("pathway maps in %s's list" % SCOPE, log)
        self.assertNotIn("map00999".replace("map", CODE), {doc["ID"] for doc in self.db.data["kegg"]})

    @unittest.skipIf(hasattr(os, "getuid") and os.getuid() == 0, "root writes a read-only directory")
    def test_files_it_may_not_write_stop_it_before_the_database_changes(self):
        before = [dict(doc) for doc in self.db.data["kegg"]]
        os.chmod(self.org_dir, 0o555)
        self.addCleanup(os.chmod, self.org_dir, 0o755)
        with self.assertRaises(AssertionError) as caught:
            self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps")
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(before, self.db.data["kegg"])

    def test_a_dry_run_writes_nothing(self):
        before = [dict(doc) for doc in self.db.data["kegg"]]
        log = self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps", "--dry-run")
        self.assertIn("DRY RUN", log)
        self.assertIn("dropped map04620", log)
        self.assertEqual(before, self.db.data["kegg"])
        self.assertFalse(os.path.exists(os.path.join(self.org_dir, "kgml")))

    def test_a_species_this_tool_did_not_install_is_refused(self):
        with open(os.path.join(self.data_dir, "current", "common", "organisms_custom.list"), "w") as handle:
            handle.write("")
        with self.assertRaises(AssertionError) as caught:
            self.run_main("--scope-organism=%s" % SCOPE, "--rebuild-maps")
        self.assertIn("only rebuilds species it installed", str(caught.exception))
        self.assertEqual(len(MAPS) - 1, len(self.db.data["kegg"]), "the pathways of a refused species changed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
