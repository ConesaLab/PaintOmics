"""Edge tiers, currency metabolites, and the verbs a title may use.

Check 1 (the walk is not a graph artifact) and check 2 (the title and the
summary do not outrun the body) share one idea: a relation the databases draw
is either a *mechanism* -- signed, directed, one molecule acting on another --
or an *association* -- two molecules seen together, bound, sharing a
metabolite, or paired by a prediction. A statement inherits the weaker tier of
the legs it rests on, and a title may say "drives" only when a statement of the
mechanism tier stands behind it.

Currency metabolites (ATP, water, NAD, phosphate, ...) join half of metabolism
to the other half. A leg through one says nothing, so they are never walked,
never a seed, and a gene-gene relation the KGML draws *through* one is not an
edge of the walk network at all.
"""
from __future__ import annotations

import re

# KEGG compound ids of the currency metabolites. The user's list names ATP, Pi,
# PPi, NAD and water; the rest are the same kind of molecule.
CURRENCY = frozenset({
    "C00001",   # H2O
    "C00002",   # ATP
    "C00008",   # ADP
    "C00020",   # AMP
    "C00009",   # orthophosphate (Pi)
    "C00013",   # diphosphate (PPi)
    "C00003",   # NAD+
    "C00004",   # NADH
    "C00005",   # NADPH
    "C00006",   # NADP+
    "C00080",   # H+
    "C00007",   # O2
    "C00011",   # CO2
    "C00010",   # CoA
    "C00044",   # GTP
    "C00035",   # GDP
    "C00144",   # GMP
    "C00075",   # UTP
    "C00015",   # UDP
    "C00105",   # UMP
    "C00063",   # CTP
    "C00112",   # CDP
    "C00055",   # CMP
    "C00027",   # H2O2
    "C00014",   # NH3
    "C00019",   # S-adenosyl-L-methionine
    "C00021",   # S-adenosyl-L-homocysteine
})

MECHANISM, ASSOCIATION = "mechanism", "association"

# KGML subtype names that make a relation a mechanism, beyond the signed ones
# the network already reads (activation, expression, inhibition, repression,
# dephosphorylation).
_MECHANISM_SUBTYPES = {"activation", "expression", "inhibition", "repression", "dephosphorylation",
                       "phosphorylation", "ubiquitination", "glycosylation", "methylation"}
# A relation carrying one of these is an association whatever else it says:
# the arrow is indirect, or a binding, or drawn through a shared metabolite.
_ASSOCIATION_SUBTYPES = {"indirect effect", "binding/association", "compound", "dissociation",
                         "state change", "missing interaction"}


def is_currency(node_id):
    """A walk node id ("c:C00002") of a currency metabolite."""
    node_id = str(node_id or "")
    return node_id.startswith("c:") and node_id[2:] in CURRENCY


def edge_tier(edge):
    """1 (mechanism) or 2 (association) for a leg's edge record {db, subtype}."""
    if not edge:
        return 2
    db = str(edge.get("db") or "")
    subtype = str(edge.get("subtype") or "")
    names = {part.strip() for part in subtype.split(",") if part.strip()}
    if db == "anchor":
        return 1
    if db == "job":                                  # the job's own miRNA pairing: a prediction
        return 2
    if db == "KEGG":
        if names & _ASSOCIATION_SUBTYPES:
            return 2
        if names & _MECHANISM_SUBTYPES:
            return 1
        if any(name.startswith("rn:") for name in names):   # enzyme -> metabolite
            return 1
        return 2
    if db == "Reactome":
        return 1 if names & {"reaction", "inhibition"} else 2
    if db == "OmniPath":
        return 1 if names & {"stimulation", "inhibition"} else 2
    return 2


def statement_tier(stmt, chain):
    """The tier of a statement: mechanism when it cites at least one step leg
    and every step leg it cites is tier 1; association otherwise."""
    by_n = {leg["n"]: leg for leg in chain}
    steps = []
    for leg in stmt.get("legs") or []:
        if isinstance(leg, bool) or not isinstance(leg, int):
            continue
        record = by_n.get(leg)
        if record is not None and record.get("kind") == "step":
            steps.append(record)
    if not steps:
        return ASSOCIATION
    for leg in steps:
        edge = leg.get("edge") or {}
        tier = edge.get("tier")
        if tier is None:
            tier = edge_tier(edge)
        if int(tier) != 1:
            return ASSOCIATION
    return MECHANISM


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------
# Mechanistic verbs: one thing acting on another. Matched as whole words in any
# inflection; "shut(s) down" and "switch(es) on/off" are two-word forms.
_MECH_STEMS = [
    r"driv(?:e|es|en|ing)", r"rewir(?:e|es|ed|ing)", r"shuts? (?:down|off)", r"shutting (?:down|off)",
    r"switch(?:es|ed|ing)? (?:on|off)", r"turn(?:s|ed|ing)? (?:on|off)", r"activat(?:e|es|ed|ing)",
    r"inhibit(?:s|ed|ing)?", r"induc(?:e|es|ed|ing)", r"repress(?:es|ed|ing)?", r"suppress(?:es|ed|ing)?",
    r"trigger(?:s|ed|ing)?", r"block(?:s|ed|ing)?", r"abolish(?:es|ed|ing)?", r"silenc(?:e|es|ed|ing)",
    r"control(?:s|led|ling)?", r"regulat(?:e|es|ed|ing)", r"promot(?:e|es|ed|ing)", r"caus(?:e|es|ed|ing)",
    r"phosphorylat(?:e|es|ed|ing)", r"degrad(?:e|es|ed|ing)", r"derepress(?:es|ed|ing)?",
    r"reprogram(?:s|med|ming)?", r"remodel(?:s|led|ling)?", r"mediat(?:e|es|ed|ing)", r"depend(?:s|ed|ing)? on",
    r"signal(?:s|led|ling)? through", r"up-?regulat(?:e|es|ed|ing)", r"down-?regulat(?:e|es|ed|ing)",
    r"orchestrat(?:e|es|ed|ing)", r"dictat(?:e|es|ed|ing)", r"govern(?:s|ed|ing)?", r"licens(?:e|es|ed|ing)",
    r"enforc(?:e|es|ed|ing)", r"impos(?:e|es|ed|ing)", r"commit(?:s|ted|ting)?",
]
MECHANISTIC_RE = re.compile(r"\b(?:%s)\b" % "|".join(_MECH_STEMS), re.I)
# A passive mechanistic verb is a mechanism only with an agent: "is induced by
# Ikaros". Without "by" it describes a change ("is induced" ~ "goes up").
_PASSIVE_RE = re.compile(r"\b(?:is|are|was|were|be|been|being|gets?|got|became|become)\s+"
                         r"(?:\w+ly\s+)?(?P<verb>\w+(?:ed|en))\b(?P<agent>\s+by\b)?", re.I)
_PASSIVE_STEMS = re.compile(r"^(?:driv|rewir|activat|inhibit|induc|repress|suppress|trigger|block|abolish|"
                            r"silenc|controll|regulat|promot|caus|phosphorylat|degrad|derepress|reprogramm|"
                            r"remodell?|mediat|up-?regulat|down-?regulat|orchestrat|dictat|govern|licens|enforc|"
                            r"impos|committ|shut|switch|turn)", re.I)


def mechanistic_verbs(text):
    """The mechanistic verbs a text uses, in reading order: every active form,
    and a passive form only when an agent follows ("is induced by")."""
    text = str(text or "")
    passive_spans = []
    found = []
    for match in _PASSIVE_RE.finditer(text):
        verb = match.group("verb")
        if _PASSIVE_STEMS.match(verb):
            passive_spans.append((match.start("verb"), match.end("verb")))
            if match.group("agent"):
                found.append((match.start("verb"), verb.lower()))
    for match in MECHANISTIC_RE.finditer(text):
        if any(start <= match.start() < end for start, end in passive_spans):
            continue                                  # judged as a passive above
        found.append((match.start(), match.group(0).lower()))
    found.sort()
    return [verb for _pos, verb in found]


_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\[(])")
_PERTURBATION_SUBJECT_RE = re.compile(
    r"\b(?:perturbation|induction|knock-?out|knock-?down|overexpression|deletion|treatment|stimulation|"
    r"loss|ablation|activation) of\b|\b(?:perturbation|induction|knock-?out|knock-?down|overexpression|"
    r"deletion|treatment|stimulation|loss|ablation)\b", re.I)


def _genes_in(text, names):
    """The chain node ids whose names a text uses as whole words."""
    from src.classes.AIInterpret.walker import verify
    out = set()
    for node_id, name in names:
        if re.search(verify._names_re(name), text, re.I):
            out.add(node_id)
    return out


def _name_catalog(walker):
    from src.classes.AIInterpret.walker import verify
    catalog = []
    for node_id in verify.chain_nodes(walker.record()["chain"]):
        for name in verify._node_names(walker, node_id):
            catalog.append((node_id, name))
    return catalog


def _statement_nodes(stmt, walker):
    """The chain nodes a statement rests on: its cites and the genes its claim names."""
    from src.classes.AIInterpret.walker import verify
    nodes = set()
    for cite in stmt.get("cites") or []:
        if isinstance(cite, (list, tuple)) and cite:
            node_id = verify.resolve_node(cite[0], walker)
            if node_id:
                nodes.add(node_id)
    nodes |= _genes_in(" ".join([str(stmt.get("claim") or ""), str(stmt.get("prose") or "")]),
                       _name_catalog(walker))
    return nodes


def _near_anchor(stmt, walker, anchor):
    """Whether one of the statement's step legs touches the anchor or its neighbour."""
    if not anchor or not anchor.get("node"):
        return False
    node = anchor["node"]
    near = {node} | set(walker.network.neighbours(node))
    by_n = {leg["n"]: leg for leg in walker.record()["chain"]}
    for n in stmt.get("legs") or []:
        leg = by_n.get(n) if isinstance(n, int) and not isinstance(n, bool) else None
        if leg and leg.get("kind") == "step" and (leg["from"] in near or leg["to"] in near):
            return True
    return False


def title_outruns_body(results, kept, walker, anchor=None):
    """Objections to a Results title and summary whose verbs outrun the kept
    statements. An empty list passes.

    Every kept statement carries its tier (statement_tier). A sentence of the
    title or the summary with a mechanistic verb must be backed by a mechanism
    statement: one naming a gene the sentence names, or any one when the
    sentence names no gene. A sentence whose subject is the perturbation needs
    a mechanism statement resting on a leg at the anchor; an unanchored run may
    not put a mechanistic verb on the perturbation at all. A body with no
    mechanism statement allows no mechanistic verb anywhere."""
    if not results:
        return []
    chain = walker.record()["chain"]
    tiers = {int(s["n"]): s.get("tier") or statement_tier(s, chain) for s in kept}
    mechanism = [s for s in kept if tiers[int(s["n"])] == MECHANISM]
    catalog = _name_catalog(walker)
    problems = []
    sentences = [("title", str(results.get("title") or ""))]
    sentences += [("summary", s) for s in _SENTENCE_RE.split(str(results.get("summary") or "")) if s.strip()]
    mech_nodes = {int(s["n"]): _statement_nodes(s, walker) for s in mechanism}
    for where, sentence in sentences:
        verbs = mechanistic_verbs(sentence)
        if not verbs:
            continue
        head = "the %s says %r" % (where, ", ".join(sorted(set(verbs))))
        if not mechanism:
            problems.append("%s, but no kept statement rests on a mechanism edge: use associational "
                            "words (rose, fell, accompanied, was higher)" % head)
            continue
        if _PERTURBATION_SUBJECT_RE.search(sentence):
            if not anchor or not anchor.get("in_graph"):
                problems.append("%s of the perturbation, but the design names no perturbed gene in the "
                                "network: describe what changed after it, not what it did" % head)
                continue
            if not any(_near_anchor(s, walker, anchor) for s in mechanism):
                problems.append("%s of the perturbation, but no mechanism statement rests on a leg at %s: "
                                "describe what changed after it, not what it did" % (head, anchor.get("gene")))
                continue
        named = _genes_in(sentence, catalog)
        if named and not any(named & nodes for nodes in mech_nodes.values()):
            problems.append("%s about %s, but no mechanism statement names them: the statements about "
                            "them rest on associations" % (
                                head, ", ".join(sorted(walker.label(n) for n in named))))
    return problems


def neutral_title(scope_name, card):
    """The title code writes when the Narrator's could not be made to fit the
    body: what changed, after what, with no verb of mechanism."""
    perturbation = str((card or {}).get("perturbation") or "the perturbation").strip()
    first = re.split(r"[,.;:(]", perturbation, maxsplit=1)[0].strip()
    if len(first) > 60:
        first = first[:57].rstrip() + "..."
    return "%s: what changed after %s" % (scope_name, first or "the perturbation")


def drop_mechanistic_sentences(results):
    """Remove every summary sentence that still carries a mechanistic verb;
    return how many went. The title is replaced by the caller."""
    dropped = 0
    kept = []
    for sentence in _SENTENCE_RE.split(str(results.get("summary") or "")):
        if not sentence.strip():
            continue
        if mechanistic_verbs(sentence):
            dropped += 1
            continue
        kept.append(sentence)
    results["summary"] = " ".join(kept)
    return dropped
