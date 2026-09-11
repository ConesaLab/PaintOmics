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

Deliberately NOT produced (all degrade cleanly): ``hubData/`` (guards at
PathwayAcquisitionJob.py:2296/2528), ``pathways_network.json`` (empty tab),
Reactome/MapMan collections (source checkboxes simply absent).

Usage:
  python customSpeciesInstaller.py \
      --code=nben --name="Nicotiana benthamiana" \
      --lineage="Eukaryotes;Plants;Eudicots;Nightshade family" \
      --annotation=/path/to/annotation.tsv \
      --scope-organism=nta [--dry-run] [--verify-only]

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


def select_maps(gene2ko, ko2maps, classes, scope_maps):
    annotated_kos = set().union(*gene2ko.values()) if gene2ko else set()
    hit = defaultdict(set)
    for ko in annotated_kos:
        for m in ko2maps.get(ko, ()):
            hit[m].add(ko)
    selected = {}
    for m, kos in hit.items():
        if scope_maps is not None:
            if m not in scope_maps:
                continue
        else:
            cls = classes.get(m, "")
            if cls.split(";")[0] in DEFAULT_EXCLUDED_CLASSES:
                continue
        selected[m] = kos
    return annotated_kos, selected


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


# --------------------------------------------------------------------------- #
#  Assembly                                                                   #
# --------------------------------------------------------------------------- #

def build_pathway_docs(code, cache, selected, map_names, classes, annotated_kos):
    docs = []
    skipped = []
    for m in sorted(selected):
        number = m.replace("map", "")
        raw = cache.fetch("/get/ko%s/kgml" % number, "ko%s.xml" % number,
                          binary=True, optional=True)
        if raw is None:
            skipped.append(m + " (no reference KGML)")
            continue
        genes, compounds, related = parse_ko_kgml(raw, annotated_kos)
        if not genes:
            # The link table said the map is hit but the diagram carries none of
            # our KOs (global overview maps can differ); nothing would paint.
            skipped.append(m + " (no annotated KO on the diagram)")
            continue
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
    return docs, skipped


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

def write_mongo(code, dbname_docs, xref_docs, kegg_docs, mongo_host, mongo_port):
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(mongo_host, mongo_port)
    db = client[code + "-paintomics"]
    for collection, docs in (("dbname", dbname_docs), ("xref", xref_docs),
                             ("kegg", kegg_docs)):
        db[collection].drop()
        for i in range(0, len(docs), 5000):
            db[collection].insert_many(docs[i:i + 5000])
    stamp = datetime.now().strftime("%Y%m%d %H%M")
    db["versions"].drop()
    db["versions"].insert_many([{"name": "KEGG", "date": stamp},
                                {"name": "MAPPING", "date": stamp}])
    # Same indexes the standard build creates (common_build_database.createDatabase):
    # the two xref lookups, and the pathway `source` index that keeps
    # /organism_databases from scanning every pathway document
    # (DatabaseAvailability.PATHWAY_SOURCE_INDEX).
    db.xref.create_index([("dbname_id", ASCENDING), ("_id", ASCENDING)])
    db.xref.create_index([("display_id", ASCENDING)])
    db.kegg.create_index([("source", ASCENDING)])
    client.close()


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
    source of truth DBManager uses (dropdown format verified live)."""
    from pymongo import MongoClient
    client = MongoClient(mongo_host, mongo_port)
    codes = sorted(n[:-len("-paintomics")] for n in client.list_database_names()
                   if n.endswith("-paintomics") and n != "global-paintomics")
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
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if src not in sys.path:
        sys.path.insert(0, src)
    from AdminTools.species_json import species_json_rows, write_species_json
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
    parser.add_argument("--name", required=True)
    parser.add_argument("--lineage", default="Eukaryotes;Custom",
                        help="semicolon lineage shown in admin tools")
    parser.add_argument("--annotation",
                        help="gene→KO table (eggNOG-mapper output or TSV)")
    parser.add_argument("--scope-organism", default=None,
                        help="KEGG code of a relative; restricts pathways to "
                             "the maps KEGG assigns that organism (recommended)")
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

    if not args.annotation:
        raise SystemExit("--annotation is required for an install")

    log("STEP 1. Parsing annotation %s" % args.annotation)
    gene2ko, transcript2gene, gene_meta = parse_annotation(args.annotation)
    if not gene2ko:
        raise SystemExit("No gene→KO assignments found — nothing to install.")
    log("  %d genes with KO, %d transcript ids, %d distinct KOs"
        % (len(gene2ko), len(transcript2gene),
           len(set().union(*gene2ko.values()))))

    log("STEP 2. Loading KEGG reference tables (cache: %s)" % cache_dir)
    map_names, ko2maps, ko_names, classes, scope_maps = load_reference(
        cache, args.scope_organism)
    annotated_kos, selected = select_maps(gene2ko, ko2maps, classes, scope_maps)
    log("  %d annotated KOs hit %d pathway maps%s"
        % (len(annotated_kos), len(selected),
           " (scoped to %s)" % args.scope_organism if scope_maps else
           " (class-filtered)"))
    if not selected:
        raise SystemExit("No pathway maps selected — check the annotation and "
                         "the scope organism.")

    log("STEP 3. Building pathway documents from reference KGML")
    kegg_docs, skipped = build_pathway_docs(args.code, cache, selected,
                                            map_names, classes, annotated_kos)
    for s in skipped:
        log("  skipped %s" % s)
    kos_in_pathways = {g["id"] for doc in kegg_docs for g in doc["genes"]}
    log("  %d pathways, %d distinct KOs placed on diagrams"
        % (len(kegg_docs), len(kos_in_pathways)))

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

    log("STEP 5. Writing MongoDB %s-paintomics" % args.code)
    write_mongo(args.code, dbname_docs, xref_docs, kegg_docs,
                mongo_host, mongo_port)

    log("STEP 6. Writing species files")
    g2p_path, g2p_lines = write_gene2pathway(kegg_data_dir, args.code, kegg_docs)
    log("  %s (%d lines)" % (g2p_path, g2p_lines))
    register_in_organisms_list(kegg_data_dir, args.code, args.name, args.lineage)
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


if __name__ == "__main__":
    main()
