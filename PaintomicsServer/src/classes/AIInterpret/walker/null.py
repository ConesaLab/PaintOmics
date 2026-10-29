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

What a failure shows is narrower than "the data has no structure". The
scripted walker seeds on a relevant node and steps only onto relevant
neighbours, so with few relevant nodes it stalls after a leg or two on real
and permuted data alike and the test has little power. Measured on job
7O521E0Y0y (human bladder cancer, 199 of 6,070 measured genes relevant): the
network walk found 1 module against 1.1 on permuted data (p_modules 0.73,
p_heat 0.29) and failed, while a direct count found 45 edges joining two
relevant genes against 27.8 expected (p = 0.10 over 200 permutations). On
hsa05219 its 4 relevant genes of 22 measured touch no other relevant gene:
real 0 modules, null 0, p = 1, and the check can never pass there. The pass
rule stays as it was measured (D6.1); the gate also reports, from the same
permutations, how many edges join two relevant nodes against chance, and
whether a module could form on the real data at all (``untestable``), so the
reason a reader sees says what the check could and could not tell.
"""
from __future__ import annotations

import copy
import random
import statistics
from collections import OrderedDict

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from src.classes.AIInterpret.walker import heat as heat_mod
from src.classes.AIInterpret.walker import overlay as ov_mod
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
    """+1, -1 or 0: the sign of the largest value among a node's relevant
    layers, p-value and q-value columns aside (overlay.layer_sign)."""
    return ov_mod.layer_sign(*[layer for layer in overlay.layers.get(node_id, []) if layer.get("relevant")])


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


def measured_pairs(graph, nodes):
    """The edges of ``graph`` between two of ``nodes``, as two index arrays
    into ``nodes``: each unordered pair once, whichever way its arrows run.
    Only measured nodes carry a flag, real or permuted, so an edge to an
    unmeasured node can never join two relevant ones. Built once per gate (the
    network holds ~115k edges); a permutation only changes which pairs count."""
    index = {v: i for i, v in enumerate(nodes)}
    pairs = set()
    for a, b in graph.edges:
        i, j = index.get(a), index.get(b)
        if i is not None and j is not None and i != j:
            pairs.add((i, j) if i < j else (j, i))
    if not pairs:
        empty = np.zeros(0, dtype=np.intp)
        return empty, empty
    both = np.array(list(pairs), dtype=np.intp)
    return both[:, 0], both[:, 1]


def relevant_flags(r, nodes):
    """The relevant flags of ``nodes`` as a boolean vector, in their order."""
    return np.fromiter((bool(r.get(v)) for v in nodes), dtype=bool, count=len(nodes))


def adjacent_relevant(flags, left, right):
    """How many of the pairs join two relevant nodes."""
    return int(np.count_nonzero(flags[left] & flags[right]))


def largest_relevant_group(flags, left, right):
    """The size of the largest group of relevant nodes connected through
    edges between relevant nodes; 0 when nothing is relevant. It bounds what
    one stretch of the check's walker can hold: the stretch starts at a seed
    candidate, which is relevant, and every step goes to a relevant neighbour."""
    if not flags.any():
        return 0
    keep = flags[left] & flags[right]
    n = len(flags)
    links = coo_matrix((np.ones(int(keep.sum()), dtype=np.int8), (left[keep], right[keep])), shape=(n, n))
    _groups, labels = connected_components(links, directed=False)
    return int(np.bincount(labels[flags]).max())


def _counted(n, noun):
    """A count with its noun: "1 module", "6,070 measured genes"."""
    return "%s %s%s" % (format(int(n), ","), noun, "" if n == 1 else "s")


# The module minimum in words, for the sentence a reader sees.
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _why(gate, noun):
    """The reason a failed gate gives, in plain words. Two cases: no module
    could form on the real data (untestable), or the walker could not tell
    the data from shuffled data. Neither says the data has no structure: the
    check's walker sees only chains of relevant nodes, and the adjacency count
    beside it is the evidence a reader can weigh for themselves."""
    pairs = gate["adjacent_pairs"]
    minimum = _NUMBER_WORDS.get(MODULE_MIN_RELEVANT, str(MODULE_MIN_RELEVANT))
    if gate["untestable"]:
        if gate["relevant"] < MODULE_MIN_RELEVANT:
            where = "this graph holds %s, fewer than the %s a module needs" % (
                _counted(gate["relevant"], "relevant " + noun), minimum)
        else:
            where = "no %s of the %s on this graph are connected to one another (%s among them)" % (
                minimum, _counted(gate["relevant"], "relevant " + noun), _counted(pairs["real"], "adjacent pair"))
        return ("could not be tested on this data: %s, and the check's walker steps only from relevant %s to "
                "relevant %s, so the data can give it no module and there is nothing to compare with shuffled "
                "data" % (where, noun, noun))
    p_heat = gate["p_heat"]
    heat = ("seeds no hotter than on shuffled data (p = %.2f)" % p_heat if p_heat >= ALPHA else
            "seeds hotter than on shuffled data (p = %.2f), which counts only with at least two modules" % p_heat)
    return ("the check's walker, which steps only from relevant %s to relevant %s, found %s on the data and "
            "%.1f on shuffled (permuted) data on average (p = %.2f), with %s: it could not tell this data from "
            "shuffled data on this graph. Of %s, %s %s relevant; %s of them %s adjacent, %.1f expected by chance "
            "(p = %.2f)" % (noun, noun, _counted(gate["real"]["modules"], "module"), gate["null_mean"]["modules"],
                            gate["p_modules"], heat, _counted(gate["measured"], "measured " + noun),
                            format(gate["relevant"], ","), "is" if gate["relevant"] == 1 else "are",
                            _counted(pairs["real"], "pair"), "is" if pairs["real"] == 1 else "are",
                            pairs["null_mean"], pairs["p"]))


def structure_null(graph, overlay, params, real_walker, k=DEFAULT_K, seed=0):
    """The gate dict: {k, real, walk, null_mean, p_modules, p_heat, measured,
    relevant, adjacent_pairs, untestable, pass, why}. ``real`` is the scripted
    walker on the real flags; ``walk`` the model walk's own module count, for
    the reader. ``adjacent_pairs`` ({real, null_mean, p}) counts the edges
    joining two relevant nodes on the same permutations; ``untestable`` is
    true when no group of MODULE_MIN_RELEVANT relevant nodes is connected on
    the real data, so the walker can find no module whatever the null gives.
    Neither enters the pass rule."""
    scripted = policies.greedy(Walker(graph, overlay, real_walker.scope, dict(params)))
    real = {"modules": modules_found(scripted, overlay), "seed_heat": round(seed_heat(scripted, overlay), 3)}
    walk = {"modules": modules_found(real_walker, overlay), "seed_heat": round(seed_heat(real_walker, overlay), 3)}
    nodes = sorted(overlay.measured)
    left, right = measured_pairs(graph, nodes)
    flags = relevant_flags(overlay.r, nodes)
    real_pairs = adjacent_relevant(flags, left, right)
    rng = random.Random(seed)
    null_modules, null_heat, null_pairs = [], [], []
    for _ in range(k):
        ov = permute_flags(graph, overlay, rng)
        walker = policies.greedy(Walker(graph, ov, real_walker.scope, dict(params)))
        null_modules.append(modules_found(walker, ov))
        null_heat.append(seed_heat(walker, ov))
        null_pairs.append(adjacent_relevant(relevant_flags(ov.r, nodes), left, right))
    p_modules = (1 + sum(1 for m in null_modules if m >= real["modules"])) / float(k + 1)
    p_heat = (1 + sum(1 for h in null_heat if h >= real["seed_heat"])) / float(k + 1)
    passed = p_modules < ALPHA or (real["modules"] >= 2 and p_heat < ALPHA)
    gate = {"k": k, "real": real, "walk": walk,
            "null_mean": {"modules": round(statistics.mean(null_modules), 2) if null_modules else 0.0,
                          "seed_heat": round(statistics.mean(null_heat), 3) if null_heat else 0.0},
            "p_modules": round(p_modules, 4), "p_heat": round(p_heat, 4),
            "measured": len(nodes), "relevant": int(flags.sum()),
            "adjacent_pairs": {"real": real_pairs,
                               "null_mean": round(statistics.mean(null_pairs), 2) if null_pairs else 0.0,
                               "p": round((1 + sum(1 for c in null_pairs if c >= real_pairs)) / float(k + 1), 4)},
            "untestable": largest_relevant_group(flags, left, right) < MODULE_MIN_RELEVANT,
            "pass": bool(passed), "why": ""}
    if not passed:
        # "gene" when every measured node of the walked graph is one; a job
        # with compounds or miRNAs on it reads "feature", the app's own word.
        noun = "gene" if all((graph.nodes.get(v) or {}).get("kind") == "gene" for v in nodes) else "feature"
        gate["why"] = _why(gate, noun)
    return gate
