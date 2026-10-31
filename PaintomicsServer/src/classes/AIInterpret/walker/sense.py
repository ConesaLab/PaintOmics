"""The sense check: a second model call, with its own brief, that answers five
questions per statement as fields. Code enforces the verdicts: a failing
statement goes back to the Writer once, then drops.
"""
from __future__ import annotations

import json
import re

from src.classes.AIInterpret.walker import regulators

VERDICT = {"type": "object",
           "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}},
           "required": ["ok", "note"], "additionalProperties": False}
SENSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "direction": VERDICT, "timing": VERDICT, "contrast": VERDICT,
                    "layers": VERDICT, "literature": VERDICT,
                },
                "required": ["n", "direction", "timing", "contrast", "layers", "literature"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}
FIELDS = ("direction", "timing", "contrast", "layers", "literature")

_BRIEF_HEAD = (
    "You check interpretation statements against the data they cite and the experiment design. "
    "For each statement answer five questions as fields, ok true/false with a one-line note:\n"
    "direction: does the direction claimed match the values quoted?\n"
)
TIMING_QUESTION = (
    "timing: does the timing fit the design -- an effect after the perturbation, not a difference "
    "already present at the baseline?\n"
)
# The timing question for a card that states no perturbation and whose columns
# have no order. Since #209 (29 Oct 2026) a job with an empty design box gets
# such a card ("perturbation: not stated", axis kind "groups") instead of the
# placeholder's knockout, and asked about "an effect after the perturbation"
# the model failed statements that make no timing claim at all: on job
# UJl1G1b334 (S. gordonii, sgo00340, columns LSr.LSt, MSr.MSt, MSr.LSr) 4 of
# 10 statements dropped on timing -- opposite signs across two contrasts, a
# hut-gene fact from B. subtilis, "no differential expression in any
# condition" -- the only timing drops in a 64-walk round. With no before and
# after to fit, what is left to check is a time course or an order the design
# lacks.
UNORDERED_TIMING_QUESTION = (
    "timing: the design states no perturbation and its columns have no order, so there is no before and after "
    "to fit. ok unless the statement reads the columns as a time course or an order (a rise from one column to "
    "the next, early or late, an effect after a treatment the design does not state); a value in one column, "
    "columns compared by name and a fact from the literature are ok.\n"
)
_BRIEF_TAIL = (
    "contrast: is the contrast claimed one the design actually has?\n"
    "layers: where the cited layers differ (transcript vs protein vs accessibility), does the statement say so, "
    "and does it call a series that changes sign at every point noise rather than a disagreement?\n"
    "literature: every claim beyond what the drawn edge says carries a paper or is worded as a hypothesis; "
    "a claim the pathway itself supports needs no paper.\n"
    "Be strict and specific. Judge only what is written.\n"
)
BRIEF = _BRIEF_HEAD + TIMING_QUESTION + _BRIEF_TAIL
# Axis kinds whose columns carry no order (card.axis_kind): "time" and
# "ordered" keep the perturbation question.
UNORDERED_AXES = ("groups", "unlabeled")
# How a card says it has no perturbation: the deterministic card writes "not
# stated", the model card is told to say so, a user may write "none".
NOT_STATED_RE = re.compile(r"^\W*(?:not (?:stated|given|specified|described|known)|none|unknown|n/?a|"
                           r"no perturbation)\b", re.I)


def perturbation_stated(card):
    """True when the card names a perturbation: a perturbation text that is
    not a "not stated" or a perturbed gene."""
    text = str(card.get("perturbation") or "").strip()
    return bool(card.get("perturbed_genes")) or bool(text and not NOT_STATED_RE.match(text))


def timing_question(card=None):
    """The timing line the card allows: the perturbation question unless the
    card states no perturbation and its axis has no order. No card, today's
    question."""
    if card and not perturbation_stated(card) and card.get("axis_kind", "groups") in UNORDERED_AXES:
        return UNORDERED_TIMING_QUESTION
    return TIMING_QUESTION


def brief(card=None):
    return _BRIEF_HEAD + timing_question(card) + _BRIEF_TAIL


def sense_check(client, card_text, statements, chain_text, temperature=0.1, budget_seconds=None, card=None):
    """{n: {field: {ok, note}}}; None when the call fails (the caller then keeps
    the statements marked unchecked rather than inventing verdicts).
    `budget_seconds` bounds the call, retries included; `card`, the design
    card `card_text` was written from, sets the timing question."""
    body = json.dumps([{"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                        "cites": s.get("cites"), "legs": s.get("legs"),
                        "grounded_in": s.get("grounded_in"), "beyond": s.get("beyond")}
                       for s in statements], ensure_ascii=False, indent=1)
    prompt = "DESIGN CARD\n%s\n\nCHAIN (legs with readings and layer values)\n%s\n\nSTATEMENTS\n%s" % (
        card_text, chain_text, body)
    try:
        out = client.complete_json(
            [{"role": "system", "content": brief(card) + regulators.reading_rule()},
             {"role": "user", "content": prompt}],
            # Five verdicts with a note each: about 350 tokens a statement. A
            # fixed 2,500 cut a twelve-statement answer off and the whole check
            # read as unavailable.
            "sense_check", SENSE_SCHEMA, lambda text: None, max_tokens=max(2500, 600 + 350 * len(statements)),
            temperature=temperature, budget_seconds=budget_seconds)
    except Exception:                                                 # noqa: BLE001
        return None
    if not isinstance(out, dict) or not isinstance(out.get("verdicts"), list):
        return None
    verdicts = {}
    for v in out["verdicts"]:
        try:
            n = int(v["n"])
        except (KeyError, TypeError, ValueError):
            continue
        verdicts[n] = {f: {"ok": bool(v.get(f, {}).get("ok")), "note": str(v.get(f, {}).get("note", ""))}
                       for f in FIELDS}
    return verdicts


def failed_fields(verdict):
    return [f for f in FIELDS if verdict and not verdict[f]["ok"]]
