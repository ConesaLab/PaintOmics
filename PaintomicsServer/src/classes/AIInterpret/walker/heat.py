"""Heat, degree cap and the two scans.

heat(v) = -log10 P[X >= x], X ~ Hypergeom(N, K, n) over the 1-hop neighbourhood
of v: n measured neighbours, x relevant among them; N measured and K relevant
over the graph without v. Conditioning on n is what removes the degree bias: a
node with one measured neighbour can reach at most p = K/N whichever neighbour
it has. Computed once per overlay; both scans only select and rank.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from scipy.stats import hypergeom

from src.classes.AIInterpret.walker.tiers import is_currency


def compute_heat(network, measured, relevant):
    """{node: {n, x, p, heat, degree}} for every node of the network.

    Neighbour counts are gathered in one pass over the adjacency; the
    hypergeometric tail is one vectorised SciPy call over every node, not one
    call per node (the planted-module grid recomputes heat hundreds of times).
    """
    ids = list(network.nodes)
    index = {v: i for i, v in enumerate(ids)}
    meas = np.zeros(len(ids), dtype=bool)
    rel = np.zeros(len(ids), dtype=bool)
    for v in measured:
        i = index.get(v)
        if i is not None:
            meas[i] = True
            rel[i] = bool(relevant.get(v))
    n = np.zeros(len(ids), dtype=np.int64)
    x = np.zeros(len(ids), dtype=np.int64)
    degree = np.zeros(len(ids), dtype=np.int64)
    for i, v in enumerate(ids):
        nb = [index[w] for w in network.neighbours(v) if w in index]
        degree[i] = len(nb)
        if nb:
            m = meas[nb]
            n[i] = int(m.sum())
            x[i] = int((m & rel[nb]).sum())
    N = int(meas.sum())
    K = int(rel.sum())
    n_total = N - meas.astype(np.int64)
    k_total = K - (meas & rel).astype(np.int64)
    p = np.ones(len(ids))
    live = (n > 0) & (n_total > 0)
    if live.any():
        p[live] = hypergeom.sf(x[live] - 1, n_total[live], k_total[live], n[live])
    heat = np.where(n > 0, -np.log10(np.maximum(p, 1e-300)), 0.0)
    return {v: {"n": int(n[i]), "x": int(x[i]), "p": float(p[i]), "heat": float(heat[i]),
                "degree": int(degree[i])} for i, v in enumerate(ids)}


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


ANCHOR_RADIUS = 2          # a candidate this close to the anchor is "in its neighbourhood"


def scan_graph(network, overlay, sep, limit, dist=None):
    """Every measured node ranked by heat, with the seed candidates flagged:
    r = 1, n >= 1, not a hub, not a currency metabolite, and not within
    ``sep`` edges of a hotter candidate.

    Anchored (``dist`` = node -> steps from the perturbed gene): the candidate
    slots are filled first from the anchor's neighbourhood (within
    ANCHOR_RADIUS steps, hottest first, the same separation rule), then by
    heat over the whole graph for whatever slots remain, and the candidates
    are listed nearest the anchor first. A perturbation with a rich
    neighbourhood is walked from that neighbourhood; one with a poor
    neighbourhood is walked from it and then from the hottest nodes. Every
    row carries its distance."""
    rows = []
    for node_id in network.nodes:
        if node_id not in overlay.measured:
            continue
        h = overlay.heat[node_id]
        rows.append({"id": node_id, "label": network.nodes[node_id]["label"],
                     "kind": network.nodes[node_id]["kind"],
                     "r": int(bool(overlay.r.get(node_id))), "heat": round(h["heat"], 2),
                     "x": h["x"], "n": h["n"], "degree": h["degree"],
                     "hub": node_id in overlay.capped, "candidate": False, "skipped": None,
                     "dist": None if dist is None else dist.get(node_id)})
    rows.sort(key=lambda r: (-r["heat"], -r["degree"], r["label"]))
    blocked = {}
    chosen = {"n": 0}

    def take(pool, quota, separation):
        for row in pool:
            if chosen["n"] >= quota:
                break
            if row["candidate"] or not row["r"] or row["n"] < 1 or row["hub"] or is_currency(row["id"]):
                continue
            if row["id"] in blocked:
                row["skipped"] = blocked[row["id"]]
                continue
            row["candidate"] = True
            chosen["n"] += 1
            for w in _within(network, row["id"], separation, overlay.capped):
                blocked.setdefault(w, row["label"])

    if dist:
        # The neighbourhood is small and every relevant node in it is worth a
        # seed, so the separation there is one edge whatever the graph's is.
        near = [r for r in rows if r["dist"] is not None and r["dist"] <= ANCHOR_RADIUS]
        take(near, limit, 1)
    take(rows, limit, sep)
    if dist:
        far = 10 ** 6
        rows.sort(key=lambda r: (not r["candidate"], r["dist"] if r["candidate"] and r["dist"] is not None else far,
                                 -r["heat"], -r["degree"], r["label"]))
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
