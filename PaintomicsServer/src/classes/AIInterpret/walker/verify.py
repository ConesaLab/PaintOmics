"""What code checks about statements and about the Results text.

The Writer and the Narrator are models; nothing they say reaches the user
until these checks pass. Every check here is mechanical: a citation resolves
or it does not, a number is in the record verbatim or it is not.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache

# A quoted value is a decimal: signed as the layer text prints it (+2.13, −0.35),
# or unsigned when a sentence gives a magnitude ("fell by 3.24"), which must be
# the record's value with either sign. "18-24h" is a range, not a value, and a
# number after ±, < or > (±0.6, |value| > 1.00) is a bound, not a reading.
# A small value is printed in scientific form (overlay.value_text: +3.2e-05),
# and a model quoting it back may write 3.2e-05, 3.2e-5, 3.2E-05, 3.2 × 10⁻⁵
# or 3.2x10^-5: all are one value. Only a negative exponent makes one -- the
# record prints nothing else that way, and "2 x 10^3 cells" stays what it was.
_EXPONENT = r"(?:[eE][\-−]\d+|\s?[×x]\s?10(?:\^[\-−]\d+|⁻[⁰¹²³⁴⁵⁶⁷⁸⁹]+))"
NUMBER_RE = re.compile(r"(?<![\w.±<>≤≥|])[+\-−]?(?:\d+\.\d+%s?|\d+%s)" % (_EXPONENT, _EXPONENT))
BOUND_BEFORE_RE = re.compile(r"[±<>≤≥|]\s*$")


# A number written as a heat: "heat 2.43", "heat of 2.43", "heat score 2.43",
# "(heat: 2.43)". The walker prints a node's heat after its name, and naming
# it a heat is the one way to quote it that cannot pass for a reading.
HEAT_BEFORE_RE = re.compile(r"\bheat(?:\s+score)?(?:\s+(?:of|is|was))?\s*[:=≈]?\s*$", re.I)


def _is_bound(text, start):
    return bool(BOUND_BEFORE_RE.search(text[max(0, start - 3):start]))


def quoted_values(text):
    """The values a text quotes, as written: NUMBER_RE's matches less the bounds
    written with a space after the comparator ("> 1.00", "p < 0.05")."""
    text = str(text or "")
    return [m.group(0) for m in NUMBER_RE.finditer(text) if not _is_bound(text, m.start())]


def data_values(text):
    """(value, position) for every value a text quotes as data: quoted_values
    less the numbers written as a heat (HEAT_BEFORE_RE)."""
    text = str(text or "")
    return [(m.group(0), m.start()) for m in NUMBER_RE.finditer(text)
            if not _is_bound(text, m.start()) and not HEAT_BEFORE_RE.search(text[max(0, m.start() - 24):m.start()])]
LEG_LIST_RE = re.compile(r"\[(e\d+(?:\s*,\s*e?\d+)*)\]")


def cited_legs(text):
    """Every leg a text tags: [e3], and each leg of [e3, e5] or [e3, 5]."""
    legs = []
    for group in LEG_LIST_RE.findall(str(text or "")):
        for token in re.split(r"\s*,\s*", group):
            n = int(token.lstrip("e"))
            if n not in legs:
                legs.append(n)
    return legs
PAPER_RE = re.compile(r"\[(\d+)\]")
PAPER_LIST_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def cited_refs(text):
    """Every paper ref in a text: [3], and each number of [3, 5]; legs [eN] are not papers."""
    refs = []
    for group in PAPER_LIST_RE.findall(str(text or "")):
        for number in re.split(r"\s*[,;]\s*", group):
            if int(number) not in refs:
                refs.append(int(number))
    return refs
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(])")


def sentences(text):
    """A text's sentences, split where every pruner and check here splits them."""
    return SENTENCE_RE.split(str(text or ""))


_WORD_RE = re.compile(r"[^\W_]")


def is_placeholder(text):
    """Whether a Results text says nothing: not a single word in it. "...",
    "…" and "" are what the Narrator wrote in every field it skipped on
    7O521E0Y0y's 20-statement network walk (2026-10-28). Not a word count:
    verify_results already refuses a section too short for its statements."""
    return not _WORD_RE.search(str(text or ""))
STATEMENT_MIN, STATEMENT_MAX = 3, 5
RESULTS_WORDS = {"pathway": (150, 450), "network": (300, 900)}
# The walker's working words. In a statement or a Results section they narrate
# the walk instead of reporting biology: "the calcium cluster read led to the
# Syk kinase seed via a jump", "a network walk from the seed miRNA miR-151-3p".
# Name the genes instead. Only the unambiguous forms are refused: "seed" is
# ordinary in a plant job (seed storage proteins, seed miRNAs) and in a miRNA's
# seed region, "a jump at 12h" describes values, and kinesin walks along a
# microtubule, so bare "seed", "jump" and "walk" are left to the prompts.
JARGON_RE = re.compile(r"\b(clusters?|the walk(?:er)?|(?:a|an|the|this|our|each) (?:network|graph|pathway) walk(?:er)?s?"
                       r"|via a jump|jump(?:s|ed|ing)? (?:to|from|back)"
                       r"|seed nodes?|seed mirnas? (?:[a-z]{3}-)?(?:mir|let)-)\b", re.I)
# The Narrator's brief in its own words, and the pipeline's: what a reader
# never meets. DeepSeek plans in the first string of its JSON, the summary,
# and the planning reached the page. Of the 19 Results sections stored on
# paintomics.org on 2026-10-28, 5 summaries were such planning ("The kept
# statements are the facts; the prose is mine. I must produce exactly 12 fact
# paragraphs ...", "We walked a signed, directed interaction network from
# XPO1 to ...") and no title was; every alternative here matches only in those
# five, none in the other 14 summaries or any title. JARGON_RE's walker words
# are pruned everywhere; these only in the summary and the title
# (drop_meta_sentences). Of the 324 stored paragraph sentences they match 23:
# fifteen connectives that open "The drawn edge from STEAP3 to BNIP3L is ...",
# leg lists such as "(e12, e23)", and four built on "kept statement" that carry
# their paragraph's reading. Removing a paragraph sentence can take a citation
# or a statement's only paragraph with it, and fail a section that passed.
# "The chain" is not here: it carries biology in six stored paragraphs ("Thrb
# is the timed hub of this chain"), and y712svrXTf's summary keeps "The chain
# separates into a baseline-up miRNA module ...".
META_RE = re.compile(
    r"\b(?:kept|dropped) statements?\b|\bstatements? \d+\b|\bfrom_statement\b"
    r"|\b(?:fact|connective) paragraphs?\b|\bthe prose\b|\bwalk order\b|\bdesign card\b|\bdrawn edges?\b"
    r"|\btwo decimals\b|\bpast tense\b|\bhedging words\b|\bunlabell?ed omic\b"
    r"|\b\d+\s*(?:-|–|to)\s*\d+ words\b|\bc1\s*\.\.\s*cN\b|\[(?:eN|N|node · layer)\]"
    # a list of bare leg or jump ids, "e1, e3, e5", never the citation "[e3, e5]"
    r"|(?-i:(?<![\[\w])[eJ]\d+(?:, (?:and )?[eJ]\d+)+\b(?!\]))"
    r"|\b(?:I|we) (?:must|will|need|should|have to)\b|\bI(?:'ll|'m going to)\b|\blet me\b"
    r"|\bwe (?:walked|traced)\b", re.I)
# When drop_meta_sentences has removed part of a summary, a first sentence
# left opening on one of these followed what went: "Then TP53 (−1.58) and back
# through MDM2, CDKN2A, CDK4, CDKN1A." was all that stood before the brief's
# words on hsa05219. "Next to EGFR, ERBB2 ..." opens a summary that means it.
SEQUENCE_LEAD_RE = re.compile(r"^(?:Then|Next(?! to\b)|After that|From there)(?=[\s,])")


def chain_nodes(chain):
    """Every node on the chain, in walk order, first appearance kept."""
    seen = {}
    for leg in chain:
        for node_id in (leg["from"], leg["to"]):
            seen.setdefault(node_id, True)
    return list(seen)


def resolve_node(name, walker):
    """A cite's node: id or label among the chain's nodes."""
    return walker._resolve(name, chain_nodes(walker.record()["chain"]))


# Words that place a value in time or order. On an omic whose file carried no
# column labels they are claims the data cannot support: the STATegra miRNA
# file has six unlabeled columns, and a Results section once said two miRNAs
# "declined steadily over the time course".
TIMING_RE = re.compile(r"\b(early|earlier|late|later|over time|time course|time-course|progressive(ly)?|"
                       r"steadily|transient(ly)?|sustained|peak(s|ed|ing)?|onset|baseline|"
                       r"(at|by|from|to|until) \d+(\.\d+)?\s*h)\b", re.I)


# A miRNA's organism prefix: "mmu-miR-155-5p", "hsa-let-7a". Text names the
# molecule with or without it, and the walker's label strips only "mmu-".
ORGANISM_PREFIX_RE = re.compile(r"^[a-z]{2,4}-(?=(?:miR|let|lin)-)", re.I)


def bare_name(name):
    """A node or member name without a miRNA's organism prefix."""
    return ORGANISM_PREFIX_RE.sub("", str(name or "").strip())


def _names_re(name):
    """A pattern for one bare name as a whole word, with an optional organism prefix."""
    return r"(?<![A-Za-z0-9-])(?:[a-z]{2,4}-)?%s(?![A-Za-z0-9])" % re.escape(name)


@lru_cache(maxsize=1 << 16)
def _name_pattern(name):
    """_names_re(name) compiled case-insensitive, once per name. A network
    walk's catalog runs to ~950 nodes and several names each, past re's own
    512-pattern cache: on vRmKBo4E60 (round heat4, 2026-10-31) every scripted
    step recompiled every name, 70,228 compiles in one 91-leg walk, and the
    structure null took 335 s and left the Writers none."""
    return re.compile(_names_re(name), re.I)


def _node_names(walker, node_id):
    """The bare names a text may call a node by, walker's label first: the
    label, the network label's members and every layer member."""
    layers = walker.overlay.layers.get(node_id) or []
    names = [walker.label(node_id)] + [layer.get("member") for layer in layers]
    names += str((walker.network.nodes.get(node_id) or {}).get("label", "")).split(",")
    out = []
    for name in names:
        name = bare_name(name)
        if len(name) >= 3 and name not in out:
            out.append(name)
    return out


def unlabeled_nodes(walker):
    """Chain nodes whose every measured layer comes from an omic with no column
    labels (the job's miRNAs, typically): bare name -> node id, for every name
    the node goes by."""
    unlabeled = {name for name, labels in (walker.overlay.labels or {}).items() if not labels}
    out = {}
    if not unlabeled:
        return out
    for node_id in chain_nodes(walker.record()["chain"]):
        layers = walker.overlay.layers.get(node_id) or []
        if layers and all(layer["omic"] in unlabeled for layer in layers):
            for name in _node_names(walker, node_id):
                out.setdefault(name, node_id)
    return out


def _mentions(sentence, catalog):
    """(start, end, node id) of every chain node a sentence names, left to right;
    at one position the longest name wins and overlapping matches are dropped.
    A name the sentence does not contain (case-insensitive) is never matched,
    and a pattern is compiled once per name (_name_pattern)."""
    sentence = str(sentence or "")
    lowered = sentence.lower()
    found = []
    for node_id, name in catalog:
        if str(name).lower() not in lowered:
            continue
        for match in _name_pattern(name).finditer(sentence):
            found.append((match.start(), match.end(), node_id))
    found.sort(key=lambda f: (f[0], f[0] - f[1]))
    kept, end = [], -1
    for mention in found:
        if mention[0] >= end:
            kept.append(mention)
            end = mention[1]
    return kept


def timing_on_unlabeled(text, walker):
    """The unlabeled nodes ``text`` gives a timing word to.

    A timing word belongs to the node the sentence names nearest before it, or
    to the first named after it when none comes before. Asking only whether a
    sentence names the miRNA and holds a timing word anywhere dropped
    "miR-320-3p ... targets Akt3, which shows a late DNase-seq upregulation",
    where "late" is Akt3's, a gene with labelled columns."""
    nodes = unlabeled_nodes(walker)
    if not nodes:
        return []
    display = {}
    for name, node_id in nodes.items():
        display.setdefault(node_id, name)
    catalog = [(node_id, name) for node_id in chain_nodes(walker.record()["chain"])
               for name in _node_names(walker, node_id)]
    named = []
    for sentence in re.split(r"(?<=[.;:])\s+", str(text or "")):
        timings = list(TIMING_RE.finditer(sentence))
        if not timings:
            continue
        mentions = _mentions(sentence, catalog)
        for timing in timings:
            before = [m for m in mentions if m[1] <= timing.start()]
            owner = before[-1] if before else next((m for m in mentions if m[0] >= timing.end()), None)
            if owner is not None and owner[2] in display and display[owner[2]] not in named:
                named.append(display[owner[2]])
    return named


def _named_genes(text, walker):
    """The chain's node labels (every member of a box label) that a text names."""
    text = str(text or "")
    names = set()
    for node_id in chain_nodes(walker.record()["chain"]):
        for part in str(walker.network.nodes.get(node_id, {}).get("label", "")).split(","):
            part = bare_name(part)
            if len(part) >= 3 and _name_pattern(part).search(text):
                names.add(part)
    return names


def _paper_names_any(paper, genes):
    blob = " ".join([str(paper.get("title") or ""), str(paper.get("abstract") or ""),
                     str((paper.get("sections") or {}).get("abstract") or "")])
    return any(_name_pattern(g).search(blob) for g in genes)


# A leg as the chain text labels it: "e3" for a step, "J3" for a jump.
LEG_LABEL_RE = re.compile(r"^\s*[eEjJ]?(\d+)\s*$")


def leg_number(value):
    """A leg as a model wrote it -- 3, "3", "e3" or "J3", the chain text's own
    labels -- as its number; anything else unchanged, for the check to object
    to. default/llm (2026-09-30) wrote "legs": ["e1", "e2"] where DeepSeek
    wrote [1, 2], and every statement of the walk was refused as naming legs
    "ee1" and "ee2" that are not on the chain."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = LEG_LABEL_RE.match(value)
        if match:
            return int(match.group(1))
    return value


def normalise_legs(item):
    """In place: an object's "legs" and each grounded_in "leg" as numbers, so
    the checks and everything downstream of them read one form."""
    if isinstance(item.get("legs"), (list, tuple)):
        item["legs"] = [leg_number(leg) for leg in item["legs"]]
    if isinstance(item.get("grounded_in"), (list, tuple)):
        for ground in item["grounded_in"]:
            if isinstance(ground, dict) and "leg" in ground:
                ground["leg"] = leg_number(ground["leg"])
    return item


# A paper named by its PubMed id in prose: "[21849978]", "(PMID 21849978)", "PMID: 21849978".
PMID_MENTION_RE = re.compile(r"\[\s*(\d{5,9})\s*\]|\(?\bPMID:?\s*(\d{5,9})\)?")


def normalise_papers(stmt, papers):
    """In place: a retrieved paper named by its PMID becomes its [N].

    The Writer's search results list each paper as "[N] title ... PMID p", and
    the schema asks for N. default/llm (2026-09-30) wrote {"pmid": p} in every
    beyond entry instead; the Verifier refused each as "neither a retrieved
    paper nor the hypothesis flag", and the Writer flagged every claim a
    hypothesis to pass -- a Results section with no reference in it. A value
    that already is an N on the list is never read as a PMID."""
    by_pmid = {str(paper.get("pmid")): ref for ref, paper in (papers or {}).items() if paper.get("pmid")}
    if not by_pmid:
        return stmt

    def ref_of(value):
        ref = paper_ref(value)
        if ref is not None and ref in papers:
            return ref
        digits = re.sub(r"(?i)^\s*\[?\s*(pmid:?\s*)?|\s*\]?\s*$", "", str(value if value is not None else ""))
        return by_pmid.get(digits)

    if isinstance(stmt.get("beyond"), (list, tuple)):
        for beyond in stmt["beyond"]:
            if isinstance(beyond, dict):
                ref = ref_of(beyond.get("paper")) or ref_of(beyond.get("pmid"))
                if ref is not None:
                    beyond["paper"] = ref
    if isinstance(stmt.get("papers"), (list, tuple)):
        stmt["papers"] = [ref_of(value) or value for value in stmt["papers"]]

    def cite(match):
        pmid = match.group(1) or match.group(2)
        if match.group(1) and int(pmid) in papers:
            return match.group(0)
        return "[%d]" % by_pmid[pmid] if pmid in by_pmid else match.group(0)

    for key in ("claim", "prose"):
        if isinstance(stmt.get(key), str):
            stmt[key] = PMID_MENTION_RE.sub(cite, stmt[key])
    return stmt


def _as_list(stmt, key, problems):
    """A statement field the model should have sent as a list; anything else is
    an objection, never an exception (a rewrite once sent "papers": 3)."""
    value = stmt.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    problems.append("%s is not a list" % key)
    return []


def verify_statement(stmt, walker, papers, read=None, part=None, names_genes=True):
    """The objections to one submitted statement; an empty list is a pass.

    ``read``: the refs the Writer opened with read_paper; when given, a paper
    may be cited only after it was read, and a claim that names genes of the
    chain may cite only a paper whose title or abstract names one of them.
    ``part``: (first, last), the legs this statement's Writer covers.
    ``names_genes``: False when a paper agent reads every cited paper for the
    passage that states the claim, which asks more than a gene name in the
    abstract and does not fail a paper that names the protein another way."""
    problems = []
    chain = walker.record()["chain"]
    n_legs = len(chain)
    normalise_legs(stmt)
    normalise_papers(stmt, papers)
    legs = _as_list(stmt, "legs", problems)
    if not legs:
        problems.append("names no leg")
    for leg in legs:
        if not isinstance(leg, int) or isinstance(leg, bool) or leg < 1 or leg > n_legs:
            problems.append("leg e%s is not on the chain" % leg)
        elif part is not None and not part[0] <= leg <= part[1]:
            problems.append("leg e%s is outside your part of the walk (e%d to e%d)" % (leg, part[0], part[1]))
    words = JARGON_RE.findall(" ".join([str(stmt.get("claim") or ""), str(stmt.get("prose") or "")]))
    if words:
        problems.append("uses the walker's word %r: name the genes or describe them biologically"
                        % sorted({w.lower() for w in words})[0])
    cites = _as_list(stmt, "cites", problems)
    if not cites:
        problems.append("cites no [node · layer]")
    non_relevant = []
    for cite in cites:
        if not isinstance(cite, (list, tuple)) or len(cite) != 2:
            problems.append("a cite is not a [node, layer] pair: %r" % (cite,))
            continue
        node_id = resolve_node(cite[0], walker)
        if node_id is None:
            # default/llm (2026-09-30) cited nodes by their position on the
            # chain, [[0, "Metabolomics"], [1, ...]], and resubmitted the same
            # three times against an objection that did not say what to write.
            mine = [leg for leg in chain if part is None or part[0] <= leg["n"] <= part[1]] or chain
            names = [walker.label(n) for n in chain_nodes(mine)[:3]]
            problems.append("cite %r is not a node on the chain; name the node as the chain writes it "
                            "(for example %s), never by a number" % (cite[0], ", ".join(repr(n) for n in names)))
            continue
        layers = [l for l in walker.overlay.layers.get(node_id, [])
                  if l["omic"].lower() == str(cite[1]).lower()]
        if not layers:
            problems.append("%s has no layer %r" % (walker.label(node_id), cite[1]))
            continue
        if not any(l["relevant"] for l in layers):
            non_relevant.append("%s · %s" % (walker.label(node_id), cite[1]))
    prose = str(stmt.get("prose") or "")
    for label in timing_on_unlabeled(" ".join([str(stmt.get("claim") or ""), prose]), walker):
        problems.append("gives %s a timing or order word, but its layer has no column labels; "
                        "describe its columns as c1..cN without early, late or baseline" % label)
    if non_relevant and "not relevant" not in prose.lower():
        problems.append("cites a non-relevant layer (%s) without saying \"not relevant\""
                        % ", ".join(non_relevant))
    for ground in _as_list(stmt, "grounded_in", problems):
        leg = ground.get("leg") if isinstance(ground, dict) else None
        if leg not in legs:
            problems.append("grounded_in names leg e%s the statement does not cite" % leg)
    # Every paper the statement leans on, wherever it names it -- a beyond
    # claim, the papers list, or an [N] in its own words -- is read-checked and
    # topic-checked the same way. Only beyond entries were, so an unread,
    # off-topic search hit in the papers list reached the reference list.
    claim_genes = _named_genes(" ".join([str(stmt.get("claim") or ""), prose]), walker)
    refs, genes_for = [], {}
    for beyond in _as_list(stmt, "beyond", problems):
        if not isinstance(beyond, dict):
            problems.append("a beyond entry is not an object")
            continue
        if beyond.get("hypothesis"):
            continue
        ref = paper_ref(beyond.get("paper"))
        if ref is None or ref not in papers:
            problems.append("beyond claim %r has neither a retrieved paper nor the hypothesis flag"
                            % str(beyond.get("claim", ""))[:60])
            continue
        genes_for.setdefault(ref, set()).update(_named_genes(beyond.get("claim"), walker))
        if ref not in refs:
            refs.append(ref)
    missing = set()
    for paper in _as_list(stmt, "papers", problems) + cited_refs(" ".join([str(stmt.get("claim") or ""), prose])):
        ref = paper_ref(paper)
        if ref is None or ref not in papers:
            if str(paper) not in missing:
                missing.add(str(paper))
                problems.append("paper [%s] was never retrieved" % paper)
        elif ref not in refs:
            refs.append(ref)
    for ref in refs:
        if read is None:
            continue
        if ref not in read:
            problems.append("[%d] is cited without being read: call read_paper on it first" % ref)
            continue
        genes = genes_for.get(ref) or claim_genes
        if names_genes and genes and not _paper_names_any(papers[ref], genes):
            problems.append("[%d] does not mention %s; cite a paper about the claim or mark it a hypothesis"
                            % (ref, ", ".join(sorted(genes))))
        context = papers[ref].get("context") or {}
        match = context.get("match") or {}
        if "other" in (match.get("organism"), match.get("system")):
            sentence = " ".join(citing_sentences(stmt, ref))
            if not context_named(sentence, context):
                problems.append("[%d] is a %s paper; say so where you cite it (\"in %s\"), or cite one about %s"
                                % (ref, " ".join(w for w in (context.get("organism"), context.get("system"))
                                                 if w and w != "unknown"),
                                   " ".join(w for w in (context.get("organism"), context.get("system"))
                                            if w and w != "unknown"),
                                   "this organism and system"))
    problems.extend(_invented_problems(stmt, walker, papers))
    return problems


def _invented_problems(stmt, walker, papers):
    """One objection per value the statement's claim or prose quotes as data
    (invented_values) that no layer the walk read holds (read_nodes) and no
    paper it cites states in the text the Writer has of it (title, abstract,
    every section fetched), naming the node the value is given to: the node
    whose heat it equals, else (without saying it is that node's) the node
    the walk read named nearest before it; and one per value a read node
    holds that is given to a node whose heat it is (heats_given_as_values).
    The Writer resubmits; a statement still quoting one on its last
    submission is dropped."""
    # "; ": the claim is its own clause, so its names make no group with the
    # prose's first value (heats_given_as_values) and its "hottest" no heat.
    text = "; ".join([str(stmt.get("claim") or ""), str(stmt.get("prose") or "")])
    values = data_values(text)
    if not values:
        return []
    read = read_nodes(walker, chain_nodes(walker.record()["chain"]))
    # The walker guard's heat set (heat_nodes): a heat printed only in a scan
    # or a neighbour list is as misreadable here as in a reading.
    named = heat_nodes(walker, read)
    heats = heats_of(walker.overlay, named)
    stated = set()
    for ref in statement_refs(stmt):
        paper = (papers or {}).get(ref) or {}
        blob = " ".join([str(paper.get("title") or ""), str(paper.get("abstract") or "")]
                        + [str(v or "") for v in (paper.get("sections") or {}).values()])
        for value in quoted_values(blob):
            size = _norm(value).lstrip("+−")      # a paper's figure, with either sign
            stated.update((size, "+" + size, "−" + size))
    found = invented_values(text, _record_numbers(walker, read), heats, stated)
    # A value some read node holds, given to a node whose printed heat it is
    # (heats_given_as_values): any node the walk printed a heat for.
    mentions = _mentions(text, name_catalog(walker, named))
    unheld = {at for _value, at in found}
    given = {at: (value, node_id) for value, at, node_id in heats_given_as_values(
        text, None, heats, walker.overlay, stated, values, mentions) if at not in unheld}
    if not found and not given:
        return []
    readable = set(read)
    out = []
    for value, at in sorted(found + [(value, at) for at, (value, _n) in given.items()], key=lambda f: f[1]):
        before = [m[2] for m in mentions if m[1] <= at and m[2] in readable]
        owners = heat_owners(value, heats)
        node_id = given[at][1] if at in given else \
            next((n for n in reversed(before) if n in owners), None) or (owners[0] if owners else None)
        if node_id is not None:
            label = walker.label(node_id)
            out.append("quotes %s for %s, which its layers do not hold (%s); %s is %s's heat, not a measured value"
                       % (value, label, layers_line(walker.overlay, node_id), _norm(value).lstrip("+−"), label))
        elif before:
            # Not "for" that node: the sentence may give the value to one the
            # walk never read ("Bbb falls, as does its partner Xyz (−0.70)").
            out.append("quotes %s, which no layer the walk read holds (the nearest node named before it, "
                       "%s, reads %s); quote values as the layers print them" % (
                           value, walker.label(before[-1]), layers_line(walker.overlay, before[-1])))
        else:
            out.append("quotes %s, which no layer the walk read holds; quote values as the layers print them"
                       % value)
    return out


def layers_line(overlay, node_id, lines=3):
    """A node's layer text (as the model reads it) on one line, for an objection."""
    rows = [" ".join(row.split()) for row in overlay.layer_text(node_id).splitlines()]
    return "; ".join(rows[:lines]) + ("; …" if len(rows) > lines else "")


def citing_sentences(stmt, ref):
    """The prose sentences that cite paper ``ref`` as [N] (or in a list), as
    the reader sees them; the beyond claim citing it when the prose does not;
    the statement's claim when neither does."""
    out = []
    for sentence in sentences(stmt.get("prose")):
        if ref in cited_refs(sentence):
            out.append(sentence.strip())
    if out:
        return out
    beyond = stmt.get("beyond") if isinstance(stmt.get("beyond"), (list, tuple)) else []
    for entry in beyond:
        if isinstance(entry, dict) and paper_ref(entry.get("paper")) == ref and entry.get("claim"):
            out.append(str(entry["claim"]))
    return out or [str(stmt.get("claim") or "")]


_GENERIC_SYSTEM_WORDS = {"cells", "cell", "line", "lines", "tissue", "tissues", "culture", "cultured", "primary",
                         "precursor", "precursors", "human", "mouse", "adult", "stem"}


def context_named(sentence, context):
    """Whether a sentence names the organism or the system of the paper it
    cites: "in human T cells [3]". True when the context is unknown."""
    context = context or {}
    match = context.get("match") or {}
    words = []
    if match.get("organism") == "other" and context.get("organism") not in (None, "", "unknown"):
        words.append(str(context["organism"]))
    if match.get("system") == "other" and context.get("system") not in (None, "", "unknown"):
        # the words that name the system, not the ones any sentence about cells has
        # a heading's tokens and their hyphen parts ("T-Lymphocytes" -> "T-Lymphocytes", "Lymphocytes";
        # "HL-60 Cells" -> "HL-60" only, since "HL", "60" and "Cells" name nothing)
        for token in re.split(r"[\s,/]+", str(context["system"])):
            for word in [token] + token.split("-"):
                if len(word) >= 4 and word.lower() not in _GENERIC_SYSTEM_WORDS and word not in words:
                    words.append(word)
        # and the phrases a sentence would use for the heading ("T-Lymphocytes": "T cells")
        from src.classes.AIInterpret.walker import literature
        words.extend(literature.SYNONYMS.get(str(context["system"]).strip().lower(), ()))
    if not words:
        # an organism word alone, or a system whose only names are generic:
        # nothing specific the sentence could be asked to say
        return True
    text = str(sentence or "").lower().replace("-", " ")
    return any(word.lower().replace("-", " ").rstrip("s") in text for word in words)


def statement_papers(stmt):
    """Every paper ref a statement cites, in order: its papers list, then its beyond claims."""
    refs = []
    listed = stmt.get("papers") if isinstance(stmt.get("papers"), (list, tuple)) else []
    beyond = stmt.get("beyond") if isinstance(stmt.get("beyond"), (list, tuple)) else []
    for value in list(listed) + [b.get("paper") for b in beyond
                                 if isinstance(b, dict) and not b.get("hypothesis")]:
        ref = paper_ref(value)
        if ref is not None and ref not in refs:
            refs.append(ref)
    return refs


def _worded_refs(stmt):
    """The paper refs a statement cites in its own words: [N] in its claim or prose."""
    return cited_refs(" ".join([str(stmt.get("claim") or ""), str(stmt.get("prose") or "")]))


def statement_refs(stmt):
    """The papers a Results paragraph retelling ``stmt`` may cite: the ones the
    statement cites in its fields and in its own words."""
    refs = statement_papers(stmt)
    for ref in _worded_refs(stmt):
        if ref not in refs:
            refs.append(ref)
    return refs


def owed_refs(stmt):
    """The papers a Results paragraph retelling ``stmt`` must cite: the ones
    the statement cites in its own words, its claim and its prose, in order
    (always a subset of statement_refs). A paper only in its papers list or a
    beyond claim is one the paragraph may cite but does not owe.

    The Narrator retells a statement's words, and a citation those words never
    make has no sentence to sit in. On job Aiu6NzaoQ3 (tomato proteomics
    network, re-run 2026-10-29) statement 1's prose cited no paper, while its
    fields listed [101] and a beyond claim on the phenylpropanoid pathway that
    the prose never states; the Narrator retold the prose verbatim, draft and
    repair both failed on "paragraph 1 drops [101]" alone, and the 12-statement
    walk lost its whole Results section. The other 11 statements carried their
    [N] in their prose."""
    return _worded_refs(stmt)


def _statement_by_n(kept, origin):
    try:
        origin = int(origin)
    except (TypeError, ValueError):
        return None
    return next((s for s in kept if int(s["n"]) == origin), None)


def drop_uncited_sentences(results, kept):
    """Remove every sentence of a statement paragraph that cites a paper its
    statement does not, and every summary sentence citing a paper no kept
    statement cites; return how many went. The Narrator sees only the kept
    statements' papers, but a model can still write an [N] of its own, and a
    stray reference is a literature claim nothing checked."""
    allowed_all = {ref for s in kept for ref in statement_refs(s)}
    count = {"dropped": 0}

    def keep(text, allowed):
        out = []
        for sentence in sentences(text):
            if any(ref not in allowed for ref in cited_refs(sentence)):
                count["dropped"] += 1
                continue
            out.append(sentence)
        return " ".join(out)

    paragraphs = []
    for paragraph in results.get("paragraphs") or []:
        if isinstance(paragraph, dict) and paragraph.get("from_statement") is not None:
            source = _statement_by_n(kept, paragraph.get("from_statement"))
            if source is not None:
                text = keep(paragraph.get("text"), set(statement_refs(source)))
                if not text.strip():
                    continue
                paragraph = dict(paragraph, text=text)
        paragraphs.append(paragraph)
    results["paragraphs"] = paragraphs
    if results.get("summary"):
        results["summary"] = keep(results["summary"], allowed_all)
    return count["dropped"]


def paper_ref(value):
    """A model-supplied paper reference as an int: 3, "3" or "[3]"; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*\[?\s*(\d+)\s*\]?\s*", str(value or ""))
    return int(match.group(1)) if match else None


def verify_statements(statements, walker, papers, read=None, count=None, legs=None, names_genes=True):
    """[(statement, problems)] for every statement, plus a count objection.
    ``count`` = (fewest, most) statements; ``legs`` = the Writer's part."""
    lo, hi = count or (STATEMENT_MIN, STATEMENT_MAX)
    out = [(s, verify_statement(s, walker, papers, read, legs, names_genes)) for s in statements]
    count_problem = None
    if not (lo <= len(statements) <= hi):
        count_problem = "%d statements; between %d and %d are required" % (len(statements), lo, hi)
    return out, count_problem


def citation_pairs(stmt):
    """(paper, claim) for every citation a statement makes: each beyond claim
    with its paper, then any other paper the statement cites, for its claim."""
    pairs = []
    beyond = stmt.get("beyond") if isinstance(stmt.get("beyond"), (list, tuple)) else []
    for entry in beyond:
        if isinstance(entry, dict) and not entry.get("hypothesis"):
            ref = paper_ref(entry.get("paper"))
            if ref is not None and (ref, entry.get("claim")) not in pairs:
                pairs.append((ref, str(entry.get("claim") or stmt.get("claim") or "")))
    covered = {ref for ref, _claim in pairs}
    for ref in statement_refs(stmt):
        if ref not in covered:
            pairs.append((ref, str(stmt.get("claim") or "")))
    return pairs


def drop_jargon_sentences(results):
    """Remove every Results sentence that narrates the walk (JARGON_RE); return
    how many went. Paragraphs left empty go too."""
    count = {"dropped": 0}

    def keep(text):
        out = []
        for sentence in sentences(text):
            if JARGON_RE.search(sentence):
                count["dropped"] += 1
                continue
            out.append(sentence)
        return " ".join(out)

    paragraphs = []
    for paragraph in results.get("paragraphs") or []:
        if isinstance(paragraph, dict):
            text = keep(paragraph.get("text"))
            if not text.strip():
                continue
            paragraph = dict(paragraph, text=text)
        paragraphs.append(paragraph)
    results["paragraphs"] = paragraphs
    for key in ("summary", "title"):
        if results.get(key):
            results[key] = keep(results[key])
    return count["dropped"]


def drop_meta_sentences(results):
    """Remove from the summary and the title what is in the Narrator's brief's
    own words (META_RE), and return the removed text, as written, for the
    repair round. The paragraphs are left alone (see META_RE).

    Sentence by sentence: the rest of a summary stays, so y712svrXTf keeps its
    finding ("The baseline-up module is consistent with a PI3K-suppressive
    brake ...") once its "We traced ..." is gone. When a sentence went, the
    first ones left opening on a sequencing word (SEQUENCE_LEAD_RE) go too. A
    summary that loses nothing comes back as it was, byte for byte: stored
    views are read through this. A title with any such word goes whole; the
    caller decides what stands in for it."""
    removed = []
    if not isinstance(results, dict):
        return removed
    if results.get("summary"):
        summary = []
        for sentence in sentences(results["summary"]):
            if not sentence.strip():
                continue
            (removed if META_RE.search(sentence) else summary).append(sentence)
        if removed:
            while summary and SEQUENCE_LEAD_RE.match(summary[0].lstrip()):
                removed.append(summary.pop(0))
            results["summary"] = " ".join(summary)
    if results.get("title") and META_RE.search(str(results["title"])):
        removed.append(str(results["title"]))
        results["title"] = ""
    return removed


def _clip(text, width):
    """``text`` on one line, cut at a word boundary to at most ``width``
    characters with "…" when it was longer: how a repair note quotes a
    sentence the pruners removed."""
    text = " ".join(str(text or "").split())
    if len(text) <= width:
        return text
    return text[:width].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def meta_note(removed):
    """The repair round's objection for a summary drop_meta_sentences pruned:
    which sentences went and what to write instead. It joins the objections
    of a repair the section's problems asked for; it never asks for one, and
    is never one of the problems itself."""
    quoted = ["\"%s\"" % _clip(sentence, 60) for sentence in removed[:3]]
    return ("code removed %s of your summary or title that restated your instructions instead of reporting "
            "the biology (%s%s); write the summary in plain biological terms: what changed in this "
            "experiment, naming the genes, as a paper's own summary of its results, never the rules, the "
            "statements or how the section was planned" % (
                "a sentence" if len(removed) == 1 else "%d sentences" % len(removed), ", ".join(quoted),
                ", ..." if len(removed) > 3 else ""))


# Owed citations a paragraph lost only with sentences removed for a misquoted
# value: their claim went too, so that paragraph alone no longer owes them; no
# other pruner excuses (the [80] case: CHANGELOG, PR #205). Stripped before storage.
LET_GO = "_let_go"


def drop_unrecorded_sentences(results, walker, kept=None, removed=None):
    """Remove every sentence of the Results that quotes a value the record does
    not hold, and return how many went. A misquoted number is a sentence the
    checks cannot stand behind; dropping it keeps the rest of a section that is
    otherwise grounded, where rejecting the whole section on one "+1.62" left a
    job with statements and no Results at all. Paragraphs left empty go too.

    With ``kept``, the kept statements, a sentence of a paragraph retelling one
    may also quote what a paper it cites states (unrecorded_values), and a
    paragraph that loses an owed citation this way is marked (LET_GO). With
    ``removed``, a list, every sentence removed with a citation its statement
    makes adds a line saying which and why, for the Narrator's repair round."""
    numbers = _record_numbers(walker)
    count = {"dropped": 0}

    def keep_quoted(text, source=None, where=None):
        """(the sentences kept, the owed citations they no longer carry)."""
        out, gone = [], set()
        source_refs = set(statement_refs(source)) if source is not None else set()
        owed = set(owed_refs(source)) if source is not None else set()
        for sentence in sentences(text):
            values = unrecorded_values(sentence, numbers, source, source_refs)
            if not values:
                out.append(sentence)
                continue
            count["dropped"] += 1
            refs = [ref for ref in cited_refs(sentence) if ref in source_refs]
            gone.update(owed.intersection(refs))
            if removed is not None and refs:
                removed.append(_removal_note(where, source, sentence, refs, values))
        text = " ".join(out)
        return text, gone - set(cited_refs(text))

    paragraphs = []
    for i, paragraph in enumerate(results.get("paragraphs") or [], 1):
        if not isinstance(paragraph, dict):
            paragraphs.append(paragraph)          # verify_results objects to it
            continue
        source = _statement_by_n(kept or (), paragraph.get("from_statement"))
        text, let_go = keep_quoted(paragraph.get("text"), source, i)
        if text.strip():
            paragraph = dict(paragraph, text=text)
            paragraph.pop(LET_GO, None)           # set here only, never by the model's answer
            if let_go:
                paragraph[LET_GO] = sorted(let_go)
            paragraphs.append(paragraph)
    results["paragraphs"] = paragraphs
    if results.get("summary"):
        results["summary"] = keep_quoted(results["summary"])[0]
    return count["dropped"]


def pop_let_go(results):
    """{statement n, as text: the citations its paragraphs let go (LET_GO)},
    with the marks removed from ``results``."""
    out = {}
    for paragraph in (results or {}).get("paragraphs") or []:
        refs = paragraph.pop(LET_GO, None) if isinstance(paragraph, dict) else None
        if refs:
            out.setdefault(str(paragraph.get("from_statement")), set()).update(refs)
    return {n: sorted(refs) for n, refs in out.items()}


def _removal_note(where, source, sentence, refs, values):
    """The repair round's account of a sentence the pruner took out although it
    carried its statement's citation. Told only "paragraph 1 drops [5]", the
    Narrator re-sent the same sentence, which already had [5]."""
    cites = ", ".join("[%d]" % ref for ref in refs)
    passage = ("%s's confirmed passage" % cites if len(refs) == 1
               else "the confirmed passages of %s" % cites)
    return ("paragraph %d (from statement %s): the sentence \"%s\" citing %s was removed because it quotes %s, "
            "which neither the record nor %s holds; keep %s and quote only the record's values or the paper's"
            % (where, source.get("n"), _clip(sentence, 90), cites, " and ".join(dict.fromkeys(values)), passage,
               cites))


def paper_numbers(stmt, ref):
    """The values a statement's confirmed evidence for paper ``ref`` states, in
    the claim a paper agent checked or the passage it quoted, normalised as the
    record's are.

    A Results sentence retelling a cited finding quotes the paper's numbers,
    and the record never holds them. On job 7O521E0Y0y (hsa05219, 2026-10-28)
    the Narrator kept every citation; the pruner removed "FGFR3 mutations were
    mutually exclusive with RAS mutations (p = 0.001) [5]" and "overexpressed
    in 31.2% and downregulated in 25.0% ... [119]" as unrecorded, the
    verifier then found [5] and [119] dropped, the repair re-sent the same
    sentences, and both runs that day lost their whole Results section. [5]'s
    passage reads "(p = 0.001)", [119]'s holds 31.2% and 25.0%. Only these
    confirmed fields count, never the statement's prose: the Verifier checks
    only the prose numbers that read as data, signed or a heat's size
    (invented_values), and an unsigned figure such as "31.2%" passes it
    unchecked."""
    evidence = stmt.get("evidence") if isinstance(stmt.get("evidence"), (list, tuple)) else []
    tokens = set()
    for item in evidence:
        if isinstance(item, dict) and paper_ref(item.get("ref")) == ref:
            for field in ("claim", "quote"):
                tokens.update(_norm(value) for value in quoted_values(item.get(field)))
    return tokens


def unrecorded_values(sentence, numbers, source=None, source_refs=None):
    """The values a Results sentence quotes and may not: in neither the record
    (``numbers``) nor, in a paragraph retelling statement ``source``, the
    confirmed evidence of a paper the sentence cites and the statement cites
    (paper_numbers). A misquoted logFC beside a citation stays unrecorded, the
    paper's passage does not hold it; so does a paper's number in a sentence
    that cites nothing, in the summary or in a link paragraph.

    ``source_refs`` is statement_refs(source) as a set, for a caller going
    through a paragraph sentence by sentence; computed here when omitted."""
    out, stated = [], None
    for value in quoted_values(sentence):
        if _in_record(value, numbers):
            continue
        if stated is None:
            stated = set()
            if source is not None:
                if source_refs is None:
                    source_refs = set(statement_refs(source))
                for ref in set(cited_refs(sentence)) & source_refs:
                    stated |= paper_numbers(source, ref)
        if not _in_record(value, stated):
            out.append(value)
    return out


def _record_numbers(walker, nodes=None):
    """Every value token that appears in the layer text of any node the walk
    read (``nodes``, read_nodes when None): every layer's, not the
    MEMBERS_SHOWN a model reads (overlay.capped_layers). The record holds
    what the user measured; a sentence quoting a member's value the walker's
    cap left out is quoting the user's data, not inventing it."""
    return node_numbers(walker.overlay, read_nodes(walker) if nodes is None else nodes)


def heat_nodes(walker, value_nodes):
    """The nodes whose printed heat a text may misread as a value: the value
    nodes (read_nodes, or the walker guard's readable set), then every node
    any answer printed a heat for (Walker.heats_shown). The one definition
    the walker guard and the Verifier share, so their heat sets cannot drift."""
    return list(dict.fromkeys(list(value_nodes) + sorted(getattr(walker, "heats_shown", ()) or ())))


def read_nodes(walker, chain=None):
    """The nodes whose layers the walk read: every chain node, in walk order,
    then every node whose layers block an answer printed (Walker.layers_shown,
    the walker guard's own set; a relevant neighbour is shown and never
    stepped onto). Chain-only, the Verifier dropped three true statements of
    round heat2 (2026-10-31): "Gli2 (+1.75) and Gli3 (+1.75)" on vRmKBo4E60,
    "Cyp51 (Control +0.51) and Msmo1 (Control +0.61)" twice on Bgm0v4EU01,
    each a value of a relevant neighbour the walker had quoted in a reading.
    getattr: a walker rebuilt from a record or by a test may lack the set.
    ``chain``: chain_nodes, when the caller has them."""
    nodes = list(chain) if chain is not None else chain_nodes(walker.record()["chain"])
    chained = set(nodes)
    return nodes + sorted(n for n in getattr(walker, "layers_shown", ()) or () if n not in chained)


def node_numbers(overlay, node_ids):
    """Every value token in these nodes' layer text, every layer of each."""
    tokens = set()
    for node_id in node_ids:
        tokens |= node_tokens(overlay, node_id)
    return tokens


def node_tokens(overlay, node_id):
    """One node's value tokens (every layer), kept in ``overlay.value_tokens``.
    The walker guard asks for every readable node on every move and the
    Verifier for the chain on every statement; rendering and scanning the
    layer text each time is what this saves. The entry is keyed by what
    layer_text prints of each layer, so a layer edited or added in place
    (the direction tests rewrite one after the walk) is read afresh."""
    cache = getattr(overlay, "value_tokens", None)
    layers = (getattr(overlay, "layers", None) or {}).get(node_id) or []
    key = tuple((l.get("omic"), l.get("member"), l.get("relevant"), l.get("text")) if isinstance(l, dict) else l
                for l in layers)
    hit = cache.get(node_id) if cache is not None else None
    if hit is not None and hit[0] == key:
        return hit[1]
    tokens = frozenset(_norm(value) for value in quoted_values(overlay.layer_text(node_id, limit=None)))
    if cache is not None:
        cache[node_id] = (key, tokens)
    return tokens


def heats_of(overlay, node_ids):
    """{a heat as the walker prints it, normalised: [node ids with that heat]}
    for these nodes; a heat of 0.00 says nothing and is left out."""
    out = {}
    for node_id in node_ids:
        heat = round(((getattr(overlay, "heat", None) or {}).get(node_id) or {}).get("heat", 0.0), 2)
        if heat > 0:
            out.setdefault(_norm("%.2f" % heat), []).append(node_id)
    return out


def heat_owners(value, heats):
    """The nodes whose heat a quoted value's size equals."""
    return heats.get(_norm(value).lstrip("+−"), [])


def invented_values(text, numbers, heats, also=()):
    """(value, position) for every value ``text`` quotes as data (data_values)
    that is in neither ``numbers``, the layers' value tokens, nor ``also``,
    and that reads as a measurement: signed, as a layer prints every reading,
    or, unsigned, the size of a heat in ``heats`` (heats_of).

    The walker printed a neighbour's heat after its name, "rrm1 r=0 2.43", and
    showed an r=0 neighbour's layers to no one; on the zebrafish job
    74Qe0uN7n6 (re-run 2026-10-31) a reading stepping there wrote "Gene
    expression on rrm1 shows log2FC +2.43 (relevant), a strong induction",
    where the layer reads +0.12, not relevant, and the Writer kept "rrm1
    (+2.43) and rrm2b (+2.30) are strongly induced". 15 of that round's 19
    network walks quoted a heat as a value. An unsigned number unlike any
    heat is left alone: "31.2%", "p = 0.001" and "2.5 µM" are a paper's or the
    design's, and the Narrator's check (unrecorded_values) still reads every
    number of the Results. Nor is an unsigned heat written as one without the
    word (heat_phrased)."""
    text = str(text or "")
    out = []
    for value, at in data_values(text):
        if _in_record(value, numbers) or _in_record(value, also):
            continue
        if _norm(value)[:1] in ("+", "−"):
            out.append((value, at))
        elif heat_owners(value, heats) and not heat_phrased(text, at, at + len(value)):
            out.append((value, at))
    return out


# How a walker writes a heat without the word: with its count after it, "(2.43,
# 2 of 19 relevant)", or after "hot" in the same clause, "the hottest branch
# (cysC/cysQ at 2.68, cysA/P/U/W at 3.15)". In the final3 records of 2026-10-31
# 55 of 1846 legs would have been refused for nothing else. HOT_WINDOW is wide
# enough for that 3.15, 49 characters after "hottest"; a sentence end (". ")
# or a semicolon closes the clause, a decimal point does not. "Hot" does not
# make a measurement a heat: "Ccc, the hottest node, rises 2.43-fold" gives the
# number a unit (MEASURE_AFTER_RE) and a verb of change or a value label
# between "hottest" and it (MEASURE_BEFORE_RE); either keeps it data. The count
# rule stands alone: a number with "2 of 19" after it is a heat however worded.
HEAT_COUNT_AFTER_RE = re.compile(r"^\s*[,(]\s*\d+\s+of\s+\d+(?:\s+(?:relevant|neighbours))?")
HOT_RE = re.compile(r"\bhot(?:ter|test)?\b", re.I)
CLAUSE_END_RE = re.compile(r"[.!?](?=\s)|;")
HOT_WINDOW = 60
# A list a walker opens with one heat named and continues bare: "Metabolite
# nodes are hot without gene layers: C00074 heat 4.91, C00206 3.88, C00236
# 3.87" (74Qe0uN7n6, round heat1, 2026-10-31; 3.87 is C00236's heat). The
# whole clause is read, not HOT_WINDOW: a list runs long.
HEAT_NUMBER_RE = re.compile(r"\bheat(?:\s+score)?(?:\s+(?:of|is|was))?\s*[:=≈]?\s*\d+\.\d+", re.I)
MEASURE_AFTER_RE = re.compile(r"^(?:\s*-?\s*fold\b|\s*[×%]|\s*x\b|\s*(?:[µunpm]M|kDa)\b)", re.I)
MEASURE_BEFORE_RE = re.compile(
    r"\b(?:ris(?:e|es|ing|en)|rose|fall(?:s|ing|en)?|fell|increas(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)"
    r"|up|down|show(?:s|ed|ing)?|log2?\s?FC|log2?\s?fold\s?change|log2FoldChange|FC|fold change)\b", re.I)


def heat_phrased(text, start, end):
    """Whether the unsigned number at text[start:end] is written as a heat
    without the word: followed by its count, or after "hot", "hotter" or
    "hottest" or after an earlier "heat <number>" in its clause, with
    nothing that writes it as a measurement (a unit after it, a verb of
    change or a value label between). A signed number never is: "+2.43" is
    data."""
    if HEAT_COUNT_AFTER_RE.match(text[end:]):
        return True
    if MEASURE_AFTER_RE.match(text[end:]):
        return False
    whole = CLAUSE_END_RE.split(text[:start])[-1]
    listed = list(HEAT_NUMBER_RE.finditer(whole))
    if listed and not MEASURE_BEFORE_RE.search(whole[listed[-1].end():]):
        return True
    clause = CLAUSE_END_RE.split(text[max(0, start - HOT_WINDOW):start])[-1]
    hot = list(HOT_RE.finditer(clause))
    return bool(hot) and not MEASURE_BEFORE_RE.search(clause[hot[-1].end():])


def name_catalog(walker, node_ids):
    """(node id, bare name) for every name a text may call these nodes by."""
    return [(node_id, name) for node_id in node_ids for name in _node_names(walker, node_id)]


def heats_given_as_values(text, catalog, heats, overlay, also=(), values=None, mentions=None):
    """(value, position, node) for every value a text quotes as data
    (data_values) and gives to the node it names nearest before it in the
    clause, from ``catalog`` (name_catalog), when the value's size is that
    node's printed heat (``heats``, heats_of), the node's own layers do not
    hold it, and it is not written as a heat (heat_phrased; a signed value
    never is).

    A pooled record misses these: on Bgm0v4EU01 (round heat2, 2026-10-31) a
    reading gave "Pkm ... shows Control +0.52, up like Ranbp9 (+0.52)", and
    Pkm reads +0.04 with heat 0.52; Ranbp9 holds +0.52, so the value was in
    the walk's record and passed. "Gli1 (+1.77) ... Gli2 (+1.75)" with
    Gli2's heat 1.77 is not one: +1.77 sits after Gli1. Nor is a value after
    a group of names, "Ranbp9 and Pkm both rise (+0.52, +0.04)": another
    node named since the clause opened or the last value before the nearest
    name means the values may go to the names in order, and the nearest name
    is not the owner. A value ``also`` holds (a cited paper's) is left alone.

    ``values`` (data_values) and ``mentions`` (_mentions over ``catalog``):
    for a caller that has them."""
    text = str(text or "")
    values = data_values(text) if values is None else values
    if not values:
        return []
    heat_of = {node_id: token for token, nodes in heats.items() for node_id in nodes}
    mentions = _mentions(text, catalog) if mentions is None else mentions
    out = []
    for value, at in values:
        opened = [m.end() for m in CLAUSE_END_RE.finditer(text, 0, at)]
        before = [m for m in mentions if m[0] >= (opened[-1] if opened else 0) and m[1] <= at]
        if not before:
            continue
        nearest = before[-1]
        node_id, token = nearest[2], _norm(value)
        if heat_of.get(node_id) != token.lstrip("+−") or _in_record(value, node_tokens(overlay, node_id)) \
                or _in_record(value, also):
            continue
        since = max([v_at + len(v) for v, v_at in values if v_at + len(v) <= nearest[0]] + [0])
        if any(m[2] != node_id for m in before if m[0] >= since):
            continue                                   # a name group: "Ranbp9 and Pkm (+0.52, ...)"
        if token[:1] not in ("+", "−") and heat_phrased(text, at, at + len(value)):
            continue
        out.append((value, at, node_id))
    return out


_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789−")
_PARTS_RE = re.compile(r"^([+\-−]?)(\d+(?:\.\d+)?)(?:[eE]|[×x]10\^?)?([\-−]\d+)?$")
# Below this a value is compared in scientific form, however it was written.
_SMALL = Decimal("0.01")


def _norm(token):
    """One spelling per value: spaces out, a minus as "−", and a small value
    (a written exponent, or a magnitude under 0.01) in one scientific form,
    "+3.2e-05", "3.2×10⁻⁵" and "0.000032" all becoming "3.2e-5" with the sign
    they were written with. Values of 0.01 and over, and an exact 0, compare
    as written, as they always have -- and one of those written with an
    exponent compares in the fixed form overlay.value_text prints it in, two
    decimals at least: 0.00996 is printed "+0.01", and "1.0e-02" and "1e-2"
    quote it as well as "0.01" does."""
    token = token.replace(" ", "").translate(_SUPERSCRIPTS)
    match = _PARTS_RE.match(token)
    if match:
        sign, mantissa, exponent = match.groups()
        try:
            value = Decimal(mantissa)
            if exponent:
                value = value.scaleb(int(exponent.replace("−", "-")))
        except (InvalidOperation, ValueError):
            value = None
        sign = {"": "", "+": "+"}.get(sign, "−")
        if value is not None and (exponent or 0 < value < _SMALL):
            if value < _SMALL:
                return sign + format(value.normalize(), "e")
            fixed = value.normalize()
            try:
                fixed = fixed if fixed.as_tuple().exponent < -2 else fixed.quantize(_SMALL)
            except InvalidOperation:        # more digits than the context holds: as written
                return token.replace("-", "−")
            return sign + format(fixed, "f")
    return token.replace("-", "−")


def _in_record(token, numbers):
    """A quoted value is in the record: a signed one verbatim, an unsigned one
    with either sign (a magnitude of a recorded value)."""
    token = _norm(token)
    if token[:1] in ("+", "−"):
        return token in numbers
    return token in numbers or ("+" + token) in numbers or ("−" + token) in numbers


def _words(text):
    """A text as the dropped-statement check compares it: whitespace runs as
    one space, lower case."""
    return " ".join(str(text or "").split()).lower()


UNCOVERED = "statement %d is not covered"
_UNCOVERED_RE = re.compile("^%s$" % re.escape(UNCOVERED).replace("%d", r"(\d+)"))
# The statement is printed as the Narrator tagged it (%s): it need not be a number.
NOT_KEPT = "paragraph %d comes from statement %s, which was not kept"
_NOT_KEPT_RE = re.compile("^%s$" % re.escape(NOT_KEPT).replace("%d", r"(\d+)").replace("%s", r".+"))


def mendable_by_code(problems):
    """What code mends in verify_results' ``problems`` without a model, when
    that is every problem: {"uncovered": the statements no paragraph retells,
    "not_kept": the paragraphs (numbered from 1, as the problems number them)
    retelling a statement that was not kept, "short": the word count of a
    section under its fewest words}. None when any other problem is among
    them, or when there is nothing to insert or remove."""
    found = {"uncovered": [], "not_kept": [], "short": []}
    for problem in problems or ():
        uncovered, not_kept = _UNCOVERED_RE.match(str(problem)), _NOT_KEPT_RE.match(str(problem))
        if uncovered:
            found["uncovered"].append(int(uncovered.group(1)))
        elif not_kept:
            found["not_kept"].append(int(not_kept.group(1)))
        elif too_few_words(problem):
            found["short"].append(problem)
        else:
            return None
    if not (found["uncovered"] or found["not_kept"]) or len(found["short"]) > 1:
        return None
    return found


WORD_COUNT = "%d words; between %d and %d are required"
_WORD_COUNT_RE = re.compile("^%s$" % re.escape(WORD_COUNT).replace("%d", r"(\d+)"))


def too_few_words(problem):
    """True when ``problem`` is verify_results' word count for a section
    under its fewest words; False for one over its most, and for any other
    problem. Read from the numbers the problem carries, not its wording
    alone."""
    match = _WORD_COUNT_RE.match(str(problem))
    if match is None:
        return False
    words, lo, _hi = (int(g) for g in match.groups())
    return words < lo


def verify_results(results, kept, dropped, walker, scope="pathway", words_range=None):
    """Objections to the Narrator's Results section. ``words_range`` = (fewest,
    most) words; RESULTS_WORDS by scope otherwise."""
    problems = []
    chain = walker.record()["chain"]
    paragraphs = results.get("paragraphs") or []
    if not paragraphs:
        return ["no paragraphs"]
    kept_ids = {int(s["n"]) for s in kept}
    covered = set()
    words = 0
    numbers = _record_numbers(walker)
    allowed_all = {ref for s in kept for ref in statement_refs(s)}
    for ref in cited_refs(results.get("summary")):
        if ref not in allowed_all:
            problems.append("the summary cites [%d], which no kept statement cites" % ref)
    for i, para in enumerate(paragraphs, 1):
        if not isinstance(para, dict):
            problems.append("paragraph %d is not an object" % i)
            continue
        text = str(para.get("text") or "")
        words += len(re.sub(r"\[[^\]]*\]", "", text).split())
        for leg in cited_legs(text):
            if int(leg) < 1 or int(leg) > len(chain):
                problems.append("paragraph %d cites leg e%s, not on the chain" % (i, leg))
        normalise_legs(para)
        para_legs = para.get("legs") if isinstance(para.get("legs"), (list, tuple)) else []
        for leg in para_legs:
            if not isinstance(leg, int) or leg < 1 or leg > len(chain):
                problems.append("paragraph %d tags leg e%s, not on the chain" % (i, leg))
        origin = para.get("from_statement")
        source, source_refs = None, set()
        if origin is None:
            if quoted_values(text):
                problems.append("link paragraph %d quotes a value" % i)
            if PAPER_RE.search(re.sub(r"\[e\d+\]", "", text)):
                problems.append("link paragraph %d cites a paper" % i)
        else:
            source = _statement_by_n(kept, origin)
            if source is None:
                problems.append(NOT_KEPT % (i, origin))
            else:
                # A "..." paragraph retells nothing: its statement is not covered.
                if not is_placeholder(text):
                    covered.add(int(source["n"]))
                source_refs = set(statement_refs(source))
                # A paragraph retells its statement's literature claims, so it
                # keeps their citations: the network Narrator once wrote "Slc7a11
                # encodes the xCT subunit ... central to ferroptosis" with the [1]
                # dropped. Only the citations its statement makes in its own
                # words are owed (owed_refs): Aiu6NzaoQ3 lost its Results on a
                # [101] only statement 1's fields carried. Except the ones this
                # paragraph let go with a misquoting sentence (LET_GO).
                quoted = set(cited_refs(text))
                let_go = set(para.get(LET_GO) or ())
                for ref in owed_refs(source):
                    if ref not in quoted and ref not in let_go:
                        problems.append("paragraph %d drops [%d], which its statement cites" % (i, ref))
                # ...and cites nothing its statement does not.
                for ref in sorted(quoted - source_refs):
                    problems.append("paragraph %d cites [%d], which its statement does not cite" % (i, ref))
        # Sentence by sentence, as drop_unrecorded_sentences decides, so a
        # sentence the pruner keeps is never the one this check objects to.
        for sentence in sentences(text):
            for value in unrecorded_values(sentence, numbers, source, source_refs):
                problems.append("paragraph %d quotes %s, not in the record" % (i, value))
        for label in timing_on_unlabeled(text, walker):
            problems.append("paragraph %d gives %s a timing or order word, but its layer has no column labels"
                            % (i, label))
        if JARGON_RE.search(text):
            problems.append("paragraph %d narrates the walk (%s); report the biology and name the genes"
                            % (i, JARGON_RE.search(text).group(0)))
    for n in sorted(kept_ids - covered):
        problems.append(UNCOVERED % n)
    # What the kept statements say in their own words, for the check below.
    kept_words = [_words(s.get(field)) for s in kept for field in ("claim", "prose")]
    for stmt in dropped:
        # The first eight words of a dropped claim, not five: "Syk gene
        # expression surges late" is how any kept statement about Syk begins,
        # and the direction check now drops statements about the same genes
        # the kept ones name. Nor eight words a kept statement says too: on
        # job sG3755d52w (E. coli proteomics network, 2026-10-29) kept 9 and
        # dropped 13 both opened "cysD (P21156, −1.83) and cysN (P23845,
        # −1.49) are", the Narrator retold 9 faithfully, draft and repair both
        # failed on "a dropped statement is mentioned" alone, and the 7-statement
        # walk lost its Results. Such a head says nothing of the dropped one.
        head = _words(" ".join(str(stmt.get("claim", "")).split()[:8]))
        if len(head.split()) < 8 or any(head in words for words in kept_words):
            continue
        if any(head in _words(p.get("text")) for p in paragraphs if isinstance(p, dict)):
            problems.append("a dropped statement is mentioned: %r" % head)
    lo, hi = words_range or RESULTS_WORDS["network" if scope == "network" else "pathway"]
    if not (lo <= words <= hi):
        problems.append(WORD_COUNT % (words, lo, hi))
    return problems
