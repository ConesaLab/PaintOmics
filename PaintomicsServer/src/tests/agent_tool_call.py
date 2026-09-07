#!/usr/bin/env python3
"""Invoke a `@function_tool` the way the Agents SDK invokes it.

`FunctionTool.on_invoke_tool` has always been typed to take a `ToolContext`,
not the plain `RunContextWrapper` a tool body reads through `ctx.context`.
openai-agents 0.8.4 never actually read a ToolContext-only field -- the tool
name came from a closed-over `schema.name` -- so passing the wrapper worked by
accident, and six suites did. 0.22.0 reads `ctx.tool_name` on the first line of
`_on_invoke_tool_impl`, and `ctx.run_config` in the handled-error reporter that
`failure_error_function` goes through, so the wrong type now raises
`AttributeError: 'RunContextWrapper' object has no attribute 'tool_name'`
before the tool body runs. That was a defect in the fixtures, not an
incompatibility in the SDK: it made six suites fail at once for one reason.

Two things here are deliberate, because the obvious versions of both break:

  * `ToolContext` is built through the SDK's own `from_agent_context` factory
    rather than field by field. It is a dataclass whose required fields grew
    between the two versions (0.22 added `_tool_invocations`,
    `_restored_unbound_approval_call_ids` and `tool_namespace`), so naming them
    here would pin the fixture to one release.
  * The name and the arguments are carried in a `ResponseFunctionToolCall`
    rather than passed as keywords. `from_agent_context` grew `tool_name` and
    `tool_arguments` keywords only in 0.22; the `tool_call` parameter it reads
    them off is positional on both, and is what the runner itself passes.

Usage:
    from src.tests.agent_tool_call import invokeTool
    result = invokeTool(L.get_experiment_overview, ctx, pathway_id="mmu04910")
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from agents import RunContextWrapper                                # noqa: E402
from agents.tool_context import ToolContext                         # noqa: E402
from openai.types.responses import ResponseFunctionToolCall         # noqa: E402


def toolContext(context, tool=None, arguments="{}", toolCallId="call_test"):
    """The context object the SDK hands `tool.on_invoke_tool`.

    `context` is the plain object the tool body reads as `ctx.context` (a
    LoopContext or AgentContext here). `tool` supplies the name that the
    SDK's failure reporting reads back as `ctx.tool_name`.
    """
    call = ResponseFunctionToolCall(
        name=getattr(tool, "name", None) or "tool_under_test",
        arguments=arguments,
        call_id=toolCallId,
        type="function_call",
    )
    return ToolContext.from_agent_context(
        RunContextWrapper(context=context), toolCallId, call)


def invokeTool(tool, context, **arguments):
    """Call `tool` with `arguments` and return its string result.

    Mirrors what the runner does: JSON-encode the arguments, hand the tool a
    ToolContext carrying the same name and arguments, and drive the coroutine
    to completion. Each call gets its own event loop so a suite can invoke
    tools from ordinary synchronous tests.
    """
    encoded = json.dumps(arguments)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            tool.on_invoke_tool(toolContext(context, tool, encoded), encoded))
    finally:
        loop.close()
