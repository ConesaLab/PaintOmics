#!/usr/bin/env python3
"""Check 3, direction logic: a falling feedback reporter reports its pathway
down and a falling true inhibitor reports it up; a statement claiming the
opposite is objected to, one that reads the same with the values reversed is
flagged, and the gate sums it up. The model is a stub. Offline."""
import os
import shutil
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import direction                # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod      # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod       # noqa: E402
from src.classes.AIInterpret.walker import policies                # noqa: E402
from src.classes.AIInterpret.walker import regulators              # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                         # noqa: E402


class StubClient(object):
    """Answers the claim call from a script and the fit call by comparing the
    sign the statement names with the sign of the values it is shown."""

    def __init__(self, claimed):
        self.claimed = claimed
        self.calls = []

    def complete_json(self, messages, name, schema, fallback, max_tokens=0, temperature=0.0, budget_seconds=None):
        self.calls.append(name)
        self.budgets = getattr(self, "budgets", []) + [budget_seconds]
        user = messages[1]["content"]
        if name == "direction_claim":
            return {"claimed": self.claimed, "quote": "the words"}
        if name == "direction_fit":
            # the statement says "fell" or "rose"; the values shown carry the sign of the largest one
            values = user.split("VALUES OF")[1].split("\n", 1)[1].split("\n\nSTATEMENT")[0]
            statement = user.split("STATEMENT\nclaim: ", 1)[1].split("\nprose:")[0].lower()
            shown_negative = "\u22122" in values or "-2" in values
            says_fell = "fell" in statement or "suppress" in statement
            says_rose = "rose" in statement or "induc" in statement
            if "reads the same" in statement:
                return {"consistent": True, "note": "vague"}
            if says_fell and not says_rose:
                return {"consistent": shown_negative, "note": "fell vs values"}
            if says_rose and not says_fell:
                return {"consistent": not shown_negative, "note": "rose vs values"}
            return {"consistent": True, "note": "no direction"}
        raise AssertionError(name)


class Clock(object):
    """A clock the test moves: every model call can cost seconds of it."""

    def __init__(self, t=10000.0):
        self.t = t

    def time(self):
        return self.t


class SlowStub(StubClient):
    """StubClient whose calls each take `costs` seconds of `clock`."""

    def __init__(self, claimed, clock, costs):
        StubClient.__init__(self, claimed)
        self.clock, self.costs = clock, list(costs)

    def complete_json(self, *args, **kwargs):
        out = StubClient.complete_json(self, *args, **kwargs)
        self.clock.t += self.costs.pop(0) if self.costs else 0
        return out


class Throttled(object):
    """requests.Response double: a 429 asking for a long wait."""
    status_code, text, headers = 429, "rate limited", {"Retry-After": "84"}

    def raise_for_status(self):
        import requests
        err = requests.exceptions.HTTPError("HTTP 429")
        err.response = self
        raise err


class DirectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        net = net_mod.build_network("tst", cls.data_dir)
        cls.graph = net.filter_pathway("KEGG:tst00001")
        cls.feedback = next(r for r in regulators.PANEL if r["class"] == "feedback")
        cls.inhibitor = next(r for r in regulators.PANEL if r["class"] == "inhibitor")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def walker_with(self, node_id, symbol, values):
        """The fixture walked, with one chain node renamed to a panel gene and
        given the values (its Gene expression layer)."""
        job = fx.make_job()
        ov = ov_mod.overlay_job(self.graph, job)
        walker = policies.greedy(Walker(self.graph, ov, "KEGG:tst00001", params_for("pathway")))
        self.assertIn(node_id, {n for leg in walker.chain for n in (leg.src, leg.dst)})
        self.graph.nodes[node_id]["label"] = symbol
        layer = ov.layers[node_id][0]
        layer["member"], layer["values"], layer["relevant"] = symbol, values, True
        layer["text"] = ov_mod.values_text(values, ["0h", "2h", "6h"])
        return walker

    def statement(self, symbol, prose):
        return {"n": 1, "claim": prose, "prose": prose, "cites": [[symbol, "Gene expression"]], "legs": [1]}

    def test_data_direction_and_panel_hits(self):
        self.assertEqual(direction.data_direction({"values": [0.1, -2.0, 0.5]}), -1)
        self.assertEqual(direction.data_direction({"values": [0.1, 2.0, "x", None]}), 1)
        self.assertEqual(direction.data_direction({"values": []}), 0)
        walker = self.walker_with("g:2", self.feedback["symbol"], [-0.1, -2.0, -1.5])
        hits = direction.panel_hits(self.statement(self.feedback["symbol"], "it fell"), walker)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["row"]["class"], "feedback")
        self.assertEqual(hits[0]["sign"], -1)
        self.assertEqual(direction.panel_hits(self.statement("Foxo1", "x"), walker), [])
        self.assertEqual(direction.flipped_text("0h +0.10 · 2h −2.00 · 6h −1.50"), "0h −0.10 · 2h +2.00 · 6h +1.50")

    def test_a_falling_reporter_read_as_derepression_is_objected(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        stmt = self.statement(symbol, "%s fell to −2.00, derepressing the pathway" % symbol)
        client = StubClient("up")
        verdicts, objections = direction.direction_check(client, [stmt], walker)
        verdict = verdicts[1][0]
        self.assertEqual((verdict["implied"], verdict["claimed"]), ("down", "up"))
        self.assertFalse(verdict["consistent"])
        self.assertEqual(len(objections[1]), 1)
        self.assertIn("reports the pathway down, not up", objections[1][0])
        gate = direction.gate(verdicts, 0)
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["checked"], 1)
        self.assertIn("claims up", gate["why"])

    def test_a_falling_reporter_read_as_pathway_down_passes(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        stmt = self.statement(symbol, "%s fell to −2.00, so the pathway is less active" % symbol)
        verdicts, objections = direction.direction_check(StubClient("down"), [stmt], walker)
        self.assertEqual(objections, {})
        self.assertTrue(verdicts[1][0]["consistent"])
        self.assertTrue(verdicts[1][0]["fits"])
        self.assertFalse(verdicts[1][0]["fits_flipped"])
        gate = direction.gate(verdicts, 0)
        self.assertTrue(gate["pass"])
        self.assertEqual(gate["consistent"], 1)

    def test_a_falling_inhibitor_reads_the_other_way(self):
        symbol = self.inhibitor["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        stmt = self.statement(symbol, "%s fell to −2.00, releasing the brake on the pathway" % symbol)
        verdicts, objections = direction.direction_check(StubClient("up"), [stmt], walker)
        self.assertEqual(verdicts[1][0]["implied"], "up")
        self.assertEqual(objections, {})
        verdicts, objections = direction.direction_check(StubClient("down"), [stmt], walker)
        self.assertIn("reports the pathway up, not down", objections[1][0])

    def test_a_right_direction_the_values_do_not_support_is_named_as_such(self):
        # claimed and implied agree (down), but the statement says the gene rose
        # while its values fell: the fit step fails and the reason must say so,
        # not "claims down where the gene implies down"
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        stmt = self.statement(symbol, "%s rose to +2.00, so the pathway is less active" % symbol)
        verdicts, objections = direction.direction_check(StubClient("down"), [stmt], walker)
        verdict = verdicts[1][0]
        self.assertEqual((verdict["implied"], verdict["claimed"]), ("down", "down"))
        self.assertFalse(verdict["consistent"])
        self.assertFalse(verdict["fits"])
        self.assertIn("does not follow from %s's values" % symbol, objections[1][0])
        gate = direction.gate(verdicts, 0)
        self.assertFalse(gate["pass"])
        self.assertIn("does not follow from %s's values: rose vs values" % symbol, gate["why"])
        self.assertNotIn("claims down where", gate["why"])

    def test_a_statement_that_fits_any_values_is_flagged(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        stmt = self.statement(symbol, "%s reads the same whatever happens" % symbol)
        verdicts, objections = direction.direction_check(StubClient("none"), [stmt], walker)
        self.assertTrue(verdicts[1][0]["insensitive"])
        self.assertIn("values reversed", objections[1][0])
        gate = direction.gate(verdicts, 1)
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["insensitive"], 1)
        self.assertEqual(gate["dropped"], 1)

    def test_no_panel_gene_means_not_applicable(self):
        walker = self.walker_with("g:2", "Bbb", [-0.1, -2.0, -1.5])
        verdicts, objections = direction.direction_check(StubClient("up"), [self.statement("Bbb", "Bbb fell")], walker)
        self.assertEqual((verdicts, objections), ({}, {}))
        gate = direction.gate(verdicts, 0)
        self.assertTrue(gate["pass"])
        self.assertTrue(gate["not_applicable"])

    def test_a_failing_model_call_leaves_the_verdict_unchecked(self):
        class Broken(StubClient):
            def complete_json(self, *args, **kwargs):
                raise RuntimeError("gateway")
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        verdicts, objections = direction.direction_check(Broken("up"), [self.statement(symbol, "x fell")], walker)
        self.assertEqual(objections, {})
        self.assertIsNone(verdicts[1][0]["claimed"])
        self.assertTrue(direction.gate(verdicts, 0)["pass"])

    # The stage runs first after the Writers, when the 2026-09-15 throttling
    # hit: its calls must give up by its deadline like every other stage's.
    def test_every_call_is_given_the_time_left(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        client = StubClient("down")
        direction.direction_check(client, [self.statement(symbol, "x fell")], walker, deadline=time.time() + 30)
        self.assertEqual(len(client.budgets), 3, client.budgets)
        self.assertTrue(all(b is not None and 20 < b <= 30 for b in client.budgets), client.budgets)

    def test_a_due_deadline_asks_nothing_and_says_so(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        client = StubClient("up")
        verdicts, objections = direction.direction_check(client, [self.statement(symbol, "x fell")], walker,
                                                         deadline=time.time() + 1)
        self.assertEqual(client.calls, [], "a call was sent with no time to answer")
        self.assertEqual(objections, {})
        self.assertEqual(verdicts[1][0]["unchecked"], direction.UNCHECKED_OUT_OF_TIME)

    def test_a_rate_limited_gateway_gives_up_by_the_deadline_and_the_verdict_says_so(self):
        # The real client and its real retry loop: a 429 asking for 84 s may
        # not be slept through when the stage has 30 s, and a call that gave
        # up on the deadline is "not asked", never "no objection" -- a
        # statement sent back for contradicting its regulator would otherwise
        # stand once the re-check gave up.
        from src.classes.AIInterpret import llm_client as lc
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        sleeps, real_post, real_sleep = [], lc.requests.post, lc.time.sleep
        lc.requests.post = lambda *args, **kwargs: Throttled()
        lc.time.sleep = sleeps.append
        try:
            client = lc.LLMClient({"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m",
                                   "fallback_models": []})
            verdicts, objections = direction.direction_check(
                client, [self.statement(symbol, "Cish fell, derepressing the pathway")], walker,
                deadline=time.time() + 30)
        finally:
            lc.requests.post, lc.time.sleep = real_post, real_sleep
        self.assertFalse([s for s in sleeps if s >= 30], "a retry slept past the stage's deadline: %s" % sleeps)
        self.assertEqual(objections, {})
        self.assertEqual(verdicts[1][0]["unchecked"], direction.UNCHECKED_OUT_OF_TIME)

    def _mid_hit(self, costs, left=10.0):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        clock = Clock()
        client = SlowStub("down", clock, costs)
        real_time = direction.time
        direction.time = clock
        try:
            verdicts, _ = direction.direction_check(client, [self.statement(symbol, "x fell")], walker,
                                                    deadline=clock.t + left)
        finally:
            direction.time = real_time
        return client.calls, verdicts[1][0]

    def test_the_deadline_passing_after_the_claim_skips_both_fits(self):
        calls, verdict = self._mid_hit([8])
        self.assertEqual(calls, ["direction_claim"])
        self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_OUT_OF_TIME)

    def test_the_deadline_passing_after_the_fit_skips_the_flipped_fit(self):
        calls, verdict = self._mid_hit([2, 6])
        self.assertEqual(calls, ["direction_claim", "direction_fit"])
        self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_OUT_OF_TIME)

    def test_under_the_call_minimum_but_over_the_old_one_second_floor_nothing_is_sent(self):
        calls, verdict = self._mid_hit([], left=2.5)
        self.assertEqual(calls, [])
        self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_OUT_OF_TIME)

    def test_a_call_is_sent_with_just_over_the_minimum(self):
        calls, verdict = self._mid_hit([], left=3.5)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("unchecked", verdict)

    def test_an_answer_that_arrived_before_time_ran_out_still_counts(self):
        # The claim contradicts the implied direction, then the deadline passes:
        # the objection it raised stands although the fits were never asked.
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        for claimed, prose, costs, calls in (("up", "x fell", [8], ["direction_claim"]),
                                             ("down", "x rose", [2, 6], ["direction_claim", "direction_fit"])):
            clock = Clock()
            client = SlowStub(claimed, clock, costs)
            real_time = direction.time
            direction.time = clock
            try:
                verdicts, objections = direction.direction_check(client, [self.statement(symbol, prose)], walker,
                                                                 deadline=clock.t + 10)
            finally:
                direction.time = real_time
            verdict = verdicts[1][0]
            self.assertEqual(client.calls, calls)
            self.assertFalse(verdict["consistent"], prose)
            self.assertEqual(verdict["unchecked"], direction.UNCHECKED_OUT_OF_TIME)
            self.assertTrue(objections.get(1), "an objection received before the deadline was lost (%s)" % prose)

    def _one_call_fails(self, which, exc):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])

        class Stub(StubClient):
            def complete_json(self, messages, name, *args, **kwargs):
                if name == which:
                    raise exc
                return StubClient.complete_json(self, messages, name, *args, **kwargs)
        verdicts, objections = direction.direction_check(Stub("down"), [self.statement(symbol, "x fell")], walker,
                                                         deadline=time.time() + 30)
        return verdicts[1][0], objections

    def test_a_call_the_budget_ended_or_the_service_did_not_answer_leaves_the_verdict_unchecked(self):
        import requests
        ended = requests.exceptions.HTTPError("HTTP 429")
        ended.response, ended.gave_up_at_deadline = Throttled(), True
        refused = requests.exceptions.HTTPError("HTTP 429")    # every retry refused, time still left
        refused.response = Throttled()
        for which in ("direction_claim", "direction_fit"):
            verdict, objections = self._one_call_fails(which, ended)
            self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_OUT_OF_TIME, which)
            verdict, objections = self._one_call_fails(which, refused)
            self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_NO_ANSWER, which)
            verdict, objections = self._one_call_fails(which, RuntimeError("502"))
            self.assertEqual(verdict.get("unchecked"), direction.UNCHECKED_NO_ANSWER, which)
            self.assertEqual(objections, {})

    def test_without_a_budget_a_timeout_is_a_failed_call_not_out_of_time(self):
        import requests
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        hit = direction.panel_hits(self.statement(symbol, "x fell"), walker)[0]

        class Slow(object):
            def complete_json(self, *args, **kwargs):
                raise requests.exceptions.Timeout("gateway read timeout")
        self.assertIsNone(direction.claimed_direction(Slow(), self.statement(symbol, "x fell"), hit))

    def test_each_call_gets_exactly_the_time_left(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        clock = Clock()
        client = SlowStub("down", clock, [])
        real_time = direction.time
        direction.time = clock
        try:
            direction.direction_check(client, [self.statement(symbol, "x fell")], walker, deadline=clock.t + 10)
        finally:
            direction.time = real_time
        self.assertEqual(client.budgets, [10.0, 10.0, 10.0])

    def test_one_fit_left_unanswered_leaves_the_verdict_unchecked(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        for failing_fit in (1, 2):
            class Stub(StubClient):
                fits = 0

                def complete_json(self, messages, name, *args, **kwargs):
                    if name == "direction_fit":
                        Stub.fits += 1
                        if Stub.fits == failing_fit:
                            raise RuntimeError("502")
                    return StubClient.complete_json(self, messages, name, *args, **kwargs)
            verdicts, _ = direction.direction_check(Stub("down"), [self.statement(symbol, "x fell")], walker,
                                                    deadline=time.time() + 30)
            self.assertEqual(verdicts[1][0].get("unchecked"), direction.UNCHECKED_NO_ANSWER,
                             "fit call %d unanswered" % failing_fit)

    def test_each_objection_is_kept_on_its_regulators_verdict(self):
        symbol = self.feedback["symbol"]
        walker = self.walker_with("g:2", symbol, [-0.1, -2.0, -1.5])
        verdicts, objections = direction.direction_check(StubClient("up"), [self.statement(symbol, "x fell")],
                                                         walker, deadline=time.time() + 30)
        self.assertTrue(objections[1])
        self.assertEqual(verdicts[1][0]["objections"], objections[1])

    def test_the_gate_names_every_reason_and_keeps_the_drops(self):
        def row(**kw):
            v = {"gene": "Cish", "pathway": "JAK-STAT", "class": "feedback", "implied": "down",
                 "claimed": None, "consistent": True, "insensitive": False}
            v.update(kw)
            return v
        # The claim went unanswered and both fits said true: still insensitive.
        gate = direction.gate({1: [row(insensitive=True, unchecked=direction.UNCHECKED_NO_ANSWER)]}, 0)
        self.assertFalse(gate["pass"])
        self.assertEqual((gate["checked"], gate["insensitive"], gate["unchecked"]), (1, 1, 0))
        # Every kept regulator unanswered, but the check dropped two statements.
        gate = direction.gate({1: [row(unchecked=direction.UNCHECKED_OUT_OF_TIME)]}, 2)
        self.assertEqual((gate["dropped"], gate["not_applicable"], gate["pass"]), (2, False, True))
        self.assertIn("1 not checked: " + direction.UNCHECKED_OUT_OF_TIME, gate["why"])
        gate = direction.gate({1: [row(unchecked=direction.UNCHECKED_OUT_OF_TIME)],
                               2: [row(gene="Socs3", unchecked=direction.UNCHECKED_NO_ANSWER)]}, 0)
        self.assertIn(direction.UNCHECKED_OUT_OF_TIME, gate["why"])
        self.assertIn(direction.UNCHECKED_NO_ANSWER, gate["why"])
        gate = direction.gate({1: [row(claimed="down"), row(gene="Socs3", unchecked=direction.UNCHECKED_NO_ANSWER)]}, 0)
        self.assertIn("1 not checked: " + direction.UNCHECKED_NO_ANSWER, gate["why"])

    def test_the_gate_does_not_count_an_unasked_regulator_as_checked(self):
        def row(**kw):
            v = {"gene": "Cish", "pathway": "JAK-STAT", "class": "feedback", "implied": "down",
                 "claimed": None, "consistent": True, "insensitive": False}
            v.update(kw)
            return v
        unasked = row(unchecked=direction.UNCHECKED_OUT_OF_TIME)
        gate = direction.gate({1: [unasked]}, 0)
        self.assertTrue(gate["not_applicable"])
        self.assertEqual((gate["checked"], gate["consistent"], gate["unchecked"]), (0, 0, 1))
        self.assertEqual(gate["why"], direction.UNCHECKED_OUT_OF_TIME, "the page shows why nothing was checked")
        gate = direction.gate({1: [row(unchecked=direction.UNCHECKED_NO_ANSWER)]}, 0)
        self.assertEqual((gate["checked"], gate["unchecked"], gate["why"]), (0, 1, direction.UNCHECKED_NO_ANSWER))
        # Every statement that cited a panel gene was dropped by the check.
        gate = direction.gate({}, 2)
        self.assertEqual(gate["dropped"], 2)
        self.assertIn("dropped every statement", gate["why"])
        gate = direction.gate({1: [row(claimed="down"), unasked]}, 0)
        self.assertTrue(gate["pass"])
        self.assertEqual((gate["checked"], gate["consistent"], gate["unchecked"]), (1, 1, 1))
        self.assertIn("1 not checked", gate["why"])
        # An objection raised before time ran out still counts against the gate.
        gate = direction.gate({1: [row(claimed="up", consistent=False, unchecked=direction.UNCHECKED_OUT_OF_TIME)]}, 0)
        self.assertFalse(gate["pass"])
        self.assertEqual((gate["checked"], gate["unchecked"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
