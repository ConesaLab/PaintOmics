#!/usr/bin/env python
import os, csv, json, shutil, re, itertools, glob, random
from collections import defaultdict, Counter
from time import sleep, strftime
from sys import stderr, path
from subprocess import check_call, check_output, CalledProcessError
import sys

# Configure CSV field size limit to handle large fields in mapping data
# Use a safe approach that works across different platforms
field_size_limit = sys.maxsize
while True:
    try:
        csv.field_size_limit(field_size_limit)
        break
    except OverflowError:
        # On some platforms (Windows), sys.maxsize is too large
        # Progressively reduce until we find a value that works
        field_size_limit = int(field_size_limit / 10)

path.insert(0, os.path.dirname(os.path.realpath(__file__)) + "/../")

class XREF_Entry (object):
    def __init__(self, display_id, dbname_id, description):
        self._id= None
        self.display_id= display_id
        self.dbname_id= dbname_id
        self.description= description

    def setID(self, _id):
        self._id = _id
    def getID(self):
        return self._id
    def __str__(self):
        return "{_id : " + self._id + "display_id : " + self.display_id + "dbname_id : " + self.dbname_id + "description : " + self.description + "}"
    def __repr__(self):
        return self.__str__()

class DBNAME_Entry (object):
    def __init__(self, dbname, display_label, dbname_type):
        self._id= None
        self.dbname= dbname
        self.display_label= display_label
        self.dbname_type = dbname_type

    def setID(self, _id):
        self._id = _id
    def getID(self):
        return self._id

_ID_ALPHABET = "0123456789abcdefABCDEF"   # string.hexdigits, resolved once


def generateRandomID(database = 'global'):
    # choices (with replacement), not sample (without). The old line was
    #     ''.join(random.sample(string.hexdigits*5, 24))
    # which drew 24 items without replacement from a 110-character population, so every
    # single character cost a _randbelow() call against a shrinking pool. Profiling the
    # real 281,304-row ensembl mapping showed this function was 5.0s of a 6.9s parse --
    # 73% of the build's CPU -- with 10.8 million _randbelow calls underneath it.
    #
    # The output is unchanged in every way that matters: same 24 characters, same
    # hexdigits alphabet. The keyspace drops from ~10^48 to ~10^32, which is still
    # astronomically beyond the ~450k ids a build mints (birthday collision probability
    # ~10^-21), and the uniqueness loop below remains the actual guarantee.
    # Measured: 2.16s -> 0.62s for 450,000 ids, 0 collisions.
    randomID = ""
    valid = False
    while not valid:
        randomID = ''.join(random.choices(_ID_ALPHABET, k=24))
        valid = ((not randomID in xref[database]) and (not randomID in transcript2xref[database]) and (not randomID in dbname))

    return randomID

def insertXREF(item, database = 'global'):
    id_key = item.display_id
    elemAux = ALL_ENTRIES.get(id_key+"#"+item.dbname_id, None)

    if elemAux == None: #did not exists
        item.setID(generateRandomID(database))
        xref[database][item.getID()] = item
        ALL_ENTRIES[id_key+"#"+item.dbname_id] = item
        # For Reactome save the real key in ALL_ENTRIES based on the display name
        KEY_ENTRIES[id_key] = id_key+"#"+item.dbname_id
        elemAux=item

    return elemAux.getID()

def findXREF(entry_name, db_id):
    return ALL_ENTRIES.get(entry_name + "#" + db_id, None)

def findXREFByEntry(entry_name):
    return ALL_ENTRIES.get(KEY_ENTRIES.get(entry_name, None), None)

def deleteXREF(itemID, database = 'global'):
    del ALL_ENTRIES[xref[database].get(itemID).display_id + "#" + xref[database].get(itemID).dbname_id]
    del xref[database][itemID]

def insertTR_XREF(item_id, transcript_id, database = 'global'):
    elemAux = transcript2xref[database].get(transcript_id, set([]))
    if not transcript_id in transcript2xref[database]:
        transcript2xref[database][transcript_id] = elemAux
    elemAux.add(item_id)

    elemAux = xref2transcript[database].get(item_id, set([]))
    if not item_id in xref2transcript[database]:
        xref2transcript[database][item_id] = elemAux
    elemAux.add(transcript_id)

def insertDatabase(item):
    elemAux = ALL_DBS.get(item.dbname, None)

    if elemAux == None: #did not exists
        item.setID(generateRandomID())
        dbname[item.getID()] = item
        ALL_DBS[item.dbname] = item
        elemAux = item

    return elemAux.getID()


#**************************************************************************
# ENSEMBL GENEBUILD REGISTRY
#
# Every species whose organism has an Ensembl / Ensembl Genomes genebuild
# must carry its Ensembl gene, transcript and peptide identifiers, linked to
# the KEGG identifier table. Species with their own <code>_resources/ declare
# the dumps in download_conf.py; every other species is served by
# scripts/default/, which consults this registry. Measured on paintomics.org
# on 2026-09-10: 157 of 171 installed species had no ensembl_gene table, 72 of
# them eukaryotes with a genebuild, so an Ensembl gene id from a GTF or an
# expression matrix resolved 0 features on soybean, maize, grape, rice, goat
# and 60-odd others while the job reported success.
#**************************************************************************
ENSEMBL_GENEBUILDS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                       "common_resources", "ensembl_genebuilds.json")

#: The two identifier spaces one Ensembl dump can be asked for, and the
#: db_name values that carry them. UniProt is split across reviewed and
#: unreviewed entries, and both are real accessions KEGG maps to genes.
ENSEMBL_XREF_DBS = {"entrez": "EntrezGene",
                    "uniprot": ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL"]}


def loadEnsemblGenebuilds(path=ENSEMBL_GENEBUILDS_FILE):
    """The registry document: bases plus a `genebuilds` map keyed by species code."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def ensemblResourcesFor(specie, path=ENSEMBL_GENEBUILDS_FILE):
    """EXTERNAL_RESOURCES entries for a registered species, or {} if it has none.

    Shaped exactly like a hand-written download_conf.py so the same downloader
    (downloadEnsemblMapping) and the same processors serve both kinds of species:
      "ensembl"         -> the EntrezGene dump, read by processEnsemblData
      "ensembl_uniprot" -> the UniProt dump, read by processEnsemblUniProtData
    A genebuild that publishes only one of the two gets only that key.
    """
    registry = loadEnsemblGenebuilds(path)
    entry = registry.get("genebuilds", {}).get(specie)
    if not entry:
        return {}

    base = registry["vertebrates-base"] if entry["division"] == "vertebrates" else registry["genomes-base"]
    common = {"url": base, "species-dir": entry["species-path"], "division": entry["division"]}
    resources = {}
    if "entrez" in entry.get("xrefs", []):
        resources["ensembl"] = [dict(common, output="ensembl_mapping.list",
                                     description="Source: Ensembl cross-reference TSV dump (" +
                                                 str(entry.get("assembly")) + "), EntrezGene rows.")]
    if "uniprot" in entry.get("xrefs", []):
        resources["ensembl_uniprot"] = [dict(common, output="ensembl_uniprot.list",
                                             description="Source: Ensembl cross-reference TSV dump (" +
                                                         str(entry.get("assembly")) + "), UniProt rows.",
                                             **{"xref-type": "uniprot", "xref-db": ENSEMBL_XREF_DBS["uniprot"]})]
    return resources


#: Prefixes some Ensembl Genomes genebuilds carry on every identifier because
#: the annotation was imported from GenBank/RefSeq: tomato (SL4.0) names its
#: genes `gene-Solyc01g080690.3`, transcripts `mRNA-Solyc01g080690.3.1` and
#: peptides `CDS-Solyc01g080690.3.1`; lettuce uses `gene-`/`rna-`. Ensembl's
#: own GTF uses the prefixed form, so it is kept; but nobody uploads it -- an
#: ITAG gene id in a user's matrix is `Solyc01g080690.3` -- so the bare form is
#: stored alongside, in the same table and the same mate group. Measured on
#: paintomics.org 2026-09-10: `Solyc03g007960.3` matched no table at all.
ENSEMBL_ID_PREFIXES = ("gene-", "mRNA-", "rna-", "CDS-", "cds-")


def bareEnsemblIdentifier(identifier):
    """The identifier without a GenBank-style type prefix, or None if it has none."""
    for prefix in ENSEMBL_ID_PREFIXES:
        if identifier.startswith(prefix) and len(identifier) > len(prefix):
            return identifier[len(prefix):]
    return None


def _insertEnsemblIdentifier(value, db_id, description, transcript_key):
    """Insert an Ensembl identifier and, when it carries a type prefix, its bare form too.

    Both land in the same table and the same transcript group, so either spelling
    reaches everything the group reaches. With ``transcript_key`` None the value IS
    the transcript and keys its own group. Returns the xref id of the value itself.
    """
    xref_id = insertXREF(XREF_Entry(value, db_id, description))
    if transcript_key is None:
        # The transcript itself: its own xref id keys the group, as
        # processEnsemblData has always done (insertTR_XREF(ti, ti)).
        transcript_key = xref_id
    insertTR_XREF(xref_id, transcript_key)
    bare = bareEnsemblIdentifier(value)
    if bare is not None:
        insertTR_XREF(insertXREF(XREF_Entry(bare, db_id, description)), transcript_key)
    return xref_id

# def translate(featureName, destinationDB):
#     found=[]
#
#     #FIND THE ID FOR DATABASE
#     for item in dbname.itervalues():
#         if(item.dbname == destinationDB):
#             destinationDB = item.getID()
#             break
#
#     #FIND THE FEATURES WHOSE NAME MATCH TO PROVIDED
#     for item in xref.itervalues():
#         if(item.display_id == featureName):
#             found.append(item.getID())
#
#     #FOR EACH MATCH, FIND THE ASSOCIATED TRANSCRIPTS
#     found2 = set([])
#     for item in found:
#         for key, value in transcript2xref.iteritems():
#             if(item in value): #IF THE TRANSCRIPT IS ASSOCIATED TO CURRENT ITEM
#                 found2 = found2.union(value) #MERGE ALL POSSIBLE MATCHES (WE WILL FILTER LATER BY DB ID)
#
#     #FOR EACH MATCHED ITEM, FILTER BY DATABASE ID
#     found=[]
#     for item_id in found2:
#         item = xref.get(item_id)
#         if(item.dbname_id == destinationDB):
#             found.append(item)
#
#     return found

def showPercentage(n, total, prev, errorMessage):
    # `prev` was accepted and returned but never used to gate the write, so this emitted
    # one progress line PER INPUT ROW. Measured on a real 358,853-row mapping file: 12.8 MB
    # of carriage-return-separated output, or 45.1 MB once errorMessage goes sticky (four
    # of the five processors never clear it), all appended to one shared install.log.
    # The cost is operability, not speed -- it is only ~0.16s -- but it buries the one
    # exception an admin needs after a multi-hour failure under tens of MB with no
    # newlines. Emitting only on change drops it to 11 lines per file.
    #
    # Silent unless PAINTOMICS_INSTALL_VERBOSE=1. A progress bar is for a human watching
    # a terminal; this output goes to a shared install.log that is read after the fact,
    # where it only makes the real messages harder to find.
    percen = int(n/float(total)*10)
    if percen == prev or not VERBOSE:
        return percen
    stderr.write("0%[" + ("#"*percen) + (" "*(10 - percen)) + "]100% [" + str(n) + "/" + str(total) + "]\t"+ errorMessage + "\r" )
    return percen


#**************************************************************************
# FAIL-SOFT INPUT HANDLING
#
# A species is assembled from independent sources. Losing one of them costs the
# identifier types it feeds and nothing else, so the build records what it lost and
# carries on; only a result that cannot be used at all is worth refusing. The old
# behaviour -- exit(1) the moment any input file was absent -- threw away organisms
# that would have installed perfectly well with, say, no VEGA ids.
#**************************************************************************
def skipSource(label, reason, consequence):
    """Record that an optional input is unusable and say what it costs."""
    SKIPPED_SOURCES.append((label, reason, consequence))
    stderr.write("\nWARNING [" + label + "] " + reason + "\n         -> " + consequence + "\n")


def haveInputFile(label, fileName, consequence):
    """True if fileName is usable; otherwise record the skip and return False.

    Also rejects a file that exists but is empty, which is what a failed download
    leaves behind and what used to be parsed into an empty identifier type.
    """
    if not fileName or not os.path.isfile(fileName):
        skipSource(label, "input file not found: " + str(fileName), consequence)
        return False
    if os.path.getsize(fileName) == 0:
        skipSource(label, "input file is empty: " + fileName, consequence)
        return False
    return True


def assertInstallable():
    """Refuse only a species that cannot answer a single query.

    Two things make a species usable: at least one identifier the user can upload,
    and at least one pathway to paint. Everything else is a degradation worth a
    warning. Checked here rather than at each input so that a build missing four of
    six sources still installs if the remaining two carry the species.
    """
    identifiers = sum(len(entries) for entries in xref.values())
    pathways = len(ALL_PATHWAYS)

    if identifiers == 0 or pathways == 0:
        raise Exception(
            "Refusing to install " + str(SPECIE) + ": the build produced " +
            str(identifiers) + " identifier(s) and " + str(pathways) + " pathway(s), so "
            "nothing a user uploads could ever match. Skipped sources: " +
            (", ".join(label for label, _, _ in SKIPPED_SOURCES) or "none") +
            ". This is the one case worth failing on -- every other missing source is "
            "reported as a warning and installed anyway.")

    return identifiers, pathways


def countIdentifiersByType():
    """Number of xref entries per identifier type, as the database will store them.

    `xref` is keyed by SCOPE ('global'), not by identifier type -- grouping on its keys
    reports one bucket called "global" and hides exactly what this summary exists to
    show. The type lives on each entry as dbname_id, resolved through `dbname`.
    """
    perType = Counter()
    for entries in xref.values():
        for entry in entries.values():
            label = dbname[entry.dbname_id].dbname if entry.dbname_id in dbname else str(entry.dbname_id)
            perType[label] += 1
    return perType


def summariseBuild():
    """One compact block per species: what was built, and what was lost."""
    identifiers = sum(len(entries) for entries in xref.values())
    byType = sorted(countIdentifiersByType().items())

    stderr.write("\n" + "=" * 62 + "\n")
    stderr.write("BUILD SUMMARY  " + str(SPECIE) + "\n")
    stderr.write("=" * 62 + "\n")
    stderr.write("  identifiers : %d across %d type(s)\n" % (identifiers, len(byType)))
    for name, count in byType:
        stderr.write("      %-32s %8d\n" % (name, count))
    stderr.write("  pathways    : %d\n" % len(ALL_PATHWAYS))

    dropped = {k: len(v) for k, v in FAILED_LINES.items() if v}
    if dropped:
        stderr.write("  rows dropped while parsing (per source):\n")
        for k, v in sorted(dropped.items()):
            stderr.write("      %-32s %8d\n" % (k, v))

    if UNKNOWN_PATHWAY_PAIRS or PATHWAYS_WITHOUT_NODES:
        stderr.write("  pathways missing from the shared KEGG reference:\n")
        if UNKNOWN_PATHWAY_PAIRS:
            unknown = sorted({p for pair in UNKNOWN_PATHWAY_PAIRS for p in pair})
            stderr.write("      %-32s %8d  (e.g. %s)\n" % (
                "unlinked pathway pairs", len(UNKNOWN_PATHWAY_PAIRS),
                ", ".join(unknown[:5])))
        if PATHWAYS_WITHOUT_NODES:
            stderr.write("      %-32s %8d  (e.g. %s)\n" % (
                "pathways with no node data", len(PATHWAYS_WITHOUT_NODES),
                ", ".join(sorted(set(PATHWAYS_WITHOUT_NODES))[:5])))
        stderr.write("      -> re-run the download with --common=1 to refresh "
                     "pathways_all.list, then reinstall this species\n")

    if SKIPPED_SOURCES:
        stderr.write("  WARNINGS -- installed WITHOUT these sources:\n")
        for label, reason, consequence in SKIPPED_SOURCES:
            stderr.write("      %-20s %s\n" % (label, reason))
            stderr.write("      %-20s -> %s\n" % ("", consequence))
    else:
        stderr.write("  warnings    : none\n")
    stderr.write("=" * 62 + "\n")

    # Hand the warnings back to DBManager, which runs this build as a subprocess and
    # otherwise only sees an exit status.
    #
    # The destination is whatever DBManager named in PAINTOMICS_BUILD_WARNINGS: a
    # fresh 0600 file per species, not a fixed /tmp path that two concurrent installs
    # would both write to and read each other's warnings out of. When the variable is
    # absent there is no parent listening -- a build run by hand from the shell --
    # so nothing is written and the summary above is the whole report.
    handoffPath = os.environ.get("PAINTOMICS_BUILD_WARNINGS")
    if not handoffPath:
        return

    # Tab-separated, one record per line, so a reason carrying either character
    # would split into fields the reader then drops. These strings are file paths
    # and str(exception), neither of which is under our control.
    def oneLine(value):
        return " ".join(str(value).split())

    try:
        with open(handoffPath, "w") as handle:
            for label, reason, consequence in SKIPPED_SOURCES:
                handle.write("\t".join([oneLine(SPECIE), oneLine(label),
                                        oneLine(reason), oneLine(consequence)]) + "\n")
    except Exception as writeError:
        stderr.write("  (could not write the warning hand-off file: %s)\n" % writeError)

#**************************************************************************
#* SHARED FEATURES BETWEEN PATHWAYS
#*
#* All three network builders (KEGG, Reactome, MapMan) fill the same diagonal
#* pathway-pair matrix and emit one "shared biological features" edge per
#* non-zero cell. All three filled it from a gene -> pathways mapping ONLY, so
#* two pathways sharing metabolites and no gene were never connected, and the
#* edge weight the view starts from counted genes when the user may have
#* submitted no genes at all. The counting is identical whatever the feature
#* is, so it lives here once and is called for each feature class.
#**************************************************************************
def indexCompoundsByPathway(ALL_PATHWAYS):
    """compound id -> the set of pathways containing it.

    Blank identifiers are dropped rather than indexed: an empty id present in
    two pathways joins them, and an empty id present in a hundred joins all
    hundred to each other. Reactome writes an entry with no usable id whenever
    a SimpleEntity carries neither a ChEBI nor a KEGG mapping.
    """
    index = defaultdict(set)
    for pathwayID, pathway in ALL_PATHWAYS.items():
        for compound in pathway.get("compounds", []) or []:
            compoundID = compound.get("id")
            if compoundID:
                index[compoundID].add(pathwayID)
    return index


def accumulateSharedFeatures(pathways_matrix, feature2pathways):
    """Add one to every pathway PAIR that shares a feature.

    `pathways_matrix` is diagonal -- only one of (a,b) and (b,a) is a key -- so
    each pair is incremented through whichever direction exists. A pair that
    exists in neither direction names a pathway that never became a node and is
    skipped; writing the missing direction in would double the weight of every
    pair, since the bulk step below reads both.
    """
    for pathways in feature2pathways.values():
        associated = sorted(pathways)
        for index, current_path in enumerate(associated):
            row = pathways_matrix.get(current_path)
            for other_path in associated[index + 1:]:
                if row is not None and other_path in row:
                    row[other_path] += 1
                else:
                    other_row = pathways_matrix.get(other_path)
                    if other_row is not None and current_path in other_row:
                        other_row[current_path] += 1


#**************************************************************************
#  ______ _   _  _____ ______ __  __ ____  _
# |  ____| \ | |/ ____|  ____|  \/  |  _ \| |
# | |__  |  \| | (___ | |__  | \  / | |_) | |
# |  __| | . ` |\___ \|  __| | |\/| |  _ <| |
# | |____| |\  |____) | |____| |  | | |_) | |____
# |______|_| \_|_____/|______|_|  |_|____/|______|
#
def normaliseKeggPathwayId(value, specie=None):
    """Reduce any KEGG pathway identifier to its bare 5-digit map number.

    KEGG is not self-consistent about prefixes, and it has changed convention before.
    Measured live on 2026-08-12:

        /list/pathway/mmu      ->  mmu01100          (bare, no "path:")
        /link/pathway/mmu      ->  path:mmu00010     (prefixed)
        /link/pathway/compound ->  path:map00010     (prefixed, and "map" not the organism)

    Callers key NODES and ALL_PATHWAYS by the bare number ("01100"), which the BRITE
    classification file also emits. The code used to get there with
    `row[1].replace("path:" + SPECIE, "")` -- a literal, positional strip that silently
    yields "mmu00010" instead of "00010" the moment KEGG drops the "path:" prefix from
    /link the way it already dropped it from /list. Parse the shape instead: strip any
    leading "<db>:" and any leading organism or "map" code, then require 5 digits.

    Returns "" when the value is not a pathway id, so a caller can skip it rather than
    silently indexing on a malformed key.
    """
    if not value:
        return ""
    identifier = value.strip()
    if ":" in identifier:                      # path:mmu00010 -> mmu00010
        identifier = identifier.split(":", 1)[1]
    if specie and identifier.startswith(specie):
        identifier = identifier[len(specie):]  # mmu00010 -> 00010
    elif identifier.startswith("map"):
        identifier = identifier[3:]            # map00010 -> 00010
    else:
        # Any other organism prefix: strip the leading non-digits.
        identifier = identifier.lstrip("abcdefghijklmnopqrstuvwxyz")
    return identifier if identifier.isdigit() else ""


#**************************************************************************
def processEnsemblData():
    """
    # ENSEMBL MAPPING FILE CONTAINS THE FOLLOWING COLUMNS
    # 1. Ensembl Gene ID
    # 2. EntrezGene ID
    # 3. Ensembl Protein ID
    # 4. Ensembl Transcript ID
    """
    FAILED_LINES["ENSEMBL"]=[]

    resource = EXTERNAL_RESOURCES.get("ensembl")[0]
    file_name= DATA_DIR + "mapping/" + resource.get("output")
    if not haveInputFile("ENSEMBL", file_name,
                         "Ensembl gene/transcript/protein identifiers will be absent for this species"):
        return

    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    #Register databases and get the assigned IDs
    ensembl_transcript_db_id = insertDatabase(DBNAME_Entry("ensembl_transcript", "Ensembl transcript", "Identifier"))
    ensembl_gene_db_id = insertDatabase(DBNAME_Entry("ensembl_gene", "Ensembl gene", "Identifier"))
    ensembl_peptide_db_id = insertDatabase(DBNAME_Entry("ensembl_peptide", "Ensembl protein", "Identifier"))
    entrezgene_db_id = insertDatabase(DBNAME_Entry("entrezgene", "EntrezGene ID", "Identifier"))

    #Process files
    stderr.write("PROCESSING ENSEMBL MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                ensembl_gi = row[0]
                entrez_gi = row[1]
                ensembl_pi = row[2]
                ensembl_ti = row[3]

                if ensembl_ti == "": #ALWAYS FALSE
                    raise Exception("Empty ENSEMBL transcript value.")

                # The transcript is the group key: every identifier on this row
                # joins its group. A prefixed id (gene-/mRNA-/CDS-) is stored in
                # both spellings, see ENSEMBL_ID_PREFIXES.
                ensembl_ti = _insertEnsemblIdentifier(ensembl_ti, ensembl_transcript_db_id,
                                                      resource.get("description"), None)

                if ensembl_gi != "": #ALWAYS TRUE
                    _insertEnsemblIdentifier(ensembl_gi, ensembl_gene_db_id, resource.get("description"), ensembl_ti)

                if entrez_gi != "":
                    entrez_gi = insertXREF(XREF_Entry(entrez_gi, entrezgene_db_id, resource.get("description")))
                    insertTR_XREF(entrez_gi, ensembl_ti)

                if ensembl_pi != "":
                    _insertEnsemblIdentifier(ensembl_pi, ensembl_peptide_db_id, resource.get("description"), ensembl_ti)

            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING ENSEMBL MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES["ENSEMBL"].append([errorMessage] + row)
    csvfile.close()

    TOTAL_FEATURES["ENSEMBL"]=total_lines

    return total_lines


def processEnsemblUniProtData():
    """Link Ensembl identifiers to KEGG through the UniProt accessions KEGG maps itself.

    Eighteen registered genebuilds (Ensembl Fungi collections, Dictyostelium,
    Leishmania, mung bean) publish no EntrezGene cross-references, so
    processEnsemblData has nothing to link them with. They all publish UniProt
    ones, and processKEGGMappingData files KEGG's own uniprot -> kegg_id list
    under the same `uniprot_acc` table. Where the accession already exists, the
    Ensembl gene, transcript and peptide join ITS transcript groups, which is
    where kegg_id sits -- one hop, no bridge needed. An accession KEGG does not
    know is inserted and linked to the Ensembl transcript so it stays reachable.

    Must run AFTER processKEGGMappingData: it only ever joins groups that exist.
    A no-op for species whose configuration declares no "ensembl_uniprot"
    resource, and a warned skip when the declared file is absent.

    Input: mapping/ensembl_uniprot.list as written by downloadEnsemblMapping with
    xref-type "uniprot": gene, accession, peptide, transcript per row.
    """
    resources = (EXTERNAL_RESOURCES or {}).get("ensembl_uniprot")
    if not resources:
        return 0
    resource = resources[0]

    FAILED_LINES["ENSEMBL-UNIPROT"] = []
    file_name = DATA_DIR + "mapping/" + resource.get("output")
    if not haveInputFile("ENSEMBL-UNIPROT", file_name,
                         "Ensembl identifiers will not be linked to KEGG through UniProt for this species"):
        return 0

    if not ALL_DBS.get("kegg_id"):
        stderr.write("WARNING [ENSEMBL-UNIPROT] processKEGGMappingData() has not run yet; "
                     "Ensembl identifiers can only join groups that already exist.\n")

    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    ensembl_transcript_db_id = insertDatabase(DBNAME_Entry("ensembl_transcript", "Ensembl transcript", "Identifier"))
    ensembl_gene_db_id = insertDatabase(DBNAME_Entry("ensembl_gene", "Ensembl gene", "Identifier"))
    ensembl_peptide_db_id = insertDatabase(DBNAME_Entry("ensembl_peptide", "Ensembl protein", "Identifier"))
    uniprot_acc_db_id = insertDatabase(DBNAME_Entry("uniprot_acc", "UniProt Accession", "Identifier"))

    stderr.write("PROCESSING ENSEMBL-UNIPROT MAPPING FILE...\n")
    linked = 0
    unknown = 0
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i = 0
        prev = -1
        errorMessage = ""
        for row in rows:
            i += 1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                ensembl_gi, uniprot_acc, ensembl_pi, ensembl_ti = row[0], row[1], row[2], row[3]
                if ensembl_ti == "":
                    raise Exception("Empty ENSEMBL transcript value.")

                ensembl_ti = _insertEnsemblIdentifier(ensembl_ti, ensembl_transcript_db_id,
                                                      resource.get("description"), None)
                members = [ensembl_ti]
                if ensembl_gi != "":
                    members.append(_insertEnsemblIdentifier(ensembl_gi, ensembl_gene_db_id,
                                                            resource.get("description"), ensembl_ti))
                if ensembl_pi != "":
                    members.append(_insertEnsemblIdentifier(ensembl_pi, ensembl_peptide_db_id,
                                                            resource.get("description"), ensembl_ti))
                if uniprot_acc == "":
                    continue

                known = findXREF(uniprot_acc, uniprot_acc_db_id)
                if known is None:
                    # KEGG does not map this accession: keep it reachable from the
                    # Ensembl side, nothing more.
                    insertTR_XREF(insertXREF(XREF_Entry(uniprot_acc, uniprot_acc_db_id,
                                                        resource.get("description"))), ensembl_ti)
                    unknown += 1
                    continue

                # Join the groups the accession already belongs to (KEGG's), and
                # bring the accession into the Ensembl group as well. Bare forms
                # of prefixed ids were linked to ensembl_ti above, so they follow
                # through the transcript's own membership: link it here too.
                for group in list(xref2transcript['global'].get(known.getID(), ())):
                    for member in members:
                        insertTR_XREF(member, group)
                    for bare in (bareEnsemblIdentifier(row[0]), bareEnsemblIdentifier(row[2]),
                                 bareEnsemblIdentifier(row[3])):
                        if bare is not None:
                            bareItem = findXREF(bare, ensembl_gene_db_id) or \
                                       findXREF(bare, ensembl_peptide_db_id) or \
                                       findXREF(bare, ensembl_transcript_db_id)
                            if bareItem is not None:
                                insertTR_XREF(bareItem.getID(), group)
                insertTR_XREF(known.getID(), ensembl_ti)
                linked += 1
            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING ENSEMBL-UNIPROT MAPPING FILE [line " + str(i) + "]: " + str(ex)
                FAILED_LINES["ENSEMBL-UNIPROT"].append([errorMessage] + row)

    TOTAL_FEATURES["ENSEMBL-UNIPROT"] = total_lines
    stderr.write("  * %d rows joined a KEGG-mapped UniProt accession, %d carried an accession KEGG does not map\n"
                 % (linked, unknown))
    if linked == 0 and total_lines > 0:
        skipSource("ENSEMBL-UNIPROT", "none of the %d UniProt accessions matched KEGG's uniprot list" % total_lines,
                   "Ensembl identifiers are installed but reach no KEGG identifier")
    return total_lines

#**************************************************************************
#
# |  __ \|  ____|  ____/ ____|  ____/ __ \
# | |__) | |__  | |__ | (___ | |__ | |  | |
# |  _  /|  __| |  __| \___ \|  __|| |  | |
# | | \ \| |____| |    ____) | |___| |__| |
# |_|  \_\______|_|   |_____/|______\___\_\
#
#**************************************************************************
def processRefSeqData():
    """
    # REFSEQ MAPPING FILE CONTAINS THE LOT OF COLUMNS
    # 1. tax_id
    # 2. Entrez ID
    # ...
    # 4. RNA_nucleotide_accession.version
    # 5. RNA_nucleotide_gi
    # 6. protein_accession.version
    # 7. protein_gi
    # ...
    """
    stderr.write("\n\nPROCESSING REFSEQ MAPPING FILE...\n")
    FAILED_LINES["REFSEQ"]=[]

    #Get settings for the file
    resource = EXTERNAL_RESOURCES.get("refseq")[0]
    file_name= DATA_DIR + "mapping/" + resource.get("output")

    #Check if file exists
    if not haveInputFile("REFSEQ", file_name,
                         "RefSeq RNA/protein accessions and EntrezGene ids will be absent for this species"):
        return

    #Extract the file in a temporal directory
    stderr.write("  * EXTRACTING FILE...\n")
    # grep, not awk. Both filter the same single column, but awk parses every one of
    # gene2refseq's 95 million rows into fields while grep does an anchored byte match:
    # measured on the real 1,977 MB file, awk took 207.1s and grep 7.6s for byte-identical
    # output (same md5, same 239,667 rows). Decompression alone is 6.6s, so this step is
    # now I/O-bound rather than burning 200s of CPU -- per organism.
    # `set -o pipefail` matters here: without it a corrupt archive makes gunzip exit
    # non-zero while grep still exits 0, and check_call sees success on a truncated file.
    # grep exiting 1 (no rows matched) is also a real failure -- it means the taxid does
    # not occur in this file at all, which used to produce an empty mapping silently.
    command = ("set -o pipefail; gunzip -c " + file_name +
               " | LC_ALL=C grep '^" + str(resource.get("specie-code")) + "\t' > /tmp/build.tmp")
    try:
        check_call(command, shell=True, executable="/bin/bash")
    except CalledProcessError as filterError:
        raise Exception(
            "No rows for specie-code " + str(resource.get("specie-code")) + " in " + file_name +
            " (or the archive is corrupt): " + str(filterError))
    stderr.write("  * PROCESSING FILE...\n")

    #Count the number of genes (for percentage)
    total_lines = int(check_output(['wc', '-l', "/tmp/build.tmp"]).decode('utf-8').split()[0])

    #Register the databases
    refseq_rna_predicted_db_id = insertDatabase(DBNAME_Entry("refseq_rna_predicted", "RefSeq RNA nucleotide accession (predicted)", "Identifier"))
    refseq_rna_curated_db_id = insertDatabase(DBNAME_Entry("refseq_rna_curated", "RefSeq RNA nucleotide accession (curated)", "Identifier"))
    refseq_peptide_predicted_db_id = insertDatabase(DBNAME_Entry("refseq_peptide_predicted", "RefSeq protein accession (predicted)", "Identifier"))
    refseq_peptide_curated_db_id = insertDatabase(DBNAME_Entry("refseq_peptide_curated", "RefSeq protein accession (curated)", "Identifier"))
    entrezgene_db_id = insertDatabase(DBNAME_Entry("entrezgene", "EntrezGene ID", "Identifier"))

    #Add the entries for tables
    with open("/tmp/build.tmp", "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                entrez_tax = row[0]

                if entrez_tax != str(resource.get("specie-code")):
                    continue

                entrez_gi = row[1]
                rna_acc   = row[3]
                prot_acc  = row[5]

                if rna_acc == "-":
                    raise Exception("Empty REFSEQ transcript value.")

                if rna_acc[0].lower() == "x": #predicted
                    rna_acc = insertXREF(XREF_Entry(rna_acc, refseq_rna_predicted_db_id, resource.get("description")))
                else:
                    rna_acc = insertXREF(XREF_Entry(rna_acc, refseq_rna_curated_db_id, resource.get("description")))
                insertTR_XREF(rna_acc, rna_acc)

                if entrez_gi != "-": #ALWAYS TRUE
                    entrez_gi = insertXREF(XREF_Entry(entrez_gi, entrezgene_db_id, resource.get("description")))
                    insertTR_XREF(entrez_gi, rna_acc)

                if prot_acc != "-":
                    if prot_acc[0].lower() == "x": #predicted
                        internalID = insertXREF(XREF_Entry(prot_acc, refseq_peptide_predicted_db_id, resource.get("description")))
                    else:
                        internalID = insertXREF(XREF_Entry(prot_acc, refseq_peptide_curated_db_id, resource.get("description")))

                    insertTR_XREF(internalID, rna_acc)

            except Exception as ex:
                errorMessage= "FAILED WHILE PROCESSING REFSEQ MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES["REFSEQ"].append([errorMessage] + row)

    csvfile.close()
    os.remove("/tmp/build.tmp")

    TOTAL_FEATURES["REFSEQ"]=total_lines

    return total_lines

def processRefSeqGeneSymbolData():
    """
    # REFSEQ MAPPING FILE CONTAINS THE LOT OF COLUMNS
    # 1. tax_id
    # 2. Entrez ID
    # 3. Symbol
    # 4. ...
    # 5. Synonyms
    # ...
    """
    FAILED_LINES["REFSEQ GENE SYMBOL"]=[]

    # Processing statistics counters
    genes_processed = 0
    genes_successfully_mapped = 0
    genes_skipped_no_transcript = 0
    genes_skipped_empty_symbol = 0
    genes_failed_other = 0

    stderr.write("\n\nPROCESSING REFSEQ GENE SYMBOL MAPPING FILE...\n")

    #Get settings for the file
    resource = EXTERNAL_RESOURCES.get("refseq")[1]
    file_name= DATA_DIR + "mapping/" + resource.get("output")

    #Check if file exists
    if not haveInputFile("REFSEQ GENE SYMBOL", file_name,
                         "gene symbols and their synonyms will be absent -- users cannot upload by symbol"):
        return

    #Extract the file in a temporal directory
    stderr.write("  * EXTRACTING FILE...\n")
    # grep, not awk. Both filter the same single column, but awk parses every one of
    # gene2refseq's 95 million rows into fields while grep does an anchored byte match:
    # measured on the real 1,977 MB file, awk took 207.1s and grep 7.6s for byte-identical
    # output (same md5, same 239,667 rows). Decompression alone is 6.6s, so this step is
    # now I/O-bound rather than burning 200s of CPU -- per organism.
    # `set -o pipefail` matters here: without it a corrupt archive makes gunzip exit
    # non-zero while grep still exits 0, and check_call sees success on a truncated file.
    # grep exiting 1 (no rows matched) is also a real failure -- it means the taxid does
    # not occur in this file at all, which used to produce an empty mapping silently.
    command = ("set -o pipefail; gunzip -c " + file_name +
               " | LC_ALL=C grep '^" + str(resource.get("specie-code")) + "\t' > /tmp/build.tmp")
    try:
        check_call(command, shell=True, executable="/bin/bash")
    except CalledProcessError as filterError:
        raise Exception(
            "No rows for specie-code " + str(resource.get("specie-code")) + " in " + file_name +
            " (or the archive is corrupt): " + str(filterError))
    stderr.write("  * PROCESSING FILE...\n")

    #Count the number of genes (for percentage)
    total_lines = int(check_output(['wc', '-l', "/tmp/build.tmp"]).decode('utf-8').split()[0])

    #Register the databases
    refseq_gene_symbol_db_id = insertDatabase(DBNAME_Entry("refseq_gene_symbol", "RefSeq Gene Symbol", "Identifier"))
    refseq_gene_symbol_synonyms_db_id = insertDatabase(DBNAME_Entry("refseq_gene_symbol_synonyms", "RefSeq Gene Symbol Synonyms", "Identifier"))
    entrezgene_db_id = insertDatabase(DBNAME_Entry("entrezgene", "EntrezGene ID", "Identifier"))

    #Add the entries for tables
    with open("/tmp/build.tmp", "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                #CHECK IF THE SPECIE CODE IS VALID
                entrez_tax = row[0]
                if entrez_tax != str(resource.get("specie-code")):
                    continue

                #READ THE VALUES
                entrez_gi    = row[1]
                gene_symbol  = row[2]
                synonyms     = row[4]

                genes_processed += 1

                # Skip genes with empty symbols
                if gene_symbol == "-":
                    genes_skipped_empty_symbol += 1
                    continue

                #CHECK ENTREZ ID WAS PREVIOSLY REGISTERED
                # If not found, this gene lacks RefSeq transcripts - skip silently
                entrez_gi = findXREF(entrez_gi, entrezgene_db_id)
                if entrez_gi == None:
                    genes_skipped_no_transcript += 1
                    continue

                #GET THE SYNONYMS IN A LIST(IF ANY)
                if synonyms == "-":
                    gene_symbols=[]
                else:
                    gene_symbols = synonyms.split("|")

                #ADD A NEW ENTRY FOR EACH GENE SYMBOL
                gene_symbol = insertXREF(XREF_Entry(gene_symbol, refseq_gene_symbol_db_id, resource.get("description")))

                aux=[gene_symbol]
                for gene_symbol in gene_symbols:
                    gene_symbol = insertXREF(XREF_Entry(gene_symbol, refseq_gene_symbol_synonyms_db_id, resource.get("description")))
                    aux.append(gene_symbol)

                gene_symbols=aux

                #GET ALL THE TRANSCRIPT IDS ASSOCIATED WITH THE ENTREZ ID
                transcript_ids = xref2transcript['global'].get(entrez_gi.getID(), [])

                #IF NO TRANSCRIPTS REMOVE THE ENTRIES AND FAIL (SHOULD NOT HAPPEN)
                if len(transcript_ids) == 0:
                    for gene_symbol in gene_symbols:
                        deleteXREF(gene_symbol)
                    raise Exception("No transcript ID associated for current ENTREZ ID.")

                #IF THERE IS AT LEAST ONE TRANSCRIPT, CREATE THE ASSOCIATIONS
                for transcript_id in transcript_ids:
                    for gene_symbol in gene_symbols:
                        insertTR_XREF(gene_symbol, transcript_id)

                # Successfully processed this gene
                genes_successfully_mapped += 1

            except Exception as ex:
                genes_failed_other += 1
                errorMessage= "FAILED WHILE PROCESSING REFSEQ GENE SYMBOL MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES["REFSEQ GENE SYMBOL"].append([errorMessage] + row)

    csvfile.close()
    os.remove("/tmp/build.tmp")

    # Print processing summary
    stderr.write("\n" + "="*80 + "\n")
    stderr.write("RefSeq Gene Symbol Processing Summary:\n")
    stderr.write("="*80 + "\n")
    stderr.write("  Total genes in file: {}\n".format(total_lines))
    stderr.write("  Genes processed: {}\n".format(genes_processed))
    stderr.write("    - Successfully mapped: {} ({:.1f}%)\n".format(
        genes_successfully_mapped,
        100.0 * genes_successfully_mapped / genes_processed if genes_processed > 0 else 0))
    stderr.write("    - Skipped (no RefSeq transcript): {} ({:.1f}%)\n".format(
        genes_skipped_no_transcript,
        100.0 * genes_skipped_no_transcript / genes_processed if genes_processed > 0 else 0))
    stderr.write("    - Skipped (empty gene symbol): {} ({:.1f}%)\n".format(
        genes_skipped_empty_symbol,
        100.0 * genes_skipped_empty_symbol / genes_processed if genes_processed > 0 else 0))
    stderr.write("    - Failed (other errors): {} ({:.1f}%)\n".format(
        genes_failed_other,
        100.0 * genes_failed_other / genes_processed if genes_processed > 0 else 0))
    stderr.write("\n")
    stderr.write("  NOTE: Genes without RefSeq transcripts are expected and normal.\n")
    stderr.write("  They are not included in the database because PaintOmics requires\n")
    stderr.write("  transcript-level data for pathway analysis.\n")
    stderr.write("="*80 + "\n\n")

    TOTAL_FEATURES["REFSEQ GENE SYMBOL"]=total_lines

    return total_lines


#**************************************************************************
#  _    _ _   _ _____ _____  _____   ____ _______
# | |  | | \ | |_   _|  __ \|  __ \ / __ \__   __|
# | |  | |  \| | | | | |__) | |__) | |  | | | |
# | |  | | . ` | | | |  ___/|  _  /| |  | | | |
# | |__| | |\  |_| |_| |    | | \ \| |__| | | |
#  \____/|_| \_|_____|_|    |_|  \_\\____/  |_|
#
#**************************************************************************
def processUniProtData():
    """
    # UNIPROT MAPPING FILE CONTAINS THE LOT OF COLUMNS
    # 1. UniProtKB-AC
    # 2. UniProtKB-ID
    # 3. GeneID (EntrezGene)
    # 4. RefSeq (Peptide)
    # 5. GI
    # 6. PDB
    # ...
    # 12. PIR
    # ...
    # 15. UniGene
    # ...
    # 19. Ensembl
    # 20. Ensembl_TRS
    # 21. Ensembl_PRO
    """
    FAILED_LINES["UNIPROT"]=[]
    stderr.write("\n\nPROCESSING UniProt MAPPING FILE...\n")

    resource = EXTERNAL_RESOURCES.get("uniprot")[0]
    file_name= DATA_DIR + "mapping/" + resource.get("output")
    if not haveInputFile("UNIPROT", file_name,
                         "UniProt accessions and identifiers will be absent for this species"):
        return

    #Extract the file in a temporal directory
    stderr.write("  * EXTRACTING FILE...\n")
    command = "gunzip -c  " + file_name + " > /tmp/build.tmp"
    check_call(command, shell=True)

    stderr.write("  * PROCESSING FILE...\n")
    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', '/tmp/build.tmp']).decode('utf-8').split()[0])

    #Register databases and get the assigned IDs
    uniprot_acc_db_id = insertDatabase(DBNAME_Entry("uniprot_acc", "UniProt Accession", "Identifier"))
    uniprot_id_db_id = insertDatabase(DBNAME_Entry("uniprot_id", "UniProt Identifier", "Identifier"))
    ensembl_transcript_db_id = insertDatabase(DBNAME_Entry("ensembl_transcript", "Ensembl transcript", "Identifier"))
    refseq_peptide_predicted_db_id = insertDatabase(DBNAME_Entry("refseq_peptide_predicted", "RefSeq protein accession (predicted)", "Identifier"))
    refseq_peptide_curated_db_id = insertDatabase(DBNAME_Entry("refseq_peptide_curated", "RefSeq protein accession (curated)", "Identifier"))

    #Process files
    with open('/tmp/build.tmp', "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""
        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            errorMessage=""
            try:
                uniprot_acc = row[0]
                uniprot_id  = row[1]
                ref_prot_acc= row[3]
                ensembl_ti  = row[19]

                if ensembl_ti == "" and ref_prot_acc == "":
                    raise Exception("Empty UniProt transcript information.")

                uniprot_acc = insertXREF(XREF_Entry(uniprot_acc, uniprot_acc_db_id, resource.get("description")))
                uniprot_id  = insertXREF(XREF_Entry(uniprot_id, uniprot_id_db_id, resource.get("description")))

                transcript_ids = []
                if ensembl_ti != "":
                    ensembl_ti = ensembl_ti.replace(" ", "").split(";")
                    #FOR EACH TRANSCRIPT
                    for transcript_id in ensembl_ti:
                        #GET THE ID FOR THE ENTRY
                        transcript_id = findXREF(transcript_id, ensembl_transcript_db_id)
                        #IF THE TRANSCRIPT EXISTS
                        if transcript_id != None:
                            transcript_ids.append(transcript_id.getID())

                if ref_prot_acc != "":
                    ref_prot_acc = ref_prot_acc.replace(" ", "").split(";")
                    for peptide_acc in ref_prot_acc:
                        #GET THE ID FOR THE ENTRY
                        peptide_id = findXREF(peptide_acc, refseq_peptide_predicted_db_id)
                        if peptide_id == None:
                            peptide_id = findXREF(peptide_acc, refseq_peptide_curated_db_id)

                        if peptide_id != None:
                            # GET THE ASSOCIATED TRANSCRIPT ID FOR CURRENT PEPTIDE
                            transcript_ids += xref2transcript['global'].get(peptide_id.getID(), [])

                #IF NO TRANSCRIPTS REMOVE THE ENTRIES AND FAIL (SHOULD NOT HAPPEN)
                if len(transcript_ids) == 0:
                    deleteXREF(uniprot_id)
                    deleteXREF(uniprot_acc)
                    raise Exception("UniProt transcripts are not in Database (possible retired transcripts)")

                #IF THERE IS AT LEAST ONE TRANSCRIPT, CREATE THE ASSOCIATIONS
                for transcript_id in transcript_ids:
                    insertTR_XREF(uniprot_id, transcript_id)
                    insertTR_XREF(uniprot_acc, transcript_id)
            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING UNIPROT MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES["UNIPROT"].append([errorMessage] + row)

    csvfile.close()
    os.remove("/tmp/build.tmp")

    TOTAL_FEATURES["UNIPROT"]=total_lines

    return total_lines

#**************************************************************************
# __      ________ _____
# \ \    / /  ____/ ____|   /\
#  \ \  / /| |__ | |  __   /  \
#   \ \/ / |  __|| | |_ | / /\ \
#    \  /  | |___| |__| |/ ____ \
#     \/   |______\_____/_/    \_\
#
#**************************************************************************
def processVegaData():
    """
    #
    # ENSEMBL MAPPING FILE CONTAINS THE FOLLOWING COLUMNS
    # 1. Vega Gene ID
    # 2. EntrezGene ID
    # 3. Vega Protein ID
    # 4. Vega Transcript ID
    # 5. Ensembl Transcript ID
    """
    FAILED_LINES["VEGA"]=[]
    resource = EXTERNAL_RESOURCES.get("vega")[0]
    file_name= DATA_DIR + "mapping/" + resource.get("output")
    if not haveInputFile("VEGA", file_name,
                         "VEGA identifiers will be absent for this species"):
        return

    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    #Register databases and get the assigned IDs
    ensembl_transcript_db_id = insertDatabase(DBNAME_Entry("ensembl_transcript", "Ensembl transcript", "Identifier"))
    vega_transcript_db_id = insertDatabase(DBNAME_Entry("vega_transcript", "Vega transcript", "Identifier"))
    vega_gene_db_id = insertDatabase(DBNAME_Entry("vega_gene", "Vega gene", "Identifier"))
    vega_peptide_db_id = insertDatabase(DBNAME_Entry("vega_peptide", "Vega protein", "Identifier"))
    entrezgene_db_id = insertDatabase(DBNAME_Entry("entrezgene", "EntrezGene ID", "Identifier"))

    #Process files
    stderr.write("\n\nPROCESSING ENSEMBL Vega MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter=',')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                vega_gi = row[0]
                entrez_gi = row[1]
                vega_pi = row[2]
                vega_ti = row[3]
                ensembl_ti = row[4]

                if ensembl_ti == "": #ALWAYS FALSE
                    raise Exception("Empty ENSEMBL Vega transcript value.")

                ensembl_ti = insertXREF(XREF_Entry(ensembl_ti, ensembl_transcript_db_id, resource.get("description")))
                #TODO: IF TI NOT IN DB?

                if vega_ti != "": #ALWAYS TRUE
                    vega_ti = insertXREF(XREF_Entry(vega_ti, vega_transcript_db_id, resource.get("description")))
                    insertTR_XREF(vega_ti, ensembl_ti)

                if vega_gi != "": #ALWAYS TRUE
                    vega_gi = insertXREF(XREF_Entry(vega_gi, vega_gene_db_id, resource.get("description")))
                    insertTR_XREF(vega_gi, ensembl_ti)

                if entrez_gi != "":
                    entrez_gi = insertXREF(XREF_Entry(entrez_gi, entrezgene_db_id, resource.get("description")))
                    insertTR_XREF(entrez_gi, ensembl_ti)

                if vega_pi != "":
                    vega_pi = insertXREF(XREF_Entry(vega_pi, vega_peptide_db_id, resource.get("description")))
                    insertTR_XREF(vega_pi, ensembl_ti)

            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING ENSEMBL VEGA MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES["VEGA"].append([errorMessage] + row)
    csvfile.close()

    TOTAL_FEATURES["VEGA"]=total_lines

    return total_lines


def processMapManMappingData():
    global external_mapping, kegg_id_2_refseq_tid

    # TODO: split into different types of IDs? Seems like potato can have at least two (PGSC and ITAG)
    # keep it simple just to try.
    #STEP 1. Register databases and get the assigned IDs
    mapman_gene_db_id = insertDatabase(DBNAME_Entry("mapman_gene_id", "MapMan Gene identifier", "Identifier"))
    kegg_id_db_id = insertDatabase(DBNAME_Entry("kegg_id", "KEGG Feature ID", "Identifier"))
    # mapman_id_db_id = insertDatabase(DBNAME_Entry("mapman_id", "Mapman Feature ID", "Identifier"))

    # XREF descriptions
    mapman_gene_desc = "Extracted from MapMan Database"
    # mapman_feature_desc = "Extracted from Mapman Database (GENE 2 MAPMAN file)"
    ncbi_kegg_desc = "Extracted from KEGG Database (NCBI Gene ID 2 KEGG  file)"

    #STEP 2. READ THE UniProt 2 KEGG FILE but DO NOT process it as it will be done in "processKEGGMappingData
    # Instead, load the contents and keep as a link table to KEGG gene ids
    #
    # This file only feeds the mapman_kegg cross-link below, which is itself optional
    # (see mapman_kegg_file_name). An unconditional `return` here used to end the whole
    # function whenever KEGG has no ncbi-geneid conversion for a species (HTTP 400,
    # e.g. bvu) -- discarding the species' own gene-to-bin MapMan data too, even though
    # nothing downstream needed this file for it. Missing it now only means the
    # cross-link loop finds no matches, which it already handles: every gene falls
    # through to the standalone-transcript fallback below instead.
    ncbi_file_name= DATA_DIR + "mapping/" + "ncbi-geneid2kegg.list"
    haveNcbiMapping = haveInputFile("NCBI GENE ID 2 KEGG", ncbi_file_name,
                         "NCBI gene ids will not be linked to KEGG features for this species")

    # MapMan input files. Prefer the canonical DATA_DIR/mapping/<output> location
    # (populated by the download step) and fall back to the configured `url + file`
    # for legacy installs that keep MapMan source files outside DATA_DIR.
    # Returns None rather than exiting: the three MapMan inputs are independent, and
    # only the gene-to-bin file is load-bearing. Losing the other two costs metabolites
    # or the KEGG cross-link, which is a warning, not a reason to drop the organism.
    def _resolveMapManPath(resource, displayName, consequence):
        if resource is None:
            skipSource(displayName, "no such resource declared for " + str(SPECIE), consequence)
            return None
        candidate = DATA_DIR + "mapping/" + resource.get("output")
        if os.path.isfile(candidate):
            return candidate
        fallback = resource.get("url") + resource.get("file")
        if os.path.isfile(fallback):
            return fallback
        skipSource(displayName, "not found in either " + candidate + " or " + fallback, consequence)
        return None

    def _firstResource(name):
        entries = EXTERNAL_RESOURCES.get(name)
        return entries[0] if entries else None

    mapman_cpd_file_name = _resolveMapManPath(
        _firstResource("metabolites"), "MAPMAN METABOLITES",
        "MapMan metabolite identifiers will be absent; genes are unaffected")

    mapman_file_name = _resolveMapManPath(
        _firstResource("mapman_gene"), "MAPMAN GENE 2 BIN",
        "no MapMan bins can be assigned, so MapMan diagrams will carry no features")

    mapman_kegg_file_name = _resolveMapManPath(
        _firstResource("mapman_kegg"), "MAPMAN GENE 2 KEGG",
        "MapMan genes will not be cross-linked to KEGG ids")

    # The gene-to-bin file IS the MapMan mapping. Without it there is nothing to build,
    # so stop here and let the species install with its KEGG data only.
    if mapman_file_name is None:
        return

    # Insert compounds
    if mapman_cpd_file_name is not None:
        processMapMan2CompoundSymbolMappingData(mapman_cpd_file_name)

    # Initialize the mapping_dict NCBI Gene ID => [many possible KEGG_IDs]
    ncbi_mapping_dict = defaultdict(list)

    if haveNcbiMapping:
        with open(ncbi_file_name, 'r') as mapping_file:
            dict_reader = csv.DictReader(mapping_file, fieldnames=["ncbi_gene", "kegg_id"], delimiter="\t")
            for mapping_entry in dict_reader:
                # Remove prefix
                ncbi_mapping_dict[mapping_entry["ncbi_gene"].replace("ncbi-geneid:", "")] += [mapping_entry["kegg_id"].replace(SPECIE + ":", "")]

    # Initialize the mapping dict MapmMan Gene => [many possible MapMan feature IDs]
    external_mapping = defaultdict(list)

    with open(mapman_file_name, 'r') as mapping_file:
        dict_reader = csv.DictReader(mapping_file, fieldnames=["mapman_gene", "version", "mapman_ids"], delimiter="\t")
        for mapping_entry in dict_reader:
            # Each gene might be associated to multiple terms
            ontology_terms = mapping_entry["mapman_ids"].split()

            # Prepare the dictionary in the form <Term>: [<list of genes>]
            for ontology_term in ontology_terms:
                external_mapping[ontology_term].extend([mapping_entry["mapman_gene"]])

    total_lines = 0
    genes_present = set()

    # Only the cross-link to KEGG needs this file. Without it every MapMan gene still
    # gets inserted below, just with its own transcript instead of a shared KEGG one.
    if mapman_kegg_file_name is not None:
      total_lines = int(check_output(['wc', '-l', mapman_kegg_file_name]).decode('utf-8').split()[0])

      with open(mapman_kegg_file_name, 'r') as mapman_file:
        i = 0
        prev = -1
        errorMessage = ""
        file_reader = csv.reader(mapman_file, delimiter="\t")

        for mapping_entry in file_reader:
            i += 1

            ncbi_gene_id = mapping_entry[1]
            gene_id = mapping_entry[0]

            prev = showPercentage(i, total_lines, prev, errorMessage)

            # If the gene is present in the mapping dict, we use the KEGG ID transcript id to link
            # each with one another, otherwise we leave the gene to be linked with "itself" later
            # in the dump process.
            try:
                external_gi = insertXREF(XREF_Entry(gene_id, mapman_gene_db_id, mapman_gene_desc))

                # Keep track of the genes located in this file
                genes_present.add(gene_id)

                # Insert link with KEGG features (if available)
                if ncbi_gene_id in ncbi_mapping_dict:

                        mapped_kegg_ids = ncbi_mapping_dict[ncbi_gene_id]

                        # Add a reference for each one
                        for kegg_id in mapped_kegg_ids:
                            kegg_gi = insertXREF(XREF_Entry(kegg_id, kegg_id_db_id, ncbi_kegg_desc))

                            transcript_id = kegg_id_2_refseq_tid.get(kegg_gi,
                                                                 generateRandomID())  # Try to reuse the ids for random transcripts
                            kegg_id_2_refseq_tid[kegg_gi] = transcript_id

                            insertTR_XREF(external_gi, transcript_id)
                            insertTR_XREF(kegg_gi, transcript_id)

                # If no mates were linked, add a transcript with itself
                if not external_gi in xref2transcript['global']:
                    insertTR_XREF(external_gi, generateRandomID())
            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING " + mapman_kegg_file_name +" MAPPING FILE [line " + str(i) + "]: "+ str(ex)
                FAILED_LINES.setdefault("MAPMAN GENE 2 KEGG", []).append([errorMessage])

    # Every MapMan gene not already linked above gets inserted with its own transcript.
    # Dedented out of the `with` block on purpose: when the KEGG cross-link file is
    # absent this loop is what still populates the MapMan identifiers.
    all_genes = set(list(itertools.chain.from_iterable(external_mapping.values())))

    for remaining_gene in all_genes.difference(genes_present):
        gene_gi = insertXREF(XREF_Entry(remaining_gene, mapman_gene_db_id, mapman_gene_desc))

        insertTR_XREF(gene_gi, generateRandomID())

    version = open(DATA_DIR + 'MAPMAN_MAPPING', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M"))
    version.close()

    return total_lines


#**************************************************************************
#  _  ________ _____  _____
# | |/ /  ____/ ____|/ ____|
# | ' /| |__ | |  __| |  __
# |  < |  __|| | |_ | | |_ |
# | . \| |___| |__| | |__| |
# |_|\_\______\_____|\_____|
#
#**************************************************************************
def processKEGGMappingData():
    """
    # KEGG MAPPING FILE CONTAINS THE FOLLOWING COLUMNS
    # WE USE THIS FUNCTION WHEN NO EXTERNAL DATA IS AVAILABLE
    # 1. EXTERNAL DB ID (uniprot acc, entrezid,...)
    # 2. KEGG DB ID (entrezid, Ensembl id...)
    """
    #STEP 1. Register databases and get the assigned IDs
    random_transcript_db_id = insertDatabase(DBNAME_Entry("random_transcript_db_id", "Random transcripts (not real)", "Identifier"))
    uniprot_acc_db_id = insertDatabase(DBNAME_Entry("uniprot_acc", "UniProt Accession", "Identifier"))
    ncbi_geneid_db_id = insertDatabase(DBNAME_Entry("ncbi_geneid", "NCBI Gene ID", "Identifier"))
    # ncbi_gi_db_id = insertDatabase(DBNAME_Entry("ncbi_gi", "NCBI GI ID", "Identifier"))
    kegg_id_db_id = insertDatabase(DBNAME_Entry("kegg_id", "KEGG Feature ID", "Identifier"))
    kegg_gene_symbol_db_id = insertDatabase(DBNAME_Entry("kegg_gene_symbol", "KEGG Gene Symbol", "Identifier"))
    kegg_gene_symbol_synonyms_db_id = insertDatabase(DBNAME_Entry("kegg_gene_symbol_synonyms", "KEGG Gene Symbol Synonyms", "Identifier"))

    total_lines=[0,0,0]

    #STEP 2. READ THE UniProt 2 KEGG FILE
    file_name= DATA_DIR + "mapping/" + "uniprot2kegg.list"
    if not os.path.isfile(file_name):
        stderr.write("\n\nUnable to find the UNIPROT 2 KEGG MAPPING file: " + file_name + "\n")
    else:
        processKEGGMappingDataAUX("UNIPROT 2 KEGG", file_name, uniprot_acc_db_id, kegg_id_db_id, random_transcript_db_id, "up:")

    #STEP 3. READ THE NCBI Gene ID 2 KEGG FILE
    file_name= DATA_DIR + "mapping/" + "ncbi-geneid2kegg.list"
    if not os.path.isfile(file_name):
        stderr.write("\n\nUnable to find the NCBI Gene ID 2 KEGG MAPPING file: " + file_name + "\n")
    else:
        processKEGGMappingDataAUX("NCBI Gene ID 2 KEGG", file_name, ncbi_geneid_db_id, kegg_id_db_id, random_transcript_db_id, "ncbi-geneid:")

    #STEP 4. READ THE NCBI GI 2 KEGG FILE
    # file_name= DATA_DIR + "mapping/" + "ncbi-gi2kegg.list"
    # if not os.path.isfile(file_name):
    #     stderr.write("\n\nUnable to find the NCBI GI 2 KEGG MAPPING file: " + file_name + "\n")
    # else:
    #     processKEGGMappingDataAUX("NCBI GI 2 KEGG", file_name, ncbi_gi_db_id, kegg_id_db_id, random_transcript_db_id, "ncbi-gi:")

    #STEP 4. READ THE KEGG 2 GENE SYMBOL FILE
    file_name= DATA_DIR + "mapping/" + "kegg2genesymbol.list"
    if not os.path.isfile(file_name):
        stderr.write("\n\nUnable to find the KEGG 2 GENE SYMBOL MAPPING file: " + file_name + "\n")
    else:
        processKEGG2GeneSymbolMappingData("KEGG 2 GENE SYMBOL", file_name, kegg_gene_symbol_db_id, kegg_gene_symbol_synonyms_db_id, kegg_id_db_id, random_transcript_db_id)

    kegg_id_2_refseq_tid.clear()

    return total_lines

def processKEGGMappingDataAUX(display_file_name, file_name, current_db_id, kegg_id_db_id, transcripts_db_id, prefix):
    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    #Process files
    stderr.write("\n\nPROCESSING " + display_file_name +" MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                external_gi  = row[0].replace(prefix, "")
                kegg_gi      = row[1].replace(SPECIE + ":", "")

                external_gi = insertXREF(XREF_Entry(external_gi, current_db_id, "Extracted from KEGG Database (" + display_file_name + " file)"))
                kegg_gi = insertXREF(XREF_Entry(kegg_gi, kegg_id_db_id, "Extracted from KEGG Database (" + display_file_name + " file)"))

                transcript_id= kegg_id_2_refseq_tid.get(kegg_gi, generateRandomID()) #Try to reuse the ids for random transcripts
                kegg_id_2_refseq_tid[kegg_gi] = transcript_id

                insertTR_XREF(external_gi, transcript_id)
                insertTR_XREF(kegg_gi, transcript_id)

            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING " + display_file_name +" MAPPING FILE [line " + str(i) + "]: "+ str(ex)
    csvfile.close()

    return total_lines

def processKEGG2GeneSymbolMappingData(display_file_name, file_name, kegg_gene_symbol_db_id, kegg_gene_symbol_synonyms_db_id, kegg_id_db_id, transcripts_db_id):
    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    #Process files
    stderr.write("\n\nPROCESSING " + display_file_name +" MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0
        prev=-1
        errorMessage=""

        for row in rows:
            i+=1
            prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                kegg_gi      = row[0].replace(SPECIE + ":", "")
                # Handle both old 2-column format (ID, SYMBOL;description) and new 4-column format (ID, TYPE, LOCATION, SYMBOL;description)
                gene_symbol_raw  = row[3] if len(row) >= 4 else row[1]

                # Always insert the KEGG ID regardless of gene symbol presence
                kegg_gi = insertXREF(XREF_Entry(kegg_gi, kegg_id_db_id, "Extracted from KEGG Database (" + display_file_name + " file)"))

                transcript_id= kegg_id_2_refseq_tid.get(kegg_gi, generateRandomID()) #Try to reuse the ids for random transcripts
                kegg_id_2_refseq_tid[kegg_gi] = transcript_id
                insertTR_XREF(kegg_gi, transcript_id)

                # Process gene symbol if it exists (format: "symbol;description" or just "description")
                gene_symbol = gene_symbol_raw.split(";")
                if len(gene_symbol) < 2: #it means that the line only contains a description, not a gene symbol
                    continue  # Skip gene symbol processing but KEGG ID is already inserted

                gene_synonyms = gene_symbol[0].split(", ") #COULD BE A LIST OF GENE SYMBOLS
                gene_symbol   = gene_synonyms.pop(0)

                if len(gene_symbol) > 15: #discard long ids -> could be a description
                    continue  # Skip gene symbol processing but KEGG ID is already inserted

                gene_symbol = insertXREF(XREF_Entry(gene_symbol, kegg_gene_symbol_db_id, "Extracted from KEGG Database (" + display_file_name + " file)"))

                gene_synonyms_aux=[gene_symbol]
                for gene_synonym in gene_synonyms:
                    gene_synonyms_aux.append(insertXREF(XREF_Entry(gene_synonym, kegg_gene_symbol_synonyms_db_id, "Extracted from KEGG Database (" + display_file_name + " file)")))

                # Link gene symbols to the same transcript
                for gene_symbol in gene_synonyms_aux:
                    insertTR_XREF(gene_symbol, transcript_id)

            except Exception as ex:
                errorMessage = "FAILED WHILE PROCESSING " + display_file_name +" MAPPING FILE [line " + str(i) + "]: "+ str(ex)
    csvfile.close()

    return total_lines

def processKEGGCommonData(dirName, ROOT_DIRECTORY):
    #STEP1. PROCESS KEGG COMPOUND DATA
    processKEGG2CompoundSymbolMappingData(dirName + "compounds_all.list")

    #STEP2. PROCESS ALL SPECIES FILES
    stderr.write("\nPROCESSING AVAILABLE SPECIES FILE...\n")
    file_name = dirName + "organisms_all.list"

    SPECIES_AUX={}
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        for row in rows:
            SPECIES_AUX[row[2]] = {"value": row[1], "name": row[2]}
    csvfile.close()

    SPECIES=[]
    for specie_name in sorted(SPECIES_AUX.keys()):
        SPECIES.append(SPECIES_AUX.get(specie_name))

    SPECIES = {"success": True, "species": SPECIES}
    csvfile = open(ROOT_DIRECTORY + "public_html/resources/data/all_species.json", 'w')
    csvfile.write(json.dumps(SPECIES, separators=(',',':')) + "\n")
    csvfile.close()

    stderr.write("\nPROCESSING VERSIONS FILE...\n")
    #STEP3. PROCESS VERSION FILE
    file_name= dirName + "/VERSION"
    file = open(file_name, 'r')
    ALL_VERSIONS["COMMON"] = {"name" : "COMMON", "date" : file.readline().rstrip().split("\t")[1]}
    file.close()

    stderr.write("\nDUMP FILES...\n")
    #STEP4. DUMP VERSION INFO
    file = open("/tmp/versions.tmp", 'w')
    for elem in ALL_VERSIONS.values():
        file.write(json.dumps(elem, separators=(',',':')) + "\n")
    file.close()

    #STEP4. CREATE DATABASES
    stderr.write("\nCREATING GLOBAL DATABASES...\n")
    createGlobalDatabase()

def processMapMan2CompoundSymbolMappingData(file_name):
    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])

    # The process will also insert information about compounds, thus discarding the previous database
    # and importing a new one, needing again the KEGG compounds.
    processKEGG2CompoundSymbolMappingData(DATA_DIR + "../common/compounds_all.list")

    #STEP 1. Process files
    stderr.write("\n\nPROCESSING Mapman 2 Compound MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0

        for row in rows:
            i+=1
            #prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                mapman_id      = row[0]
                compound_symbols  = row[1].split(".")[-1].split("|")

                for compound_symbol in compound_symbols:
                    # KEGG_COMPOUNDS.append({"id" : kegg_id, "name" : compound_symbol.lstrip()})
                    MAPMAN_COMPOUNDS[compound_symbol.lstrip()] = mapman_id
            except Exception:
                # A bad row is tolerated and skipped, same as it always was --
                # nothing ever read the message that used to be built here.
                pass
    csvfile.close()

    #STEP 2. DUMP THE TABLE INTO A FILE
    file = open("/tmp/compounds.tmp", 'a')
    for cpdName, cpdID in MAPMAN_COMPOUNDS.items():
        file.write(json.dumps({"id" : cpdID, "name" : cpdName}, separators=(',', ':')) + "\n")
    file.close()

    # Insert the compounds collection
    createCompoundsCollection()

    return total_lines

def processKEGG2CompoundSymbolMappingData(file_name):
    #Get line count (for percentage)
    total_lines = int(check_output(['wc', '-l', file_name]).decode('utf-8').split()[0])
    # KEGG_COMPOUNDS = []

    #STEP 1. Process files
    stderr.write("\n\nPROCESSING KEGG 2 Compound MAPPING FILE...\n")
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        i =0

        for row in rows:
            i+=1
            #prev = showPercentage(i, total_lines, prev, errorMessage)
            try:
                kegg_id      = row[0].replace("cpd:", "")
                compound_symbols  = row[1]

                # Ensure pure KEGG IDs (e.g. C00002) are searchable alongside textual compound names.
                compound_symbols = [kegg_id] + compound_symbols.split("; ")
                for compound_symbol in compound_symbols:
                    compound_symbol = compound_symbol.strip()
                    if compound_symbol == "":
                        continue
                    # A compound NAME is not unique across KEGG ids: "D-Fructose" is
                    # the primary name of C00095, C05003 and C10906 all at once. This
                    # used to be `KEGG_COMPOUNDS[symbol] = kegg_id`, so the last id in
                    # file order won and 96 (name, id) pairs were lost -- 65 of them
                    # with no surviving synonym, which is why an uploaded "D-Fructose"
                    # resolved to C10906 and every C00095 box in glycolysis stayed grey.
                    # Keep every id and emit one document per pair; the runtime already
                    # clones one feature per matched document
                    # (FeatureNamesToKeggIDsMapper.py:589-629), so nothing downstream
                    # has to change.
                    KEGG_COMPOUNDS.setdefault(compound_symbol, set()).add(kegg_id)
            except Exception:
                # A bad row is tolerated and skipped, same as it always was --
                # nothing ever read the message that used to be built here.
                pass
    csvfile.close()

    # STEP 1.5. Load CHEBI to KEGG mapping
    stderr.write("\nPROCESSING CHEBI TO KEGG MAPPING FILE...\n")
    kegg2chebi_file = os.path.dirname(file_name) + "/kegg2chebi.list"
    if os.path.exists(kegg2chebi_file):
        try:
            with open(kegg2chebi_file, "r") as chebi_file:
                chebi_rows = csv.reader(chebi_file, delimiter='\t')
                chebi_count = 0
                for row in chebi_rows:
                    try:
                        # File format: chebi:XXXXX\tcpd:CXXXXX
                        chebi_id = row[0].split(":")[1] if ":" in row[0] else row[0]
                        kegg_id = row[1].split(":")[1] if ":" in row[1] else row[1]

                        # Add mapping from CHEBI ID to KEGG compound ID
                        # Store both with and without "chebi:" prefix for flexibility
                        # One ChEBI id can map to several KEGG compounds too; keep them all.
                        KEGG_COMPOUNDS.setdefault("chebi:" + chebi_id, set()).add(kegg_id)
                        KEGG_COMPOUNDS.setdefault(chebi_id, set()).add(kegg_id)
                        chebi_count += 1
                    except Exception as ex:
                        stderr.write(f"\nWarning: Failed to process CHEBI mapping line: {row} - {str(ex)}\n")
                        continue
                stderr.write(f"\nLoaded {chebi_count} CHEBI to KEGG mappings.\n")
        except Exception as ex:
            stderr.write(f"\nWarning: Failed to load CHEBI mapping file: {str(ex)}\n")
    else:
        stderr.write(f"\nWarning: CHEBI mapping file not found at {kegg2chebi_file}\n")

    #STEP 2. DUMP THE TABLE INTO A FILE
    file = open("/tmp/compounds.tmp", 'w')
    # One document per (name, id) pair, not per name -- a name shared by several KEGG
    # compounds now yields one document each instead of silently keeping the last.
    for cpdName, cpdIDs in KEGG_COMPOUNDS.items():
        for cpdID in sorted(cpdIDs):
            file.write(json.dumps({"id" : cpdID, "name" : cpdName}, separators=(',', ':')) + "\n")
    file.close()

    return total_lines

def normaliseMapManBin(binCode):
    """Strip leading zeros from each segment of a MapMan bin code.

    MapMan diagram XML and MapMan mapping files disagree on how to spell the
    same ontology bin: the diagrams sometimes zero-pad a segment ("18.4.01",
    "17.8.1.1.02") while the mappings never do ("18.4.1"). Compared verbatim
    the two never match, which silently imports the affected diagrams with no
    features on them at all.

    Only fully numeric segments are touched, so anything unexpected is passed
    through untouched rather than mangled.
    """
    if not binCode:
        return binCode

    segments = binCode.split(".")

    for index, segment in enumerate(segments):
        # lstrip("0") on "0" or "000" would leave an empty string
        if segment.isdigit():
            segments[index] = segment.lstrip("0") or "0"

    return ".".join(segments)


def processMapManPathwaysData():
    from DBManager import generateThumbnail
    import xml.etree.ElementTree as XMLParser

    # Declare later used variables
    FAILED_LINES["MAPMAN PATHWAYS"] = []
    NODES = {}
    EDGES = []

    REVERSE_MAPMAN_CPD = {v: k for k, v in MAPMAN_COMPOUNDS.items()}

    if not len(external_mapping):
        skipSource("MAPMAN PATHWAYS", "no MapMan gene-to-bin mapping was loaded",
                   "MapMan diagrams are skipped entirely; KEGG pathways are unaffected")
        return

    # Check if classification file exists. Prefer DATA_DIR/mapping/<output>
    # (populated by the download step) and fall back to the configured
    # `url + file` for legacy installs.
    mapman_classification_resource = EXTERNAL_RESOURCES.get("mapman_classification")[0]
    _candidate = DATA_DIR + "mapping/" + mapman_classification_resource.get("output")
    if os.path.isfile(_candidate):
        mapman_classification_file_name = _candidate
    else:
        mapman_classification_file_name = mapman_classification_resource.get("url") + mapman_classification_resource.get("file")

    if not os.path.isfile(mapman_classification_file_name):
        skipSource("MAPMAN CLASSIFICATION",
                   "not found in either " + _candidate + " or " + mapman_classification_file_name,
                   "MapMan diagrams are skipped entirely; KEGG pathways are unaffected")
        return

    # MapMan pathways files are the same for each species, even the XML files.
    # The handful of species compatible with MapMan will specify to download the same dataset.
    # Here we override the data always.
    # TODO: modify DBManager.py and move the code to "downloadData"?
    MAPMAN_DIR = DATA_DIR + "../mapman"
    MAPMAN_XML = MAPMAN_DIR + "/xml/"

    # If the path already exists rename it
    if os.path.exists(MAPMAN_DIR):
        shutil.rmtree(MAPMAN_DIR + ".bak", ignore_errors=True)
        shutil.move(MAPMAN_DIR, MAPMAN_DIR + ".bak")
    else:
        os.makedirs(MAPMAN_DIR)

    mapman_pathways = EXTERNAL_RESOURCES.get("mapman_pathways")[0]
    # Prefer DATA_DIR/mapping/<output>, fall back to configured `url + file`.
    _candidate = DATA_DIR + "mapping/" + mapman_pathways.get("output")
    pathways_file_name = _candidate if os.path.isfile(_candidate) else (mapman_pathways.get("url") + mapman_pathways.get("file"))

    # Try to extract the archive on the final dir, if there is an error
    # rename the previous dir
    try:
        os.makedirs(MAPMAN_DIR)
        check_call(["tar", "xzvf", pathways_file_name, "-C", MAPMAN_DIR])
    except Exception as e:
        if os.path.exists(MAPMAN_DIR):
            shutil.move(MAPMAN_DIR + ".bak", MAPMAN_DIR)
        skipSource("MAPMAN PATHWAYS", "could not extract " + str(pathways_file_name) + ": " + str(e),
                   "MapMan diagrams are skipped entirely; KEGG pathways are unaffected")
        return

    # Make sure to create the thumbnail directory
    if not os.path.exists(os.path.dirname(MAPMAN_DIR + "/png/thumbnails/")):
        os.makedirs(MAPMAN_DIR + "/png/thumbnails/")

    # Process the clasiffication file
    classiffication_mapping_dict = defaultdict(list)

    with open(mapman_classification_file_name, 'r') as mapping_file:
        dict_reader = csv.DictReader(mapping_file, fieldnames=["primary", "secondary", "pathway"], delimiter="\t")
        for pathway_entry in dict_reader:
            # Remove prefix
            classiffication_mapping_dict[pathway_entry["pathway"].strip()] = ';'.join([pathway_entry["primary"].strip(), pathway_entry["secondary"].strip()])

    i = 0;
    prev = -1;
    errorMessage = "";
    xml_files = os.listdir(MAPMAN_XML)
    total_lines = len(xml_files)

    # Initialize classification counters
    mainClassificationIDs = {}
    secClassificationIDs = {}
    pathway2gene = defaultdict(set)
    gene2pathway = defaultdict(set)

    for xml_file in xml_files:
        i+=1
        prev = showPercentage(i, total_lines, prev, errorMessage)

        file_name = MAPMAN_XML + xml_file
        pathway_id = xml_file.replace(".xml", "")

        # Generate thumbnail
        png_file = MAPMAN_DIR + "/png/" + xml_file.replace(".xml", ".png")
        try:
            generateThumbnail(png_file)
        except Exception:
            print(png_file)

        # Classification (network) data
        classification = classiffication_mapping_dict.get(pathway_id, "Not classified;Unclassified")
        classification_terms = classification.split(";")

        mainClassification = classification_terms[0]
        secondClassification = classification_terms[1]

        # Primary classification
        if not mainClassification in mainClassificationIDs:
            mainClassificationIDs[mainClassification] = len(mainClassificationIDs) + 1
            NODES[str(mainClassificationIDs[mainClassification]) + "A"] = {"data": {"id": mainClassification.lower().replace(" ", "_"), "label": mainClassification, "is_classification": "A"}, "group": "nodes"}

        # Secondary classification
        if not secondClassification in secClassificationIDs:
            secClassificationIDs[mainClassification] = len(secClassificationIDs) + 1
            NODES[str(secClassificationIDs[mainClassification]) + "B"] = {"data": {"id": secondClassification.lower().replace(" ", "_"),
                                                                 "parent": mainClassification.lower().replace(" ", "_"),
                                                                 "label": secondClassification,
                                                                 "is_classification": "B"}, "group": "nodes"}


        # Append to the global pathways container
        ALL_PATHWAYS[pathway_id] = {"ID": pathway_id, "name": pathway_id, "genes": [],
                                    "compounds": [], "relatedPathways": [], "source": "MapMan", "featureDB": "mapman_id",
                                    "classification": classification}

        # Pathway node information
        NODES[pathway_id] = {"data": {"id": pathway_id, "label": pathway_id, "total_features": 0}, "group": "nodes"}
        NODES[pathway_id]["data"]["parent"] = mainClassification.lower().replace(" ","_"),


        try:
            pathwayInfoXML = XMLParser.parse(file_name)
            # Image element
            root = pathwayInfoXML.getroot()
            # FOR EACH NODE IN THE XML FILE (DataArea)
            for child in root:
                try:
                    # Somewhere in the future maybe MapMan would get compound support added,
                    # leave here the possibility of getting the type like in KEGG
                    # entryType = child.get("type")

                    entry = {
                        "id": "",
                        "x": int(child.get("x")),
                        "y": int(child.get("y")),
                        "width": int(child.get("width", 0)),
                        "height": int(child.get("height", 0)),
                        "title": child.get("title", None),
                        "blockFormat": child.get("blockFormat"),
                        "type": child.get("type"),
                        "visualizationType": child.get("visualizationType"),
                        "recursive": True
                    }

                    # Each DataArea has at least one 'Identifier' child
                    for featureID in child:
                        # and not already_added.has_key(featureID)
                        # Some diagrams zero-pad their bin codes ("18.4.01",
                        # "17.8.1.1.02") while no mapping ever does ("18.4.1"),
                        # so the two spellings of the same bin never matched and
                        # the affected diagrams imported empty. Normalise here;
                        # neither the gene mappings nor the metabolite mapping
                        # contain a padded segment, so this cannot collide.
                        id_terms = normaliseMapManBin(featureID.get("id"))

                        # Mapman does not have a way to tell if the node is a gene or compound,
                        # so we determine it by checking the presence of the id inside the compounds
                        # dict.
                        if id_terms in REVERSE_MAPMAN_CPD:
                            compound_linked = REVERSE_MAPMAN_CPD.get(id_terms)

                            entryAux = entry.copy()
                            entryAux["id"] = compound_linked
                            entryAux["recursive"] = featureID.get("recursive")

                            ALL_PATHWAYS[pathway_id]["compounds"].append(entryAux)

                        else:
                            # As opposed to KEGG, where there are multiple identifiers
                            # mapped to the same coordinates, MapMan refers to orthology terms
                            # instead of genes.
                            #
                            # We transform the term to its mapped genes and clone the same
                            # entry pointing to the same x & y coordinates to allow to work
                            # as a KEGG pathway.
                            #
                            # General terms should also include child terms. I.e. 20.1 should
                            # reference also 20.1.*.*
                            # re.escape keeps the dots literal - unescaped, "18.4"
                            # would also match a bin spelled "18X4".
                            pattern_search = re.compile(r"{0}(\.|\Z)".format(re.escape(id_terms)))

                            # external_mapping
                            genes_linked = set(list(itertools.chain.from_iterable([v for k, v in external_mapping.items() if pattern_search.search(k)])))

                            # Link pathway to genes for network construction
                            pathway2gene[pathway_id].update(genes_linked)

                            for gene_id in genes_linked:

                                # Link gene to pathway for network construction
                                gene2pathway[gene_id].add(pathway_id)

                                entryAux = entry.copy()
                                entryAux["id"] = gene_id
                                entryAux["recursive"] = featureID.get("recursive")

                                ALL_PATHWAYS[pathway_id]["genes"].append(entryAux)

                except Exception as ex:
                    errorMessage = "FAILED WHILE PROCESSING PATHWAY XML FILE [" + file_name + "]: " + str(ex)
                    FAILED_LINES["MAPMAN PATHWAYS"].append([errorMessage])

        except Exception as ex:
            errorMessage = "FAILED WHILE PROCESSING PATHWAY XML FILE [" + file_name + "]: " + str(ex)

    #***********************************************************************************
    #* GENERATE THE NETWORK FILE DATA FOR MAPMAN
    #***********************************************************************************

    #***********************************************************************************
    #* FIRST PROCESS THE FILE WITH ALL PATHWAYS AND GENERATE A DIAGONAL MATRIX
    #          mmu00100 -> [ mmu00101 = 0, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00102 -> [                             mmu00103 = 0,...]
    #***********************************************************************************
    all_pathways = sorted(NODES.keys())

    pathways_matrix = {}
    while len(all_pathways) > 0:
        current_path = all_pathways[0]
        del all_pathways[0]
        pathways_matrix[current_path] = dict(zip(all_pathways, [0]*len(all_pathways)))

    #***********************************************************************************
    #* PROCESS THE FILE WITH THE RELATION GENE ID -> PATHWAY ID AND FILL THE MATRIX
    #          WITH THE NUMBER OF SHARED GENES
    #          mmu00100 -> [ mmu00101 = 1, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 5, mmu00103 = 3,...]
    #          mmu00102 -> [                             mmu00103 =20,...]
    #***********************************************************************************
    previous_gene = ""
    associated_paths = set()

    for gene_id, pathway_ids in gene2pathway.items():
        for path_id in pathway_ids:
            if gene_id != previous_gene:
                associated_paths = sorted(associated_paths)
                while len(associated_paths) > 0:
                    current_path = associated_paths[0]
                    del associated_paths[0]
                    for other_path in associated_paths:
                        try:
                            pathways_matrix[current_path][other_path] += 1
                        except:
                            stderr.write("Pathways " + current_path + " or " + other_path + " not found in MapMan network values.\n")

                associated_paths = set([])

            associated_paths.add(path_id)
            previous_gene = gene_id

    # LAST PATHWAY
    associated_paths = sorted(associated_paths)
    while len(associated_paths) > 0:
        current_path = associated_paths[0]
        del associated_paths[0]
        for other_path in associated_paths:
            try:
                pathways_matrix[current_path][other_path] += 1
            except:
                try:
                    pathways_matrix[other_path][current_path] += 1
                except:
                    stderr.write("Pathways " + current_path + " or " + other_path + " not found in MapMan network values.\n")

    #***********************************************************************************
    #* THE SAME, FOR COMPOUNDS
    #          A MapMan diagram carries metabolites as well as genes, and two bins that
    #          use the same metabolite share a biological feature. The matrix counted
    #          genes only.
    #***********************************************************************************
    mapmanPathways = {pathwayID: pathway for pathwayID, pathway in ALL_PATHWAYS.items()
                      if pathway.get("source") == "MapMan"}
    accumulateSharedFeatures(pathways_matrix,
                             indexCompoundsByPathway(mapmanPathways))

    #***********************************************************************************
    #* GET THE NUMBER OF GENES FOR EACH PATHWAY
    #***********************************************************************************
    mapman_g2p_file = DATA_DIR + "gene2pathway_mapman.list"

    # Write a "gene2pathway_mapman.list" to be used for metagenes generation.
    with open(mapman_g2p_file, 'w') as mapman_gene2pathway:
        for path_id, gene_ids in pathway2gene.items():
            new_string = set([w for w in gene_ids if len( w ) == 20])
            NODES[path_id]["data"]["total_features"] = len(new_string)

            # Write one row for each gene and pathway
            mapman_gene2pathway.writelines(str(geneID) + "\t" + str(path_id) + "\n" for geneID in gene_ids)

    # A field of its own beside it: total_features has meant "genes" since
    # Paintomics 3 and is read by clusters.py and by any client that has not
    # reloaded, so redefining it would silently move their filter.
    for path_id, pathway in mapmanPathways.items():
        node = NODES.get(path_id)
        if node is not None:
            node["data"]["total_compounds"] = len(
                set(compound.get("id") for compound in pathway.get("compounds", [])
                    if compound.get("id")))


    #***********************************************************************************
    #* BULK THE MATRIX INTO JSON:
    #          FOR EACH PATHWAY ID AND FOR EACH POSITION WITH NON ZERO (SHARE AT LEAST 1 GENE), CREATE AN EDGE
    #***********************************************************************************
    already_linked_pathways={}
    for path_id, shared_genes in pathways_matrix.items():
        #First create the edges based on the links between networks (extracted from KGML files)
        if path_id in ALL_PATHWAYS:
            relatedPathways = ALL_PATHWAYS[path_id]["relatedPathways"]
            for other_path_id in relatedPathways:
                if not path_id + "-" + other_path_id["id"] in already_linked_pathways:
                    EDGES.append({"data": {"id": path_id + "-" + other_path_id["id"], "source": path_id, "target": other_path_id["id"], "weight": 1, "class": 'l'}, "group":"edges"})
                    #Avoid repeated edges (including the opposite links)
                    already_linked_pathways[path_id + "-" + other_path_id["id"]] = 1
                    already_linked_pathways[other_path_id["id"]+ "-" + path_id] = 1
        #Add the edges based on the existance of shared genes
        for other_path_id, n_shared_genes in shared_genes.items():
            if n_shared_genes > 0:
                EDGES.append({"data": {"id": path_id + "-" + other_path_id, "source": path_id, "target": other_path_id, "weight": n_shared_genes, "class": 's'}, "group":"edges"})

    #***********************************************************************************
    #* SAVE THE NETWORK TO A FILE
    #***********************************************************************************
    network = {
        "nodes": list(NODES.values()),
        "edges": EDGES
    }
    csvfile = open(DATA_DIR + "pathways_network_MapMan.json", 'w')
    csvfile.write(json.dumps(network, separators=(',',':')) + "\n")
    csvfile.close()

    TOTAL_FEATURES["MAPMAN PATHWAYS"]=total_lines

    #***********************************************************************************
    #* PROCESS THE VERSION FILES
    #***********************************************************************************
    version = open(DATA_DIR + 'MAPMAN_VERSION', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M"))
    version.close()

    ALL_VERSIONS["MAPMAN"] = {"name" : "MAPMAN", "date" : strftime("%Y%m%d %H%M")}
    ALL_VERSIONS["MAPMAN_MAPPING"] = {"name": "MAPMAN_MAPPING", "date": strftime("%Y%m%d %H%M")}
    #
    # file_name= DATA_DIR + "mapping/MAP_VERSION"
    # file = open(file_name, 'r')
    #
    # file.close()

def buildReactomeHierarchyEdges(installedPathways, relationFile, speciesMarker,
                                maxCombinedDepth=3, maxGroupSize=60):
    """
    Derive the "linked biological processes" relation for Reactome from its
    pathway hierarchy.

    Why this exists
    ---------------
    A KEGG pathway map draws boxes that point at other KEGG maps, and those
    cross-references are what the network's default edge type ("Linked
    biological processes") is built from - 1,903 edges over 584 mmu nodes.
    Reactome was given the same treatment: an edge wherever one pathway's
    diagram embeds another pathway as a process node. That is the right
    analogue, but it collects almost nothing here, because of what the
    downloader chooses to install.

    downloadReactome walks `ReactomePathwayLow - ReactomePathwayHigh`, i.e. the
    *leaves* of the hierarchy (climbing to a parent only when a leaf has no
    diagram of its own). A leaf has no sub-pathways, so it embeds no process
    nodes, so it contributes no edges. Measured on mmu: 451 edges for 524
    pathway nodes, of which 52 pointed at pathways that were never installed,
    leaving 399 real ones covering 276 nodes. Half the network was isolated
    dots, and a typical job - which draws only its significant pathways -
    showed 11 nodes and 1 edge.

    What relates two Reactome leaves is not a diagram cross-reference; it is
    where they sit in the hierarchy. Reactome states that relation explicitly
    in ReactomePathwaysRelation - the same file the downloader already reads to
    decide what to fetch.

    The rule
    --------
    Two installed pathways are linked when they share an ancestor and the sum
    of their two distances up to it is at most `maxCombinedDepth`; an installed
    ancestor is linked to an installed descendant on the same budget. At the
    default of 3 that means siblings (1+1), and "one of the pair nested one
    level deeper than the other" (1+2).

    That extra level is not slack, it is the case the control exists for. Four
    of the eleven significant Reactome pathways in the job this was diagnosed
    on are extracellular-matrix processes, but only two of them are siblings:
    "Laminin interactions" hangs directly off "Extracellular matrix
    organization" while "Collagen chain trimerization" sits one step further
    down, under "Collagen formation". A siblings-only rule calls those
    unrelated. KEGG would have drawn all four inside a single map.

    Measured on mmu, per budget - pairs, nodes covered of 524, and the degree
    distribution, against KEGG's 1,903 pairs over 584 nodes at max 167 /
    median 6 / mean 8.7:

        <=2 (siblings only)  1,375   489/524   max  20, median  4, mean  5.6
        <=3 (default)        3,259   517/524   max  56, median 10, mean 12.6
        <=4                  6,786   523/524   max  89, median 22, mean 26.0

    3 is where the ECM case is recovered and the graph is still legible; 4
    connects almost everything to almost everything, which says nothing. (The
    <=4 row is the uncapped count - mmu's largest group is 57 within two levels
    but 90 within three, so `maxGroupSize` fires at that budget and brings it
    to 4,259. It does not fire at the default.)

    Groups are emitted as cliques rather than stars because the shared ancestor
    is usually not itself an installed node, so there is nothing to centre a
    star on. `maxGroupSize` bounds the quadratic: an ancestor with more
    installed descendants than that falls back to true siblings only, and says
    so on stderr rather than silently thinning the graph. mmu's largest group
    is 57, so it does not fire there - it exists because the group size is a
    property of the species' Reactome coverage, not of this code.

    :param installedPathways: set of pathway stIds that exist as network nodes.
        Pairs are only emitted between two members of this set, which is also
        what keeps the 52 dangling edges out.
    :param relationFile: path to ReactomePathwaysRelation.list (parent<TAB>child).
    :param speciesMarker: e.g. "R-MMU-". Matched as a prefix on both columns:
        the file holds every species at once, and a bare substring test picks up
        foreign identifiers.
    :returns: set of (a, b) tuples, a < b, so the caller can de-duplicate
        against edges it has already emitted in either direction.
    """
    edges = set()

    if not os.path.isfile(relationFile):
        stderr.write(
            "Reactome pathway relations not found at " + relationFile +
            "; the network will carry only the edges found in the diagrams.\n")
        return edges

    parentsOf = defaultdict(set)
    with open(relationFile) as handle:
        for row in handle:
            columns = row.rstrip("\n").split("\t")
            if len(columns) < 2:
                continue
            parent, child = columns[0], columns[1]
            if parent.startswith(speciesMarker) and child.startswith(speciesMarker):
                parentsOf[child].add(parent)

    # A pathway's partner is at least one step from the shared ancestor, so no
    # single member can usefully be further than maxCombinedDepth - 1.
    maxSingleDepth = max(1, maxCombinedDepth - 1)

    def ancestorsOf(pathway):
        """{ancestor: distance}, breadth-first, bounded. Reactome's relation
        graph is a DAG with diamonds - a pathway can be reached by more than
        one route - so `seen` keeps the first (shortest) distance and stops the
        walk from revisiting."""
        distances = {}
        frontier = {pathway}
        seen = {pathway}
        for distance in range(1, maxSingleDepth + 1):
            nextFrontier = set()
            for node in frontier:
                for parent in parentsOf[node]:
                    if parent not in seen:
                        seen.add(parent)
                        distances[parent] = distance
                        nextFrontier.add(parent)
            frontier = nextFrontier
            if not frontier:
                break
        return distances

    membersOf = defaultdict(list)
    for pathway in installedPathways:
        for ancestor, distance in ancestorsOf(pathway).items():
            membersOf[ancestor].append((pathway, distance))
            # An installed ancestor is linked to its installed descendants.
            if ancestor in installedPathways:
                edges.add((ancestor, pathway) if ancestor < pathway
                          else (pathway, ancestor))

    # sorted() throughout, so a rebuild on identical input is deterministic.
    for ancestor in sorted(membersOf):
        members = sorted(membersOf[ancestor])
        if len(members) > maxGroupSize:
            stderr.write(
                "Reactome hierarchy: {} has {} installed descendants within {} "
                "levels, above the group cap of {}; pairing its direct children "
                "only.\n".format(ancestor, len(members), maxSingleDepth,
                                 maxGroupSize))
            members = [entry for entry in members if entry[1] == 1]

        for i in range(len(members)):
            first, firstDistance = members[i]
            for j in range(i + 1, len(members)):
                second, secondDistance = members[j]
                if firstDistance + secondDistance <= maxCombinedDepth:
                    edges.add((first, second) if first < second
                              else (second, first))

    return edges


def loadReactomeMapping(filePath, keyColumn, valueColumns, minColumns,
                        failedLines=None, specieLabel="", allowEmpty=False):
    """
    Index a Reactome mapping TSV as {key: [tuple(row[c] for c in valueColumns), ...]}.

    Order of appearance is preserved, because callers treat the first hit as the
    authoritative identifier and the remainder as secondary ones.

    quoting=csv.QUOTE_NONE is required. Reactome display names contain unbalanced
    double quotes and apostrophes ("5'-phospho...", 'the "open" state'), which the
    default csv dialect treats as field delimiters. That silently merges columns
    and shifts every identifier one field to the left, so entities map to the
    wrong genes rather than failing loudly.

    An empty file raises unless the caller passes allowEmpty=True. The split
    exists because the callers' invariants differ: a per-species GENE mapping
    can be legitimately empty (Reactome keys ddi/spo/pfa through NCBI and
    UniProt but not Ensembl, and processReactomeData.R deliberately writes an
    empty file for such a source -- the caller enforces the real invariant,
    that not EVERY gene mapping is empty), while the common, all-species ChEBI
    file has no legitimate empty state: zero rows there means a truncated
    download, and every downstream lookup would quietly return nothing instead
    of reporting the real problem.
    """
    if failedLines is None:
        failedLines = []

    if not os.path.isfile(filePath):
        raise Exception(
            "Reactome mapping file not found: " + filePath + "\n" +
            "processReactomeData.R did not produce it. Check that the species code '" +
            str(specieLabel) + "' matches a species present in the Reactome download.")

    index = defaultdict(list)
    rowCount = 0
    with open(filePath) as handle:
        reader = csv.reader(handle, delimiter='\t', quoting=csv.QUOTE_NONE)
        for lineNumber, row in enumerate(reader, start=1):
            # Blank trailing lines are normal; do not log them as failures.
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            if len(row) < minColumns:
                failedLines.append(
                    ["Short row in " + os.path.basename(filePath), str(lineNumber), "\t".join(row)])
                continue
            index[row[keyColumn]].append(tuple(row[c] for c in valueColumns))
            rowCount += 1

    if rowCount == 0:
        if not allowEmpty:
            raise Exception(
                "Reactome mapping file is empty: " + filePath + "\n" +
                "This file is not species-scoped, so zero rows means a truncated "
                "or failed download (species being processed: '" +
                str(specieLabel) + "').")
        stderr.write("WARNING: no rows in {} for species '{}'; this identifier "
                     "source contributes nothing.\n".format(
                         os.path.basename(filePath), specieLabel))
        return index

    stderr.write("Indexed {} rows from {} ({} distinct keys)\n".format(
        rowCount, os.path.basename(filePath), len(index)))
    return index


def processReactomePathwaysData():

    # Declare later used variables
    FAILED_LINES["REACTOME PATHWAYS"] = []
    TOTAL_FEATURES["REACTOME PATHWAYS"] = 0
    NODES = {}
    EDGES = []

    # Entity processing counters for tracking identifier sources
    entities_processed = 0
    entities_with_geneNames = 0
    entities_fallback_mapping = 0
    entities_fallback_displayName = 0
    entities_skipped_no_identifier = 0
    #
    #if not len(ALL_ENTRIES):
    #    stderr.write("The mapping entries dictionary is not filled. Mapping of KEGG & auxiliary files must be processed first.")
    #    exit(1)

    REACTOME_DIR = DATA_DIR + "/reactome"

    print("REACTOME_DATA_DIR:" + REACTOME_DIR)
    #REACTOME_DIR = DATA_DIR + "reactome"

    # If the path already exists rename it
    #if (os.path.exists(REACTOME_DIR)):
    #    shutil.rmtree(REACTOME_DIR + ".bak", ignore_errors=True)
    #    shutil.move(REACTOME_DIR, REACTOME_DIR + ".bak")

    #os.makedirs(REACTOME_DIR)
    #os.makedirs(REACTOME_DIR + "/png/thumbnails/")

    # When downloading Reactome data we only retrieve the top pathways from the species.
    # The rest of them need to be downloaded in the installation process.
    #reactome_top_pathways = EXTERNAL_RESOURCES.get("reactome")[0]
    #reactome_top_pathways_file_name = DATA_DIR + "mapping/" + reactome_top_pathways.get("output")

    #if not os.path.isfile(reactome_top_pathways_file_name):
    #    stderr.write("\n\nUnable to find the Reactome top pathways file: " + reactome_top_pathways_file_name + "\n")
    #    exit(1)

    # Because of the current particularities of the Reactome data, the mapping process must be done parallel
    # to the insertion of pathways.
    reactome_gene_db_id = insertDatabase(
        DBNAME_Entry("reactome_gene_id", "Reactome Gene identifier", "Identifier"))

    reactome_gene_db_id_other = insertDatabase(
        DBNAME_Entry("reactome_gene_id_other", "Reactome Gene identifier (other ids)", "Identifier"))

    reactome_gene_desc = "Extracted from Reactome Database"
    reactome_gene_desc_other = "Extracted from Reactome Database (other identifiers)"

    # Initialize classification counters
    mainClassificationIDs = {}
    secClassificationIDs = {}
    pathway2gene = defaultdict(set)
    gene2pathway = defaultdict(set)
    REACTOME_COMPOUNDS = {}
    total_lines = 0


    PATHWAY_ID = set()

    # In case we want to split the linking process with KEGG & other databases, keeping
    # Reactome isolated.
    dbname = 'global'

    # Cache variables
    reactome_id_2_refseq_tid = {}  # We use a different one




    # The ReactomePathway.txt check further down already fail-softs the KEGG
    # build when Reactome was never downloaded - but the R script used to run
    # BEFORE that check and die first. ptr made this real: Reactome dropped
    # chimpanzee from its release, its download correctly skips Reactome, and
    # the build then crashed in processReactomeData.R on three empty mappings.
    # An absent download means "no Reactome for this species": take the same
    # warn-and-return exit before anything Reactome-related runs.
    if not haveInputFile("REACTOME PATHWAYS", DATA_DIR + 'ReactomePathway.txt',
                         "Reactome was not downloaded for this species (not covered "
                         "by the current Reactome release, or downloaded without "
                         "--reactome=1); KEGG pathways are unaffected."):
        return

    # The process will also insert information about compounds, thus discarding the previous database
    # and importing a new one, needing again the KEGG compounds.
    processKEGG2CompoundSymbolMappingData(DATA_DIR + "../common/compounds_all.list")

    mapReactomeDir = DATA_DIR + "mapping/reactome/"

    command = ROOT_DIR + "/scripts/processReactomeData.R" + " --specie=" + SPECIE + " --root=" + DATA_DIR + "/../common/"

    print("Processing Reactome data with R script...")
    print("Command: " + command)
    try:
        # Redirect stderr to stdout so we can capture all output
        output = check_output(command + " 2>&1", shell=True, universal_newlines=True)
        print(output)  # Print R script output
        print("Reactome R script completed successfully")
    except CalledProcessError as e:
        rOutput = e.output if getattr(e, 'output', None) else ""
        # The R script's aggregate zero-coverage stop is a fact about Reactome's
        # release, not a build failure: every gene mapping came up empty, so
        # there is nothing to attach genes to and installing Reactome would ship
        # pathways with no gene mapped to anything. Take the same warn-and-return
        # exit as a missing download -- the KEGG pathways just built are
        # unaffected -- instead of failing the species with a misleading
        # "check memory/R installation" error.
        if "in any of Ensembl2Reactome, NCBI2Reactome or UniProt2Reactome" in rOutput:
            print(rOutput)
            print("WARNING: Reactome's current release has no gene mappings for "
                  "species '" + str(SPECIE) + "'; skipping Reactome (KEGG "
                  "pathways are unaffected).")
            return
        print("ERROR: Reactome R script failed with exit code: " + str(e.returncode))
        if rOutput:
            print("Output from R script:")
            print(rOutput)
        raise Exception("Failed to process Reactome data. Check memory usage and R installation.")


    # ------------------------------------------------------------------------
    # Mapping tables: Reactome physical-entity stId -> external identifiers.
    #
    # These used to be kept as parallel lists and searched with
    #     [i for i, x in enumerate(someList) if x == wanted]
    # once per entity, per pathway. Each file holds 1e5-7e5 rows and a species
    # contributes ~1e5 entities, so that is ~1e11 comparisons - the reason a
    # Reactome install appeared to hang rather than fail. Hash indexes built
    # once turn every one of those scans into an O(1) dict lookup.
    #
    # quoting=csv.QUOTE_NONE is required: Reactome display names contain
    # unbalanced double quotes and apostrophes ("5'-...", 'sodium "channel"'),
    # which the default csv dialect treats as field delimiters and silently
    # merges columns, shifting every identifier one field to the left.
    # ------------------------------------------------------------------------
    failedLines = FAILED_LINES["REACTOME PATHWAYS"]

    # stId -> [(ensembl_id, symbol), ...] and the NCBI / UniProt equivalents.
    # allowEmpty on the three per-species gene mappings ONLY: any one of them
    # can be legitimately empty (see loadReactomeMapping's docstring); the
    # all-empty case is caught just below. The common ChEBI file further down
    # keeps the raise -- empty there means a truncated download.
    ensemblByStId = loadReactomeMapping(
        mapReactomeDir + "Ensembl2Reactome.txt", keyColumn=1, valueColumns=(0, 2),
        minColumns=3, failedLines=failedLines, specieLabel=SPECIE, allowEmpty=True)
    ncbiByStId = loadReactomeMapping(
        mapReactomeDir + "NCBI2Reactome.txt", keyColumn=1, valueColumns=(0, 2),
        minColumns=3, failedLines=failedLines, specieLabel=SPECIE, allowEmpty=True)
    uniprotByStId = loadReactomeMapping(
        mapReactomeDir + "UniProt2Reactome.txt", keyColumn=1, valueColumns=(0, 2),
        minColumns=3, failedLines=failedLines, specieLabel=SPECIE, allowEmpty=True)

    # The invariant the old per-file check was reaching for: a species whose
    # EVERY gene mapping is empty would install Reactome pathways with no gene
    # attached to anything, which must fail loudly rather than ship.
    if not ensemblByStId and not ncbiByStId and not uniprotByStId:
        raise Exception(
            "All three Reactome gene mappings (Ensembl, NCBI, UniProt) are "
            "empty for species '" + str(SPECIE) + "'. Reactome does not cover "
            "this organism; install it with --reactome=0.")

    # stId -> [chebi_id, ...]. This file is NOT species-filtered, so it is the
    # largest of the five and was the single worst offender in the old scan.
    chebiByStId = {
        stId: [value[0] for value in values]
        for stId, values in loadReactomeMapping(
            DATA_DIR + "../common/ChEBI2Reactome_PE_All_Levels.txt",
            keyColumn=1, valueColumns=(0,), minColumns=2,
            failedLines=failedLines, specieLabel=SPECIE).items()
    }

    # chebi_id -> [kegg_compound_id, ...]. The source file is written by
    # downloadChEBItoKEGGMapping() as "chebi:XXXXX<TAB>cpd:CXXXXX", so column 0
    # is the ChEBI accession and column 1 the KEGG compound - despite the
    # historical variable names suggesting the opposite.
    keggByChebi = defaultdict(list)
    kegg2chebiPath = DATA_DIR + "../common/kegg2chebi.list"
    if not os.path.isfile(kegg2chebiPath):
        raise Exception("KEGG-ChEBI mapping file not found: " + kegg2chebiPath)
    with open(kegg2chebiPath) as KEGG2ChEBI:
        for lineNumber, row in enumerate(csv.reader(KEGG2ChEBI, delimiter='\t', quoting=csv.QUOTE_NONE), start=1):
            if len(row) < 2:
                continue
            # Entries are namespaced ("chebi:15377"), but tolerate bare ids so a
            # hand-edited or differently-sourced file cannot raise IndexError.
            chebiId = row[0].split(":")[-1].strip()
            keggId = row[1].split(":")[-1].strip()
            if chebiId and keggId:
                keggByChebi[chebiId].append(keggId)

    # kegg_compound_id -> [display name, ...]. KEGG_COMPOUNDS maps name -> id and
    # is fully populated by processKEGG2CompoundSymbolMappingData() above; it is
    # not mutated below, so a single reverse index stays valid for the whole run.
    keggCompoundNamesById = defaultdict(list)
    for compoundName, compoundIds in KEGG_COMPOUNDS.items():
        # Values are a SET of ids (a name can belong to several compounds), so index
        # the name under each of them -- iterating the value as a scalar here would
        # walk the string's characters and build an index keyed by letters.
        for compoundId in compoundIds:
            keggCompoundNamesById[compoundId].append(compoundName)



    REACTOME_PATHWAY = DATA_DIR + 'ReactomePathway.txt'

    # Absent whenever the species was downloaded without --reactome=1, and for every
    # organism Reactome does not curate. Neither is a reason to lose the KEGG pathways
    # that were just built, so warn and return instead of raising FileNotFoundError.
    if not haveInputFile("REACTOME PATHWAYS", REACTOME_PATHWAY,
                         "Reactome pathways will be absent; KEGG pathways are unaffected. "
                         "Re-run the download with --reactome=1 if this species is curated by Reactome"):
        return

    with open( file=REACTOME_PATHWAY ) as pathwayList:
        for line in pathwayList:
            PATHWAY_ID.add(line.strip())

    def showPercentageSimple(n, total):
        # Unconditional and once per pathway, so ~1,000 lines for human alone. Silent
        # unless PAINTOMICS_INSTALL_VERBOSE=1, like the other progress writer.
        percen = int( n / float( total ) * 10 )
        if VERBOSE:
            stderr.write(
                "0%[" + ("#" * percen) + (" " * (10 - percen)) + "]100% [" + str( n ) + "/" + str( total ) + "]\t" )
        return percen


    stderr.write("\nClassification part \n")

    # Loaded once: this file is constant for the whole species and was previously
    # re-opened and re-parsed inside the per-pathway loop below.
    pathway_hierachy_file = DATA_DIR + "ReactomePathwayHierarchy.json"
    if not os.path.isfile(pathway_hierachy_file):
        raise Exception(
            "Reactome pathway hierarchy not found: " + pathway_hierachy_file + "\n" +
            "Run the download step with --reactome=1 before building.")
    with open(pathway_hierachy_file) as HierachyRelation:
        hierachyRelation = json.load(HierachyRelation)

    #pathway_id = ""
    indexFinal = 0

    total_feature = defaultdict(set)

    for pathway_id in PATHWAY_ID:


        stderr.write("Start Installing: " + pathway_id + "   ")
        indexFinal += 1
        showPercentageSimple( indexFinal, len( PATHWAY_ID ) )
        stderr.write('\n')

        #stderr.write("\nStart Analysis:" + pathway_id +"\n")
        nodes_tmp_file = REACTOME_DIR + "/" + pathway_id + ".json"

        with open(nodes_tmp_file) as pathway_info:
            pathway_data = json.load(pathway_info)

        #try:
            # Find Higher Level Pathway information and download them
        #    IDList = findHighLevelPathway(pathway_id, ReactomeHierarchy, ReactomePathwayHighList, ReactomePathwayLowList)
        #except Exception as ex:
        #   IDList = [pathway_id,pathway_id]
        pathway_name = pathway_data.get("displayName")

        ## Some time this could happen: the downloader records a hierarchy entry
        ## only for pathways it resolved, so a pathway listed in ReactomePathway.txt
        ## can be missing here. Treating it as its own top level keeps the build
        ## going instead of raising TypeError on IDList[0].
        IDList = hierachyRelation.get(pathway_id)
        if not IDList or len(IDList) < 2:
            FAILED_LINES["REACTOME PATHWAYS"].append(
                ["Missing hierarchy entry", pathway_id, str(IDList)])
            IDList = [pathway_id, pathway_id]
        top_pathway_details_filename = REACTOME_DIR + '/' + IDList[0] + "details.json"
        secondary_pathway_details_filename= REACTOME_DIR + '/' + IDList[1] + "details.json"
        try:
            with open(top_pathway_details_filename) as top_pathways:
                top_pathway=json.load(top_pathways)
            with open(secondary_pathway_details_filename) as secondary_pathways:
                secondary_pathway=json.load(secondary_pathways)
        except Exception:
            continue

        mainClassification = top_pathway.get("displayName")
        secondClassification = secondary_pathway.get("displayName")

        if not mainClassification in mainClassificationIDs:
            mainClassificationIDs[mainClassification] = len(mainClassificationIDs) + 1
            NODES[str(mainClassificationIDs[mainClassification]) + "A"] = {
                "data": {"id": mainClassification.lower().replace(" ", "_"),
                         "label": mainClassification, "is_classification": "A"},
                "group": "nodes"}

        # Secondary classification.
        #
        # The guard tested `secondClassification` but the three lines under it
        # all keyed on `mainClassification`, so the counter advanced once per
        # distinct *main* classification while the guard fired once per distinct
        # *secondary* one. Two secondary classifications under the same main one
        # therefore computed the same NODES key and the second silently replaced
        # the first. mmu installs 57 classification nodes for what should be
        # more; the losses are invisible because the client skips every node
        # carrying `is_classification` when it builds the network.
        if not secondClassification in secClassificationIDs:
            secClassificationIDs[secondClassification] = len(secClassificationIDs) + 1
            NODES[str(secClassificationIDs[secondClassification]) + "B"] = {
                "data": {"id": secondClassification.lower().replace(" ", "_"),
                         "parent": mainClassification.lower().replace(" ", "_"),
                         "label": secondClassification,
                         "is_classification": "B"}, "group": "nodes"}

        # Append to the global pathways container
        ALL_PATHWAYS[pathway_id] = {"ID": pathway_id, "name": pathway_name, "genes": [],
                                    "compounds": [], "relatedPathways": [],
                                    "source": "Reactome",
                                    "featureDB": "reactome_gene_id",
                                    "classification": ';'.join([mainClassification, secondClassification])}

        # Pathway node information
        NODES[pathway_id] = {
            "data": {"id": pathway_id, "label": pathway_name, "total_features": 0},
            "group": "nodes"}
        NODES[pathway_id]["data"]["parent"] = mainClassification.lower().replace(" ", "_"),

        # Select the first and only component

        #stderr.write("\nLoading pathway info ")


        with open (REACTOME_DIR + "/" + pathway_id + ".graph.json") as graphInf:
            graphData = json.load(graphInf)

        nodesInf = graphData.get( "nodes" )

        # Build a hierarchy relationship for nodes class as complex
        nodesHighList = list()  # high level
        nodesLowList = list()  # low level
        for item in graphData.get('nodes'):
            #if item['schemaClass'] == 'Complex' | item['schemaClass'] == 'DefinedSet' | item['schemaClass'] == 'CandidateSet' | item['schemaClass'] == 'Polymer':
            try:
                for children in item['children']:
                    nodesHighList.append(item['dbId'])
                    nodesLowList.append(children)
            except Exception:
                continue
        nodesHighSet = set(nodesHighList)
        nodesLowSet = set(nodesLowList)

        nodesMiddleSet = nodesHighSet.intersection(nodesLowSet)
        nodesTopSet = nodesHighSet.difference(nodesLowSet)




        # Complex item contains several proteins, we need to find out which protein it represents.
        def findLastLevelNodes( nodesList ):
            length = len( nodesList )
            for subNode in nodesList:
                try:
                    tempList= next( item for item in nodesInf if item["dbId"] == subNode )['children']
                    if not tempList:  # Empty children list = leaf node, don't pop
                        continue
                    nodesList.pop( nodesList.index( subNode ) )
                    nodesList = nodesList + tempList
                except Exception:
                    continue

            hasSubList = False

            if len(nodesList) == length:
                for subNode in nodesList:
                    try:
                        children = next( item for item in nodesInf if item["dbId"] == subNode )['children']
                        if children:  # Only count non-empty children lists
                            hasSubList = True
                    except Exception:
                        continue
                if hasSubList:
                    return findLastLevelNodes( nodesList )
                else:
                    return set( nodesList )
            else:
                return findLastLevelNodes( nodesList )

        highHierarchySet = defaultdict( set )
        middleHierarchySet = defaultdict( set )
        #node = 10032727
        for node in nodesTopSet:
            inputList = next( item for item in nodesInf if item["dbId"] == node )['children']
            outputSet = findLastLevelNodes(inputList)
            for value in outputSet:
                highHierarchySet[node].add(value)
        for node in nodesMiddleSet:
            inputList = next( item for item in nodesInf if item["dbId"] == node )['children']
            outputSet = findLastLevelNodes(inputList)
            for value in outputSet:
                middleHierarchySet[node].add(value)


        # Parse each node of the pathway
        #reactome_entity = pathway_data.get("nodes")[20]
        #reactome_entity = next( item for item in pathway_data.get("nodes") if item["reactomeId"] == 188833 )
        for reactome_entity in pathway_data.get("nodes"):

            entity_id = reactome_entity.get("reactomeId")

            try:
                 entity_reactome = next( item for item in nodesInf if item["dbId"] == entity_id )
            except Exception:
                continue

            #graphic_id = reactome_entity.get("id")

            #stderr.write("\nChecking entity id " + str(entity_id))

            # Calculate the middle point
            propX = int(reactome_entity.get("prop").get("x"))
            propY = int(reactome_entity.get("prop").get("y"))

            propHeight = int(reactome_entity.get("prop").get("height"))
            propWidth = int(reactome_entity.get("prop").get("width"))

            entry = {
                "id": "",
                "x": int(propX + (propWidth / 2)),
                "y": int(propY + (propHeight / 2)),
                "height": propHeight,
                "width": propWidth,
                "schemaClass": reactome_entity.get("schemaClass")
            }
            entry['schemaClass'] = entity_reactome["schemaClass"]

            #There are complex item in Reactome, which means that a protein contains some other components. We need to find out the protein
            #ID to make PaintOmics work.

                ## Sometime the graph.json file may not contain all nodes in the json file. We should use previous method to install that node.


            # Sometimes the reactome id has subclasses we need to manage them one by one
            if entity_id in nodesHighSet:
                if entity_id in highHierarchySet:
                    nodeIDSet = highHierarchySet[entity_id]
                elif entity_id in middleHierarchySet:
                    nodeIDSet = middleHierarchySet[entity_id]
                else:
                    nodeIDSet = {entity_id}
            else:
                nodeIDSet = {entity_id}

                # Find element in the database. if we can not find it return the Reactome id
            for nodeID in nodeIDSet:

                #stderr.write('\n \nstart analysising:' + str(nodeID))

                entity_reactome = next( item for item in nodesInf if item["dbId"] == nodeID )
                entity_reactome_id = entity_reactome['stId']
                #entry['schemaClass'] = next( item for item in nodesInf if item["dbId"] == entity_id )["schemaClass"]
                entity_reactome_id_name = entity_reactome['displayName']
                entity_reactome_id_name_simple = entity_reactome_id_name.rsplit('[', -1)[0]
                if entity_reactome.get("schemaClass") == 'SimpleEntity':
                    chebiIds = chebiByStId.get(entity_reactome_id, [])
                    ## Can not find chebi ID
                    if not chebiIds:
                        #print("Can not find:" + entity_reactome_id)
                        entryAux = entry.copy()
                        entryAux["id"] = entity_reactome_id_name_simple
                        REACTOME_COMPOUNDS[entity_reactome_id_name_simple] = entity_reactome_id
                        ALL_PATHWAYS[pathway_id]["compounds"].append(entryAux)
                    else:
                        # The first ChEBI id that carries a KEGG compound mapping wins.
                        # The previous implementation pop()ed from a set, so which id
                        # won -- and therefore the contents of the built database --
                        # varied between runs on identical input. Iterating in file
                        # order makes the build reproducible.
                        keggIds = []
                        for subChebiID in chebiIds:
                            if keggByChebi.get(subChebiID):
                                keggIds = keggByChebi[subChebiID]
                                break

                        if not keggIds:
                            entryAux = entry.copy()
                            entryAux["id"] =entity_reactome_id_name_simple
                            REACTOME_COMPOUNDS[entity_reactome_id_name_simple] = entity_reactome_id
                            ALL_PATHWAYS[pathway_id]["compounds"].append(entryAux)
                        else:
                            keggID = keggIds[0]

                            ## Get the key from KEGG and use it to save reactome
                            compoundNames = keggCompoundNamesById.get(keggID, [])
                            if compoundNames:
                                for compoundName in compoundNames:
                                    REACTOME_COMPOUNDS[compoundName] = keggID
                                    entryAux = entry.copy()
                                    entryAux["id"] = keggID
                                    ALL_PATHWAYS[pathway_id]["compounds"].append(entryAux)
                            else:
                                # Sometimes the kegg id is not present in the kegg_compounds.
                                # We need use entity_reactome_id_name to serve as the key.
                                # This fallback previously sat in an `except` clause that could
                                # never run -- an empty list comprehension raises nothing -- so
                                # these compounds were silently dropped from the pathway.
                                REACTOME_COMPOUNDS[entity_reactome_id_name_simple] = keggID
                                entryAux = entry.copy()
                                entryAux["id"] = keggID
                                ALL_PATHWAYS[pathway_id]["compounds"].append( entryAux )

                elif entity_reactome.get("schemaClass") == "EntityWithAccessionedSequence":

                    ensemblHits = ensemblByStId.get(entity_reactome_id, [])
                    ncbiHits = ncbiByStId.get(entity_reactome_id, [])
                    uniprotHits = uniprotByStId.get(entity_reactome_id, [])
                    other_ids = set()

                    # Blank cells are discarded: an empty identifier used to reach
                    # pathway2gene/gene2pathway as a real gene key.
                    for hits in (ensemblHits, ncbiHits, uniprotHits):
                        for externalId, symbol in hits:
                            if externalId:
                                other_ids.add(externalId)
                            if symbol:
                                other_ids.add(symbol)

                    # Try to get gene identifier from multiple sources with fallback hierarchy
                    gene_names = entity_reactome.get('geneNames')
                    gene_id = None
                    gene_ids = []

                    # Source 1: geneNames field (primary - existing behavior)
                    if gene_names and len(gene_names) > 0:
                        gene_ids = gene_names.copy()
                        gene_id = gene_ids.pop(0).upper()
                        entities_with_geneNames += 1

                    # Source 2: External mapping files (fallback - Ensembl > NCBI > UniProt)
                    elif ensemblHits or ncbiHits or uniprotHits:
                        # Prefer Ensembl symbols (most authoritative)
                        if ensemblHits and ensemblHits[0][1]:
                            gene_id = ensemblHits[0][1].upper()
                            gene_ids = [symbol.upper() for _, symbol in ensemblHits[1:] if symbol]
                            entities_fallback_mapping += 1
                        # Then try NCBI symbols
                        elif ncbiHits and ncbiHits[0][1]:
                            gene_id = ncbiHits[0][1].upper()
                            gene_ids = [symbol.upper() for _, symbol in ncbiHits[1:] if symbol]
                            entities_fallback_mapping += 1
                        # Finally try UniProt symbols
                        elif uniprotHits and uniprotHits[0][1]:
                            gene_id = uniprotHits[0][1].upper()
                            gene_ids = [symbol.upper() for _, symbol in uniprotHits[1:] if symbol]
                            entities_fallback_mapping += 1

                    # Source 3: DisplayName (last resort - parse name before [location])
                    if not gene_id:
                        displayName = entity_reactome.get('displayName', '')
                        if displayName:
                            displayName_simple = displayName.rsplit('[', 1)[0].strip()
                            if displayName_simple:
                                gene_id = displayName_simple.upper()
                                gene_ids = []
                                entities_fallback_displayName += 1
                                stderr.write("Warning: Using displayName for {}: {}\n".format(entity_reactome_id, gene_id))

                    # Skip only if all methods fail
                    if not gene_id:
                        entities_skipped_no_identifier += 1
                        stderr.write("Error: No identifier found for {}\n".format(entity_reactome_id))
                        FAILED_LINES["REACTOME PATHWAYS"].append(
                            ["No gene identifier", entity_reactome_id, entity_reactome.get('displayName', '')])
                        continue

                    # Track entity as processed successfully
                    entities_processed += 1
                    total_feature[pathway_id].add(gene_id)



                    for ID in gene_ids:
                        other_ids.add(ID.upper())


                    pathway2gene[pathway_id].update([gene_id] + list(other_ids))
                    for gene in [gene_id] + list(other_ids):
                        gene2pathway[gene].add(pathway_id)
                    entryAux = entry.copy()
                    entryAux["id"] = gene_id

                    ALL_PATHWAYS[pathway_id]["genes"].append(entryAux)

                    # Reactome may use as gene id symbols already provided by KEGG
                    # or other linked databases. As Paintomics uses the database id
                    # to properly identify which symbol to display, we need to have
                    # a copy for reactome database, using an empty string so as to avoid
                    # conflictions.
                    previous_symbol_item = findXREFByEntry(gene_id)

                    reactome_gi = insertXREF(
                        XREF_Entry(gene_id, reactome_gene_db_id,
                                   reactome_gene_desc), dbname)

                    if previous_symbol_item:
                        transcript_id = previous_symbol_item.getID()
                    else:
                        # Generate a random ID for the reactome identifier
                        transcript_id = reactome_id_2_refseq_tid.get(
                            reactome_gi,
                            generateRandomID(dbname))  # Try to reuse the ids for random transcripts
                        reactome_id_2_refseq_tid[reactome_gi] = transcript_id

                    # Save the reference
                    insertTR_XREF(reactome_gi, transcript_id, dbname)

                    for synonym_id in other_ids:
                        # Check if the id already exists as a previous XREF.
                        # Note: this requires KEGG mapping to be done BEFORE inserting
                        # reactome pathways.
                        #stderr.write("\n\t\tSynonym " + str(synonym_id))

                        entity_item = findXREFByEntry(synonym_id)

                        if not entity_item:
                            entity_gi = insertXREF(XREF_Entry(synonym_id,
                                                              reactome_gene_db_id_other,
                                                              reactome_gene_desc_other),
                                                   dbname)

                        else:
                            entity_gi = entity_item.getID()

                        insertTR_XREF(entity_gi, transcript_id, dbname)

                elif entity_reactome.get( "schemaClass" ) == "Pathway":
                    entry = {
                        "id": entity_reactome_id,
                        "name": entity_reactome_id_name,
                        "x": int( propX + (propWidth / 2) ),
                        "y": int( propY + (propHeight / 2) ),
                        "height": propHeight,
                        "width": propWidth
                    }
                    ALL_PATHWAYS[pathway_id]["relatedPathways"].append( entry )
                # TODO: What about Genome Encoded Entity class in Reactome. Currently, we do not install them since they are Ghost homologue of a protein.
                else:
                    continue




    # Append the new Reactome compounds to the file previously generated
    # by KEGG process.
    file = open("/tmp/compounds.tmp", 'a')
    for cpdName, cpdID in REACTOME_COMPOUNDS.items():
        file.write(json.dumps({"id": cpdID, "name": cpdName}, cls=SetEncoder, separators=(',', ':')) + "\n")
    file.close()

    # Insert the compounds collection
    createCompoundsCollection()

    # MapMan pathways files are the same for each species, even the XML files.
    # The handful of species compatible with MapMan will specify to download the same dataset.
    # Here we override the data always.
    # TODO: modify DBManager.py and move the code to "downloadData"?

    # mapman_pathways = EXTERNAL_RESOURCES.get("mapman_pathways")[0]
    # pathways_file_name = DATA_DIR + "mapping/" + mapman_pathways.get("output")

    # i = 0;
    # prev = -1;
    # errorMessage = "";
    # xml_files = os.listdir(MAPMAN_XML)
    # total_lines = len(xml_files)
    #
    # for xml_file in xml_files:
    #     i+=1
    #     prev = showPercentage(i, total_lines, prev, errorMessage)

    # ***********************************************************************************
    # * GENERATE THE NETWORK FILE DATA FOR REACTOME
    # ***********************************************************************************

    # ***********************************************************************************
    # * FIRST PROCESS THE FILE WITH ALL PATHWAYS AND GENERATE A DIAGONAL MATRIX
    #          mmu00100 -> [ mmu00101 = 0, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00102 -> [                             mmu00103 = 0,...]
    # ***********************************************************************************
    all_pathways = sorted(NODES.keys())

    pathways_matrix = {}
    while len(all_pathways) > 0:
        current_path = all_pathways[0]
        del all_pathways[0]
        pathways_matrix[current_path] = dict(zip(all_pathways, [0] * len(all_pathways)))

    # ***********************************************************************************
    # * PROCESS THE FILE WITH THE RELATION GENE ID -> PATHWAY ID AND FILL THE MATRIX
    #          WITH THE NUMBER OF SHARED GENES
    #          mmu00100 -> [ mmu00101 = 1, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 5, mmu00103 = 3,...]
    #          mmu00102 -> [                             mmu00103 =20,...]
    # ***********************************************************************************
    previous_gene = ""
    associated_paths = set()

    for gene_id, pathway_ids in gene2pathway.items():
        for path_id in pathway_ids:
            if gene_id != previous_gene:
                associated_paths = sorted(associated_paths)
                while len(associated_paths) > 0:
                    current_path = associated_paths[0]
                    del associated_paths[0]
                    for other_path in associated_paths:
                        try:
                            pathways_matrix[current_path][other_path] += 1
                        except:
                            stderr.write("Pathways " + current_path + " or " + other_path + " not found in Reactome network values.\n")

                associated_paths = set([])

            associated_paths.add(path_id)
            previous_gene = gene_id

    # LAST PATHWAY
    associated_paths = sorted(associated_paths)
    while len(associated_paths) > 0:
        current_path = associated_paths[0]
        del associated_paths[0]
        for other_path in associated_paths:
            try:
                pathways_matrix[current_path][other_path] += 1
            except:
                stderr.write(
                    "Pathways " + current_path + " or " + other_path + " not found in Reactome network values.\n")

    # ***********************************************************************************
    # * THE SAME, FOR COMPOUNDS
    #          A Reactome SimpleEntity is a small molecule, and two processes that use
    #          the same one share a biological feature. The matrix counted genes only,
    #          so the "shared biological features" mode was blind to metabolites.
    # ***********************************************************************************
    reactomePathways = {pathwayID: pathway for pathwayID, pathway in ALL_PATHWAYS.items()
                        if pathway.get("source") == "Reactome"}
    accumulateSharedFeatures(pathways_matrix,
                             indexCompoundsByPathway(reactomePathways))

    # ***********************************************************************************
    # * ENTITY PROCESSING SUMMARY
    # ***********************************************************************************
    stderr.write("\n" + "="*80 + "\n")
    stderr.write("Reactome Entity Processing Summary:\n")
    stderr.write("="*80 + "\n")
    total_entities = (entities_with_geneNames + entities_fallback_mapping +
                     entities_fallback_displayName + entities_skipped_no_identifier)
    stderr.write("  Total EntityWithAccessionedSequence nodes: {}\n".format(total_entities))
    stderr.write("  Successfully processed: {}\n".format(entities_processed))
    stderr.write("    - With geneNames field: {} ({:.1f}%)\n".format(
        entities_with_geneNames,
        100.0 * entities_with_geneNames / total_entities if total_entities > 0 else 0))
    stderr.write("    - Rescued via mapping files: {} ({:.1f}%)\n".format(
        entities_fallback_mapping,
        100.0 * entities_fallback_mapping / total_entities if total_entities > 0 else 0))
    stderr.write("    - Rescued via displayName: {} ({:.1f}%)\n".format(
        entities_fallback_displayName,
        100.0 * entities_fallback_displayName / total_entities if total_entities > 0 else 0))
    stderr.write("  Skipped (no identifier): {} ({:.1f}%)\n".format(
        entities_skipped_no_identifier,
        100.0 * entities_skipped_no_identifier / total_entities if total_entities > 0 else 0))
    stderr.write("="*80 + "\n\n")

    # ***********************************************************************************
    # * GET THE NUMBER OF GENES FOR EACH PATHWAY
    # ***********************************************************************************
    reactome_g2p_file = DATA_DIR + "gene2pathway_reactome.list"

    # Write a "gene2pathway_mapman.list" to be used for metagenes generation.
    with open(reactome_g2p_file, 'w') as reactome_gene2pathway:
        for path_id, gene_ids in pathway2gene.items():
            # Write one row for each gene and pathway
            # reactome_gene2pathway.writelines(geneID.encode('utf-8') + "\t".encode('utf-8') + path_id.encode('utf-8') + "\n".encode('utf-8') for geneID in gene_ids)
            reactome_gene2pathway.writelines("{}\t{}\n".format(geneID, path_id) for geneID in gene_ids)


    for path_id, gene_ids in total_feature.items():
        try:
            NODES[path_id]["data"]["total_features"] = len(gene_ids)
        except Exception:
            print(path_id)
            continue

    # A field of its own beside it: total_features has meant "genes" since
    # Paintomics 3 and is read by clusters.py and by any client that has not
    # reloaded, so redefining it would silently move their filter.
    for path_id, pathway in reactomePathways.items():
        node = NODES.get(path_id)
        if node is not None:
            node["data"]["total_compounds"] = len(
                set(compound.get("id") for compound in pathway.get("compounds", [])
                    if compound.get("id")))

    # ***********************************************************************************
    # * BULK THE MATRIX INTO JSON:
    #          FOR EACH PATHWAY ID AND FOR EACH POSITION WITH NON ZERO (SHARE AT LEAST 1 GENE), CREATE AN EDGE
    # ***********************************************************************************
    # The set of pathways that actually became nodes. Both edge passes below are
    # restricted to it: an edge whose target was never installed is invisible in
    # the client (which drops any edge with an unknown endpoint) but still costs
    # a row in a 2MB file, and 52 of the 451 link edges were exactly that.
    installedPathways = set(
        node["data"]["id"] for node in NODES.values()
        if "is_classification" not in node["data"])

    already_linked_pathways = {}

    def addLinkEdge(source, target):
        """One 'l' edge, once, in whichever direction it is first seen."""
        if source == target:
            return
        key = source + "-" + target
        if key in already_linked_pathways:
            return
        EDGES.append({"data": {"id": key, "source": source, "target": target,
                               "weight": 1, "class": 'l'}, "group": "edges"})
        already_linked_pathways[key] = 1
        already_linked_pathways[target + "-" + source] = 1

    for path_id, shared_genes in pathways_matrix.items():
        # First create the edges based on the links between networks (extracted
        # from the pathway diagrams: a Reactome diagram can embed another
        # pathway as a process node, which is this database's analogue of a
        # KEGG map link).
        if path_id in ALL_PATHWAYS:
            for other_path_id in ALL_PATHWAYS[path_id]["relatedPathways"]:
                if other_path_id["id"] in installedPathways and path_id in installedPathways:
                    addLinkEdge(path_id, other_path_id["id"])
        # Add the edges based on the existance of shared genes
        for other_path_id, n_shared_genes in shared_genes.items():
            if n_shared_genes > 0:
                EDGES.append({"data": {"id": path_id + "-" + other_path_id, "source": path_id, "target": other_path_id,
                                       "weight": n_shared_genes, "class": 's'}, "group": "edges"})

    # Then the hierarchy, which is where Reactome actually records that two
    # processes are related - see buildReactomeHierarchyEdges for why the
    # diagram pass on its own leaves half of these nodes isolated.
    hierarchyEdges = buildReactomeHierarchyEdges(
        installedPathways,
        DATA_DIR + "../common/ReactomePathwaysRelation.list",
        "R-" + SPECIE.upper() + "-")

    linkEdgesFromDiagrams = len(already_linked_pathways) // 2
    for source, target in sorted(hierarchyEdges):
        addLinkEdge(source, target)

    stderr.write(
        "Reactome network edges: {} from diagrams, {} after adding the "
        "hierarchy ({} pathway nodes)\n".format(
            linkEdgesFromDiagrams, len(already_linked_pathways) // 2,
            len(installedPathways)))

    # ***********************************************************************************
    # * SAVE THE NETWORK TO A FILE
    # ***********************************************************************************
    network = {
        "nodes": list(NODES.values()),
        "edges": EDGES
    }
    csvfile = open(DATA_DIR + "pathways_network_Reactome.json", 'w')
    csvfile.write(json.dumps(network, separators=(',', ':')) + "\n")
    csvfile.close()

    TOTAL_FEATURES["REACTOME PATHWAYS"] = total_lines

    # ***********************************************************************************
    # * PROCESS THE VERSION FILES
    # ***********************************************************************************
    version = open(DATA_DIR + 'REACTOME_VERSION', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M"))
    version.close()

    ALL_VERSIONS["REACTOME"] = {"name": "REACTOME", "date": strftime("%Y%m%d %H%M")}
    ALL_VERSIONS["REACTOME_MAPPING"] = {"name": "REACTOME_MAPPING", "date": strftime("%Y%m%d %H%M")}

    # ***********************************************************************************
    # * Move Reactome PNG files to global position
    # ***********************************************************************************

    REACTOME_DIR_PNG = REACTOME_DIR + '/png/'
    onlyPNG = [f for f in os.listdir(REACTOME_DIR_PNG) if os.path.isfile(os.path.join(REACTOME_DIR_PNG, f))]
    REACTOME_DIR_PNG_THUMB = REACTOME_DIR_PNG + '/thumbnails/'
    onlyPNGThumb = [f for f in os.listdir(REACTOME_DIR_PNG_THUMB) if
                    os.path.isfile(os.path.join(REACTOME_DIR_PNG_THUMB, f))]

    REACTOME_GLOBAL_DIR = DATA_DIR + '/../' + 'reactome'
    REACTOME_GLOBAL_DIR_PNG = REACTOME_GLOBAL_DIR + '/png/'
    REACTOME_GLOBAL_DIR_THUMB = REACTOME_GLOBAL_DIR_PNG + '/thumbnails'
    if not os.path.exists(REACTOME_GLOBAL_DIR):
        os.makedirs(REACTOME_GLOBAL_DIR)
    if not os.path.exists(REACTOME_GLOBAL_DIR_PNG):
        os.makedirs(REACTOME_GLOBAL_DIR_PNG)
    if not os.path.exists(REACTOME_GLOBAL_DIR_THUMB):
        os.makedirs(REACTOME_GLOBAL_DIR_THUMB)

    for file_name in onlyPNG:
        shutil.copy(REACTOME_DIR_PNG + file_name, REACTOME_GLOBAL_DIR_PNG)
    for file_name in onlyPNGThumb:
        shutil.copy(REACTOME_DIR_PNG_THUMB + file_name, REACTOME_GLOBAL_DIR_THUMB)

def processKEGGPathwaysData():
    FAILED_LINES["KEGG PATHWAYS"] = []
    #STEP 1. PROCESS THE pathways.list FILE
    file_name= DATA_DIR + "pathways.list"
    file = open(file_name, 'r')
    for line in file:
        line = line.rstrip().split("\t")
        pathway_id   = line[0]
        pathway_name = line[1]
        pathway_name = pathway_name[0:pathway_name.rfind(" - ")]

        ALL_PATHWAYS[pathway_id] = {"ID": pathway_id, "name": pathway_name, "classification": set([]), "genes":[], "compounds":[], "relatedPathways":[], "source": "KEGG", "featureDB": "kegg_id" }

    file.close()

    #STEP 2. PROCESS THE pathways_classification.list FILE
    file_name= DATA_DIR + "../common/pathways_classification.list"
    file = open(file_name, 'r')
    mainClassification=""; secondClassification="";
    for line in file:
        line = line.rstrip().split("  ")
        if line[0][0] == "A": #main classification
            mainClassification=line[0][1:]
        elif line[0][0] == "B":
            secondClassification = line[1]
        elif line[0][0] == "C":
            pathway_id   = line[2]
            if SPECIE + pathway_id in ALL_PATHWAYS:
                ALL_PATHWAYS[SPECIE + pathway_id]["classification"] = mainClassification + ";" + secondClassification
    file.close()

    #STEP 3. PROCESS ALL THE KGML FILES
    i =0; prev=-1; errorMessage=""; total_lines= len(list(ALL_PATHWAYS.keys()))
    for pathway_id in ALL_PATHWAYS.keys():
        i+=1
        prev = showPercentage(i, total_lines, prev, errorMessage)
        #FOR EACH PATHWAY READ THE XML DATA
        file_name= DATA_DIR + "kgml/" + pathway_id +".kgml"
        try:
            import xml.etree.ElementTree as XMLParser
            pathwayInfoXML = XMLParser.parse(file_name)
            root = pathwayInfoXML.getroot()
            already_added = {}
            #FOR EACH NODE IN THE XML FILE
            for child in root:
                try:
                    entryType =  child.get("type")

                    if (entryType == "compound") or (entryType == "gene"):
                        graphicInfo = child.find("graphics")
                        featureIDList = child.get("name").split(" ")
                        entry = {
                            "id"     : "",
                            "x"      : graphicInfo.get("x"),
                            "y"      : graphicInfo.get("y"),
                            "height" : graphicInfo.get("height"),
                            "width"  : graphicInfo.get("width")
                        }

                        for featureID in featureIDList:
                            if (entryType == "compound") and not featureID in already_added:
                                entryAux = entry.copy()
                                entryAux["id"] = featureID.replace("cpd:","")
                                ALL_PATHWAYS[pathway_id]["compounds"].append(entryAux)
                                #already_added[featureID] = 1
                            elif(entryType == "gene") and not featureID in already_added:
                                entryAux = entry.copy()
                                entryAux["id"] = featureID.replace(SPECIE + ":","")
                                ALL_PATHWAYS[pathway_id]["genes"].append(entryAux)
                                #already_added[featureID] = 1
                    elif (entryType == "map"):
                        graphicInfo = child.find("graphics")
                        pathAuxID = normaliseKeggPathwayId(child.get("name"), SPECIE)
                        if pathway_id != SPECIE + pathAuxID:
                            entry = {
                                "id"     : pathAuxID,
                                "name"   : graphicInfo.get("name"),
                                "x"      : graphicInfo.get("x"),
                                "y"      : graphicInfo.get("y"),
                                "height" : graphicInfo.get("height"),
                                "width"  : graphicInfo.get("width")
                            }
                            ALL_PATHWAYS[pathway_id]["relatedPathways"].append(entry)


                except Exception as ex:
                    errorMessage = "FAILED WHILE PROCESSING PATHWAY KGML FILE [" + file_name + "]: " + str(ex)
                    FAILED_LINES["KEGG PATHWAYS"].append([errorMessage])

        except Exception as ex:
            errorMessage = "FAILED WHILE PROCESSING PATHWAY KGML FILE [" + file_name + "]: " + str(ex)

    TOTAL_FEATURES["KEGG PATHWAYS"]=total_lines

    # Select only KEGG pathways
    generatePathwaysNetwork({k: v for k,v in ALL_PATHWAYS.items() if v["source"] == "KEGG"})

    #STEP 4. PROCESS THE VERSION FILES
    file_name= DATA_DIR + "KEGG_VERSION"
    file = open(file_name, 'r')
    ALL_VERSIONS["KEGG"] = {"name" : "KEGG", "date" : file.readline().rstrip().split("\t")[1]}
    file.close()

    file_name= DATA_DIR + "mapping/MAP_VERSION"
    file = open(file_name, 'r')
    ALL_VERSIONS["MAPPING"] = {"name" : "MAPPING", "date" : file.readline().rstrip().split("\t")[1]}
    file.close()

def generatePathwaysNetwork(ALL_PATHWAYS):
    NODES = {}
    EDGES = []

    #***********************************************************************************
    #* STEP 1. FIRST PROCESS THE FILE WITH ALL PATHWAYS AND GENERATE A DIAGONAL MATRIX
    #          mmu00100 -> [ mmu00101 = 0, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00102 -> [                             mmu00103 = 0,...]
    #***********************************************************************************
    file_name = DATA_DIR + "../common/pathways_all.list"
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        for row in rows:
            path_id = row[0].replace("map","")
            path_name = row[1]
            NODES[path_id] = {"data": {"id": SPECIE + path_id, "label": path_name, "total_features": 0}, "group" : "nodes"}
    csvfile.close()
    all_pathways = sorted(NODES.keys())

    pathways_matrix = {}
    while len(all_pathways) > 0:
        current_path = all_pathways[0]
        del all_pathways[0]
        pathways_matrix[current_path] = dict(zip(all_pathways, [0]*len(all_pathways)))

    #***********************************************************************************
    #* STEP 2. PROCESS THE PATHWAYS CLASSIFICATION FILE AND ADD THE PARENT NODES AND UPDATE
    #          THE PATHWAYS parent FIELD
    #***********************************************************************************
    file_name= DATA_DIR + "../common/pathways_classification.list"
    csvfile = open(file_name, 'r')
    mainClassification=""; secondClassification=""
    mainClassificationID=0; secondClassificationID=0
    for line in csvfile:
        line = line.rstrip().split("  ")
        if line[0][0] == "A": #main classification
            mainClassificationID+=1
            mainClassification=line[0][1:]
            NODES[str(mainClassificationID) + "A"] = {"data": {"id": mainClassification.lower().replace(" ","_"), "label": mainClassification, "is_classification" : "A"}, "group" : "nodes"}
        elif line[0][0] == "B":
            secondClassificationID+=1
            secondClassification = line[1]
            NODES[str(secondClassificationID) + "B"] = {"data": {"id": secondClassification.lower().replace(" ","_"), "parent" : mainClassification.lower().replace(" ","_"), "label": secondClassification, "is_classification" : "B"}, "group" : "nodes"}
        elif line[0][0] == "C":
            pathway_id   = line[2]
            NODES[pathway_id]["data"]["parent"] = mainClassification.lower().replace(" ", "_"),

    csvfile.close()
    #***********************************************************************************
    #* STEP 3. PROCESS THE FILE WITH THE RELATION GENE ID -> PATHWAY ID AND FILL THE MATRIX
    #          WITH THE NUMBER OF SHARED GENES
    #          mmu00100 -> [ mmu00101 = 1, mmu00102 = 0, mmu00103 = 0,...]
    #          mmu00101 -> [               mmu00102 = 5, mmu00103 = 3,...]
    #          mmu00102 -> [                             mmu00103 =20,...]
    #***********************************************************************************
    file_name = DATA_DIR + "gene2pathway.list"

    # Read the file into a dictionary to not rely on the KEGG
    # return format.
    gene2pathway = defaultdict(set)

    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')

        for row in rows:
            pathwayId = normaliseKeggPathwayId(row[1], SPECIE)
            if pathwayId:
                gene2pathway[row[0]].add(pathwayId)

    # Process the info
    for gene, associated_paths in gene2pathway.items():
        while len(associated_paths) > 0:
            current_path = associated_paths.pop()
            for other_path in associated_paths:
                try:
                    pathways_matrix[current_path][other_path] += 1
                except:
                    try:
                        pathways_matrix[other_path][current_path] += 1
                    except:
                        # Counted, not printed. This fires once per pathway PAIR, so a
                        # single species emitted 235 copies of the same 140-character
                        # sentence -- 33 KB, and the largest single contributor to
                        # install.log. The advice in it is identical every time; what an
                        # operator needs is the count and one example.
                        UNKNOWN_PATHWAY_PAIRS.append((current_path, other_path))


    # Free memory
    del gene2pathway

    #***********************************************************************************
    #* STEP 3B. THE SAME, FOR COMPOUNDS
    #          Two pathways that share a metabolite share a biological feature. The
    #          matrix counted genes only, so a metabolomics user asking for "shared
    #          biological features" got edges drawn from data they never submitted --
    #          and none at all between two pathways joined solely by their compounds.
    #***********************************************************************************
    #          ALL_PATHWAYS holds every source installed for the species, and NODES
    #          here is keyed by the bare 5-digit map number, so the ids are narrowed to
    #          KEGG's and normalised before they are used as keys.
    keggPathways = {pathwayID: pathway for pathwayID, pathway in ALL_PATHWAYS.items()
                    if pathway.get("source") == "KEGG"}
    compound2pathway = {}
    for compoundID, pathwayIDs in indexCompoundsByPathway(keggPathways).items():
        normalised = set(filter(None, (normaliseKeggPathwayId(pathwayID, SPECIE)
                                       for pathwayID in pathwayIDs)))
        if len(normalised) > 1:
            compound2pathway[compoundID] = normalised
    accumulateSharedFeatures(pathways_matrix, compound2pathway)
    del compound2pathway

    #***********************************************************************************
    #* STEP 4. GET THE NUMBER OF GENES FOR EACH PATHWAY
    #***********************************************************************************
    file_name = DATA_DIR + "pathway2gene.list"
    previous_pathway=""
    nGenes = 0
    with open(file_name, "r") as csvfile:
        rows = csv.reader(csvfile, delimiter='\t')
        for row in rows:
            path_id = normaliseKeggPathwayId(row[0], SPECIE)
            if path_id != previous_pathway and previous_pathway!= "":
                try:
                    NODES[previous_pathway]["data"]["total_features"] += nGenes
                except:
                    # Same aggregation as above: one line per pathway, same advice each
                    # time. Reported once, with a count, by summarisePathwayGaps().
                    PATHWAYS_WITHOUT_NODES.append(previous_pathway)
                nGenes=0
            nGenes+=1
            previous_pathway = path_id
        #LAST PATHWAY
        NODES[previous_pathway]["data"]["total_features"] += nGenes
    csvfile.close()

    #***********************************************************************************
    #* STEP 4B. AND THE NUMBER OF COMPOUNDS
    #          A separate field, not folded into total_features: that name has meant
    #          "genes" since Paintomics 3 and is read by clusters.py and by any client
    #          that has not reloaded, so redefining it would silently move their filter.
    #***********************************************************************************
    for pathwayID, pathway in keggPathways.items():
        node = NODES.get(normaliseKeggPathwayId(pathwayID, SPECIE))
        if node is not None:
            node["data"]["total_compounds"] = len(
                set(compound.get("id") for compound in pathway.get("compounds", [])
                    if compound.get("id")))

    #***********************************************************************************
    #* STEP 5. BULK THE MATRIX INTO JSON:
    #          FOR EACH PATHWAY ID AND FOR EACH POSITION WITH NON ZERO (SHARE AT LEAST 1 GENE), CREATE AN EDGE
    #***********************************************************************************
    already_linked_pathways={}
    for path_id, shared_genes in pathways_matrix.items():
        #First create the edges based on the links between networks (extracted from KGML files)
        if SPECIE + path_id in ALL_PATHWAYS:
            relatedPathways = ALL_PATHWAYS[SPECIE + path_id]["relatedPathways"]
            for other_path_id in relatedPathways:
                if not path_id + "-" + other_path_id["id"] in already_linked_pathways:
                    EDGES.append({"data": {"id": path_id + "-" + other_path_id["id"], "source": SPECIE + path_id, "target": SPECIE + other_path_id["id"], "weight": 1, "class": 'l'}, "group":"edges"})
                    #Avoid repeated edges (including the opposite links)
                    already_linked_pathways[path_id + "-" + other_path_id["id"]] = 1
                    already_linked_pathways[other_path_id["id"]+ "-" + path_id] = 1
        #Add the edges based on the existance of shared genes
        for other_path_id, n_shared_genes in shared_genes.items():
            if n_shared_genes > 0:
                EDGES.append({"data": {"id": path_id + "-" + other_path_id, "source": SPECIE + path_id, "target": SPECIE + other_path_id, "weight": n_shared_genes, "class": 's'}, "group":"edges"})

    #***********************************************************************************
    #* STEP 6 SAVE THE NETWORK TO A FILE
    #***********************************************************************************
    network = {
        "nodes": list(NODES.values()),
        "edges": EDGES
    }
    csvfile = open(DATA_DIR + "pathways_network.json", 'w')
    csvfile.write(json.dumps(network, separators=(',',':')) + "\n")
    csvfile.close()


def mergeNetworkFiles():
    # Other files (MapMan or others)
    other_files = glob.glob(DATA_DIR + "pathways_network_*.json")

    if other_files:
        # Initialize the final dictionary in the way:
        # { DB: {network_info}, DB2: {network2_info}, ...}
        network_data = {}

        # Load KEGG network file
        with open(DATA_DIR + "pathways_network.json", 'r+') as kegg_handler:
            # Append KEGG data
            network_data["KEGG"] = json.load(kegg_handler)

            # Load each other databases
            for db_file in other_files:
                # Extract the DB name
                db_name = re.search(r"pathways_network_(.*)\.json", db_file).group(1)

                with open(db_file, 'r') as db_handler:
                    network_data[db_name] = json.load(db_handler)

            # Override the old contents with the new ones
            kegg_handler.seek(0)
            kegg_handler.write(json.dumps(network_data, separators=(',', ':')) + "\n")
            kegg_handler.truncate()


#**************************************************************************
# OTHER DATABASES
#**************************************************************************
def printResults():
    stderr.write("\n\n\n")
    stderr.write("\nVALID FEATURES LINES    : " + str(len(ALL_ENTRIES)))
    try:
        for key, value in FAILED_LINES.items():
            stderr.write("\nERRONEOUS " + str(key) + " LINES : " + str(len(value)) + " of " + str(TOTAL_FEATURES[key]) + " [" + str(int(len(value)/float(TOTAL_FEATURES.get(key, 0.1))*100)) +"%]")
        stderr.write("\n\n")

    except Exception:
        stderr.write("\nERRONEOUS in print result: " + str(key) )

def dumpDatabase():
    # Every species build funnels through here, so this is the one place that can
    # decide whether what was assembled is worth installing. Both calls are cheap and
    # neither touches the database -- assertInstallable raises before anything is
    # written if the result would be unusable.
    summariseBuild()
    assertInstallable()

    #STEP1. GENERATE THE TABLE feature id --> [transcripts ids]

    # Remove the file if already exists to avoid adding
    # at the end of an invalid file.
    dump_xref_file = "/tmp/xref.tmp"

    if os.path.exists(dump_xref_file):
        os.remove(dump_xref_file)

    # To avoid depleting the ram completely, repeat the process for each database
    orphanFeatures = 0

    for dbid, db_values in transcript2xref.items():
        if VERBOSE:
            stderr.write("\nDumping database " + str(dbid))

        xref2xref = defaultdict(list)

        for transcriptID, value in db_values.items():
            for feature_id in value:
                xref2xref[feature_id].append(value)

        #STEP 2. DUMP THE xref TABLE INTO A FILE
        # Note: open the file for appending
        file = open(dump_xref_file, 'a')

        for elem in xref[dbid].values():
            item = elem.__dict__
            # item["mates"] = list(xref2xref.get(elem.getID(), []))
            item["mates"] = list(set(itertools.chain.from_iterable(xref2xref.get(elem.getID(), []))))

            if(len(item["mates"])> 0):
                file.write(json.dumps(item, separators=(',',':')) + "\n")
            else:
                # One line per unlinked feature was the single largest contributor to
                # install.log. It is a normal outcome for tens of thousands of features,
                # so count it and report the total once.
                orphanFeatures += 1
                if VERBOSE:
                    stderr.write("No transcripts detected for " + elem.display_id + " ["+ elem.description + "]\n")

        file.close()

    if orphanFeatures:
        stderr.write("\n  note: %d feature(s) had no linked transcript and were not dumped "
                     "(set PAINTOMICS_INSTALL_VERBOSE=1 to list them)\n" % orphanFeatures)

    #STEP 2. DUMP THE transcript2xref TABLE INTO A FILE
    file = open("/tmp/dbname.tmp", 'w')

    for elem in dbname.values():
        file.write(json.dumps(elem.__dict__, separators=(',',':')) + "\n")
    file.close()

    #STEP 3. DUMP THE pathways TABLE INTO A FILE
    file = open("/tmp/pathways.tmp", 'w')

    error_tolerance = int(len(list(ALL_PATHWAYS.items())) * 0.05)
    for elem in ALL_PATHWAYS.values():
        try:
            file.write(json.dumps(elem, cls=SetEncoder, separators=(',',':')) + "\n")
        except Exception as e:
            stderr.write(f"Error when dumping the pathways: {str(e)}\n")
            error_tolerance-=1
            if error_tolerance == 0:
                raise Exception("Too many errors while installing the pathways information. Aborting.")

    file.close()

    #STEP 4. DUMP THE VERSIONS TABLE INTO A FILE
    file = open("/tmp/versions.tmp", 'w')
    for elem in ALL_VERSIONS.values():
        file.write(json.dumps(elem, separators=(',',':')) + "\n")
    file.close()

def dumpErrors():
    #STEP 1. DUMP THE ERROR TABLE INTO A FILE
    file = open("/tmp/errors.tmp", 'w')

    try:
        for key, value in FAILED_LINES.items():
            if len(value) > 0:
                file.write("#************************************\n#\n# " + key + "\n#\n#************************************\n")
                for line in value:
                    file.write(line[0] + "\n")
                    file.write("\t" + "\t".join(line[1:]) + "\n")
                file.write("\n\n\n")
    except Exception as ex:
        raise ex
    finally:
        file.close()

def runMongoImport(database, collection, filePath):
    """Bulk-load a collection with mongoimport.

    --host and --port are passed explicitly. Without them mongoimport defaults
    to localhost, which is only correct when MongoDB happens to run beside the
    installer. In the containerised deployment the database is a separate
    service, so every import silently targeted the wrong host -- and because
    these run through `check_call(..., shell=True)`, the only symptom was a
    non-zero exit status with no indication that the address was the problem.
    """
    from conf.serverconf import MONGODB_HOST, MONGODB_PORT

    command = ("mongoimport --host " + str(MONGODB_HOST) +
               " --port " + str(MONGODB_PORT) +
               " --db " + database +
               " --collection " + collection +
               " --drop --file " + filePath)
    stderr.write("  " + command + "\n")
    check_call(command, shell=True)


# Pathway sources this builder produces. Anything else found in the shared
# `kegg` collection was put there by a separate installer and must survive a
# rebuild -- see preserveForeignPathways().
BUILDER_PATHWAY_SOURCES = ("KEGG", "Reactome", "MapMan")

FOREIGN_PATHWAY_BACKUP = "/tmp/foreign_pathways.tmp"


def preserveForeignPathways(database):
    """Stream pathway documents this builder does NOT produce out to disk.

    The `kegg` collection is shared by every pathway source, but only KEGG,
    Reactome and MapMan are rebuilt here -- and runMongoImport passes --drop.
    OmniPath scopes its own write to `source: OmniPath` precisely so it cannot
    damage the others, yet the reverse was never true: a routine KEGG/Reactome
    rebuild silently deleted all of its documents, and since nothing in the
    tree calls omnipathInstaller, nothing put them back.

    Documents are streamed through a file rather than held in a list so the
    cost is bounded by the cursor batch, not by the size of the foreign source.
    `_id` is preserved via bson.json_util so restored documents keep their
    identity. Returns the number of documents written.
    """
    from bson import json_util

    query = {"source": {"$nin": list(BUILDER_PATHWAY_SOURCES)}}
    written = 0
    with open(FOREIGN_PATHWAY_BACKUP, "w") as handle:
        for document in database.kegg.find(query, no_cursor_timeout=False).batch_size(200):
            handle.write(json_util.dumps(document))
            handle.write("\n")
            written += 1

    stderr.write("  preserved " + str(written) + " non-builder pathway documents\n")
    return written


def restoreForeignPathways(database, expected):
    """Re-insert the documents preserved by preserveForeignPathways().

    Raises if the count does not match: losing a source silently is exactly the
    failure this function exists to prevent, so a partial restore must be loud.
    """
    if expected == 0:
        return

    from bson import json_util

    batch, restored = [], 0
    with open(FOREIGN_PATHWAY_BACKUP, "r") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            batch.append(json_util.loads(line))
            if len(batch) >= 200:
                database.kegg.insert_many(batch)
                restored += len(batch)
                batch = []
    if batch:
        database.kegg.insert_many(batch)
        restored += len(batch)

    if restored != expected:
        raise Exception("Restored %d of %d non-builder pathway documents; "
                        "the shared 'kegg' collection is now incomplete."
                        % (restored, expected))

    stderr.write("  restored " + str(restored) + " non-builder pathway documents\n")


def createDatabase():
    try:
        runMongoImport(SPECIE + "-paintomics", "xref", "/tmp/xref.tmp")
        runMongoImport(SPECIE + "-paintomics", "dbname", "/tmp/dbname.tmp")

        from pymongo import MongoClient as _MongoClient
        from conf.serverconf import MONGODB_HOST as _HOST, MONGODB_PORT as _PORT
        _db = _MongoClient(_HOST, _PORT)[SPECIE + "-paintomics"]
        foreignCount = preserveForeignPathways(_db)

        runMongoImport(SPECIE + "-paintomics", "kegg", "/tmp/pathways.tmp")

        restoreForeignPathways(_db, foreignCount)

        runMongoImport(SPECIE + "-paintomics", "versions", "/tmp/versions.tmp")

        from pymongo import MongoClient, ASCENDING
        from conf.serverconf import MONGODB_HOST, MONGODB_PORT
        client = MongoClient(MONGODB_HOST, MONGODB_PORT)

        db = client[SPECIE + "-paintomics"]

        db.xref.create_index([("dbname_id", ASCENDING),("_id", ASCENDING)])
        db.xref.create_index([("display_id", ASCENDING)])
        # DatabaseAvailability reads distinct("source") from every organism
        # on every /organism_databases cache miss; without this index that
        # is a scan of every pathway document (DatabaseAvailability.
        # PATHWAY_SOURCE_INDEX has the numbers). mongoimport recreated the
        # collection just above, so the index has to be recreated here.
        db.kegg.create_index([("source", ASCENDING)])

    except CalledProcessError as ex:
        raise ex
    except Exception as ex:
        raise ex

def createGlobalDatabase():
    try:
        runMongoImport("global-paintomics", "versions", "/tmp/versions.tmp")

        createCompoundsCollection()

    except CalledProcessError as ex:
        raise ex
    except Exception as ex:
        raise ex

def createCompoundsCollection():
    try:
        runMongoImport("global-paintomics", "kegg_compounds", "/tmp/compounds.tmp")

        from pymongo import MongoClient, TEXT
        from conf.serverconf import MONGODB_HOST, MONGODB_PORT
        client = MongoClient(MONGODB_HOST, MONGODB_PORT)
        db = client["global-paintomics"]
        db.kegg_compounds.create_index([("name", TEXT)])

    except CalledProcessError as ex:
        raise ex
    except Exception as ex:
        raise ex

def describeBadDownload(path):
    """Return why `path` is not a usable download, or None if it looks fine.

    Every downloader here writes straight into the final filename with curl invoked
    WITHOUT -f, so a 404 page, a redirect stub or a BioMart error was saved as data and
    the caller returned True. The build then parsed HTML as TSV, accumulated
    FAILED_LINES nobody checks, and loaded a near-empty ID-mapping table into MongoDB
    with --drop -- an install that reports SUCCESS and silently unmaps every gene.
    """
    if not os.path.isfile(path):
        return "file was not created"
    if os.stat(path).st_size == 0:
        return "file is empty"

    with open(path, "rb") as handle:
        head = handle.read(4096)

    # BioMart answers HTTP 200 with a plain-text error body, so -f cannot catch it.
    if head.lstrip().startswith(b"Query ERROR"):
        return "BioMart returned an error: " + head.decode("utf-8", "replace").strip().splitlines()[0]

    # An HTML body where tabular or compressed data was expected is an error page.
    if not path.endswith((".gz", ".zip", ".png", ".jpg")):
        sniff = head.lstrip()[:200].lower()
        if sniff.startswith((b"<!doctype html", b"<html", b"<?xml version=\"1.0\" encoding=\"utf-8\"?><!doctype html")):
            return "server returned an HTML page instead of data"

    # A gzip file that does not start with the gzip magic number is not a gzip file.
    if path.endswith(".gz") and not head.startswith(b"\x1f\x8b"):
        return "expected gzip data but the file does not start with the gzip magic number"

    return None


def _curlToTemp(curlArgs, outputName, description):
    """Run curl into a .part file, validate it, then atomically put it in place."""
    directory = os.path.dirname(outputName)
    if directory:
        os.makedirs(directory, exist_ok=True)

    tmpName = outputName + ".part"
    try:
        # -f: fail on 4xx/5xx instead of writing the error body to the output file.
        # -L: follow redirects; without it a 301 wrote an empty stub and returned 0.
        check_call(curlArgs + ["-o", tmpName])
        problem = describeBadDownload(tmpName)
        if problem:
            raise Exception(description + ": " + problem)
        os.replace(tmpName, outputName)
        return True
    finally:
        if os.path.exists(tmpName):
            os.remove(tmpName)


def _sharedCachePath(URL, fileName):
    """Where a URL's single shared copy lives, or None if caching is unavailable.

    Several resources are whole-database dumps that every organism downloads
    independently: NCBI's gene2refseq.gz is 1,977 MB and is requested by 15 of the
    configured organisms, so a full rebuild transfers ~30 GB of byte-identical data and
    stores 15 copies of it. The URL fully determines the content, so fetch it once and
    hard-link the rest.
    """
    try:
        from conf.serverconf import KEGG_DATA_DIR
    except Exception:
        return None
    if not KEGG_DATA_DIR:
        return None
    import hashlib
    digest = hashlib.sha1((URL + fileName).encode("utf-8")).hexdigest()[:16]
    cacheDir = os.path.join(KEGG_DATA_DIR, "download", "_shared_cache")
    return os.path.join(cacheDir, digest + "_" + os.path.basename(fileName))


def _linkOrCopy(source, destination):
    """Hard-link source to destination, falling back to a copy across filesystems."""
    if os.path.exists(destination):
        os.remove(destination)
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def downloadFile(URL, fileName, outputName, delay, maxTries, checkIfExists=False):
    stderr.write("DOWNLOADING " + URL + fileName + "\n")

    # A cached file is only reusable if it is actually valid. Accepting any non-empty
    # file meant an error page saved once was reused forever, so a failed run stayed
    # broken until the download directory was deleted by hand.
    if checkIfExists and os.path.isfile(outputName) and describeBadDownload(outputName) is None:
        return True

    # Reuse a copy another organism already fetched from the same URL. Validated with
    # the same check as a fresh download, so a bad cached file cannot be inherited.
    cachePath = _sharedCachePath(URL, fileName)
    if cachePath and os.path.isfile(cachePath) and describeBadDownload(cachePath) is None:
        stderr.write("  * REUSING shared copy " + cachePath + "\n")
        _linkOrCopy(cachePath, outputName)
        return True

    lastError = None
    for _ in range(maxTries):
        wait(delay)
        try:
            _curlToTemp(
                ["curl", "-sfSL", "--connect-timeout", "300", "--max-time", "1800", URL + fileName],
                outputName, "download of " + fileName)
            # Seed the shared cache so the next organism asking for this same URL links
            # to it instead of transferring it again. Best-effort: a cache we cannot
            # write must never fail a download that already succeeded.
            if cachePath:
                try:
                    _linkOrCopy(outputName, cachePath)
                except Exception as cacheError:
                    stderr.write("  * WARNING: could not populate shared cache: " + str(cacheError) + "\n")
            return True
        except Exception as e:
            lastError = e
    raise Exception('Unable to retrieve ' + fileName + " from " + URL + ": " + str(lastError) + "\n")

def _listRemoteDirectory(url, delay, maxTries):
    """Return the filenames linked from an Ensembl FTP-over-HTTPS directory listing.

    Uses curl rather than requests, matching every other fetch in this module -- and
    curl is what the deploy image is already built around.
    """
    lastError = None
    for _ in range(maxTries):
        wait(delay)
        try:
            listing = check_output(
                ["curl", "-sfSL", "--connect-timeout", "60", "--max-time", "180", url],
                universal_newlines=True)
            # Directory listings link each entry; ignore the "Parent Directory" link and
            # anything with a query string.
            return [name for name in re.findall(r'href="([^"?][^"]*)"', listing)
                    if not name.startswith("/") and not name.startswith("..")]
        except Exception as exc:
            lastError = exc
    raise Exception("Unable to list " + url + ": " + str(lastError))


def resolveEnsemblTsvUrl(resource, delay, maxTries):
    """Resolve the current URL of an organism's Ensembl cross-reference TSV.

    Ensembl retired BioMart: POSTing to www/plants.ensembl.org/biomart/martservice now
    answers HTTP 405 with a 31-byte body, which the old downloader wrote to disk as the
    mapping file and reported as a successful download. The equivalent data is published
    as per-release TSV dumps instead.

    Neither the release number nor the filename is stable -- the file is named
    <Species>.<assembly>.<release>.entrez.tsv.gz, so all three move. Resolve them at run
    time rather than pinning them in 16 config files that would silently rot: vertebrates
    have no `current_tsv` symlink (verified 404), so pick the highest release-N; Ensembl
    Genomes does publish `current`, so use it directly.
    """
    division = resource.get("division", "vertebrates")
    speciesDir = resource.get("species-dir")
    if not speciesDir:
        raise Exception("Ensembl resource is missing 'species-dir'")

    if division == "vertebrates":
        baseUrl = resource.get("url", "https://ftp.ensembl.org/pub/")
        releases = []
        for entry in _listRemoteDirectory(baseUrl, delay, maxTries):
            match = re.match(r'^release-(\d+)/?$', entry)
            if match:
                releases.append(int(match.group(1)))
        if not releases:
            raise Exception("No release-N directories found under " + baseUrl)
        directoryUrl = baseUrl + "release-" + str(max(releases)) + "/tsv/" + speciesDir + "/"
    else:
        # Ensembl Genomes (plants, protists, fungi...) keeps a stable `current`.
        baseUrl = resource.get("url", "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/")
        directoryUrl = baseUrl + division + "/tsv/" + speciesDir + "/"

    wanted = resource.get("xref-type", "entrez")
    candidates = [name for name in _listRemoteDirectory(directoryUrl, delay, maxTries)
                  if name.endswith("." + wanted + ".tsv.gz")]
    if not candidates:
        raise Exception("No *." + wanted + ".tsv.gz found in " + directoryUrl)
    return directoryUrl + sorted(candidates)[0]


def downloadEnsemblMapping(resource, outputName, delay, maxTries):
    """Fetch Ensembl cross-references and write them in the 4-column shape the build expects.

    `processEnsemblData` reads exactly row[0..3] = gene, entrez, peptide, transcript,
    which is what the old BioMart query produced. The TSV dump carries the same fields in
    a different order (gene, transcript, protein, xref, db_name, ...), so translate here
    and leave the parser untouched.

    The dump writes "-" for an absent value where BioMart wrote an empty string, and the
    parser tests `!= ""` -- passing "-" through would register a cross-reference whose
    identifier is literally "-" for every gene without a peptide.
    """
    import gzip

    url = resolveEnsemblTsvUrl(resource, delay, maxTries)
    stderr.write("DOWNLOADING " + url + "\n")

    tmpGz = outputName + ".tsv.gz.part"
    tmpOut = outputName + ".part"
    try:
        _curlToTemp(["curl", "-sfSL", "--connect-timeout", "300", "--max-time", "1800", url],
                    tmpGz, "Ensembl TSV " + url)

        # One .entrez.tsv.gz mixes several xref sources in its db_name column. Arabidopsis
        # carries 17,674 real EntrezGene rows alongside 47,058 EntrezGene_trans_name rows,
        # whose xref is a TRANSCRIPT NAME ("AT1G30814-203"), not a gene id. Loading those
        # would register 47k transcript names as Entrez gene identifiers, so keep only the
        # exact db_name we asked for.
        # May be a single name or several: the .uniprot dump splits reviewed and
        # unreviewed accessions across Uniprot/SWISSPROT and Uniprot/SPTREMBL, and both
        # are real UniProt identifiers.
        wantedDb = resource.get("xref-db", "EntrezGene")
        wantedDbs = {wantedDb} if isinstance(wantedDb, str) else set(wantedDb)

        written = 0
        skippedDb = 0
        with gzip.open(tmpGz, "rt", encoding="utf-8", errors="replace") as source, \
             open(tmpOut, "w", encoding="utf-8") as target:
            for lineNumber, line in enumerate(source):
                fields = line.rstrip("\n").split("\t")
                if lineNumber == 0 and fields[0] == "gene_stable_id":
                    continue  # header
                if len(fields) < 5:
                    continue
                gene, transcript, protein, xref, dbName = fields[0], fields[1], fields[2], fields[3], fields[4]
                if dbName not in wantedDbs:
                    skippedDb += 1
                    continue
                if not transcript or transcript == "-":
                    continue  # the parser keys everything off the transcript
                blank = lambda value: "" if value == "-" else value
                target.write("\t".join([blank(gene), blank(xref), blank(protein), transcript]) + "\n")
                written += 1

        if written == 0:
            raise Exception("Ensembl TSV " + url + " yielded no usable rows for db_name in " +
                            ", ".join(sorted(wantedDbs)))
        if skippedDb:
            stderr.write("  * SKIPPED " + str(skippedDb) + " rows from other xref sources\n")
        os.replace(tmpOut, outputName)
        stderr.write("  * WROTE " + str(written) + " ensembl mapping rows\n")
        return True
    finally:
        for leftover in (tmpGz, tmpOut):
            if os.path.exists(leftover):
                os.remove(leftover)


def downloadMapManResource(resource, outputName, delay, maxTries, checkIfExists=False):
    """Fetch a single MapMan resource from GoMapMan into `outputName`.

    MapMan inputs used to be hand-copied from a private directory on one
    machine (`/home/tian/mapman/`), which made the MapMan organisms
    unrebuildable anywhere else. They are all published by GoMapMan, whose
    export API serves them over plain HTTPS, so `resource` describes a URL
    rather than a local path.

    Unlike `downloadFile` this helper is strict on purpose: the installer
    pipes whatever it downloads straight into MongoDB, so a 404 body that is
    silently stored as the mapping file becomes a plausible-looking database
    full of HTML. Every download is therefore verified before it is accepted.

    Recognised `resource` keys, beyond the usual url/file/output:
      decompress   -- gunzip the payload after download (GoMapMan ships .gz)
      skip_header  -- drop the first line (the metabolite export has one)
      expect       -- "tsv" or "targz"; shape check applied after decompression
      local        -- absolute path to a file shipped in the source tree; used
                      instead of url/file, with no network access

    `local` exists because GoMapMan publishes gene-to-entrez for ath, sly and
    stu only. Rice's KEGG cross-link is derived rather than published, so it
    ships in osa_resources/ and is copied in here. The caller resolves it to
    an absolute path (ROOT_DIR is known there, not here). Everything after
    the fetch -- decompress, skip_header, validate, and removing a rejected
    payload -- is deliberately shared with the download path so a local
    resource cannot bypass the shape checks.
    """
    from urllib.parse import quote

    localPath = resource.get("local")

    if checkIfExists and os.path.isfile(outputName) and os.stat(outputName).st_size > 0:
        stderr.write("SKIPPING (already present) " + outputName + "\n")
        return True

    if localPath:
        if not os.path.isfile(localPath):
            raise Exception("Local MapMan resource not found: " + localPath)

        stderr.write("COPYING " + localPath + "\n")
        downloadName = outputName + (".gz" if resource.get("decompress") else "")

        try:
            shutil.copyfile(localPath, downloadName)

            if resource.get("decompress"):
                _decompressMapManResource(downloadName, outputName)

            if resource.get("skip_header"):
                _dropFirstLine(outputName)

            _validateMapManResource(outputName, resource.get("expect"))
            return True
        except Exception:
            for stale in (downloadName, outputName):
                try:
                    os.remove(stale)
                except OSError:
                    pass
            raise

    # `file` is a raw name and may contain spaces or '|', so quote it here
    # instead of expecting every caller to pre-encode its config entry.
    url = resource.get("url") + quote(resource.get("file"))

    stderr.write("DOWNLOADING " + url + "\n")

    downloadName = outputName + (".gz" if resource.get("decompress") else "")
    lastError = None

    for nTry in range(1, maxTries + 1):
        wait(delay)
        try:
            # -f: fail on 4xx/5xx instead of saving the error page as data.
            # -L: follow redirects (mapman.gabipd.org now redirects offsite).
            check_call(["curl", "-f", "-L", "--connect-timeout", "300",
                        "--max-time", "1800", url, "-o", downloadName])

            if resource.get("decompress"):
                _decompressMapManResource(downloadName, outputName)

            if resource.get("skip_header"):
                _dropFirstLine(outputName)

            _validateMapManResource(outputName, resource.get("expect"))
            return True
        except Exception as ex:
            lastError = ex
            stderr.write("  attempt " + str(nTry) + "/" + str(maxTries) +
                         " failed: " + str(ex) + "\n")
            # Never leave a rejected payload on disk. GoMapMan answers an
            # unknown path with 200 + its SPA shell rather than a 404, so a
            # retained body is HTML that a later checkIfExists run would
            # happily accept as the mapping file.
            for stale in (downloadName, outputName):
                try:
                    os.remove(stale)
                except OSError:
                    pass

    raise Exception("Unable to retrieve " + resource.get("file") + " from " +
                    resource.get("url") + " (" + str(lastError) + ")\n")


MAPMAN_STORE_URL = ("https://www.plabipd.de/portal/mapman"
                    "?p_p_id=MapManDataDownload_WAR_MapManDataDownloadportlet"
                    "&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
                    "&p_p_cacheability=cacheLevelPage&p_p_col_id=column-1&p_p_col_count=1"
                    "&_MapManDataDownload_WAR_MapManDataDownloadportlet_Show=Pathways"
                    "&_MapManDataDownload_WAR_MapManDataDownloadportlet_RessourceId={id}"
                    "&_MapManDataDownload_WAR_MapManDataDownloadportlet_Download={what}")


def augmentMapManPathways(archiveName, classificationName, manifestName, delay, maxTries):
    """Add the MapMan diagrams that GoMapMan's Paintomics export leaves out.

    GoMapMan ships 20 diagrams and they are overwhelmingly Secondary Metabolism
    and Hormones; the general maps every MapMan user actually reaches for -
    Metabolism overview, glycolysis, TCA, photosynthesis, transcription, and
    the Metabolites compound map - are simply absent. The full 3.6-era set is
    published by the MapManStore archive, so the manifest lists the missing 50
    and this step folds them into the archive the build step already reads.

    Everything downstream is untouched: this rewrites `archiveName` in place and
    appends to `classificationName`, so processMapManPathwaysData still just
    untars one file.

    All or nothing on purpose. A partial fetch would give different installs
    different pathway universes - and the pathway count is an enrichment
    denominator, so "63 diagrams today, 67 tomorrow" silently changes p-values.
    On any failure this leaves the GoMapMan archive exactly as it found it and
    says so, giving exactly two reproducible outcomes rather than a spectrum.
    """
    import json
    import tarfile
    import tempfile

    if not os.path.isfile(manifestName):
        stderr.write("No MapMan extra-diagram manifest at " + manifestName +
                     "; keeping the base diagram set only.\n")
        return 0

    with open(manifestName, "r") as handle:
        diagrams = json.load(handle).get("diagrams", [])

    if not diagrams:
        return 0

    stagingDir = tempfile.mkdtemp(prefix="mapman_extra_")
    stagedXml = os.path.join(stagingDir, "xml")
    stagedPng = os.path.join(stagingDir, "png")
    os.makedirs(stagedXml)
    os.makedirs(stagedPng)
    existing = set()

    try:
        # Names already in the archive win: those pathway ids are live in
        # MongoDB and in saved jobs, so they must not be renamed or replaced.
        with tarfile.open(archiveName, "r:gz") as archive:
            existing = set(
                os.path.basename(m.name)[:-4]
                for m in archive.getmembers()
                if m.name.startswith("xml/") and m.name.endswith(".xml"))

        wanted = [d for d in diagrams if d["name"] not in existing]
        stderr.write("\nFetching " + str(len(wanted)) + " extra MapMan diagrams "
                     "(" + str(len(diagrams) - len(wanted)) + " already in the base archive)...\n")

        for index, diagram in enumerate(wanted, 1):
            _fetchMapManDiagram(diagram, stagedXml, stagedPng, delay, maxTries)
            if index % 10 == 0:
                stderr.write("  " + str(index) + "/" + str(len(wanted)) + "\n")

        added = _repackMapManArchive(archiveName, stagedXml, stagedPng)
        _appendMapManClassification(classificationName, wanted, existing)

        stderr.write("MapMan diagrams: " + str(len(existing)) + " -> " +
                     str(len(existing) + added) + "\n")
        return added
    except Exception as ex:
        stderr.write("\nCould not assemble the extra MapMan diagrams: " + str(ex) + "\n"
                     "Continuing with the " + str(len(existing)) + " diagrams already in the "
                     "archive. It and the classification file are unchanged.\n")
        return 0
    finally:
        shutil.rmtree(stagingDir, ignore_errors=True)


def _fetchMapManDiagram(diagram, stagedXml, stagedPng, delay, maxTries):
    """Download one diagram's layout + background and normalise it to PNG."""
    name = diagram["name"]

    xmlPath = os.path.join(stagedXml, name + ".xml")
    _curlMapManStore(diagram["ressourceId"], "PathwayAnnotation", xmlPath, delay, maxTries)

    with open(xmlPath, "r", errors="replace") as handle:
        if "<Image" not in handle.read(4096):
            raise Exception("diagram '" + name + "' did not return MapMan XML")

    # The store serves backgrounds as PNG, SVG or JPEG depending on the
    # diagram's age. generateThumbnail and the client both want png/<id>.png.
    rawPath = os.path.join(stagedPng, name + ".download")
    _curlMapManStore(diagram["ressourceId"], "PathwayImage", rawPath, delay, maxTries)
    _convertToPng(rawPath, os.path.join(stagedPng, name + ".png"), name)
    os.remove(rawPath)


def _curlMapManStore(ressourceId, what, outputName, delay, maxTries):
    url = MAPMAN_STORE_URL.format(id=ressourceId, what=what)
    lastError = None

    for _ in range(maxTries):
        wait(delay)
        try:
            check_call(["curl", "-f", "-L", "--connect-timeout", "120",
                        "--max-time", "600", url, "-o", outputName])
            if os.path.isfile(outputName) and os.stat(outputName).st_size > 0:
                return
            lastError = "empty response"
        except Exception as ex:
            lastError = ex

    raise Exception("RessourceId " + str(ressourceId) + " (" + what + "): " + str(lastError))


def _convertToPng(sourceName, targetName, diagramName):
    """Render whatever the store returned as a PNG, on white."""
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True

    with open(sourceName, "rb") as handle:
        head = handle.read(1024)

    if b"<svg" in head or b"<?xml" in head:
        import cairosvg
        # These diagrams are plain vector art with no data: URIs, so the
        # default safe fetcher is fine here. White ground, not transparent:
        # the client paints coloured boxes on top of this image.
        cairosvg.svg2png(url=sourceName, write_to=targetName, background_color="white")
    else:
        image = Image.open(sourceName)
        if image.mode not in ("RGB", "RGBA", "P", "L", "LA"):
            image = image.convert("RGB")
        image.save(targetName, "PNG")

    # A diagram that renders blank is worse than one that is missing: it looks
    # deliberate. Reject anything with no tonal range at all.
    rendered = Image.open(targetName).convert("RGB")
    if max(high - low for low, high in rendered.getextrema()) < 12:
        raise Exception("diagram '" + diagramName + "' rendered blank")


def _repackMapManArchive(archiveName, stagedXml, stagedPng):
    """Rewrite the pathways tarball as base + staged extras, atomically."""
    import tarfile
    import tempfile

    handle, tempName = tempfile.mkstemp(suffix=".tar.gz",
                                        dir=os.path.dirname(os.path.abspath(archiveName)))
    os.close(handle)
    added = 0

    try:
        with tarfile.open(archiveName, "r:gz") as source:
            with tarfile.open(tempName, "w:gz") as target:
                for member in source.getmembers():
                    extracted = source.extractfile(member) if member.isfile() else None
                    target.addfile(member, extracted)

                for folder, prefix in ((stagedXml, "xml"), (stagedPng, "png")):
                    for entry in sorted(os.listdir(folder)):
                        target.add(os.path.join(folder, entry), arcname=prefix + "/" + entry)
                        if prefix == "xml":
                            added += 1

        os.replace(tempName, archiveName)
        return added
    except Exception:
        if os.path.isfile(tempName):
            os.remove(tempName)
        raise


def _appendMapManClassification(classificationName, diagrams, existing):
    """Give each added diagram a category row; without one it reads 'Not classified'."""
    if not os.path.isfile(classificationName):
        return

    with open(classificationName, "r", errors="replace") as handle:
        alreadyListed = set(
            line.split("\t")[2].strip()
            for line in handle if line.count("\t") >= 2)

    rows = ["\t".join([d["primary"], d["secondary"], d["name"]])
            for d in diagrams
            if d["name"] not in alreadyListed and d["name"] not in existing]

    if not rows:
        return

    with open(classificationName, "r", errors="replace") as handle:
        needsNewline = not handle.read().endswith("\n")

    with open(classificationName, "a") as handle:
        handle.write(("\n" if needsNewline else "") + "\n".join(rows) + "\n")


def _decompressMapManResource(downloadName, outputName):
    """gunzip `downloadName` onto `outputName`, streaming to bound memory."""
    import gzip

    with gzip.open(downloadName, "rb") as compressed, open(outputName, "wb") as plain:
        shutil.copyfileobj(compressed, plain, 1024 * 1024)
    os.remove(downloadName)


def _dropFirstLine(fileName):
    """Strip a leading header row in place, streaming rather than slurping."""
    tmpName = fileName + ".tmp"
    with open(fileName, "r") as source, open(tmpName, "w") as target:
        source.readline()
        shutil.copyfileobj(source, target, 1024 * 1024)
    os.replace(tmpName, fileName)


def _validateMapManResource(fileName, expect):
    """Reject a download that is empty or is not the shape we asked for."""
    import tarfile

    if not os.path.isfile(fileName) or os.stat(fileName).st_size == 0:
        raise Exception(fileName + " is missing or empty after download")

    if expect == "targz":
        if not tarfile.is_tarfile(fileName):
            raise Exception(fileName + " is not a tar archive (server error page?)")
        return

    if expect == "tsv":
        # Read one line only; these files run to tens of MB.
        with open(fileName, "r") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if "\t" not in line:
                    raise Exception(fileName + " has no tab-separated columns; "
                                    "first line was: " + line[:120].rstrip())
                return
        raise Exception(fileName + " contains no data rows")


def wait(nSeconds):
    sleep(nSeconds)

class SetEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)

#**************************************************************************
# VARIABLE DECLARATION
#**************************************************************************
#Temporal representation for XREF TABLES
xref = defaultdict(dict)
transcript2xref= defaultdict(dict)
xref2transcript = defaultdict(dict)
dbname = {}

#STORE ALL ENTRIES TO BE SAVED
ALL_ENTRIES = {}
ALL_DBS = {}
ALL_PATHWAYS = {}
ALL_VERSIONS = {}
KEY_ENTRIES = {}
KEGG_COMPOUNDS = {}
MAPMAN_COMPOUNDS = {}

#OTHER AUXILIAR TABLES OR VARIABLES
TOTAL_FEATURES = {}
FAILED_LINES = {}

# Optional inputs that were absent or unusable. A species is built from a dozen
# independent sources and most of them only contribute one identifier type, so a
# missing one costs that type and nothing else -- it is not a reason to abandon an
# organism that would otherwise install. Each skip is recorded here with the
# consequence spelled out, reported in the build summary, and re-raised as a hard
# failure ONLY if what survives is unusable (see assertInstallable).
SKIPPED_SOURCES = []

# Set PAINTOMICS_INSTALL_VERBOSE=1 to restore the per-row progress bars and the
# per-feature "no transcripts" lines. The default is a summary: the old output was
# tens of MB of carriage returns per species, which buried the few lines that matter.
VERBOSE = os.environ.get("PAINTOMICS_INSTALL_VERBOSE", "0") == "1"

# Pathways referenced by the species data but absent from the shared KEGG reference.
# Both used to print a full sentence of identical advice per occurrence; they are
# aggregated and reported once by summarisePathwayGaps().
UNKNOWN_PATHWAY_PAIRS = []
PATHWAYS_WITHOUT_NODES = []


DATA_DIR = ""
ROOT_DIR = ""
SPECIE  = ""
EXTERNAL_RESOURCES = None
COMMON_RESOURCES = None

kegg_id_2_refseq_tid = {}
external_mapping = {}
