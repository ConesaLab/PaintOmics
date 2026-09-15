"""Check 1 at request time: is the walk more than the graph would give any job?

The relevant flag is permuted over the measured nodes of the walked graph
(a permuted job is one whose measurements landed on other genes), heat is
recomputed, and the scripted greedy walker runs with the run's own
parameters. Two statistics are kept per permutation: how many modules it
found (segments, or the one chain, holding at least three relevant walked
nodes) and the mean heat of the seeds it chose. The real run's statistics
come from the model walk, so the null is conservative: greedy on permuted
flags is a stronger walker than a model on real ones. Seconds, not minutes;
no model in the loop.
"""
from __future__ import annotations

import copy
import random
import statistics

from src.classes.AIInterpret.walker import heat as heat_mod
from src.classes.AIInterpret.walker import policies
from src.classes.AIInterpret.walker.walk import Walker

MODULE_MIN_RELEVANT = 3
DEFAULT_K = 50
ALPHA = 0.05


def permute_flags(graph, overlay, rng):
    """A shallow copy of the overlay whose relevant flags are permuted over the
    measured nodes; heat recomputed on ``graph``. K is unchanged by construction."""
    ov = copy.copy(overlay)
    nodes = sorted(overlay.measured)
    flags = [bool(overlay.r.get(v)) for v in nodes]
    rng.shuffle(flags)
    ov.r = dict(zip(nodes, flags))
    ov.heat = heat_mod.compute_heat(graph, ov.measured, ov.r)
    ov.K = sum(1 for v in nodes if ov.r[v])
    return ov


def _segments(walker):
    """The walked node sets per module: the recorded segments, else the whole chain."""
    chain = walker.record()["chain"]
    segments = walker.record().get("segments") or []
    if not segments:
        nodes = set()
        for leg in chain:
            nodes.update((leg["from"], leg["to"]))
        return [nodes] if nodes else []
    out = []
    for segment in segments:
        nodes = set()
        for leg in chain:
            if segment["first"] <= leg["n"] <= segment["last"]:
                nodes.update((leg["from"], leg["to"]))
        out.append(nodes)
    return out


def modules_found(walker, overlay):
    """Modules whose walked nodes hold at least MODULE_MIN_RELEVANT relevant ones."""
    return sum(1 for nodes in _segments(walker)
               if sum(1 for v in nodes if overlay.r.get(v)) >= MODULE_MIN_RELEVANT)


def seed_heat(walker, overlay):
    """The mean heat of the plan's seeds; 0 without a plan."""
    seeds = (walker.plan or {}).get("seeds") or []
    if not seeds:
        return 0.0
    return statistics.mean(overlay.heat.get(v, {}).get("heat", 0.0) for v in seeds)


def structure_null(graph, overlay, params, real_walker, k=DEFAULT_K, seed=0):
    """The gate dict: {k, real, null_mean, p_modules, p_heat, pass, why}."""
    real = {"modules": modules_found(real_walker, overlay),
            "seed_heat": round(seed_heat(real_walker, overlay), 3)}
    rng = random.Random(seed)
    null_modules, null_heat = [], []
    for _ in range(k):
        ov = permute_flags(graph, overlay, rng)
        walker = policies.greedy(Walker(graph, ov, real_walker.scope, dict(params)))
        null_modules.append(modules_found(walker, ov))
        null_heat.append(seed_heat(walker, ov))
    p_modules = (1 + sum(1 for m in null_modules if m >= real["modules"])) / float(k + 1)
    p_heat = (1 + sum(1 for h in null_heat if h >= real["seed_heat"])) / float(k + 1)
    passed = p_modules < ALPHA or (real["modules"] >= 2 and p_heat < ALPHA)
    why = ""
    if not passed:
        why = ("permuted data gives as many modules as this walk found (%d; p=%.2f) and seeds as hot "
               "(p=%.2f): the walk is not distinguishable from the graph alone"
               % (real["modules"], p_modules, p_heat))
    return {"k": k, "real": real,
            "null_mean": {"modules": round(statistics.mean(null_modules), 2) if null_modules else 0.0,
                          "seed_heat": round(statistics.mean(null_heat), 3) if null_heat else 0.0},
            "p_modules": round(p_modules, 4), "p_heat": round(p_heat, 4), "pass": bool(passed), "why": why}
