#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install a CUSTOM species from a gene→KO annotation table.

Serves organisms KEGG does not cover (Nicotiana benthamiana, Trichoderma
harzianum, ...). The user supplies a functional annotation produced by
eggNOG-mapper, BlastKOALA/KAAS, or any tool that assigns KEGG Orthology (KO)
identifiers to genes. This tool synthesizes everything the Paintomics runtime
reads for a species, using KEGG's *reference* KO pathways (ko00010-style KGML,
type="ortholog" entries) instead of per-organism pathway files.

The runtime contract this satisfies (verified against the live code 2026-08-14):

  * ``<code>-paintomics.kegg``     one doc per pathway; ``genes[].id`` is matched
                                   case-insensitively against ``xref.display_id``
                                   (PathwayAcquisitionJob.py:1751-1755). We store
                                   bare KO ids ("K00001") on both sides.
  * ``<code>-paintomics.xref``     identifier graph. Step 1 matches the user's
                                   feature names on ``display_id`` (no dbname
                                   filter), unwinds ``mates`` and keeps mates of
                                   the target dbname (FeatureNamesToKeggIDsMapper
                                   .py:114-143). Species without an organismDB
                                   entry fall back to targets ``kegg_id`` +
                                   ``kegg_gene_symbol`` — both dbname docs MUST
                                   exist or the mapper's forked workers die
                                   (FeatureNamesToKeggIDsMapper.py:209-210).
  * ``<code>-paintomics.dbname``   the ID-type registry backing the above.
  * ``<code>-paintomics.versions`` "KEGG" + "MAPPING" docs (AdminServlet.py:132
                                   indexes [0] and 500s without them).
  * ``current/<code>/gene2pathway.list``  read unconditionally by the metagenes
                                   R script (generateMetaGenes.R:104); lines are
                                   ``<code>:<featureID>\tpath:<code><number>``.
  * ``current/common/organisms_all.list`` must contain the code or species.json
                                   regeneration aborts (DBManager.py:1675-1680)
                                   — including regeneration triggered by any
                                   LATER standard install.
  * ``current/species.json``       the organism dropdown.
  * Pathway diagram PNGs are species-independent (current/common/png/map*.png);
    only maps newer than the local common snapshot are fetched, with 300px
    thumbnails to match the installed ones.
  * ``current/<code>/kgml/<code><number>.kgml``  one per installed pathway: the
    reference KO map with every box carrying one of the species' KOs turned into
    a ``gene`` entry named ``<code>:K00001``, the way a KEGG organism's own KGML
    names its genes (species_kgml). Boxes for KOs the species lacks stay
    ``ortholog`` entries with no relation or reaction on them, as in a KEGG
    organism's KGML; the rest of KEGG's relations and reactions are kept as
    drawn. Three readers use it: the AI walk's graph
    (walker/network.py keys a gene node by the id the pathway documents carry,
    so ``g:K00001``), the metabolite hub's compound neighbourhoods
    (common/KeggGraph/store.py) and the evidence relations of the pathway view
    (PathwayEvidence._keggRelationGraph).
  * ``current/<code>/pathways.list`` and ``mapping/kegg2genesymbol.list``: the
    pathway names and the KO symbols the walk labels its nodes with.

Which maps a species gets (select_maps, build_pathway_docs). KEGG lists a map
for an organism as soon as one of its genes has a KO on it, so a scope
organism's list carries maps the species cannot run: on 2026-11-06 the plants,
the alga and the fungus installed on paintomics.org each got Toll-like receptor
signalling, Neuroactive ligand signalling, Cornified envelope formation and
Bacterial invasion of epithelial cells. A map is kept only when

  1. the scope organism's list has it (or, with no scope organism, its class is
     not one of DEFAULT_EXCLUDED_CLASSES);
  2. for a plant, fungus or protist (taxon_of: the species' own lineage, else
     the scope organism's KEGG lineage), its class is not one that describes
     animal organs, animal cells or human disease (NON_ANIMAL_EXCLUDED_CLASSES,
     NON_ANIMAL_EXCLUDED_MAPS); an animal, a prokaryote or a lineage the tool
     cannot read keeps every class;
  3. at least ``--min-kos`` (MIN_KOS, 2) of the species' KOs are drawn on it.

Every map dropped is logged with its reason, and the KEGG version document
records the scope organism and ``--min-kos`` (map_rule). ``--rebuild-maps``
applies the rule, and writes the KGML, for a species this tool installed before
either existed, reading its KOs from the installed database: the identifier
graph is not touched, so no annotation file is needed. It reuses the recorded
scope organism when none is given; installs before 2026-11-07 recorded none.

Deliberately NOT produced, and what each absence does to the species:

  * ``hubData/``: nothing reads it where ``kgml/`` is present (store.get_graph).
  * ``pathways_network.json``: GET /kegg_data/pathway_network/<code> answers
    404, and the Step 3 network card says no KEGG pathway network is available
    for this organism. The Tools rail beside it and the pathway table keep working.
  * Reactome/MapMan/OmniPath collections: Step 1 lists those sources as "not
    installed" and they cannot be ticked.

Mapping, enrichment, the pathway maps and the AI walk work as for any other
organism. A species installed before ``kgml/`` was written has no graph until
``--rebuild-maps`` runs: the walk routes answer ``unavailable`` and the AI panel
and Step 4's Walk column say why (walker/service.py NO_GRAPH_MESSAGE).

Usage:
  python customSpeciesInstaller.py \
      --code=nben --name="Nicotiana benthamiana" \
      --lineage="Eukaryotes;Plants;Eudicots;Nightshade family" \
      --annotation=/path/to/annotation.tsv \
      --scope-organism=nta [--min-kos=2] [--dry-run] [--verify-only]

  # a species installed earlier: apply the map rule and write its KGML
  python customSpeciesInstaller.py --code=nben --scope-organism=nta \
      --rebuild-maps [--min-kos=2] [--dry-run]

Annotation format (auto-detected):
  * eggNOG-mapper ``.emapper.annotations`` (TSV, ``#query`` header line), or
  * generic TSV with a header row: first column = feature id, plus a column
    whose name contains ``ko`` (case-insensitive; cells like ``ko:K00001,ko:K00002``
    or ``K00001``), optional ``name``/``description`` columns.
  Feature ids ending in ``.<digits>`` are treated as transcripts and registered
  both as-is and collapsed to their gene id.
"""
import argparse
import gzip
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as XMLParser
from collections import defaultdict
from datetime import datetime

KEGG_REST = "https://rest.kegg.jp"
KO_RE = re.compile(r"^K\d{5}$")
TRANSCRIPT_SUFFIX_RE = re.compile(r"\.\d+$")
# Pathway classes whose reference maps make no sense outside their taxon when
# no --scope-organism narrows the map set for us.
DEFAULT_EXCLUDED_CLASSES = ("Human Diseases", "Drug Development")
# Fewest of the species' KOs a map must draw to be kept. A map drawing one is
# one painted box, an enzyme other maps share: on the five species installed on
# paintomics.org by 2026-11-06 that was Staurosporine and Exopolysaccharide
# biosynthesis, lacto-series glycosphingolipids, Diterpenoid biosynthesis. Two
# is kept on purpose. At three, Anthocyanin biosynthesis left Petunia and
# N. benthamiana (KEGG's own tobacco genome draws one KO on it) and Nitrogen
# cycle, the nitrate assimilation every one of them has, left all five; what
# three would also remove, Caffeine metabolism in four and a few bacterial or
# animal maps, costs a sparse row in the table. --min-kos 3 is that stricter cut.
MIN_KOS = 2
# KEGG BRITE br08901 classes ("A" or "A;B", the classification the pathway
# documents carry) whose maps describe animal organs and tissues, animal
# cell-to-cell signalling, or human disease and drugs. Not applied to a plant,
# fungus or protist's own Environmental adaptation or Aging maps (Circadian
# rhythm - plant, Plant-pathogen interaction, Longevity regulating pathway -
# multiple species), which KEGG already narrows by taxon.
NON_ANIMAL_EXCLUDED_CLASSES = (
    "Human Diseases",
    "Drug Development",
    "Organismal Systems;Immune system",
    "Organismal Systems;Endocrine system",
    "Organismal Systems;Circulatory system",
    "Organismal Systems;Digestive system",
    "Organismal Systems;Excretory system",
    "Organismal Systems;Nervous system",
    "Organismal Systems;Sensory system",
    "Organismal Systems;Development and regeneration",
    # neuroactive ligands, hormones, cytokines, ECM receptors, cell adhesion
    "Environmental Information Processing;Signaling molecules and interaction",
    # focal adhesion, adherens, tight and gap junctions, stem-cell pluripotency
    "Cellular Processes;Cellular community - eukaryotes",
)
# Maps of an otherwise shared class that are defined by an animal cell type.
NON_ANIMAL_EXCLUDED_MAPS = {
    "map04820": "it is about muscle cells",
    "map04148": "it is the clearance of dead cells by animal phagocytes",
}
# The second field of a KEGG lineage (organisms_all.list) for eukaryotes that
# are not animals, plus the words a person writes in --lineage for them.
NON_ANIMAL_KINGDOMS = ("viridiplantae", "plants", "fungi", "protists", "sar", "discoba",
                       "amoebozoa", "rhodophyta", "metamonada", "haptista", "cryptophyceae")
ANIMAL_KINGDOMS = ("metazoa", "animals")
_ID_ALPHABET = "0123456789abcdefABCDEF"  # same alphabet the standard build mints


# --------------------------------------------------------------------------- #
#  Small utilities                                                            #
# --------------------------------------------------------------------------- #

def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def serverconf():
    """Best-effort import of the app configuration (defensive: the tool must
    stay usable on a build host where src/ is not importable)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.dirname(here)
        if src not in sys.path:
            sys.path.insert(0, src)
        from conf import serverconf as sc
        return sc
    except Exception:
        return None


class KeggCache(object):
    """Cached, rate-limited KEGG REST fetcher. Files persist in cache_dir so a
    re-run (or a second custom species) costs no repeat downloads."""

    def __init__(self, cache_dir, min_interval=0.35):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self._last = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def fetch(self, path, cache_name, binary=False, optional=False):
        target = os.path.join(self.cache_dir, cache_name)
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            mode = "rb" if binary else "r"
            with open(target, mode) as fh:
                return fh.read()
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        url = KEGG_REST + path
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as ex:
            if optional and ex.code in (400, 404):
                return None
            raise
        tmp = target + ".part"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, target)  # atomic: no truncated cache entries
        return payload if binary else payload.decode("utf-8", "replace")


def generate_random_id(used):
    while True:
        rid = "".join(random.choices(_ID_ALPHABET, k=24))
        if rid not in used:
            used.add(rid)
            return rid


# --------------------------------------------------------------------------- #
#  Annotation parsing                                                         #
# --------------------------------------------------------------------------- #

def _extract_kos(cell):
    kos = set()
    for token in re.split(r"[,;\s]+", (cell or "").strip()):
        token = token.strip().replace("ko:", "")
        if KO_RE.match(token):
            kos.add(token)
    return kos


def parse_annotation(path):
    """Return (gene2ko, transcript2gene, gene_meta).

    gene2ko: gene id -> set(KO); transcript2gene: transcript id -> gene id
    (only where the two differ); gene_meta: gene id -> (name, description).
    Handles eggNOG-mapper native output and generic TSV. Blank/'-'/'#' KO cells
    mean "no assignment" and the feature is skipped (an unmapped feature and an
    absent feature behave identically in Step 1).
    """
    opener = gzip.open if path.endswith(".gz") else open
    gene2ko = defaultdict(set)
    transcript2gene = {}
    gene_meta = {}

    def _columns_from(header):
        lowered = [h.strip().lower() for h in header]
        ko = next((i for i, h in enumerate(lowered)
                   if "kegg_ko" in h or h == "ko"), None)
        if ko is None:
            ko = next((i for i, h in enumerate(lowered) if "ko" in h), 1)
        name = next((i for i, h in enumerate(lowered)
                     if "preferred_name" in h or h == "name"), None)
        desc = next((i for i, h in enumerate(lowered) if "description" in h), None)
        return ko, name, desc

    with opener(path, "rt", encoding="utf-8-sig", errors="replace") as fh:
        ko_col = None  # columns resolve on the first header/data line
        name_col = desc_col = None
        for raw in fh:
            line = raw.rstrip("\n")
            if not line or line.startswith("##"):
                continue
            if ko_col is None:
                if line.startswith("#"):  # eggNOG-mapper '#query ...' header
                    ko_col, name_col, desc_col = _columns_from(
                        line.lstrip("#").split("\t"))
                    continue
                first = line.split("\t")
                # A header row never carries a K-number outside column 0; if
                # this first line already looks like data, fall back to the
                # positional layout (id<TAB>ko) and process the line itself.
                if any(_extract_kos(c) for c in first[1:]):
                    ko_col, name_col, desc_col = 1, None, None
                else:
                    ko_col, name_col, desc_col = _columns_from(first)
                    continue
            cols = line.split("\t")
            if len(cols) <= ko_col:
                continue
            qid = cols[0].strip()
            if not qid:
                continue
            kos = _extract_kos(cols[ko_col])
            gid = TRANSCRIPT_SUFFIX_RE.sub("", qid)
            if gid != qid:
                transcript2gene[qid] = gid
            if not kos:
                continue
            gene2ko[gid].update(kos)
            if gid not in gene_meta:
                name = cols[name_col].strip() if name_col is not None and len(cols) > name_col else ""
                desc = cols[desc_col].strip() if desc_col is not None and len(cols) > desc_col else ""
                if name in ("-", "#", "nan"):
                    name = ""
                if desc in ("-", "#", "nan"):
                    desc = ""
                gene_meta[gid] = (name, desc[:300])
    return gene2ko, transcript2gene, gene_meta


# --------------------------------------------------------------------------- #
#  KEGG reference data                                                        #
# --------------------------------------------------------------------------- #

def load_reference(cache, scope_organism):
    """Fetch the small global tables: map list, KO->map links, KO names, BRITE
    classes and (optionally) the scope organism's own pathway list."""
    map_names = {}
    for line in cache.fetch("/list/pathway", "list_pathway.tsv").splitlines():
        if "\t" in line:
            pid, name = line.split("\t", 1)
            map_names[pid.replace("path:", "").strip()] = name.strip()

    ko2maps = defaultdict(set)
    for line in cache.fetch("/link/pathway/ko", "link_pathway_ko.tsv").splitlines():
        if "\t" not in line:
            continue
        ko, path = line.split("\t")
        ko = ko.replace("ko:", "").strip()
        path = path.replace("path:", "").strip()
        if path.startswith("map"):  # every link is duplicated in ko-space; keep one
            ko2maps[ko].add(path)

    ko_names = {}
    for line in cache.fetch("/list/ko", "list_ko.tsv").splitlines():
        if "\t" not in line:
            continue
        ko, name = line.split("\t", 1)
        ko = ko.replace("ko:", "").strip()
        symbol = name.split(";")[0].split(",")[0].strip()
        ko_names[ko] = (symbol, name.strip()[:300])

    classes = {}
    cur_a, cur_b = "", ""
    for line in cache.fetch("/get/br:br08901", "br08901.txt").splitlines():
        if line.startswith("A"):
            cur_a = line[1:].strip()
        elif line.startswith("B  "):
            cur_b = line[3:].strip()
        elif line.startswith("C    "):
            num = line[5:].split()[0]
            classes["map" + num] = cur_a + ";" + cur_b

    scope_maps = None
    if scope_organism:
        listing = cache.fetch("/list/pathway/" + scope_organism,
                              "list_pathway_%s.tsv" % scope_organism)
        scope_maps = set()
        for line in listing.splitlines():
            pid = line.split("\t")[0].strip()
            num = re.sub(r"\D", "", pid)
            if len(num) == 5:
                scope_maps.add("map" + num)
        if not scope_maps:
            raise SystemExit("Scope organism '%s' returned no pathways — is the "
                             "code right?" % scope_organism)
    return map_names, ko2maps, ko_names, classes, scope_maps


def taxon_of(lineage):
    """"animal", "non-animal" (a eukaryote that is not an animal) or None.

    Reads a KEGG lineage ("Eukaryotes;Fungi;Dikarya;Ascomycota") or what a
    person passed as --lineage ("Eukaryotes;Plants;Eudicots"). None for a
    prokaryote and for anything it cannot place ("Eukaryotes;Custom",
    choanoflagellates): the class rule is applied only where it is sure.
    """
    parts = [p.strip().lower() for p in (lineage or "").split(";") if p.strip()]
    if any(p in ANIMAL_KINGDOMS for p in parts):
        return "animal"
    if len(parts) >= 2 and parts[0] == "eukaryotes" and parts[1] in NON_ANIMAL_KINGDOMS:
        return "non-animal"
    return None


def lineage_of(kegg_data_dir, code):
    """The lineage column of ``code``'s row in organisms_custom.list, else in
    organisms_all.list (KEGG's own list, so a scope organism is found there).
    None when neither lists it."""
    if not code:
        return None
    common_dir = os.path.join(kegg_data_dir, "current", "common")
    for basename in ("organisms_custom.list", "organisms_all.list"):
        path = os.path.join(common_dir, basename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                row = line.rstrip("\n").split("\t")
                if len(row) >= 4 and row[1] == code:
                    return row[3]
    return None


def excluded_for_taxon(map_id, classification, taxon):
    """Why the class rule drops a map for this taxon, or None to keep it."""
    if taxon != "non-animal":
        return None
    for cls in NON_ANIMAL_EXCLUDED_CLASSES:
        if classification == cls or classification.startswith(cls + ";"):
            return "class %s does not apply to a plant, fungus or protist" % classification
    if map_id in NON_ANIMAL_EXCLUDED_MAPS:
        return "%s, which a plant, fungus or protist does not have" % NON_ANIMAL_EXCLUDED_MAPS[map_id]
    return None


def select_maps(annotated_kos, ko2maps, classes, scope_maps, taxon=None):
    """The maps the species' KOs reach that rules 1 and 2 keep (module
    docstring), as {map: KOs on it by the link table}, and [(map, reason)] for
    the ones rule 2 drops. Maps outside the scope list are not reported: KEGG
    never offered them for the scope organism."""
    hit = defaultdict(set)
    for ko in annotated_kos:
        for m in ko2maps.get(ko, ()):
            hit[m].add(ko)
    selected, dropped = {}, []
    for m, kos in sorted(hit.items()):
        cls = classes.get(m, "")
        if scope_maps is not None:
            if m not in scope_maps:
                continue
        elif cls.split(";")[0] in DEFAULT_EXCLUDED_CLASSES:
            continue
        why = excluded_for_taxon(m, cls, taxon)
        if why:
            dropped.append((m, why))
            continue
        selected[m] = kos
    return selected, dropped


def parse_ko_kgml(xml_bytes, annotated_kos):
    """Flatten one reference KGML exactly the way the standard installer does
    (common_build_database.py:2852-2911), with two deliberate differences:
    entries are type="ortholog" (the reference-map flavour of "gene"), and only
    KOs present in THIS organism's annotation are kept, so pathway gene sets —
    and therefore the Step 2 enrichment universe — describe the organism, not
    the whole reference map."""
    genes, compounds, related = [], [], []
    root = XMLParser.fromstring(xml_bytes)
    own_number = re.sub(r"\D", "", root.get("name") or "")
    for child in root:
        entry_type = child.get("type")
        if entry_type not in ("ortholog", "compound", "map"):
            continue  # same silent skip as the standard build
        graphic = child.find("graphics")
        if graphic is None:
            continue
        box = {
            "x": graphic.get("x"),
            "y": graphic.get("y"),
            "height": graphic.get("height"),
            "width": graphic.get("width"),
        }
        if entry_type == "ortholog":
            for token in (child.get("name") or "").split(" "):
                ko = token.replace("ko:", "").strip()
                if ko in annotated_kos:
                    entry = dict(box)
                    entry["id"] = ko
                    genes.append(entry)
        elif entry_type == "compound":
            for token in (child.get("name") or "").split(" "):
                entry = dict(box)
                entry["id"] = token.replace("cpd:", "").strip()
                compounds.append(entry)
        else:  # map
            number = re.sub(r"\D", "", child.get("name") or "")
            if len(number) != 5 or number == own_number:
                continue
            entry = dict(box)
            entry["id"] = number
            entry["name"] = graphic.get("name")
            related.append(entry)
    return genes, compounds, related


def species_kgml(xml_bytes, code, annotated_kos):
    """The reference KO map as this species' own KGML, as bytes.

    The KGML of a KEGG organism names its genes ``mmu:12345`` in ``gene``
    entries and keeps the boxes it has no gene for as ``ortholog`` entries
    (``ko:K00129``), with no relation or reaction on them: on paintomics.org's
    tatv and nta installs every reaction's enzyme is a gene entry and no
    relation touches an ortholog entry. Here a species' gene id is its KO, the
    id its pathway documents carry (parse_ko_kgml), so an ortholog box holding
    any of the species' KOs becomes a ``gene`` entry naming those KOs,
    ``<code>:K00001``, and the KOs it lacks leave that box; a box with none of
    them stays an ortholog entry, and the relations and reactions drawn on it
    are dropped, as are a group's ortholog members and a group left with none.
    Kept, the reference map's 8,611 reactions on ortholog boxes (thaz,
    2026-11-07) put 3,220 compounds with no edge into the walk's graph and the
    KOs the species lacks into the metabolite hub's. Every reader strips the
    prefix (KeggGraph.parser._names, PathwayEvidence), so the graph's gene ids
    are the documents' gene ids. The pathway is renamed ``path:<code><number>``:
    the walk tags an edge with it, and a pathway walk finds its map by the
    job's pathway id.
    """
    root = XMLParser.fromstring(xml_bytes)
    number = re.sub(r"\D", "", root.get("name") or "")
    root.set("name", "path:%s%s" % (code, number))
    root.set("org", code)
    absent = set()                     # entry ids the species has nothing in
    for entry in root.findall("entry"):
        if entry.get("type") != "ortholog":
            continue
        own = [ko for ko in (t.replace("ko:", "").strip()
                             for t in (entry.get("name") or "").split())
               if ko in annotated_kos]
        if own:
            entry.set("type", "gene")
            entry.set("name", " ".join("%s:%s" % (code, ko) for ko in own))
        else:
            absent.add(entry.get("id"))
    for group in root.findall("entry"):
        if group.get("type") != "group":
            continue
        for component in group.findall("component"):
            if component.get("id") in absent:
                group.remove(component)
        if group.find("component") is None:
            absent.add(group.get("id"))
            root.remove(group)
    for relation in root.findall("relation"):
        if relation.get("entry1") in absent or relation.get("entry2") in absent:
            root.remove(relation)
    for reaction in root.findall("reaction"):
        if reaction.get("id") in absent:
            root.remove(reaction)
    return XMLParser.tostring(root, encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Assembly                                                                   #
# --------------------------------------------------------------------------- #

def build_pathway_docs(code, cache, selected, map_names, classes, annotated_kos,
                       min_kos=MIN_KOS):
    """(docs, kgml, skipped): a pathway document per kept map, the species'
    KGML for each by document ID, and [(map, reason)] for the maps dropped
    here. Rule 3 counts the distinct KOs the diagram draws, which is what the
    map paints and the enrichment counts, not the link table's count."""
    docs = []
    kgml = {}
    skipped = []
    for m in sorted(selected):
        number = m.replace("map", "")
        raw = cache.fetch("/get/ko%s/kgml" % number, "ko%s.xml" % number,
                          binary=True, optional=True)
        if raw is None:
            skipped.append((m, "KEGG has no reference KGML for it"))
            continue
        genes, compounds, related = parse_ko_kgml(raw, annotated_kos)
        drawn = len({g["id"] for g in genes})
        if not drawn:
            # The link table said the map is hit but the diagram carries none of
            # our KOs (global overview maps can differ); nothing would paint.
            skipped.append((m, "none of the species' KOs is drawn on it"))
            continue
        if drawn < min_kos:
            skipped.append((m, "only %d of the species' KOs %s drawn on it (fewer than %d)"
                            % (drawn, "is" if drawn == 1 else "are", min_kos)))
            continue
        kgml[code + number] = species_kgml(raw, code, annotated_kos)
        docs.append({
            "ID": code + number,
            "name": map_names.get(m, m),
            "classification": classes.get(m, "Unclassified;Unclassified"),
            "source": "KEGG",       # anything else is filtered out of the UI
            "featureDB": "kegg_id",
            "genes": genes,
            "compounds": compounds,
            "relatedPathways": related,
        })
    return docs, kgml, skipped


def plan_maps(code, cache, annotated_kos, scope_organism, taxon, min_kos):
    """Steps 2-3 of both modes: the reference tables, the map rule, the pathway
    documents and their KGML. Logs every map dropped and why. Returns
    (kegg_docs, kgml, ko_names)."""
    map_names, ko2maps, ko_names, classes, scope_maps = load_reference(cache, scope_organism)
    selected, dropped = select_maps(annotated_kos, ko2maps, classes, scope_maps, taxon)
    log("  %d annotated KOs: %d pathway maps %s, %s"
        % (len(annotated_kos), len(selected) + len(dropped),
           "in %s's list" % scope_organism if scope_maps else "outside the excluded classes",
           "taxon %s" % taxon if taxon else "taxon not recognised, so no class is dropped for it"))

    log("STEP 3. Building pathway documents from reference KGML (at least %d KOs a map)" % min_kos)
    kegg_docs, kgml, skipped = build_pathway_docs(code, cache, selected, map_names, classes,
                                                  annotated_kos, min_kos)
    for m, why in sorted(dropped + skipped):
        log("  dropped %s %s: %s" % (m, map_names.get(m, m), why))
    kos_in_pathways = {g["id"] for doc in kegg_docs for g in doc["genes"]}
    log("  %d pathways kept, %d dropped; %d distinct KOs placed on diagrams"
        % (len(kegg_docs), len(dropped) + len(skipped), len(kos_in_pathways)))
    return kegg_docs, kgml, ko_names


def build_identifier_graph(gene2ko, transcript2gene, gene_meta, ko_names,
                           annotated_kos):
    """Create dbname/xref documents. Mate topology (mirrors what the standard
    build's shared-transcript mechanism produces):

        gene    <-> its KOs, plus itself
        transcript (id form with .N) <-> the same KOs, plus itself
        KO      <-> itself, every gene/transcript carrying it, and its symbol
        symbol  <-> itself and its KO

    Self-mates matter: they are what lets a user upload KO ids (or symbols)
    directly and still resolve to the kegg_id target."""
    used_ids = set()
    dbnames = {}
    labels = {"kegg_id": "KEGG Orthology", "kegg_gene_symbol": "Gene symbol",
              "external_gene_id": "Gene ID", "external_transcript_id": "Transcript ID"}
    for name in ("kegg_id", "kegg_gene_symbol",
                 "external_gene_id", "external_transcript_id"):
        # Same doc shape the standard build dumps (display_label/dbname_type are
        # what the admin tools show), so custom species read identically there.
        dbnames[name] = {"_id": generate_random_id(used_ids), "dbname": name,
                         "display_label": labels[name],
                         "dbname_type": "Identifier"}

    xrefs = {}

    def new_xref(display_id, dbname, description):
        rid = generate_random_id(used_ids)
        xrefs[rid] = {"_id": rid, "display_id": display_id,
                      "dbname_id": dbnames[dbname]["_id"],
                      "description": description, "mates": {rid}}
        return rid

    # KO rows cover EVERY annotated KO, not just those placed on a diagram, so
    # Step 1 reports genes as mapped even when their pathways are out of scope
    # (native species likewise carry xrefs for genes that sit in no pathway).
    ko_row = {}
    for ko in sorted(annotated_kos):
        symbol, definition = ko_names.get(ko, ("", "KEGG Orthology " + ko))
        ko_row[ko] = new_xref(ko, "kegg_id", definition)
        if symbol:
            sid = new_xref(symbol, "kegg_gene_symbol", definition)
            xrefs[sid]["mates"].add(ko_row[ko])
            xrefs[ko_row[ko]]["mates"].add(sid)

    linked_genes = 0
    for gid in sorted(gene2ko):
        kos = sorted(k for k in gene2ko[gid] if k in ko_row)
        if not kos:
            continue  # its KOs sit only on out-of-scope maps
        linked_genes += 1
        name, desc = gene_meta.get(gid, ("", ""))
        grow = new_xref(gid, "external_gene_id", desc or name)
        for ko in kos:
            xrefs[grow]["mates"].add(ko_row[ko])
            xrefs[ko_row[ko]]["mates"].add(grow)

    linked_transcripts = 0
    for tid, gid in sorted(transcript2gene.items()):
        kos = sorted(k for k in gene2ko.get(gid, ()) if k in ko_row)
        if not kos:
            continue
        linked_transcripts += 1
        trow = new_xref(tid, "external_transcript_id", "transcript of " + gid)
        for ko in kos:
            xrefs[trow]["mates"].add(ko_row[ko])
            xrefs[ko_row[ko]]["mates"].add(trow)

    for doc in xrefs.values():
        doc["mates"] = sorted(doc["mates"])
    return (list(dbnames.values()), list(xrefs.values()),
            linked_genes, linked_transcripts)


# --------------------------------------------------------------------------- #
#  Writers                                                                    #
# --------------------------------------------------------------------------- #

def replace_collection(db, name, docs, indexes=()):
    """Write ``docs`` to ``<name>_incoming`` with its indexes, then rename it
    over ``name``. The server keeps answering from the old collection until the
    rename, instead of from an empty one while the documents go in."""
    incoming = db[name + "_incoming"]
    incoming.drop()
    for i in range(0, len(docs), 5000):
        incoming.insert_many(docs[i:i + 5000])
    for keys in indexes:
        incoming.create_index(keys)
    incoming.rename(name, dropTarget=True)


def map_rule(scope_organism, min_kos):
    """What the KEGG version document records of how the maps were chosen, so
    --rebuild-maps can repeat the scope organism without being told it."""
    return {"scope_organism": scope_organism, "min_kos": min_kos}


def write_mongo(code, dbname_docs, xref_docs, kegg_docs, mongo_host, mongo_port, rule=None):
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(mongo_host, mongo_port)
    db = client[code + "-paintomics"]
    xref_indexes = ([("dbname_id", ASCENDING), ("_id", ASCENDING)], [("display_id", ASCENDING)])
    for collection, docs, indexes in (("dbname", dbname_docs, ()), ("xref", xref_docs, xref_indexes),
                                      ("kegg", kegg_docs, ([("source", ASCENDING)],))):
        replace_collection(db, collection, docs, indexes)
    stamp = datetime.now().strftime("%Y%m%d %H%M")
    db["versions"].drop()
    db["versions"].insert_many([dict({"name": "KEGG", "date": stamp}, **(rule or {})),
                                {"name": "MAPPING", "date": stamp}])
    # Same indexes the standard build creates (common_build_database.createDatabase):
    # the two xref lookups, and the pathway `source` index that keeps
    # /organism_databases from scanning every pathway document
    # (DatabaseAvailability.PATHWAY_SOURCE_INDEX). Built on the incoming
    # collections above already, so these find them in place.
    db.xref.create_index([("dbname_id", ASCENDING), ("_id", ASCENDING)])
    db.xref.create_index([("display_id", ASCENDING)])
    db.kegg.create_index([("source", ASCENDING)])
    client.close()


def installed_kos(code, mongo_host, mongo_port):
    """Every KO an installed custom species carries, read from its identifier
    graph: build_identifier_graph wrote a ``kegg_id`` row for each annotated
    KO, placed on a diagram or not, so this is the annotation's KO set."""
    from pymongo import MongoClient
    client = MongoClient(mongo_host, mongo_port)
    try:
        db = client[code + "-paintomics"]
        target = db.dbname.find_one({"dbname": "kegg_id"})
        if target is None:
            raise SystemExit("%s-paintomics has no kegg_id identifiers: is %s installed here?"
                             % (code, code))
        return {doc["display_id"] for doc in db.xref.find({"dbname_id": target["_id"]},
                                                           {"display_id": 1, "_id": 0})
                if KO_RE.match(str(doc.get("display_id") or ""))}
    finally:
        client.close()


def write_pathways(code, kegg_docs, mongo_host, mongo_port, rule=None):
    """--rebuild-maps: replace the pathway documents, and date the KEGG version
    with the rule that chose them. The identifier graph and the MAPPING version
    are left as they are."""
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(mongo_host, mongo_port)
    db = client[code + "-paintomics"]
    replace_collection(db, "kegg", kegg_docs, ([("source", ASCENDING)],))
    db.kegg.create_index([("source", ASCENDING)])
    db["versions"].update_one({"name": "KEGG"},
                              {"$set": dict({"date": datetime.now().strftime("%Y%m%d %H%M")}, **(rule or {}))},
                              upsert=True)
    client.close()


def recorded_scope(code, mongo_host, mongo_port):
    """The scope organism the KEGG version document records (map_rule), or
    None: installs before 2026-11-07 recorded none."""
    from pymongo import MongoClient
    client = MongoClient(mongo_host, mongo_port)
    try:
        doc = client[code + "-paintomics"]["versions"].find_one({"name": "KEGG"}) or {}
        return doc.get("scope_organism") or None
    finally:
        client.close()


def write_species_graph(kegg_data_dir, code, kegg_docs, kgml, ko_names, annotated_kos):
    """``kgml/``, ``pathways.list`` and ``mapping/kegg2genesymbol.list`` (module
    docstring). ``kgml/`` is replaced whole, so a map the rule no longer keeps
    loses its file with its document. Returns the number of KGML files."""
    specie_dir = os.path.join(kegg_data_dir, "current", code)
    kgml_dir = os.path.join(specie_dir, "kgml")
    incoming, outgoing = kgml_dir + ".incoming", kgml_dir + ".outgoing"
    for stale in (incoming, outgoing):
        shutil.rmtree(stale, ignore_errors=True)
    os.makedirs(incoming)
    for doc in kegg_docs:
        with open(os.path.join(incoming, doc["ID"] + ".kgml"), "wb") as fh:
            fh.write(kgml[doc["ID"]])
    if os.path.isdir(kgml_dir):
        os.replace(kgml_dir, outgoing)
    os.replace(incoming, kgml_dir)
    shutil.rmtree(outgoing, ignore_errors=True)

    def write_lines(path, lines):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(line + "\n" for line in lines)
        os.replace(tmp, path)

    # The walk names a pathway from this file (network._read_pathway_names).
    write_lines(os.path.join(specie_dir, "pathways.list"),
                ["path:%s\t%s" % (doc["ID"], doc["name"]) for doc in kegg_docs])
    # The row shape of a KEGG organism's file, ``mmu:12345<TAB>CDS<TAB>locus<TAB>
    # Sym1, Sym2; description``, with the KO for the gene: the walk labels a node
    # by its symbol and reads a gene named in the design by it.
    write_lines(os.path.join(specie_dir, "mapping", "kegg2genesymbol.list"),
                ["%s:%s\tCDS\t-\t%s" % (code, ko, ko_names[ko][1])
                 for ko in sorted(annotated_kos) if ko in ko_names and ";" in ko_names[ko][1]])
    return len(kegg_docs)


def check_species_dir_writable(kegg_data_dir, code):
    """Refuse before the database is touched when the species files cannot be
    written: a rebuild that replaced the pathway documents and then stopped on
    a file it may not open (a directory owned by the server's user, the tool
    run as another) left the documents and the files describing two installs."""
    specie_dir = os.path.join(kegg_data_dir, "current", code)
    os.makedirs(specie_dir, exist_ok=True)
    paths = [specie_dir] + [os.path.join(specie_dir, name) for name in
                            ("gene2pathway.list", "pathways.list", "kgml", "mapping",
                             os.path.join("mapping", "kegg2genesymbol.list"))]
    denied = [path for path in paths if os.path.exists(path) and not os.access(path, os.W_OK)]
    if denied:
        raise SystemExit("Cannot write %s as this user (uid %d); run the tool as the owner of the "
                         "data directory. Nothing was changed." % (", ".join(denied), os.getuid()))


def write_gene2pathway(kegg_data_dir, code, kegg_docs):
    """generateMetaGenes.R re-prefixes matched feature ids with '<code>:', so
    the left column must be code-prefixed KO ids (verified: generateMetaGenes.R
    :194-195, live mmu file 'mmu:103988\tpath:mmu00010')."""
    specie_dir = os.path.join(kegg_data_dir, "current", code)
    os.makedirs(specie_dir, exist_ok=True)
    path = os.path.join(specie_dir, "gene2pathway.list")
    lines = 0
    with open(path, "w") as fh:
        for doc in kegg_docs:
            for ko in sorted({g["id"] for g in doc["genes"]}):
                fh.write("%s:%s\tpath:%s\n" % (code, ko, doc["ID"]))
                lines += 1
    return path, lines


def register_in_organisms_list(kegg_data_dir, code, name, lineage):
    """Register the species name where species.json generation looks it up.

    A code missing from organisms_all.list aborts species.json generation for
    EVERY later install (DBManager.py:1675). But organisms_all.list is
    re-downloaded from KEGG by `download --common=1`, which would silently drop
    a custom row — so the durable registry is a sibling organisms_custom.list
    (same 4-column format, one row per custom species) that survives common
    refreshes, plus the row appended to organisms_all.list for everything that
    reads it today. Re-running after a common refresh restores the row.
    """
    common_dir = os.path.join(kegg_data_dir, "current", "common")
    tnumber = "T9%04d" % (sum(ord(c) for c in code) % 10000)
    newline = "%s\t%s\t%s\t%s" % (tnumber, code, name, lineage)
    for basename in ("organisms_all.list", "organisms_custom.list"):
        path = os.path.join(common_dir, basename)
        lines = []
        if os.path.isfile(path):
            with open(path) as fh:
                lines = [l.rstrip("\n") for l in fh]
        lines = [l for l in lines if l.split("\t")[1:2] != [code]]
        lines.append(newline)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    return os.path.join(common_dir, "organisms_all.list")


def regenerate_species_json(kegg_data_dir, mongo_host, mongo_port):
    """Rewrite current/species.json from installed *-paintomics DBs, the same
    source of truth DBManager uses (dropdown format verified live).

    Installed means a finished build, as DBManager.getCurrentInstalledSpecies
    counts it: a database without its KEGG and MAPPING version records
    (species_json.FINISHED_BUILD_RECORDS) is left out and named. Listing every
    database put a half-built organism back in the dropdown after DBManager had
    left it out."""
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if src not in sys.path:
        sys.path.insert(0, src)
    from AdminTools.species_json import (species_json_rows, write_species_json, build_records,
                                         missing_build_records, unfinished_build_fact)
    from pymongo import MongoClient
    client = MongoClient(mongo_host, mongo_port)
    codes = []
    for database in sorted(n for n in client.list_database_names()
                           if n.endswith("-paintomics") and n != "global-paintomics"):
        code = database[:-len("-paintomics")]
        absent = missing_build_records(build_records(client[database]))
        if absent:
            log("  WARNING: %s; species.json leaves %s out until a build of it finishes"
                % (unfinished_build_fact(database, absent), code))
            continue
        codes.append(code)
    client.close()
    names = {}
    common_dir = os.path.join(kegg_data_dir, "current", "common")
    # organisms_custom.list second: custom rows win, and they survive the
    # KEGG-refresh clobber of organisms_all.list.
    for basename in ("organisms_all.list", "organisms_custom.list"):
        listing = os.path.join(common_dir, basename)
        if not os.path.isfile(listing):
            continue
        with open(listing) as fh:
            for line in fh:
                row = line.rstrip("\n").split("\t")
                if len(row) >= 3:
                    names[row[1]] = row[2]
    missing = [c for c in codes if not names.get(c)]
    if missing:
        raise SystemExit("These installed species are missing from "
                         "organisms_all.list: %s" % ", ".join(missing))
    # The same writer and the same order as DBManager's, so the file does
    # not change shape depending on which installer ran last.
    target = write_species_json(os.path.join(kegg_data_dir, "current", "species.json"),
                                species_json_rows(codes, names))
    return target, len(codes)


def ensure_diagrams(kegg_data_dir, cache, kegg_docs, code):
    """The shared common/png snapshot may predate newer KEGG maps; fetch any
    diagram a custom pathway needs, plus a 300px thumbnail like the installed
    ones (verified 300x300 boxes)."""
    png_dir = os.path.join(kegg_data_dir, "current", "common", "png")
    thumb_dir = os.path.join(png_dir, "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    fetched = []
    for doc in kegg_docs:
        number = doc["ID"].replace(code, "", 1)
        png = os.path.join(png_dir, "map%s.png" % number)
        thumb = os.path.join(thumb_dir, "map%s_thumb.png" % number)
        if not os.path.isfile(png):
            payload = cache.fetch("/get/map%s/image" % number,
                                  "map%s.png" % number, binary=True, optional=True)
            if payload is None:
                log("  WARNING: no diagram image for map%s" % number)
                continue
            with open(png, "wb") as fh:
                fh.write(payload)
            fetched.append("map" + number)
        if not os.path.isfile(thumb):
            try:
                from PIL import Image
                img = Image.open(png)
                img.thumbnail((300, 300))
                img.save(thumb)
            except Exception as ex:
                log("  WARNING: thumbnail for map%s failed: %s" % (number, ex))
    return fetched


# --------------------------------------------------------------------------- #
#  Verification                                                               #
# --------------------------------------------------------------------------- #

def verify(code, kegg_data_dir, mongo_host, mongo_port, sample_genes):
    """Prove the install satisfies the runtime by running the exact aggregation
    Step 1 runs (FeatureNamesToKeggIDsMapper.py:114-143) plus structural checks.
    Returns a list of problems (empty == pass)."""
    from pymongo import MongoClient
    problems = []
    client = MongoClient(mongo_host, mongo_port)
    db = client[code + "-paintomics"]

    target = db.dbname.find_one({"dbname": "kegg_id"})
    symbol_db = db.dbname.find_one({"dbname": "kegg_gene_symbol"})
    if target is None or symbol_db is None:
        problems.append("dbname docs kegg_id/kegg_gene_symbol missing "
                        "(mapper workers would crash)")
    for doc in db.kegg.find({}, {"ID": 1, "source": 1, "genes": 1}):
        if "ID" not in doc:
            problems.append("kegg doc without ID (organism load would KeyError)")
            break
        if doc.get("source") != "KEGG":
            problems.append("%s: source != KEGG" % doc.get("ID"))
            break
    for name in ("KEGG", "MAPPING"):
        if db.versions.find_one({"name": name}) is None:
            problems.append("versions doc %s missing (admin page 500s)" % name)

    if target is not None:
        resolved = 0
        for gid in sample_genes:
            hits = list(db.xref.aggregate([
                {"$match": {"display_id": {"$in": [gid]}}},
                {"$unwind": "$mates"},
                {"$lookup": {"from": "xref", "localField": "mates",
                             "foreignField": "_id", "as": "unwind_mate"}},
                {"$unwind": "$unwind_mate"},
                {"$match": {"unwind_mate.dbname_id": target["_id"]}},
                {"$project": {"display_id": "$unwind_mate.display_id"}},
            ]))
            if hits:
                resolved += 1
        if sample_genes and resolved == 0:
            problems.append("no sample gene resolved to a KO through the "
                            "Step 1 aggregation")
        else:
            log("  verify: %d/%d sample genes resolve to KOs"
                % (resolved, len(sample_genes)))

    g2p = os.path.join(kegg_data_dir, "current", code, "gene2pathway.list")
    if not os.path.isfile(g2p) or os.path.getsize(g2p) == 0:
        problems.append("gene2pathway.list missing/empty (metagenes would fail "
                        "step 2)")
    kgml_dir = os.path.join(kegg_data_dir, "current", code, "kgml")
    pathway_ids = [doc.get("ID") for doc in db.kegg.find({}, {"ID": 1})]
    no_kgml = [pid for pid in pathway_ids
               if not os.path.isfile(os.path.join(kgml_dir, "%s.kgml" % pid))]
    if no_kgml:
        problems.append("%d of %d pathways have no kgml/<ID>.kgml (%s...), so the AI walk "
                        "has no graph for them; run --rebuild-maps"
                        % (len(no_kgml), len(pathway_ids), ", ".join(map(str, no_kgml[:3]))))
    species_json = os.path.join(kegg_data_dir, "current", "species.json")
    try:
        with open(species_json) as fh:
            entries = json.load(fh)["species"]
        if not any(e.get("value") == code for e in entries):
            problems.append("species.json does not list %s" % code)
    except Exception as ex:
        problems.append("species.json unreadable: %s" % ex)
    client.close()
    return problems


# --------------------------------------------------------------------------- #
#  Main                                                                       #
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--code", required=True,
                        help="organism code (letters only, must not collide "
                             "with a KEGG code — check rest.kegg.jp/list/genome)")
    parser.add_argument("--name", help="display name (required for an install)")
    parser.add_argument("--lineage", default=None,
                        help="semicolon lineage shown in admin tools and read to "
                             "tell a plant, fungus or protist (default "
                             "Eukaryotes;Custom; --rebuild-maps reads the "
                             "installed row)")
    parser.add_argument("--annotation",
                        help="gene→KO table (eggNOG-mapper output or TSV)")
    parser.add_argument("--scope-organism", default=None,
                        help="KEGG code of a relative; restricts pathways to "
                             "the maps KEGG assigns that organism (recommended). "
                             "--rebuild-maps reads the one recorded at install "
                             "when this is not given")
    parser.add_argument("--min-kos", type=int, default=MIN_KOS,
                        help="fewest of the species' KOs a map must draw to be "
                             "kept (default %d)" % MIN_KOS)
    parser.add_argument("--rebuild-maps", action="store_true",
                        help="for a species this tool installed: apply the map "
                             "rule and write its KGML, reading its KOs from the "
                             "installed database; identifiers are not touched")
    parser.add_argument("--kegg-data-dir", default=None)
    parser.add_argument("--mongo-host", default=None)
    parser.add_argument("--mongo-port", default=None, type=int)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if not re.match(r"^[a-z]{3,6}$", args.code):
        raise SystemExit("--code must be 3-6 lowercase letters (a digit-free "
                         "code keeps pathwayID.replace(organism,'map') safe)")
    if args.min_kos < 1:
        raise SystemExit("--min-kos must be 1 or more")

    sc = serverconf()
    kegg_data_dir = args.kegg_data_dir or (sc and sc.KEGG_DATA_DIR)
    mongo_host = args.mongo_host or (sc and getattr(sc, "MONGODB_HOST", "localhost")) or "localhost"
    mongo_port = args.mongo_port or (sc and getattr(sc, "MONGODB_PORT", 27017)) or 27017
    if not kegg_data_dir:
        raise SystemExit("--kegg-data-dir required (serverconf not importable)")
    cache_dir = args.cache_dir or os.path.join(kegg_data_dir, "download",
                                               "custom_species_cache")
    cache = KeggCache(cache_dir)

    if args.verify_only:
        problems = verify(args.code, kegg_data_dir, mongo_host, mongo_port, [])
        for p in problems:
            log("PROBLEM: " + p)
        raise SystemExit(1 if problems else 0)
    if args.rebuild_maps:
        return rebuild_maps(args, kegg_data_dir, mongo_host, mongo_port, cache, cache_dir)

    if not args.annotation or not args.name:
        raise SystemExit("--annotation and --name are required for an install")
    lineage = args.lineage or "Eukaryotes;Custom"

    log("STEP 1. Parsing annotation %s" % args.annotation)
    gene2ko, transcript2gene, gene_meta = parse_annotation(args.annotation)
    if not gene2ko:
        raise SystemExit("No gene→KO assignments found — nothing to install.")
    annotated_kos = set().union(*gene2ko.values())
    log("  %d genes with KO, %d transcript ids, %d distinct KOs"
        % (len(gene2ko), len(transcript2gene), len(annotated_kos)))

    log("STEP 2. Loading KEGG reference tables (cache: %s)" % cache_dir)
    taxon = species_taxon(lineage, kegg_data_dir, args.scope_organism)
    kegg_docs, kgml, ko_names = plan_maps(args.code, cache, annotated_kos, args.scope_organism,
                                          taxon, args.min_kos)
    kos_in_pathways = {g["id"] for doc in kegg_docs for g in doc["genes"]}

    log("STEP 4. Building identifier graph")
    dbname_docs, xref_docs, n_genes, n_transcripts = build_identifier_graph(
        gene2ko, transcript2gene, gene_meta, ko_names, annotated_kos)
    log("  %d xref rows (%d genes, %d transcripts linked)"
        % (len(xref_docs), n_genes, n_transcripts))

    if not kegg_docs or not n_genes:
        raise SystemExit("Refusing to install an empty species.")
    if args.dry_run:
        log("DRY RUN — nothing written.")
        return

    check_species_dir_writable(kegg_data_dir, args.code)
    log("STEP 5. Writing MongoDB %s-paintomics" % args.code)
    write_mongo(args.code, dbname_docs, xref_docs, kegg_docs,
                mongo_host, mongo_port, map_rule(args.scope_organism, args.min_kos))

    log("STEP 6. Writing species files")
    g2p_path, g2p_lines = write_gene2pathway(kegg_data_dir, args.code, kegg_docs)
    log("  %s (%d lines)" % (g2p_path, g2p_lines))
    n_kgml = write_species_graph(kegg_data_dir, args.code, kegg_docs, kgml, ko_names, annotated_kos)
    log("  %d KGML maps, pathways.list and mapping/kegg2genesymbol.list" % n_kgml)
    register_in_organisms_list(kegg_data_dir, args.code, args.name, lineage)
    fetched = ensure_diagrams(kegg_data_dir, cache, kegg_docs, args.code)
    if fetched:
        log("  fetched %d missing diagrams: %s" % (len(fetched), ", ".join(fetched)))
    target, n_species = regenerate_species_json(kegg_data_dir, mongo_host,
                                                mongo_port)
    log("  %s now lists %d species" % (target, n_species))

    log("STEP 7. Verifying against the runtime contract")
    sample = [g for g in list(gene2ko)[:200]
              if any(k in kos_in_pathways for k in gene2ko[g])][:5]
    problems = verify(args.code, kegg_data_dir, mongo_host, mongo_port, sample)
    if problems:
        for p in problems:
            log("PROBLEM: " + p)
        raise SystemExit(1)
    log("SUCCESS: %s installed. New jobs can use it immediately; the organism "
        "dropdown reads species.json per page load." % args.code)


def species_taxon(lineage, kegg_data_dir, scope_organism):
    """taxon_of the species' own lineage, else of the scope organism's KEGG
    lineage (organisms_all.list), logged with where it came from."""
    taxon = taxon_of(lineage)
    source = "lineage %s" % lineage
    if taxon is None and scope_organism:
        scope_lineage = lineage_of(kegg_data_dir, scope_organism)
        taxon = taxon_of(scope_lineage)
        source = "%s's KEGG lineage %s" % (scope_organism, scope_lineage)
    log("  taxon: %s (from %s)" % (taxon or "not recognised", source))
    return taxon


def rebuild_maps(args, kegg_data_dir, mongo_host, mongo_port, cache, cache_dir):
    """--rebuild-maps: the map rule and the KGML for a species installed
    earlier, from the KOs its database holds. Writes the pathway documents, the
    KEGG version date and the species files; the identifier graph, the MAPPING
    version, organisms_*.list and species.json are left as they are."""
    custom_codes = set()
    custom_list = os.path.join(kegg_data_dir, "current", "common", "organisms_custom.list")
    if os.path.isfile(custom_list):
        with open(custom_list, encoding="utf-8", errors="replace") as fh:
            custom_codes = {line.split("\t")[1] for line in fh if line.count("\t") >= 1}
    if args.code not in custom_codes:
        # A KEGG-built species keeps its own KGML and gene ids; rebuilding it
        # from reference KO maps would replace both.
        raise SystemExit("%s is not in organisms_custom.list, so this tool did not install it; "
                         "--rebuild-maps only rebuilds species it installed." % args.code)
    registered = lineage_of(kegg_data_dir, args.code)  # its organisms_custom.list row

    log("STEP 1. Reading the KOs %s-paintomics holds" % args.code)
    annotated_kos = installed_kos(args.code, mongo_host, mongo_port)
    if not annotated_kos:
        raise SystemExit("%s-paintomics holds no KO identifiers — nothing to rebuild." % args.code)
    log("  %d distinct KOs" % len(annotated_kos))

    scope = args.scope_organism or recorded_scope(args.code, mongo_host, mongo_port)
    if not scope:
        # Without one every map outside the excluded classes is on offer, and
        # the species would gain maps its install never had.
        raise SystemExit("Pass --scope-organism, the KEGG code %s was installed with: its "
                         "database does not record one." % args.code)

    log("STEP 2. Loading KEGG reference tables (cache: %s)" % cache_dir)
    taxon = species_taxon(args.lineage or registered, kegg_data_dir, scope)
    kegg_docs, kgml, ko_names = plan_maps(args.code, cache, annotated_kos, scope,
                                          taxon, args.min_kos)
    if not kegg_docs:
        raise SystemExit("Refusing to leave %s with no pathway." % args.code)
    if args.dry_run:
        log("DRY RUN — nothing written.")
        return

    check_species_dir_writable(kegg_data_dir, args.code)
    log("STEP 4. Writing the pathway documents of %s-paintomics" % args.code)
    write_pathways(args.code, kegg_docs, mongo_host, mongo_port, map_rule(scope, args.min_kos))

    log("STEP 5. Writing species files")
    g2p_path, g2p_lines = write_gene2pathway(kegg_data_dir, args.code, kegg_docs)
    log("  %s (%d lines)" % (g2p_path, g2p_lines))
    n_kgml = write_species_graph(kegg_data_dir, args.code, kegg_docs, kgml, ko_names, annotated_kos)
    log("  %d KGML maps, pathways.list and mapping/kegg2genesymbol.list" % n_kgml)
    fetched = ensure_diagrams(kegg_data_dir, cache, kegg_docs, args.code)
    if fetched:
        log("  fetched %d missing diagrams: %s" % (len(fetched), ", ".join(fetched)))

    log("STEP 6. Verifying against the runtime contract")
    sample = sorted({g["id"] for doc in kegg_docs for g in doc["genes"]})[:5]
    problems = verify(args.code, kegg_data_dir, mongo_host, mongo_port, sample)
    if problems:
        for p in problems:
            log("PROBLEM: " + p)
        raise SystemExit(1)
    log("SUCCESS: %s rebuilt: %d pathways, each with its KGML." % (args.code, len(kegg_docs)))


if __name__ == "__main__":
    main()
