#!/usr/bin/env python3
"""The walk arms the transport's deadline, and the transport reads it.

The retry shim under the Agents SDK backs off on its own schedule -- up to 7
attempts with waits that reached 60 s. One guard stops it from overrunning the
run:

    left = _run_seconds_left()
    if left is not None and left < delay + RETRY_MIN_ATTEMPT_SECONDS:
        raise

`left` is None unless somebody called set_run_deadline(). The previous
interpreter imported that function and, across every commit checked, never
called it -- so the guard was dead and the transport could retry past the
deadline the rest of the run respected. An import is not a call, and only a
test that looks for the CALL, in the right place, can tell the difference.

    python -m src.tests.test_both_arms_arm_the_transport
"""
from __future__ import annotations

import inspect
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret import agent               # noqa: E402
from src.classes.AIInterpret.walker import service      # noqa: E402

_PASSED, _FAILED = [], []


def test_the_walk_arms_the_deadline_before_its_model_loop():
    """Arming it after the walk has started leaves the early calls unguarded,
    and the Writer and the checks run on the same armed context."""
    src = inspect.getsource(service._model_walk)
    armed = src.find("set_run_deadline(")
    started = src.find("asyncio.run(")
    assert armed != -1, "service._model_walk never arms the transport's deadline"
    assert started != -1, "service._model_walk no longer runs the model walk; update this test"
    assert armed < started, "the deadline is armed after the model walk starts"


def test_the_guard_actually_reads_the_deadline():
    """If the transport stopped consulting it, arming it would be theatre."""
    src = inspect.getsource(agent)
    assert "_run_seconds_left()" in src
    assert "RETRY_MIN_ATTEMPT_SECONDS" in src, (
        "the transport no longer reserves time for the attempt it is about to make")


def test_the_deadline_is_per_run():
    """A ContextVar, so two walks on two worker threads keep their own."""
    import contextvars
    import threading
    agent.set_run_deadline(100.0)
    seen = {}

    def other():
        seen["value"] = agent._run_seconds_left()

    thread = threading.Thread(target=contextvars.Context().run, args=(other,))
    thread.start()
    thread.join()
    assert seen["value"] is None, "a deadline set by one walk leaked into another thread"
    assert agent._run_seconds_left() is not None


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_the_walk_arms_the_deadline_before_its_model_loop,
              test_the_guard_actually_reads_the_deadline,
              test_the_deadline_is_per_run):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
