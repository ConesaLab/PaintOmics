"""The sense check: a second model call, with its own brief, that answers five
questions per statement as fields. Code enforces the verdicts: a failing
statement goes back to the Writer once, then drops.
"""
from __future__ import annotations

import json

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

BRIEF = (
    "You check interpretation statements against the data they cite and the experiment design. "
    "For each statement answer five questions as fields, ok true/false with a one-line note:\n"
    "direction: does the direction claimed match the values quoted?\n"
    "timing: does the timing fit the design -- an effect after the perturbation, not a difference "
    "already present at the baseline?\n"
    "contrast: is the contrast claimed one the design actually has?\n"
    "layers: where the cited layers differ (transcript vs protein vs accessibility), does the statement say so, "
    "and does it call a series that changes sign at every point noise rather than a disagreement?\n"
    "literature: every claim beyond what the drawn edge says carries a paper or is worded as a hypothesis; "
    "a claim the pathway itself supports needs no paper.\n"
    "Be strict and specific. Judge only what is written.\n"
)


def sense_check(client, card_text, statements, chain_text, temperature=0.1):
    """{n: {field: {ok, note}}}; None when the call fails (the caller then keeps
    the statements marked unchecked rather than inventing verdicts)."""
    body = json.dumps([{"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                        "cites": s.get("cites"), "legs": s.get("legs"),
                        "grounded_in": s.get("grounded_in"), "beyond": s.get("beyond")}
                       for s in statements], ensure_ascii=False, indent=1)
    prompt = "DESIGN CARD\n%s\n\nCHAIN (legs with readings and layer values)\n%s\n\nSTATEMENTS\n%s" % (
        card_text, chain_text, body)
    try:
        out = client.complete_json(
            [{"role": "system", "content": BRIEF + regulators.reading_rule()}, {"role": "user", "content": prompt}],
            # Five verdicts with a note each: about 350 tokens a statement. A
            # fixed 2,500 cut a twelve-statement answer off and the whole check
            # read as unavailable.
            "sense_check", SENSE_SCHEMA, lambda text: None, max_tokens=max(2500, 600 + 350 * len(statements)),
            temperature=temperature)
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
