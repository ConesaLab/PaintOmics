"""The Writer: an agent loop that turns the sealed chain into 3-5 statements,
each grounded in the drawn edges it rests on, with literature only for what
is built on top of them. submit_statements is the only way to finish; the
Verifier answers every submission and code keeps what passed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool

from src.classes.AIInterpret.agent import _model
from src.classes.AIInterpret.walker import verify
from src.classes.AIInterpret.walker.errors import tool_failure
from src.classes.AIInterpret.walker.walk import sign_glyph

logger = logging.getLogger(__name__)

SEARCH_HITS = 8
MAX_SUBMITS = 3


@dataclass
class WriterContext:
    walker: object
    card: str
    pubmed: object                      # PubMedClient
    papers: dict = field(default_factory=dict)      # ref -> paper dict
    pmid_to_ref: dict = field(default_factory=dict)
    searches: list = field(default_factory=list)
    submits: int = 0
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    done: bool = False
    trace: list = field(default_factory=list)


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
        pmids = await asyncio.to_thread(c.pubmed.search, query, SEARCH_HITS)
        new = [p for p in pmids if str(p) not in c.pmid_to_ref]
        papers = await asyncio.to_thread(c.pubmed.fetch_abstracts, new) if new else []
    except Exception as exc:                                          # noqa: BLE001
        return "Search failed (%s)." % exc
    lines = []
    for paper in papers or []:
        pmid = str(paper.get("pmid", ""))
        if not pmid or pmid in c.pmid_to_ref:
            continue
        ref = len(c.papers) + 1
        c.papers[ref] = dict(paper, ref_index=ref)
        c.pmid_to_ref[pmid] = ref
        lines.append(_paper_line(ref, paper))
    for pmid in pmids:
        ref = c.pmid_to_ref.get(str(pmid))
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
    c.trace.append({"tool": "read_paper", "ref": int(ref_index), "section": section})
    return "[%d] %s\n%s" % (int(ref_index), section, (text or "(section not available)")[:6000])


@function_tool(name_override="check_my_citations", failure_error_function=_fail("check_my_citations"))
async def check_my_citations(ctx: RunContextWrapper[WriterContext], draft: str) -> str:
    """Which [N] in a draft resolve to a retrieved paper. Check before submitting."""
    c = ctx.context
    refs = sorted({int(m) for m in verify.PAPER_RE.findall(str(draft))})
    lines = ["[%d] %s" % (r, "resolves: " + str(c.papers[r].get("title", ""))[:80]
                           if r in c.papers else "UNKNOWN -- never retrieved") for r in refs]
    c.trace.append({"tool": "check_my_citations", "refs": refs})
    return "\n".join(lines) or "no [N] in the draft"


@function_tool(name_override="submit_statements", failure_error_function=_fail("submit_statements"))
async def submit_statements(ctx: RunContextWrapper[WriterContext], statements_json: str) -> str:
    """Submit 3-5 statements as a JSON array of objects {claim, prose, cites: [[node, layer], ...], legs: [N, ...], grounded_in: [{leg, db}], beyond: [{claim, paper: N or null, hypothesis: true/false}], papers: [N, ...]}. The only way to finish. The Verifier answers; fix what it objects to and resubmit, at most three times."""
    c = ctx.context
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
    checked, count_problem = verify.verify_statements(statements, c.walker, c.papers)
    failing = [(s, p) for s, p in checked if p]
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


WRITER_TOOLS = [search_literature, read_paper, check_my_citations, submit_statements]

INSTRUCTIONS = """You are the Writer of an Agentic Graph Walk. You receive the design card, the chain (every leg with its edge, the walker's reading, and the node's layer values as the user labelled them), the nodes seen but not walked, and the walker's notes. The chain is the only evidence you may use.

Write 3 to 5 statements. Each names its evidence as [node, layer] pairs (cites), the legs it rests on, and:
- grounded_in: the drawn edges on the chain it restates, as {leg, db}. A relation the pathway draws is supported by the pathway: cite the leg, never search for it.
- beyond: every claim that goes past what the drawn edges say (a direction against the drawn sign, a mechanism, a causal timing). Each needs a paper found with search_literature and read with read_paper, or hypothesis: true and prose worded as a hypothesis.

A value is evidence only if its layer is flagged relevant; a non-relevant value may be cited only to say it did not change, and the prose must say "not relevant" next to it. Read timing against the card: a difference already present at the baseline is not a response. Where layers disagree, say so; a series that changes sign at every point is noise, not a disagreement.

Finish with submit_statements (a JSON array). Use check_my_citations before it.
"""


def chain_text(walker):
    """The chain as the Writer, the sense check and the Narrator read it."""
    rec = walker.record()
    lines = []
    for leg in rec["chain"]:
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
    if rec["chain"]:
        first = rec["chain"][0]["from"]
        lines.insert(0, "start: %s\n   layers:\n%s" % (walker.label(first), "\n".join(
            "      " + l for l in walker.overlay.layer_text(first).splitlines())))
    return "\n".join(lines)


def seen_text(walker, limit=20):
    rows = walker.record()["seen"]
    rows = sorted(rows, key=lambda r: -r["heat"])[:limit]
    return "\n".join("%s · r=%s · heat %.2f · offered after e%s" % (
        r["label"], "—" if r["r"] is None else r["r"], r["heat"],
        ",e".join(str(x) for x in r["after_legs"][:4])) for r in rows) or "none"


async def run_writer_async(walker, card_text, pubmed, max_turns=30, model=None, temperature=0.3):
    ctx = WriterContext(walker=walker, card=card_text, pubmed=pubmed)
    agent = Agent[WriterContext](name="Writer", model=model or _model(), instructions=INSTRUCTIONS,
                                 model_settings=ModelSettings(temperature=temperature),
                                 tools=WRITER_TOOLS)
    rec = walker.record()
    prompt = "DESIGN CARD\n%s\n\nCHAIN\n%s\n\nSEEN, NOT WALKED (hottest)\n%s\n\nNOTES\n%s\n\nSTOP: %s (%s)" % (
        card_text, chain_text(walker), seen_text(walker),
        "\n".join("after e%d: %s" % (n["after_leg"], n["text"]) for n in rec["notes"]) or "none",
        rec["stop_reason"], rec["stop_reading"])
    try:
        await Runner.run(agent, prompt, context=ctx, max_turns=max_turns)
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[writer] the loop ended early: %s", exc)
    return ctx


def run_writer(walker, card_text, pubmed, max_turns=30, model=None):
    return asyncio.run(run_writer_async(walker, card_text, pubmed, max_turns, model))
