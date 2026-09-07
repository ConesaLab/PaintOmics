#!/usr/bin/env python3
"""A tool that raises must not look like a tool nobody called.

The SDK catches any exception a tool raises, hands the model "An error occurred
while running the tool", and carries on. `_trace` runs at the END of each tool,
so a raise also means no trace event -- and in the archive a tool failing on
every call is indistinguishable from a tool the agent chose not to use. Every
adoption and cost figure measured over the 60 archived runs counts successful
calls only.

This was not theoretical: the first version of the delegation-cache tests passed
against a fixture that raised KeyError on every call, because the swallowed
error came back as an ordinary string.

Each tool now carries a failure_error_function that records the failure in the
run journal and tells the model what broke.

    python -m src.tests.test_tool_failures_are_visible
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# `_trace` archives to CLIENT_TMP_DIR/ai_traces, the corpus every
# tool-usefulness figure here is computed from -- servlet runs included.
# Redirected BEFORE agent_loop is imported: serverconf is read at import time.
import tempfile as _tempfile
from src.conf import serverconf as _serverconf
_serverconf.CLIENT_TMP_DIR = _tempfile.mkdtemp(prefix="tracetest-")

from src.tests.agent_tool_call import invokeTool                # noqa: E402
from src.classes.AIInterpret import agent_loop as L       # noqa: E402


def _isolate_trace_archive():
    """Keep test traces out of the real archive.

    _archive_trace writes every run to CLIENT_TMP/ai_traces, which is the corpus
    the tool-usage benchmark reads. Unit tests that drive real tools were writing
    there too -- five files, and six simulated faults that the analyzer then
    reported as a defect in get_experiment_overview.
    """
    import tempfile
    L._archive_trace = lambda ctx: None
    return tempfile.gettempdir()


_isolate_trace_archive()

_PASSED, _FAILED = [], []


def _context():
    c = L.LoopContext(job_instance=None, job_id="TEST0002",
                      organism_name="Mus musculus",
                      experiment_design="two conditions")
    c.started_at = time.time()
    c.hard_deadline = time.time() + 600
    return c


def _invoke(tool, ctx, **kwargs):
    return invokeTool(tool, ctx, **kwargs)


def _breaking(fn):
    """Run fn with _spend raising, which breaks every tool that returns text."""
    original = L._spend

    def boom(*_a, **_kw):
        raise RuntimeError("simulated tool fault")
    L._spend = boom
    try:
        return fn()
    finally:
        L._spend = original


def test_a_raising_tool_lands_in_the_trace():
    def body():
        c = _context()
        _invoke(L.get_experiment_overview, c)
        errors = [e for e in c.trace if str(e.get("result", "")).startswith("ERROR")]
        assert errors, ("a tool raised and left no trace event; the archive "
                        "would show the tool as never called: %r" % c.trace)
        assert errors[0]["tool"] == "get_experiment_overview", (
            "the failure was filed under the wrong tool: %r" % errors[0])
    _breaking(body)


def test_the_model_is_told_what_broke():
    def body():
        c = _context()
        out = str(_invoke(L.get_experiment_overview, c))
        assert "get_experiment_overview" in out, "the message does not name the tool"
        assert "simulated tool fault" in out, "the message hides the actual error"
        assert "same arguments" in out, "the model is not told to stop repeating it"
    _breaking(body)


def test_a_failure_still_counts_as_a_tool_call():
    """Otherwise the budget and the turn count drift from what really happened."""
    def body():
        c = _context()
        before = c.tool_calls
        _invoke(L.get_experiment_overview, c)
        assert c.tool_calls == before + 1, "the failed call was not counted"
    _breaking(body)


def test_every_tool_declares_a_failure_handler_naming_itself():
    """Guards against copy-paste drift: the handler is created per tool with the
    tool's own name, and a mismatch would file failures under the wrong tool."""
    src = inspect.getsource(L)
    tree = ast.parse(src)
    missing, mismatched = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call)
                    and getattr(dec.func, "id", "") == "function_tool"):
                if getattr(dec, "id", "") == "function_tool":
                    missing.append(node.name)
                continue
            kw = next((k for k in dec.keywords
                       if k.arg == "failure_error_function"), None)
            if kw is None:
                missing.append(node.name)
                continue
            named = (kw.value.args[0].value
                     if isinstance(kw.value, ast.Call) and kw.value.args else None)
            if named != node.name:
                mismatched.append((node.name, named))
    assert not missing, "tools with no failure handler: %s" % ", ".join(missing)
    assert not mismatched, ("handler named for the wrong tool: %s"
                            % ", ".join("%s -> %s" % m for m in mismatched))


def test_every_declared_tool_is_in_the_toolbelt():
    """The AST check above is worth exactly as many tools as it sees.

    It used to assert a count of ten, which turned a deliberate removal into a
    failure. What matters is that every @function_tool in the module is actually
    wired into TOOLBELT -- a tool defined and not registered is dead code the
    agent can never call, and one registered without a failure handler is the
    hole this suite exists to close.
    """
    import ast
    import inspect
    src = inspect.getsource(L)
    tree = ast.parse(src)
    declared = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if getattr(func, "id", "") == "function_tool":
                declared.add(node.name)
    registered = {t.name for t in L.TOOLBELT}
    assert declared == registered, (
        "declared but not in TOOLBELT: %s; in TOOLBELT but not declared here: %s"
        % (sorted(declared - registered), sorted(registered - declared)))



def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_a_raising_tool_lands_in_the_trace,
              test_the_model_is_told_what_broke,
              test_a_failure_still_counts_as_a_tool_call,
              test_every_tool_declares_a_failure_handler_naming_itself,
              test_every_declared_tool_is_in_the_toolbelt):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
