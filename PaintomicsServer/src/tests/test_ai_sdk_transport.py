#!/usr/bin/env python3
"""The Agents SDK transport must stream, must own its retries, and must be bounded.

Why this exists
---------------
On 2026-08-17 two live interpretations on paintomics.uv.es sat at "Synthesizing
report... 78%" for over an hour and a half (jobs 4101fcI63x, Wj2Nc70376). The
citation top-up asks the model to return the whole report again; for the
105-pathway STATegra example that answer is ~17k tokens, and the CSIC gateway
gives each *non-streamed* attempt about 120 s before it gives up (measured:
14k tokens took 477 s = four internal attempts; the top-up got HTTP 408 after
489 s, three times in a row). Streaming does not have that ceiling -- a 25k
token generation streamed through in 203 s -- because the budget is per read,
not per response.

Three transport defects turned "one slow call" into "never finishes":

  1. Nothing streamed. Every SDK call went through ``create(stream=False)``.
  2. Retries multiplied. ``AsyncOpenAI`` retries 408 twice on its own, and
     ``_paced_create`` retried the whole thing four times: 12 attempts, each
     eight minutes long.

The tests below pin the fixed contract:

  * ``_stream_to_completion`` folds a chunk stream (content, tool calls, usage)
    into a ``ChatCompletion`` the SDK can consume unchanged.
  * ``_build_client`` (the client each event loop's model is bound to) has
    ``max_retries=0`` and a finite timeout, and its ``create`` shim streams by default and passes a
    caller's ``stream=True`` straight through.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_sdk_transport
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("AI_CSIC_API_KEY", "test-key")


def _chunk(**kw):
    """A ChatCompletionChunk with one choice, built from delta fields."""
    from openai.types.chat import ChatCompletionChunk
    delta = kw.pop("delta", {})
    finish = kw.pop("finish_reason", None)
    usage = kw.pop("usage", None)
    return ChatCompletionChunk.model_validate({
        "id": "chatcmpl-test", "object": "chat.completion.chunk",
        "created": 1, "model": "stub-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": usage,
    })


class _FakeStream:
    """Stands in for openai's AsyncStream: async-iterable, closable."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()

    async def close(self):
        self.closed = True


class StreamReassemblyTest(unittest.TestCase):

    def test_content_and_usage_fold_into_a_chat_completion(self):
        from src.classes.AIInterpret.agent import _stream_to_completion
        from openai.types.chat import ChatCompletion
        stream = _FakeStream([
            _chunk(delta={"role": "assistant", "content": "Hel"}),
            _chunk(delta={"content": "lo"}),
            _chunk(delta={"content": " [1]"}, finish_reason="stop"),
            _chunk(usage={"prompt_tokens": 10, "completion_tokens": 3,
                          "total_tokens": 13}),
        ])
        done = asyncio.run(_stream_to_completion(stream))
        self.assertIsInstance(done, ChatCompletion)
        self.assertEqual(done.choices[0].message.content, "Hello [1]")
        self.assertEqual(done.choices[0].message.role, "assistant")
        self.assertEqual(done.choices[0].finish_reason, "stop")
        self.assertEqual(done.usage.completion_tokens, 3)
        self.assertEqual(done.usage.total_tokens, 13)
        self.assertEqual(done.id, "chatcmpl-test")
        self.assertEqual(done.object, "chat.completion")
        self.assertTrue(stream.closed, "the stream must be closed once folded")

    def test_tool_call_deltas_are_joined_by_index(self):
        from src.classes.AIInterpret.agent import _stream_to_completion
        stream = _FakeStream([
            _chunk(delta={"role": "assistant", "content": None,
                          "tool_calls": [{"index": 0, "id": "call_a",
                                          "type": "function",
                                          "function": {"name": "get_feature_values",
                                                       "arguments": ""}}]}),
            _chunk(delta={"tool_calls": [{"index": 0,
                                          "function": {"arguments": "{\"gene_sym"}}]}),
            _chunk(delta={"tool_calls": [{"index": 0,
                                          "function": {"arguments": "bol\": \"Ccl2\"}"}}]}),
            _chunk(delta={"tool_calls": [{"index": 1, "id": "call_b",
                                          "type": "function",
                                          "function": {"name": "compare_genes",
                                                       "arguments": "{}"}}]}),
            _chunk(delta={}, finish_reason="tool_calls"),
        ])
        done = asyncio.run(_stream_to_completion(stream))
        calls = done.choices[0].message.tool_calls
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].id, "call_a")
        self.assertEqual(calls[0].function.name, "get_feature_values")
        self.assertEqual(calls[0].function.arguments, '{"gene_symbol": "Ccl2"}')
        self.assertEqual(calls[1].id, "call_b")
        self.assertEqual(calls[1].function.name, "compare_genes")
        self.assertEqual(done.choices[0].finish_reason, "tool_calls")
        self.assertIsNone(done.choices[0].message.content)

    def test_no_usage_chunk_yields_no_usage(self):
        # vLLM only sends usage when stream_options asks for it; the SDK treats a
        # missing usage as zero rather than crashing, so None must survive.
        from src.classes.AIInterpret.agent import _stream_to_completion
        stream = _FakeStream([_chunk(delta={"content": "x"}, finish_reason="stop")])
        done = asyncio.run(_stream_to_completion(stream))
        self.assertIsNone(done.usage)
        self.assertEqual(done.choices[0].message.content, "x")

    def test_an_empty_stream_is_an_error_not_an_empty_answer(self):
        # A gateway that closes the stream without a single choice has not
        # answered; surfacing that as a retryable error beats handing the SDK
        # a blank message it would score as "the model said nothing".
        from src.classes.AIInterpret.agent import _stream_to_completion, _EmptyStream
        with self.assertRaises(_EmptyStream):
            asyncio.run(_stream_to_completion(_FakeStream([])))


    def test_a_truncated_completion_is_counted_and_logged(self):
        """finish_reason "length" means the model ran out of output budget and
        the partial text ships as though it were finished.

        A stored report ends on the bare heading "### 4." with no Limitations
        section after it -- the last section is simply where the tokens ran out,
        and nothing anywhere said so. Two of sixteen recent reports are missing a
        required section for this reason.
        """
        from src.classes.AIInterpret.agent import _stream_to_completion, _TRUNCATIONS
        before = len(_TRUNCATIONS)
        stream = _FakeStream([
            _chunk(delta={"role": "assistant", "content": "## Follow-up\n\n### 4."}),
            _chunk(delta={}, finish_reason="length"),
        ])
        done = asyncio.run(_stream_to_completion(stream))
        self.assertEqual(done.choices[0].finish_reason, "length")
        self.assertEqual(len(_TRUNCATIONS), before + 1,
                         "a truncated answer was not recorded")
        self.assertEqual(_TRUNCATIONS[-1], len("## Follow-up\n\n### 4."))

    def test_a_normal_completion_is_not_counted_as_truncated(self):
        from src.classes.AIInterpret.agent import _stream_to_completion, _TRUNCATIONS
        before = len(_TRUNCATIONS)
        stream = _FakeStream([
            _chunk(delta={"role": "assistant", "content": "All done."}),
            _chunk(delta={}, finish_reason="stop"),
        ])
        asyncio.run(_stream_to_completion(stream))
        self.assertEqual(len(_TRUNCATIONS), before,
                         "a completed answer was logged as truncated")

class ClientConfigurationTest(unittest.TestCase):

    def setUp(self):
        import src.classes.AIInterpret.agent as agent
        self.agent = agent
        self._saved = agent._sdk_configured
        agent._sdk_configured = False

    def tearDown(self):
        self.agent._sdk_configured = self._saved

    def test_client_owns_no_retries_and_has_a_finite_timeout(self):
        client = self.agent._build_client()
        self.assertEqual(client.max_retries, 0,
                         "the shim is the only retry policy; stacked retries "
                         "made one 408 cost twelve eight-minute attempts")
        timeout = client.timeout
        # httpx.Timeout or a float -- either way every phase must be finite.
        read = getattr(timeout, "read", timeout)
        self.assertIsNotNone(read)
        self.assertLess(float(read), 600.0)

    def test_create_streams_by_default_and_passes_stream_true_through(self):
        client = self.agent._build_client()
        calls = []

        async def fake_orig(*args, **kwargs):
            calls.append(kwargs)
            if kwargs.get("stream") is True and kwargs.get("_passthrough_test"):
                return "raw-stream"
            return _FakeStream([_chunk(delta={"content": "ok"}, finish_reason="stop")])

        # The shim keeps the original under a known name so tests (and a
        # future provider that cannot stream) can reach it.
        client.chat.completions._pa_orig_create = fake_orig
        from openai.types.chat import ChatCompletion

        done = asyncio.run(client.chat.completions.create(model="m", messages=[]))
        self.assertIsInstance(done, ChatCompletion)
        self.assertEqual(done.choices[0].message.content, "ok")
        self.assertIs(calls[-1].get("stream"), True,
                      "a non-streaming call must be issued as a stream")
        self.assertEqual(calls[-1].get("stream_options"), {"include_usage": True})

        raw = asyncio.run(client.chat.completions.create(
            model="m", messages=[], stream=True, _passthrough_test=True))
        self.assertEqual(raw, "raw-stream")


class ClientPerEventLoopTest(unittest.TestCase):
    """Every event loop gets a client of its own.

    paintomics.org, 26-27 Oct 2026: one process-wide AsyncOpenAI client served
    every walk, each walk ran in an event loop of its own, and httpx binds a
    pooled connection to the loop that opened it. When one walk finished and
    closed its loop, the next walk to reuse that connection died with
    "RuntimeError: Event loop is closed" (three times; job vRmKBo4E60 stored
    "The AI service failed before the walk took a step").
    """

    def setUp(self):
        import src.classes.AIInterpret.agent as agent
        self.agent = agent

    async def _grab(self):
        model = self.agent._model()
        # The same loop asking again gets the same object, not a new pool.
        self.assertIs(self.agent._model(), model)
        return model

    def test_a_loop_closed_before_the_next_starts_does_not_share_its_client(self):
        first = asyncio.new_event_loop()
        try:
            one = first.run_until_complete(self._grab())
        finally:
            first.close()
        second = asyncio.new_event_loop()
        try:
            two = second.run_until_complete(self._grab())
        finally:
            second.close()
        self.assertIsNot(one, two)
        self.assertIsNot(one._client, two._client,
                         "a closed loop's client was handed to the next loop")

    def test_two_loops_running_at_once_on_two_threads_get_two_clients(self):
        import threading
        both_running = threading.Barrier(2, timeout=10)
        seen = {}

        def walk(name):
            async def go():
                model = self.agent._model()
                both_running.wait()          # the two loops overlap here
                return model
            loop = asyncio.new_event_loop()
            try:
                seen[name] = loop.run_until_complete(go())
            finally:
                loop.close()

        threads = [threading.Thread(target=walk, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(set(seen), {"a", "b"})
        self.assertIsNot(seen["a"]._client, seen["b"]._client)

    def test_a_released_loop_closes_its_client_and_never_returns_it(self):
        released = {}

        async def use_then_release():
            model = self.agent._model()
            await self.agent.release_loop_client()
            released["client"] = model._client
            # Asked again in the same loop after the release: a new one.
            return self.agent._model()

        loop = asyncio.new_event_loop()
        try:
            again = loop.run_until_complete(use_then_release())
            loop.run_until_complete(self.agent.release_loop_client())
        finally:
            loop.close()
        self.assertTrue(released["client"].is_closed(),
                        "the client was not closed inside its own loop")
        self.assertIsNot(again._client, released["client"])

    def test_a_loop_that_is_gone_takes_its_entry_with_it(self):
        import gc
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._grab())
        finally:
            loop.close()
        self.assertIn(loop, self.agent._LOOP_MODELS)
        del loop
        gc.collect()
        self.assertEqual(len([k for k in self.agent._LOOP_MODELS.keys()
                              if k.is_closed()]), 0,
                         "a closed loop's client is still cached")

    def test_two_clients_are_two_pools(self):
        # Pacing is not theirs to hold: test_one_rate_budget_per_gateway shows
        # every client, and LLMClient, waiting on the one pacer per gateway.
        a, b = self.agent._build_client(), self.agent._build_client()
        self.assertIsNot(a, b)

    def test_no_agent_relies_on_the_sdk_default_client(self):
        """set_default_openai_client is process-global in the SDK; a default
        bound to one loop would be the shared client all over again."""
        import inspect
        source = inspect.getsource(self.agent)
        self.assertNotIn("set_default_openai_client(", source)

    def test_run_pipeline_releases_the_client_before_closing_its_loop(self):
        from src.classes.AIInterpret.walker import parallel
        made = {}

        async def walk():
            made["model"] = self.agent._model()
            return "done"

        self.assertEqual(parallel.run_pipeline(walk()), "done")
        self.assertTrue(made["model"]._client.is_closed())


class RateLimitRetryTest(unittest.TestCase):
    """429 gets a patient policy of its own.

    With the client's own retries switched off (see above), the shim is the
    only thing standing between a 60 rpm key and a verification phase that
    fans out 28 citations at once. A 429 answers instantly and costs nothing
    to retry -- unlike a 408 that took eight minutes to arrive -- so it gets
    more attempts and honours Retry-After, instead of the four quick tries
    that made the first live run redact citations the gateway had merely
    throttled.
    """

    def setUp(self):
        import src.classes.AIInterpret.agent as agent
        self.agent = agent
        self._saved = agent._sdk_configured
        agent._sdk_configured = False
        self.client = agent._build_client()
        self.sleeps = []
        self._real_sleep = asyncio.sleep

        async def fake_sleep(d):
            self.sleeps.append(d)
        agent.asyncio.sleep = fake_sleep

    def tearDown(self):
        self.agent.asyncio.sleep = self._real_sleep
        self.agent._sdk_configured = self._saved

    def _rate_limited(self, times, retry_after=None):
        import openai, httpx
        state = {"n": 0}

        async def fake_orig(*args, **kwargs):
            if state["n"] < times:
                state["n"] += 1
                headers = {"retry-after": str(retry_after)} if retry_after else {}
                resp = httpx.Response(429, headers=headers,
                                      request=httpx.Request("POST", "http://x"))
                raise openai.RateLimitError("slow down", response=resp, body=None)
            return _FakeStream([_chunk(delta={"content": "ok"}, finish_reason="stop")])
        self.client.chat.completions._pa_orig_create = fake_orig
        return state

    def test_six_throttles_in_a_row_still_succeed(self):
        state = self._rate_limited(6)
        done = asyncio.run(self.client.chat.completions.create(model="m", messages=[]))
        self.assertEqual(done.choices[0].message.content, "ok")
        self.assertEqual(state["n"], 6)
        self.assertEqual(len(self.sleeps), 6)
        # Backoff grows and is capped, never a fixed 2 s.
        self.assertGreater(self.sleeps[-1], self.sleeps[0])
        self.assertLessEqual(max(self.sleeps), 30.0)

    def test_retry_after_header_is_honoured(self):
        self._rate_limited(1, retry_after=7)
        asyncio.run(self.client.chat.completions.create(model="m", messages=[]))
        self.assertEqual(self.sleeps, [7.0])

    def test_a_408_keeps_the_short_policy(self):
        # Each 408 already cost minutes on the wire; four attempts is plenty.
        import openai, httpx
        state = {"n": 0}

        async def fake_orig(*args, **kwargs):
            state["n"] += 1
            resp = httpx.Response(408, request=httpx.Request("POST", "http://x"))
            raise openai.APIStatusError("timeout", response=resp, body=None)
        self.client.chat.completions._pa_orig_create = fake_orig
        with self.assertRaises(openai.APIStatusError):
            asyncio.run(self.client.chat.completions.create(model="m", messages=[]))
        self.assertEqual(state["n"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
