"""The model walk with agents in parallel, inside one time budget.

One interpretation used to be one walker agent followed by one Writer: 22
legs, five statements and five papers in about four minutes, one call at a
time. Here the same rules run as a team:

  1. a planner scans the graph and fixes the seeds and the steps;
  2. one walker per seed walks that seed's neighbourhood, several at once,
     sharing the set of walked edges and read nodes, so no two walk the same
     edge and each sees what the others already read;
  3. the segments are merged into one numbered chain (merge_segments), every
     leg still one a walker's tools accepted;
  4. the chain is split into parts (writer_parts) and one Writer per part
     writes its statements, several at once, over one shared paper list, and
     every citation is confirmed by a paper agent reading the paper
     (walker/literature.py).

Every stage has a deadline. A walker past it is stopped at its next turn; a
Writer past it is cancelled and keeps what passed on its last submission. The
sense check and the Narrator run after this module returns (service.run).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import time
from collections import OrderedDict

from src.classes.AIInterpret.walker.walk import Leg, Walker

logger = logging.getLogger(__name__)

# Sizes and seconds per scope. The network walk is the job's interpretation:
# about 20 cited papers and a Results section of 800-2,000 words inside ten
# minutes. A pathway walk is a smaller map, so it asks for less. "walkers",
# "writers" and "papers" are how many run at once: the gateway's key is paced
# at about 60 requests a minute, and a model turn takes about 3 s.
PLANS = {
    "network": {"max_seeds": 10, "seed_steps": (5, 8), "walkers": 4, "walk_seconds": 200,
                "parts": 6, "writers": 4, "statements": (3, 5), "citations": 24,
                "writer_seconds": 250, "papers": 6, "words": (800, 2000), "run_seconds": 600},
    "pathway": {"max_seeds": 5, "seed_steps": (3, 6), "walkers": 4, "walk_seconds": 150,
                "parts": 4, "writers": 4, "statements": (2, 4), "citations": 12,
                "writer_seconds": 200, "papers": 6, "words": (400, 1200), "run_seconds": 480},
}
PLAN_TURNS = 8
WRITER_TURNS = 30
MIN_PART_LEGS = 4
POLL_SECONDS = 2.0


def plan_for(tag):
    return dict(PLANS["network" if tag == "network" else "pathway"])


def per_seed_steps(walker, plan):
    lo, hi = plan["seed_steps"]
    seeds = max(1, len(walker.plan["seeds"]))
    return max(lo, min(hi, int(walker.plan["steps"]) // seeds))


def code_plan(walker, plan, why):
    """The planner's fallback when the model answered but never planned: the
    hottest candidates, the most steps the plan allows."""
    if walker.ranked is None:
        walker.scan("graph")
    candidates = [r["id"] for r in walker.ranked if r["candidate"]][:walker.params["max_seeds"]]
    if not candidates:
        return None
    steps = min(walker.params["ceiling"], plan["seed_steps"][1] * len(candidates))
    steps = max(steps, walker.params.get("steps_per_seed", 1) * len(candidates), walker.params["min_steps"])
    return walker.plan_walk(candidates, min(steps, walker.params["ceiling"]), why)


def merge_segments(planner, segments):
    """One Walker holding the whole walk: the planner's scan and plan, then
    each segment's legs in seed order, joined by a jump leg to the next seed,
    renumbered, with notes and the seen list moved onto the new numbers. Every
    step leg is one a segment walker's tools accepted, and the segments shared
    the closed edges, so no edge appears twice in one direction."""
    merged = Walker(planner.network, planner.overlay, planner.scope, dict(planner.params))
    merged.ranked, merged.plan = planner.ranked, planner.plan
    merged.turns = [dict(t) for t in planner.turns]
    merged.scans, merged.refusals = planner.scans, planner.refusals
    merged.seen = OrderedDict((node, list(after)) for node, after in planner.seen.items())
    merged.visited, merged.closed = set(planner.visited), set(planner.closed)
    merged.current = planner.current
    merged.loop_error = planner.loop_error
    stops, readings, errors = [], [], []
    for sub in segments:
        seed = sub.plan["seeds"][0] if sub.plan else None
        base = len(merged.turns)
        merged.turns.extend([dict(t, t=base + i, seed=seed) for i, t in enumerate(sub.turns)])
        merged.scans += sub.scans
        merged.refusals += sub.refusals
        merged.visited |= sub.visited
        merged.closed |= sub.closed
        if sub.loop_error:
            errors.append(sub.loop_error)
        if sub.stop_reason and not sub.chain and seed is not None:
            stops.append("%s: %s" % (merged.label(seed), sub.stop_reason))
        if not sub.chain:
            continue
        if merged.chain and merged.current != seed:
            merged.chain.append(Leg(len(merged.chain) + 1, "jump", merged.current, seed,
                                    "Next planned seed: %s." % merged.label(seed),
                                    (planner.plan or {}).get("reason") or "the next seed in the plan"))
        offset = len(merged.chain)
        for leg in sub.chain:
            merged.chain.append(Leg(offset + leg.n, leg.kind, leg.src, leg.dst, leg.reading, leg.reason, leg.edge))
        for note in sub.notes:
            merged.notes.append(dict(note, after_leg=offset + note["after_leg"]))
        for node, after in sub.seen.items():
            merged.seen.setdefault(node, []).extend(offset + a for a in after)
        merged.segments.append({"seed": seed, "label": merged.label(seed), "first": offset + 1,
                                "last": len(merged.chain), "stop": sub.stop_reason})
        merged.current = merged.chain[-1].dst
        if sub.stop_reason:
            stops.append("%s: %s" % (merged.label(seed), sub.stop_reason))
        if sub.stop_reading:
            readings.append(sub.stop_reading)
    steps = sum(1 for leg in merged.chain if leg.kind == "step")
    planned = int((planner.plan or {}).get("steps") or steps)
    merged.budget = {"steps": max(0, planned - steps), "jumps": 0, "notes": 0}
    merged.steps_here = 0
    merged.done = all(sub.done for sub in segments) if segments else planner.done
    merged.stop_reason = "; ".join(stops)[:600] or planner.stop_reason
    merged.stop_reading = " ".join(readings)[:1200] or planner.stop_reading
    if not merged.chain and errors and not merged.loop_error:
        merged.loop_error = errors[0]
    return merged


def writer_parts(segments, n_legs, parts, min_legs=MIN_PART_LEGS):
    """Contiguous leg ranges, one per Writer, covering legs 1..n_legs. Each
    segment owns its legs and the jump leg that leads into it; segments are
    grouped in order until a part holds about n_legs / parts legs (at least
    ``min_legs``), and while there are more than ``parts`` ranges the adjacent
    pair with the fewest legs together is joined."""
    if n_legs <= 0:
        return []
    ranges, lo = [], 1
    for segment in segments:
        if segment["last"] >= lo:
            ranges.append((lo, segment["last"]))
            lo = segment["last"] + 1
    if lo <= n_legs:
        if ranges:
            ranges[-1] = (ranges[-1][0], n_legs)
        else:
            ranges.append((lo, n_legs))
    target = max(min_legs, int(math.ceil(n_legs / float(max(1, parts)))))
    # a seed that walked far is more than one Writer's share: cut it in pieces
    pieces = []
    for first, last in ranges:
        size = last - first + 1
        chunks = max(1, int(round(size / float(target)))) if size > 1.5 * target else 1
        step = int(math.ceil(size / float(chunks)))
        pieces.extend((a, min(last, a + step - 1)) for a in range(first, last + 1, step))
    out = []
    for first, last in pieces:
        if out and out[-1][1] - out[-1][0] + 1 < target:
            out[-1] = (out[-1][0], last)
        else:
            out.append((first, last))
    if len(out) > 1 and out[-1][1] - out[-1][0] + 1 < min_legs:
        out[-2:] = [(out[-2][0], out[-1][1])]
    while len(out) > max(1, parts):
        i = min(range(len(out) - 1), key=lambda k: out[k + 1][1] - out[k][0])
        out[i:i + 2] = [(out[i][0], out[i + 1][1])]
    return out


def run_pipeline(coro):
    """asyncio.run, except that it does not wait for worker threads.

    The paper agents and the PubMed fetches run on threads (asyncio.to_thread).
    A stage past its deadline cancels the coroutines waiting on them, but a
    thread cannot be cancelled, and asyncio.run's cleanup waits for every one
    to finish: on 2026-09-15 a gateway retry sleeping on one of them returned
    the pipeline 54 s after the Writers' deadline, and the Narrator's time was
    gone. Here the loop gets its own executor, which is shut down without
    waiting; a leftover thread ends on its own and its answer goes nowhere.
    """
    loop = asyncio.new_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(thread_name_prefix="walk")
    loop.set_default_executor(executor)
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            left = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in left:
                task.cancel()
            if left:
                loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            executor.shutdown(wait=False)
            asyncio.set_event_loop(None)
            loop.close()


async def _wait(tasks, deadline, cancelled):
    """Wait for tasks until they finish, the deadline passes or the walk is
    cancelled; cancel what is left and let it unwind."""
    pending = set(tasks)
    while pending:
        if (cancelled is not None and cancelled()) or time.time() >= deadline:
            break
        done, pending = await asyncio.wait(pending, timeout=min(POLL_SECONDS, max(0.1, deadline - time.time())))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return [t for t in tasks if t.done() and not t.cancelled() and t.exception() is None]


async def walk_in_parallel(planner, card_text, plan, deadline, cancelled=None, on_progress=None,
                           runner=None):
    """Stages 1-3. Returns the merged Walker. ``runner`` replaces the model
    loops in tests: an object with ``plan(walker)`` and ``segment(walker,
    opening, others, max_turns)`` coroutines."""
    if runner is None:
        from src.classes.AIInterpret.walker import sdk
        runner = _ModelRunner(sdk, card_text)
    await runner.plan(planner)
    if planner.plan is None:
        if planner.loop_error:
            return merge_segments(planner, [])
        code_plan(planner, plan, "planned by code: the model did not call plan")
        if planner.plan is None:
            planner.stop("no seed candidate: nothing relevant with a measured neighbour", "no seed candidate")
            return merge_segments(planner, [])
    seeds = list(planner.plan["seeds"])
    steps = per_seed_steps(planner, plan)
    closed, visited = set(planner.closed), set(planner.visited)
    segments = []
    for _seed in seeds:
        sub = Walker(planner.network, planner.overlay, planner.scope, dict(planner.params))
        sub.closed, sub.visited = closed, visited
        segments.append(sub)
    slots = asyncio.Semaphore(plan["walkers"])

    def on_turn(w):
        if not w.done and ((cancelled is not None and cancelled()) or time.time() >= deadline):
            w.stop("", "stopped: the walk's time budget is spent" if time.time() >= deadline
                   else "cancelled: the job was deleted")
            return
        if on_progress is not None:
            on_progress(merge_segments(planner, segments))

    async def walk_one(i):
        sub = segments[i]
        async with slots:
            opening = sub.start_at(seeds[i], steps, (planner.plan or {}).get("reason", ""))
            if time.time() >= deadline or (cancelled is not None and cancelled()):
                sub.stop("", "stopped before walking: the walk's time budget is spent")
                return
            sub.on_turn = on_turn
            others = ", ".join(planner.label(s) for j, s in enumerate(seeds) if j != i)
            try:
                await runner.segment(sub, opening, others, steps * 3 + 8)
            finally:
                sub.on_turn = None
                if not sub.done:
                    sub.stop("", "stopped: the walk's time budget is spent")

    tasks = [asyncio.ensure_future(walk_one(i)) for i in range(len(seeds))]
    await _wait(tasks, deadline, cancelled)
    for sub in segments:
        if not sub.done:
            sub.stop("", "stopped: the walk's time budget is spent" if sub.plan is not None
                     else "not walked: the walk's time budget was spent before this seed's turn")
    return merge_segments(planner, segments)


class _ModelRunner:
    def __init__(self, sdk, card_text):
        self.sdk, self.card = sdk, card_text

    async def plan(self, walker):
        await self.sdk.run_plan_async(walker, self.card, max_turns=PLAN_TURNS)

    async def segment(self, walker, opening, others, max_turns):
        await self.sdk.run_segment_async(walker, self.card, opening, others, max_turns)


async def write_in_parallel(walker, card_text, pubmed, client, plan, deadline, cancelled=None,
                            on_progress=None, write_one=None):
    """Stage 4. Returns (kept, dropped, store, contexts): the statements of
    every part in walk order, numbered 1..n across the walk. ``write_one``
    replaces the model Writer in tests: a coroutine taking a WriterContext."""
    from src.classes.AIInterpret.walker import literature
    from src.classes.AIInterpret.walker import writer as writer_mod

    n_legs = len(walker.chain)
    parts = writer_parts(walker.segments, n_legs, plan["parts"])
    store = literature.LiteratureStore()
    slots = asyncio.Semaphore(plan["writers"])
    paper_slots = asyncio.Semaphore(plan["papers"])
    per_part = max(1, int(math.ceil(plan["citations"] / float(max(1, len(parts))))))
    contexts = [writer_mod.WriterContext(walker=walker, card=card_text, pubmed=pubmed, store=store,
                                         client=client, legs=part, count=plan["statements"],
                                         citations=per_part, paper_slots=paper_slots, deadline=deadline)
                for part in parts]
    finished = {"n": 0}

    def others_for(i):
        lines = []
        for j, (lo, hi) in enumerate(parts):
            if j != i:
                seeds = [s["label"] for s in walker.segments if lo <= s["first"] <= hi]
                lines.append("e%d to e%d: %s" % (lo, hi, ", ".join(seeds) or "the walk"))
        return "\n".join(lines)

    async def run_one(i):
        async with slots:
            if time.time() >= deadline or (cancelled is not None and cancelled()):
                return
            if write_one is not None:
                await write_one(contexts[i])
            else:
                await writer_mod.run_writer_async(contexts[i], others_for(i), max_turns=WRITER_TURNS)
        finished["n"] += 1
        if on_progress is not None:
            on_progress("Writing statements: %d of %d parts written, %d papers checked"
                        % (finished["n"], len(parts), store.checks))

    tasks = [asyncio.ensure_future(run_one(i)) for i in range(len(parts))]
    await _wait(tasks, deadline, cancelled)
    kept, dropped = [], []
    for c in contexts:
        if c.done:
            kept.extend(c.kept)
            dropped.extend(c.dropped)
        else:
            # Stopped by the deadline, the turn limit or an error: what passed
            # on its latest submission is as checked as an accepted one.
            kept.extend(c.last_passing)
    for n, stmt in enumerate(kept, 1):
        stmt["n"] = n
    for n, stmt in enumerate(dropped, len(kept) + 1):
        stmt["n"] = n
    return kept, dropped, store, contexts
