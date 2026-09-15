"""What code checks about statements and about the Results text.

The Writer and the Narrator are models; nothing they say reaches the user
until these checks pass. Every check here is mechanical: a citation resolves
or it does not, a number is in the record verbatim or it is not.
"""
from __future__ import annotations

import re

# A quoted value is a decimal: signed as the layer text prints it (+2.13, −0.35),
# or unsigned when a sentence gives a magnitude ("fell by 3.24"), which must be
# the record's value with either sign. "18-24h" is a range, not a value, and a
# number after ±, < or > (±0.6, |value| > 1.00) is a bound, not a reading.
NUMBER_RE = re.compile(r"(?<![\w.±<>≤≥|])[+\-−]?\d+\.\d+")
BOUND_BEFORE_RE = re.compile(r"[±<>≤≥|]\s*$")


def quoted_values(text):
    """The values a text quotes, as written: NUMBER_RE's matches less the bounds
    written with a space after the comparator ("> 1.00", "p < 0.05")."""
    text = str(text or "")
    return [m.group(0) for m in NUMBER_RE.finditer(text)
            if not BOUND_BEFORE_RE.search(text[max(0, m.start() - 3):m.start()])]
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
STATEMENT_MIN, STATEMENT_MAX = 3, 5
RESULTS_WORDS = {"pathway": (150, 450), "network": (300, 900)}
# The walker's working words. In a statement or a Results section they narrate
# the walk instead of reporting biology: "the calcium cluster read led to the
# Syk kinase seed via a jump". Name the genes instead. Only the unambiguous
# forms are refused: "seed" is ordinary in a plant job and in a miRNA's seed
# region, and "a jump at 12h" describes values, so those two are left to the
# prompts.
JARGON_RE = re.compile(r"\b(clusters?|the walk(?:er)?|via a jump|jump(?:s|ed|ing)? (?:to|from|back))\b", re.I)


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
    at one position the longest name wins and overlapping matches are dropped."""
    found = []
    for node_id, name in catalog:
        for match in re.finditer(_names_re(name), sentence, re.I):
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
            if len(part) >= 3 and re.search(_names_re(part), text, re.I):
                names.add(part)
    return names


def _paper_names_any(paper, genes):
    blob = " ".join([str(paper.get("title") or ""), str(paper.get("abstract") or ""),
                     str((paper.get("sections") or {}).get("abstract") or "")])
    return any(re.search(_names_re(g), blob, re.I) for g in genes)


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
            problems.append("cite %r is not a node on the chain" % (cite[0],))
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
    return problems


def citing_sentences(stmt, ref):
    """The prose sentences that cite paper ``ref`` as [N] (or in a list), as
    the reader sees them; the beyond claim citing it when the prose does not;
    the statement's claim when neither does."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", str(stmt.get("prose") or "")):
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


def statement_refs(stmt):
    """The papers a Results paragraph retelling ``stmt`` may cite: the ones the
    statement cites in its fields and in its own words."""
    refs = statement_papers(stmt)
    for ref in cited_refs(" ".join([str(stmt.get("claim") or ""), str(stmt.get("prose") or "")])):
        if ref not in refs:
            refs.append(ref)
    return refs


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
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", str(text or "")):
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
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", str(text or "")):
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


def drop_unrecorded_sentences(results, walker):
    """Remove every sentence of the Results that quotes a value the record does
    not hold, and return how many went. A misquoted number is a sentence the
    checks cannot stand behind; dropping it keeps the rest of a section that is
    otherwise grounded, where rejecting the whole section on one "+1.62" left a
    job with statements and no Results at all. Paragraphs left empty go too."""
    numbers = _record_numbers(walker)
    count = {"dropped": 0}

    def keep_quoted(text):
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", str(text or "")):
            if any(not _in_record(value, numbers) for value in quoted_values(sentence)):
                count["dropped"] += 1
                continue
            kept.append(sentence)
        return " ".join(kept)

    paragraphs = []
    for paragraph in results.get("paragraphs") or []:
        if not isinstance(paragraph, dict):
            paragraphs.append(paragraph)          # verify_results objects to it
            continue
        text = keep_quoted(paragraph.get("text"))
        if text.strip():
            paragraphs.append(dict(paragraph, text=text))
    results["paragraphs"] = paragraphs
    if results.get("summary"):
        results["summary"] = keep_quoted(results["summary"])
    return count["dropped"]


def _record_numbers(walker):
    """Every value token that appears in any chain node's layer text."""
    tokens = set()
    for node_id in chain_nodes(walker.record()["chain"]):
        for value in quoted_values(walker.overlay.layer_text(node_id)):
            tokens.add(_norm(value))
    return tokens


def _norm(token):
    return token.replace(" ", "").replace("-", "−").replace("+", "+")


def _in_record(token, numbers):
    """A quoted value is in the record: a signed one verbatim, an unsigned one
    with either sign (a magnitude of a recorded value)."""
    token = _norm(token)
    if token[:1] in ("+", "−"):
        return token in numbers
    return token in numbers or ("+" + token) in numbers or ("−" + token) in numbers


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
        para_legs = para.get("legs") if isinstance(para.get("legs"), (list, tuple)) else []
        for leg in para_legs:
            if not isinstance(leg, int) or leg < 1 or leg > len(chain):
                problems.append("paragraph %d tags leg e%s, not on the chain" % (i, leg))
        origin = para.get("from_statement")
        if origin is None:
            if quoted_values(text):
                problems.append("link paragraph %d quotes a value" % i)
            if PAPER_RE.search(re.sub(r"\[e\d+\]", "", text)):
                problems.append("link paragraph %d cites a paper" % i)
        else:
            source = _statement_by_n(kept, origin)
            if source is None:
                problems.append("paragraph %d comes from statement %s, which was not kept" % (i, origin))
            else:
                covered.add(int(source["n"]))
                # A paragraph retells its statement's literature claims, so it
                # keeps their citations: the network Narrator once wrote "Slc7a11
                # encodes the xCT subunit ... central to ferroptosis" with the [1]
                # dropped.
                quoted = set(cited_refs(text))
                for ref in statement_papers(source):
                    if ref not in quoted:
                        problems.append("paragraph %d drops [%d], which its statement cites" % (i, ref))
                # ...and cites nothing its statement does not.
                for ref in sorted(quoted - set(statement_refs(source))):
                    problems.append("paragraph %d cites [%d], which its statement does not cite" % (i, ref))
        for value in quoted_values(text):
            if not _in_record(value, numbers):
                problems.append("paragraph %d quotes %s, not in the record" % (i, value))
        for label in timing_on_unlabeled(text, walker):
            problems.append("paragraph %d gives %s a timing or order word, but its layer has no column labels"
                            % (i, label))
        if JARGON_RE.search(text):
            problems.append("paragraph %d narrates the walk (%s); report the biology and name the genes"
                            % (i, JARGON_RE.search(text).group(0)))
    for n in kept_ids - covered:
        problems.append("statement %d is not covered" % n)
    for stmt in dropped:
        # The first eight words of a dropped claim, not five: "Syk gene
        # expression surges late" is how any kept statement about Syk begins,
        # and the direction check now drops statements about the same genes
        # the kept ones name.
        head = " ".join(str(stmt.get("claim", "")).split()[:8]).lower()
        if len(head.split()) >= 8 and any(head in str(p.get("text", "")).lower()
                                          for p in paragraphs if isinstance(p, dict)):
            problems.append("a dropped statement is mentioned: %r" % head)
    lo, hi = words_range or RESULTS_WORDS["network" if scope == "network" else "pathway"]
    if not (lo <= words <= hi):
        problems.append("%d words; between %d and %d are required" % (words, lo, hi))
    return problems
