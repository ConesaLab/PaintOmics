"""The sealed record: one JSON document per walk, the only thing the report,
the column and the chat read."""
from __future__ import annotations

import json
import os
import time

SCHEMA = 1


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


def save(record, out_dir, name=None):
    os.makedirs(out_dir, exist_ok=True)
    name = name or "walk_%s_%s.json" % (record["job"], record["scope"].replace(":", "_"))
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=1)
    return path
