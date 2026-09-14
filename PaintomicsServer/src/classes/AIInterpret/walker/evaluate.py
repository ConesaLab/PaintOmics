"""Test 1: planted-module recall, no model in the loop.

On a real graph, grow a connected module of m nodes, set r = 1 on it with
probability 0.9 and elsewhere at rate q, recompute heat, run the greedy policy,
and measure whether a seed landed in or next to the module, how much of the
module the walk covered, and how much of the walk was module. Everything else
about the job -- values included -- stays as it is.
"""
from __future__ import annotations

import copy
import random
import statistics

from src.classes.AIInterpret.walker import heat as heat_mod
from src.classes.AIInterpret.walker import policies
from src.classes.AIInterpret.walker.walk import Walker

GRID_M = (6, 12)
GRID_Q = (0.10, 0.33, 0.50)
PASS = {"recall": 0.7, "seed_hit": 0.9}       # at q = 0.33, m = 12, proposed


def grow_module(network, measured, m, rng):
    """A connected set of m measured nodes by breadth-first growth from a random
    measured node, hubs excluded; None when the graph cannot supply one."""
    pool = sorted(v for v in measured if network.neighbours(v))
    if len(pool) < m:
        return None
    for _ in range(50):
        start = rng.choice(pool)
        module, frontier = [start], [start]
        seen = {start}
        while frontier and len(module) < m:
            u = frontier.pop(0)
            nb = sorted(w for w in network.neighbours(u) if w in measured and w not in seen)
            rng.shuffle(nb)
            for w in nb:
                if len(module) >= m:
                    break
                seen.add(w)
                module.append(w)
                frontier.append(w)
        if len(module) == m:
            return set(module)
    return None


def plant(network, overlay, module, q, rng, p_in=0.9):
    """A copy of the overlay with r replaced by the planted flags and heat recomputed."""
    ov = copy.copy(overlay)
    ov.r = {v: (rng.random() < (p_in if v in module else q)) for v in overlay.measured}
    ov.heat = heat_mod.compute_heat(network, ov.measured, ov.r)
    ov.K = sum(1 for v in ov.measured if ov.r[v])
    return ov


def one_run(network, overlay, params, m, q, rng):
    module = grow_module(network, overlay.measured, m, rng)
    if module is None:
        return None
    ov = plant(network, overlay, module, q, rng)
    walker = policies.greedy(Walker(network, ov, "planted", dict(params)))
    walked = set(walker.visited)
    seeds = set((walker.plan or {}).get("seeds") or [])
    near = set(module)
    for v in module:
        near |= network.neighbours(v)
    hit = int(any(s in near for s in seeds))
    recall = len(walked & module) / float(len(module))
    precision = len(walked & module) / float(len(walked)) if walked else 0.0
    return {"m": m, "q": q, "seed_hit": hit, "recall": recall, "precision": precision,
            "walked": len(walked), "legs": len(walker.chain)}


def grid(network, overlay, params, plants=100, seed=0, ms=GRID_M, qs=GRID_Q):
    """Mean seed hit, recall and precision per (m, q), with every run kept."""
    rng = random.Random(seed)
    runs, summary = [], []
    for m in ms:
        for q in qs:
            cell = []
            for _ in range(plants):
                r = one_run(network, overlay, params, m, q, rng)
                if r is not None:
                    cell.append(r)
            runs.extend(cell)
            if cell:
                summary.append({"m": m, "q": q, "plants": len(cell),
                                "seed_hit": statistics.mean(r["seed_hit"] for r in cell),
                                "recall": statistics.mean(r["recall"] for r in cell),
                                "precision": statistics.mean(r["precision"] for r in cell)})
    verdict = None
    for cell in summary:
        if cell["m"] == 12 and abs(cell["q"] - 0.33) < 1e-9:
            verdict = {"recall": cell["recall"] >= PASS["recall"],
                       "seed_hit": cell["seed_hit"] >= PASS["seed_hit"], "cell": cell}
    return {"summary": summary, "runs": runs, "pass": verdict, "params": dict(params),
            "plants": plants, "seed": seed}
