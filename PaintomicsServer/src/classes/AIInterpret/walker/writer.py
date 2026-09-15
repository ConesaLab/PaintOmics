"""The Writers: agent loops that turn one part of the sealed chain each into
statements, grounded in the drawn edges they rest on, with published biology
for what is built on top of them. Several run at once over one walk
(walker/parallel.py), sharing one numbered paper list.

submit_statements is the only way to finish. The Verifier answers every
submission: code checks the evidence, the legs and the wording, and a paper
agent reads every cited paper in full and must find the passage that states
the claim (walker/literature.py). What fails goes back to the Writer.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool

from src.classes.AIInterpret.agent import _model
from src.classes.AIInterpret.walker import literature
from src.classes.AIInterpret.walker import regulators
from src.classes.AIInterpret.walker import verify
from src.classes.AIInterpret.walker.errors import tool_failure
from src.classes.AIInterpret.walker.walk import sign_glyph

logger = logging.getLogger(__name__)

SEARCH_HITS = 8
MAX_SUBMITS = 3
READ_CHARS = 6000


@dataclass
class WriterContext:
    walker: object
    card: str
    pubmed: object                      # PubMedClient
    store: object = None                # LiteratureStore shared by every Writer of the walk
    client: object = None               # the paper agent's LLM client; None leaves citations unchecked
    legs: tuple | None = None           # (first, last) leg this Writer covers; None = the whole chain
    count: tuple = (verify.STATEMENT_MIN, verify.STATEMENT_MAX)
    citations: int = 2                  # papers this Writer should cite across its statements
    paper_slots: object = None          # asyncio.Semaphore bounding the paper agents of the walk
    card_ctx: dict | None = None        # {"organism", "system"} of the design, for the context match
    deadline: float | None = None
    searches: list = field(default_factory=list)
    submits: int = 0
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    last_passing: list = field(default_factory=list)   # what passed on the latest submission
    done: bool = False
    trace: list = field(default_factory=list)
    read: set = field(default_factory=set)          # refs opened with read_paper
    loop_error: str | None = None                   # the model loop ended on this exception

    def __post_init__(self):
        if self.store is None:
            self.store = literature.LiteratureStore()

    @property
    def papers(self):
        return self.store.papers


def _fail(name):
    return tool_failure("writer", name)


def _paper_line(ref, paper):
    return "[%d] %s (%s, %s) PMID %s" % (ref, str(paper.get("title", ""))[:120],
                                          paper.get("year", "?"), str(paper.get("journal", ""))[:40],
                                          paper.get("pmid", "?"))


def _abstract(paper):
    sections = paper.get("sections") or {}
    return sections.get("abstract") or paper.get("abstract") or ""


@function_tool(name_override="search_literature", failure_error_function=_fail("search_literature"))
async def search_literature(ctx: RunContextWrapper[WriterContext], query: str, topic_tag: str) -> str:
    """Search PubMed for ONE claim that goes beyond what the drawn edges say. Never search a relation the pathway already draws. Returns papers as [N] you may cite. Keep queries broad: two or three gene symbols joined by OR, AND one biological term."""
    c = ctx.context
    c.searches.append({"query": query, "topic_tag": topic_tag})
    try:
        pmids = await asyncio.to_thread(c.pubmed.search, query, SEARCH_HITS, "relevance")
        new = [p for p in pmids if str(p) not in c.store.pmid_to_ref]
        papers = await asyncio.to_thread(c.pubmed.fetch_abstracts, new) if new else []
    except Exception as exc:                                          # noqa: BLE001
        return "Search failed (%s)." % exc
    lines = []
    # In-context papers first, each hit tagged with its organism, system and
    # scope as its MeSH headings give them (check 4).
    for paper in literature.rank_by_context([p for p in papers or [] if str(p.get("pmid", ""))], c.card_ctx or {}):
        ref, added = c.store.add(paper)
        if added:
            tag = literature.context_line(paper.get("context") or {})
            lines.append(_paper_line(ref, paper) + (" [%s]" % tag if tag else ""))
    for pmid in pmids:
        ref = c.store.pmid_to_ref.get(str(pmid))
        if ref and not any(l.startswith("[%d]" % ref) for l in lines):
            lines.append(_paper_line(ref, c.papers[ref]) + " -- already retrieved")
    total = len(pmids)
    c.trace.append({"tool": "search_literature", "query": query, "hits": total, "new": len(lines)})
    if not lines:
        return "no hits for %r (PubMed matched %d). Broaden the query or word the claim as a hypothesis." % (query, total)
    return "PubMed matched %d; listed:\n%s" % (total, "\n".join(lines))


@function_tool(name_override="read_paper", failure_error_function=_fail("read_paper"))
async def read_paper(ctx: RunContextWrapper[WriterContext], ref_index: int, section: str) -> str:
    """Read one section (abstract, introduction, results, discussion) of a retrieved paper [N]. The abstract is instant; other sections fetch full text."""
    c = ctx.context
    paper = c.papers.get(int(ref_index))
    if paper is None:
        return "No paper [%s]; cite only indices search_literature returned." % ref_index
    section = str(section or "abstract").lower()
    if section != "abstract" and not (paper.get("sections") or {}).get(section):
        try:
            fetched = await asyncio.to_thread(c.pubmed.fetch_papers, [paper["pmid"]])
            if fetched:
                fresh = dict(fetched[0], ref_index=int(ref_index))
                c.papers[int(ref_index)] = paper = fresh
        except Exception as exc:                                      # noqa: BLE001
            return "Full text unavailable (%s); the abstract:\n%s" % (exc, _abstract(paper))
    text = (paper.get("sections") or {}).get(section) if section != "abstract" else _abstract(paper)
    c.read.add(int(ref_index))
    c.trace.append({"tool": "read_paper", "ref": int(ref_index), "section": section})
    return "[%d] %s\n%s" % (int(ref_index), section, (text or "(section not available)")[:READ_CHARS])


@function_tool(name_override="check_my_citations", failure_error_function=_fail("check_my_citations"))
async def check_my_citations(ctx: RunContextWrapper[WriterContext], draft: str) -> str:
    """Which [N] in a draft resolve to a retrieved paper. Check before submitting."""
    c = ctx.context
    refs = sorted({int(m) for m in verify.PAPER_RE.findall(str(draft))})
    lines = ["[%d] %s" % (r, "resolves: " + str(c.papers[r].get("title", ""))[:80]
                           if r in c.papers else "UNKNOWN -- never retrieved") for r in refs]
    c.trace.append({"tool": "check_my_citations", "refs": refs})
    return "\n".join(lines) or "no [N] in the draft"


async def confirm_citations(c, checked):
    """Send every citation of the statements code passed to the paper agents,
    at once, and turn each unconfirmed one into an objection. A confirmed one
    becomes the statement's evidence: {ref, claim, quote, section}."""
    if c.client is None:
        return
    if c.paper_slots is None:
        # Made here, inside the running loop: on Python 3.9 a semaphore binds
        # to the loop current when it is created.
        c.paper_slots = asyncio.Semaphore(4)
    jobs = []
    for stmt, problems in checked:
        if problems:
            continue
        stmt["evidence"] = []
        for ref, claim in verify.citation_pairs(stmt):
            jobs.append((stmt, problems, ref, claim))
    verdicts = await asyncio.gather(*(literature.check_citation(
        c.store, c.client, c.pubmed, ref, claim, c.paper_slots, c.deadline,
        sentence=" ".join(verify.citing_sentences(stmt, ref))) for stmt, _p, ref, claim in jobs))
    for (stmt, problems, ref, claim), verdict in zip(jobs, verdicts):
        if verdict.get("supported"):
            stmt["evidence"].append({"ref": ref, "claim": claim, "quote": verdict["quote"],
                                     "section": verdict["section"], "full_text": verdict.get("full_text")})
        else:
            problems.append("[%d] does not support %r: %s. Cite a paper whose text states it, word the "
                            "claim as a hypothesis, or say no more than the paper does"
                            % (ref, str(claim)[:80], verdict.get("why")))


async def submit(c, statements_json):
    """The Verifier's answer to one submission (the body of submit_statements)."""
    try:
        statements = json.loads(statements_json)
        assert isinstance(statements, list)
    except (ValueError, AssertionError):
        return "submit_statements needs a JSON array of statement objects."
    c.submits += 1
    for i, stmt in enumerate(statements, 1):
        if isinstance(stmt, dict):
            stmt["n"] = i
    statements = [s for s in statements if isinstance(s, dict)]
    checked, count_problem = verify.verify_statements(statements, c.walker, c.papers, c.read,
                                                      count=c.count, legs=c.legs, names_genes=c.client is None)
    await confirm_citations(c, checked)
    failing = [(s, p) for s, p in checked if p]
    c.last_passing = [copy.deepcopy(dict(s, verifier="pass")) for s, p in checked if not p]
    c.trace.append({"tool": "submit_statements", "attempt": c.submits, "n": len(statements),
                    "failing": len(failing), "count_problem": count_problem})
    if (failing or count_problem) and c.submits < MAX_SUBMITS:
        lines = ["The Verifier objects; fix and resubmit ALL statements (attempt %d of %d):" % (
            c.submits, MAX_SUBMITS)]
        if count_problem:
            lines.append("- " + count_problem)
        for stmt, problems in failing:
            lines.append("- statement %d (%s): %s" % (stmt["n"], str(stmt.get("claim", ""))[:60],
                                                        "; ".join(problems)))
        return "\n".join(lines)
    c.kept = [dict(s, verifier="pass") for s, p in checked if not p]
    c.dropped = [{"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"), "why": "; ".join(p),
                  "by": "verifier"} for s, p in checked if p]
    c.done = True
    return "accepted: %d kept, %d dropped by the Verifier. Done." % (len(c.kept), len(c.dropped))


@function_tool(name_override="submit_statements", failure_error_function=_fail("submit_statements"))
async def submit_statements(ctx: RunContextWrapper[WriterContext], statements_json: str) -> str:
    """Submit your statements as a JSON array of objects {claim, prose, cites: [[node, layer], ...], legs: [N, ...], grounded_in: [{leg, db}], beyond: [{claim, paper: N or null, hypothesis: true/false}], papers: [N, ...]}. The only way to finish. The Verifier answers, and a paper agent reads every cited paper for the passage that states your claim; fix what they object to and resubmit, at most three times."""
    return await submit(ctx.context, statements_json)


WRITER_TOOLS = [search_literature, read_paper, check_my_citations, submit_statements]

INSTRUCTIONS = """You are a Writer of an Agentic Graph Walk. You receive the design card and one part of the walk: its legs with their edges, the walker's readings and each node's layer values as the user labelled them, plus the notes. That part of the chain is the only data evidence you may use; other Writers cover the other parts.

Write the number of statements the brief asks for, about your legs only. Each names its evidence as [node, layer] pairs (cites), the legs it rests on, and:
- grounded_in: the drawn edges on the chain it restates, as {leg, db}. A relation the pathway draws is supported by the pathway: cite the leg, never search for it.
- beyond: what published biology adds to the values -- the established role of these genes, a known regulation or mechanism that explains the direction, what the change means for the cell. Each claim needs a paper found with search_literature and read with read_paper, or hypothesis: true and prose worded as a hypothesis. Searches return PubMed's best matches: prefer the study that established the claim over a paper that mentions it in passing.

Citations are checked. A paper agent reads every paper you cite, in full, and must find the passage that states your claim; a claim the paper does not state is sent back. So claim exactly what the paper says, and cite the paper where you read it. Aim for the number of papers the brief asks for across your statements, each on a different claim.

Context matters. Every search hit is tagged with the organism, cell type and scope its indexing gives it, in-context papers first. Prefer a paper from the design's organism and system; when you cite one from another (a human cohort for a mouse cell line, a liver study for B cells), say so in the sentence that cites it ("in human T cells [3]"); code refuses the citation otherwise.

Write like a paper. Name the genes and proteins ("Itpr1, Itpr2 and Itpr3", "the IP3 receptors"); never call a set of genes a cluster, and never use the walker's words seed, jump or the walk. A value is evidence only if its layer is flagged relevant; a non-relevant value may be cited only to say it did not change, and the prose must say "not relevant" next to it. Read timing against the card: a difference already present at the baseline is not a response. An omic the card lists as unlabeled has no time points or order: call its columns c1..cN and never give its values early, late, baseline, peak or over-time words; code refuses them. Where layers disagree, say so; a series that changes sign at every point is noise, not a disagreement.

Finish with submit_statements (a JSON array). Use check_my_citations before it.
"""


def chain_text(walker, legs=None):
    """The chain as the Writers, the sense check and the Narrator read it;
    ``legs`` = (first, last) keeps one part of it, numbered as in the whole."""
    rec = walker.record()
    chosen = [leg for leg in rec["chain"] if legs is None or legs[0] <= leg["n"] <= legs[1]]
    lines = []
    for leg in chosen:
        if leg["kind"] == "step":
            e = leg["edge"]
            lines.append("e%d · %s → %s · %s · %s · %s %s · %s the arrow" % (
                leg["n"], walker.label(leg["from"]), walker.label(leg["to"]), e["db"], e["name"],
                e["subtype"] or "edge", sign_glyph(e["sign"]), e["dir"]))
        else:
            lines.append("J%d · jump → %s" % (leg["n"], walker.label(leg["to"])))
        lines.append("   reading: %s" % leg["reading"])
        lines.append("   reason: %s" % leg["reason"])
        lines.append("   layers of %s:\n%s" % (walker.label(leg["to"]),
                                                "\n".join("      " + l for l in walker.overlay.layer_text(leg["to"]).splitlines())))
    if chosen:
        first = chosen[0]["from"]
        lines.insert(0, "start: %s\n   layers:\n%s" % (walker.label(first), "\n".join(
            "      " + l for l in walker.overlay.layer_text(first).splitlines())))
    return "\n".join(lines)


def seen_text(walker, limit=20, legs=None):
    rows = [r for r in walker.record()["seen"]
            if legs is None or any(legs[0] - 1 <= a <= legs[1] for a in r["after_legs"])]
    rows = sorted(rows, key=lambda r: -r["heat"])[:limit]
    return "\n".join("%s · r=%s · heat %.2f · offered after e%s" % (
        r["label"], "—" if r["r"] is None else r["r"], r["heat"],
        ",e".join(str(x) for x in r["after_legs"][:4])) for r in rows) or "none"


def writer_prompt(c, others=""):
    """The brief one Writer starts from: its legs, the notes on them, what the
    other Writers cover, and how many statements and papers to write."""
    walker, legs = c.walker, c.legs
    rec = walker.record()
    notes = [n for n in rec["notes"] if legs is None or legs[0] - 1 <= n["after_leg"] <= legs[1]]
    part = "legs e%d to e%d" % legs if legs else "the whole chain"
    return ("BRIEF\nWrite %d to %d statements about %s, citing about %d papers across them.\n\n"
            "DESIGN CARD\n%s\n\nYOUR PART OF THE WALK (%s)\n%s\n\nSEEN FROM THESE LEGS, NOT WALKED (hottest)\n%s\n\n"
            "NOTES\n%s\n\nOTHER PARTS, WRITTEN BY OTHER WRITERS\n%s\n\nSTOP: %s" % (
                c.count[0], c.count[1], part, c.citations, c.card, part, chain_text(walker, legs),
                seen_text(walker, legs=legs),
                "\n".join("after e%d: %s" % (n["after_leg"], n["text"]) for n in notes) or "none",
                others or "none", rec["stop_reason"]))


async def run_writer_async(c, others="", max_turns=30, model=None, temperature=0.3):
    """Run one Writer to its accepted submission, its turn limit or an error;
    returns the context, whose kept/last_passing say what survived."""
    agent = Agent[WriterContext](name="Writer", model=model or _model(),
                                 instructions=INSTRUCTIONS + "\n" + regulators.reading_rule(),
                                 model_settings=ModelSettings(temperature=temperature),
                                 tools=WRITER_TOOLS)
    try:
        await Runner.run(agent, writer_prompt(c, others), context=c, max_turns=max_turns)
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[writer] the loop ended early: %s", exc)
        c.loop_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return c
