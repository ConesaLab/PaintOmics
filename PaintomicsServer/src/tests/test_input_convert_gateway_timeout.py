#!/usr/bin/env python3
"""A hung model gateway must fail a conversion turn fast, and say so.

Why this exists
---------------
On 2026-08-21 the CSIC gateway (llm.iiia.es) stopped answering for about an
hour: TCP and TLS completed, then not one byte came back. Every conversion
started in that window looked, to the user, like the feature being broken:

  * the server turn called the gateway NON-streamed with a 180 s read timeout
    and three attempts, so one turn took ~9 minutes to fail;
  * the browser polls a ticket for ~4 minutes and then reports the generic
    "The conversion timed out." -- nothing named the gateway;
  * the failed turn came back as `state: done, action: null`, which the agent
    loop treats as "the model wrote garbage" and RETRIES, five times;
  * each zombie turn held one of the two conversion slots, so the next click
    was refused with "the server is converting other files".

The interpreter had already moved every long call to a streamed, bounded
transport for exactly this gateway behaviour (see
csic-gateway-120s-per-attempt-budget); the converter was written later and
took the unbounded path.

What is pinned here
-------------------
  * `LLMClient.complete(stream=True)` folds a server-sent-event stream into
    one string, and a `budget_seconds` wall clock bounds the WHOLE call,
    retries included -- a stream that trickles forever is cut, and no retry
    starts past the deadline.
  * `agent_turn.next_action` asks for a streamed, bounded turn whose budget
    ends before the browser stops listening, and raises `GatewayUnavailable`
    (never returns None) when the gateway does not answer. None stays
    reserved for an answer that arrived but did not parse -- that IS worth
    another attempt; a dead gateway is not.
  * The servlet reports a failed turn as `state: "error"` WITH the reason,
    consumes the failed ticket, refuses the next turn immediately while the
    gateway is cooling down, and never over-releases the concurrency
    semaphore on an error path.

Usage:
    cd PaintomicsServer
    PYTHONPATH=. python src/tests/test_input_convert_gateway_timeout.py
"""
import json
import os
import sys
import time
import unittest

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SERVER_ROOT)

import requests

from src.classes.AIInterpret import llm_client as lc
from src.classes.InputConvert import agent_turn
from src.common.PySiQ import Queue, JobStatus
from src.servlets import InputConvertServlet as servlet

# No ladder: every count below is "attempts per model" for ONE model. The
# fallback has its own suite (test_model_fallback).
PROVIDER = {"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
            "fallback_models": ""}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
def _sse(text_parts, done=True):
    """The byte lines an OpenAI-compatible gateway streams for `text_parts`."""
    lines = []
    for part in text_parts:
        lines.append(("data: " + json.dumps(
            {"choices": [{"delta": {"content": part}}]})).encode("utf-8"))
        lines.append(b"")                      # SSE records are blank-line separated
    if done:
        lines.append(b"data: [DONE]")
    return lines


class _StreamResp:
    """requests.Response double for stream=True: iter_lines() + close()."""
    headers = {}

    def __init__(self, lines, delay=0.0, status=200):
        self._lines = lines
        self._delay = delay
        self.status_code = status
        self.closed = False
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError("HTTP %d" % self.status_code)
            err.response = self
            raise err

    def iter_lines(self):
        for line in self._lines:
            if self._delay:
                time.sleep(self._delay)
            yield line

    def close(self):
        self.closed = True

    def json(self):                            # only the non-stream path uses this
        raise AssertionError("a streamed response must not be read with .json()")


class _Patched:
    """Swap requests.post and time.sleep inside llm_client for one test."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.sleeps = []

    def __enter__(self):
        self._post, self._sleep = lc.requests.post, lc.time.sleep

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            self.calls.append({"payload": json, "timeout": timeout, "stream": stream})
            return self.handler(json, stream)

        lc.requests.post = fake_post
        # `time` is one shared module, so this stub is global: keep real
        # sub-second sleeps (the trickling stream below relies on them) and
        # only swallow the 5 s / 10 s retry backoffs.
        real_sleep = self._sleep
        lc.time.sleep = lambda s: real_sleep(s) if s < 1 else self.sleeps.append(s)
        return self

    def __exit__(self, *exc):
        lc.requests.post, lc.time.sleep = self._post, self._sleep


class _Request:
    def __init__(self, state=None, cookies=None):
        self.cookies = cookies or {}
        self._state = state or {}

    def get_json(self, force=True, silent=True):
        return self._state


class _Response:
    def __init__(self):
        self.content = None
        self.status = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        self.status = status

    def getResponse(self):
        return self.content


class _AnySession:
    def isValidUser(self, userID, sessionToken):
        return True


def _wait_until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ---------------------------------------------------------------------------
# LLMClient.complete(stream=True, budget_seconds=...)
# ---------------------------------------------------------------------------
class StreamedCompletion(unittest.TestCase):

    def test_stream_is_folded_into_one_string(self):
        parts = ['{"type": "code", ', '"python": "print(1)", ', '"summary": "ok"}']
        with _Patched(lambda payload, stream: _StreamResp(_sse(parts))) as p:
            out = lc.LLMClient(PROVIDER).complete(
                [{"role": "user", "content": "x"}], stream=True)
        self.assertEqual(out, "".join(parts))
        self.assertEqual(len(p.calls), 1)
        self.assertTrue(p.calls[0]["payload"].get("stream"),
                        "the payload must ask the gateway to stream")
        self.assertTrue(p.calls[0]["stream"],
                        "requests.post must be told to stream the body")

    def test_default_call_is_unchanged(self):
        """stream defaults off: the interpreter's callers see no difference."""
        class _Plain:
            headers = {}
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "plain"}}]}

        with _Patched(lambda payload, stream: _Plain()) as p:
            out = lc.LLMClient(PROVIDER).complete([{"role": "user", "content": "x"}])
        self.assertEqual(out, "plain")
        self.assertNotIn("stream", p.calls[0]["payload"])
        self.assertFalse(p.calls[0]["stream"])

    def test_a_trickling_stream_is_cut_at_the_budget(self):
        # 200 chunks, 50 ms apart = 10 s of "alive but useless" streaming.
        slow = _StreamResp(_sse(["x"] * 200), delay=0.05)
        started = time.monotonic()
        with _Patched(lambda payload, stream: slow) as p:
            with self.assertRaises(requests.exceptions.Timeout):
                lc.LLMClient(PROVIDER).complete(
                    [{"role": "user", "content": "x"}], stream=True,
                    budget_seconds=0.4, max_attempts=3)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0, "the budget must cut the stream, not wait it out")
        self.assertTrue(slow.closed, "the cut stream must be closed")
        self.assertEqual(len(p.calls), 1, "no retry may start once the budget is spent")

    def test_read_timeouts_retry_within_the_budget_only(self):
        def handler(payload, stream):
            raise requests.exceptions.ReadTimeout("no token for 60 s")

        with _Patched(handler) as p:
            with self.assertRaises(requests.exceptions.Timeout):
                lc.LLMClient(PROVIDER).complete(
                    [{"role": "user", "content": "x"}], stream=True,
                    max_attempts=2, budget_seconds=1000)
        self.assertEqual(len(p.calls), 2, "max_attempts bounds the retries")

        # A budget that the first attempt already exhausted: no second attempt.
        fake_clock = {"now": 0.0}
        real = lc.time.monotonic
        lc.time.monotonic = lambda: fake_clock["now"]
        try:
            def slow_fail(payload, stream):
                fake_clock["now"] += 200.0        # the attempt ate 200 "seconds"
                raise requests.exceptions.ReadTimeout("no token")

            with _Patched(slow_fail) as p:
                with self.assertRaises(requests.exceptions.Timeout):
                    lc.LLMClient(PROVIDER).complete(
                        [{"role": "user", "content": "x"}], stream=True,
                        max_attempts=3, budget_seconds=150)
            self.assertEqual(len(p.calls), 1,
                             "past the deadline, the retry loop must stop")

            # A retry that does fit gets only the budget that is left: a
            # blocked read cannot see the deadline, so the read timeout is
            # what bounds the final attempt.
            fake_clock["now"] = 0.0

            def eat_seventy(payload, stream):
                fake_clock["now"] += 70.0
                raise requests.exceptions.ReadTimeout("no token")

            with _Patched(eat_seventy) as p:
                with self.assertRaises(requests.exceptions.Timeout):
                    lc.LLMClient(PROVIDER).complete(
                        [{"role": "user", "content": "x"}], stream=True,
                        timeout=(15, 60), max_attempts=3, budget_seconds=100)
            self.assertEqual(p.calls[0]["timeout"], (15, 60))
            self.assertEqual(p.calls[1]["timeout"], (15, 30),
                             "the second attempt may only read for what is left")
            self.assertEqual(len(p.calls), 2, "no third attempt fits the budget")
        finally:
            lc.time.monotonic = real


# ---------------------------------------------------------------------------
# agent_turn.next_action
# ---------------------------------------------------------------------------
class _RecordingClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.kwargs = reply, error, None

    def complete(self, messages, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.reply


STATE = {"goal": "convert", "fileName": "x.tsv", "profile": {"tables": []},
         "history": [], "answers": {}, "instructions": []}


class TurnContract(unittest.TestCase):

    def test_turn_is_streamed_and_bounded_inside_the_browsers_patience(self):
        client = _RecordingClient(reply='{"type": "done"}')
        agent_turn.next_action(dict(STATE), client=client)
        self.assertTrue(client.kwargs.get("stream"), "the turn must stream")
        budget = client.kwargs.get("budget_seconds")
        self.assertIsNotNone(budget, "the turn must carry a wall-clock budget")
        # convert-drawer.js polls 240 times, a second apart plus the round
        # trip: the server must give up first, with a reason, or the browser
        # reports a bare "timed out" while the turn keeps spending.
        self.assertLessEqual(budget, 200)
        self.assertEqual(budget, agent_turn.TURN_BUDGET_SECONDS)

    def test_a_hung_gateway_raises_not_none(self):
        client = _RecordingClient(
            error=requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)"))
        with self.assertRaises(agent_turn.GatewayUnavailable) as ctx:
            agent_turn.next_action(dict(STATE), client=client)
        message = str(ctx.exception)
        self.assertIn("AI service", message)
        self.assertIn(str(agent_turn.TURN_BUDGET_SECONDS), message,
                      "the message should say how long it waited")

    def test_a_refusal_names_the_status(self):
        resp = _StreamResp([], status=503)
        err = requests.exceptions.HTTPError("HTTP 503")
        err.response = resp
        client = _RecordingClient(error=err)
        with self.assertRaises(agent_turn.GatewayUnavailable) as ctx:
            agent_turn.next_action(dict(STATE), client=client)
        self.assertIn("503", str(ctx.exception))

    def test_garbage_from_a_live_gateway_is_still_none(self):
        """An answer that does not parse is the MODEL's failure: retry it."""
        client = _RecordingClient(reply="I cannot help with that.")
        self.assertIsNone(agent_turn.next_action(dict(STATE), client=client))


# ---------------------------------------------------------------------------
# InputConvertServlet
# ---------------------------------------------------------------------------
class ServletFailurePath(unittest.TestCase):

    def setUp(self):
        self._session = servlet.UserSessionManager
        self._enabled = servlet._converter_enabled
        self._next = agent_turn.next_action
        servlet.UserSessionManager = _AnySession
        servlet._converter_enabled = lambda: True
        servlet._reset_gateway_state()
        # A fresh per-test usage table so the daily limit never leaks between tests.
        servlet._usage.clear()

    def tearDown(self):
        servlet.UserSessionManager = self._session
        servlet._converter_enabled = self._enabled
        agent_turn.next_action = self._next
        servlet._reset_gateway_state()

    def _turn(self, queue, state=None, cookies=None, ticket="convert_t1"):
        request = _Request(state or {"goal": "g"}, cookies or {"userID": "u1", "sessionToken": "s"})
        return servlet.inputConvertTurn(request, _Response(), queue, ticket).content

    def _result(self, queue, ticket):
        request = _Request(cookies={"userID": "u1", "sessionToken": "s"})
        return servlet.inputConvertResult(request, _Response(), queue, ticket).content

    def test_failed_turn_reports_error_with_the_reason_and_is_consumed(self):
        agent_turn.next_action = lambda payload: (_ for _ in ()).throw(
            agent_turn.GatewayUnavailable("The AI service did not answer within 5 seconds."))
        queue = Queue()
        queue.start_worker(1)
        try:
            body = self._turn(queue)
            self.assertTrue(body.get("success"), body)
            ticket = body["ticket"]
            self.assertTrue(_wait_until(
                lambda: queue.check_status(ticket) == JobStatus.FAILED))
            polled = self._result(queue, ticket)
            self.assertEqual(polled.get("state"), "error")
            self.assertIn("did not answer", polled.get("message", ""))
            # Consumed: the next poll finds nothing rather than re-reporting it.
            self.assertEqual(self._result(queue, ticket).get("state"), "unknown")
        finally:
            queue.stop_worker()

    def test_next_turn_is_refused_immediately_while_the_gateway_cools_down(self):
        agent_turn.next_action = lambda payload: (_ for _ in ()).throw(
            agent_turn.GatewayUnavailable("The AI service did not answer within 5 seconds."))
        queue = Queue()
        queue.start_worker(1)
        try:
            ticket = self._turn(queue)["ticket"]
            self.assertTrue(_wait_until(
                lambda: queue.check_status(ticket) == JobStatus.FAILED))
            self.assertTrue(servlet._gateway_cooldown_left() > 0,
                            "a failed turn must start the cooldown")
            refused = self._turn(queue, ticket="convert_t2")
            self.assertFalse(refused.get("success"), refused)
            text = json.dumps(refused)
            self.assertIn("did not answer", text)
            self.assertIn("try again", text.lower())
            # Nothing was enqueued for the refused click.
            self.assertEqual(queue.check_status("convert_t2"), JobStatus.NOT_QUEUED)
        finally:
            queue.stop_worker()

    def test_cooldown_expires(self):
        servlet._mark_gateway_down("The AI service did not answer within 5 seconds.")
        self.assertGreater(servlet._gateway_cooldown_left(), 0)
        servlet._gateway_down_until = time.monotonic() - 1
        self.assertEqual(servlet._gateway_cooldown_left(), 0)
        queue = Queue()                                 # no worker: the job just sits
        body = self._turn(queue, ticket="convert_t3")
        self.assertTrue(body.get("success"), body)

    def test_a_successful_turn_clears_the_cooldown(self):
        servlet._mark_gateway_down("The AI service did not answer within 5 seconds.")
        agent_turn.next_action = lambda payload: {"type": "done"}
        servlet._gateway_down_until = time.monotonic() - 1
        queue = Queue()
        queue.start_worker(1)
        try:
            ticket = self._turn(queue, ticket="convert_t4")["ticket"]
            self.assertTrue(_wait_until(
                lambda: queue.check_status(ticket) == JobStatus.FINISHED))
            self.assertEqual(self._result(queue, ticket).get("action"), {"type": "done"})
        finally:
            queue.stop_worker()

    def test_an_error_before_the_slot_is_taken_does_not_free_someone_elses(self):
        queue = Queue()
        servlet._inflight.acquire()                     # another conversion in flight
        try:
            before = servlet._inflight._value
            # Exhaust this user's daily budget so the route refuses BEFORE acquire.
            day = int(time.time() // 86400)
            servlet._usage["u9"] = (day, servlet.MAX_TURNS_PER_USER_PER_DAY)
            refused = self._turn(queue, cookies={"userID": "u9", "sessionToken": "s"},
                                 ticket="convert_t5")
            self.assertFalse(refused.get("success"), refused)
            self.assertEqual(servlet._inflight._value, before,
                             "a pre-acquire failure must not release a slot it never held")
        finally:
            servlet._inflight.release()

    def test_the_worker_releases_the_slot_it_was_handed(self):
        agent_turn.next_action = lambda payload: {"type": "done"}
        queue = Queue()
        queue.start_worker(1)
        try:
            free = servlet._inflight._value
            ticket = self._turn(queue, ticket="convert_t6")["ticket"]
            self.assertTrue(_wait_until(
                lambda: queue.check_status(ticket) == JobStatus.FINISHED))
            self.assertTrue(_wait_until(lambda: servlet._inflight._value == free),
                            "the slot must come back once the turn is over")
        finally:
            queue.stop_worker()


if __name__ == "__main__":
    unittest.main(verbosity=2)
