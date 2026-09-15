"""Check 1 at request time: is the walk more than the graph would give any job?

A permutation test of the DATA, not of the model: the scripted greedy walker
runs once on the real relevant flags and fifty times on measurements
permuted over the measured nodes of the walked graph (a permuted job is one
whose measurements landed on other genes: flag, values and direction move
together; heat is recomputed each time). Two
statistics per run: how many *modules* the walker found and the mean heat
of the seeds it chose. A module is one stretch of the chain between jumps
that holds at least three relevant walked nodes and whose signed legs agree
with the values at least half the time (an activation joins two nodes
moving the same way, an inhibition two moving apart). The same walker reads
both sides, so a difference is the data's, not a policy's; a graph whose
real flags give the walker no more than permuted flags do makes every walk
on it an artifact of the graph. Seconds, not minutes; no model in the loop.
"""
from __future__ import annotations

import copy
import random
import statistics
from collections import OrderedDict

from src.classes.AIInterpret.walker import heat as heat_mod
from src.classes.AIInterpret.walker import policies
from src.classes.AIInterpret.walker.walk import Walker

MODULE_MIN_RELEVANT = 3
MODULE_MIN_CONCORDANCE = 0.5
DEFAULT_K = 50
ALPHA = 0.05


def permute_flags(graph, overlay, rng):
    """A shallow copy of the overlay whose measured nodes have exchanged their
    measurements: each node receives a donor node's relevant flag AND its
    layers, so a node's direction travels with its flag (a permuted job is
    one whose measurements landed on other genes; a flag without its values
    would leave the null's relevant nodes directionless and no module could
    form in it). Heat recomputed on ``graph``. K is unchanged by construction."""
    ov = copy.copy(overlay)
    nodes = sorted(overlay.measured)
    donors = list(nodes)
    rng.shuffle(donors)
    ov.r = {v: bool(overlay.r.get(d)) for v, d in zip(nodes, donors)}
    ov.layers = OrderedDict((v, overlay.layers.get(d, [])) for v, d in zip(nodes, donors))
    ov.heat = heat_mod.compute_heat(graph, ov.measured, ov.r)
    ov.K = sum(1 for v in nodes if ov.r[v])
    return ov


def node_direction(overlay, node_id):
    """+1, -1 or 0: the sign of the largest value among a node's relevant layers."""
    best = 0.0
    for layer in overlay.layers.get(node_id, []):
        if not layer.get("relevant"):
            continue
        for value in layer.get("values") or []:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number and abs(number) > abs(best):
                best = number
    return 1 if best > 0 else (-1 if best < 0 else 0)


def leg_concordance(leg, overlay):
    """True when a signed step leg's ends move as the arrow says, False when
    they move against it, None when the leg is unsigned or an end has no
    relevant direction. ``leg`` is a chain dict or a Leg."""
    edge = leg["edge"] if isinstance(leg, dict) else leg.edge
    src, dst = (leg["from"], leg["to"]) if isinstance(leg, dict) else (leg.src, leg.dst)
    sign = (edge or {}).get("sign")
    if sign not in (1, -1):
        return None
    a, b = node_direction(overlay, src), node_direction(overlay, dst)
    if not a or not b:
        return None
    return sign == a * b


def segments(chain):
    """The chain cut at its jumps: lists of step legs (dicts), one per stretch."""
    out, current = [], []
    for leg in chain:
        if leg["kind"] == "jump":
            if current:
                out.append(current)
            current = []
        else:
            current.append(leg)
    if current:
        out.append(current)
    return out


def is_module(legs, overlay):
    """A stretch of legs is a module when its nodes hold at least
    MODULE_MIN_RELEVANT relevant ones and its signed legs agree with the
    values at least MODULE_MIN_CONCORDANCE of the time (one signed leg with
    directions at both ends is required)."""
    nodes = set()
    for leg in legs:
        nodes.update((leg["from"], leg["to"]))
    if sum(1 for v in nodes if overlay.r.get(v)) < MODULE_MIN_RELEVANT:
        return False
    verdicts = [c for c in (leg_concordance(leg, overlay) for leg in legs) if c is not None]
    return bool(verdicts) and sum(verdicts) / float(len(verdicts)) >= MODULE_MIN_CONCORDANCE


def modules_found(walker, overlay):
    """How many stretches of the chain are modules (is_module)."""
    return sum(1 for legs in segments(walker.record()["chain"]) if is_module(legs, overlay))


def seed_heat(walker, overlay):
    """The mean heat of the plan's seeds; 0 without a plan."""
    seeds = (walker.plan or {}).get("seeds") or []
    if not seeds:
        return 0.0
    return statistics.mean(overlay.heat.get(v, {}).get("heat", 0.0) for v in seeds)


def structure_null(graph, overlay, params, real_walker, k=DEFAULT_K, seed=0):
    """The gate dict: {k, real, walk, null_mean, p_modules, p_heat, pass, why}.
    ``real`` is the scripted walker on the real flags; ``walk`` the model
    walk's own module count, for the reader."""
    scripted = policies.greedy(Walker(graph, overlay, real_walker.scope, dict(params)))
    real = {"modules": modules_found(scripted, overlay), "seed_heat": round(seed_heat(scripted, overlay), 3)}
    walk = {"modules": modules_found(real_walker, overlay), "seed_heat": round(seed_heat(real_walker, overlay), 3)}
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
        why = ("permuted data gives the scripted walker as many modules (%d real; p=%.2f) and seeds as hot "
               "(p=%.2f) as the real data does: on this graph the data has no structure a walk could find"
               % (real["modules"], p_modules, p_heat))
    return {"k": k, "real": real, "walk": walk,
            "null_mean": {"modules": round(statistics.mean(null_modules), 2) if null_modules else 0.0,
                          "seed_heat": round(statistics.mean(null_heat), 3) if null_heat else 0.0},
            "p_modules": round(p_modules, 4), "p_heat": round(p_heat, 4), "pass": bool(passed), "why": why}
