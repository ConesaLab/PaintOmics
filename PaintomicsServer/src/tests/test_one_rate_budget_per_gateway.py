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
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("AI_CSIC_API_KEY", "test-key")

from src.classes.AIInterpret import pacing                       # noqa: E402


class _Recording(pacing.Pacer):
    """A pacer on a frozen clock that records each wait instead of sleeping."""

    def __init__(self, rpm):
        pacing.Pacer.__init__(self, rpm, clock=lambda: 1000.0)
        self.seen = []

    def acquire(self, seconds_left=None, floor=0.0, cancel=None):
        self.seen.append(("thread", self.reserve()))
        return True

    async def wait(self, seconds_left=None, floor=0.0):
        self.seen.append(("loop", self.reserve()))
        return True


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

    def test_a_new_ceiling_retunes_the_pacer_and_keeps_the_slots_it_gave(self):
        """Rebuilt on a new ceiling, the pacer forgot the slots its callers
        were sleeping toward, and the next caller went at once beside them."""
        os.environ["AI_LLM_MAX_RPM"] = "6"
        key = pacing._key(self.provider["api_base"])
        pacer = pacing._PACERS[key] = pacing.Pacer(6, clock=lambda: 1000.0)
        for _ in range(3):
            pacer.reserve()                                  # slots at 1000, 1010, 1020
        os.environ["AI_LLM_MAX_RPM"] = "12"
        self.assertIs(pacing.pacer_for(self.provider["api_base"]), pacer)
        self.assertEqual((pacer.rpm, pacer.interval), (12, 5.0))
        self.assertEqual(pacer.reserve(), 30.0, "the next slot is after the ones already handed out")
        self.assertEqual(pacer.reserve(), 35.0, "and the ones after it at the new spacing")

    def test_a_trailing_slash_is_the_same_gateway(self):
        pacing.learn("http://gw/v1/", {"x-litellm-key-rpm-limit": "100"})
        self.assertIs(pacing.pacer_for("http://gw/v1"), pacing.pacer_for("http://gw/v1/"))
        self.assertEqual(pacing.ceiling("http://gw/v1"), 90)



class _Clock(object):
    """A monotonic clock that moves only when something sleeps on it."""

    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class SlotsHandedBackTest(unittest.TestCase):
    """A slot is taken before its caller sleeps. A Writer cancelled at its
    stage's deadline while it waited, or a call that gave its wait up, used to
    keep its slot, and the stages after it (the Narrator) waited out slots
    nobody would send in."""

    def test_cancelled_waiters_hand_their_slots_back(self):
        pacer = pacing.Pacer(6, clock=lambda: 1000.0)       # a slot every 10 s, a clock that stands still
        seen = {}

        async def go():
            waiters = [asyncio.ensure_future(pacer.wait()) for _ in range(5)]
            await asyncio.sleep(0)                           # every one has reserved: 0, 10, 20, 30, 40 s
            for waiter in waiters[2:]:
                waiter.cancel()
            await asyncio.gather(*waiters[2:], return_exceptions=True)
            seen["delay"] = pacer.reserve()
            waiters[1].cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
        asyncio.run(go())
        self.assertEqual(seen["delay"], 50.0 - 3 * 10.0, "the cancelled waiters' slots were never given back")

    def test_a_slot_handed_back_mid_queue_never_puts_two_requests_in_one(self):
        clock = {"t": 0.0}
        pacer = pacing.Pacer(6, clock=lambda: clock["t"])
        held = [pacer._take()[0] for _ in range(4)]          # 0, 10, 20, 30
        pacer.release(held.pop(1))                           # the waiter at 10 s is cancelled
        sent = held + [pacer._take()[0] for _ in range(12)]
        self.assertEqual(len(set(sent)), len(sent), "two requests on one slot")
        worst = max(sum(1 for s in sent if t <= s < t + 60.0) for t in sent)
        self.assertLessEqual(worst, 6)

    def test_a_wait_the_budget_cannot_afford_is_refused_and_its_slot_given_back(self):
        clock = _Clock()
        pacer = pacing.Pacer(6, clock=clock.now)
        for _ in range(3):
            pacer.reserve()                                  # the next slot is 30 s away
        with mock.patch.object(pacing.time, "sleep", clock.sleep):
            self.assertFalse(pacer.acquire(seconds_left=10, floor=3))
            self.assertEqual(clock.slept, [], "it slept into a budget it could not use")
            self.assertTrue(pacer.acquire(seconds_left=40, floor=3))
        self.assertEqual(clock.slept, [30.0], "the refused slot was not the next one taken")

    def test_a_thread_waiting_for_its_slot_wakes_when_its_stage_ends(self):
        import threading
        pacer = pacing.Pacer(6, clock=lambda: 1000.0)
        for _ in range(3):
            pacer.reserve()                                  # 30 s of queue
        stop = threading.Event()
        threading.Timer(0.05, stop.set).start()
        started = time.monotonic()
        self.assertFalse(pacer.acquire(100, 3, cancel=stop))
        self.assertLess(time.monotonic() - started, 5, "slept on toward its slot")
        self.assertEqual(pacer.reserve(), 30.0, "its slot went back")
        self.assertFalse(pacer.acquire(100, 3, cancel=stop), "an ended stage takes no slot at all")
        self.assertEqual(pacer.reserve(), 40.0, "the slot it was offered is the next one given")

    def test_a_slot_open_now_is_never_refused(self):
        pacer = pacing.Pacer(6, clock=lambda: 1000.0)
        self.assertTrue(pacer.acquire(seconds_left=1, floor=3))


class PacedCallKeepsItsBudgetTest(_Case):
    """Pacing is on by default (54/min learned from the key): a call with 10 s
    of budget could sleep 28 s queued behind a walk's fan-out, then post with
    the 10 s timeout computed before it slept."""

    def setUp(self):
        _Case.setUp(self)
        os.environ["AI_LLM_MAX_RPM"] = "6"
        self.clock = _Clock()
        self.pacer = pacing._PACERS[pacing._key(self.provider["api_base"])] = pacing.Pacer(6, clock=self.clock.now)
        for _ in range(3):
            self.pacer.reserve()                             # 30 s of queue ahead of the call
        self.posts = []

        def post(*args, **kwargs):
            self.posts.append(kwargs)
            return _Response(self.headers)
        self.llm_client.requests.post = post

    def ask_within(self, budget):
        client = self.llm_client.LLMClient(self.provider, self.provider_name)
        with mock.patch("time.monotonic", self.clock.now), mock.patch("time.sleep", self.clock.sleep):
            return client.complete([{"role": "user", "content": "hi"}], max_tokens=5, budget_seconds=budget)

    def test_a_wait_past_the_budget_fails_as_out_of_budget_and_sends_nothing(self):
        with self.assertRaises(self.llm_client.requests.exceptions.Timeout) as raised:
            self.ask_within(10)
        self.assertTrue(self.llm_client.ran_out_of_budget(raised.exception))
        self.assertEqual(self.posts, [])
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.pacer.reserve(), 30.0, "the unused slot was kept")

    def test_a_wait_its_stage_ended_is_not_sent_and_gives_its_slot_back(self):
        """A paper agent's thread outlives its Writers' stage: cancelling the
        coroutine left it asleep in the pacer, and it later sent a check
        nobody read, on the key's budget."""
        import threading
        stop = threading.Event()
        stop.set()
        with pacing.stopped_by(stop), self.assertRaises(self.llm_client.requests.exceptions.Timeout) as raised:
            self.ask_within(60)
        self.assertTrue(self.llm_client.ran_out_of_budget(raised.exception))
        self.assertIn("stage ended", str(raised.exception))
        self.assertEqual((self.posts, self.clock.slept), ([], []))
        self.assertEqual(self.pacer.reserve(), 30.0, "the unused slot was kept")
        self.assertIsNone(pacing.current_stop(), "the signal is the block's only")

    def test_the_read_timeout_is_what_is_left_after_the_wait(self):
        self.assertEqual(self.ask_within(60), "ok")
        self.assertEqual(self.clock.slept, [30.0])
        self.assertEqual(self.posts[0]["timeout"], (15, 30.0), "the timeout was computed before the wait")


class SdkCallKeepsTheRunDeadlineTest(_Case):

    def test_a_wait_past_the_run_deadline_is_not_sent(self):
        import openai
        from src.classes.AIInterpret import agent
        os.environ["AI_LLM_MAX_RPM"] = "6"
        pacer = pacing._PACERS[pacing._key(self.provider["api_base"])] = pacing.Pacer(6, clock=lambda: 1000.0)
        for _ in range(3):
            pacer.reserve()                                  # 30 s of queue
        client = agent._build_client()
        sent = []

        async def fake_orig(*args, **kwargs):
            sent.append(kwargs)
            raise AssertionError("sent past the run's deadline")
        client.chat.completions._pa_orig_create = fake_orig

        async def go():
            agent.set_run_deadline(time.time() + 25)         # an attempt needs RETRY_MIN_ATTEMPT_SECONDS (20)
            await client.chat.completions.create(model=self.provider["model"], messages=[])
        with self.assertRaises(openai.APITimeoutError):
            asyncio.run(go())
        self.assertEqual(sent, [])
        self.assertEqual(pacer.reserve(), 30.0, "the unused slot was kept")


if __name__ == "__main__":
    unittest.main(verbosity=2)
