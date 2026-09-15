"""Which installed organism a column of identifiers belongs to.

Why this exists
---------------
Every zero-match failure in the retained production logs (2026-09) was the
same mistake: identifiers from one organism run against another. Yeast ORF
names (YAL001C) against *Fusarium graminearum*; mouse-cased symbols (Aste1,
Ackr1) against *Citrus*, then against human; UniProt accessions against maize.
Each time the job passed step 1 with 0 matched, step 2 stopped with "nothing
to analyse", and the user was told to "check the organism you chose" with no
word on which organism the file actually fits.

The answer is already in the database. Step 1 recognises an identifier by an
exact, case-sensitive lookup on `xref.display_id`, which every organism
database indexes (FeatureNamesToKeggIDsMapper.findIDsByFeaturesName). So
"which organism would recognise these" is that same first hop, asked of a few
organisms instead of the one that was picked. Nothing is precomputed and
nothing can go stale: the index consulted is the one the mapping itself uses.

What it costs
-------------
Measured on paintomics.uv.es (2026-09-15): one indexed `distinct` over ~150
identifiers takes 8-130 ms per organism. Scanning every installed organism is
not an option -- 227 s across the 3,147 on paintomics.org -- so this narrows
first and looks up little:

  1. If an organism is already selected, look it up FIRST. When it recognises
     the identifiers (>= 50 %) the answer is "nothing to say" after one query,
     which is the common case and must stay the cheapest.
  2. Species-specific namespaces resolve from the string alone. Ensembl stable
     ids carry one prefix per species by construction (ENSG, ENSMUSG, ENSRNOG),
     and the locus-tag / ORF conventions of the organisms whose data arrives in
     KEGG-native form (AT1G, YAL001C, FOXG_) are just as telling. A pattern that
     covers >= 80 % of the sample names its organism with no query at all.
  3. Only organism-agnostic namespaces -- Entrez GeneIDs, UniProt accessions,
     bare gene symbols -- need a lookup, and only against a short, ordered list
     of the organisms people actually use (ORGANISM_DETECTION_SHORTLIST), never
     the whole installation. Worst case, nothing matching anywhere, is the full
     list: ~30 queries, about a second.

When it speaks
--------------
Precision over recall, because a wrong hint is worse than none. One organism
with >= 50 % of the sample recognised and every other below half of that is
"confident". Two or more within a factor of two of each other -- mouse and rat
on mouse-cased symbols, both at 100 % -- is a tie, reported as such and never
auto-applied. Nothing at 50 % is silence. An organism outside the shortlist
gets no hint; that is a miss, not an error, and the failure message still
stands.

Everything here is pure except `lookupHits`, which the caller can replace, so
the decision rule is unit-tested without a database.
"""
import json
import logging
import os
import re
import threading
import time

# --- Sample hygiene ---------------------------------------------------------

MAX_IDENTIFIERS = 200          # enough for a decision; bounds every query
MAX_IDENTIFIER_LENGTH = 80     # the server's own field limit (2_1_accepted_input.md)
MAX_SHORTLIST = 30
LOOKUP_TIMEOUT_MS = 800        # per organism; a slow box degrades to "no hint"
# The whole shortlist scan, however many organisms are left. This endpoint is
# unauthenticated and the deployment runs ONE uWSGI worker (deploy/README.md),
# so an unbounded scan is a way to hold that worker: 30 organisms each allowed
# LOOKUP_TIMEOUT_MS is 24 s of it. Past the deadline the scan stops and decides
# on what it has, which is the same outcome as an organism that scored nothing.
SCAN_DEADLINE_SECONDS = 1.5
INSTALLED_TTL_SECONDS = 300    # matches DatabaseAvailability.CACHE_TTL_SECONDS

CONFIDENT_FRACTION = 0.5       # the winner must match at least half
# A runner-up scoring at least this much OF THE WINNER ties it. It has to sit
# ABOVE CONFIDENT_FRACTION/best to mean anything: at 0.5 the arithmetic made
# the guard dead code, because `ranked` has already dropped everything under
# 0.5 and best*0.5 is at most 0.5, so every survivor tied and `confident` could
# never be reached with more than one candidate. An ordinary human gene-symbol
# file would then never fill the combo, because bta/ssc/cfa carry many of the
# same uppercase ortholog symbols and would sit around 0.55-0.8. 0.85 keeps the
# cases that are genuinely ambiguous -- mouse and rat both score ~1.0 on
# mouse-cased symbols -- and lets a clear winner through.
TIE_RATIO = 0.85
PATTERN_FRACTION = 0.8         # a namespace pattern must cover this much of the sample
SELECTED_OK_FRACTION = 0.5     # the chosen organism recognises enough: say nothing

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sanitiseIdentifiers(values):
    """Distinct, non-blank, printable strings, in order, capped.

    Anything that is not a string is dropped rather than coerced: a number in
    the identifier column of a JSON payload is a client bug, not an identifier.
    """
    seen = set()
    clean = []
    for value in values if isinstance(values, (list, tuple)) else []:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value or len(value) > MAX_IDENTIFIER_LENGTH or _CONTROL.search(value):
            continue
        if value in seen:
            continue
        seen.add(value)
        clean.append(value)
        if len(clean) >= MAX_IDENTIFIERS:
            break
    return clean


# --- Pattern layer ----------------------------------------------------------
#
# (regex, organism codes, species name). Codes are a tuple because one
# convention can belong to two installed entries -- KEGG carries rice twice
# (osa, dosa) -- and which of them applies is decided by what is installed.
#
# Only conventions that are specific to a species by DEFINITION are listed.
# A convention two organisms share (b-numbers across E. coli strains, CNAG_
# across the two C. neoformans entries) is left out on purpose: the lookup
# layer sorts those out with data, and a wrong pattern here would be a wrong
# answer with no evidence behind it.
_ENS = r"(?:\.\d+)?"           # Ensembl version suffix; the organism is evident either way

_PATTERN_TABLE = [
    (r"ENSG\d{11}" + _ENS, ("hsa",), "Homo sapiens"),
    (r"ENSMUSG\d{11}" + _ENS, ("mmu",), "Mus musculus"),
    (r"ENSRNOG\d{11}" + _ENS, ("rno",), "Rattus norvegicus"),
    (r"ENSDARG\d{11}" + _ENS, ("dre",), "Danio rerio"),
    (r"ENSGALG\d{11}" + _ENS, ("gga",), "Gallus gallus"),
    (r"ENSSSCG\d{11}" + _ENS, ("ssc",), "Sus scrofa"),
    (r"ENSBTAG\d{11}" + _ENS, ("bta",), "Bos taurus"),
    (r"ENSCAFG\d{11}" + _ENS, ("cfa",), "Canis lupus familiaris"),
    (r"ENSOARG\d{11}" + _ENS, ("oas",), "Ovis aries"),
    (r"ENSECAG\d{11}" + _ENS, ("ecb",), "Equus caballus"),
    (r"ENSOCUG\d{11}" + _ENS, ("ocu",), "Oryctolagus cuniculus"),
    (r"ENSPTRG\d{11}" + _ENS, ("ptr",), "Pan troglodytes"),
    (r"ENSMMUG\d{11}" + _ENS, ("mcc",), "Macaca mulatta"),
    (r"ENSCHIG\d{11}" + _ENS, ("chx",), "Capra hircus"),
    (r"ENSXETG\d{11}" + _ENS, ("xtr",), "Xenopus tropicalis"),
    (r"FBgn\d{7}", ("dme",), "Drosophila melanogaster"),
    (r"WBGene\d{8}", ("cel",), "Caenorhabditis elegans"),
    (r"Y[A-P][LR]\d{3}[WC](?:-[A-Z])?", ("sce",), "Saccharomyces cerevisiae"),
    (r"SP[A-Z]{1,4}[0-9A-Z]*\.\d{2}c?", ("spo",), "Schizosaccharomyces pombe"),
    (r"AT[1-5CM]G\d{5}(?:\.\d+)?", ("ath",), "Arabidopsis thaliana"),
    (r"Os\d{2}g\d{7}", ("dosa", "osa"), "Oryza sativa japonica"),
    (r"LOC_Os\d{2}g\d{5}", ("osa", "dosa"), "Oryza sativa japonica"),
    (r"Zm00001[de]\d{6}", ("zma",), "Zea mays"),
    (r"GRMZM\d?G\d{6}", ("zma",), "Zea mays"),
    (r"Solyc\d{2}g\d{6}(?:\.\d+)?", ("sly",), "Solanum lycopersicum"),
    (r"Glyma\.?\d{2}[Gg]\d{6}", ("gmx",), "Glycine max"),
    (r"Potri\.\d{3}G\d{6}", ("pop",), "Populus trichocarpa"),
    (r"Cre\d{2}\.g\d{6}", ("cre",), "Chlamydomonas reinhardtii"),
    (r"FOXG_\d{5}", ("fox",), "Fusarium oxysporum"),
    (r"FGSG_\d{5}", ("fgr",), "Fusarium graminearum"),
    (r"MGG_\d{5}", ("mgr",), "Magnaporthe oryzae"),
    (r"NCU\d{5}", ("ncr",), "Neurospora crassa"),
    (r"PF3D7_\d{7}", ("pfa",), "Plasmodium falciparum 3D7"),
    (r"DDB_G\d{7}", ("ddi",), "Dictyostelium discoideum"),
    (r"BSU_?\d{5}", ("bsu",), "Bacillus subtilis 168"),
    (r"PA\d{4}", ("pae",), "Pseudomonas aeruginosa PAO1"),
    (r"Rv\d{4}[A-Za-z]?", ("mtu",), "Mycobacterium tuberculosis H37Rv"),
]
PATTERNS = [(re.compile(regex), codes, name) for regex, codes, name in _PATTERN_TABLE]
PATTERN_NAMES = {code: name for _, codes, name in _PATTERN_TABLE for code in codes}


def matchPattern(identifiers):
    """The pattern that covers >= PATTERN_FRACTION of the sample, or None.

    Returns (codes, name, fraction). Full-match only: a prefix test would let
    'ATP5' (a symbol) count as Arabidopsis.
    """
    if not identifiers:
        return None
    best = None
    for regex, codes, name in PATTERNS:
        hits = sum(1 for value in identifiers if regex.fullmatch(value))
        fraction = hits / float(len(identifiers))
        if fraction >= PATTERN_FRACTION and (best is None or fraction > best[2]):
            best = (codes, name, fraction)
    return best


# --- Names ------------------------------------------------------------------

_speciesNames = {"path": None, "mtime": None, "names": {}}


def _speciesJsonPath():
    try:
        from src.conf.serverconf import KEGG_DATA_DIR
    except Exception:
        return None
    return os.path.join(KEGG_DATA_DIR, "current", "species.json")


def speciesNames(path=None):
    """{code: display name} from KEGG_DATA_DIR/current/species.json.

    The same file the step 1 combo is loaded from, so the hint names the
    organism exactly as the combo does. Re-read when the file changes (an
    install rewrites it); an unreadable file yields the pattern table's names.
    """
    path = path or _speciesJsonPath()
    if not path:
        return dict(PATTERN_NAMES)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return dict(PATTERN_NAMES)
    cache = _speciesNames
    if cache["path"] != path or cache["mtime"] != mtime:
        names = dict(PATTERN_NAMES)
        try:
            with open(path, encoding="utf-8") as handle:
                for entry in (json.load(handle).get("species") or []):
                    if isinstance(entry, dict) and entry.get("value") and entry.get("name"):
                        names[str(entry["value"])] = str(entry["name"])
        except (OSError, ValueError) as ex:
            logging.warning("OrganismDetector: could not read %s (%s: %s)", path,
                            type(ex).__name__, ex)
        cache.update(path=path, mtime=mtime, names=names)
    return cache["names"]


# --- Configuration ----------------------------------------------------------

DEFAULT_SHORTLIST = (
    "hsa", "mmu", "rno", "ath", "sce", "dre", "dme", "cel", "eco", "bta",
    "ssc", "gga", "cfa", "osa", "dosa", "zma", "sly", "gmx", "spo", "bsu",
    "pae", "mtu", "chx", "oas", "ecb", "ocu", "ptr", "mcc", "xtr", "pfa",
)


def shortlist():
    """The organisms the lookup layer tries, most-used first.

    From serverconf.ORGANISM_DETECTION_SHORTLIST when the deployment sets it;
    a serverconf that predates the setting gets the default rather than an
    ImportError, because this runs on a form field and must never take the
    upload down with it.
    """
    try:
        from src.conf import serverconf
        configured = getattr(serverconf, "ORGANISM_DETECTION_SHORTLIST", None)
    except Exception:
        configured = None
    codes = list(configured) if configured else list(DEFAULT_SHORTLIST)
    return [str(code).strip() for code in codes if str(code).strip()][:MAX_SHORTLIST]


# --- Lookup layer -----------------------------------------------------------

_DB_SUFFIX = "-paintomics"


_clientLock = threading.Lock()
_client = None
_installedCache = {"at": 0.0, "codes": None}


def _sharedClient():
    """One MongoClient for the process, built once.

    A client per request is what this used to do, and it is the wrong shape
    for a route the client calls on every organism change: each one builds a
    connection pool and a topology monitor thread. PyMongo's client is
    thread-safe and reconnects on its own, so the process keeps one.
    """
    global _client
    with _clientLock:
        if _client is None:
            from pymongo import MongoClient
            from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
            _client = MongoClient(MONGODB_HOST, MONGODB_PORT,
                                  serverSelectionTimeoutMS=2000,
                                  connectTimeoutMS=2000)
        return _client


class MongoLookup(object):
    """Hits per organism, over the process-wide client.

    `installed` is what has a `<code>-paintomics` database, the same definition
    DatabaseAvailability uses: an organism the visitor can pick right now. It
    is cached for INSTALLED_TTL_SECONDS because listing databases costs 48 ms
    on the 3,227-database deployment (measured 2026-09-15) and this route is
    called on every organism change, on every gene-based card.
    """

    def __init__(self, client):
        self.client = client

    def installed(self):
        now = time.time()
        cached = _installedCache["codes"]
        if cached is not None and (now - _installedCache["at"]) < INSTALLED_TTL_SECONDS:
            return cached
        codes = frozenset(
            name[:-len(_DB_SUFFIX)]
            for name in self.client.list_database_names()
            if name.endswith(_DB_SUFFIX) and name != "global" + _DB_SUFFIX
        )
        _installedCache.update(at=now, codes=codes)
        return codes

    def hits(self, organism, identifiers):
        """How many of the identifiers appear in this organism's xref.

        Distinct display_ids, because one symbol can sit in several namespaces
        and a document count would inflate it.

        This is ONE hop where the mapper does two: findIDsByFeaturesName goes
        on to require a mate in the target KEGG table, so an identifier in the
        xref with no KEGG link counts here and would not map. The second hop
        would double the queries for every organism on the shortlist, which is
        the cost this whole design exists to avoid, so the count is reported as
        what it is -- identifiers matching the organism's identifier index --
        and never as a promise about how many will map.
        """
        found = self.client[organism + _DB_SUFFIX].xref.distinct(
            "display_id", {"display_id": {"$in": identifiers}},
            maxTimeMS=LOOKUP_TIMEOUT_MS)
        return len(found)


def openLookup():
    """A MongoLookup on the configured server, or None when it cannot connect."""
    try:
        client = _sharedClient()
        client.admin.command("ping")
        return MongoLookup(client)
    except Exception as ex:
        logging.warning("OrganismDetector: no database (%s: %s); no hint", type(ex).__name__, ex)
        return None


# --- Decision ---------------------------------------------------------------

def _candidate(code, hits, total, names, method):
    return {"code": code, "name": names.get(code, code), "hits": hits, "total": total,
            "fraction": (hits / float(total)) if total else 0.0, "method": method}


def decide(scored):
    """(confident, candidates) from [{code, fraction, ...}] -- the rule above.

    `candidates` is the ranked list worth showing: the winner alone when it is
    confident, the tied group (at most three) otherwise, nothing when nothing
    reaches CONFIDENT_FRACTION.
    """
    ranked = sorted((c for c in scored if c["fraction"] >= CONFIDENT_FRACTION),
                    key=lambda c: (-c["fraction"], -c["hits"], c["code"]))
    if not ranked:
        return None, []
    best = ranked[0]
    tied = [c for c in ranked if c["fraction"] >= best["fraction"] * TIE_RATIO]
    if len(tied) == 1:
        return best, [best]
    return None, tied[:3]


def detectOrganism(identifiers, selected=None, lookup=None, installed=None, names=None,
                   shortlistCodes=None):
    """The hint for a column of identifiers. Never raises: a failure is {"success": False}.

    identifiers   raw first-column values (sanitised here)
    selected      the organism code the form currently holds, or None
    lookup        an object with installed() and hits(code, ids); None opens Mongo
    installed     override of lookup.installed() (tests)
    names         override of speciesNames() (tests)

    Result keys, all always present:
      selected     {code, name, hits, total, fraction} or None
      selectedOk   True when the chosen organism recognises enough -- say nothing
      confident    the one organism the sample fits, or None
      candidates   ranked list to show (the winner, or the tie), possibly empty
      uninstalled  {code, name} when a pattern names an organism this server lacks
      method       "selected" | "pattern" | "lookup" | None
    """
    started = time.monotonic()
    sample = sanitiseIdentifiers(identifiers)
    selected = (selected or "").strip() or None
    result = {"success": True, "sample": len(sample), "selected": None, "selectedOk": False,
              "confident": None, "candidates": [], "uninstalled": None, "method": None}
    if len(sample) < 5:
        result["method"] = None
        result["elapsedMs"] = int((time.monotonic() - started) * 1000)
        return result

    ownLookup = lookup is None and installed is None
    if ownLookup:
        lookup = openLookup()
        if lookup is None:
            result.update(success=False, elapsedMs=int((time.monotonic() - started) * 1000))
            return result
    try:
        names = names if names is not None else speciesNames()
        installedSet = set(installed) if installed is not None else set(lookup.installed())
        total = len(sample)

        def score(code, method):
            try:
                hits = lookup.hits(code, sample) if lookup is not None else 0
            except Exception as ex:
                logging.warning("OrganismDetector: lookup of %s failed (%s: %s)", code,
                                type(ex).__name__, ex)
                hits = 0
            return _candidate(code, hits, total, names, method)

        # 1. The chosen organism first: the common case ends here, one query.
        if selected and selected in installedSet:
            own = score(selected, "selected")
            result["selected"] = own
            if own["fraction"] >= SELECTED_OK_FRACTION:
                result.update(selectedOk=True, method="selected")
                return result

        # 2. A species-specific namespace names its organism without a query.
        pattern = matchPattern(sample)
        if pattern:
            codes, name, fraction = pattern
            # Never name the organism the user already chose. Versioned Ensembl
            # ids run as their own species land here -- selected scores 0 hits
            # and falls through -- and suggesting "choose mmu" to someone who
            # chose mmu is worse than saying nothing: the suffix advice above
            # is the real answer for that file.
            present = [code for code in codes
                       if code in installedSet and code != selected]
            if present:
                scored = []
                for code in present:
                    candidate = score(code, "pattern")
                    # The pattern is the evidence; the lookup only fills in the
                    # numbers. An xref without that namespace must not silence a
                    # definitional prefix -- but then hits and fraction have to
                    # move together, or the sentence reads "(0 of 120 matched)"
                    # while claiming the organism.
                    floor = int(round(fraction * total))
                    if candidate["hits"] < floor:
                        candidate["hits"] = floor
                        candidate["fraction"] = candidate["hits"] / float(total)
                    scored.append(candidate)
                confident, candidates = decide(scored)
                result.update(confident=confident, candidates=candidates, method="pattern")
                return result
            if any(code == selected for code in codes):
                result["method"] = "pattern"     # the file fits the choice; nothing to say
                return result
            result["uninstalled"] = {"code": codes[0], "name": names.get(codes[0], name)}
            result["method"] = "pattern"
            return result

        # 3. Organism-agnostic identifiers: the shortlist, most-used first,
        #    under one deadline for the whole scan (see SCAN_DEADLINE_SECONDS).
        codes = [code for code in (shortlistCodes or shortlist())
                 if code in installedSet and code != selected]
        scored = []
        for code in codes:
            scored.append(score(code, "lookup"))
            if (time.monotonic() - started) > SCAN_DEADLINE_SECONDS:
                logging.warning("OrganismDetector: scan deadline reached after %d of %d "
                                "organisms; deciding on those", len(scored), len(codes))
                break
        confident, candidates = decide(scored)
        result.update(confident=confident, candidates=candidates,
                      method="lookup" if scored else None)
        return result
    finally:
        result["elapsedMs"] = int((time.monotonic() - started) * 1000)
        if ownLookup and lookup is not None:
            try:
                lookup.client.close()
            except Exception:
                pass


# --- Wording shared by the form hint and the failure message ----------------

def describeHint(result):
    """One plain sentence for a step 2 failure message, or None when there is
    nothing worth saying. The client renders its own richer version."""
    if not result or not result.get("success") or result.get("selectedOk"):
        return None
    confident = result.get("confident")
    if confident:
        return ("They look like %s identifiers (%d of %d match its identifier index): go "
                "back to step 1 and choose that organism." % (confident["name"],
                                                              confident["hits"],
                                                              confident["total"]))
    candidates = result.get("candidates") or []
    if len(candidates) >= 2:
        return ("They look like %s identifiers: go back to step 1 and choose the one your "
                "data comes from." % " or ".join(c["name"] for c in candidates))
    uninstalled = result.get("uninstalled")
    if uninstalled:
        return ("They look like %s identifiers, which this server does not have installed; "
                "you can request it from step 1." % uninstalled["name"])
    return None
