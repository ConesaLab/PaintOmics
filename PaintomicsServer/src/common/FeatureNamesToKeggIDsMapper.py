from pymongo import MongoClient
import logging, itertools
import os, sys, signal, time
import re
from multiprocessing import Process, Manager, RawArray
from math import ceil
from bisect import bisect_right
from re import compile as compile_re, IGNORECASE as IGNORECASE_re
from collections import defaultdict
from operator import attrgetter
from itertools import chain

from src.common.Util import chunks
from src.common.KeggInformationManager import KeggInformationManager
from src.common import JobProgress

from src.classes.FoundFeature import FoundFeature

from src.conf.organismDB import dicDatabases
from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT, MAX_THREADS, MAX_WAIT_THREADS #MULTITHREADING


# The parent's stream objects, kept alive in the fork child on purpose: see
# _refreshStandardStreamsInForkChild. Never read; never emptied.
_inheritedStreams = []


def _refreshStandardStreamsInForkChild():
    """
    The mapping workers below are forked from a server process that keeps
    serving requests on other threads. A fork can land while one of those
    threads is mid-write, and the child then inherits sys.stdout/sys.stderr
    (and logging file streams) whose buffer locks are held by a thread that
    does not exist in the child: its first print/log call blocks forever.
    CPython re-initialises logging's OWN locks after a fork, but not the
    locks inside buffered IO objects, so the streams themselves must be
    replaced. Observed in production as a worker frozen in
    PyThread_acquire_lock_timed under a progress print until the
    MAX_WAIT_THREADS timeout killed the job -- and terminate() could not
    reap it, because the child also inherits uWSGI's SIGTERM handler, so
    the default disposition is restored here as well.

    Two rules keep this hook from re-creating the very deadlock it exists
    to prevent (both were violated by its first version, and a Manager
    server child sat frozen for 32 minutes on paintomics.uv.es on
    2026-08-17, its parent's ``Manager()`` blocked forever waiting for the
    child's address, one queue worker gone until the service restarted):

      * never FLUSH an inherited stream in the child. That is what
        ``StreamHandler.setStream`` does before swapping, so handlers get
        their ``stream`` attribute assigned directly instead;
      * never DROP the last reference to an inherited stream in the child.
        A TextIOWrapper's finaliser closes it, and closing flushes -- the
        same lock again, this time from inside garbage collection. The old
        objects are parked in ``_inheritedStreams`` for the life of the
        child; whatever they buffered belongs to the parent, which still
        holds them and will write it out itself.
    """
    _inheritedStreams.extend((sys.stdout, sys.stderr))
    sys.stdout = open(1, "w", buffering=1, closefd=False)
    sys.stderr = open(2, "w", buffering=1, closefd=False)
    loggers = [logging.getLogger()] + [
        logger for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)]
    for handler in {h for logger in loggers for h in logger.handlers}:
        try:
            if isinstance(handler, logging.FileHandler):
                _inheritedStreams.append(handler.stream)
                handler.stream = handler._open()
            elif isinstance(handler, logging.StreamHandler):
                _inheritedStreams.append(handler.stream)
                handler.stream = sys.stderr
        except Exception:
            pass
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _joinAllWithinDeadline(processes, seconds):
    """
    Wait for every process in ``processes`` but never longer than ``seconds``
    IN TOTAL. ``for p in processes: p.join(seconds)`` -- the previous form --
    gives every process its own budget, so with six workers the "took too
    long" kill only fired after up to six times MAX_WAIT_THREADS (90 minutes
    at the configured 900 s), while the client kept polling.
    """
    deadline = time.monotonic() + seconds
    for process in processes:
        process.join(max(0.0, deadline - time.monotonic()))


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_refreshStandardStreamsInForkChild)

#*****************************************************************
#   _____ ____  __  __ __  __  ____  _   _
#  / ____/ __ \|  \/  |  \/  |/ __ \| \ | |
# | |   | |  | | \  / | \  / | |  | |  \| |
# | |   | |  | | |\/| | |\/| | |  | | . ` |
# | |___| |__| | |  | | |  | | |__| | |\  |
#  \_____\____/|_|  |_|_|  |_|\____/|_| \_|
#
#*****************************************************************

def getDatabasesByOrganismCode(organism):
    """
    Depending on the organism this function returns the name for the databases
    which contains the valid translations for names into valid KEGG identifiers

    @param {String} organism, the organim code e.g. mmu
    @returns {List} databaseConvertion
    """

    # dicDatabases is inside the conf file "organismDB" and should be
    # updated after installing new species with external annotation data.

    return dicDatabases.get(organism, [{'KEGG': "kegg_id"}, {'KEGG': "kegg_gene_symbol"}])

def getConnectionByOrganismCode(organism):
    """
    Devuelve la conexion a la base de datos del organismo correspondiente asi como el nombre de la tabla
    que se usara para realizar la conversion para dicho organismo y un cursor asociado a ella

    @param {String} organism
    @returns
    """
    client = MongoClient(MONGODB_HOST, MONGODB_PORT)
    db = client[organism + "-paintomics"]
    return client, db

#*****************************************************************
#   _____ ______ _   _ ______  _____
#  / ____|  ____| \ | |  ____|/ ____|
# | |  __| |__  |  \| | |__  | (___
# | | |_ |  __| | . ` |  __|  \___ \
# | |__| | |____| |\  | |____ ____) |
#  \_____|______|_| \_|______|_____/
#
#*****************************************************************
#: Identifier types that name a *gene* and only ever name one gene, so two xref
#: documents carrying the same value are the same gene by definition. These and
#: only these are safe to bridge a second hop through (see _bridgeSecondHop):
#: transcript- and peptide-level identifiers are NOT, because a shared peptide
#: can join two paralogues and would silently map a feature onto its family
#: member. Names, not ids -- the ids are per-species and resolved at call time.
#:
#: `ncbi_geneid` and `entrezgene` are the SAME identifier space under two names:
#: the KEGG builder (processKEGGMappingData) files NCBI gene ids as
#: `ncbi_geneid`, the Ensembl builder (processEnsemblData) files them as
#: `entrezgene`. A species built by both therefore carries two mate islands that
#: no hop could cross -- the KEGG island holds kegg_id and kegg_gene_symbol, the
#: Ensembl island holds everything a user typically uploads. Measured on dme
#: after its kegg_id table was built: 0 of the 1,325 FlyBase ids in a real user
#: submission reached kegg_id, though every one of them had the full path
#: FBgn0000147 -> entrezgene 41446 -> ncbi_geneid 41446 -> kegg_id Dmel_CG3068.
#: Listing both names is what joins the islands, and it is an identity step by
#: the same rule as the others here: an NCBI gene id names one gene.
GENE_LEVEL_BRIDGE_DATABASES = ("entrezgene", "ncbi_geneid", "ensembl_gene", "kegg_id")

#: One tiny query per species per process, and mapping runs in forked workers
#: that each map tens of thousands of names in batches of a few hundred.
_bridgeIDCache = {}


def resolveBridgeDatabaseIds(db, databaseConvertion_id=None):
    """The dbname ids of the gene-level databases this species actually has.

    Returns [] when the species carries none of them, which turns the second
    hop off entirely rather than guessing a bridge.
    """
    cached = _bridgeIDCache.get(db.name)
    if cached is None:
        cached = [row.get("_id") for row in
                  db.dbname.find({"dbname": {"$in": list(GENE_LEVEL_BRIDGE_DATABASES)}},
                                 {"_id": 1})]
        _bridgeIDCache[db.name] = cached
    # Bridging through the target itself cannot add anything: a name reaching
    # it through a bridge document would have reached it on the first hop.
    return [identifier for identifier in cached if identifier != databaseConvertion_id]


# Documents per cursor batch for the xref lookups. Every lookup here is
# bounded -- at most 250 names or 5,000 mate ids per query -- so one batch of
# this size always holds the whole result and no getMore follows. The server
# caps a batch at 16 MB regardless; these documents are a few hundred bytes.
XREF_BATCH_SIZE = 10000


def _bridgeSecondHop(db, unresolvedDocuments, databaseConvertion_id, bridgeIDs):
    """Resolve names whose own mate group cannot reach the target database.

    ``mates`` is built by *shared transcript* (AdminTools/scripts/
    common_build_database.py), and Ensembl transcripts and RefSeq transcripts
    are near-disjoint sets. So an ``ensembl_gene`` document's group carries the
    Ensembl side plus the gene-level identifiers, while the UniProt and RefSeq
    accessions hang off the *entrezgene* document's group instead. A single hop
    therefore cannot see them: measured on mmu, ENSMUSG00000000037 (Scml2)
    reaches entrezgene 107815 but no UniProt, while 107815's own group carries
    B1AVB4/I6L9E4/Q8BYC8. That cost OmniPath (uniprot_acc) 54.9% translation
    where 87.7% was reachable, and hit Reactome the same way.

    This walks exactly one further hop, and only for names the first hop
    missed, so resolved names keep their existing answer and their cost. The
    bridge is restricted to gene-level identifiers, which makes the extra hop
    an identity step rather than a similarity step -- validated on 3,000 mmu
    features: 0 contradictions with the one-hop answer, and all 1,558 recovered
    (name, accession) pairs agreed with the input's own gene symbol.

    Two plain indexed finds, never an aggregation: mongod 4.4 on
    paintomics.uv.es cannot index a $lookup sub-pipeline and turns one into a
    collection scan of the 1.1M-document xref.

    @returns {Dict} name -> list of translated identifiers, hits only.
    """
    if not unresolvedDocuments or not bridgeIDs:
        return {}

    # Hop 1b: which bridge identifiers does each unresolved name carry?
    mateIDs = list({mate for document in unresolvedDocuments
                    for mate in (document.get("mates") or [])})
    bridgeValueByMate = {}
    for start_ in range(0, len(mateIDs), 5000):
        for hit in db.xref.find({"dbname_id": {"$in": bridgeIDs},
                                 "_id": {"$in": mateIDs[start_:start_ + 5000]}},
                                {"display_id": 1, "dbname_id": 1}, batch_size=XREF_BATCH_SIZE):
            bridgeValueByMate[hit.get("_id")] = (hit.get("dbname_id"), str(hit.get("display_id")))

    if not bridgeValueByMate:
        return {}

    bridgeValues = list({value for _, value in bridgeValueByMate.values()})

    # Hop 2: the bridge documents' OWN mate groups, and the target inside them.
    bridgeMates = defaultdict(list)
    for start_ in range(0, len(bridgeValues), 5000):
        for hit in db.xref.find({"dbname_id": {"$in": bridgeIDs},
                                 "display_id": {"$in": bridgeValues[start_:start_ + 5000]}},
                                {"display_id": 1, "mates": 1}, batch_size=XREF_BATCH_SIZE):
            bridgeMates[str(hit.get("display_id"))].extend(hit.get("mates") or [])

    farMateIDs = list({mate for mates in bridgeMates.values() for mate in mates})
    targetByMate = {}
    for start_ in range(0, len(farMateIDs), 5000):
        for hit in db.xref.find({"dbname_id": databaseConvertion_id,
                                 "_id": {"$in": farMateIDs[start_:start_ + 5000]}},
                                {"display_id": 1}, batch_size=XREF_BATCH_SIZE):
            targetByMate[hit.get("_id")] = str(hit.get("display_id"))

    if not targetByMate:
        return {}

    recovered = {}
    for document in unresolvedDocuments:
        translations = []
        seen = set()
        for mate in (document.get("mates") or []):
            bridge = bridgeValueByMate.get(mate)
            if bridge is None:
                continue
            for farMate in bridgeMates.get(bridge[1], ()):
                identifier = targetByMate.get(farMate)
                # Deduplicated because a name can reach the same bridge value
                # through several mates, and each would re-emit the same hit.
                if identifier is not None and identifier not in seen:
                    seen.add(identifier)
                    translations.append(identifier)
        if translations:
            recovered[document.get("display_id")] = translations
    return recovered


def findIDsByFeaturesName(jobID, featureNames, db, databaseConvertion_id):
    """
    This function queries the MongoDB looking for the associated gene ID for the given gene name

    @param {String} jobID, the identifier for the running job, necessary to for the temporal caches
    @param {String} featureName, the name for the feature that we want to map
    @param {String} organism, the organims code
    @param  {pymongo.Database} db, the open connection with MongoDB database
    @param {String} databaseConvertion_id, identifier for the database which contains the translated feature name (e.g. entrezgene for mmu)
    @returns {List} matchedFeatures, a list of translated identifiers
    @returns {Boolean} found, True if we found at least one translation
    """
    #Check if the id is ath the cache of translation
    cachedFeatureIDs = KeggInformationManager().findBatchInTranslationCache(jobID, featureNames, "id", databaseConvertion_id)

    notCachedIds = set(featureNames).difference(set(cachedFeatureIDs.keys()))

    # Two indexed finds instead of one aggregation. The names' xref documents
    # come off the display_id index; the union of their mates is then looked
    # up with the target dbname_id on the (dbname_id, _id) index, and the
    # mates are re-walked in array order, one row per matching mate
    # occurrence -- exactly what the previous $match / $unwind / $lookup /
    # $match aggregation produced (which fetched EVERY mate document by _id
    # and only then dropped the ~90% belonging to other databases). Measured
    # identical, per-name order included, on MongoDB 8.2 and on production's
    # MongoDB 4.4, where this form is ~9x faster than the aggregation
    # (2,000 names: 4.2 s -> 0.46 s). A $lookup sub-pipeline with $expr/$in
    # was tried first: fast on 8.2, but 4.4 cannot index it and every batch
    # turned into collection scans of the 1.1M-document xref -- mapping
    # workers hung for hours on paintomics.uv.es (2026-08-17). Plain finds
    # behave the same on every server version.
    matchedFeatures = defaultdict(list)

    try:
        if len(notCachedIds):
            # batch_size: without it a find hands back 101 documents and the
            # rest come through a separate getMore round trip -- which every
            # 250-name lookup (about 101-300 documents) paid, as did the mate
            # lookups with more than 101 hits. Profiled on the whole-genome
            # input: 1,257 getMore commands for 2,687 finds, a third of the
            # mapper's time on MongoDB. One batch returns the same documents
            # in the same order.
            nameDocuments = list(db.xref.find({"display_id": {"$in": list(notCachedIds)}},
                                              {"display_id": 1, "mates": 1},
                                              batch_size=XREF_BATCH_SIZE))
            allMates = list({mate for document in nameDocuments for mate in (document.get("mates") or [])})
            hitsByID = {}
            for start_ in range(0, len(allMates), 5000):
                for hit in db.xref.find({"dbname_id": databaseConvertion_id,
                                         "_id": {"$in": allMates[start_:start_ + 5000]}},
                                        {"display_id": 1}, batch_size=XREF_BATCH_SIZE):
                    hitsByID[hit.get("_id")] = hit.get("display_id")

            for document in nameDocuments:
                translations = None
                for mate in (document.get("mates") or []):
                    if mate in hitsByID:
                        if translations is None:
                            translations = matchedFeatures[document.get("display_id")]
                        translations.append(str(hitsByID[mate]))

            # Second hop for whatever the first could not reach. Only the names
            # that missed are carried forward, so a species whose xref groups
            # are complete pays two empty finds and nothing else.
            unresolved = [document for document in nameDocuments
                          if document.get("display_id") not in matchedFeatures]
            if unresolved:
                recovered = _bridgeSecondHop(
                    db, unresolved, databaseConvertion_id,
                    resolveBridgeDatabaseIds(db, databaseConvertion_id))
                for name, translations in recovered.items():
                    matchedFeatures[name] = translations

            # Record the misses too, as empty lists -- in the returned table
            # as well as in the cache, so a forked worker hands them back to
            # the parent with its hits. Only hits used to be cached, so a name
            # with no translation in this database was sent to MongoDB again
            # by every later omic (and by every symbol pass that saw the same
            # id) although the answer cannot change within a job. An empty
            # list reads exactly like an absent entry to every consumer:
            # `if featureIDs:` is False, chain.from_iterable adds nothing,
            # `cached or [fallback]` falls back. Keyed by the same (jobID,
            # database) as the hits, so a miss in one database can never mask
            # a hit in another.
            for name in notCachedIds:
                if name not in matchedFeatures:
                    matchedFeatures[name] = []
            KeggInformationManager().updateTranslationCache(jobID, matchedFeatures, "id", databaseConvertion_id)
    except Exception as ex:
        logging.error("EXCEPTION %s", ex)
    finally:
        matchedFeatures.update(cachedFeatureIDs)

        return matchedFeatures

def _bridgeSecondHopForDatabases(db, unresolvedByTarget, bridgeIDsByTarget):
    """_bridgeSecondHop for several target databases at once.

    Of its three lookups, the first (which bridge identifiers the unresolved
    names' mates are) and the third (which of the bridge groups' far mates
    are in the target) filter a set of ids by dbname_id, so one query over
    the union of the targets' ids, split by dbname_id afterwards, returns
    every target exactly the hits its own query did. The second lookup
    (the bridge documents' own mate groups, by display_id) stays per target:
    its result is a LIST whose order is the query's, and a name's translations
    follow that order.

    Each target's bridge set excludes the target itself (resolveBridgeDatabase
    Ids), so the shared first lookup asks for the union of the bridge sets and
    each target keeps only the hits in its own.

    @returns {Dict} target -> {name: [translated identifiers]}, hits only.
    """
    targets = [target for target, documents in unresolvedByTarget.items()
               if documents and bridgeIDsByTarget.get(target)]
    recoveredByTarget = {target: {} for target in unresolvedByTarget}
    if not targets:
        return recoveredByTarget

    bridgeValueByMateAll = _bridgeHop1bShared(db, targets, unresolvedByTarget, bridgeIDsByTarget)
    bridgeValueByMate, bridgeMates, farMateIDs = _bridgeHop2PerTarget(
        db, targets, unresolvedByTarget, bridgeIDsByTarget, bridgeValueByMateAll)
    targetByMateAll = _bridgeHop3Shared(db, farMateIDs)

    for target in targets:
        if target not in bridgeMates:
            continue
        recovered = _bridgeAssembleTarget(target, unresolvedByTarget[target],
                                          bridgeValueByMate[target], bridgeMates[target],
                                          farMateIDs[target], targetByMateAll)
        if recovered is not None:
            recoveredByTarget[target] = recovered
    return recoveredByTarget


def _bridgeHop1bShared(db, targets, unresolvedByTarget, bridgeIDsByTarget):
    """Hop 1b, shared: which bridge identifiers does each unresolved name
    carry? One query over the union of the targets' bridge sets; each target
    keeps only the hits in its own set (hop 2)."""
    allBridgeIDs = list({bridge for target in targets for bridge in bridgeIDsByTarget[target]})
    mateIDs = list({mate for target in targets for document in unresolvedByTarget[target]
                    for mate in (document.get("mates") or [])})
    bridgeValueByMateAll = {}
    for start_ in range(0, len(mateIDs), 5000):
        for hit in db.xref.find({"dbname_id": {"$in": allBridgeIDs},
                                 "_id": {"$in": mateIDs[start_:start_ + 5000]}},
                                {"display_id": 1, "dbname_id": 1}, batch_size=XREF_BATCH_SIZE):
            bridgeValueByMateAll[hit.get("_id")] = (hit.get("dbname_id"), str(hit.get("display_id")))
    return bridgeValueByMateAll


def _bridgeHop2PerTarget(db, targets, unresolvedByTarget, bridgeIDsByTarget, bridgeValueByMateAll):
    """Hop 2, per target: the bridge documents' OWN mate groups. Must stay
    per target -- the result is a LIST whose order is the query's, and a
    name's translations follow that order."""
    bridgeValueByMate = {}
    bridgeMates = {}
    farMateIDs = {}
    for target in targets:
        bridges = set(bridgeIDsByTarget[target])
        mine = {}
        for document in unresolvedByTarget[target]:
            for mate in (document.get("mates") or []):
                hit = bridgeValueByMateAll.get(mate)
                if hit is not None and hit[0] in bridges:
                    mine[mate] = hit
        bridgeValueByMate[target] = mine
        if not mine:
            continue
        bridgeValues = list({value for _, value in mine.values()})
        mates = defaultdict(list)
        for start_ in range(0, len(bridgeValues), 5000):
            for hit in db.xref.find({"dbname_id": {"$in": bridgeIDsByTarget[target]},
                                     "display_id": {"$in": bridgeValues[start_:start_ + 5000]}},
                                    {"display_id": 1, "mates": 1}, batch_size=XREF_BATCH_SIZE):
                mates[str(hit.get("display_id"))].extend(hit.get("mates") or [])
        bridgeMates[target] = mates
        farMateIDs[target] = list({mate for group in mates.values() for mate in group})
    return bridgeValueByMate, bridgeMates, farMateIDs


def _bridgeHop3Shared(db, farMateIDs):
    """Hop 3, shared: the target inside the bridge groups -- one query over
    every target's far mates, split by dbname_id at assembly."""
    allFarMates = list({mate for target in farMateIDs for mate in farMateIDs[target]})
    targetByMateAll = {}
    for start_ in range(0, len(allFarMates), 5000):
        for hit in db.xref.find({"dbname_id": {"$in": list(farMateIDs)},
                                 "_id": {"$in": allFarMates[start_:start_ + 5000]}},
                                {"display_id": 1, "dbname_id": 1}, batch_size=XREF_BATCH_SIZE):
            targetByMateAll[hit.get("_id")] = (hit.get("dbname_id"), str(hit.get("display_id")))
    return targetByMateAll


def _bridgeAssembleTarget(target, unresolvedDocuments, mine, mates, targetFarMateIDs, targetByMateAll):
    """Assemble one target's recoveries: walk each unresolved document's
    mates in order, follow its bridges, and keep the first appearance of
    every translated identifier. Returns None when nothing in the bridge
    groups belongs to the target (the caller keeps its empty dict)."""
    targetByMate = {}
    for mate in targetFarMateIDs:
        hit = targetByMateAll.get(mate)
        if hit is not None and hit[0] == target:
            targetByMate[mate] = hit[1]
    if not targetByMate:
        return None
    recovered = {}
    for document in unresolvedDocuments:
        translations = []
        seen = set()
        for mate in (document.get("mates") or []):
            bridge = mine.get(mate)
            if bridge is None:
                continue
            for farMate in mates.get(bridge[1], ()):
                identifier = targetByMate.get(farMate)
                if identifier is not None and identifier not in seen:
                    seen.add(identifier)
                    translations.append(identifier)
        if translations:
            recovered[document.get("display_id")] = translations
    return recovered


def findIDsByFeaturesNameForDatabases(jobID, featureNames, db, databaseConvertion_ids):
    """findIDsByFeaturesName for several target databases at once, sharing the
    part of the work that does not depend on the target.

    The first hop asks MongoDB two questions per batch: the names' xref
    documents, and which of their mates belong to the target database. Only
    the second depends on the target, and only through a filter on dbname_id.
    Called once per database, as it was, a batch of 250 names therefore fetched
    its documents three times and sent the same ~5,000 mate ids three times,
    once per database (KEGG, Reactome, OmniPath on mmu) -- profiled on the
    whole-genome input at 296 document finds and 341 mate finds of 5,000 ids
    each, the heaviest queries of the mapping.

    Here the documents are fetched once and the mates looked up once with
    `dbname_id: {$in: targets}`, and the hits are split by target afterwards.
    Per target the answer is identical: a document has one dbname_id, so the
    hits filtered to one target are exactly the hits its own query returned;
    the documents come from the same query in the same order, so each name's
    translations keep their mate-array order; the second hop, the misses and
    the translation-cache update still run per target, unchanged.

    Sharing is only done when every target asks for the same names -- i.e.
    when the translation cache holds the same names for all of them, which is
    every batch of a job's first omic and most batches after. When the
    not-cached sets differ the targets fall back to the per-database call,
    whose query is then exactly what it always was.

    @returns {Dict} databaseConvertion_id -> {name: [translated identifiers]}
    """
    cachedByTarget = {
        target: KeggInformationManager().findBatchInTranslationCache(jobID, featureNames, "id", target)
        for target in databaseConvertion_ids}
    notCachedByTarget = {
        target: set(featureNames).difference(set(cachedByTarget[target].keys()))
        for target in databaseConvertion_ids}
    if len({frozenset(names) for names in notCachedByTarget.values()}) != 1:
        return {target: findIDsByFeaturesName(jobID, featureNames, db, target)
                for target in databaseConvertion_ids}

    notCachedIds = notCachedByTarget[databaseConvertion_ids[0]]
    matchedByTarget = {target: defaultdict(list) for target in databaseConvertion_ids}
    try:
        if len(notCachedIds):
            nameDocuments = list(db.xref.find({"display_id": {"$in": list(notCachedIds)}},
                                              {"display_id": 1, "mates": 1},
                                              batch_size=XREF_BATCH_SIZE))
            allMates = list({mate for document in nameDocuments for mate in (document.get("mates") or [])})
            hitsByID = {}
            for start_ in range(0, len(allMates), 5000):
                for hit in db.xref.find({"dbname_id": {"$in": list(databaseConvertion_ids)},
                                         "_id": {"$in": allMates[start_:start_ + 5000]}},
                                        {"display_id": 1, "dbname_id": 1}, batch_size=XREF_BATCH_SIZE):
                    hitsByID[hit.get("_id")] = (hit.get("dbname_id"), hit.get("display_id"))

            unresolvedByTarget = {}
            for target in databaseConvertion_ids:
                matchedFeatures = matchedByTarget[target]
                for document in nameDocuments:
                    translations = None
                    for mate in (document.get("mates") or []):
                        hit = hitsByID.get(mate)
                        if hit is not None and hit[0] == target:
                            if translations is None:
                                translations = matchedFeatures[document.get("display_id")]
                            translations.append(str(hit[1]))
                unresolvedByTarget[target] = [document for document in nameDocuments
                                              if document.get("display_id") not in matchedFeatures]

            # Second hop for whatever the first could not reach, all targets at
            # once (see _bridgeSecondHopForDatabases).
            recoveredByTarget = _bridgeSecondHopForDatabases(
                db, unresolvedByTarget,
                {target: resolveBridgeDatabaseIds(db, target) for target in databaseConvertion_ids})

            for target in databaseConvertion_ids:
                matchedFeatures = matchedByTarget[target]
                for name, translations in recoveredByTarget[target].items():
                    matchedFeatures[name] = translations

                for name in notCachedIds:
                    if name not in matchedFeatures:
                        matchedFeatures[name] = []
                KeggInformationManager().updateTranslationCache(jobID, matchedFeatures, "id", target)
    except Exception as ex:
        logging.error("EXCEPTION %s", ex)
    finally:
        for target in databaseConvertion_ids:
            matchedByTarget[target].update(cachedByTarget[target])

    return matchedByTarget


def resolveDatabaseIds(organism, databases, db=None):
    """
    The MongoDB `dbname` ids for the ID and gene-symbol databases of each user
    selected database. Constant per organism, so the parent resolves them once
    per mapping call and hands them to every worker instead of each of the six
    workers issuing the same 2 x n_db find_one calls.

    @returns ({dbname: id-database _id}, {dbname: symbol-database _id})
    """
    databaseConvertion = getDatabasesByOrganismCode(organism)
    gene_databases = databaseConvertion[0]
    symbol_databases = databaseConvertion[1]
    client = None
    if db is None:
        client, db = getConnectionByOrganismCode(organism)
    def _resolveOne(sourceDB, configuredTables, role):
        """The `dbname` _id of the table `organismDB` says this source translates into.

        Both lookups below used to be `find_one(...).get("_id")`. `find_one`
        returns None for a table the species does not carry, so a species whose
        organismDB entry named a table its build script never produced raised

            AttributeError: 'NoneType' object has no attribute 'get'

        from inside a dict comprehension -- no organism, no database, no table
        name. It reached the user as a bare "Oops..Internal error!" and reached
        the log the same way, so seven identical submissions were needed before
        the cause was visible. dme declared `kegg_id` while
        `dme_resources/build_database.py` had processKEGGMappingData() commented
        out; bta, ptr and acs were in the same state.

        A configuration error, not a data error: state it as one and name the
        three things needed to fix it.
        """
        table = configuredTables.get(sourceDB)
        if table is not None:
            document = db.dbname.find_one({"dbname": table}, {"item": 1, "qty": 1})
            if document is not None:
                return document.get("_id")

        raise Exception(
            "[b]" + str(organism) + " cannot translate identifiers for the " +
            str(sourceDB) + " database.[/b][br]" +
            ("Its configuration (conf/organismDB.py) names no " + role +
             " table for " + str(sourceDB) + "."
             if table is None else
             "Its configuration (conf/organismDB.py) points the " + role +
             " table at '" + str(table) + "', but the installed " + str(organism) +
             " database has no such identifier table -- it was never built.") +
            "[br]Please report this to the administrator, and in the meantime "
            "try another pathway database for this species.")

    try:
        databaseConvertion_ids = {dbname: _resolveOne(dbname, gene_databases, "identifier") for dbname in databases}
        databaseGeneSymbol_ids = {dbname: _resolveOne(dbname, symbol_databases, "gene-symbol") for dbname in databases}

        # Some databases declare THEMSELVES as their symbol database (every
        # Reactome entry and three MapMan entries in organismDB map e.g.
        # reactome_gene_id -> reactome_gene_id). Translating a feature ID
        # through the very database it came from is the identity at best, so
        # matched clones kept the user's raw input identifier as their display
        # name and the Reactome pathway views painted "ENSMUSG..." on every
        # gene box. The xref mates graph does link those feature IDs to the
        # organism's real symbol table (mmu: GNAI3 -> Gnai3 via
        # refseq_gene_symbol), so such databases borrow a configured symbol
        # database that is not its own ID database -- KEGG's by explicit
        # preference (it is the one true gene-symbol table in every current
        # organism config), never by dict order. Organisms without one (cfa)
        # keep the old behaviour.
        degenerate = [dbname for dbname in databases
                      if symbol_databases.get(dbname) == gene_databases.get(dbname)]
        realSymbolDB = next((symbolDB for dbname, symbolDB
                             in sorted(symbol_databases.items(),
                                       key=lambda entry: entry[0] != "KEGG")
                             if symbolDB != gene_databases.get(dbname)), None)
        if degenerate and realSymbolDB is not None:
            realSymbolDoc = db.dbname.find_one({"dbname": realSymbolDB}, {"item": 1, "qty": 1})
            if realSymbolDoc is not None:
                for dbname in degenerate:
                    databaseGeneSymbol_ids[dbname] = realSymbolDoc.get("_id")
    finally:
        if client is not None:
            client.close()
    return databaseConvertion_ids, databaseGeneSymbol_ids


def _handOver(target, slot, items):
    """
    Give a worker's result list to the parent. With ``slot`` set the list is
    stored at that index of a pre-sized shared list, so the parent can
    concatenate the workers' results in WORKER ORDER -- which is the input
    order, i.e. exactly what one sequential pass produces. Appending as the
    workers happened to finish (the previous form) made the order of the
    matched features depend on scheduling, and everything downstream that
    merges duplicates by keeping the first one seen (unifyAndSort on the
    compound sets, addInputGeneData/addInputCompoundData combining values)
    then differed from run to run. Without a slot (the direct, in-process
    call) the items are simply appended.
    """
    if slot is None:
        target.extend(items)
    else:
        target[slot] = items


def mapFeatureIdentifiers(jobID, organism, databases, featureList,  matchedFeatures, notMatchedFeatures, foundFeatures, enrichment, progressArray=None, progressSlot=0, databaseIds=None, cacheTables=None, resultSlot=None):
    """
    This function is used to query the database in different threads.

    @param  {String} organism, the specie code
    @param  {List}   featureList, the list of feature IDs to map
    @param  {Dict}   alreadyMatchedGenesTable, a dict shared between threads where we store the matching for identifiers
                     (will be combined later with KeggInfoManager cache)
    @param  {List}   matchedFeatures, a list shared between threads where we store the matched features
    @param  {List}   notMatchedFeatures, a list shared between threads where we store the unmatched features
    @param  {RawArray} progressArray, optional shared-memory counter allocated before
                     the fork; this worker writes its own completion percentage into
                     progressArray[progressSlot]. Defaults to None so the direct,
                     non-forked call in Job.parseGeneBasedFiles is unaffected.
    @param  {Integer} progressSlot, this worker's index in progressArray
    @param  {Tuple}  databaseIds, optional (id-database ids, symbol-database ids)
                     already resolved by the parent (see resolveDatabaseIds);
                     resolved here when None.
    @param  {List}   cacheTables, optional shared list; when given, this worker
                     appends ONE dict {dbname_id: {name: [ids]}} holding every
                     translation it looked up, so the parent can merge it into
                     the job's translation cache. A forked worker's own cache
                     writes die with the process, which is why, before this,
                     every omic of a job re-queried MongoDB for the same names.

    @returns True
    """


    #***********************************************************************************
    #* STEP 2. GET THE CORRESPONDING DATABASE FOR CURRENT SPECIES
    #***********************************************************************************
    databaseConvertion = getDatabasesByOrganismCode(organism)


    gene_databases = databaseConvertion[0]
    symbol_databases = databaseConvertion[1]

    # Use/Not use features to count the total number of items matched
    featureEnrichment = (enrichment == "features")

    client, db  = getConnectionByOrganismCode(organism)

    if databaseIds is None:
        databaseIds = resolveDatabaseIds(organism, databases, db)
    databaseConvertion_ids, databaseGeneSymbol_ids = databaseIds

    # Iterate symbol-capable databases first so the per-featureID dedup below keeps
    # the clone whose name was resolved against a real gene-symbol database
    # (e.g. KEGG→refseq_gene_symbol for ath). Otherwise a caller-provided list ordered
    # with a same-DB symbol entry first (e.g. MapMan) silently wins and the symbol
    # lookup is skipped. Callers build `databases` from a set in some paths, so the
    # order is not stable — sort here to make the behavior deterministic.
    databases = sorted(
        databases,
        key=lambda d: gene_databases.get(d) == symbol_databases.get(d)
    )

    try:
        # Save found features for each database, plus the unique between them
        matches = {db: set() for db in databases + ["Total"]}

        # Extract names from features
        featureNames = set(map(attrgetter('name'), featureList))
        # Batch size sets how often this worker can report: a completed lookup is
        # the ONLY observable event inside it, since ~98% of its runtime is these
        # MongoDB round trips and the per-feature loop below is ~2%. Measured on
        # Drago over disjoint cold slices: 2000 -> 3 anchors/5.99s, 500 -> 6
        # anchors/5.69s, 250 -> 12 anchors/5.94s (-0.9%), 100 -> 30 anchors/+2.1%.
        # That measurement was network-bound; with every lookup now answered in
        # one cursor batch and the first and second hops shared across the
        # target databases, what a batch costs is a fixed set of round trips
        # (names, mates, symbols, bridges) and the count of batches is what is
        # left to cut: 1,000 names per batch is a quarter of the round trips of
        # 250 for the same documents, and a worker's share of a whole-genome
        # omic (5,000 names at four workers) still reports five times. The
        # result does not depend on how the names are batched: every name is
        # answered from its own documents, and the bridge groups a batch walks
        # are the same groups whichever batch reaches them.
        featureNamesBatches = chunks(list(featureNames), 1000)
        totalBatches = len(featureNamesBatches) * max(1, len(databases))
        doneBatches = 0

        # Cache all database results upfront
        allCacheFeatureIDS = {}
        allCacheSymbolsIDS = {}
        # Databases whose symbol cache was additionally keyed by input name
        # below, and only those, may use the per-name fallback when naming a
        # clone.
        nameKeyedSymbolDatabases = set()

        # One feature-ID table and one symbol table per database, filled batch
        # by batch. The batch loop is the outer one so that the ID pass can be
        # made for every database at once: the names' xref documents and the
        # mate lookup do not depend on the target database, and asking
        # MongoDB once per batch instead of once per batch per database is
        # the single largest saving in this function (see
        # findIDsByFeaturesNameForDatabases). The symbol pass below is per
        # database, as before; which database's tables a batch fills first
        # changes nothing they contain.
        for databaseConvertion_name in databases:
            allCacheFeatureIDS[databaseConvertion_name] = {}
            allCacheSymbolsIDS[databaseConvertion_name] = {}
        targetIDs = [databaseConvertion_ids.get(name) for name in databases]

        for featureNameBatch in featureNamesBatches:
            newFeatureIDsByTarget = findIDsByFeaturesNameForDatabases(
                jobID, featureNameBatch, db, targetIDs)
            for databaseConvertion_name in databases:
                cacheFeatureIDS = allCacheFeatureIDS[databaseConvertion_name]
                cacheSymbolsIDS = allCacheSymbolsIDS[databaseConvertion_name]

                databaseConvertion_id = databaseConvertion_ids.get(databaseConvertion_name)
                databaseGeneSymbol_id = databaseGeneSymbol_ids.get(databaseConvertion_name)

                # Skip the symbol pass only when it would query the ID database
                # itself (an identity translation). Compared on the RESOLVED ids:
                # resolveDatabaseIds redirects databases configured as their own
                # symbol database (Reactome, some MapMan) to the organism's real
                # symbol table, and those must run the second query or matched
                # clones keep the raw input identifier as their display name.
                sameDatabase = (databaseConvertion_id == databaseGeneSymbol_id)

                newFeatureIDs = newFeatureIDsByTarget[databaseConvertion_id]
                cacheFeatureIDS.update(newFeatureIDs)

                # Only query for symbols if the database is different from the ID database
                # Otherwise, reuse the ID results as symbols
                if sameDatabase:
                    # Reuse the feature IDs as symbols (they're the same database)
                    newSymbolIDs = newFeatureIDs
                else:
                    # Query for symbols separately
                    newSymbolIDs = findIDsByFeaturesName(jobID, list(chain.from_iterable(newFeatureIDs.values())), db,
                                                         databaseGeneSymbol_id)

                    # A symbol table can be populated for a DIFFERENT id space
                    # than the one being translated -- Reactome's gene IDs are
                    # matched against KEGG's gene-symbol table, where mmu's
                    # GNAI3 has a mate (Gnai3) but GNA12 has none at all
                    # (3.5k of STATegra's 8.4k Reactome clones). The INPUT
                    # name's own xref group does carry the symbol
                    # (ENSMUSG00000000149 -> Gna12), so a name with ANY
                    # symbol-less matched feature ID is looked up as well and
                    # used as the per-feature fallback below (ANY, not all:
                    # one name can resolve to several feature IDs and only
                    # some of them carry a symbol -- 73 extra raw-ID clones
                    # on STATegra when this asked for all of them).
                    # Decided on the RESULTS, never on the organism config:
                    # normalising organismDB.py to say what this code does
                    # must not silently switch the fallback off.
                    namesWithoutSymbol = [
                        name for name, featureIDs in newFeatureIDs.items()
                        if any(not newSymbolIDs.get(featureID)
                               for featureID in featureIDs)]
                    if namesWithoutSymbol:
                        newSymbolIDs.update(findIDsByFeaturesName(
                            jobID, namesWithoutSymbol, db, databaseGeneSymbol_id))
                        nameKeyedSymbolDatabases.add(databaseConvertion_name)

                cacheSymbolsIDS.update(newSymbolIDs)

                # One anchor per completed lookup. Reported as this worker's own
                # percentage rather than a raw count so the reader needs no
                # agreement on batch counts — workers get different numbers of
                # batches, and a percentage makes every slot commensurable.
                # A store into shared memory is ~20ns and cannot block; there is
                # no manager process to outlive the call.
                doneBatches += 1
                if progressArray is not None:
                    try:
                        progressArray[progressSlot] = min(100, int(100 * doneBatches / totalBatches))
                    except Exception:
                        pass  # progress must never be able to fail a mapping

        # Now process all features once, checking all databases for each feature
        # Use a set to track which features matched in any database (O(1) lookups)
        localMatchedFeatures = set()

        # Accumulate locally and hand over ONCE at the end. `matchedFeatures`
        # is a Manager list proxy in the forked path, and every append on it
        # is a pickle plus a socket round trip to the manager process --
        # measured at ~92us each, 40k clones per big omic, six workers
        # serialising on one manager. Element order within this worker is
        # unchanged; the interleaving between workers was never deterministic.
        localMatched = []
        localNotMatched = []

        for feature in featureList:
            originalName = feature.getName()
            featureMatchedInAnyDB = False
            # Track featureIDs already cloned for THIS feature across the database
            # loop. When two databases resolve the same input name to the same
            # featureID (e.g. ath: KEGG and MapMan both return AGI codes), cloning
            # twice would later collide in addInputGeneData and double every
            # OmicValue — Gene G1 ends up with [OV_R1, OV_R1, OV_R2, OV_R2] and
            # the pathway view shows each row twice. PathwayAcquisitionJob already
            # tags the merged gene with matchingDB=["KEGG","MapMan"] downstream,
            # so we keep the first DB on the clone and skip subsequent duplicates.
            seenIDs = set()

            # Check all databases for this feature
            for databaseConvertion_name in databases:
                cacheFeatureIDS = allCacheFeatureIDS[databaseConvertion_name]
                cacheSymbolsIDS = allCacheSymbolsIDS[databaseConvertion_name]
                featureIDs = cacheFeatureIDS.get(originalName, None)

                if featureIDs:
                    featureMatchedInAnyDB = True
                    # Increase the counter on the matching database, and keep track of the total
                    # counting only once the features. In this scenario the feature will only have one omic value
                    # containing the original name.
                    matches[databaseConvertion_name].add(
                        feature.getOmicsValues()[0].getOriginalName() if featureEnrichment else feature.getName())
                    matches["Total"].add(
                        feature.getOmicsValues()[0].getOriginalName() if featureEnrichment else feature.getName())

                    for featureID in set(featureIDs):
                        if featureID in seenIDs:
                            # Same featureID already cloned from a previous DB —
                            # cloning again would double OmicValues post-merge.
                            continue
                        seenIDs.add(featureID)

                        featureClone = feature.clone()  # IF MORE THAN 1 MATCH, CLONE THE FEATURE
                        featureClone.setID(featureID)

                        # TODO: check why this is not always applied as there are some features without matching DB
                        featureClone.setMatchingDB(databaseConvertion_name)

                        # For some IDs there might be more than one symbol, select the first
                        # one from the set. A cached entry can legitimately be an empty
                        # list (species installed with symbols=0), which indexed [0] and
                        # aborted the whole mapping. Databases redirected to a borrowed
                        # symbol table fall back to the input name's own symbol (its xref
                        # group carries one even when the feature ID's does not), and only
                        # then to the raw input name.
                        cachedSymbols = cacheSymbolsIDS.get(featureID)
                        if not cachedSymbols and databaseConvertion_name in nameKeyedSymbolDatabases:
                            cachedSymbols = cacheSymbolsIDS.get(originalName)
                        if not cachedSymbols:
                            cachedSymbols = [featureClone.getName()]
                        featureName = cachedSymbols[0]

                        featureClone.setName(featureName)
                        localMatched.append(featureClone)

            # Only add to notMatchedFeatures if it didn't match in ANY database
            if not featureMatchedInAnyDB:
                localNotMatched.append(feature)
            else:
                # Track that this feature was matched (for deduplication if needed)
                localMatchedFeatures.add(originalName)

        #*************************************************************************************
        # STORE THE RESULTS
        #*************************************************************************************
        _handOver(matchedFeatures, resultSlot, localMatched)
        _handOver(notMatchedFeatures, resultSlot, localNotMatched)

        if cacheTables is not None:
            # Everything this worker resolved, keyed exactly as
            # findIDsByFeaturesName keys the translation cache: name -> ids
            # under the id database, id -> symbols under the symbol database
            # (one and the same table when the two databases coincide, as for
            # Reactome). Misses are in here as empty lists too.
            tables = {}
            for databaseConvertion_name in databases:
                tables.setdefault(databaseConvertion_ids.get(databaseConvertion_name), {}).update(
                    allCacheFeatureIDS[databaseConvertion_name])
                tables.setdefault(databaseGeneSymbol_ids.get(databaseConvertion_name), {}).update(
                    allCacheSymbolsIDS[databaseConvertion_name])
            cacheTables.append(tables)

        # If only one database was used for the species, remove the redundant "Total" counter
        if len(databaseConvertion_ids) < 2:
            matches.pop("Total", None)

        # Exactly one append per worker: the parent counts these to detect a
        # worker that died silently (see mapFeatureNamesToKeggIDs).
        foundFeatures.append(matches)

        return matchedFeatures, notMatchedFeatures, foundFeatures

    except Exception as ex:
        raise ex
    finally:
        client.close()

def mapFeatureNamesToKeggIDs(jobID, organism, databases, featureList, enrichment, mapGeneIDs=True):
    """
    This function match the provided list of features
    to KEGG accepted feature ID (e.g. entrez gene ID for mmu)

    @param {String} organism, the organism code e.g. mmu
    @param {List} the list of features to be mapped
    @returns {Integer} foundFeatures, the number of matched features (no repetitions)
    @returns {List} matchedFeatures, the matched features
    @returns {List} notMatchedFeatures, the unmatched features
    """
    #TODO: USE mapGeneIDs

    #***********************************************************************************
    #* STEP 1. CALCULATE THE MAX NUMBER OF THREADS AND PREPARE DATA
    #***********************************************************************************

    # Avoid unnecesary calculations when there are no features
    if len(featureList) < 1:
        logging.info("NO FEATURES GIVEN. SKIPPING MAPPING - JOB: " + str(jobID))
        return [dict.fromkeys(databases, 0), [], []]

    nThreads = MAX_THREADS

    logging.info("USING " + str(nThreads) + " THREADS")
    logging.info("INPUT " + str(len(featureList)) + " FEATURES")
    logging.info("ORGANISM " + organism)

    #GET THE NUMBER OF GENES TO BE PROCESSED PER THREAD
    nLinesPerThread = int(ceil(len(featureList)/nThreads)) + 1
    #SPLIT THE ARRAY IN n PARTS
    genesListParts = chunks(featureList, nLinesPerThread)


    manager=Manager()
    #CONCATENATE THE OUTPUT LISTS -- one slot per worker, filled by that worker,
    # read back in worker (= input) order: see _handOver.
    matchedFeatures = manager.list([None] * len(genesListParts))
    notMatchedFeatures= manager.list([None] * len(genesListParts))
    foundFeatures = manager.list()
    # One entry per worker: the translations it resolved, merged into the
    # job's cache below so the NEXT omic's workers inherit them at fork time.
    cacheTables = manager.list()

    # Resolved once here rather than 2 x n_db find_one calls in each worker.
    databaseIds = resolveDatabaseIds(organism, databases)

    #***********************************************************************************
    #* STEP 2. START THE MAPPING USING N DIFFERENT THREADS IN PARALLEL
    #***********************************************************************************
    # Shared-memory progress slots, one per worker, allocated BEFORE the fork so
    # every child inherits the same mapping. Deliberately NOT a Manager list: the
    # `manager` above is a local created per call, so a proxy held for the status
    # endpoint would point at a dead process between omics and raise inside the
    # request handler. A RawArray has no server process, and reading it is a
    # memory load that cannot block or raise.
    progressArray = RawArray('i', len(genesListParts))
    JobProgress.attachAnchors(jobID, progressArray, perWorker=100)

    try:
        #matchedFeatures, notMatchedFeatures, foundFeatures = mapFeatureIdentifiers(jobID, organism, databases, featureList, matchedFeatures, notMatchedFeatures, foundFeatures, enrichment)
        threadsList = []
        i=0
        for genesListPart in genesListParts:
            thread = Process(target=mapFeatureIdentifiers, args=(jobID, organism, databases, genesListPart, matchedFeatures, notMatchedFeatures, foundFeatures, enrichment, progressArray, i, databaseIds, cacheTables, i))
            threadsList.append(thread)
            thread.start()
            i+=1

        #WAIT UNTIL ALL THREADS FINISHED (one shared budget, not one per thread)
        _joinAllWithinDeadline(threadsList, MAX_WAIT_THREADS)

        isFinished = True
        for thread in threadsList:
            if thread.is_alive():
                # TODO: possible deadlock with KeggInformationManager lock? Raise an exception to force the release there?
                isFinished = False
                thread.terminate()
                logging.info("THREAD TERMINATED IN mapFeatureNamesToKeggIDs")

        if not isFinished:
            raise Exception('Your data took too long to process and it was killed. Try it again later or upload smaller files if it persists.')


    except Exception as ex:
        manager.shutdown()
        raise ex

    #***********************************************************************************
    #* STEP 3. COMBINE THE RESULTS FOR ALL THE THREADS
    #***********************************************************************************
    # A child that DIED (unhandled exception, OOM kill) is not alive at join time,
    # so the liveness check above passes and its results are simply absent. Reading
    # foundFeatures[0] then silently returns a mapping built from a subset of the
    # input, and _matched.txt is written short — wrong results reported as success.
    # Every child appends exactly once, so a short list means a child was lost.
    if len(foundFeatures) != len(threadsList):
        manager.shutdown()
        raise Exception(
            "Identifier mapping lost %d of %d worker processes. This is usually "
            "memory pressure on the server; please try again, and upload smaller "
            "files if it persists."
            % (len(threadsList) - len(foundFeatures), len(threadsList)))

    #COMBINE DICTIONARIES
    sumFoundFeatures = dict.fromkeys(foundFeatures[0].keys())
    for dbname in sumFoundFeatures.keys():
        sumFoundFeatures[dbname] = len(set(itertools.chain.from_iterable(dbmatches[dbname] for dbmatches in foundFeatures)))

    # Carry the workers' translations into the parent's cache. The children
    # wrote them into their OWN copy of the KeggInformationManager singleton
    # (fork is copy-on-write), so until now the parent's cache -- the one the
    # next omic's workers are forked from -- stayed empty for the whole job
    # and a 4-omic upload paid the full MongoDB mapping cost four times over.
    keggInformationManager = KeggInformationManager()
    for tables in cacheTables[:]:
        for dbID, table in tables.items():
            keggInformationManager.updateTranslationCache(jobID, table, "id", dbID)

    #***********************************************************************************
    #* STEP 4. RETURN THE RESULTS
    #***********************************************************************************
    # One round trip each (a slice carries the whole list), then the workers'
    # lists concatenated in worker order -- the order a sequential pass over
    # the input produces.
    matchedFeatures = list(itertools.chain.from_iterable(part for part in matchedFeatures[:] if part is not None))
    notMatchedFeatures = list(itertools.chain.from_iterable(part for part in notMatchedFeatures[:] if part is not None))

    logging.info("FINISHED. %s uniquely matched features, %d features matched. %d features not matched.",
                 next(iter(sumFoundFeatures.values()), 0), len(matchedFeatures), len(notMatchedFeatures))
    # The Manager server is a forked process holding a copy of everything the
    # workers sent it; release it now rather than when the GC gets round to it.
    manager.shutdown()
    return sumFoundFeatures, matchedFeatures, notMatchedFeatures

# *****************************************************************
#    _____ ____  __  __ _____   ____  _    _ _   _ _____   _____
#   / ____/ __ \|  \/  |  __ \ / __ \| |  | | \ | |  __ \ / ____|
#  | |   | |  | | \  / | |__) | |  | | |  | |  \| | |  | | (___
#  | |   | |  | | |\/| |  ___/| |  | | |  | | . ` | |  | |\___ \
#  | |___| |__| | |  | | |    | |__| | |__| | |\  | |__| |____) |
#   \_____\____/|_|  |_|_|     \____/ \____/|_| \_|_____/|_____/
#
# *****************************************************************
# Upper bound on the compound-name candidates one input name may produce.
# The lookup is a case-insensitive SUBSTRING match, so a generic name matches a
# lot: on the 99k-name kegg_compounds collection "glucose" hits 198, "acid"
# 2,648, "a" 26,711 and "-" 19,778. Every hit is a cloned Feature pushed
# through a Manager pipe, so an unbounded match is a memory bill paid per
# input row. A name over the cap is treated as too generic to be a substring
# query and only its exact-name hits are kept.
MAX_COMPOUND_MATCHES = int(os.getenv("PAINTOMICS_MAX_COMPOUND_MATCHES", "500"))


class _CompoundNameCursor(object):
    """The slice of pymongo's Cursor that findCompoundIDByFeatureName uses."""

    def __init__(self, docs):
        self._docs = docs
        self._limit = None

    def limit(self, n):
        self._limit = n
        return self

    def __iter__(self):
        docs = self._docs if self._limit is None else self._docs[:self._limit]
        return iter(docs)


class _CompoundNameTable(object):
    """
    An in-memory stand-in for the ``kegg_compounds`` collection: the same
    ``find({"name": {"$regex": pattern}}).limit(n)`` calls, answered from RAM.

    kegg_compounds is a static 93k-document, 1.3 MB collection with only a
    text index, which a case-insensitive substring regex cannot use, so every
    metabolite name used to cost a full collection scan on mongod (20-50 ms
    each -- 40 s for a 4,667-row file even spread over six workers). Here the
    lower-cased names are joined into ONE haystack string and searched with
    str.find, which walks the whole 1.3 MB in well under a millisecond, and
    the hit positions are mapped back to documents in collection order.

    Semantics reproduced from the MongoDB query exactly as
    findCompoundIDByFeatureName issues it: an escaped literal (substring,
    case-insensitive) or the same literal anchored ``^...$`` (whole-name,
    case-insensitive), documents returned in natural (load) order, ``limit``
    honoured on that order. Any other pattern falls back to evaluating the
    compiled regex against every name, which is always correct and merely
    slow. Candidates found by the fast path are re-checked with the compiled
    pattern, so nothing the regex would reject can be returned.
    """

    def __init__(self, docs):
        self.docs = docs
        names = [str(doc.get("name", "")) for doc in docs]
        # A name containing the separator could straddle two entries; the
        # generic path is safe for that (never seen in KEGG, checked anyway).
        self._fastPathOK = not any("\n" in name for name in names)
        lowered = [name.lower() for name in names]
        self._haystack = "\n".join(lowered)
        self._lowered = lowered
        starts = []
        position = 0
        for name in lowered:
            starts.append(position)
            position += len(name) + 1
        self._starts = starts

    # -- collection interface -------------------------------------------------
    @property
    def kegg_compounds(self):
        return self

    def find(self, query):
        pattern = query["name"]["$regex"]
        return _CompoundNameCursor(self._matchingDocs(pattern))

    # -- matching -------------------------------------------------------------
    @staticmethod
    def _literalOf(pattern):
        """The literal a re.escape()d (optionally ^...$ anchored) pattern
        stands for, plus whether it was anchored; None if not that shape."""
        source = pattern.pattern
        anchored = len(source) >= 2 and source.startswith("^") and source.endswith("$")
        core = source[1:-1] if anchored else source
        literal = re.sub(r"\\(.)", r"\1", core, flags=re.S)
        if re.escape(literal) != core:
            return None, anchored
        return literal, anchored

    def _matchingDocs(self, pattern):
        literal, anchored = self._literalOf(pattern)
        caseInsensitive = bool(pattern.flags & re.IGNORECASE)
        if literal is None or not caseInsensitive or not self._fastPathOK or literal == "":
            return [doc for doc in self.docs if pattern.search(str(doc.get("name", "")))]
        needle = literal.lower()
        if anchored:
            candidates = [index for index, name in enumerate(self._lowered) if name == needle]
        else:
            candidates = []
            haystack, starts = self._haystack, self._starts
            position = haystack.find(needle)
            while position != -1:
                index = bisect_right(starts, position) - 1
                candidates.append(index)
                nextDoc = index + 1
                if nextDoc >= len(starts):
                    break
                position = haystack.find(needle, starts[nextDoc])
        docs = self.docs
        return [docs[index] for index in candidates
                if pattern.search(str(docs[index].get("name", "")))]


_compoundNameTable = None
_compoundNameTableSize = None


def getCompoundNameTable(db=None, revalidate=True):
    """
    The process-wide _CompoundNameTable, loaded from ``global-paintomics``
    on first use and reused for the life of the process. kegg_compounds only
    changes when the installer runs, which already requires a server restart
    for every other in-memory dataset; with ``revalidate`` the document count
    is re-checked so an in-place reload of the collection is still noticed.
    Loaded in the PARENT before the workers fork, so all of them share one
    copy-on-write table (a worker passes revalidate=False: it inherited the
    table the parent just validated and must not open a connection of its own
    just to count).
    """
    global _compoundNameTable, _compoundNameTableSize
    if _compoundNameTable is not None and not revalidate:
        return _compoundNameTable
    client = None
    if db is None:
        client, db = getConnectionByOrganismCode("global")
    try:
        size = db.kegg_compounds.estimated_document_count()
        if _compoundNameTable is None or _compoundNameTableSize != size:
            started = time.monotonic()
            # Without _id: nothing reads it (mapCompoundsIdentifiers uses id and
            # name), and an ObjectId leaf keeps all 93k dicts on the garbage
            # collector's radar for the life of the process -- ~0.2 s per full
            # collection, measured -- whereas dicts of plain strings are
            # untracked after the first pass.
            _compoundNameTable = _CompoundNameTable(list(db.kegg_compounds.find({}, {"_id": 0})))
            _compoundNameTableSize = size
            logging.info("LOADED %d KEGG COMPOUND NAMES INTO MEMORY IN %.2fs",
                         size, time.monotonic() - started)
        return _compoundNameTable
    finally:
        if client is not None:
            client.close()


def isPlaceholderCompoundName(featureName):
    """
    True when ``featureName`` cannot identify anything: empty, or made only of
    punctuation such as ``-``, ``.``, ``?`` or ``N/A``-style dashes that
    spreadsheets and pipelines leave in place of a missing identifier.

    Why this matters: on 2026-08-17 a metabolomics upload on paintomics.uv.es
    carried 1,172 rows whose identifier column was ``-``. Each became the
    regex ``.*-.*``, matched the 19,778 KEGG compound names containing a
    hyphen, and cloned the input feature once per hit -- 23 million Feature
    objects into a Manager process that reached 3.8 GB, filled the 8 GB
    machine plus its 4 GB of swap, and left every other job on the server
    (including unrelated gene-expression mappings) crawling in swap for
    26 minutes until the processes were killed by hand.
    """
    return not any(ch.isalnum() for ch in (featureName or ""))


def findCompoundIDByFeatureName(jobID, featureName, db):
    """
    This function queries the MongoDB looking for the KEGG compounds whose name
    contains the given feature name (case-insensitive).

    @param {String} jobID, the identifier for the running job, necessary to for the temporal caches
    @param {String} featureName, the name for the feature that we want to map
    @param  {pymongo.Database} db, the open connection with MongoDB database
    @returns {List} matchedFeatures, a list of translated identifiers
    @returns {Boolean} found, True if we found at least one translation
    """

    matchedFeatures=[]
    name = (featureName or "").strip()
    if isPlaceholderCompoundName(name):
        return matchedFeatures, False
    try:
        # The name is data, not a pattern: escape it. Unescaped, "NAD+" read as
        # "NA" then one or more "D", "(R)-lactate" as a group that never
        # matches its own parentheses, and a lone "." as any compound at all.
        # $regex is a substring search already, so no ".*" padding is needed.
        pattern = compile_re(re.escape(name), IGNORECASE_re)
        cursor = db.kegg_compounds.find({"name": {"$regex": pattern}}).limit(MAX_COMPOUND_MATCHES + 1)
        # See findIDsByFeatureName: Cursor.count() is gone in pymongo 4 and the
        # surrounding except would have hidden the failure entirely.
        for item in cursor:
            matchedFeatures.append(item)

        if len(matchedFeatures) > MAX_COMPOUND_MATCHES:
            # Too generic for a substring query; keep exact-name hits only.
            exact = compile_re("^" + re.escape(name) + "$", IGNORECASE_re)
            matchedFeatures = list(db.kegg_compounds.find({"name": {"$regex": exact}}).limit(MAX_COMPOUND_MATCHES))
            logging.warning("COMPOUND NAME %r MATCHES MORE THAN %d KEGG NAMES; KEEPING %d EXACT MATCH(ES) ONLY (JOB %s)",
                            name, MAX_COMPOUND_MATCHES, len(matchedFeatures), jobID)

        return matchedFeatures, len(matchedFeatures) > 0
    except Exception:
        return matchedFeatures, False

def mapCompoundsIdentifiers(jobID, featureList, matchedFeatures, notMatchedFeatures, foundFeatures, resultSlot=None):
    """
    This function is used to query the database in different threads.

    @param  {List}   featureList, the list of feature IDs to map
    @param  {List}   matchedFeatures, a list shared between threads where we store the matched features
    @param  {List}   notMatchedFeatures, a list shared between threads where we store the unmatched features
    @param  {List}   foundFeatures,
    @param  {matchedGeneIDsTablesList}   foundFeatures,
    @returns True
    """


    #***********************************************************************************
    #* STEP 2. GET THE CORRESPONDING DATABASE FOR CURRENT SPECIE
    #***********************************************************************************
    # The in-memory copy of kegg_compounds (see _CompoundNameTable), inherited
    # from the parent that loaded it before forking. It answers exactly the
    # find(...).limit(...) calls findCompoundIDByFeatureName makes.
    db = getCompoundNameTable(revalidate=False)

    # Local accumulation, one hand-over per list at the end: see the same
    # note in mapFeatureIdentifiers.
    localMatched = []
    localNotMatched = []

    try:
        matches=0
        for feature in featureList:
            if feature.getName() != "" and feature.getName()!= None:
                matchedCompounds, found = findCompoundIDByFeatureName(jobID, feature.getName(), db)

                if(found == True):
                    matches+=1 #computes the total unique matching
                    oldName = feature.getName()
                    matchedElement = FoundFeature("")
                    matchedElement.setTitle(oldName)

                    for matchedCompound in matchedCompounds:
                        feature = feature.clone() #IF MORE THAN 1 MATCH, CLONE THE FEATURE
                        feature.setID(matchedCompound.get("id"))
                        feature.setName(matchedCompound.get("name"))
                        feature.getOmicsValues()[0].setInputName(matchedCompound.get("name"))

                        if feature.calculateSimilarity(oldName) >=  0.9:
                            # matchedElement["mainCompounds"].append(feature)
                            matchedElement.addMainCompound(feature)
                        else:
                            feature.getOmicsValues()[0].setOriginalName(oldName)
                            # matchedElement["otherCompounds"].append(feature)
                            matchedElement.addOtherCompound(feature)

                    #Remove some special cases of repeated features
                    # 1.  Find all repeated features
                    repeatedFeatures = {}
                    for i in range(len(matchedElement.getMainCompounds())):
                        feature = matchedElement.getMainCompounds()[i]
                        if feature.getID() not in repeatedFeatures:
                            repeatedFeatures[feature.getID()] = ([],[])
                        repeatedFeatures[feature.getID()][0].append(i)
                    for i in range(len(matchedElement.getOtherCompounds())):
                        feature = matchedElement.getOtherCompounds()[i]
                        if feature.getID() not in repeatedFeatures:
                            repeatedFeatures[feature.getID()] = ([],[])
                        repeatedFeatures[feature.getID()][1].append(i)

                    # 2.  For each repeated features check if name is the same than the input and remove
                    #     e.g. Leucine is repeated as "Leucine" and as "Leucine" but refering "L-Leucine"
                    toRemove = ([],[])
                    for indexes in repeatedFeatures.values():
                        #Take the first feature
                        if len(indexes[0]) > 1:
                            mainFeature = matchedElement.getMainCompounds()[indexes[0][0]]
                            del indexes[0][0]
                        elif len(indexes[1]) > 1:
                            mainFeature = matchedElement.getOtherCompounds()[indexes[1][0]]
                            del indexes[1][0]
                        else:
                            continue

                        #Combine the name for the remaining features
                        for i in indexes[0]:
                            mainFeature.setName(mainFeature.getName() + ", " + matchedElement.getMainCompounds()[i].getName())
                            toRemove[0].append(i)
                        for i in indexes[1]:
                            mainFeature.setName(mainFeature.getName() + ", " + matchedElement.getOtherCompounds()[i].getName())
                            toRemove[1].append(i)

                    #Delete invalid features
                    for i in sorted(toRemove[0], reverse=True): #looping in reverse order avoid "index out of range" errors (we are removing items from the array)
                        del matchedElement.getMainCompounds()[i]
                    for i in sorted(toRemove[1], reverse=True):
                        del matchedElement.getOtherCompounds()[i]

                    #Add the CompoundSet to the list
                    localMatched.append(matchedElement)
                else:
                    localNotMatched.append(feature)

        #*************************************************************************************
        # STORE THE RESULTS
        #*************************************************************************************
        _handOver(matchedFeatures, resultSlot, localMatched)
        _handOver(notMatchedFeatures, resultSlot, localNotMatched)
        foundFeatures.append(matches)

        return True

    except Exception as ex:
        raise ex

def mapFeatureNamesToCompoundsIDs(jobID, featureList):
    """
    This function match the provided list of features
    to KEGG accepted compounds ID

    @param {String} organism, the organism code e.g. mmu
    @param {List} the list of features to be mapped
    @returns {Integer} foundFeatures, the number of matched features (no repetitions)
    @returns {List} matchedFeatures, the matched features
    @returns {List} notMatchedFeatures, the unmatched features
    """

    #***********************************************************************************
    #* STEP 1. CALCULATE THE MAX NUMBER OF THREADS AND PREPARE DATA
    #***********************************************************************************
    nThreads = MAX_THREADS

    logging.info("USING " + str(nThreads) + " THREADS")
    logging.info("INPUT " + str(len(featureList)) + " FEATURES")

    #GET THE NUMBER OF GENES TO BE PROCESSED PER THREAD
    nLinesPerThread = int(ceil(len(featureList)/nThreads)) + 1
    #SPLIT THE ARRAY IN n PARTS
    compoundsListParts = chunks(featureList, nLinesPerThread)

    # Load (or revalidate) the in-memory kegg_compounds table BEFORE forking so
    # every worker inherits the same copy instead of scanning MongoDB per name.
    getCompoundNameTable()

    manager=Manager()

    #CONCATENATE THE OUTPUT LISTS -- one slot per worker, read back in worker
    # (= input) order: see _handOver.
    matchedFeatures = manager.list([None] * len(compoundsListParts))
    notMatchedFeatures= manager.list([None] * len(compoundsListParts))
    foundFeatures= manager.list([0]*nThreads)

    #***********************************************************************************
    #* STEP 2. START THE MAPPING USING N DIFFERENT THREADS IN PARALLEL
    #***********************************************************************************
    try:
        threadsList = []
        i=0
        for compoundListPart in compoundsListParts:
            thread = Process(target=mapCompoundsIdentifiers, args=(jobID, compoundListPart, matchedFeatures, notMatchedFeatures, foundFeatures, i))
            threadsList.append(thread)
            thread.start()
            i+=1

        #WAIT UNTIL ALL THREADS FINISHED (one shared budget, not one per thread)
        _joinAllWithinDeadline(threadsList, MAX_WAIT_THREADS)

        isFinished = True
        for thread in threadsList:
            if(thread.is_alive()):
                isFinished = False
                thread.terminate()
                logging.info("THREAD TERMINATED IN mapFeatureNamesToCompoundsIDs")

        if not isFinished:
            raise Exception('Your data took too long to process and it was killed. Try it again later or upload smaller files if it persists.')

    except Exception as ex:
        manager.shutdown()
        raise ex

    #***********************************************************************************
    #* STEP 3. COMBINE THE RESULTS FOR ALL THE THREADS
    #***********************************************************************************

    foundFeatures = sum(foundFeatures)

    # One round trip each, concatenated in worker (= input) order.
    matchedFeatures = list(itertools.chain.from_iterable(part for part in matchedFeatures[:] if part is not None))
    notMatchedFeatures = list(itertools.chain.from_iterable(part for part in notMatchedFeatures[:] if part is not None))

    #***********************************************************************************
    #* STEP 4. RETURN THE RESULTS
    #***********************************************************************************
    logging.info("FINISHED. " + str(foundFeatures) + " uniquely matched compounds, " +  str(len(matchedFeatures)) + " compounds matched. " + str(len(notMatchedFeatures)) + " compounds not matched.")
    manager.shutdown()
    return foundFeatures, matchedFeatures, notMatchedFeatures
