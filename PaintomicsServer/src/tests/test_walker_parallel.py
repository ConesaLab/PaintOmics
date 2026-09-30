#!/usr/bin/env python3
"""The walk with agents in parallel, and the paper agent behind every citation.

Offline: the synthetic organism from walker_fixture, scripted walkers in place
of the model, stub LLM and PubMed clients. What is pinned is the contract
around the models:

  * walks from several seeds at once merge into one chain every leg of which
    a walker's tools accepted, numbered once, with no edge walked twice in one
    direction, and a walker past the deadline is stopped;
  * the chain is split into contiguous parts, one per Writer, that cover it;
  * a citation is kept only when the passage the paper agent returns is in
    the paper's own text, and the page learns which section it came from;
  * the Writers share one numbered paper list and their statements are
    numbered once across the walk;
  * the walker's words (cluster, the walk, jumped to) never reach a statement
    or the Results;
  * every model call after the walk gives up by its stage's deadline: a
    paper agent rate-limited near the Writers' deadline once held the
    pipeline 54 s past it, and the walk sealed with no Results section.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_parallel
"""
import asyncio
import json
import os
import shutil
import sys
import time
import types
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import literature                 # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod         # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod          # noqa: E402
from src.classes.AIInterpret.walker import parallel                   # noqa: E402
from src.classes.AIInterpret.walker import policies                   # noqa: E402
from src.classes.AIInterpret.walker import record as record_mod       # noqa: E402
from src.classes.AIInterpret.walker import verify                     # noqa: E402
from src.classes.AIInterpret.walker import writer as writer_mod       # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for    # noqa: E402
from src.tests import walker_fixture as fx                            # noqa: E402

PLAN = dict(parallel.PLANS["pathway"], seed_steps=(2, 4), walkers=2)


class Clock(object):
    """A clock the test moves, standing in for a module's `time`."""

    def __init__(self, t=10000.0):
        self.t = t

    def time(self):
        return self.t


@contextmanager
def patched(*triples):
    """Set (object, attribute, value) for the block, then put each back."""
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in triples]
    try:
        for obj, name, value in triples:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


class ScriptedRunner(object):
    """The planner and the per-seed walkers without a model: every relevant
    node is a candidate, and each walker takes the hottest open neighbour."""

    def __init__(self, pause=0.0):
        self.pause = pause
        self.running, self.most = 0, 0

    async def plan(self, walker):
        walker.params["sep"] = 0
        walker.scan("graph")
        seeds = [r["id"] for r in walker.ranked if r["candidate"]][:3]
        walker.plan_walk(seeds, max(walker.params["min_steps"], 4 * len(seeds)), "scripted plan")

    async def segment(self, walker, opening, others, max_turns):
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            while not walker.done:
                await asyncio.sleep(self.pause)
                pick = policies._pick_greedy(walker.neighbour_rows()) if walker.budget["steps"] > 0 else None
                if pick is None:
                    walker.stop("", "neighbourhood read")
                    break
                walker.step(pick["id"], "Gene expression: %s" % walker.overlay.layer_text(pick["id"]).split("\n")[0],
                            "the hottest relevant neighbour")
                if walker.budget["notes"] > 0 and len(walker.chain) == 1:
                    walker.note("first leg of this seed")
        finally:
            self.running -= 1


class _Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.net = net_mod.build_network("tst", cls.data_dir)
        cls.ov = ov_mod.overlay_job(cls.net, fx.make_job())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def walk(self, runner=None, seconds=60):
        planner = Walker(self.net, self.ov, "network", params_for("network"))
        runner = runner or ScriptedRunner()
        merged = asyncio.run(parallel.walk_in_parallel(planner, "card", PLAN, time.time() + seconds,
                                                       runner=runner))
        return merged, runner


class MergeTest(_Fixture):
    def test_seeds_walked_at_once_merge_into_one_legal_chain(self):
        merged, runner = self.walk()
        chain = merged.record()["chain"]
        self.assertGreaterEqual(len(merged.segments), 2, "the fixture should give at least two walked seeds")
        self.assertEqual([leg["n"] for leg in chain], list(range(1, len(chain) + 1)))
        steps = [(leg["from"], leg["to"]) for leg in chain if leg["kind"] == "step"]
        self.assertEqual(len(steps), len(set(steps)), "an edge was walked twice in one direction")
        for src, dst in steps:
            self.assertIsNotNone(self.net.edge_between(src, dst)[0], "a step leg has no edge")
        for before, segment in zip(merged.segments, merged.segments[1:]):
            join = chain[segment["first"] - 2]
            self.assertEqual(join["kind"], "jump")
            self.assertEqual(join["to"], segment["seed"])
            self.assertEqual(before["last"] + 1, segment["first"] - 1)
        for note in merged.notes:
            self.assertTrue(1 <= note["after_leg"] <= len(chain))
        self.assertTrue(merged.done)
        self.assertLessEqual(runner.most, PLAN["walkers"])
        self.assertGreater(runner.most, 1, "the seeds were walked one after another, not at once")

    def test_a_walker_past_the_deadline_is_stopped_at_its_next_turn(self):
        merged, _runner = self.walk(ScriptedRunner(pause=0.2), seconds=0.3)
        self.assertTrue(merged.done)
        self.assertIn("time budget", merged.stop_reason)

    def test_a_planner_that_failed_on_the_gateway_leaves_an_error_not_a_walk(self):
        class _Dead(ScriptedRunner):
            async def plan(self, walker):
                walker.loop_error = "RuntimeError: 401 Malformed API Key"

        merged, _runner = self.walk(_Dead())
        self.assertEqual(merged.chain, [])
        self.assertIn("Malformed", merged.loop_error)

    def test_the_parts_cover_the_chain_in_order(self):
        seg = lambda *bounds: [{"first": f, "last": l} for f, l in bounds]   # noqa: E731
        parts = parallel.writer_parts(seg((1, 6), (8, 12), (14, 20), (22, 23), (25, 40)), 40, 6)
        self.assertEqual(parts[0][0], 1)
        self.assertEqual(parts[-1][1], 40)
        for a, b in zip(parts, parts[1:]):
            self.assertEqual(a[1] + 1, b[0])
        self.assertLessEqual(len(parts), 6)
        self.assertEqual(parallel.writer_parts(seg((1, 60)), 60, 6),
                         [(1, 10), (11, 20), (21, 30), (31, 40), (41, 50), (51, 60)])
        self.assertEqual(parallel.writer_parts(seg((1, 3)), 3, 6), [(1, 3)])
        self.assertEqual(parallel.writer_parts([], 0, 6), [])


class _Client(object):
    """The paper agent's gateway: answers from a table keyed by claim."""

    def __init__(self, answers):
        self.answers, self.calls, self.budgets = answers, 0, []

    def complete_json(self, messages, name, schema, parser, max_tokens=0, temperature=0.0, budget_seconds=None):
        self.calls += 1
        self.budgets.append(budget_seconds)
        claim = messages[1]["content"].split("\n")[1]
        answer = self.answers.get(claim)
        if isinstance(answer, Exception):
            raise answer
        return answer or {"supported": False, "section": "", "quote": "", "note": "nothing on this"}


class _PubMed(object):
    def __init__(self, abstract="Aaa is a kinase of the test pathway."):
        self.fetched, self.abstract = [], abstract

    def fetch_papers(self, pmids):
        self.fetched.extend(pmids)
        return [{"pmid": pmids[0], "abstract": self.abstract,
                 "sections": {"abstract": self.abstract,
                              "results": "We show that Aaa “phosphorylates” Bbb   in stimulated cells, "
                                         "which drives its degradation.",
                              "discussion": "Together these data place Aaa upstream of Bbb."}}]


class PaperAgentTest(unittest.TestCase):
    SECTIONS = [("abstract", "Aaa is a kinase of the test pathway."),
                ("results", "We show that Aaa “phosphorylates” Bbb   in stimulated cells, which drives its degradation.")]

    def test_a_passage_is_found_whatever_its_quotes_and_spaces(self):
        def section(quote):
            found = literature.find_passage(quote, self.SECTIONS)
            return found[0] if found else None
        self.assertEqual(section('We show that Aaa "phosphorylates" Bbb in stimulated cells'), "results")
        self.assertEqual(section("We show that Aaa “phosphorylates” ... which drives its degradation"), "results")
        self.assertIsNone(section("Aaa phosphorylates Ccc in resting cells, which stabilises it"),
                          "a passage that is not in the paper")
        self.assertIsNone(section("Aaa is a kinase"), "too short to be evidence")

    def test_the_reader_gets_whole_sentences_as_the_paper_words_them(self):
        sections = [("abstract", "Background. The ITPR1 gene encodes the inositol 1,4,5-trisphosphate (IP3) "
                                 "receptor, a calcium channel. Other results follow.")]
        self.assertEqual(literature.find_passage("The ITPR1 gene encodes the inositol 1,4,5-trisphosphate (IP",
                                                 sections),
                         ("abstract", "The ITPR1 gene encodes the inositol 1,4,5-trisphosphate (IP3) receptor, "
                                      "a calcium channel."))

    def test_pubmed_titles_and_abstracts_keep_their_inline_markup_text(self):
        from src.classes.AIInterpret.pubmed_client import PubMedClient
        xml = ("<PubmedArticleSet><PubmedArticle><PMID>1</PMID><ArticleTitle>Inositol Polyphosphate Multikinase "
               "(<i>IPMK</i>) in ageing</ArticleTitle><Abstract><AbstractText>The (IP<sub>3</sub>) receptor "
               "releases calcium.</AbstractText></Abstract></PubmedArticle></PubmedArticleSet>")
        paper = PubMedClient()._parse_xml(xml)[0]
        self.assertEqual(paper["title"], "Inositol Polyphosphate Multikinase (IPMK) in ageing")
        self.assertEqual(paper["abstract"], "The (IP3) receptor releases calcium.")

    def check(self, store, client, claim, pubmed=None):
        async def go():
            return await literature.check_citation(store, client, pubmed or _PubMed(), 1, claim,
                                                   asyncio.Semaphore(2), time.time() + 60)
        return asyncio.run(go())

    def store(self):
        store = literature.LiteratureStore()
        store.add({"pmid": "111", "title": "Aaa phosphorylates Bbb", "abstract": "Aaa is a kinase of the test pathway."})
        return store

    def test_a_citation_is_kept_only_with_its_passage_from_the_paper(self):
        claim = "Aaa phosphorylates Bbb and drives its degradation"
        found = {"supported": True, "section": "results",
                 "quote": "We show that Aaa “phosphorylates” Bbb in stimulated cells, which drives its degradation.",
                 "note": "states it"}
        store, pubmed = self.store(), _PubMed()
        client = _Client({claim: found})
        verdict = self.check(store, client, claim, pubmed)
        self.assertTrue(verdict["supported"])
        self.assertEqual(verdict["section"], "results")
        self.assertEqual(verdict["quote"], "We show that Aaa “phosphorylates” Bbb in stimulated cells, "
                                           "which drives its degradation.", "the passage is the paper's own words")
        self.assertTrue(verdict["full_text"])
        self.assertEqual(self.check(store, client, claim, pubmed), verdict)
        self.assertEqual(client.calls, 1, "the same citation was checked twice")
        self.assertEqual(pubmed.fetched, ["111"], "the full text was fetched more than once")

        invented = "Aaa activates Ccc"
        client.answers[invented] = {"supported": True, "section": "results",
                                    "quote": "Aaa activates Ccc in every cell type we examined so far.", "note": ""}
        verdict = self.check(store, client, invented)
        self.assertFalse(verdict["supported"])
        self.assertIn("not in the paper", verdict["why"])

    def test_a_check_that_could_not_run_is_asked_again(self):
        store = self.store()
        client = _Client({"Aaa binds Ddd": RuntimeError("502")})
        self.assertFalse(self.check(store, client, "Aaa binds Ddd")["supported"])
        self.assertIsNone(store.verdict(1, "Aaa binds Ddd"), "a gateway error was cached as a verdict")


class _Throttled(object):
    """requests.Response double: a 429 asking for a long wait."""
    status_code, text = 429, "rate limited"

    def __init__(self, retry_after):
        self.headers = {"Retry-After": str(retry_after)}

    def raise_for_status(self):
        import requests
        err = requests.exceptions.HTTPError("HTTP 429")
        err.response = self
        raise err


class _Budgets(object):
    """A gateway that records the budget of every call and answers nothing."""

    def __init__(self):
        self.budgets = []

    def complete_json(self, messages, name, schema, parser, max_tokens=0, temperature=0.0, budget_seconds=None):
        self.budgets.append(budget_seconds)
        return {}


class StageBudgetTest(unittest.TestCase):
    def test_a_rate_limited_paper_agent_gives_up_by_the_writers_deadline(self):
        # The live failure: the paper agent's call runs on a thread that
        # asyncio.run waits for, and a 429 asked it to sleep 84 s just before
        # the Writers' deadline. With the real client and its real retry loop,
        # no wait may run past the deadline.
        from src.classes.AIInterpret import llm_client as lc
        sleeps, real_post, real_sleep = [], lc.requests.post, lc.time.sleep
        lc.requests.post = lambda *args, **kwargs: _Throttled(80)
        lc.time.sleep = sleeps.append
        try:
            client = lc.LLMClient({"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
                                   "fallback_models": []})
            store = PaperAgentTest.store(None)

            async def go():
                return await literature.check_citation(store, client, _PubMed(), 1, "Aaa is a kinase",
                                                       asyncio.Semaphore(1), time.time() + 60)
            verdict = asyncio.run(go())
        finally:
            lc.requests.post, lc.time.sleep = real_post, real_sleep
        self.assertFalse([s for s in sleeps if s >= 60],
                         "a retry slept past the Writers' deadline: %s" % sleeps)
        self.assertFalse(verdict["supported"])
        self.assertTrue(verdict.get("transient"), "a check the gateway refused is not a verdict")

    def test_the_pipeline_does_not_wait_for_a_thread_past_its_deadline(self):
        # A thread cannot be cancelled, and asyncio.run's cleanup waits for it:
        # whatever a paper agent or a PubMed fetch is still doing when the
        # Writers' deadline passes, the sense check and the Narrator start on time.
        import contextvars
        run = contextvars.ContextVar("run", default=None)
        seen = []

        def straggler():
            seen.append(run.get())
            time.sleep(3)

        async def pipeline():
            thread = asyncio.ensure_future(asyncio.to_thread(straggler))
            await parallel._wait([thread], time.time() + 0.2, None)
            return "sealed"

        run.set("this run")
        started = time.time()
        self.assertEqual(parallel.run_pipeline(pipeline()), "sealed")
        self.assertLess(time.time() - started, 1.5, "the pipeline waited for a thread past its deadline")
        self.assertEqual(seen, ["this run"], "the run's context (its deadline) did not reach the thread")

    def test_the_results_floor_follows_the_statements_kept(self):
        # A walk that kept 6 statements was refused a 368-word Results section
        # against the plan's 400: the floor asked for padding, the page got none.
        from src.classes.AIInterpret.walker import service

        class Brief(_Budgets):
            def complete_json(self, messages, *args, **kwargs):
                self.briefs = getattr(self, "briefs", []) + [messages[0]["content"]]
                return {}

        def length_rule(n, words):
            client = Brief()
            statements = [{"n": i + 1, "claim": "c%d" % i} for i in range(n)]
            service._narrate(client, "card", "chain", statements, [], None, {}, "pathway:x", {"gates": {}}, words)
            return [line for line in client.briefs[0].splitlines() if line.startswith("- Length")][0]

        self.assertEqual(length_rule(6, (400, 1200)), "- Length 300 to 1200 words.")
        self.assertEqual(length_rule(10, (400, 1200)), "- Length 400 to 1200 words.",
                         "a full set of statements keeps the plan's floor")

    def test_the_paper_agent_is_given_the_time_left(self):
        client = _Client({})
        PaperAgentTest.check(None, PaperAgentTest.store(None), client, "Aaa is a kinase")
        self.assertEqual(len(client.budgets), 1)
        self.assertTrue(50 < client.budgets[0] <= 60, client.budgets)

    def test_the_sense_check_leaves_the_narrator_its_time_and_the_narrator_stops_at_the_run_end(self):
        from src.classes.AIInterpret.walker import service
        client = _Budgets()
        service._sense_pass(client, "card", "chain", [{"n": 1, "claim": "c"}], [], None, None, set(), {},
                            deadline=time.time() + 40)
        self.assertEqual(len(client.budgets), 1)
        self.assertTrue(30 < client.budgets[0] <= 40, client.budgets)

        client = _Budgets()
        checks = {"gates": {}}
        service._narrate(client, "card", "chain", [{"n": 1, "claim": "c"}], [], None, {}, "pathway:x", checks,
                         (100, 200), deadline=time.time() + 30)
        self.assertTrue(client.budgets and all(b is not None and 20 < b <= 30 for b in client.budgets),
                        client.budgets)

        from src.classes.AIInterpret.walker import narrate as narrate_mod
        client = _Budgets()
        with self.assertRaises(narrate_mod.OutOfTime):
            narrate_mod.narrate(client, "card", [{"n": 1}], "chain", "", deadline=time.time() - 1)
        self.assertEqual(client.budgets, [], "the Narrator was asked after the run was due")

    def test_a_narrator_that_ran_out_of_time_is_not_reported_as_failing_its_checks(self):
        # The page shows "ran out of time" only for a reason with "time budget"
        # in it; a Narrator that gave up at its deadline used to record "no
        # results", which the page reads as a section that failed its checks.
        import requests
        from src.classes.AIInterpret.walker import service

        class Failing(object):
            def __init__(self, exc):
                self.exc = exc

            def complete_json(self, *args, **kwargs):
                raise self.exc

        def reason(exc, deadline):
            checks = {"gates": {}}
            out = service._narrate(Failing(exc), "card", "chain", [{"n": 1, "claim": "c"}], [], None, {},
                                   "pathway:x", checks, (100, 200), deadline=deadline)
            self.assertIsNone(out)
            return checks["results"]

        def http_error(status, retry_after=2, gave_up=False):
            err = requests.exceptions.HTTPError("HTTP %d" % status)
            err.response = _Throttled(retry_after)
            err.response.status_code = status
            if gave_up:
                err.gave_up_at_deadline = True    # LLMClient: the budget ended the call
            return err

        budget_timeout = requests.exceptions.Timeout("exceeded its budget")
        budget_timeout.gave_up_at_deadline = True
        soon = time.time() + 30
        self.assertEqual(reason(budget_timeout, soon), [service.RESULTS_OUT_OF_TIME])
        self.assertEqual(reason(http_error(429, 80, gave_up=True), soon), [service.RESULTS_OUT_OF_TIME])
        self.assertEqual(reason(http_error(429), time.time() + 400), [service.RESULTS_RATE_LIMITED],
                         "a gateway that kept refusing is neither the clock nor a failed check")
        self.assertEqual(reason(http_error(500), time.time() + 400), [service.RESULTS_NO_ANSWER],
                         "a server error is not rate limiting")
        self.assertEqual(reason(requests.exceptions.Timeout("gateway hung"), time.time() + 400),
                         [service.RESULTS_NO_ANSWER], "a gateway's own timeout with time left is not the clock")
        self.assertEqual(reason(requests.exceptions.Timeout("slow"), None), [service.RESULTS_NO_ANSWER],
                         "without a deadline a timeout is the gateway's, not the run's")

        class Unreadable(object):
            def complete_json(self, *args, **kwargs):
                return {}
        checks = {"gates": {}}
        service._narrate(Unreadable(), "card", "chain", [{"n": 1, "claim": "c"}], [], None, {}, "pathway:x", checks,
                         (100, 200), deadline=soon)
        self.assertEqual(checks["results"], ["no results"], "an unreadable answer is an answer, not a silent AI")

    def test_the_client_marks_only_the_wait_the_budget_refused(self):
        # The mark narrate reads: set when the retry wait would pass the
        # deadline, absent when the retries simply ran out with time left.
        from src.classes.AIInterpret import llm_client as lc
        real_post, real_sleep = lc.requests.post, lc.time.sleep
        lc.time.sleep = lambda seconds: None
        client = lc.LLMClient({"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
                               "fallback_models": []})
        try:
            for retry_after, budget, marked in ((80, 30, True), (2, 400, False)):
                lc.requests.post = lambda *args, **kwargs: _Throttled(retry_after)
                with self.assertRaises(lc.requests.exceptions.HTTPError) as raised:
                    client.complete([{"role": "user", "content": "x"}], budget_seconds=budget)
                self.assertEqual(bool(getattr(raised.exception, "gave_up_at_deadline", False)), marked,
                                 "Retry-After %s under a %s s budget" % (retry_after, budget))
        finally:
            lc.requests.post, lc.time.sleep = real_post, real_sleep

    def test_a_throttled_narrator_is_reported_as_rate_limited_on_the_real_client(self):
        # Every request answered 429 with a short Retry-After: the retries fit
        # the budget, run out, and the page must not blame the checks.
        from src.classes.AIInterpret import llm_client as lc
        from src.classes.AIInterpret.walker import service
        real_post, real_sleep = lc.requests.post, lc.time.sleep
        lc.requests.post = lambda *args, **kwargs: _Throttled(2)
        lc.time.sleep = lambda seconds: None
        try:
            client = lc.LLMClient({"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
                                   "fallback_models": []})
            checks = {"gates": {}}
            service._narrate(client, "card", "chain", [{"n": 1, "claim": "c"}], [], None, {}, "pathway:x", checks,
                             (100, 200), deadline=time.time() + 400)
        finally:
            lc.requests.post, lc.time.sleep = real_post, real_sleep
        self.assertEqual(checks["results"], [service.RESULTS_RATE_LIMITED])

    def test_a_call_with_no_time_to_answer_is_not_sent(self):
        # Flooring the budget at 1 s sent calls that could only time out, each
        # pushing the stage further past its deadline; 2.5 s is under the
        # minimum a call is sent with, and over the old floor.
        from src.classes.AIInterpret.walker import service
        for left in (1, 2.5):
            client = _Budgets()
            checks = {}
            service._sense_pass(client, "card", "chain", [{"n": 1, "claim": "c"}], [], None, None, set(), checks,
                                deadline=time.time() + left)
            self.assertEqual(client.budgets, [], "the sense check was asked with %s s left" % left)
            self.assertEqual(checks["sense"], "unavailable")

    def test_a_rewrite_with_no_time_left_is_not_sent(self):
        from src.classes.AIInterpret.walker import service
        sent = []
        walker = types.SimpleNamespace(record=lambda: {"chain": []}, overlay=None)
        store = types.SimpleNamespace(papers={}, verdict=lambda *a: None)
        with patched((service, "rewrite_once", lambda *a, **k: sent.append(k) or {}),
                     (service.verify, "verify_statement", lambda *a, **k: [])):
            service._rewrite_and_recheck(None, "card", "chain", [{"n": 1, "claim": "c"}],
                                         {1: {"direction": "x"}}, walker, store, set(),
                                         deadline=time.time() + 2.5)
        self.assertEqual(sent, [], "the rewrite was sent with no time to answer")

    def test_a_sense_recheck_with_no_time_left_is_not_sent_and_the_statement_drops(self):
        from src.classes.AIInterpret.walker import service
        clock = Clock()
        asked = []
        failing = {f: {"ok": True, "note": ""} for f in service.sense_mod.FIELDS}
        failing["timing"] = {"ok": False, "note": "no timing"}

        def sense_check(client, card, statements, chain, budget_seconds=None):
            asked.append(budget_seconds)
            clock.t += 8
            return {s["n"]: dict(failing) for s in statements}

        statements, dropped = [{"n": 1, "claim": "c"}], []
        with patched((service, "time", clock),
                     (service.sense_mod, "sense_check", sense_check),
                     (service, "_rewrite_and_recheck", lambda client, card, chain, bad, *a, **k: {
                         s["n"]: [] for s in bad})):
            service._sense_pass(None, "card", "chain", statements, dropped, None, None, set(), {},
                                deadline=clock.t + 10)
        self.assertEqual(len(asked), 1, "the re-check was sent with no time to answer")
        self.assertEqual([d["n"] for d in dropped], [1])

    CISH = "Cish is a reporter: its fall reports the pathway down"

    def recheck(self, again, first=None):
        """_direction_pass on a stub check: the first pass (`first`, by default
        Cish objecting and Pten not) is sent back, the re-check returns
        `again`; the rewrite answers nothing. Returns the kept and dropped
        statements and the deadline the rewrite was given."""
        from src.classes.AIInterpret.walker import direction as direction_mod
        from src.classes.AIInterpret.walker import service
        first = first or [{"gene": "Cish", "consistent": False, "insensitive": False, "objections": [self.CISH]},
                          {"gene": "Pten", "consistent": True, "insensitive": False}]
        calls, rewrites = [], []

        def check(client, statements, walker, deadline=None):
            calls.append(deadline)
            if len(calls) == 1:
                return {1: first}, {1: [o for v in first for o in v.get("objections", [])]}
            return {1: again}, {}

        def rewrite(client, card, chain, failing, *args, **kwargs):
            rewrites.append(kwargs.get("deadline"))
            return {s["n"]: [] for s in failing}

        statements, dropped, checks = [{"n": 1, "claim": "c"}], [], {"gates": {}}
        deadline = time.time() + 30
        with patched((direction_mod, "direction_check", check), (service, "_rewrite_and_recheck", rewrite)):
            service._direction_pass(None, "card", "chain", statements, dropped, None, None, set(), checks,
                                    deadline=deadline)
        self.assertEqual(calls, [deadline, deadline], "a direction check ran without the stage's deadline")
        self.assertEqual(rewrites, [deadline], "the rewrite ran without the stage's deadline")
        return statements, dropped

    def test_a_statement_the_direction_recheck_did_not_answer_for_is_dropped(self):
        # Sent back for contradicting Cish, and the re-check got no answer for
        # Cish -- no time, or none from the AI service: the objection stands.
        from src.classes.AIInterpret.walker import direction as direction_mod
        for reason in (direction_mod.UNCHECKED_OUT_OF_TIME, direction_mod.UNCHECKED_NO_ANSWER):
            statements, dropped = self.recheck([{"gene": "Pten", "consistent": True, "insensitive": False},
                                                {"gene": "Cish", "consistent": True, "insensitive": False, "unchecked": reason}])
            self.assertEqual(statements, [], reason)
            self.assertIn("Cish was not checked again: " + reason, dropped[0]["why"])
            self.assertIn("Cish is a reporter", dropped[0]["why"], "the objection that stands was lost")

    def test_a_recheck_that_cleared_the_objection_keeps_the_statement(self):
        # Cish answered and agreed; Pten, which never objected, went unanswered.
        from src.classes.AIInterpret.walker import direction as direction_mod
        statements, dropped = self.recheck([{"gene": "Cish", "consistent": True, "insensitive": False},
                                            {"gene": "Pten", "consistent": True, "insensitive": False,
                                             "unchecked": direction_mod.UNCHECKED_OUT_OF_TIME}])
        self.assertEqual([s["n"] for s in statements], [1])
        self.assertEqual(dropped, [])

    def test_an_insensitivity_objection_stands_until_the_recheck_answers(self):
        from src.classes.AIInterpret.walker import direction as direction_mod
        reads_the_same = "the statement reads the same with Cish's values reversed"
        statements, dropped = self.recheck(
            [{"gene": "Cish", "consistent": True, "insensitive": False, "unchecked": direction_mod.UNCHECKED_OUT_OF_TIME}],
            first=[{"gene": "Cish", "consistent": True, "insensitive": True, "objections": [reads_the_same]}])
        self.assertEqual(statements, [], "an unanswered re-check cleared an insensitivity objection")
        self.assertIn(reads_the_same, dropped[0]["why"])

    def test_the_drop_restates_only_the_unanswered_regulators_objection(self):
        # Cbl and Cblb both object; the re-check clears Cblb and leaves Cbl
        # unanswered. "Cbl" is in "Cblb": the reason must not restate Cblb's.
        from src.classes.AIInterpret.walker import direction as direction_mod
        cbl = "Cbl is an upstream inhibitor of RTK: its fall reports the pathway up, not down"
        cblb = "Cblb is an upstream inhibitor of TCR: its fall reports the pathway up, not down"
        statements, dropped = self.recheck(
            [{"gene": "Cbl", "consistent": True, "insensitive": False, "unchecked": direction_mod.UNCHECKED_NO_ANSWER},
             {"gene": "Cblb", "consistent": True, "insensitive": False}],
            first=[{"gene": "Cbl", "consistent": False, "insensitive": False, "objections": [cbl]},
                   {"gene": "Cblb", "consistent": False, "insensitive": False, "objections": [cblb]}])
        self.assertEqual(statements, [])
        self.assertIn(cbl, dropped[0]["why"])
        self.assertNotIn("Cblb", dropped[0]["why"], "the re-check cleared Cblb's objection")

    def test_a_slot_that_frees_too_late_is_not_used_for_a_call(self):
        # The paper agent checks the time before waiting for a slot; one that
        # frees with under a call's worth left must not send a doomed call.
        clock = Clock()

        class LateSlot(object):
            async def __aenter__(self):
                clock.t += 24

            async def __aexit__(self, *exc):
                return False

        client = _Client({})
        with patched((literature, "time", clock)):
            verdict = asyncio.run(literature.check_citation(
                PaperAgentTest.store(None), client, _PubMed(), 1, "Aaa is a kinase", LateSlot(), clock.t + 26))
        self.assertEqual(client.budgets, [], "a call was sent with %s s left" % 2)
        self.assertTrue(verdict.get("transient"))


class BudgetEdgesTest(unittest.TestCase):
    """Where the budget ends: decided by the clock, never below a call's worth
    of time, and never blamed for what the gateway did with time to spare."""

    def client(self):
        from src.classes.AIInterpret import llm_client as lc
        return lc.LLMClient({"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
                             "fallback_models": []})

    def test_a_gateway_timeout_with_time_left_is_not_the_budget(self):
        from src.classes.AIInterpret import llm_client as lc

        def hung(*args, **kwargs):
            raise lc.requests.exceptions.ConnectTimeout("gateway unreachable")
        with patched((lc.requests, "post", hung), (lc.time, "sleep", lambda seconds: None)):
            for budget, marked in ((400, False), (2, True)):
                with self.assertRaises(lc.requests.exceptions.Timeout) as raised:
                    self.client().complete([{"role": "user", "content": "x"}], budget_seconds=budget)
                self.assertEqual(lc.ran_out_of_budget(raised.exception), marked, "budget %s" % budget)

    def test_a_backoff_that_leaves_no_time_for_the_retry_is_refused(self):
        # A wait ending 0.5 s before the deadline used to be slept through,
        # and the retry sent with half a second to answer.
        from src.classes.AIInterpret import llm_client as lc
        sleeps = []
        with patched((lc.requests, "post", lambda *a, **k: _Throttled(9)), (lc.time, "sleep", sleeps.append),
                     (lc.random, "uniform", lambda a, b: 0.0)):
            with self.assertRaises(lc.requests.exceptions.HTTPError) as raised:
                self.client().complete([{"role": "user", "content": "x"}], budget_seconds=10)
        self.assertEqual(sleeps, [], "slept into the last seconds of the budget")
        self.assertTrue(lc.ran_out_of_budget(raised.exception))

    def fake_clock(self):
        clock = types.SimpleNamespace(t=1000.0)
        clock.monotonic = clock.time = lambda: clock.t
        clock.sleep = lambda seconds: setattr(clock, "t", clock.t + seconds)
        return clock

    def response(self, status, retry_after=None):
        response = _Throttled(retry_after if retry_after is not None else 0)
        response.status_code = status
        if retry_after is None:
            response.headers = {}
        return response

    def test_a_rung_that_fails_with_under_a_calls_worth_left_ends_the_call(self):
        # Model m answers 404 after 28 s of a 30 s budget: no second rung is
        # sent a 2 s call, the error carries the clock's mark -- and m is
        # marked down all the same, because the gateway said it is not served.
        from src.classes.AIInterpret import llm_client as lc
        from src.classes.AIInterpret import model_fallback
        clock, posts = self.fake_clock(), []

        def post(url, json=None, **kwargs):
            posts.append(json["model"])
            clock.t += 28
            return self.response(404)
        client = lc.LLMClient({"api_base": "https://gw.example/v1", "api_key": "k", "model": "m",
                               "fallback_models": ["m2"]})
        model_fallback.reset()
        try:
            with patched((lc.requests, "post", post), (lc, "time", clock), (model_fallback, "time", clock)):
                with self.assertRaises(lc.requests.exceptions.HTTPError) as raised:
                    client.complete([{"role": "user", "content": "x"}], budget_seconds=30)
                down = model_fallback.is_down("https://gw.example/v1", "m")
        finally:
            model_fallback.reset()
        self.assertEqual(posts, ["m"], "a rung was sent a call with 2 s left")
        self.assertTrue(lc.ran_out_of_budget(raised.exception))
        self.assertTrue(down, "a model the gateway reported as not served was not marked down")

    def test_a_timeout_the_budget_cut_short_does_not_mark_the_model_down(self):
        from src.classes.AIInterpret import llm_client as lc
        from src.classes.AIInterpret import model_fallback
        clock = self.fake_clock()

        def post(url, json=None, **kwargs):
            clock.t += kwargs["timeout"][1]            # the read runs to the budget
            raise lc.requests.exceptions.ReadTimeout("read timed out")
        model_fallback.reset()
        try:
            with patched((lc.requests, "post", post), (lc, "time", clock), (model_fallback, "time", clock)):
                with self.assertRaises(lc.requests.exceptions.Timeout) as raised:
                    self.client().complete([{"role": "user", "content": "x"}], budget_seconds=20)
                down = model_fallback.is_down("https://gateway.example/v1", "m")
        finally:
            model_fallback.reset()
        self.assertTrue(lc.ran_out_of_budget(raised.exception))
        self.assertFalse(down, "the budget, not the model, ended the call")

    def test_a_last_retry_that_fails_with_under_a_calls_worth_left_is_the_budgets(self):
        # 429 on every attempt; the waits fit, and the last answer comes with
        # 2 s left: the clock decides, so the Narrator reports running out of
        # time rather than rate limiting.
        from src.classes.AIInterpret import llm_client as lc
        clock = self.fake_clock()

        def post(url, json=None, **kwargs):
            clock.t += 5 if clock.t < 1024 else 3
            return self.response(429, 2)
        with patched((lc.requests, "post", post), (lc, "time", clock), (lc.random, "uniform", lambda a, b: 0.0)):
            with self.assertRaises(lc.requests.exceptions.HTTPError) as raised:
                self.client().complete([{"role": "user", "content": "x"}], budget_seconds=30)
        self.assertLess(1030 - clock.t, lc.MIN_CALL_SECONDS)
        self.assertTrue(lc.ran_out_of_budget(raised.exception))

    def test_the_immediate_retries_obey_the_call_minimum(self):
        # A 500 (retried streamed) or a 400 to a schema (retried without it)
        # after 8.5 s of a 10 s budget: the retry would have 1.5 s. Not sent.
        from src.classes.AIInterpret import llm_client as lc
        for status in (500, 400):
            clock, posts = self.fake_clock(), []

            def post(url, json=None, **kwargs):
                posts.append(kwargs["timeout"])
                clock.t += 8.5
                return self.response(status)
            client = self.client()
            with patched((lc.requests, "post", post), (lc, "time", clock),
                         (lc.LLMClient, "supports_schema", lambda self: True),
                         (lc.LLMClient, "_demote_schema", lambda self: None)):
                with self.assertRaises(lc.requests.exceptions.HTTPError) as raised:
                    client.complete([{"role": "user", "content": "x"}], budget_seconds=10,
                                    response_format={"type": "json_object"})
            self.assertEqual(len(posts), 1, "HTTP %d: a retry went out with 1.5 s left" % status)
            self.assertTrue(lc.ran_out_of_budget(raised.exception))

    def test_rewrite_once_forwards_its_budget(self):
        from src.classes.AIInterpret.walker import service
        client = _Budgets()
        service.rewrite_once(client, "card", "chain", [{"n": 1, "claim": "c"}],
                             {1: {"direction": {"ok": False, "note": "x"}}}, budget_seconds=12.5)
        self.assertEqual(client.budgets, [12.5])

    def test_the_sense_rewrite_gets_the_stage_deadline(self):
        from src.classes.AIInterpret.walker import service
        failing = {f: {"ok": True, "note": ""} for f in service.sense_mod.FIELDS}
        failing["timing"] = {"ok": False, "note": "no timing"}
        seen, deadline = [], time.time() + 30
        with patched((service.sense_mod, "sense_check", lambda client, card, statements, chain, budget_seconds=None: {
                          s["n"]: dict(failing) for s in statements}),
                     (service, "_rewrite_and_recheck", lambda client, card, chain, bad, *a, **k: seen.append(
                         k.get("deadline")) or {s["n"]: [] for s in bad})):
            service._sense_pass(None, "card", "chain", [{"n": 1, "claim": "c"}], [], None, None, set(), {},
                                deadline=deadline)
        self.assertEqual(seen, [deadline])

    def test_a_paper_agent_call_that_waited_for_a_thread_gets_only_the_time_left(self):
        # PubMed fetches share the executor; a call queued behind them starts
        # late, and its budget is read when it starts, not when it was queued.
        clock = Clock()
        real_to_thread = asyncio.to_thread

        async def late_thread(fn, *args, **kwargs):
            if not isinstance(getattr(fn, "__self__", None), _PubMed):
                clock.t += 24                          # the paper agent waited 24 s for a free worker
            return await real_to_thread(fn, *args, **kwargs)
        client = _Client({})
        with patched((literature, "time", clock), (literature.asyncio, "to_thread", late_thread)):
            verdict = asyncio.run(literature.check_citation(
                PaperAgentTest.store(None), client, _PubMed(), 1, "Aaa is a kinase", asyncio.Semaphore(1),
                clock.t + 26))
        self.assertEqual(client.budgets, [], "a call was sent with 2 s left")
        self.assertTrue(verdict.get("transient"))

    def test_the_narrator_is_not_asked_with_under_a_calls_worth(self):
        from src.classes.AIInterpret.walker import narrate as narrate_mod
        client = _Budgets()
        with self.assertRaises(narrate_mod.OutOfTime):
            narrate_mod.narrate(client, "card", [{"n": 1}], "chain", "", deadline=time.time() + 2.5)
        self.assertEqual(client.budgets, [])

    def test_an_unreadable_second_answer_is_not_blamed_on_the_first_failure(self):
        import requests
        from src.classes.AIInterpret.walker import narrate as narrate_mod

        class Client(object):
            calls = 0

            def complete_json(self, *args, **kwargs):
                Client.calls += 1
                if Client.calls == 1:
                    err = requests.exceptions.HTTPError("HTTP 429")
                    err.response = _Throttled(2)
                    raise err
                return {}
        self.assertIsNone(narrate_mod.narrate(Client(), "card", [{"n": 1}], "chain", "",
                                              deadline=time.time() + 400))

    def test_the_rewrite_and_the_sense_recheck_are_given_the_time_left(self):
        from src.classes.AIInterpret.walker import service
        sent = []
        walker = types.SimpleNamespace(record=lambda: {"chain": []}, overlay=None)
        store = types.SimpleNamespace(papers={}, verdict=lambda *a: None)
        with patched((service, "rewrite_once", lambda *a, **k: sent.append(k["budget_seconds"]) or {}),
                     (service.verify, "verify_statement", lambda *a, **k: [])):
            service._rewrite_and_recheck(None, "card", "chain", [{"n": 1, "claim": "c"}], {1: {"direction": "x"}},
                                         walker, store, set(), deadline=time.time() + 30)
        self.assertTrue(len(sent) == 1 and 20 < sent[0] <= 30, sent)

        clock = Clock()
        asked = []
        failing = {f: {"ok": True, "note": ""} for f in service.sense_mod.FIELDS}
        failing["timing"] = {"ok": False, "note": "no timing"}

        def sense_check(client, card, statements, chain, budget_seconds=None):
            asked.append(budget_seconds)
            clock.t += 1
            return {s["n"]: dict(failing) for s in statements}
        with patched((service, "time", clock), (service.sense_mod, "sense_check", sense_check),
                     (service, "_rewrite_and_recheck", lambda client, card, chain, bad, *a, **k: {
                         s["n"]: [] for s in bad})):
            service._sense_pass(None, "card", "chain", [{"n": 1, "claim": "c"}], [], None, None, set(), {},
                                deadline=clock.t + 30)
        self.assertEqual(asked, [30, 29], "the sense check and its re-check each get the time left")


class NarratorRepairTest(_Fixture):
    def test_a_repair_the_ai_service_refused_keeps_the_draft_and_its_failed_checks(self):
        # The draft was written and failed its checks; the repair was refused.
        # The page must say the section failed, not that none was written.
        import requests
        from src.classes.AIInterpret.walker import service
        walker, _ = self.walk()

        class Client(object):
            calls = 0

            def complete_json(self, *args, **kwargs):
                Client.calls += 1
                if Client.calls == 1:
                    return {"title": "Results", "summary": "s.", "paragraphs": [{"text": "Too short.", "legs": [1]}]}
                err = requests.exceptions.HTTPError("HTTP 429")
                err.response = _Throttled(2)
                raise err
        checks = {"gates": {}}
        out = service._narrate(Client(), "card", "chain", [{"n": 1, "claim": "c", "prose": "p", "legs": [1]}], [],
                               walker, {}, "pathway:x", checks, (100, 200), deadline=time.time() + 400)
        self.assertIsNone(out)
        self.assertIsNotNone(checks.get("results_dropped"))
        self.assertEqual(checks["results"][-1],
                         "the repair was not made: the AI service was rate-limiting the Narrator's requests")
        self.assertFalse([r for r in checks["results"] if r.startswith("no Results section: ")],
                         "a draft that was written is not 'no Results section'")


class NarratorRepairCasesTest(_Fixture):
    """The Narrator's one repair: given the run's deadline, refused or out of
    time without losing the draft's own verdict, and marked when made."""
    STATEMENT = [{"n": 1, "claim": "c", "prose": "p", "legs": [1]}]
    SHORT = {"title": "Results", "summary": "s.", "paragraphs": [{"text": "Too short.", "legs": [1]}]}

    def narrate(self, answers, patches=()):
        import requests
        from src.classes.AIInterpret.walker import service
        walker, _ = self.walk()
        budgets = []

        class Client(object):
            def complete_json(self, *args, **kwargs):
                budgets.append(kwargs.get("budget_seconds"))
                answer = answers[min(len(budgets), len(answers)) - 1]
                if isinstance(answer, requests.exceptions.RequestException):
                    raise answer
                return answer
        checks = {"gates": {}}
        with patched(*patches):
            out = service._narrate(Client(), "card", "chain", self.STATEMENT, [], walker, {}, "pathway:x", checks,
                                   (100, 200), deadline=time.time() + 400)
        return out, checks, budgets

    def throttled(self, retry_after=2, gave_up=False):
        import requests
        err = requests.exceptions.HTTPError("HTTP 429")
        err.response = _Throttled(retry_after)
        if gave_up:
            err.gave_up_at_deadline = True
        return err

    def test_the_repair_is_given_the_run_deadline(self):
        out, checks, budgets = self.narrate([self.SHORT, {}])
        self.assertGreater(len(budgets), 1, "no repair was asked")
        self.assertTrue(all(b is not None and b <= 400 for b in budgets), budgets)

    def test_a_repair_that_ran_out_of_time_keeps_the_drafts_objections(self):
        out, checks, _ = self.narrate([self.SHORT, self.throttled(80, gave_up=True)])
        self.assertIsNone(out)
        self.assertIsNotNone(checks.get("results_dropped"))
        self.assertEqual(checks["results"][-1], "the repair was not made: the time budget was spent")

    def test_a_title_only_draft_keeps_its_section_when_the_repair_is_refused(self):
        from src.classes.AIInterpret.walker import service
        titles = iter([["the title claims a mechanism"]] + [[]] * 10)
        draft = {"title": "X activates Y", "summary": "s.",
                 "paragraphs": [{"text": "Body.", "legs": [1], "from_statement": 1}]}
        out, checks, _ = self.narrate(
            [draft, self.throttled()],
            [(service.verify, "verify_results", lambda *a, **k: []),
             (service.tiers, "title_outruns_body", lambda *a, **k: next(titles)),
             (service.tiers, "drop_mechanistic_sentences", lambda results: 0)])
        self.assertIsNotNone(out, "a draft whose only fault was its title lost its section: %s" % checks.get("results"))
        self.assertTrue(checks["gates"]["title"].get("fallback"))

    def test_a_repaired_section_says_it_was_rewritten(self):
        from src.classes.AIInterpret.walker import service
        verdicts = iter([["paragraph 1 is too short"], [], [], []])
        draft = {"title": "Results", "summary": "s.",
                 "paragraphs": [{"text": "Body.", "legs": [1], "from_statement": 1}]}
        out, checks, _ = self.narrate(
            [draft],
            [(service.verify, "verify_results", lambda *a, **k: next(verdicts)),
             (service.tiers, "title_outruns_body", lambda *a, **k: [])])
        self.assertIsNotNone(out)
        self.assertTrue(checks["gates"]["title"]["rewritten"])


class PipelineCleanupTest(unittest.TestCase):
    """run_pipeline returns at once, yet leaves nothing half-unwound."""

    def test_a_task_left_pending_is_cancelled_not_awaited(self):
        async def pipeline():
            asyncio.ensure_future(asyncio.sleep(3))
            return "sealed"
        started = time.time()
        self.assertEqual(parallel.run_pipeline(pipeline()), "sealed")
        self.assertLess(time.time() - started, 1.5)

    def test_a_cancelled_task_finishes_unwinding(self):
        cleaned = []

        async def straggler():
            try:
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(0.01)          # an unwind of more than one step (closing a stream)
                cleaned.append(True)

        async def pipeline():
            asyncio.ensure_future(straggler())
            await asyncio.sleep(0)
            return "sealed"
        parallel.run_pipeline(pipeline())
        self.assertEqual(cleaned, [True])

    def test_an_unfinished_async_generator_is_closed(self):
        closed = []

        async def gen():
            try:
                yield 1
                yield 2
            finally:
                closed.append(True)

        async def pipeline():
            agen = gen()
            await agen.__anext__()
            pipeline.agen = agen                   # alive past the loop
            return "sealed"
        parallel.run_pipeline(pipeline())
        self.assertEqual(closed, [True])

    def test_the_loop_is_closed(self):
        loops = []

        async def pipeline():
            loops.append(asyncio.get_running_loop())
            return "sealed"
        parallel.run_pipeline(pipeline())
        self.assertTrue(loops[0].is_closed())


class ResultsNoteWiringTest(unittest.TestCase):
    """The note under a missing Results section names "Walk again" only where
    that button is drawn: the Step 4 column for a user who may edit, never
    the AI panel, which has no such control."""
    VIEWS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../PaintomicsClient/public_html/app/view/"
                                                                    "PathwayAcquisitionViews"))

    def source(self, name):
        with open(os.path.join(self.VIEWS, name), encoding="utf-8") as handle:
            return handle.read()

    def test_the_hint_follows_the_button(self):
        walk = self.source("PA_Step4WalkView.js")
        self.assertIn('(rerun ? "; Walk again to try for one." : ".")', walk)
        self.assertIn("var rerun = !!(options && options.canRerun);", walk)
        self.assertIn("paWalkResultsNode(view, {onLeg: onLeg, canRerun: this.canEdit()})", walk)
        self.assertIn("if (this.canEdit()) {\n\t\t\tvar again = paWalkEl(\"a\", \"button btn-default pa-walk-again\"",
                      walk, "the button and the hint no longer share one condition")
        self.assertEqual(walk.count("Walk again to try for one"), 1, "the hint is built once, under canRerun")
        self.assertEqual(walk.count("+ standAlone"), 3, "every no-section note ends with the same rerun hint")
        panel = self.source("PA_AIInterpretView.js")
        calls = [line for line in panel.splitlines() if "paWalkResultsNode(" in line and "typeof" not in line]
        self.assertTrue(calls)
        self.assertFalse([line for line in calls if "canRerun" in line], "the AI panel has no Walk again button")

    def test_the_notes_read_the_servers_reasons(self):
        from src.classes.AIInterpret.walker import service
        walk = self.source("PA_Step4WalkView.js")
        for reason, pattern in ((service.RESULTS_OUT_OF_TIME, "/^no Results section: .*time budget/"),
                                (service.RESULTS_RATE_LIMITED, "/^no Results section: .*rate-limiting/"),
                                (service.RESULTS_NO_ANSWER, "/^no Results section: .*did not answer/")):
            self.assertIn("said(%s)" % pattern, walk)
            self.assertRegex(reason, pattern.strip("/"))
        # The regulators the direction check did not answer for say so.
        self.assertIn('" not checked" : ""', walk)
        self.assertIn('" · not checked: " + String(verdict.unchecked)', walk)


class ModelWalkStagesTest(unittest.TestCase):
    """The gates between the stages after the Writers, on a clock the test
    moves: each stage runs only on time it was given, and a stage that uses
    all of its budget still leaves the next one its turn."""

    def walk(self, left_after_writers, sense_overrun=1.0):
        from src.classes.AIInterpret import agent
        from src.classes.AIInterpret.walker import service
        clock = Clock()
        run_deadline = clock.t + parallel.plan_for("network")["run_seconds"]
        seen = {"direction": [], "sense": [], "narrate": 0}
        walker = types.SimpleNamespace(params={}, anchor=None, record=lambda: {"chain": []}, overlay=None)
        store = types.SimpleNamespace(papers={}, checks=0, trace=[])
        context = types.SimpleNamespace(loop_error=None, trace=[], legs=[], read=set())
        statements = [{"n": 1, "claim": "c", "prose": "p", "legs": []}]

        def run_pipeline(coro):
            coro.close()
            clock.t = run_deadline - left_after_writers
            return walker, (statements, [], store, [context])

        def direction_pass(*args, **kwargs):
            seen["direction"].append(kwargs["deadline"])
            args[8]["gates"]["direction"] = {"pass": True}

        def sense_pass(*args, **kwargs):
            seen["sense"].append(kwargs["deadline"])
            clock.t = kwargs["deadline"] + sense_overrun      # a call cut at its budget returns a little late

        def narrate(*args, **kwargs):
            seen["narrate"] += 1

        checks = {"gates": {}}
        with patched((parallel, "run_pipeline", run_pipeline),
                     (agent, "set_run_deadline", lambda when: None),
                     (writer_mod, "chain_text", lambda w: "chain"),
                     (service, "time", clock),
                     (service, "_direction_pass", direction_pass),
                     (service, "_sense_pass", sense_pass),
                     (service, "_narrate", narrate)):
            service._model_walk(walker, "network", {}, "card", None, True, lambda *a, **k: None,
                                lambda: None, lambda: False, {}, checks)
        return seen, checks

    def test_the_direction_stage_is_skipped_when_its_own_deadline_has_passed(self):
        seen, checks = self.walk(left_after_writers=105)
        self.assertEqual(seen["direction"], [], "the direction check ran past its own deadline")
        self.assertIn("time budget", checks["gates"]["direction"]["why"])

    def test_a_sense_call_cut_at_its_budget_still_leaves_the_narrator_its_turn(self):
        from src.classes.AIInterpret.walker import service
        for overrun in (1.0, 4.0):
            seen, checks = self.walk(left_after_writers=200, sense_overrun=overrun)
            self.assertEqual(len(seen["sense"]), 1)
            self.assertEqual(seen["narrate"], 1, "the Narrator was skipped after a %s s overrun: %s"
                             % (overrun, checks.get("results")))
        run_deadline = 10000.0 + parallel.plan_for("network")["run_seconds"]
        self.assertEqual(seen["direction"], [run_deadline - service.SENSE_MIN_SECONDS - service.NARRATE_MIN_SECONDS],
                         "the direction check was given time kept for the sense check or the Narrator")
        seen, checks = self.walk(left_after_writers=200, sense_overrun=4.5)
        self.assertEqual(seen["narrate"], 1, "a 4.5 s overrun, inside the 5 s slack, skipped the Narrator")

    def test_the_direction_stage_needs_a_calls_worth_past_its_own_deadline(self):
        from src.classes.AIInterpret.walker import service
        seen, checks = self.walk(left_after_writers=service.SENSE_MIN_SECONDS + service.NARRATE_MIN_SECONDS + 2)
        self.assertEqual(seen["direction"], [])
        self.assertIn("time budget", checks["gates"]["direction"]["why"])

    def test_a_skipped_narrator_is_reported_as_out_of_time(self):
        from src.classes.AIInterpret.walker import service
        seen, checks = self.walk(left_after_writers=40)
        self.assertEqual(seen["narrate"], 0)
        self.assertEqual(checks["results"], [service.RESULTS_OUT_OF_TIME])


class WriterTest(_Fixture):
    def setUp(self):
        self.merged, _ = self.walk()
        chain = self.merged.record()["chain"]
        self.leg = next(leg for leg in chain if leg["kind"] == "step" and self.ov.r.get(leg["to"]))
        self.label = self.merged.label(self.leg["to"])
        self.omic = next(layer["omic"] for layer in self.ov.layers[self.leg["to"]] if layer["relevant"])

    def statement(self, claim_beyond, paper=1):
        return {"claim": "%s rises" % self.label, "prose": "%s rises [%d]." % (self.label, paper),
                "cites": [[self.label, self.omic]], "legs": [self.leg["n"]], "grounded_in": [],
                "beyond": [{"claim": claim_beyond, "paper": paper, "hypothesis": False}], "papers": [paper]}

    def context(self, answers):
        store = literature.LiteratureStore()
        store.add({"pmid": "111", "title": "%s in the test pathway" % self.label,
                   "abstract": "%s is a kinase of the test pathway." % self.label})
        abstract = "%s is a kinase of the test pathway that marks stimulated cells." % self.label
        c = writer_mod.WriterContext(walker=self.merged, card="card", pubmed=_PubMed(abstract), store=store,
                                     client=_Client(answers), count=(1, 4), deadline=time.time() + 60)
        c.read.add(1)
        return c

    def test_an_unsupported_citation_goes_back_to_the_writer_and_a_supported_one_keeps_its_passage(self):
        good = "%s is a kinase of the test pathway" % self.label
        c = self.context({good: {"supported": True, "section": "abstract",
                                 "quote": "%s is a kinase of the test pathway that marks stimulated cells." % self.label,
                                 "note": ""}})
        answer = asyncio.run(writer_mod.submit(c, json.dumps([self.statement(good), self.statement("it cures cancer")])))
        self.assertIn("does not support", answer)
        self.assertEqual(len(c.last_passing), 1)
        evidence = c.last_passing[0]["evidence"]
        self.assertEqual([(e["ref"], e["section"]) for e in evidence], [(1, "abstract")])
        self.assertFalse(c.done)

    def test_the_writers_share_one_paper_list_and_number_statements_once(self):
        async def write_one(c):
            ref, _ = c.store.add({"pmid": "111", "title": "%s in the test pathway" % self.label,
                                  "abstract": "%s is a kinase of the test pathway." % self.label})
            c.read.add(ref)
            first = c.legs[0]
            leg = next(l for l in self.merged.record()["chain"] if l["n"] >= first and l["kind"] == "step")
            label = self.merged.label(leg["to"])
            layer = self.ov.layers[leg["to"]][0]["omic"]
            stmt = {"claim": "%s moves" % label, "prose": "%s moves, not relevant" % label,
                    "cites": [[label, layer]], "legs": [leg["n"]], "grounded_in": [], "beyond": [], "papers": []}
            await writer_mod.submit(c, json.dumps([stmt]))

        plan = dict(PLAN, statements=(1, 3))
        kept, dropped, store, contexts = asyncio.run(parallel.write_in_parallel(
            self.merged, "card", _PubMed(), None, plan, time.time() + 60, write_one=write_one))
        self.assertGreaterEqual(len(contexts), 1)
        self.assertEqual(len(store.papers), 1, "two Writers numbered the same paper twice")
        self.assertEqual([s["n"] for s in kept], list(range(1, len(kept) + 1)))
        covered = [c.legs for c in contexts]
        self.assertEqual(covered[0][0], 1)
        self.assertEqual(covered[-1][1], len(self.merged.chain))

    def test_a_writer_is_never_asked_for_more_papers_than_statements(self):
        """A one-part walk once briefed its only Writer "3 to 5 statements,
        citing about 24 papers" -- the whole network quota. On default/llm
        (2026-09-30) that Writer searched 48 times in 30 turns and never
        submitted, and the walk failed with MaxTurnsExceeded."""
        async def write_one(c):
            return None

        def briefed(plan, parts):
            real = parallel.writer_parts
            parallel.writer_parts = lambda segments, n_legs, _parts: parts
            try:
                return asyncio.run(parallel.write_in_parallel(
                    self.merged, "card", _PubMed(), None, plan, time.time() + 60, write_one=write_one))[3]
            finally:
                parallel.writer_parts = real

        network = dict(parallel.PLANS["network"], writers=6)
        n = len(self.merged.chain)
        one = briefed(network, [(1, n)])
        self.assertEqual([c.citations for c in one], [network["statements"][1]])
        # The split the plan is sized for keeps its share: 24 papers over 6 parts.
        six = briefed(network, [(i, i) for i in range(1, 7)])
        self.assertEqual([c.citations for c in six], [4] * 6)

    def test_a_writer_that_never_submits_is_asked_to_when_its_turns_run_out(self):
        """default/llm (2026-09-30) searched and read for all 30 turns of every
        Writer and never submitted, so every walk failed with MaxTurnsExceeded
        and lost what it had read. The conversation now goes on without tools:
        the Writer answers with its statements, code submits them, and the
        Verifier's objections come back until it accepts."""
        from agents.items import ModelResponse
        from agents.models.interface import Model
        from agents.usage import Usage
        from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

        layer = self.ov.layers[self.leg["to"]][0]["omic"]
        passing = [{"claim": "%s moves" % self.label, "prose": "%s moves, not relevant" % self.label,
                    "cites": [[self.label, layer]], "legs": [self.leg["n"]], "grounded_in": [],
                    "beyond": [], "papers": []}]
        # The walker's own word: the Verifier objects, and the Writer must answer again.
        objected = [dict(passing[0], claim="The %s cluster moves" % self.label)]

        class Stubborn(Model):
            """Searches on every turn it has tools for. Without them it answers
            as the gateway let default/llm answer (2026-09-30): with tool calls
            it was not offered -- check_my_citations on a draft that is no
            array, then on one carrying a statement the Verifier objects to,
            submit_statements with the same -- and at last with text, inside a
            fence and a sentence, carrying one it accepts."""
            def __init__(self):
                self.calls = []

            async def get_response(self, *_args, **kwargs):          # the SDK passes keywords
                tools = kwargs["tools"]
                self.calls.append({"tools": sorted(t.name for t in tools), "input": kwargs["input"]})
                n = len(self.calls)
                if tools:
                    item = ResponseFunctionToolCall(
                        arguments=json.dumps({"query": "q%d" % n, "topic_tag": "t"}), call_id="c%d" % n,
                        name="search_literature", type="function_call", id="f%d" % n)
                elif sum(1 for call in self.calls if not call["tools"]) < 4:
                    name, args = [("check_my_citations", {"draft": "x"}),
                                  ("check_my_citations", {"draft": json.dumps(objected)}),
                                  ("submit_statements", {"statements_json": json.dumps(objected)})][
                        sum(1 for call in self.calls if not call["tools"]) - 1]
                    item = ResponseFunctionToolCall(arguments=json.dumps(args), call_id="c%d" % n, name=name,
                                                    type="function_call", id="f%d" % n)
                else:
                    text = "Here are my statements:\n```json\n%s\n```" % json.dumps(passing)
                    item = ResponseOutputMessage(
                        id="m%d" % n, role="assistant", status="completed", type="message",
                        content=[ResponseOutputText(text=text, type="output_text", annotations=[])])
                return ModelResponse(output=[item], usage=Usage(), response_id=None)

            def stream_response(self, *args, **kwargs):
                raise NotImplementedError

        model = Stubborn()
        c = writer_mod.WriterContext(walker=self.merged, card="card", pubmed=_PubMed(), count=(1, 4),
                                     legs=(1, len(self.merged.chain)), deadline=time.time() + 60)
        asyncio.run(writer_mod.run_writer_async(c, max_turns=3, model=model))

        self.assertTrue(c.done, c.loop_error)
        self.assertIsNone(c.loop_error)
        self.assertEqual(len(c.kept), 1)
        self.assertEqual(c.submits, 3, "a draft with no array is no submission; a draft or a submit "
                                       "call with one is")
        self.assertEqual([bool(call["tools"]) for call in model.calls], [True] * 3 + [False] * 4)
        # The same conversation, not a fresh one: the three searches are in it,
        # then the request for the statements.
        finish = model.calls[3]["input"]
        self.assertEqual([item.get("name") for item in finish if item.get("type") == "function_call"],
                         ["search_literature"] * 3)
        self.assertEqual(finish[-1], {"role": "user", "content": writer_mod.FINISH_NOW})
        # An answer with no array is asked again; an objection comes back to be fixed.
        self.assertIn("needs a JSON array", str(model.calls[4]["input"][-1].get("content")))
        self.assertIn("The Verifier objects", str(model.calls[5]["input"][-1].get("content")))
        self.assertIn("The Verifier objects", str(model.calls[6]["input"][-1].get("content")))



class WordingAndRecordTest(_Fixture):
    def test_the_walkers_words_never_reach_the_reader(self):
        merged, _ = self.walk()
        leg = merged.record()["chain"][0]
        label = merged.label(leg["to"])
        stmt = {"claim": "The %s cluster is induced" % label, "prose": "%s rises" % label, "cites": [],
                "legs": [1], "grounded_in": [], "beyond": [], "papers": []}
        self.assertTrue(any("cluster" in p for p in verify.verify_statement(stmt, merged, {})))
        results = {"title": "Results", "summary": "The walk then jumped to Foxo1. Foxo1 rose.", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": "The calcium cluster read led to Syk via a jump. Syk rose, a jump at 12h."}]}
        self.assertEqual(verify.drop_jargon_sentences(results), 2)
        self.assertEqual(results["summary"], "Foxo1 rose.")
        self.assertEqual(results["paragraphs"][0]["text"], "Syk rose, a jump at 12h.")
        # Seen in sealed walks on 2026-09-15: the Narrator's summary and a
        # Writer's statement narrated the walk without saying "the walk".
        biology = ("Its seed region pairs with the Prkcz 3' UTR. Seed storage proteins accumulate late. "
                   "Seed miRNAs regulate dormancy. Kinesin-1 walks along the microtubule. "
                   "Myosin V walked from one actin filament to the next. Clusterin (Clu) rose late. "
                   "Metagene clustering grouped the clustered genes. The walkway protein is unrelated.")
        results = {"title": "Results", "paragraphs": [], "summary": (
            "A network walk from the seed miRNA miR-151-3p reveals a repression module. "
            "The seed miRNA mmu-miR-3074-1-3p falls. " + biology)}
        self.assertEqual(verify.drop_jargon_sentences(results), 2)
        self.assertEqual(results["summary"], biology,
                         "a miRNA's seed region, a plant's seeds and a walking motor protein are biology")

    def test_renumbering_carries_each_passage_to_its_new_reference(self):
        from src.classes.AIInterpret.walker import service
        statements = [{"n": 1, "claim": "c [7]", "prose": "p", "papers": [7],
                       "evidence": [{"ref": 7, "claim": "c", "quote": "the passage", "section": "results"},
                                    {"ref": 9, "claim": "d", "quote": "uncited", "section": "abstract"}]}]
        papers = record_mod.renumber_citations(statements, [], None, {7: {"pmid": "77"}, 9: {"pmid": "99"}})
        service.attach_evidence(statements, papers)
        self.assertEqual(list(papers), [1])
        self.assertEqual(statements[0]["evidence"], [{"ref": 1, "claim": "c", "quote": "the passage", "section": "results"}])
        self.assertEqual(papers[1]["evidence"][0]["quote"], "the passage")
        self.assertEqual(papers[1]["evidence"][0]["statement"], 1)


if __name__ == "__main__":
    unittest.main()
