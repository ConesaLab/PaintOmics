#!/usr/bin/env python3
"""The retry shim must not outlive the run it is retrying for.

Round 33's agent-v33-r2 finished at 1722 s against a 600 s ceiling. Its trace
explains why: 28 searches in the first two minutes, the last tool call ending
at t=118 s, and then 1604 seconds of complete silence -- no tool calls at all.
The run was inside one model call the whole time.

Every phase-level guard in the pipeline bounds the work it starts, but the
transport underneath them had a budget of its own: 4 attempts at a 180 s read
timeout (8 when throttled) with backoff, and no idea when the run was due. Its
worst case is longer than the entire run.

Two rules, both pinned here: an attempt that cannot finish before the deadline
is not started, and a cancellation is never retried.

    python -m src.tests.test_transport_respects_the_deadline
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.agent import (       # noqa: E402
    RETRY_MIN_ATTEMPT_SECONDS, _run_seconds_left, set_run_deadline)

_PASSED, _FAILED = [], []


def test_no_deadline_armed_means_no_limit():
    """A caller that never armed one must behave exactly as before."""
    async def go():
        assert _run_seconds_left() is None
    asyncio.run(go())


def test_the_deadline_counts_down():
    async def go():
        set_run_deadline(time.time() + 600.0)
        left = _run_seconds_left()
        assert 599.0 < left <= 600.0, left
        set_run_deadline(time.time() - 5.0)
        assert _run_seconds_left() < 0, "an overrun must read negative"
    asyncio.run(go())


def test_the_deadline_is_per_run_not_global():
    """Two concurrent jobs in one process must not share a deadline. This is
    why it is a ContextVar and not a module global."""
    async def one(seconds, out, key):
        set_run_deadline(time.time() + seconds)
        await asyncio.sleep(0.01)
        out[key] = _run_seconds_left()

    async def go():
        out = {}
        await asyncio.gather(one(100.0, out, "a"), one(900.0, out, "b"))
        assert 90 < out["a"] < 101 and 890 < out["b"] < 901, out
    asyncio.run(go())


def test_a_child_task_inherits_the_deadline():
    """Sub-agents and verifiers run as tasks spawned after the deadline is set;
    the transport call that matters happens inside them."""
    async def go():
        set_run_deadline(time.time() + 300.0)
        seen = await asyncio.gather(asyncio.ensure_future(_leaf()))
        assert seen[0] is not None and 290 < seen[0] <= 300, seen
    asyncio.run(go())


async def _leaf():
    return _run_seconds_left()


def test_the_shim_stops_retrying_when_the_run_is_due():
    """The behaviour itself, driven through the real _paced_create."""
    from src.classes.AIInterpret import agent as A
    # The module openai actually raises from, taken from the code under test
    # rather than imported by name: openai 2.x raises httpx.*, 3.x raises
    # httpx2.*, and the two share no base class. Hard-coding `import httpx`
    # here made this suite exercise a path production could not take, and pass.
    httpx = A._transport

    A._sdk_configured = False
    A.configure_sdk()
    client = A._CLIENT
    assert client is not None, "configure_sdk no longer exposes its client"
    completions = client.chat.completions
    original = completions._pa_orig_create
    attempts = {"n": 0}

    async def always_stalls(*_a, **_kw):
        attempts["n"] += 1
        raise httpx.ReadTimeout("gateway stalled")

    completions._pa_orig_create = always_stalls
    try:
        async def out_of_time():
            set_run_deadline(time.time() + 1.0)
            with_deadline = attempts["n"]
            try:
                await completions.create(model="m", messages=[])
            except Exception:
                pass
            return attempts["n"] - with_deadline

        async def all_the_time():
            set_run_deadline(time.time() + 3600.0)
            before = attempts["n"]
            try:
                await completions.create(model="m", messages=[])
            except Exception:
                pass
            return attempts["n"] - before

        spent_late = asyncio.run(out_of_time())
        spent_early = asyncio.run(all_the_time())
    finally:
        completions._pa_orig_create = original

    assert spent_late == 1, (
        "the shim retried past the deadline: %d attempts with 1 s left"
        % spent_late)
    assert spent_early > spent_late, (
        "the deadline changed nothing -- %d attempts with an hour left vs %d "
        "with a second" % (spent_early, spent_late))


def test_a_cancellation_is_never_retried():
    """httpx maps some cancellations during a stream read onto its own error
    types, which the transient test answers True for. Without an explicit
    re-raise, 'the run is over' becomes 'sleep, then try again'."""
    import inspect
    from src.classes.AIInterpret import agent as A
    source = inspect.getsource(A.configure_sdk)
    assert "except asyncio.CancelledError:" in source, (
        "no explicit cancellation clause in the retry shim")
    assert source.index("except asyncio.CancelledError:") < \
           source.index("except (_oai.APIError, *_TRANSPORT_ERRORS"), (
        "the cancellation clause must come FIRST or the broad clause wins")


def test_the_retry_budget_is_named_in_seconds_not_just_attempts():
    """4 attempts x a 180 s read timeout is 720 s. Stating the attempt count
    alone hid a worst case longer than the whole run."""
    from src.classes.AIInterpret import agent as A
    worst = 4 * A.SDK_HTTP_READ_TIMEOUT
    assert worst > 600, (
        "the read timeout changed; this test exists to keep the relationship "
        "visible (worst case %.0f s)" % worst)
    assert RETRY_MIN_ATTEMPT_SECONDS > 0


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_no_deadline_armed_means_no_limit,
              test_the_deadline_counts_down,
              test_the_deadline_is_per_run_not_global,
              test_a_child_task_inherits_the_deadline,
              test_the_shim_stops_retrying_when_the_run_is_due,
              test_a_cancellation_is_never_retried,
              test_the_retry_budget_is_named_in_seconds_not_just_attempts):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
