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
import unittest

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

        throttled = requests.exceptions.HTTPError("HTTP 429")
        throttled.response = _Throttled(80)
        soon = time.time() + 30
        self.assertEqual(reason(requests.exceptions.Timeout("exceeded its budget"), soon), [service.RESULTS_OUT_OF_TIME])
        self.assertEqual(reason(throttled, soon), [service.RESULTS_OUT_OF_TIME])
        self.assertEqual(reason(ValueError("unreadable"), soon), ["no results"],
                         "a broken answer is not the clock running out")
        self.assertEqual(reason(requests.exceptions.Timeout("slow"), None), ["no results"],
                         "without a deadline a timeout is the gateway's, not the run's")


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
