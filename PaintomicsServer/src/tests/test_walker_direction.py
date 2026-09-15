#!/usr/bin/env python3
"""Check 3, direction logic: a falling feedback reporter reports its pathway
down and a falling true inhibitor reports it up; a statement claiming the
opposite is objected to, one that reads the same with the values reversed is
flagged, and the gate sums it up. The model is a stub. Offline."""
import os
import shutil
import sys
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

    def complete_json(self, messages, name, schema, fallback, max_tokens=0, temperature=0.0):
        self.calls.append(name)
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


if __name__ == "__main__":
    unittest.main()
