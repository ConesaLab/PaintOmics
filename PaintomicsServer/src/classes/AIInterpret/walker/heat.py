"""Heat, degree cap and the two scans.

heat(v) = -log10 P[X >= x], X ~ Hypergeom(N, K, n) over the 1-hop neighbourhood
of v: n measured neighbours, x relevant among them; N measured and K relevant
over the graph without v. Conditioning on n is what removes the degree bias: a
node with one measured neighbour can reach at most p = K/N whichever neighbour
it has. Computed once per overlay; both scans only select and rank.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np
from scipy.stats import hypergeom


def compute_heat(network, measured, relevant):
    """{node: {n, x, p, heat, degree}} for every node of the network."""
    N = len(measured)
    K = sum(1 for v in measured if relevant.get(v))
    out = {}
    for node_id in network.nodes:
        nb = network.neighbours(node_id)
        n = sum(1 for w in nb if w in measured)
        x = sum(1 for w in nb if w in measured and relevant.get(w))
        n_total = N - (1 if node_id in measured else 0)
        k_total = K - (1 if (node_id in measured and relevant.get(node_id)) else 0)
        if n > 0 and n_total > 0:
            p = float(hypergeom.sf(x - 1, n_total, k_total, n))
        else:
            p = 1.0
        out[node_id] = {"n": n, "x": x, "p": p,
                        "heat": -math.log10(max(p, 1e-300)) if n else 0.0,
                        "degree": len(nb)}
    return out


def degree_cap(heat, percentile=99):
    """(cap, capped): the degree above which nodes are hubs, and the hubs.
    A cap of None means no cap (pathway walks)."""
    degrees = [h["degree"] for h in heat.values() if h["degree"] > 0]
    if not degrees:
        return None, set()
    cap = float(np.percentile(degrees, percentile))
    return cap, {v for v, h in heat.items() if h["degree"] > cap}


def _within(network, start, radius, blocked):
    """{node: (distance, first hop)} for every node within ``radius`` steps,
    never passing through a blocked (capped) node."""
    dist = {start: (0, None)}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        d = dist[u][0]
        if d >= radius:
            continue
        for w in network.neighbours(u):
            if w in dist or w in blocked:
                continue
            first = w if d == 0 else dist[u][1]
            dist[w] = (d + 1, first)
            queue.append(w)
    return dist


def scan_graph(network, overlay, sep, limit):
    """Every measured node ranked by heat, with the seed candidates flagged:
    r = 1, n >= 1, not a hub, and not within ``sep`` edges of a hotter candidate."""
    rows = []
    for node_id in network.nodes:
        if node_id not in overlay.measured:
            continue
        h = overlay.heat[node_id]
        rows.append({"id": node_id, "label": network.nodes[node_id]["label"],
                     "kind": network.nodes[node_id]["kind"],
                     "r": int(bool(overlay.r.get(node_id))), "heat": round(h["heat"], 2),
                     "x": h["x"], "n": h["n"], "degree": h["degree"],
                     "hub": node_id in overlay.capped, "candidate": False, "skipped": None})
    rows.sort(key=lambda r: (-r["heat"], -r["degree"], r["label"]))
    blocked = {}
    chosen = 0
    for row in rows:
        if chosen >= limit:
            break
        if not row["r"] or row["n"] < 1 or row["hub"]:
            continue
        if row["id"] in blocked:
            row["skipped"] = blocked[row["id"]]
            continue
        row["candidate"] = True
        chosen += 1
        for w in _within(network, row["id"], sep, overlay.capped):
            blocked.setdefault(w, row["label"])
    return rows


def scan_here(network, overlay, current, radius, walked):
    """The measured nodes within ``radius`` steps of ``current``, hottest first,
    with distance, first hop and whether the walk has been there."""
    reach = _within(network, current, radius, overlay.capped)
    rows = []
    for node_id, (d, first) in reach.items():
        if d == 0 or node_id not in overlay.measured:
            continue
        h = overlay.heat[node_id]
        rows.append({"id": node_id, "label": network.nodes[node_id]["label"],
                     "kind": network.nodes[node_id]["kind"],
                     "r": int(bool(overlay.r.get(node_id))), "heat": round(h["heat"], 2),
                     "x": h["x"], "n": h["n"], "distance": d,
                     "via": network.nodes[first]["label"] if first else None,
                     "walked": node_id in walked})
    rows.sort(key=lambda r: (-r["heat"], r["distance"], r["label"]))
    return rows
