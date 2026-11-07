#!/usr/bin/env python3
"""Regenerate the identifier alias tables beside this file: zma, psat and sbi.

README.md in this directory says what the tables are for, where each row comes
from and under which licence. Species builds read the tables and never run this.

Two steps, both of which download what they need into --cache and reuse it:

    python3 build_identifier_aliases.py rbh --cache DIR --threads 8
    python3 build_identifier_aliases.py tables --kegg-data DATA --cache DIR --out DIR

`rbh` compares pea's Cameor v2 proteins with the RefSeq ZW6 proteins. It needs
DIAMOND (https://github.com/bbuchfink/diamond) and writes the reciprocal hits to
DIR/psat_v2_refseq_rbh.tsv, which `tables` reads: about three minutes on 8 threads.

`tables` reads DATA/<code>/mapping/*, the files the species' own build reads
(/data/KEGG_DATA/current in the deployed container), and runs the build's own
processors on them in memory. That, rather than an export of the installed
database, is what it asks "does this id reach KEGG?" of: a database that already
carries the aliases would answer yes for every sorghum gene this table exists to
link, and regenerating from it would come out empty. It also reads
DATA/<code>/gene2pathway.list, for the pathway counts it reports, so copy that
beside mapping/ when running it on another machine.

It stops, writing no table for that species, on a source it cannot fetch or read, a
source the processors skipped, a missing KEGG list, a maize or pea position source under
its floor (MIN_GFF_PLACED, MIN_LIST_PLACED), or no rows at all. It does not stop on every
short table: a source of rows that parses short, such as the locus-tag column or a
MaizeGDB table missing lines, ends the run well with a smaller table. Only the MD5 and
SHIPPED_ROWS pins in test_identifier_aliases catch that, once the table is committed.

Expect small differences on a later date. NCBI rewrites gene_info daily and KEGG
and Ensembl move with each release, which is the point of being able to re-run it.
"""
import argparse
import bisect
import collections
import gzip
import importlib.util
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ElementTree
import zipfile
from subprocess import check_call

HERE = os.path.dirname(os.path.realpath(__file__))
BUILDER = os.path.join(HERE, "..", "..", "common_build_database.py")

COLUMNS = ("alias", "alias_dbname", "target_dbname", "target_display_id", "evidence")
GENE_ALIAS = "gene_alias"

GENE_INFO_URL = "https://ftp.ncbi.nih.gov/gene/DATA/GENE_INFO/Plants/All_Plants.gene_info.gz"
MAIZEGDB_XREF_URL = "https://download.maizegdb.org/Pan-genes/B73_gene_xref/"
MAIZE_V4_GFF_URL = ("https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/"
                    "Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.gff3.gz")
MAIZE_V5_GFF_URL = ("https://download.maizegdb.org/Zm-B73-REFERENCE-NAM-5.0/"
                    "Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz")
PEA_DATASET_URL = ("https://entrepot.recherche.data.gouv.fr/api/datasets/:persistentId/"
                   "?persistentId=doi:10.57745/5MDE99")
PEA_DATAFILE_URL = "https://entrepot.recherche.data.gouv.fr/api/access/datafile/%d"
REFSEQ_ZW6_URL = ("https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/024/323/335/GCF_024323335.1_CAAS_Psat_ZW6_1.0/"
                  "GCF_024323335.1_CAAS_Psat_ZW6_1.0_")
ZW6_FEATURES = "GCF_024323335.1_feature_table.txt.gz"
#: The Cameor v2 gene models, in the same dataset as the proteins and the published table.
PEA_GFF = "CAMEOR_V2_ANNOTATION_1.gff3"

MAIZE_TAXON = "4577"
SORGHUM_TAXON = "4558"
MAIZE_LOCUS_TAG_PREFIX = "ZEAMMB73_"

#: A sequence match has to clear these both ways before it can name a gene: ZW6
#: is another cultivar, so an exact match is not expected, but a weaker one is a
#: family member as often as it is the gene.
RBH_MIN_IDENTITY = 95.0
RBH_MIN_COVERAGE = 80.0
#: Reciprocal overlap on the Cameor v2 genome, of the longer of the two genes,
#: that a published best-hit pair must also have.
MIN_GENOME_OVERLAP = 0.5

#: The position test both species use (Collinearity): an id asks the NEIGHBOURS pairs
#: nearest it on its own genome, among pairs already trusted, where its partner should be
#: on the other genome, and a pair agrees when it sits no further from the partner there
#: than it sits from the id here, plus SLACK bases. A maize v4 id also passes when its
#: GeneID is within SLACK of a v5 gene MaizeGDB pairs it with. Measured on the 2026-11
#: sources (README.md, "Where the gene is"): 99.5 % of maize's and 99.6 % of pea's trusted
#: pairs pass it with themselves left out, 0.2 % and 0.1 % when given a random gene of the
#: other genome as partner. Maize loses the same 4 rows with SLACK at 0.5, 2 or 5 Mb, or 3
#: or 6 neighbours, and pea's 221 move by at most 30. 10 neighbours let random partners
#: through as rarely, but also fail genes beside a gap between the assemblies.
NEIGHBOURS = 4
SLACK = 1000000

DIAMOND_FIELDS = ("qseqid", "sseqid", "pident", "length", "qlen", "slen", "qcovhsp", "scovhsp", "evalue", "bitscore")
#: The lists the processors only warn about, on stderr, when one is absent. Without
#: any of them a species still builds, with far fewer rows to choose from.
REQUIRED_MAPPING_FILES = ("uniprot2kegg.list", "ncbi-geneid2kegg.list", "kegg2genesymbol.list")
#: The share of its ids a maize position source has to put on a numbered chromosome.
#: On the 2026-11 files: the v4 GFF 99.2 % and the v5 GFF 100 % of the ids in MaizeGDB's
#: tables, KEGG's list and gene_info 94.4 % of KEGG's genes. They have margin below
#: that for a later release, and stop a source that parses to little or nothing, which
#: would turn its check off or lose the MaizeGDB rows and still end the run well.
MIN_GFF_PLACED = 0.95
MIN_LIST_PLACED = 0.85

RBH_FILE = "psat_v2_refseq_rbh.tsv"
RBH_HEADER = ("v2_gene", "geneid", "pident", "aln_len", "qlen", "slen", "qcov", "scov", "evalue", "bitscore",
              "top_ties_q", "top_ties_s")

_SPREADSHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RELATIONSHIP_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_PACKAGE_RELATIONSHIP = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


# --- Downloads --------------------------------------------------------------

def fetch(url, target):
    """Download `url` to `target` unless it is already there and non-empty.

    Through a .part file, so an interrupted transfer never sits at `target` to be
    taken for a whole one by the next run.
    """
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        sys.stderr.write("using cached " + target + "\n")
        return target
    sys.stderr.write("downloading " + url + "\n")
    partial = target + ".part"
    # -f so an error page is never stored as data; -L for redirects.
    check_call(["curl", "-f", "-L", "--connect-timeout", "120", "--max-time", "1800", url, "-o", partial])
    os.replace(partial, target)
    return target


def fetchPeaDatasetFile(name, cache):
    """A file of the Cameor v2 dataset, found by name: a new version renumbers the files."""
    target = os.path.join(cache, name)
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        sys.stderr.write("using cached " + target + "\n")
        return target
    with urllib.request.urlopen(PEA_DATASET_URL, timeout=120) as response:
        files = json.load(response)["data"]["latestVersion"]["files"]
    fileIds = [entry["dataFile"]["id"] for entry in files if entry["dataFile"]["filename"] == name]
    if len(fileIds) != 1:
        raise RuntimeError("the pea dataset has %d files called %s" % (len(fileIds), name))
    return fetch(PEA_DATAFILE_URL % fileIds[0], target)


def geneInfoForTaxon(cache, taxon):
    """One taxon's rows of NCBI's All_Plants gene_info, cut out of the 215 MB file on first use."""
    target = os.path.join(cache, "tax%s.gene_info.gz" % taxon)
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        sys.stderr.write("using cached " + target + "\n")
        return target
    allPlants = fetch(GENE_INFO_URL, os.path.join(cache, "All_Plants.gene_info.gz"))
    with gzip.open(allPlants, "rt", errors="replace") as source, gzip.open(target + ".tmp", "wt") as out:
        for line in source:
            if line.startswith(taxon + "\t"):
                out.write(line)
    os.replace(target + ".tmp", target)
    return target


# --- Readers ----------------------------------------------------------------

def readLocusTags(lines, prefix=""):
    """{locus tag without prefix: set of GeneIDs} from gene_info rows.

    gene_info holds one locus tag per GeneID, so a tag naming two GeneIDs is the
    only way one tag can be ambiguous, and it is reported rather than resolved.
    """
    tags = collections.defaultdict(set)
    for line in lines:
        fields = line.split("\t", 4)
        if len(fields) >= 4 and fields[3] != "-" and fields[3].startswith(prefix):
            tags[fields[3][len(prefix):]].add(fields[1])
    return tags


def readGeneChromosomes(lines):
    """{GeneID: chromosome} from gene_info rows. The value may be "-" or "Un"."""
    chromosomes = {}
    for line in lines:
        fields = line.split("\t", 7)
        if len(fields) >= 7:
            chromosomes[fields[1]] = fields[6]
    return chromosomes


def readGffGenes(lines):
    """{gene id: (chromosome, start, end)} for the gene rows of a MaizeGDB GFF3, without "chr" on the chromosome."""
    genes = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) == 9 and fields[2] == "gene":
            match = re.search(r"(?:^|;)ID=(?:gene:)?([^;]+)", fields[8])
            if match:
                genes[match.group(1)] = (re.sub(r"^[Cc]hr", "", fields[0]), int(fields[3]), int(fields[4]))
    return genes


def readFeatureGenes(lines):
    """{GeneID: (sequence, start, end)} for the gene rows of an NCBI assembly's feature table.

    The sequence is the chromosome where the row names one, and the scaffold's accession
    (NW_...) where it does not.
    """
    genes = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) > 15 and fields[0] == "gene" and fields[15]:
            genes[fields[15]] = (fields[5] or fields[6], int(fields[7]), int(fields[8]))
    return genes


def readKeggPlaces(lines):
    """{gene: (chromosome, start, end)} from KEGG's gene list, the species' mapping/kegg2genesymbol.list.

    Rows read `zma:103644366<TAB>CDS<TAB>1:complement(41254..46100)<TAB>...`, also
    with join(...) and <, >. A gene with no coordinates ("Unknown", a bare chromosome) has no entry.
    """
    places = {}
    for line in lines:
        fields = line.split("\t")
        if len(fields) >= 3 and ":" in fields[2]:
            chromosome, span = fields[2].split(":", 1)
            numbers = [int(number) for number in re.findall(r"\d+", span)]
            if numbers:
                places[fields[0].split(":", 1)[-1]] = (chromosome, min(numbers), max(numbers))
    return places


def readGeneLists(lines):
    """{gene: [genes]} from a MaizeGDB cross-reference: gene, then a comma-separated list."""
    table = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) >= 2:
            table[fields[0]] = [gene for gene in fields[1].split(",") if gene]
    return table


def readWorkbook(path, wanted):
    """{sheet name: rows without the header} for the wanted sheets of an xlsx. Standard library only."""
    def columnOf(reference):
        column = 0
        for letter in re.match(r"[A-Z]+", reference).group(0):
            column = column * 26 + ord(letter) - 64
        return column - 1

    with zipfile.ZipFile(path) as archive:
        shared = []
        with archive.open("xl/sharedStrings.xml") as handle:
            for _, element in ElementTree.iterparse(handle):
                if element.tag == _SPREADSHEET + "si":
                    shared.append("".join(part.text or "" for part in element.iter(_SPREADSHEET + "t")))
                    element.clear()
        with archive.open("xl/_rels/workbook.xml.rels") as handle:
            partOf = {relation.get("Id"): relation.get("Target")
                      for relation in ElementTree.parse(handle).getroot().iter(_PACKAGE_RELATIONSHIP)}
        with archive.open("xl/workbook.xml") as handle:
            sheets = {sheet.get("name"): partOf[sheet.get(_RELATIONSHIP_ID)]
                      for sheet in ElementTree.parse(handle).getroot().iter(_SPREADSHEET + "sheet")}

        workbook = {}
        for name in wanted:
            rows = []
            with archive.open("xl/" + sheets[name].lstrip("/").replace("xl/", "")) as handle:
                for _, element in ElementTree.iterparse(handle):
                    if element.tag != _SPREADSHEET + "row":
                        continue
                    cells = {}
                    for cell in element.findall(_SPREADSHEET + "c"):
                        value = cell.find(_SPREADSHEET + "v")
                        if value is not None:
                            text = value.text or ""
                            cells[columnOf(cell.get("r"))] = shared[int(text)] if cell.get("t") == "s" else text
                    if cells:
                        rows.append([cells.get(column, "") for column in range(max(cells) + 1)])
                    element.clear()
            workbook[name] = rows[1:]
    return workbook


def readRefseqReciprocal(lines):
    """{v2 gene: GeneID} for the DIAMOND reciprocal hits that clear both thresholds."""
    kept = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if fields[0] == RBH_HEADER[0]:
            continue
        if float(fields[2]) >= RBH_MIN_IDENTITY and float(fields[6]) >= RBH_MIN_COVERAGE \
                and float(fields[7]) >= RBH_MIN_COVERAGE:
            kept[fields[0]] = fields[1]
    return kept


# --- A species as its build sees it -----------------------------------------

class Species(object):
    """The xref tables a species' build makes before processIdentifierAliases runs."""

    def __init__(self, code, keggData):
        mapping = os.path.join(keggData, code, "mapping")
        absent = [name for name in REQUIRED_MAPPING_FILES if not os.path.isfile(os.path.join(mapping, name))]
        if absent:
            raise SystemExit("%s: %s absent from %s: not choosing rows from part of a species"
                             % (code, ", ".join(absent), mapping))
        spec = importlib.util.spec_from_file_location("common_build_database_" + code, BUILDER)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.builder.SPECIE = code
        self.builder.DATA_DIR = os.path.join(keggData, code) + "/"
        self.builder.EXTERNAL_RESOURCES = self.builder.ensemblResourcesFor(code)
        if self.builder.EXTERNAL_RESOURCES.get("ensembl"):
            self.builder.processEnsemblData()
        self.builder.processKEGGMappingData()
        self.builder.processEnsemblUniProtData()
        if self.builder.SKIPPED_SOURCES:
            raise SystemExit("%s: the build skipped %s: not choosing rows from part of a species"
                             % (code, ", ".join(label for label, _, _ in self.builder.SKIPPED_SOURCES)))

    def keggPlaces(self):
        """Where KEGG puts each of this species' genes: see readKeggPlaces."""
        with open(self.builder.DATA_DIR + "mapping/kegg2genesymbol.list", encoding="utf-8") as handle:
            return readKeggPlaces(handle)

    def displayIds(self, table):
        """Every display id in a table."""
        tableId = self.builder.ALL_DBS[table].getID()
        return {entry.display_id for entry in self.builder.xref["global"].values() if entry.dbname_id == tableId}

    def has(self, table, value):
        """Whether the table has this id, in a group a name could join."""
        entry = self.builder.findXREF(value, self.builder.ALL_DBS[table].getID())
        return entry is not None and bool(self.builder.xref2transcript["global"].get(entry.getID()))

    def reach(self, table, value):
        """The KEGG genes this id resolves to, or an empty set when the table lacks it."""
        entry = self.builder.findXREF(value, self.builder.ALL_DBS[table].getID())
        return self.builder.keggGenesReachedBy(entry.getID()) if entry is not None else set()


# --- Which rows, per species ------------------------------------------------

#: Where the maize rules read positions from. v4 and v5: {gene: (chromosome, start,
#: end)} from MaizeGDB's GFF files; kegg: from readKeggPlaces; chromosomes: {GeneID:
#: chromosome} from gene_info.
MaizePlaces = collections.namedtuple("MaizePlaces", "v4 v5 kegg chromosomes")


def overlaps(one, other):
    """Whether two (chromosome, start, end) places share a base."""
    return one[0] == other[0] and one[1] <= other[2] and other[1] <= one[2]


def distance(one, other):
    """Bases between two (sequence, start, end) places: 0 when they overlap, infinite on different sequences."""
    if one[0] != other[0]:
        return float("inf")
    return max(0, max(one[1], other[1]) - min(one[2], other[2]))


def middle(place):
    return (place[1] + place[2]) / 2.0


class Collinearity(object):
    """Where pairs of genes already trusted put an id's partner, on the partner's genome.

    Built from anchors, [(place on the id's genome, place on the partner's genome)], a place
    being (sequence, start, end). supports() asks the NEIGHBOURS anchors nearest the id on
    its own genome, and at least half of those asked, and at least one, have to sit on the
    partner's sequence no further from the partner than they sit from the id, plus SLACK.
    It compares distances, not order or strand, so a stretch the two assemblies orient
    differently still agrees. An id with no anchor on its sequence has no support.
    """

    def __init__(self, anchors):
        bySequence = collections.defaultdict(list)
        for own, other in anchors:
            bySequence[own[0]].append((middle(own), other))
        self.anchors = {sequence: sorted(entries) for sequence, entries in bySequence.items()}
        self.middles = {sequence: [entry[0] for entry in entries] for sequence, entries in self.anchors.items()}

    def nearest(self, place):
        """The NEIGHBOURS anchors nearest a place on its sequence, as (middle, other place); on a tie the lower one."""
        entries = self.anchors.get(place[0], [])
        middles = self.middles.get(place[0], [])
        point = middle(place)
        right = bisect.bisect_left(middles, point)
        left = right - 1
        chosen = []
        while len(chosen) < NEIGHBOURS and (left >= 0 or right < len(entries)):
            if right >= len(entries) or (left >= 0 and point - middles[left] <= middles[right] - point):
                chosen.append(entries[left])
                left -= 1
            else:
                chosen.append(entries[right])
                right += 1
        return chosen

    def agreeing(self, place, partner):
        """(anchors that put the partner where it is, anchors asked)."""
        point, target = middle(place), middle(partner)
        nearest = self.nearest(place)
        agree = sum(1 for anchor, other in nearest
                    if other[0] == partner[0] and abs(middle(other) - target) <= abs(anchor - point) + SLACK)
        return agree, len(nearest)

    def supports(self, place, partner):
        agree, asked = self.agreeing(place, partner)
        return agree > 0 and 2 * agree >= asked


def tagIsElsewhere(v4, geneId, v5s, places, collinearity):
    """Whether NCBI's tag files this v4 id under a GeneID away from where the v5 genome has that gene.

    NCBI really carries these tags, and a handful are wrong. The v5 genome places a v4 id
    two ways: the v5 genes MaizeGDB pairs it with, and where MaizeGDB's 1:1 pairs around
    it on v4 sit on v5 (collinearity). KEGG's place for the GeneID has to be within SLACK
    of the first or pass the second. ZEAMMB73_Zm00001d033937 sits on waxy 1 (541854,
    chromosome 9) while both put the id on chromosome 1, and ZEAMMB73_Zm00001d034536 on
    103644213, on the id's chromosome but 44 Mb from both. Where KEGG gives the GeneID no
    place, gene_info's chromosome is compared instead, and a MaizeGDB v5 gene on it
    explains a difference. Only numbered chromosomes count, so a contig or a GeneID filed
    as "Un" is not evidence of anything, and the row is not checked.
    """
    own = places.v4.get(v4)
    if own is None or not own[0].isdigit():
        return False
    partners = [places.v5[v5] for v5 in v5s if v5 in places.v5]
    target = places.kegg.get(geneId)
    if target is not None and target[0].isdigit():
        return not (any(distance(target, partner) <= SLACK for partner in partners)
                    or collinearity.supports(own, target))
    chromosome = places.chromosomes.get(geneId, "")
    if not chromosome.isdigit() or own[0] == chromosome:
        return False
    return not any(partner[0] == chromosome for partner in partners)


def reachesTheLocus(species, v5, places):
    """Whether a KEGG gene this v5 gene reaches overlaps it: Ensembl's link can name a gene elsewhere on the genome."""
    place = places.v5.get(v5)
    return place is not None and any(gene in places.kegg and overlaps(place, places.kegg[gene])
                                     for gene in species.reach("ensembl_gene", v5))


def maizeRows(species, locusTags, v4ToV5, v5ToV4, places):
    """B73 v4 gene ids: returns (rows, ids NCBI's pass skipped, ids MaizeGDB's pass skipped).

    NCBI's locus tag where its GeneID is in KEGG, unless the GeneID is away from where
    the v5 genome has the id (tagIsElsewhere). It names one GeneID, the id KEGG's maize
    genes are keyed on. Otherwise MaizeGDB's strict 1:1 pair, whose route goes through
    whatever the v5 Ensembl gene is linked to, which is sometimes several genes and
    sometimes a gene elsewhere: at least one of them has to overlap the v5 gene. An id
    whose tag was skipped loses its pair only when the pair's v5 gene reaches the very
    gene the tag was skipped for: Zm00001d017669 and Zm00001d025539 do, and
    Zm00001d052718's reaches only another gene, so that id keeps its pair.
    """
    kegg = species.displayIds("kegg_id")
    collinearity = Collinearity([(places.v4[v4], places.v5[v5s[0]]) for v4, v5s in v4ToV5.items()
                                 if len(v5s) == 1 and v5ToV4.get(v5s[0]) == [v4]
                                 and v4 in places.v4 and v5s[0] in places.v5])
    rows = {}
    skippedNcbi = {}
    skippedMaizeGdb = []
    for tag, geneIds in locusTags.items():
        if tag.startswith("Zm00001d"):
            known = sorted(gene for gene in geneIds if gene in kegg and species.has("ncbi_geneid", gene))
            if len(known) == 1:
                if tagIsElsewhere(tag, known[0], v4ToV5.get(tag, ()), places, collinearity):
                    skippedNcbi[tag] = known[0]
                else:
                    rows[tag] = (tag, GENE_ALIAS, "ncbi_geneid", known[0], "ncbi_locus_tag")
    for v4, v5s in v4ToV5.items():
        if v4 not in rows and len(v5s) == 1 and v5ToV4.get(v5s[0]) == [v4]:
            reached = species.reach("ensembl_gene", v5s[0])
            if reached and skippedNcbi.get(v4) not in reached:
                if reachesTheLocus(species, v5s[0], places):
                    rows[v4] = (v4, GENE_ALIAS, "ensembl_gene", v5s[0], "maizegdb_1to1")
                else:
                    skippedMaizeGdb.append(v4)
    return list(rows.values()), sorted(skippedNcbi), sorted(skippedMaizeGdb)


def transcriptGene(transcript):
    """Psat.cameor.v2.1g00050.1 -> Psat.cameor.v2.1g00050"""
    return re.sub(r"\.\d+$", "", transcript)


def publishedPeaPairs(v1OnV2, v2OnV1, intersection):
    """{v2 gene: v1a gene} from the Kreplak et al. table: best hits both ways that also overlap on the genome.

    The sheet's rows are the authors' DIAMOND best hits, one per protein: query,
    target, ... ; and its intersection sheet: the v1 genes laid over the v2 genome.
    """
    bestV1 = {}
    for row in v1OnV2:
        bestV1.setdefault(row[0], row[1])
    bestV2 = {}
    for row in v2OnV1:
        bestV2.setdefault(row[0], row[1])

    reciprocal = {}
    for query, hit in bestV2.items():
        if bestV1.get(hit) == query:
            reciprocal.setdefault(transcriptGene(query), transcriptGene(hit))

    overlap = {}
    for row in intersection:
        pair = (row[4], row[9])
        longer = max(int(row[2]) - int(row[1]) + 1, int(row[7]) - int(row[6]) + 1)
        overlap[pair] = max(overlap.get(pair, 0.0), int(row[10]) / float(longer))
    return {v2: v1 for v2, v1 in reciprocal.items() if overlap.get((v2, v1), 0.0) >= MIN_GENOME_OVERLAP}


#: Where the pea rules read positions from: {gene: (sequence, start, end)}, v2 from the
#: Cameor v2 GFF, zw6 from ZW6's feature table, keyed on GeneID.
PeaPlaces = collections.namedtuple("PeaPlaces", "v2 zw6")
#: The evidence of a pea row with no published pair: the sequence match, and its place.
RBH_COLLINEAR = "refseq_rbh+collinear"


def peaRows(species, published, refseq, places):
    """Pea Cameor v2 gene ids: returns (rows, pairs the routes disagree on, sequence matches collinearity does not support).

    A sequence match with no published pair compares two cultivars, so it also has to
    sit where the pairs both routes agree on put it: Collinearity over those pairs, from
    the Cameor v2 genome to ZW6's. A gene with no such pair on its sequence has none.
    """
    kegg = species.displayIds("kegg_id")
    reached = {v2: species.reach("ensembl_gene", v1) for v2, v1 in published.items()}
    table = {v2: v1 for v2, v1 in published.items() if reached[v2]}
    sequence = {v2: gene for v2, gene in refseq.items() if gene in kegg and species.has("ncbi_geneid", gene)}
    agreed = Collinearity([(places.v2[v2], places.zw6[gene]) for v2, gene in sequence.items()
                           if v2 in table and gene in reached[v2] and v2 in places.v2 and gene in places.zw6])

    rows = []
    conflicts = []
    unsupported = []
    for v2 in sorted(set(table) | set(sequence)):
        if v2 in table and v2 in sequence and sequence[v2] not in reached[v2]:
            conflicts.append((v2, table[v2], sequence[v2]))
        elif v2 in table:
            evidence = "published_table_rbh+coord" + ("+refseq_rbh" if v2 in sequence else "")
            rows.append((v2, GENE_ALIAS, "ensembl_gene", table[v2], evidence))
        elif v2 in places.v2 and sequence[v2] in places.zw6 and agreed.supports(places.v2[v2], places.zw6[sequence[v2]]):
            rows.append((v2, GENE_ALIAS, "ncbi_geneid", sequence[v2], RBH_COLLINEAR))
        else:
            unsupported.append((v2, sequence[v2]))
    return rows, conflicts, unsupported


def dropInferred(rows):
    """The pea rows without those that no published table pairs: the sequence match and its place."""
    return [row for row in rows if row[4] != RBH_COLLINEAR]


def sorghumRows(species, locusTags):
    """Ensembl genes that reach no KEGG gene today, whose NCBI locus tag names a GeneID KEGG knows."""
    kegg = species.displayIds("kegg_id")
    rows = []
    for gene in species.displayIds("ensembl_gene"):
        if gene in locusTags and not species.reach("ensembl_gene", gene):
            known = sorted(geneId for geneId in locusTags[gene]
                           if geneId in kegg and species.has("ncbi_geneid", geneId))
            if len(known) == 1:
                rows.append((gene, "ensembl_gene", "ncbi_geneid", known[0], "ncbi_locus_tag"))
    return rows


# --- Output -----------------------------------------------------------------

def writeTable(path, rows):
    """Sorted, without duplicates, and byte-identical from one run to the next: no file name, no mtime."""
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as out:
            out.write(("\t".join(COLUMNS) + "\n").encode("ascii"))
            for row in sorted(set(rows)):
                out.write(("\t".join(row) + "\n").encode("ascii"))


def reportTable(code, rows, species, keggData):
    """What a table covers, for the README and the log."""
    pathwayGenes = set()
    with open(os.path.join(keggData, code, "gene2pathway.list"), encoding="utf-8") as handle:
        for line in handle:
            pathwayGenes.add(line.split("\t")[0].replace(code + ":", ""))
    reached = set()
    for _, _, targetDb, targetId, _ in rows:
        reached |= species.reach(targetDb, targetId)
    evidence = collections.Counter(row[4] for row in rows)
    sys.stderr.write("%s: %d rows %s; %d of %d KEGG genes, %d of %d pathway genes\n"
                     % (code, len(rows), sorted(evidence.items()), len(reached), len(species.displayIds("kegg_id")),
                        len(reached & pathwayGenes), len(pathwayGenes)))


# --- The DIAMOND step -------------------------------------------------------

def proteinGenes(lines):
    """{RefSeq protein accession: GeneID} from an NCBI assembly's feature table."""
    genes = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if fields[0] == "CDS" and fields[10]:
            genes[fields[10]] = fields[15]
    return genes


def fastaRecords(lines):
    """(accession, sequence) per record, with a trailing stop removed: DIAMOND would count it as a residue."""
    accession, parts = None, []
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith(">"):
            if accession is not None:
                yield accession, "".join(parts).rstrip("*")
            accession, parts = line[1:].split()[0], []
        else:
            parts.append(line)
    if accession is not None:
        yield accession, "".join(parts).rstrip("*")


def longestProteinPerGene(records, genes):
    """{accession: sequence} keeping the longest protein of each GeneID, the first of equal length.

    One protein per gene, or a gene with many isoforms takes the best hit only by
    having more chances at it, and its isoforms' e-values are on a larger database.
    """
    longest = {}
    for accession, sequence in records:
        gene = genes.get(accession)
        if gene is not None and (gene not in longest or len(sequence) > len(longest[gene][1])):
            longest[gene] = (accession, sequence)
    return dict(longest.values())


def bestHits(lines, queryGene, subjectGene):
    """{query gene: (bitscore, gene, DIAMOND fields, genes tied at the top)}. A tie goes to the hit DIAMOND lists first.

    The order DIAMOND lists equal scores in is what the shipped table was made with:
    settling them by lowest id instead moved 226 of its 24,127 reciprocal pairs.
    """
    best = {}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        query, subject = queryGene(fields[0]), subjectGene(fields[1])
        if query is None or subject is None:
            continue
        score = float(fields[9])
        known = best.get(query)
        if known is None or score > known[0]:
            best[query] = (score, subject, fields, {subject})
        elif score == known[0]:
            known[3].add(subject)
    return best


def reciprocalHits(v2OnRefseq, refseqOnV2, genes):
    """The RBH_HEADER rows: pairs of genes that are each other's best hit, by bitscore."""
    def refseqGene(accession):
        return genes.get(accession)

    forward = bestHits(v2OnRefseq, transcriptGene, refseqGene)
    backward = bestHits(refseqOnV2, refseqGene, transcriptGene)
    rows = []
    for v2 in sorted(forward):
        _, gene, fields, tiesForward = forward[v2]
        if gene in backward and backward[gene][1] == v2:
            rows.append([v2, gene] + fields[2:] + [str(len(tiesForward)), str(len(backward[gene][3]))])
    return rows


def runRbh(args):
    """Download both proteomes and write the reciprocal hits of pea Cameor v2 against RefSeq ZW6."""
    os.makedirs(args.cache, exist_ok=True)
    v2Fasta = fetchPeaDatasetFile("CAMEOR_V2_ANNOTATION_1_prot.fasta", args.cache)
    refseqFasta = fetch(REFSEQ_ZW6_URL + "protein.faa.gz", os.path.join(args.cache, "GCF_024323335.1_protein.faa.gz"))
    features = fetch(REFSEQ_ZW6_URL + "feature_table.txt.gz", os.path.join(args.cache, ZW6_FEATURES))

    with gzip.open(features, "rt") as handle:
        genes = proteinGenes(handle)
    with gzip.open(refseqFasta, "rt") as handle:
        longest = longestProteinPerGene(fastaRecords(handle), genes)

    work = os.path.join(args.cache, "rbh")
    os.makedirs(work, exist_ok=True)
    v2Clean = os.path.join(work, "v2.fa")
    refseqClean = os.path.join(work, "refseq.fa")
    with open(v2Fasta, encoding="utf-8") as source, open(v2Clean, "w", encoding="utf-8") as out:
        for accession, sequence in fastaRecords(source):
            out.write(">%s\n%s\n" % (accession, sequence))
    with open(refseqClean, "w", encoding="utf-8") as out:
        for accession, sequence in longest.items():
            out.write(">%s\n%s\n" % (accession, sequence))

    threads = str(args.threads)

    def search(query, subjects, output):
        database = subjects + ".db"
        check_call([args.diamond, "makedb", "--in", subjects, "--db", database, "--threads", threads, "--quiet"])
        check_call([args.diamond, "blastp", "--sensitive", "--evalue", "1e-5", "--query", query, "--db", database,
                    "--threads", threads, "--quiet", "--outfmt", "6"] + list(DIAMOND_FIELDS) + ["--out", output])
        return output

    v2OnRefseq = search(v2Clean, refseqClean, os.path.join(work, "v2_on_refseq.tsv"))
    refseqOnV2 = search(refseqClean, v2Clean, os.path.join(work, "refseq_on_v2.tsv"))
    with open(v2OnRefseq, encoding="utf-8") as forward, open(refseqOnV2, encoding="utf-8") as backward:
        rows = reciprocalHits(forward, backward, genes)

    target = os.path.join(args.cache, RBH_FILE)
    with open(target, "w", encoding="utf-8") as out:
        out.write("\t".join(RBH_HEADER) + "\n")
        for row in rows:
            out.write("\t".join(row) + "\n")
    sys.stderr.write("wrote %s: %d reciprocal pairs\n" % (target, len(rows)))


# --- The tables step --------------------------------------------------------

def requirePlaced(source, chromosomeOf, ids, least, code="zma", numbered=True):
    """Stop unless a position source places this share of the ids it is meant to place.

    Maize counts an id on a numbered chromosome, the only places its check compares. Pea
    counts one anywhere, a scaffold included, because its check compares those too.
    """
    if numbered:
        placed, where = sum(1 for gene in ids if chromosomeOf.get(gene, "").isdigit()), "on a numbered chromosome"
    else:
        placed, where = sum(1 for gene in ids if gene in chromosomeOf), "anywhere"
    if not ids or placed < least * len(ids):
        raise SystemExit("%s: %s puts %d of %d ids %s, under the %d %% it has to: no table written"
                         % (code, source, placed, len(ids), where, least * 100))


def buildMaize(args):
    species = Species("zma", args.kegg_data)
    geneInfo = geneInfoForTaxon(args.cache, MAIZE_TAXON)
    with gzip.open(geneInfo, "rt", errors="replace") as handle:
        locusTags = readLocusTags(handle, MAIZE_LOCUS_TAG_PREFIX)
    with gzip.open(geneInfo, "rt", errors="replace") as handle:
        chromosomes = readGeneChromosomes(handle)
    tables = []
    for name in ("B73v4_to_B73v5.tsv", "B73v5_to_B73v4.tsv"):
        with open(fetch(MAIZEGDB_XREF_URL + name, os.path.join(args.cache, name)), encoding="utf-8") as handle:
            tables.append(readGeneLists(handle))
    genes = []
    for url in (MAIZE_V4_GFF_URL, MAIZE_V5_GFF_URL):
        with gzip.open(fetch(url, os.path.join(args.cache, url.rsplit("/", 1)[1])), "rt", encoding="utf-8") as handle:
            genes.append(readGffGenes(handle))
    places = MaizePlaces(genes[0], genes[1], species.keggPlaces(), chromosomes)
    keggGenes = species.displayIds("kegg_id")
    requirePlaced("the v4 GFF", {gene: place[0] for gene, place in places.v4.items()}, tables[0], MIN_GFF_PLACED)
    requirePlaced("the v5 GFF", {gene: place[0] for gene, place in places.v5.items()}, tables[1], MIN_GFF_PLACED)
    requirePlaced("KEGG's gene list", {gene: place[0] for gene, place in places.kegg.items()}, keggGenes, MIN_LIST_PLACED)
    requirePlaced("gene_info", places.chromosomes, keggGenes, MIN_LIST_PLACED)
    rows, skippedNcbi, skippedMaizeGdb = maizeRows(species, locusTags, tables[0], tables[1], places)
    sys.stderr.write("zma: locus tag's GeneID away from where v5 has the id, %d skipped: %s\n"
                     % (len(skippedNcbi), " ".join(skippedNcbi)))
    sys.stderr.write("zma: v5 gene's KEGG genes elsewhere, %d skipped: %s\n"
                     % (len(skippedMaizeGdb), " ".join(skippedMaizeGdb)))
    return species, rows


def buildPea(args):
    species = Species("psat", args.kegg_data)
    workbook = readWorkbook(fetchPeaDatasetFile("v1_genes_to_v2.xlsx", args.cache),
                            ("v1_vs_v2", "v2_vs_v1", "intersection_v1_v2"))
    rbhFile = os.path.join(args.cache, RBH_FILE)
    if not os.path.isfile(rbhFile):
        raise SystemExit(rbhFile + " is missing: run the `rbh` step first (it needs DIAMOND)")
    with open(rbhFile, encoding="utf-8") as handle:
        refseq = readRefseqReciprocal(handle)
    published = publishedPeaPairs(workbook["v1_vs_v2"], workbook["v2_vs_v1"], workbook["intersection_v1_v2"])
    places = peaPlaces(args.cache)
    requirePlaced("the Cameor v2 GFF", {gene: place[0] for gene, place in places.v2.items()},
                  set(published) | set(refseq), MIN_GFF_PLACED, code="psat", numbered=False)
    requirePlaced("ZW6's feature table", {gene: place[0] for gene, place in places.zw6.items()},
                  set(refseq.values()), MIN_GFF_PLACED, code="psat", numbered=False)
    rows, conflicts, unsupported = peaRows(species, published, refseq, places)
    sys.stderr.write("psat: %d genes dropped because the published table and the sequence match disagree\n"
                     % len(conflicts))
    sys.stderr.write("psat: %d sequence matches dropped, their neighbours do not put the GeneID there: %s\n"
                     % (len(unsupported), " ".join(v2 for v2, _ in unsupported)))
    if args.drop_inferred:
        rows = dropInferred(rows)
    return species, rows


def peaPlaces(cache):
    """Where the Cameor v2 genes and ZW6's GeneIDs are, from the dataset's GFF and the RefSeq feature table."""
    with open(fetchPeaDatasetFile(PEA_GFF, cache), encoding="utf-8") as handle:
        v2 = readGffGenes(handle)
    with gzip.open(fetch(REFSEQ_ZW6_URL + "feature_table.txt.gz", os.path.join(cache, ZW6_FEATURES)), "rt",
                   encoding="utf-8") as handle:
        zw6 = readFeatureGenes(handle)
    return PeaPlaces(v2, zw6)


def buildSorghum(args):
    species = Species("sbi", args.kegg_data)
    with gzip.open(geneInfoForTaxon(args.cache, SORGHUM_TAXON), "rt", errors="replace") as handle:
        locusTags = readLocusTags(handle)
    return species, sorghumRows(species, locusTags)


BUILDS = {"zma": buildMaize, "psat": buildPea, "sbi": buildSorghum}


def runTables(args):
    os.makedirs(args.cache, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    for code in args.species:
        species, rows = BUILDS[code](args)
        if not rows:
            raise SystemExit(code + ": no rows chosen, so no table written")
        # The report reads gene2pathway.list: fail on it before the table changes.
        reportTable(code, rows, species, args.kegg_data)
        writeTable(os.path.join(args.out, code + ".tsv.gz"), rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)

    rbh = steps.add_parser("rbh", help="pea Cameor v2 against RefSeq ZW6, with DIAMOND")
    rbh.add_argument("--cache", required=True, help="directory for downloads and intermediate files")
    rbh.add_argument("--threads", type=int, default=8)
    rbh.add_argument("--diamond", default="diamond", help="the DIAMOND binary")
    rbh.set_defaults(run=runRbh)

    tables = steps.add_parser("tables", help="write the alias tables")
    tables.add_argument("--kegg-data", required=True,
                        help="directory holding <code>/mapping/ and <code>/gene2pathway.list for each species")
    tables.add_argument("--cache", required=True, help="directory for downloads")
    tables.add_argument("--out", default=HERE, help="directory to write <code>.tsv.gz into")
    tables.add_argument("--species", nargs="+", choices=sorted(BUILDS), default=sorted(BUILDS))
    tables.add_argument("--drop-inferred", action="store_true",
                        help="leave out the pea rows no published table pairs: the cross-cultivar sequence match "
                             "and its collinearity")
    tables.set_defaults(run=runTables)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
