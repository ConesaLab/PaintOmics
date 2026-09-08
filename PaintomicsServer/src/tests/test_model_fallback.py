#!/usr/bin/env python3
"""The pinned model first; when it is not being served, the next one -- now.

Why this exists
---------------
On 2026-09-08 the CSIC gateway kept answering while the backend for the
pinned model went away: every request for deepseek-ai/DeepSeek-V4-Flash-0731
came back 500 ("Cannot connect to host host.docker.internal:8000"), and the
gateway's own default/llm alias answered in a third of a second. Every AI
feature failed for the rest of the day because nothing knew a second name.

What is pinned here
-------------------
  * LLMClient.complete asks the configured model first, and on a failure
    that means "not served" (5xx, connection, timeout, 404) asks the next
    model on the ladder at once; `model_used` says which one answered.
  * A model that failed that way is skipped for the cooldown: the next call
    goes straight to what works, and after the cooldown the pinned model is
    tried first again.
  * A 4xx that every model would answer alike (401, 400) is raised, not
    routed around, and the pinned model is not marked down for it.
  * A plain request that gets a 5xx is retried streamed before the model is
    given up on (the shape of the 2026-09-08 outage).
  * The Agents SDK shim does the same swap on its own create call, with no
    wait, and leaves 429 alone.
  * The ladder comes from `fallback_models` (list or comma string), else the
    AI_<PROVIDER>_FALLBACK_MODELS variable, else the CSIC default; the
    primary is never repeated in it.
  * The probe's verdict: OK / DEGRADED / FAIL / SKIP with exit 0 / 3 / 1 / 2.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_model_fallback
"""
import asyncio
import json
import os
import sys
import unittest

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SERVER_ROOT)

import requests  # noqa: E402

from src.classes.AIInterpret import llm_client as lc  # noqa: E402
from src.classes.AIInterpret import model_fallback as mf  # noqa: E402
from src.AdminTools import check_llm_gateway as gateway  # noqa: E402

PRIMARY, SECOND, THIRD = "pinned/model", "fallback/one", "fallback/two"
PROVIDER = {"api_base": "https://gateway.example/v1", "api_key": "k",
            "model": PRIMARY, "fallback_models": [SECOND, THIRD]}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class _Plain:
    """requests.Response double for a non-streamed answer."""
    headers = {}

    def __init__(self, content="answer", status=200):
        self.status_code = status
        self.text = content

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError("HTTP %d" % self.status_code)
            err.response = self
            raise err

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}


class _Streamed:
    """requests.Response double for stream=True."""
    headers = {}
    status_code = 200

    def __init__(self, content="answer"):
        self._content = content

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield ("data: " + json.dumps({"choices": [{"delta": {"content": self._content}}]})).encode()
        yield b"data: [DONE]"

    def close(self):
        pass


class _Gateway:
    """Swap requests.post inside llm_client; answer per model."""

    def __init__(self, behaviour):
        # behaviour: model -> callable(payload, stream) -> response or raises
        self.behaviour = behaviour
        self.calls = []

    def __enter__(self):
        self._post, self._sleep = lc.requests.post, lc.time.sleep

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            self.calls.append((json["model"], bool(stream)))
            return self.behaviour[json["model"]](json, stream)

        lc.requests.post = fake_post
        lc.time.sleep = lambda s: None
        return self

    def __exit__(self, *exc):
        lc.requests.post, lc.time.sleep = self._post, self._sleep

    @property
    def models(self):
        return [m for m, _ in self.calls]


def _http(status):
    def answer(payload, stream):
        return _Plain("", status=status)
    return answer


def _answers(text):
    def answer(payload, stream):
        return _Streamed(text) if stream else _Plain(text)
    return answer


def _dead(payload, stream):
    raise requests.exceptions.ConnectionError("connection reset by peer")


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------
class Ladder(unittest.TestCase):

    def test_defaults_and_parsing(self):
        self.assertEqual(mf.fallback_models({"model": "a", "fallback_models": "b, c ,a"}), ["b", "c"])
        self.assertEqual(mf.fallback_models({"model": "a", "fallback_models": ["b", "b"]}), ["b"])
        self.assertEqual(mf.fallback_models({"model": "a", "fallback_models": ""}), [])
        # No key in the config: the environment, then the CSIC default.
        os.environ.pop("AI_CSIC_FALLBACK_MODELS", None)
        self.assertEqual(mf.fallback_models({"model": "a"}, "csic"), ["default/llm"])
        self.assertEqual(mf.fallback_models({"model": "a"}, "openrouter"), [])
        os.environ["AI_CSIC_FALLBACK_MODELS"] = "x/y"
        try:
            self.assertEqual(mf.candidates({"model": "a"}, "csic"), ["a", "x/y"])
        finally:
            del os.environ["AI_CSIC_FALLBACK_MODELS"]

    def test_ordered_puts_a_down_model_last_but_keeps_it(self):
        mf.reset()
        mf.mark_down("base", PRIMARY, "500")
        self.assertEqual(mf.ordered("base", [PRIMARY, SECOND]), [SECOND, PRIMARY])
        mf.mark_up("base", PRIMARY)
        self.assertEqual(mf.ordered("base", [PRIMARY, SECOND]), [PRIMARY, SECOND])

    def test_what_counts_as_not_served(self):
        self.assertTrue(mf.falls_back(requests.exceptions.ConnectionError()))
        self.assertTrue(mf.falls_back(requests.exceptions.Timeout()))
        for status, expected in [(500, True), (502, True), (404, True),
                                 (400, False), (401, False), (403, False), (429, False)]:
            err = requests.exceptions.HTTPError()
            err.response = _Plain(status=status)
            self.assertIs(mf.falls_back(err), expected, status)


class CompleteFallsBack(unittest.TestCase):

    def setUp(self):
        mf.reset()

    def tearDown(self):
        mf.reset()

    def _ask(self, client, **kw):
        return client.complete([{"role": "user", "content": "q"}], **kw)

    def test_a_dead_pinned_model_hands_over_to_the_next_at_once(self):
        with _Gateway({PRIMARY: _http(500), SECOND: _answers("from two")}) as g:
            client = lc.LLMClient(PROVIDER)
            self.assertEqual(self._ask(client), "from two")
        # The pinned model was asked plain, then streamed, then the next rung
        # -- never a backoff-retry of the same plain request.
        self.assertEqual(g.calls, [(PRIMARY, False), (PRIMARY, True), (SECOND, False)])
        self.assertEqual(client.model_used, SECOND)
        self.assertTrue(mf.is_down(PROVIDER["api_base"], PRIMARY))
        self.assertFalse(mf.is_down(PROVIDER["api_base"], SECOND))

    def test_the_next_call_skips_the_model_in_cooldown(self):
        with _Gateway({PRIMARY: _http(500), SECOND: _answers("two")}) as g:
            client = lc.LLMClient(PROVIDER)
            self._ask(client)
            g.calls.clear()
            self.assertEqual(self._ask(client), "two")
        self.assertEqual(g.models, [SECOND])

    def test_after_the_cooldown_the_pinned_model_is_first_again(self):
        with _Gateway({PRIMARY: _answers("one"), SECOND: _answers("two")}) as g:
            client = lc.LLMClient(PROVIDER)
            mf.mark_down(PROVIDER["api_base"], PRIMARY, "earlier")
            self.assertEqual(self._ask(client), "two")
            # Expire the cooldown by hand.
            with mf._lock:
                mf._down[(PROVIDER["api_base"], PRIMARY)] = 0.0
            g.calls.clear()
            self.assertEqual(self._ask(client), "one")
        self.assertEqual(g.models, [PRIMARY])
        self.assertEqual(client.model_used, PRIMARY)
        self.assertFalse(mf.is_down(PROVIDER["api_base"], PRIMARY))

    def test_a_connection_error_moves_on_without_waiting(self):
        with _Gateway({PRIMARY: _dead, SECOND: _answers("two")}) as g:
            client = lc.LLMClient(PROVIDER)
            self.assertEqual(self._ask(client, max_attempts=3), "two")
        self.assertEqual(g.calls, [(PRIMARY, False), (SECOND, False)])

    def test_every_rung_dead_raises_the_last_error_and_marks_all_down(self):
        with _Gateway({PRIMARY: _http(503), SECOND: _http(502), THIRD: _dead}) as g:
            client = lc.LLMClient(PROVIDER)
            with self.assertRaises(requests.exceptions.ConnectionError):
                self._ask(client, max_attempts=2)
        self.assertEqual(g.models[:4], [PRIMARY, PRIMARY, SECOND, SECOND])
        for model in (PRIMARY, SECOND, THIRD):
            self.assertTrue(mf.is_down(PROVIDER["api_base"], model), model)

    def test_an_auth_error_is_not_a_reason_to_change_model(self):
        with _Gateway({PRIMARY: _http(401), SECOND: _answers("two")}) as g:
            client = lc.LLMClient(PROVIDER)
            with self.assertRaises(requests.exceptions.HTTPError):
                self._ask(client)
        self.assertEqual(g.models, [PRIMARY])
        self.assertFalse(mf.is_down(PROVIDER["api_base"], PRIMARY))

    def test_a_streamed_request_that_fails_is_not_retried_streamed_again(self):
        with _Gateway({PRIMARY: _http(500), SECOND: _answers("two")}) as g:
            client = lc.LLMClient(PROVIDER)
            self.assertEqual(self._ask(client, stream=True), "two")
        self.assertEqual(g.calls, [(PRIMARY, True), (SECOND, True)])

    def test_without_fallbacks_the_old_retry_policy_stands(self):
        alone = dict(PROVIDER, fallback_models="")
        with _Gateway({PRIMARY: _http(500)}) as g:
            client = lc.LLMClient(alone)
            with self.assertRaises(requests.exceptions.HTTPError):
                self._ask(client, max_attempts=3)
        # Plain, then streamed, then a backoff retry: three attempts, one model.
        self.assertEqual(g.models, [PRIMARY, PRIMARY, PRIMARY])

    def test_the_budget_covers_the_whole_ladder(self):
        clock = {"now": 0.0}
        real = lc.time.monotonic
        lc.time.monotonic = lambda: clock["now"]
        try:
            def slow_fail(payload, stream):
                clock["now"] += 100.0
                raise requests.exceptions.ReadTimeout("no token")

            with _Gateway({PRIMARY: slow_fail, SECOND: _answers("two")}) as g:
                client = lc.LLMClient(PROVIDER)
                with self.assertRaises(requests.exceptions.Timeout):
                    self._ask(client, budget_seconds=90)
            # The pinned model ate the budget; the next rung was never asked.
            self.assertEqual(g.models, [PRIMARY])
        finally:
            lc.time.monotonic = real

    def test_the_tool_loop_asks_the_rung_believed_up(self):
        def with_tools(text):
            def answer(payload, stream):
                self.assertIn("tools", payload)
                return _Plain(text)
            return answer

        with _Gateway({PRIMARY: _http(500), SECOND: with_tools("done")}) as g:
            client = lc.LLMClient(PROVIDER)
            out = client.complete_with_tools([{"role": "user", "content": "q"}],
                                             tools=[{"type": "function"}],
                                             tool_executor=lambda n, a: "")
        self.assertEqual(out, "done")
        self.assertEqual(g.models, [PRIMARY, SECOND])
        self.assertEqual(client.model_used, SECOND)


# ---------------------------------------------------------------------------
# The Agents SDK shim
# ---------------------------------------------------------------------------
class SdkShimFallsBack(unittest.TestCase):

    def setUp(self):
        try:
            import src.classes.AIInterpret.agent as agent
        except Exception as ex:  # noqa: BLE001 -- the SDK is optional locally
            self.skipTest("agents SDK not importable here: %s" % ex)
        self.agent = agent
        self.provider = agent.AI_PROVIDERS[agent.AI_LLM_PROVIDER]
        self._saved = (agent._sdk_configured, agent._MODEL_OBJ,
                       self.provider.get("fallback_models"), self.provider.get("api_key"))
        self.provider["fallback_models"] = "spare/model"
        self.provider["api_key"] = self.provider.get("api_key") or "test-key"
        agent._sdk_configured = False
        agent._MODEL_OBJ = None
        mf.reset()
        agent.configure_sdk()
        self.client = agent._MODEL_OBJ._client
        self.sleeps = []
        self._real_sleep = asyncio.sleep

        async def fake_sleep(d):
            self.sleeps.append(d)
        agent.asyncio.sleep = fake_sleep

    def tearDown(self):
        self.agent.asyncio.sleep = self._real_sleep
        (self.agent._sdk_configured, self.agent._MODEL_OBJ,
         fallbacks, key) = self._saved
        if fallbacks is None:
            self.provider.pop("fallback_models", None)
        else:
            self.provider["fallback_models"] = fallbacks
        self.provider["api_key"] = key
        mf.reset()

    def test_a_500_on_the_pinned_model_switches_at_once(self):
        import openai, httpx
        from src.tests.test_ai_sdk_transport import _FakeStream, _chunk
        seen = []

        async def fake_orig(*args, **kwargs):
            seen.append(kwargs.get("model"))
            if kwargs.get("model") == self.provider["model"]:
                resp = httpx.Response(500, request=httpx.Request("POST", "http://x"))
                raise openai.InternalServerError("backend gone", response=resp, body=None)
            return _FakeStream([_chunk(delta={"content": "ok"}, finish_reason="stop")])
        self.client.chat.completions._pa_orig_create = fake_orig

        done = asyncio.run(self.client.chat.completions.create(
            model=self.provider["model"], messages=[]))
        self.assertEqual(done.choices[0].message.content, "ok")
        self.assertEqual(seen, [self.provider["model"], "spare/model"])
        self.assertEqual(self.sleeps, [], "a different model is not a retry: no wait")
        self.assertTrue(mf.is_down(self.provider["api_base"], self.provider["model"]))

        # The next create goes straight to the spare.
        seen.clear()
        asyncio.run(self.client.chat.completions.create(model=self.provider["model"], messages=[]))
        self.assertEqual(seen, ["spare/model"])

    def test_a_429_stays_on_the_pinned_model(self):
        import openai, httpx
        from src.tests.test_ai_sdk_transport import _FakeStream, _chunk
        seen = []

        async def fake_orig(*args, **kwargs):
            seen.append(kwargs.get("model"))
            if len(seen) == 1:
                resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
                raise openai.RateLimitError("slow down", response=resp, body=None)
            return _FakeStream([_chunk(delta={"content": "ok"}, finish_reason="stop")])
        self.client.chat.completions._pa_orig_create = fake_orig

        asyncio.run(self.client.chat.completions.create(model=self.provider["model"], messages=[]))
        self.assertEqual(seen, [self.provider["model"]] * 2)
        self.assertFalse(mf.is_down(self.provider["api_base"], self.provider["model"]))


# ---------------------------------------------------------------------------
# The probe's verdict
# ---------------------------------------------------------------------------
class ProbeVerdict(unittest.TestCase):

    def _r(self, model, ok, error=None, configured=True):
        return {"ok": ok, "provider": "csic", "host": "llm.example", "model": model,
                "seconds": 0.3, "reply": "OK" if ok else None, "error": error,
                "configured": configured}

    def test_pinned_answers_is_ok(self):
        status, line = gateway.verdict([self._r("a", True), self._r("b", False, "500")])
        self.assertEqual(status, gateway.EXIT_OK)
        self.assertIn("a answered", line)

    def test_only_a_fallback_answers_is_degraded(self):
        status, line = gateway.verdict([self._r("a", False, "HTTP 500"), self._r("b", True)])
        self.assertEqual(status, gateway.EXIT_DEGRADED)
        self.assertIn("a is not answering", line)
        self.assertIn("b is", line)

    def test_nothing_answers_is_fail(self):
        status, line = gateway.verdict([self._r("a", False, "500"), self._r("b", False, "reset")])
        self.assertEqual(status, gateway.EXIT_FAIL)
        self.assertIn("no model answered", line)

    def test_no_key_is_skip(self):
        status, _ = gateway.verdict([self._r("a", False, "no key", configured=False)])
        self.assertEqual(status, gateway.EXIT_SKIP)

    def test_probe_asks_one_model_streamed_without_fallback(self):
        with _Gateway({PRIMARY: _answers("OK"), SECOND: _answers("OK"), THIRD: _dead}) as g:
            results = gateway.probe_ladder(PROVIDER, "csic", timeout=5)
        self.assertEqual([r["model"] for r in results], [PRIMARY, SECOND, THIRD])
        self.assertEqual(g.calls[:2], [(PRIMARY, True), (SECOND, True)])
        self.assertTrue(results[0]["ok"] and results[1]["ok"])
        self.assertFalse(results[2]["ok"])


if __name__ == "__main__":
    unittest.main()
