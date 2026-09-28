"""Check 3: direction logic on the regulators a statement leans on.

A feedback reporter (Cish, Socs3, Dusp1, Nfkbia ...) is induced by its own
pathway, so its fall says the pathway is down. A true inhibitor (Pten, Tsc2,
Cbl ...) is a brake, so its fall says the pathway is up. The example walk once
read a falling Cish as "derepressed JAK-STAT signalling" -- the inhibitor
rule applied to a reporter.

Code decides the direction the data implies (walker/regulators.py knows each
gene's class; the sign of its largest value is the data's direction). A model
is asked only what code cannot read: which direction the statement's words
claim for the pathway, and whether the statement would still read as true
with the regulator's values reversed -- the counter-hypothesis. A claim that
contradicts the implied direction is sent back once and then dropped; a
statement that fits the values and their opposite alike is flagged as saying
nothing about the data.
"""
from __future__ import annotations

import logging
import re
import time

from src.classes.AIInterpret import llm_client
from src.classes.AIInterpret.walker import regulators
from src.classes.AIInterpret.walker import verify

logger = logging.getLogger(__name__)

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {"claimed": {"type": "string", "enum": ["up", "down", "none"]}, "quote": {"type": "string"}},
    "required": ["claimed", "quote"], "additionalProperties": False,
}
FIT_SCHEMA = {
    "type": "object",
    "properties": {"consistent": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["consistent", "note"], "additionalProperties": False,
}
CLAIM_BRIEF = (
    "You read one interpretation statement from a multi-omics analysis. Answer which direction of ACTIVITY the "
    "statement claims for the named pathway: up (more active, derepressed, engaged), down (less active, "
    "suppressed, shut off) or none (the statement makes no claim about that pathway's activity, or only "
    "describes the gene's own values). Quote the words that carry the claim, or an empty string for none. "
    "Judge only what is written."
)
FIT_BRIEF = (
    "You check whether an interpretation statement is consistent with the values shown for one gene, given the "
    "gene's known role. Answer consistent true when the statement's reading of that gene follows from these "
    "values and the role; false when the values point the other way or the statement asserts a direction the "
    "values do not show. One line of note."
)
TEMPERATURE = 0.1
# verdict["unchecked"] for a hit the stage's deadline left no time to ask
# about, or whose call gave up on that deadline.
UNCHECKED_OUT_OF_TIME = "the direction check's time budget was spent"


class OutOfTime(Exception):
    """A direction call was not made, or gave up, because the stage's
    deadline had no time left for it."""

_SIGN_RE = re.compile(r"(?<=\s)([+−-])(?=\d)")


def data_direction(layer):
    """+1, -1 or 0: the sign of the largest-magnitude value of a layer."""
    best = 0.0
    for value in layer.get("values") or []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:
            continue
        if abs(number) > abs(best):
            best = number
    return 1 if best > 0 else (-1 if best < 0 else 0)


def panel_hits(stmt, walker):
    """The regulators of the panel among a statement's cites: one hit per gene,
    with the cited layer (a relevant one when the statement cites several)."""
    hits = {}
    for cite in stmt.get("cites") or []:
        if not isinstance(cite, (list, tuple)) or len(cite) != 2:
            continue
        node_id = verify.resolve_node(cite[0], walker)
        if node_id is None:
            continue
        layers = [l for l in walker.overlay.layers.get(node_id, []) if l["omic"].lower() == str(cite[1]).lower()]
        if not layers:
            continue
        row = regulators.panel_row(walker.label(node_id))
        for layer in layers:
            if row is None:
                row = regulators.panel_row(layer.get("member"))
        if row is None:
            continue
        layer = next((l for l in layers if l.get("relevant")), layers[0])
        sign = data_direction(layer)
        if sign == 0:
            continue
        current = hits.get(node_id)
        if current is None or (layer.get("relevant") and not current["layer"].get("relevant")):
            hits[node_id] = {"node": node_id, "gene": walker.label(node_id), "row": row, "layer": layer, "sign": sign}
    return list(hits.values())


def flipped_text(layer_text):
    """The layer text with every value's sign reversed."""
    def flip(match):
        return "−" if match.group(1) == "+" else "+"
    return _SIGN_RE.sub(flip, str(layer_text or ""))


def _statement_text(stmt):
    return "claim: %s\nprose: %s" % (stmt.get("claim") or "", stmt.get("prose") or "")


def _role(row):
    return ("a negative-feedback target of %s (induced by the pathway; its change reads WITH the pathway)"
            if row["class"] == "feedback" else
            "an upstream inhibitor of %s (a brake; its change reads AGAINST the pathway)") % row["pathway"]


def _gave_up(exc, budget_seconds):
    """Raise OutOfTime when a budgeted call gave up on its budget: that is not
    a failed call (no objection) but one that was never answered."""
    if budget_seconds is not None and llm_client.ran_out_of_budget(exc):
        raise OutOfTime(str(exc)) from exc


def claimed_direction(client, stmt, hit, temperature=TEMPERATURE, budget_seconds=None):
    """{claimed: up|down|none, quote}; None when the call fails. Raises
    OutOfTime when the call gave up on `budget_seconds`."""
    prompt = ("PATHWAY: %s\nREGULATOR NAMED IN THE STATEMENT: %s, %s\n\nSTATEMENT\n%s" % (
        hit["row"]["pathway"], hit["gene"], _role(hit["row"]), _statement_text(stmt)))
    try:
        out = client.complete_json([{"role": "system", "content": CLAIM_BRIEF}, {"role": "user", "content": prompt}],
                                   "direction_claim", CLAIM_SCHEMA, lambda text: None, max_tokens=200,
                                   temperature=temperature, budget_seconds=budget_seconds)
    except Exception as exc:                                          # noqa: BLE001
        _gave_up(exc, budget_seconds)
        logger.warning("[direction] the claim call failed", exc_info=True)
        return None
    if not isinstance(out, dict) or out.get("claimed") not in ("up", "down", "none"):
        return None
    return {"claimed": out["claimed"], "quote": str(out.get("quote") or "")[:200]}


def fits_values(client, stmt, hit, layer_text, temperature=TEMPERATURE, budget_seconds=None):
    """{consistent: bool, note}; None when the call fails. Raises OutOfTime
    when the call gave up on `budget_seconds`."""
    prompt = ("GENE: %s, %s\nVALUES OF %s (%s; the user's column labels):\n%s\n\nSTATEMENT\n%s" % (
        hit["gene"], _role(hit["row"]), hit["gene"], hit["layer"]["omic"], layer_text, _statement_text(stmt)))
    try:
        out = client.complete_json([{"role": "system", "content": FIT_BRIEF}, {"role": "user", "content": prompt}],
                                   "direction_fit", FIT_SCHEMA, lambda text: None, max_tokens=200,
                                   temperature=temperature, budget_seconds=budget_seconds)
    except Exception as exc:                                          # noqa: BLE001
        _gave_up(exc, budget_seconds)
        logger.warning("[direction] the fit call failed", exc_info=True)
        return None
    if not isinstance(out, dict) or not isinstance(out.get("consistent"), bool):
        return None
    return {"consistent": out["consistent"], "note": str(out.get("note") or "")[:200]}


def direction_check(client, statements, walker, deadline=None):
    """(verdicts, objections) over the statements that cite a panel regulator.

    verdicts[n] = [{gene, pathway, class, data_sign, implied, claimed, quote,
    fits, fits_flipped, consistent, insensitive}]; objections[n] = the
    sentences sent back to the Writer. A statement with no panel gene has no
    entry. A model call that fails leaves that verdict unchecked (no objection).

    Every call gives up by `deadline`, a 429's wait included. A call the
    deadline leaves under llm_client.MIN_CALL_SECONDS for is not made; the
    verdict of a hit with a call not made, or given up on the deadline,
    carries unchecked=UNCHECKED_OUT_OF_TIME, so a caller can tell "no
    objection" from "not asked"."""
    def budget():
        """Seconds left for the next call; OutOfTime when too few."""
        if deadline is None:
            return None
        left = deadline - time.time()
        if left < llm_client.MIN_CALL_SECONDS:
            raise OutOfTime("no time left for the call")
        return left

    verdicts, objections = {}, {}
    for stmt in statements:
        for hit in panel_hits(stmt, walker):
            row = hit["row"]
            implied = regulators.implied_direction(row, hit["sign"])
            verdict = {"gene": hit["gene"], "pathway": row["pathway"], "class": row["class"],
                       "data_sign": hit["sign"], "implied": implied, "claimed": None, "quote": "",
                       "fits": None, "fits_flipped": None, "consistent": True, "insensitive": False}
            claim = fit = flipped = None
            text = walker.overlay.layer_text(hit["node"])
            try:
                claim = claimed_direction(client, stmt, hit, budget_seconds=budget())
                fit = fits_values(client, stmt, hit, text, budget_seconds=budget())
                flipped = fits_values(client, stmt, hit, flipped_text(text), budget_seconds=budget())
            except OutOfTime:
                verdict["unchecked"] = UNCHECKED_OUT_OF_TIME
            if claim is not None:
                verdict["claimed"], verdict["quote"] = claim["claimed"], claim["quote"]
                if claim["claimed"] != "none" and claim["claimed"] != implied:
                    verdict["consistent"] = False
                    objections.setdefault(stmt["n"], []).append(
                        "%s is %s: its %s reports the pathway %s, not %s" % (
                            hit["gene"], _role(row), "fall" if hit["sign"] < 0 else "rise", implied, claim["claimed"]))
            if fit is not None:
                verdict["fits"] = fit["consistent"]
            if flipped is not None:
                verdict["fits_flipped"] = flipped["consistent"]
            if fit is not None and flipped is not None and fit["consistent"] and flipped["consistent"]:
                verdict["insensitive"] = True
                objections.setdefault(stmt["n"], []).append(
                    "the statement reads the same with %s's values reversed: state what the values show and "
                    "which way they point" % hit["gene"])
            if fit is not None and not fit["consistent"] and verdict["consistent"]:
                verdict["consistent"] = False
                verdict["fit_note"] = fit.get("note") or ""
                objections.setdefault(stmt["n"], []).append(
                    "the statement does not follow from %s's values: %s" % (hit["gene"], fit["note"]))
            verdicts.setdefault(stmt["n"], []).append(verdict)
    return verdicts, objections


def _reason(v):
    """One verdict's failure in words: a reversed reading, a claim against the
    implied direction, or a claim in the right direction that the values do
    not support (the fit step)."""
    if v["insensitive"]:
        return "reads the same with the values reversed"
    if v["claimed"] != v["implied"]:
        return "the statement claims %s where %s implies %s" % (v["claimed"], v["gene"], v["implied"])
    note = v.get("fit_note") or ""
    return "the statement does not follow from %s's values%s" % (v["gene"], ": " + note if note else "")


def gate(verdicts, dropped):
    """The gate dict from the final verdicts and the statements dropped here."""
    rows = [v for group in verdicts.values() for v in group]
    if not rows:
        return {"pass": True, "not_applicable": True, "checked": 0, "consistent": 0, "insensitive": 0,
                "unchecked": 0, "dropped": 0, "why": "no statement cites a regulator of the panel"}
    # A regulator the deadline left unasked is neither checked nor consistent;
    # one that objected before time ran out still counts against the gate.
    asked = [v for v in rows if not (v.get("unchecked") and v["consistent"] and not v["insensitive"])]
    unasked = len(rows) - len(asked)
    if not asked:
        return {"pass": True, "not_applicable": True, "checked": 0, "consistent": 0, "insensitive": 0,
                "unchecked": unasked, "dropped": dropped, "why": UNCHECKED_OUT_OF_TIME}
    consistent = sum(1 for v in asked if v["consistent"] and not v["insensitive"])
    insensitive = sum(1 for v in asked if v["insensitive"])
    out = {"pass": consistent == len(asked), "not_applicable": False, "checked": len(asked), "consistent": consistent,
           "insensitive": insensitive, "unchecked": unasked, "dropped": dropped, "why": ""}
    reasons = []
    if not out["pass"]:
        bad = [v for v in asked if not v["consistent"] or v["insensitive"]]
        reasons.append("; ".join("%s (%s): %s" % (v["gene"], v["pathway"], _reason(v)) for v in bad))
    if unasked:
        reasons.append("%d not checked: %s" % (unasked, UNCHECKED_OUT_OF_TIME))
    out["why"] = "; ".join(reasons)
    return out
