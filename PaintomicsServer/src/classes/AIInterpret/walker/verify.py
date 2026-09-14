"""What code checks about statements and about the Results text.

The Writer and the Narrator are models; nothing they say reaches the user
until these checks pass. Every check here is mechanical: a citation resolves
or it does not, a number is in the record verbatim or it is not.
"""
from __future__ import annotations

import re

NUMBER_RE = re.compile(r"[+\-−±]\s?\d+(?:\.\d+)?")
LEG_RE = re.compile(r"\[e(\d+)\]")
PAPER_RE = re.compile(r"\[(\d+)\]")
STATEMENT_MIN, STATEMENT_MAX = 3, 5
RESULTS_WORDS = {"pathway": (150, 450), "network": (300, 900)}


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


def verify_statement(stmt, walker, papers):
    """The objections to one submitted statement; an empty list is a pass."""
    problems = []
    chain = walker.record()["chain"]
    n_legs = len(chain)
    legs = stmt.get("legs") or []
    if not legs:
        problems.append("names no leg")
    for leg in legs:
        if not isinstance(leg, int) or leg < 1 or leg > n_legs:
            problems.append("leg e%s is not on the chain" % leg)
    cites = stmt.get("cites") or []
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
    if non_relevant and "not relevant" not in prose.lower():
        problems.append("cites a non-relevant layer (%s) without saying \"not relevant\""
                        % ", ".join(non_relevant))
    for ground in stmt.get("grounded_in") or []:
        leg = ground.get("leg") if isinstance(ground, dict) else None
        if leg not in legs:
            problems.append("grounded_in names leg e%s the statement does not cite" % leg)
    for beyond in stmt.get("beyond") or []:
        if not isinstance(beyond, dict):
            problems.append("a beyond entry is not an object")
            continue
        paper = beyond.get("paper")
        if beyond.get("hypothesis"):
            continue
        if paper is None or int(paper) not in papers:
            problems.append("beyond claim %r has neither a retrieved paper nor the hypothesis flag"
                            % str(beyond.get("claim", ""))[:60])
    for paper in stmt.get("papers") or []:
        if int(paper) not in papers:
            problems.append("paper [%s] was never retrieved" % paper)
    return problems


def verify_statements(statements, walker, papers):
    """[(statement, problems)] for every statement, plus a count objection."""
    out = [(s, verify_statement(s, walker, papers)) for s in statements]
    count_problem = None
    if not (STATEMENT_MIN <= len(statements) <= STATEMENT_MAX):
        count_problem = "%d statements; between %d and %d are required" % (
            len(statements), STATEMENT_MIN, STATEMENT_MAX)
    return out, count_problem


def _record_numbers(walker):
    """Every value token that appears in any chain node's layer text."""
    tokens = set()
    for node_id in chain_nodes(walker.record()["chain"]):
        for match in NUMBER_RE.finditer(walker.overlay.layer_text(node_id)):
            tokens.add(_norm(match.group(0)))
    return tokens


def _norm(token):
    return token.replace(" ", "").replace("-", "−").replace("+", "+")


def verify_results(results, kept, dropped, walker, scope="pathway"):
    """Objections to the Narrator's Results section."""
    problems = []
    chain = walker.record()["chain"]
    paragraphs = results.get("paragraphs") or []
    if not paragraphs:
        return ["no paragraphs"]
    kept_ids = {int(s["n"]) for s in kept}
    covered = set()
    words = 0
    numbers = _record_numbers(walker)
    for i, para in enumerate(paragraphs, 1):
        text = str(para.get("text") or "")
        words += len(re.sub(r"\[[^\]]*\]", "", text).split())
        for leg in LEG_RE.findall(text):
            if int(leg) < 1 or int(leg) > len(chain):
                problems.append("paragraph %d cites leg e%s, not on the chain" % (i, leg))
        for leg in para.get("legs") or []:
            if not isinstance(leg, int) or leg < 1 or leg > len(chain):
                problems.append("paragraph %d tags leg e%s, not on the chain" % (i, leg))
        origin = para.get("from_statement")
        if origin is None:
            if NUMBER_RE.search(text):
                problems.append("link paragraph %d quotes a value" % i)
            if PAPER_RE.search(re.sub(r"\[e\d+\]", "", text)):
                problems.append("link paragraph %d cites a paper" % i)
        else:
            if int(origin) not in kept_ids:
                problems.append("paragraph %d comes from statement %s, which was not kept" % (i, origin))
            covered.add(int(origin))
        for match in NUMBER_RE.finditer(text):
            token = _norm(match.group(0))
            if token.startswith("±"):
                continue
            if token not in numbers:
                problems.append("paragraph %d quotes %s, not in the record" % (i, match.group(0)))
    for n in kept_ids - covered:
        problems.append("statement %d is not covered" % n)
    for stmt in dropped:
        head = " ".join(str(stmt.get("claim", "")).split()[:5]).lower()
        if head and any(head in str(p.get("text", "")).lower() for p in paragraphs):
            problems.append("a dropped statement is mentioned: %r" % head)
    lo, hi = RESULTS_WORDS["network" if scope == "network" else "pathway"]
    if not (lo <= words <= hi):
        problems.append("%d words; between %d and %d are required" % (words, lo, hi))
    return problems
