"""The Narrator: one model call that writes a Results section from the kept
statements only, every sentence tagged with its statement and legs; code
checks every number and leg (verify.verify_results). One repair, then the
story is dropped and the statements stand alone.
"""
from __future__ import annotations

import json
import time

import requests

RESULTS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_statement": {"type": ["integer", "null"]},
                    "legs": {"type": "array", "items": {"type": "integer"}},
                    "text": {"type": "string"},
                },
                "required": ["from_statement", "legs", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "paragraphs"],
    "additionalProperties": False,
}

BRIEF = (
    "You write the Results section of a paper from a graph walk over a pathway or a whole interaction "
    "network. Facts come only from "
    "the kept statements; the prose is yours. Write it as a paper reports results, not as a tour of the "
    "walk: name the genes and proteins (\"Itpr1, Itpr2 and Itpr3\", \"the IP3 receptors\"), never call a "
    "set of genes a cluster, and never use the walker's words seed, jump or the walk; code removes a "
    "sentence that does. Rules:\n"
    "- Exactly %d paragraphs carry facts, one per kept statement, in walk order, each tagged "
    "from_statement = that statement's n, with the legs it rests on. Explain how these genes connect "
    "under the experiment design.\n"
    "- Between them you may add at most %d connective paragraphs of one sentence each, tagged from_statement "
    "null: they may only say what a drawn edge or the design card says, and contain no value and no paper.\n"
    "- Quote values exactly as they appear in the chain, with sign and two decimals, and cite legs as "
    "[eN], layers as [node · layer], papers as [N] only where the statement did. Every paper a statement "
    "cites appears as [N] in its paragraph, next to the claim it supports; PAPERS lists what a reader of "
    "each paper confirmed it states, so give the literature its full weight there.\n"
    "- Never mention a dropped statement. Past tense, no hedging words the statements do not use.\n"
    "- An omic the design card lists as unlabeled has no time points: describe its values by column (c1..cN) "
    "and give them no early, late, baseline, peak or over-time word, even in a sentence about another gene.\n"
    "- Length %d to %d words."
)


def prune_connectives(results):
    """Drop the connective paragraphs that broke their rule (a value or a paper
    in a paragraph that belongs to no statement). They carry no facts by
    definition, so removing one loses nothing the checks allow, and one stray
    number in a bridge sentence no longer sinks a whole Results section.
    Returns how many were dropped."""
    from src.classes.AIInterpret.walker import verify
    kept, dropped = [], 0
    for paragraph in results.get("paragraphs") or []:
        if not isinstance(paragraph, dict):
            kept.append(paragraph)                # verify_results objects to it
            continue
        text = str(paragraph.get("text") or "")
        if paragraph.get("from_statement") is None and (
                verify.quoted_values(text) or verify.cited_refs(text)):
            dropped += 1
            continue
        kept.append(paragraph)
    results["paragraphs"] = kept
    return dropped


def _embedded_json(text):
    """The outermost JSON object in a reply that wrapped it in prose or a fence."""
    text = str(text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


class OutOfTime(Exception):
    """The Narrator's time ran out before it had a readable answer."""


def _ran_out(exc):
    """Under a deadline the client gives up with a timeout, or with the 429
    whose wait would not fit in the time left: the budget, not a bad answer."""
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.exceptions.HTTPError) and getattr(response, "status_code", None) == 429


def narrate(client, card_text, kept, chain_text, papers_text, words=(150, 450),
            objections=None, temperature=0.3, deadline=None):
    """The Results dict, or None when the call fails. Raises OutOfTime when
    `deadline` passed, or the call gave up on it, before a readable answer.

    The token budget follows the word budget: a network Results section of up to
    900 words is about 1,300 tokens of prose plus the JSON around it, and the
    2,500-token budget the pathway section needs cut one off mid-object, which
    left the job with no Results at all."""
    prompt = "DESIGN CARD\n%s\n\nCHAIN\n%s\n\nKEPT STATEMENTS\n%s\n\nPAPERS\n%s" % (
        card_text, chain_text,
        json.dumps([{"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                     "legs": s.get("legs"), "cites": s.get("cites"), "papers": s.get("papers")}
                    for s in kept], ensure_ascii=False, indent=1),
        papers_text or "none")
    if objections:
        prompt += "\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS; fix every one:\n- " + "\n- ".join(objections)
    max_tokens = max(2500, int(words[1] * 5))
    brief = BRIEF % (len(kept), max(0, len(kept) - 1), words[0], words[1])
    ran_out = False
    for _attempt in range(2):                 # one more try when the reply is unreadable
        budget = None if deadline is None else deadline - time.time()
        if budget is not None and budget <= 0:
            raise OutOfTime("the run was due before the Narrator could answer")
        try:
            out = client.complete_json(
                [{"role": "system", "content": brief}, {"role": "user", "content": prompt}],
                "results_section", RESULTS_SCHEMA, _embedded_json, max_tokens=max_tokens,
                temperature=temperature, budget_seconds=budget)
            ran_out = False
        except Exception as exc:                                      # noqa: BLE001
            out, ran_out = None, deadline is not None and _ran_out(exc)
        if isinstance(out, dict) and isinstance(out.get("paragraphs"), list):
            return out
    if ran_out:
        raise OutOfTime("the Narrator's call gave up at the run's deadline")
    return None
