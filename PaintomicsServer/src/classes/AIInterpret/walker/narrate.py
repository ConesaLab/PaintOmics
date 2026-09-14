"""The Narrator: one model call that writes a Results section from the kept
statements only, every sentence tagged with its statement and legs; code
checks every number and leg (verify.verify_results). One repair, then the
story is dropped and the statements stand alone.
"""
from __future__ import annotations

import json

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
    "You write the Results section of a paper from a graph walk over a pathway. Facts come only from "
    "the kept statements; the prose is yours. Rules:\n"
    "- Paragraphs in walk order, one per statement, each tagged from_statement = that statement's n, "
    "with the legs it rests on. Explain why each node led to the next under the experiment design.\n"
    "- A paragraph with from_statement null is connective: it may only say what a drawn edge or the "
    "design card says, and must contain no value and no paper.\n"
    "- Quote values exactly as they appear in the chain, with sign and two decimals, and cite legs as "
    "[eN], layers as [node · layer], papers as [N] only where the statement did.\n"
    "- Never mention a dropped statement. Past tense, no hedging words the statements do not use.\n"
    "- Length %d to %d words."
)


def narrate(client, card_text, kept, chain_text, papers_text, words=(150, 450),
            objections=None, temperature=0.3):
    """The Results dict, or None when the call fails."""
    prompt = "DESIGN CARD\n%s\n\nCHAIN\n%s\n\nKEPT STATEMENTS\n%s\n\nPAPERS\n%s" % (
        card_text, chain_text,
        json.dumps([{"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                     "legs": s.get("legs"), "cites": s.get("cites"), "papers": s.get("papers")}
                    for s in kept], ensure_ascii=False, indent=1),
        papers_text or "none")
    if objections:
        prompt += "\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS; fix every one:\n- " + "\n- ".join(objections)
    try:
        out = client.complete_json(
            [{"role": "system", "content": BRIEF % words}, {"role": "user", "content": prompt}],
            "results_section", RESULTS_SCHEMA, lambda text: None, max_tokens=2500,
            temperature=temperature)
    except Exception:                                                 # noqa: BLE001
        return None
    if not isinstance(out, dict) or not isinstance(out.get("paragraphs"), list):
        return None
    return out
