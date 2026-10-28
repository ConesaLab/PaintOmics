#!/usr/bin/env python3
"""Every caller of one gateway draws on one request budget, bounded per minute.

The CSIC LiteLLM key allows 60 requests a minute (``x-litellm-key-rpm-limit:
60``, measured 2026-10-28). Production set no AI_LLM_MAX_RPM, so nothing paced:
one hsa05219 walk burst its Writers and paper agents past the limit, seven
paper agents gave up on "429 Client Error", and the walk kept no statement.
Even with pacing on, the two transports kept separate budgets -- LLMClient's
token bucket and the SDK shim's _AsyncPacer -- so N set meant up to 2N sent,
and the bucket's minute of stored tokens let 2N through in one rolling minute.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_one_rate_budget_per_gateway
"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("AI_CSIC_API_KEY", "test-key")

from src.classes.AIInterpret import pacing                       # noqa: E402


class _Recording(pacing.Pacer):
    """A pacer on a frozen clock that records each wait instead of sleeping."""

    def __init__(self, rpm):
        pacing.Pacer.__init__(self, rpm, clock=lambda: 1000.0)
        self.seen = []

    def acquire(self):
        self.seen.append(("thread", self.reserve()))

    async def wait(self):
        self.seen.append(("loop", self.reserve()))


class _Response(object):
    """What requests.post returns, as far as LLMClient reads it."""

    def __init__(self, headers=None, content="ok"):
        from requests.structures import CaseInsensitiveDict
        self.headers = CaseInsensitiveDict(headers or {})
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _Case(unittest.TestCase):

    def setUp(self):
        from src.classes.AIInterpret import llm_client
        from src.conf.serverconf import AI_LLM_PROVIDER, AI_PROVIDERS
        self.llm_client = llm_client
        self.provider = AI_PROVIDERS[AI_LLM_PROVIDER]
        self.provider_name = AI_LLM_PROVIDER
        self._saved = (os.environ.get("AI_LLM_MAX_RPM"), llm_client.requests.post,
                       self.provider.get("api_key"), self.provider.get("fallback_models"))
        self.provider["api_key"] = self.provider.get("api_key") or "test-key"
        self.provider["fallback_models"] = ""
        os.environ.pop("AI_LLM_MAX_RPM", None)
        pacing.reset()
        self.headers = {}
        llm_client.requests.post = lambda *a, **k: _Response(self.headers)

    def tearDown(self):
        env, post, key, fallbacks = self._saved
        if env is None:
            os.environ.pop("AI_LLM_MAX_RPM", None)
        else:
            os.environ["AI_LLM_MAX_RPM"] = env
        self.llm_client.requests.post = post
        self.provider["api_key"] = key
        if fallbacks is None:
            self.provider.pop("fallback_models", None)
        else:
            self.provider["fallback_models"] = fallbacks
        pacing.reset()

    def ask(self):
        client = self.llm_client.LLMClient(self.provider, self.provider_name)
        return client.complete([{"role": "user", "content": "hi"}], max_tokens=5)


class RollingWindowTest(unittest.TestCase):

    def test_no_sixty_second_window_holds_more_than_the_ceiling(self):
        clock = {"t": 0.0}
        pacer = pacing.Pacer(60, clock=lambda: clock["t"])
        sent = []
        # A burst of 150 at t=0, an idle spell, then another burst of 150:
        # the token bucket let 60 + 60 through in the first minute.
        for start in (0.0, 1000.0):
            clock["t"] = start
            for _ in range(150):
                sent.append(start + pacer.reserve())
        sent.sort()
        worst = max(sum(1 for s in sent if t <= s < t + 60.0) for t in sent)
        self.assertLessEqual(worst, 60)
        self.assertEqual(worst, 60, "the ceiling is a rate to use, not only to respect")

    def test_waiting_for_a_slot_does_not_block_the_loop(self):
        pacer = pacing.Pacer(600)              # a slot every 0.1 s
        ticks = []

        async def tick():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        async def go():
            pacer.reserve()                    # the next slot is 0.1 s away
            await asyncio.gather(pacer.wait(), tick())
        asyncio.run(go())
        self.assertEqual(len(ticks), 5)
        self.assertLess(ticks[-1] - ticks[0], 0.09, "the loop stood still while the pacer waited")


class SharedBudgetTest(_Case):

    def test_llmclient_and_the_sdk_transport_draw_on_one_budget(self):
        from src.classes.AIInterpret import agent
        os.environ["AI_LLM_MAX_RPM"] = "60"
        shared = pacing._PACERS[pacing._key(self.provider["api_base"])] = _Recording(60)
        self.ask()
        client = agent._build_client()

        async def fake_orig(*args, **kwargs):
            from src.tests.test_ai_sdk_transport import _FakeStream, _chunk
            return _FakeStream([_chunk(delta={"content": "ok"}, finish_reason="stop")])
        client.chat.completions._pa_orig_create = fake_orig
        asyncio.run(client.chat.completions.create(model=self.provider["model"], messages=[]))
        asyncio.run(client.chat.completions.create(model=self.provider["model"], messages=[]))
        self.ask()
        self.assertEqual(shared.seen, [("thread", 0.0), ("loop", 1.0), ("loop", 2.0), ("thread", 3.0)],
                         "the two transports kept budgets of their own")


class CeilingTest(_Case):

    def test_the_ceiling_is_learned_from_the_first_answer(self):
        self.assertIsNone(pacing.pacer_for(self.provider["api_base"]), "paced before anything was learned")
        self.headers = {"X-LiteLLM-Key-RPM-Limit": "60", "x-litellm-key-tpm-limit": "10000000"}
        with self.assertLogs("src.classes.AIInterpret.pacing", "INFO") as logged:
            self.ask()
            self.ask()
        self.assertEqual(pacing.ceiling(self.provider["api_base"]), 54)
        self.assertEqual(pacing.pacer_for(self.provider["api_base"]).rpm, 54)
        self.assertEqual(len([r for r in logged.records if "allows 60 requests/min" in r.getMessage()]), 1,
                         "the learned ceiling is announced once")

    def test_no_header_means_no_pacing(self):
        self.ask()
        self.assertEqual(pacing.ceiling(self.provider["api_base"]), 0)
        self.assertIsNone(pacing.pacer_for(self.provider["api_base"]))

    def test_the_setting_overrides_what_was_learned_and_zero_turns_it_off(self):
        self.headers = {"x-litellm-key-rpm-limit": "60"}
        self.ask()
        os.environ["AI_LLM_MAX_RPM"] = "30"
        self.assertEqual(pacing.pacer_for(self.provider["api_base"]).rpm, 30)
        os.environ["AI_LLM_MAX_RPM"] = "0"
        self.assertIsNone(pacing.pacer_for(self.provider["api_base"]))
        os.environ["AI_LLM_MAX_RPM"] = ""
        self.assertEqual(pacing.pacer_for(self.provider["api_base"]).rpm, 54, "an empty setting is unset")

    def test_a_trailing_slash_is_the_same_gateway(self):
        pacing.learn("http://gw/v1/", {"x-litellm-key-rpm-limit": "100"})
        self.assertIs(pacing.pacer_for("http://gw/v1"), pacing.pacer_for("http://gw/v1/"))
        self.assertEqual(pacing.ceiling("http://gw/v1"), 90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
