"""The walker's six tools on the OpenAI Agents SDK, and the loop that runs a
model over them. Imported only when a model walks; the engine and the
scripted policies never touch the SDK.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from agents import (Agent, MaxTurnsExceeded, ModelSettings, RunContextWrapper, Runner, ToolsToFinalOutputResult,
                    function_tool)

from src.classes.AIInterpret import llm_client
from src.classes.AIInterpret.agent import _model
from src.classes.AIInterpret.walker.errors import tool_failure
from src.classes.AIInterpret.walker.walk import Walker

logger = logging.getLogger(__name__)

# The most tokens one walker or planner turn may produce. A turn is a tool
# call with a one-sentence reading; one run streamed 1.1 million characters
# of a single answer for half an hour and took no step.
TURN_MAX_TOKENS = 1500


def tool_turns(temperature, max_tokens=TURN_MAX_TOKENS):
    """Settings for an agent every turn of which is a tool call. Left to
    "auto", DeepSeek-V4-Flash writes its reasoning as answer text before the
    call; on a real brief that ran past the turn's token cap -- 11 of 29
    planner and walker turns and 6 of 19 Writer turns on the STATegra 5-omics
    walk of 2026-10-26 -- so the call never came, and the SDK took the cut-off
    text for the agent's final answer: a walker seed ended "no tool call", a
    Writer was sent to its finish having searched and read nothing. The
    reasoning a turn needs goes in the tools' own arguments (reading, reason,
    the statements). Pair it with reset_tool_choice=False, or the SDK drops the
    requirement after the first call, and with finished_when."""
    return ModelSettings(temperature=temperature, max_tokens=max_tokens, tool_choice="required")


def finished_when(done):
    """The loop's end for a tool_turns agent, which can no longer end it with
    text: after the tool call that leaves ``done(run_context)`` true."""
    def behaviour(ctx, results):
        if not done(ctx.context):
            return ToolsToFinalOutputResult(is_final_output=False, final_output=None)
        return ToolsToFinalOutputResult(is_final_output=True, final_output=str(results[-1].output))
    return behaviour


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
2. plan(seeds, steps, reason): choose the seed candidates that tell distinct biological stories, in the order they should be reported, and the total steps. A candidate whose values already differ at the baseline is a baseline difference, not a response; say so in the reason if you skip it. When the brief names an ANCHOR (the gene the experiment perturbed), the scan shows each candidate's distance from it (d=): make the nearest candidates the first seeds, so the walk starts where the perturbation acts and moves outward.
3. Your turn ends when plan answers "plan set". Several walkers then walk your seeds at the same time, one each."""

SEGMENT_INSTRUCTIONS = """You walk ONE seed's neighbourhood in an Agentic Graph Walk over a biological graph with a user's multi-omics data laid over it. Other walkers walk the other seeds at the same time: an edge they walked shows as closed, a node they read as walked.

""" + _READING_RULES + """

1. Move with step (one edge, either direction of the arrow). Before every move give a reading: one sentence stating what the values of the node you go to SHOW, with the numbers -- direction, timing, which layers agree, whether a difference is already there at the baseline. A layer the card lists as unlabeled has columns c1..cN with no time or order: read its direction and size, not its timing. Name the layer and quote its numbers; every answer shows the layers of the relevant neighbours, so read them before you step. "Check whether..." is not a reading.
2. Read the seed's relevant neighbours first, then follow what the values say. When the brief names an ANCHOR, every neighbour shows its distance from it (d=): between neighbours of similar heat take the nearer one, and do not wander away from the perturbation for a marginally hotter node. jump back to a node you walked when a branch is exhausted; code refuses a jump while a relevant unvisited neighbour is still open.
3. Use scan(scope="here", radius=2) when the neighbours are few or you want to see what is hot two steps out.
4. note what the Writer should not miss. Use your steps; stop when the neighbourhood is read and what is left repeats what you have.

Rules code enforces: only a listed neighbour; an edge closes per direction; the reading must name a layer of the node; budgets are counters, a refusal costs nothing."""


def anchor_line(walker):
    """One line on the perturbed gene for the briefs, or a blank line."""
    anchor = walker.anchor
    if not anchor:
        return ""
    if not anchor.get("in_graph"):
        return "\nANCHOR: the design perturbs %s (%s), which has no edges in this graph; read every change as after that perturbation.\n" % (
            anchor["gene"], anchor.get("direction", "unknown"))
    here = walker.dist.get(walker.current) if walker.dist and walker.current else None
    return ("\nANCHOR: the design perturbs %s (%s); it is a node of this graph%s. Distances (d=) count steps from it; "
            "relate what you read to that perturbation and its direction.\n" % (
                anchor["gene"], anchor.get("direction", "unknown"),
                "" if here is None else ", %d step(s) from where you stand" % here))


TURNS_SPENT = "turns spent"
CONTEXT_FULL = "the model's context window was full"


async def _run_loop(agent, walker, prompt, max_turns):
    """Run the loop; why it ended without the model ending it (TURNS_SPENT,
    CONTEXT_FULL), or None."""
    ctx = WalkContext(walker=walker, card="")
    try:
        await Runner.run(agent, prompt, context=ctx, max_turns=max_turns)
    except asyncio.CancelledError:
        raise
    except MaxTurnsExceeded:
        # Every turn is a tool call, so a model that never plans or never stops
        # runs out of turns: the planner's code plan and the segment's stop
        # follow, as for a model that stopped. Not a gateway that failed.
        logger.info("[walker] %s used its %d turns", agent.name, max_turns)
        return TURNS_SPENT
    except Exception as exc:                                          # noqa: BLE001
        if llm_client.context_window_exceeded(exc):
            # The last resort behind overlay.MEMBERS_SHOWN: a turn whose
            # prompt outgrew the model's window ends the loop as its turn
            # limit does, keeping the legs already walked. As a loop error it
            # failed the whole walk of job y712svrXTf (2026-10-29), and no
            # retry of the same prompt could have fitted.
            logger.warning("[walker] %s ended on the context window: %s", agent.name, str(exc)[:300])
            return CONTEXT_FULL
        logger.warning("[walker] the model loop ended early: %s", exc)
        # Kept so the service can tell a gateway that failed from a model that
        # chose to stop: the first is an error the user can retry.
        walker.loop_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return None


async def run_plan_async(walker, card_text, max_turns=8, model=None, temperature=0.2):
    """The planner: scan the graph and fix the seeds and the steps. Leaves
    walker.plan None when the model never planned."""
    agent = Agent[WalkContext](name="Planner", model=model or _model(), instructions=PLANNER_INSTRUCTIONS,
                               model_settings=tool_turns(temperature), tools=PLANNER_TOOLS,
                               reset_tool_choice=False,
                               tool_use_behavior=finished_when(lambda c: c.walker.plan is not None or c.walker.done))
    kickoff = ("DESIGN CARD\n%s\n%s\nGraph: %s. At most %d seeds; at least %d steps per seed; ceiling %d steps.\n"
               "Begin with scan(scope=\"graph\")." % (
                   card_text, anchor_line(walker), walker.scope, walker.params["max_seeds"],
                   walker.params.get("steps_per_seed", 1), walker.params["ceiling"]))
    await _run_loop(agent, walker, kickoff, max_turns)
    return walker


async def run_segment_async(walker, card_text, opening, others, max_turns, model=None, temperature=0.2):
    """One seed's walker, already standing on its seed (``opening`` is what
    start_at answered). Stopped by code when the model does not stop."""
    agent = Agent[WalkContext](name="Walker", model=model or _model(), instructions=SEGMENT_INSTRUCTIONS,
                               model_settings=tool_turns(temperature), tools=SEGMENT_TOOLS,
                               reset_tool_choice=False, tool_use_behavior=finished_when(lambda c: c.walker.done))
    kickoff = ("DESIGN CARD\n%s\n%s\nGraph: %s. You walk from %s; %s.\nOther walkers, at the same time: %s.\n\n"
               "WHERE YOU STAND\n%s" % (card_text, anchor_line(walker), walker.scope, walker.label(walker.current),
                                         walker._budget_line(), others or "none", opening))
    ended = await _run_loop(agent, walker, kickoff, max_turns)
    if not walker.done:
        walker.stop("the model stopped calling tools", "loop ended without stop: %s"
                    % (ended or (TURNS_SPENT if len(walker.turns) >= max_turns - 1 else "no tool call")))
    return walker
