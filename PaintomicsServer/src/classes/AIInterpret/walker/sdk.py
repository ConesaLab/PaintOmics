"""The walker's six tools on the OpenAI Agents SDK, and the loop that runs a
model over them. Imported only when a model walks; the engine and the
scripted policies never touch the SDK.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool

from src.classes.AIInterpret.agent import _model
from src.classes.AIInterpret.walker.errors import tool_failure
from src.classes.AIInterpret.walker.walk import Walker

logger = logging.getLogger(__name__)


@dataclass
class WalkContext:
    walker: Walker
    card: str


def _fail(name):
    return tool_failure("walker", name, "Try again with different arguments.")


@function_tool(name_override="scan", failure_error_function=_fail("scan"))
async def scan(ctx: RunContextWrapper[WalkContext], scope: str, radius: int = 2) -> str:
    """The one scoring tool. scope="graph": every measured node of this graph ranked by heat (how surprising its neighbourhood's relevant count is) with the seed candidates flagged; call it first. scope="here" with radius 1-3: the measured nodes within that many steps of where you stand, hottest first, with distance and first hop. Same statistic, different scope. Free."""
    return ctx.context.walker.scan(scope, radius)


@function_tool(name_override="plan", failure_error_function=_fail("plan"))
async def plan(ctx: RunContextWrapper[WalkContext], seeds: list[str], steps: int, reason: str) -> str:
    """Fix your seeds (names from the graph scan's candidates, in the order you will visit them), the number of steps you want (3 up to the ceiling), and why. Called once. One jump per seed and one note per two steps follow. Places you on the first seed and shows its layers and neighbours."""
    return ctx.context.walker.plan_walk(seeds, steps, reason)


@function_tool(name_override="step", failure_error_function=_fail("step"))
async def step(ctx: RunContextWrapper[WalkContext], to: str, reading: str, reason: str) -> str:
    """Move one edge to a listed neighbour, with or against the arrow. reading: one sentence on what the node's values say in the design card's terms (name the layer). reason: why you go there. Closes that edge in that direction. Answers with the node's layers and its neighbours."""
    return ctx.context.walker.step(to, reading, reason)


@function_tool(name_override="jump", failure_error_function=_fail("jump"))
async def jump(ctx: RunContextWrapper[WalkContext], to: str, reading: str, reason: str) -> str:
    """Teleport to an unvisited seed or to a node already on the chain; a leg without an edge."""
    return ctx.context.walker.jump(to, reading, reason)


@function_tool(name_override="note", failure_error_function=_fail("note"))
async def note(ctx: RunContextWrapper[WalkContext], text: str) -> str:
    """Record an observation pinned to the last leg (at most 400 characters). It reaches the Writer; it is never evidence."""
    return ctx.context.walker.note(text)


@function_tool(name_override="stop", failure_error_function=_fail("stop"))
async def stop(ctx: RunContextWrapper[WalkContext], reading: str, reason: str) -> str:
    """End the walk with a final reading and the reason. The only way to finish; always legal."""
    return ctx.context.walker.stop(reading, reason)


PLANNER_TOOLS = [scan, plan]
SEGMENT_TOOLS = [scan, step, jump, note, stop]

_READING_RULES = """Every node carries layers: the user's values as text with their own column labels, and a flag saying whether the user marked that layer relevant. Heat is how surprising a node's neighbourhood's relevant count is given its size. You never compute on values; you read them against the design card.

Describe what you find as a biologist would: name the genes and proteins, or their biological role ("Itpr1, Itpr2 and Itpr3", "the IP3 receptors"). Never call a set of genes a cluster; readings, reasons and notes reach the Writers and the reader."""

PLANNER_INSTRUCTIONS = """You plan an Agentic Graph Walk over a biological graph with a user's multi-omics data laid over it.

""" + _READING_RULES + """

1. scan(scope="graph").
2. plan(seeds, steps, reason): choose the seed candidates that tell distinct biological stories, in the order they should be reported, and the total steps. A candidate whose values already differ at the baseline is a baseline difference, not a response; say so in the reason if you skip it.
3. After plan answers "plan set", reply with one sentence and stop calling tools. Several walkers then walk your seeds at the same time, one each."""

SEGMENT_INSTRUCTIONS = """You walk ONE seed's neighbourhood in an Agentic Graph Walk over a biological graph with a user's multi-omics data laid over it. Other walkers walk the other seeds at the same time: an edge they walked shows as closed, a node they read as walked.

""" + _READING_RULES + """

1. Move with step (one edge, either direction of the arrow). Before every move give a reading: one sentence stating what the values of the node you go to SHOW, with the numbers -- direction, timing, which layers agree, whether a difference is already there at the baseline. A layer the card lists as unlabeled has columns c1..cN with no time or order: read its direction and size, not its timing. Name the layer and quote its numbers; every answer shows the layers of the relevant neighbours, so read them before you step. "Check whether..." is not a reading.
2. Read the seed's relevant neighbours first, then follow what the values say. jump back to a node you walked when a branch is exhausted; code refuses a jump while a relevant unvisited neighbour is still open.
3. Use scan(scope="here", radius=2) when the neighbours are few or you want to see what is hot two steps out.
4. note what the Writer should not miss. Use your steps; stop when the neighbourhood is read and what is left repeats what you have.

Rules code enforces: only a listed neighbour; an edge closes per direction; the reading must name a layer of the node; budgets are counters, a refusal costs nothing."""


async def _run_loop(agent, walker, prompt, max_turns):
    ctx = WalkContext(walker=walker, card="")
    try:
        await Runner.run(agent, prompt, context=ctx, max_turns=max_turns)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[walker] the model loop ended early: %s", exc)
        # Kept so the service can tell a gateway that failed from a model that
        # chose to stop: the first is an error the user can retry.
        walker.loop_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])


async def run_plan_async(walker, card_text, max_turns=8, model=None, temperature=0.2):
    """The planner: scan the graph and fix the seeds and the steps. Leaves
    walker.plan None when the model never planned."""
    agent = Agent[WalkContext](name="Planner", model=model or _model(), instructions=PLANNER_INSTRUCTIONS,
                               model_settings=ModelSettings(temperature=temperature), tools=PLANNER_TOOLS)
    kickoff = ("DESIGN CARD\n%s\n\nGraph: %s. At most %d seeds; at least %d steps per seed; ceiling %d steps.\n"
               "Begin with scan(scope=\"graph\")." % (
                   card_text, walker.scope, walker.params["max_seeds"], walker.params.get("steps_per_seed", 1),
                   walker.params["ceiling"]))
    await _run_loop(agent, walker, kickoff, max_turns)
    return walker


async def run_segment_async(walker, card_text, opening, others, max_turns, model=None, temperature=0.2):
    """One seed's walker, already standing on its seed (``opening`` is what
    start_at answered). Stopped by code when the model does not stop."""
    agent = Agent[WalkContext](name="Walker", model=model or _model(), instructions=SEGMENT_INSTRUCTIONS,
                               model_settings=ModelSettings(temperature=temperature), tools=SEGMENT_TOOLS)
    kickoff = ("DESIGN CARD\n%s\n\nGraph: %s. You walk from %s; %s.\nOther walkers, at the same time: %s.\n\n"
               "WHERE YOU STAND\n%s" % (card_text, walker.scope, walker.label(walker.current),
                                         walker._budget_line(), others or "none", opening))
    await _run_loop(agent, walker, kickoff, max_turns)
    if not walker.done:
        walker.stop("the model stopped calling tools", "loop ended without stop: %s"
                    % ("turns spent" if len(walker.turns) >= max_turns - 1 else "no tool call"))
    return walker
