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


WALKER_TOOLS = [scan, plan, step, jump, note, stop]

INSTRUCTIONS = """You are the walker of an Agentic Graph Walk over a biological graph with a user's multi-omics data laid over it.

Every node carries layers: the user's values as text with their own column labels, and a flag saying whether the user marked that layer relevant. Heat is how surprising a node's neighbourhood's relevant count is given its size. You never compute on values; you read them against the design card.

Procedure:
1. scan(scope="graph"), then plan(seeds, steps, reason): choose which seed candidates tell distinct stories and how many steps you need. A candidate whose values already differ at the baseline is a baseline difference, not a response; say so in the reason if you skip it.
2. Move with step (one edge, either direction of the arrow). Before every move give a reading: one sentence stating what the values of the node you go to SHOW, with the numbers -- direction, timing, which layers agree, whether a difference is already there at the baseline. A layer the card lists as unlabeled has columns c1..cN with no time or order: read its direction and size, not its timing. Name the layer and quote its numbers; every answer shows the layers of the relevant neighbours, so read them before you step. "Check whether..." is not a reading.
3. Walk from a seed before leaving it: step to its relevant neighbours and read them. jump (to an unvisited seed or a chain node) only when the neighbourhood is exhausted; code refuses a jump while a relevant unvisited neighbour is still open. Use the steps you planned.
4. Use scan(scope="here", radius=2) when the neighbours are few or you want to see what is hot two steps out.
5. note what the Writer should not miss. stop when every chosen seed has been read and what is left repeats what the chain shows.

Rules code enforces: only a listed neighbour; an edge closes per direction; the reading must name a layer of the node; budgets are counters, a refusal costs nothing. A node that is hot but not relevant can be walked to; its heat comes from its neighbours.
"""


async def run_walk_async(walker, card_text, max_turns=60, model=None, temperature=0.2):
    ctx = WalkContext(walker=walker, card=card_text)
    agent = Agent[WalkContext](name="Walker", model=model or _model(), instructions=INSTRUCTIONS,
                               model_settings=ModelSettings(temperature=temperature),
                               tools=WALKER_TOOLS)
    kickoff = ("DESIGN CARD\n%s\n\nGraph: %s. Ceiling %d steps; at most %d seeds; at least %d steps per seed.\n"
               "Begin with scan(scope=\"graph\")." % (
                   card_text, walker.scope, walker.params["ceiling"], walker.params["max_seeds"],
                   walker.params.get("steps_per_seed", 1)))
    try:
        await Runner.run(agent, kickoff, context=ctx, max_turns=max_turns)
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[walker] the model loop ended early: %s", exc)
        # Kept so the service can tell a gateway that failed from a model that
        # chose to stop: the first is an error the user can retry.
        walker.loop_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    if not walker.done:
        walker.stop("the model stopped calling tools", "loop ended without stop: %s"
                    % ("turns spent" if len(walker.turns) >= max_turns - 1 else "no tool call"))
    return walker


def run_walk(walker, card_text, max_turns=60, model=None):
    return asyncio.run(run_walk_async(walker, card_text, max_turns, model))
