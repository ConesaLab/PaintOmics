"""The papers a walk cites, and the paper agent that confirms each citation.

Every Writer of a walk shares one ``LiteratureStore``: one numbered paper
list, so two Writers that find the same paper cite it under the same number.

A citation is only kept when a paper agent has read the paper and found the
passage that states the claim. The Writer reads an abstract, or one section
of up to 6,000 characters, to choose a paper; the paper agent reads
everything the paper's record holds -- the abstract and, when PubMed Central
or Europe PMC carries the article, its introduction, results and discussion
-- and returns only the passage, copied exactly, with the section it sits in.
Code then finds that passage in the paper's own text. A passage that is not
there, or a paper with no passage for the claim, is an objection the Writer
must answer, and nothing the agent says reaches the reader except the
passage code found.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# A passage shorter than this is a phrase, not evidence; a longer one than
# the maximum is a paragraph the reader cannot check at a glance.
QUOTE_MIN_CHARS = 40
QUOTE_MAX_CHARS = 900
# The most of one paper the agent reads. The PMC parser already caps each
# section (AI_MAX_SECTION_CHARS), so this only bounds a paper with many.
PAPER_TEXT_MAX_CHARS = 120000
# Below this many seconds before the run is due, a citation is not checked:
# the answer would arrive after the Writer has been stopped.
CHECK_MIN_SECONDS = 25
SECTION_ORDER = ("abstract", "introduction", "results", "discussion", "other")

QUOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "section": {"type": "string"},
        "quote": {"type": "string"},
        "note": {"type": "string"},
        "beyond_passage": {"type": "boolean"},
    },
    "required": ["supported", "section", "quote", "note", "beyond_passage"],
    "additionalProperties": False,
}

PAPER_AGENT_BRIEF = (
    "You check one citation in a Results section. You receive a claim and the full text of the paper "
    "cited for it, section by section, each under a label such as [ABSTRACT] or [RESULTS].\n"
    "- Find the passage that states the claim or directly supports it: one to three consecutive "
    "sentences from one section. Copy it character for character, without ellipses or edits.\n"
    "- supported is false when no passage supports the claim. A passage that only names the same genes, "
    "or states something weaker or different, is not support; say what the paper does say in the note.\n"
    "- section is the label the passage sits under, in lower case (abstract, introduction, results, "
    "discussion or other).\n"
    "- note: one line on why the passage supports the claim, or why nothing does.\n"
    "- beyond_passage: when a SENTENCE is given, true if the sentence attributes to the paper anything the passage "
    "does not state -- a stronger mechanism, another cell type or organism, a direction, a generalisation. The "
    "sentence may also report the user's own measurements (numbers with signs, time points); those are not the "
    "paper's and do not count. False when no sentence is given."
)

# NFKC already turns no-break and thin spaces into spaces; quotes and dashes
# it leaves alone.
_PUNCTUATION = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2010": "-",
                "\u2011": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"}
_ELLIPSIS_RE = re.compile("\\s*(?:\\.\\.\\.|\u2026)\\s*")


def normalise(text):
    """Text as compared, not as shown: one form for Unicode, quotes, dashes and
    spaces, lower case, and runs of whitespace as one space."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    for old, new in _PUNCTUATION.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip().lower()


def paper_sections(paper):
    """The paper's text by section, in reading order, empty sections left out."""
    sections = dict(paper.get("sections") or {})
    if not sections.get("abstract") and paper.get("abstract"):
        sections["abstract"] = paper["abstract"]
    ordered = [(name, sections[name]) for name in SECTION_ORDER if sections.get(name)]
    ordered += [(name, text) for name, text in sections.items() if name not in SECTION_ORDER and text]
    return ordered


_SENTENCE_SPLIT_RE = re.compile("(?<=[.!?])\\s+(?=[A-Z0-9\"'(\\[])")


def find_passage(quote, sections, preferred=None):
    """(section, passage) for the section whose text holds ``quote``, or None.
    The quote is compared after normalise(); a quote the agent shortened with
    an ellipsis is found only when every piece of at least 15 characters sits,
    in order, in one section. The passage is the whole sentences the quote
    falls in, as the paper words them, so the reader never sees a fragment.
    The preferred section is tried first."""
    pieces = [normalise(p) for p in _ELLIPSIS_RE.split(str(quote or ""))]
    pieces = [p.strip(" \"'") for p in pieces if len(p.strip(" \"'")) >= 15]
    if not pieces or sum(len(p) for p in pieces) < QUOTE_MIN_CHARS:
        return None
    order = sorted(sections, key=lambda item: item[0] != preferred)
    for name, text in order:
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(str(text)) if s.strip()]
        norm = [normalise(s) for s in sentences]
        starts, at = [], 0
        for sentence in norm:
            starts.append(at)
            at += len(sentence) + 1
        body, start, first = " ".join(norm), 0, None
        for piece in pieces:
            found = body.find(piece, start)
            if found < 0:
                break
            first = found if first is None else first
            start = found + len(piece)
        else:
            i = max(k for k, s in enumerate(starts) if s <= first)
            j = max(k for k, s in enumerate(starts) if s < start)
            passage = re.sub(r"\s+", " ", " ".join(sentences[i:j + 1])).strip()
            return name, (passage if len(passage) <= QUOTE_MAX_CHARS * 1.5 else str(quote).strip())
    return None



def paper_text(sections):
    """The paper as the agent reads it: labelled sections, bounded in total."""
    out, used = [], 0
    for name, text in sections:
        text = str(text)[:max(0, PAPER_TEXT_MAX_CHARS - used)]
        if not text:
            break
        out.append("[%s]\n%s" % (name.upper(), text))
        used += len(text)
    return "\n\n".join(out)


def _claim_key(ref, claim, sentence=None):
    return int(ref), normalise(claim)[:400], normalise(sentence or "")[:400]


def _embedded_json(text):
    text = str(text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


@dataclass
class LiteratureStore:
    """One walk's papers: retrieval numbers, the fetched full texts and every
    citation verdict. Shared by the Writers, which all run on one event loop,
    so nothing here needs a thread lock."""
    papers: dict = field(default_factory=dict)          # ref -> paper as PubMed returned it
    pmid_to_ref: dict = field(default_factory=dict)
    full: dict = field(default_factory=dict)            # pmid -> [(section, text)]
    verdicts: dict = field(default_factory=dict)        # (ref, claim) -> verdict
    fetching: dict = field(default_factory=dict)        # pmid -> asyncio.Lock
    checks: int = 0
    trace: list = field(default_factory=list)

    def add(self, paper):
        """Number a retrieved paper, or return the number it already has."""
        pmid = str(paper.get("pmid", ""))
        if pmid in self.pmid_to_ref:
            return self.pmid_to_ref[pmid], False
        ref = len(self.papers) + 1
        self.papers[ref] = dict(paper, ref_index=ref)
        self.pmid_to_ref[pmid] = ref
        return ref, True

    async def full_text(self, pubmed, ref):
        """The paper's sections, fetched once per PMID even when several claims
        ask at the same time; the abstract alone when full text is unavailable."""
        paper = self.papers[ref]
        pmid = str(paper.get("pmid", ""))
        if pmid in self.full:
            return self.full[pmid]
        lock = self.fetching.setdefault(pmid, asyncio.Lock())
        async with lock:
            if pmid not in self.full:
                sections = paper_sections(paper)
                try:
                    fetched = await asyncio.to_thread(pubmed.fetch_papers, [pmid])
                    if fetched:
                        sections = paper_sections(dict(fetched[0], abstract=fetched[0].get("abstract")
                                                       or paper.get("abstract")))
                except Exception as exc:                              # noqa: BLE001
                    logger.warning("[literature] full text for PMID %s failed: %s", pmid, exc)
                self.full[pmid] = sections
        return self.full[pmid]

    def verdict(self, ref, claim, sentence=None):
        return self.verdicts.get(_claim_key(ref, claim, sentence))

    def evidence(self):
        """Every confirmed passage, by paper number."""
        out = {}
        for (ref, _claim), verdict in self.verdicts.items():
            if verdict.get("supported"):
                out.setdefault(ref, []).append(verdict)
        return out


async def check_citation(store, client, pubmed, ref, claim, slots, deadline=None, sentence=None):
    """Confirm that paper ``ref`` states ``claim``, and that ``sentence`` (the
    prose the reader sees citing it, when given) attributes nothing to the
    paper beyond that passage. Returns the verdict: {supported, quote,
    section, full_text, claim} when a passage was found in the paper, else
    {supported: False, why}. Verdicts are cached per (paper, claim,
    sentence); a check that could not run (the gateway, the deadline) is not,
    so a resubmission asks again."""
    cached = store.verdict(ref, claim, sentence)
    if cached is not None:
        return cached
    if int(ref) not in store.papers:
        return {"supported": False, "why": "paper [%s] was never retrieved" % ref}
    if deadline is not None and deadline - time.time() < CHECK_MIN_SECONDS:
        return {"supported": False, "why": "there was no time left to read the paper", "transient": True}
    sections = await store.full_text(pubmed, int(ref))
    if not sections:
        verdict = {"supported": False, "why": "the paper has no text to check"}
        store.verdicts[_claim_key(ref, claim, sentence)] = verdict
        return verdict
    paper = store.papers[int(ref)]
    prompt = "CLAIM\n%s\n%s\nPAPER\n%s\n%s" % (
        claim, ("\nSENTENCE (as the reader sees it)\n%s\n" % sentence) if sentence else "",
        paper.get("title", ""), paper_text(sections))
    started = time.time()
    try:
        async with slots:
            out = await asyncio.to_thread(
                client.complete_json,
                [{"role": "system", "content": PAPER_AGENT_BRIEF}, {"role": "user", "content": prompt}],
                "citation_check", QUOTE_SCHEMA, _embedded_json, 900, 0.0)
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[literature] paper agent for [%s] failed: %s", ref, exc)
        return {"supported": False, "why": "the paper could not be checked (%s)" % type(exc).__name__,
                "transient": True}
    store.checks += 1
    out = out if isinstance(out, dict) else {}
    quote = re.sub(r"\s+", " ", str(out.get("quote") or "")).strip().strip("\"'\u201c\u201d")
    found = find_passage(quote, sections, str(out.get("section") or "").lower().strip("[] ")) \
        if out.get("supported") and QUOTE_MIN_CHARS <= len(quote) <= QUOTE_MAX_CHARS else None
    if found and sentence and out.get("beyond_passage") is True:
        verdict = {"supported": False, "why": "the sentence says more than the passage: %s"
                   % str(out.get("note") or "it attributes to the paper what the paper does not state")[:200],
                   "beyond_passage": True}
    elif found:
        section, passage = found
        verdict = {"supported": True, "quote": passage, "section": section, "claim": str(claim),
                   "full_text": any(name != "abstract" for name, _ in sections)}
    elif out.get("supported"):
        verdict = {"supported": False, "why": (
            "the passage returned is too short to show the claim" if len(quote) < QUOTE_MIN_CHARS else
            "the passage returned is longer than a citation can quote" if len(quote) > QUOTE_MAX_CHARS else
            "the passage the paper agent returned is not in the paper")}
    else:
        verdict = {"supported": False, "why": str(out.get("note") or "no passage in the paper states it")[:300]}
    store.verdicts[_claim_key(ref, claim, sentence)] = verdict
    store.trace.append({"ref": int(ref), "supported": verdict["supported"], "section": verdict.get("section"),
                        "beyond_passage": bool(verdict.get("beyond_passage")),
                        "full_text": any(name != "abstract" for name, _ in sections),
                        "seconds": round(time.time() - started, 1)})
    return verdict


# ---------------------------------------------------------------------------
# Paper context: what a paper studied, read off its MeSH indexing
# ---------------------------------------------------------------------------
# A paper on human hepatoma cells is not the same evidence for a mouse pre-B
# cell experiment as a paper on mouse pre-B cells. PubMed's indexers already
# recorded organism, material and study type as MeSH headings and publication
# types, so the Writer can be told -- without a model call -- which of its
# papers were done in the same organism and system as the design card, and
# rank them accordingly. The record's ``mesh`` and ``pub_types`` lists come
# from pubmed_client._parse_xml, in document order.

ORGANISM_HEADINGS = {"Mice": "mouse", "Humans": "human", "Rats": "rat", "Zebrafish": "zebrafish",
                     "Drosophila melanogaster": "fly", "Caenorhabditis elegans": "worm",
                     "Saccharomyces cerevisiae": "yeast", "Arabidopsis": "arabidopsis", "Danio rerio": "zebrafish"}
IN_VITRO_HEADINGS = ("Cell Line", "Cells, Cultured", "Cell Line, Tumor", "Cell Culture Techniques", "HEK293 Cells",
                     "HeLa Cells", "Jurkat Cells")
CLINICAL_HEADINGS = ("Cohort Studies", "Case-Control Studies", "Prospective Studies", "Retrospective Studies",
                     "Clinical Trials as Topic", "Biomarkers, Tumor", "Prognosis", "Treatment Outcome", "Patients")
# Headings that name an organism, a demographic or a generic material, not the
# cell type or tissue the paper is about.
SYSTEM_SKIP = {"Animals", "Humans", "Mice", "Rats", "Male", "Female", "Adult", "Aged", "Middle Aged", "Child",
               "Mice, Inbred C57BL", "Mice, Knockout", "Mice, Transgenic", "Cell Line", "Cells, Cultured",
               "Cell Line, Tumor"}
TISSUE_HEADINGS = frozenset({"Liver", "Brain", "Bone Marrow", "Spleen", "Thymus", "Kidney", "Heart", "Lung", "Skin",
                             "Pancreas", "Muscle, Skeletal", "Adipose Tissue", "Blood", "Intestines", "Colon",
                             "Breast", "Prostate"})
_CELL_HEADING_RE = re.compile(r"^(B|T|NK|Dendritic|Mast|Precursor|Stem|Hematopoietic|Germinal Center|Plasma) ")
# MeSH names most cell types by a Greek suffix rather than the word "Cells":
# Hepatocytes, Monocytes, Fibroblasts, Osteoblasts; Macrophages and their
# subheadings ("Macrophages, Alveolar") lead with the word.
_CELL_TYPE_RE = re.compile(r"(cytes|blasts)$|^Macrophages\b")
_SYSTEM_SUFFIXES = ("Cells", "Lymphocytes", "Cell Line")
# Words of a system heading that say nothing about which system it is.
_SYSTEM_STOPWORDS = {"cells", "cell", "line", "lymphocytes", "precursor"}
# MeSH heading (lower-cased) -> phrases a design card uses for the same system.
SYNONYMS = {"b-lymphocytes": ["b cell", "b-cell", "b cells", "pre-b", "pro-b"],
            "precursor cells, b-lymphoid": ["pre-b", "pro-b", "b cell precursor"],
            "t-lymphocytes": ["t cell", "t-cell"],
            "hepatocytes": ["liver", "hepat"],
            "macrophages": ["macrophage"],
            "fibroblasts": ["fibroblast"]}
UNKNOWN = "unknown"


def _heading_list(paper, key):
    """The paper's ``key`` list as clean strings; [] for a record without it."""
    values = paper.get(key) if isinstance(paper, dict) else None
    if isinstance(values, str):
        values = [values]
    return [str(v).strip() for v in (values or []) if str(v or "").strip()]


def _organism_of(heading):
    """The species word a MeSH heading names, or None for a heading that is
    not an organism. Strain headings ("Mice, Inbred C57BL") count as the
    species they qualify."""
    if heading in ORGANISM_HEADINGS:
        return ORGANISM_HEADINGS[heading]
    if heading.startswith("Mice,"):
        return "mouse"
    if heading.startswith("Rats,"):
        return "rat"
    return None


def _names_system(heading):
    """Whether a MeSH heading names a cell type, tissue or organ."""
    if heading in SYSTEM_SKIP:
        return False
    return (heading.endswith(_SYSTEM_SUFFIXES) or heading in TISSUE_HEADINGS
            or _CELL_HEADING_RE.match(heading) is not None
            or _CELL_TYPE_RE.search(heading) is not None)


def paper_context(paper) -> dict:
    """{"organism", "system", "scope"} read from the paper's MeSH headings and publication types; "unknown" where MeSH says nothing. No model call."""
    mesh = _heading_list(paper, "mesh")
    pub_types = _heading_list(paper, "pub_types")
    # Organism: the first organism heading in document order, so a paper
    # indexed "Humans" then "Mice" (human genes, mouse model) reads as human.
    organism = next((org for org in map(_organism_of, mesh) if org), UNKNOWN)
    system = next((h for h in mesh if _names_system(h)), UNKNOWN)
    if any("review" in pt.lower() for pt in pub_types):
        scope = "review"
    elif any(h in IN_VITRO_HEADINGS for h in mesh):
        scope = "in vitro"
    elif organism == "human" and any(h in CLINICAL_HEADINGS for h in mesh):
        scope = "clinical"
    elif organism not in (UNKNOWN, "human"):
        scope = "in vivo"
    else:
        scope = UNKNOWN
    return {"organism": organism, "system": system, "scope": scope}


def _known(value):
    """The value as a lower-cased string, or "" when it says nothing."""
    text = str(value or "").strip().lower()
    return "" if text == UNKNOWN else text


def _words(text):
    """The whole words of a heading or a card's text: lower case, hyphens as
    spaces, punctuation dropped."""
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower().replace("-", " ")))


def context_match(card, ctx) -> dict:
    """{"organism": "same"|"other"|"unknown", "system": "same"|"other"|"unknown"} of a paper's context against the design card. card["organism"] is a species word ("mouse"); card["system"] is free text ("mouse B3 pre-B cell line")."""
    card = card if isinstance(card, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    card_organism, paper_organism = _known(card.get("organism")), _known(ctx.get("organism"))
    if not card_organism or not paper_organism:
        organism = UNKNOWN
    else:
        organism = "same" if card_organism == paper_organism else "other"
    card_system, paper_system = _known(card.get("system")), _known(ctx.get("system"))
    if not card_system or not paper_system:
        system = UNKNOWN
    else:
        # A whole word of the heading in the card's text ("marrow" in "bone
        # marrow macrophages"), or a phrase the card would use for it.
        card_words = _words(card_system)
        words = _words(paper_system) - _SYSTEM_STOPWORDS
        card_text = card_system.replace("-", " ")
        matched = bool(words & card_words) or any(
            phrase in card_system or phrase.replace("-", " ") in card_text
            for phrase in SYNONYMS.get(paper_system, ()))
        system = "same" if matched else "other"
    return {"organism": organism, "system": system}


def context_line(ctx) -> str:
    """One line for the page: "mouse · B-Lymphocytes · in vitro", the unknown
    parts left out; "" when nothing is known."""
    ctx = ctx if isinstance(ctx, dict) else {}
    parts = [str(ctx.get(key) or "").strip() for key in ("organism", "system", "scope")]
    return " · ".join(p for p in parts if p and p.lower() != UNKNOWN)


def rank_by_context(papers, card) -> list:
    """The papers, stably sorted so those from the card's organism come first,
    then those from its system, then the rest. Each paper gains
    paper["context"] = {organism, system, scope, match} on the way."""
    ranked = []
    for paper in papers or []:
        if not isinstance(paper, dict):
            continue
        ctx = paper_context(paper)
        paper["context"] = dict(ctx, match=context_match(card, ctx))
        ranked.append(paper)
    return sorted(ranked, key=lambda p: (p["context"]["match"]["organism"] != "same",
                                         p["context"]["match"]["system"] != "same"))
