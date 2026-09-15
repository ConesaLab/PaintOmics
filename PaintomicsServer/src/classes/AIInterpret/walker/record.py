"""The sealed record: one JSON document per walk, the only thing the report,
the column and the chat read."""
from __future__ import annotations

import json
import os
import re
import time

SCHEMA = 2                    # 2: checks.gates, checks.rendered, checks.anchor, segment distances, tiers


def seal(job_id, scope, network, overlay, walker, card, statements, dropped, results,
         papers, model_used, timings, checks=None):
    graph = {"nodes": len(network.nodes), "edges": len(network.edges),
             "N": overlay.N, "K": overlay.K, "regulators": overlay.regulators,
             "degree_cap": overlay.cap, "capped": len(overlay.capped)}
    walk = walker.record()
    # the layer text of every chain node, so the record is self-contained
    layers = {}
    for leg in walk["chain"]:
        for node_id in (leg["from"], leg["to"]):
            if node_id not in layers:
                layers[node_id] = {"label": walker.label(node_id),
                                   "kind": network.nodes.get(node_id, {}).get("kind"),
                                   "r": int(bool(overlay.r.get(node_id))) if node_id in overlay.measured else None,
                                   "heat": round(overlay.heat.get(node_id, {}).get("heat", 0.0), 2),
                                   "layers": overlay.layers.get(node_id, []),
                                   "text": overlay.layer_text(node_id)}
    return {
        "schema": SCHEMA, "job": job_id, "scope": scope, "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "graph": graph, "design_card": card, "walk": walk, "nodes": layers,
        "statements": statements, "dropped": dropped, "results": results,
        "papers": papers, "checks": checks or {}, "model_used": model_used, "timings": timings,
    }


def renumber_citations(statements, dropped, results, papers):
    """Number the papers 1..n in the order the reader meets them: the Results
    section first, then the kept statements. Rewrites every [N] (and each
    number of [N, M]) in the Results text and in the statements, the
    statements' paper fields, and returns the papers map holding only the
    cited papers under their new numbers. Retrieved papers nothing cites are
    left out: a reference list numbered by retrieval order jumped to [17]
    for a section citing three papers."""
    from src.classes.AIInterpret.walker import verify

    order = []

    def meet(text):
        for ref in verify.cited_refs(text):
            if ref in papers and ref not in order:
                order.append(ref)

    if results:
        meet(results.get("summary"))
        for paragraph in results.get("paragraphs") or []:
            meet(paragraph.get("text"))
    for stmt in statements:
        meet(stmt.get("claim"))
        meet(stmt.get("prose"))
        for ref in verify.statement_papers(stmt):
            if ref in papers and ref not in order:
                order.append(ref)
    mapping = {old: new for new, old in enumerate(order, 1)}

    def rewrite(text):
        def one(match):
            numbers = [int(x) for x in re.split(r"\s*[,;]\s*", match.group(1))]
            kept = [str(mapping[n]) for n in numbers if n in mapping]
            return "[%s]" % ", ".join(kept) if kept else ""
        return verify.PAPER_LIST_RE.sub(one, str(text)) if text else text

    if results:
        results["summary"] = rewrite(results.get("summary"))
        for paragraph in results.get("paragraphs") or []:
            paragraph["text"] = rewrite(paragraph.get("text"))
    def rewrite_why(text):
        # The Verifier's reasons name papers by their retrieval number; a
        # dropped statement's paper is usually not in the list, so it is named
        # by PMID rather than by a number the reference list does not hold.
        def one(match):
            names = []
            for n in (int(x) for x in re.split(r"\s*[,;]\s*", match.group(1))):
                if n in mapping:
                    names.append("[%d]" % mapping[n])
                elif n in papers:
                    names.append("PMID %s" % papers[n].get("pmid"))
                else:
                    names.append("[?]")
            return ", ".join(names)
        return verify.PAPER_LIST_RE.sub(one, str(text)) if text else text

    for stmt in dropped:
        if stmt.get("why"):
            stmt["why"] = rewrite_why(stmt["why"])
    for stmt in list(statements) + list(dropped):
        for key in ("claim", "prose"):
            if stmt.get(key):
                stmt[key] = rewrite(stmt[key])
        if stmt.get("papers"):
            stmt["papers"] = [mapping[verify.paper_ref(p)] for p in stmt["papers"]
                              if verify.paper_ref(p) in mapping]
        if isinstance(stmt.get("evidence"), list):
            stmt["evidence"] = [dict(item, ref=mapping[item["ref"]]) for item in stmt["evidence"]
                                if isinstance(item, dict) and item.get("ref") in mapping]
        for beyond in stmt.get("beyond") or []:
            if isinstance(beyond, dict) and beyond.get("paper") is not None:
                beyond["paper"] = mapping.get(verify.paper_ref(beyond["paper"]))
            if isinstance(beyond, dict) and beyond.get("claim"):
                beyond["claim"] = rewrite(beyond["claim"])
    return {mapping[old]: papers[old] for old in order}


def save(record, out_dir, name=None):
    os.makedirs(out_dir, exist_ok=True)
    name = name or "walk_%s_%s.json" % (record["job"], record["scope"].replace(":", "_"))
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=1)
    return path
