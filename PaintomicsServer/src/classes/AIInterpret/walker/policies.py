"""Scripted policies that drive the same six tools with no model.

``greedy``: the Test 1 walker and the ablation baseline -- the hottest relevant
unvisited neighbour, ``steps // seeds`` moves per seed, then the next seed.
``random``: a random legal step, for the ablation's floor.
"""
from __future__ import annotations

import random


def _pick_greedy(rows):
    for row in rows:
        if row["r"] == 1 and row["open"] and not row["visited"]:
            return row
    return None


def greedy(walker, seeds=None, steps=None, per_seed=None):
    """Run a whole walk. ``seeds`` and ``steps`` default to every candidate
    (up to max_seeds) and to 5 steps per seed within the ceiling."""
    walker.scan("graph")
    candidates = [r["id"] for r in walker.ranked if r["candidate"]]
    seeds = list(seeds or candidates[:walker.params["max_seeds"]])
    if not seeds:
        walker.stop("no seed candidate: nothing relevant with a measured neighbour", "no seed candidate")
        return walker
    steps = int(steps or min(walker.params["ceiling"], max(walker.params["min_steps"], 5 * len(seeds))))
    walker.plan_walk(seeds, steps, "greedy policy: the hottest candidates, five steps each")
    per_seed = per_seed or max(1, steps // len(seeds))
    here = 0
    while not walker.done:
        pick = _pick_greedy(walker.neighbour_rows()) if here < per_seed else None
        if pick and walker.budget["steps"] > 0:
            walker.step(pick["id"], "greedy policy: %s" % walker.overlay.layer_text(pick["id"]).split("\n")[0],
                        "the hottest relevant unvisited neighbour (heat %.2f)" % pick["heat"])
            here += 1
            continue
        unvisited = [s for s in seeds if s not in walker.visited]
        if walker.budget["steps"] <= 0 or not unvisited or walker.budget["jumps"] <= 0:
            walker.stop("no relevant unvisited neighbour here and no unvisited seed left",
                        "greedy policy: every seed visited or the budget spent")
            break
        walker.jump(unvisited[0], "next seed", "greedy policy: %d steps spent from this seed"
                    % here if here >= per_seed else "no relevant unvisited neighbour here")
        here = 0
    return walker


def random_walk(walker, seeds=None, steps=None, rng=None):
    """A random legal step each turn, the same plan as greedy would take."""
    rng = rng or random.Random(0)
    walker.scan("graph")
    candidates = [r["id"] for r in walker.ranked if r["candidate"]]
    seeds = list(seeds or candidates[:walker.params["max_seeds"]])
    if not seeds:
        walker.stop("no seed candidate", "no seed candidate")
        return walker
    steps = int(steps or min(walker.params["ceiling"], max(walker.params["min_steps"], 5 * len(seeds))))
    walker.plan_walk(seeds, steps, "random policy")
    while not walker.done:
        rows = [r for r in walker.neighbour_rows() if r["open"] and r["r"] is not None]
        if rows and walker.budget["steps"] > 0:
            row = rng.choice(rows)
            walker.step(row["id"], "random policy: %s" % walker.overlay.layer_text(row["id"]).split("\n")[0],
                        "random legal step")
            continue
        unvisited = [s for s in seeds if s not in walker.visited]
        if walker.budget["steps"] <= 0 or not unvisited or walker.budget["jumps"] <= 0:
            walker.stop("nothing legal left", "random policy: budget spent")
            break
        walker.jump(unvisited[0], "next seed", "random policy: dead end")
    return walker
