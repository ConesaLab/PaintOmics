#!/usr/bin/env python3
"""The Verifier's rules on hand-built statements and Results text, and the
planted-module evaluation on the synthetic organism. Offline."""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import card as card_mod      # noqa: E402
from src.classes.AIInterpret.walker import evaluate               # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod      # noqa: E402
from src.classes.AIInterpret.walker import policies               # noqa: E402
from src.classes.AIInterpret.walker import verify                 # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                        # noqa: E402


class WalkerVerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        net = net_mod.build_network("tst", cls.data_dir)
        cls.graph = net.filter_pathway("KEGG:tst00001")
        cls.ov = ov_mod.overlay_job(cls.graph, fx.make_job())
        cls.walker = policies.greedy(Walker(cls.graph, cls.ov, "KEGG:tst00001", params_for("pathway")))
        cls.chain = cls.walker.record()["chain"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def good_statement(self):
        leg = self.chain[0]
        node = leg["to"]
        layer = self.ov.layers[node][0]
        return {"n": 1, "claim": "a claim", "prose": "%s moves" % self.walker.label(node),
                "cites": [[self.walker.label(node), layer["omic"]]], "legs": [1],
                "grounded_in": [{"leg": 1, "db": "KEGG"}], "beyond": [], "papers": []}

    def test_a_grounded_statement_passes(self):
        stmt = self.good_statement()
        if not self.ov.layers[self.chain[0]["to"]][0]["relevant"]:
            stmt["prose"] += " (not relevant)"
        self.assertEqual(verify.verify_statement(stmt, self.walker, {}), [])

    def test_the_rules_that_refuse(self):
        stmt = self.good_statement()
        stmt["legs"] = [99]
        self.assertTrue(any("e99" in p for p in verify.verify_statement(stmt, self.walker, {})))
        stmt = self.good_statement()
        stmt["cites"] = [["Nobody", "Gene expression"]]
        self.assertTrue(any("not a node on the chain" in p for p in verify.verify_statement(stmt, self.walker, {})))
        stmt = self.good_statement()
        stmt["cites"] = [[stmt["cites"][0][0], "Metabolomics"]]
        self.assertTrue(any("has no layer" in p for p in verify.verify_statement(stmt, self.walker, {})))
        stmt = self.good_statement()
        stmt["beyond"] = [{"claim": "X represses Y", "paper": None, "hypothesis": False}]
        self.assertTrue(any("neither a retrieved paper" in p for p in verify.verify_statement(stmt, self.walker, {})))
        stmt["beyond"] = [{"claim": "X represses Y", "paper": 3, "hypothesis": False}]
        self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, {3: {}}) if "beyond" in p], [])
        stmt = self.good_statement()
        stmt["grounded_in"] = [{"leg": 2, "db": "KEGG"}]
        self.assertTrue(any("grounded_in" in p for p in verify.verify_statement(stmt, self.walker, {})))

    def test_a_non_relevant_layer_must_be_called_not_relevant(self):
        # Ccc is measured and not relevant; find it on the chain if the walk passed it
        node = next((n for n in verify.chain_nodes(self.chain) if not self.ov.r.get(n)), None)
        if node is None:
            self.skipTest("the greedy walk on the fixture never crosses a non-relevant node")
        stmt = {"n": 2, "claim": "c", "prose": "%s rises" % self.walker.label(node),
                "cites": [[self.walker.label(node), self.ov.layers[node][0]["omic"]]],
                "legs": [1], "grounded_in": [], "beyond": [], "papers": []}
        self.assertTrue(any("non-relevant" in p for p in verify.verify_statement(stmt, self.walker, {})))
        stmt["prose"] += ", not relevant"
        self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, {}) if "non-relevant" in p], [])

    def test_statement_count(self):
        out, problem = verify.verify_statements([self.good_statement()], self.walker, {})
        self.assertIn("1 statements", problem)

    def test_results_checks(self):
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]   # the first quoted value
        kept = [{"n": 1}]
        text = ("The walk began at %s, which read %s at the first point [e1]. " % (
            self.walker.label(node), value)) * 12
        good = {"title": "t", "summary": "s", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": text},
            {"from_statement": None, "legs": [], "text": "The map draws the next edge as activation."}]}
        self.assertEqual(verify.verify_results(good, kept, [], self.walker), [])
        bad = {"title": "t", "summary": "s", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": text + " It reached +9.99 at 6 h [e42]."},
            {"from_statement": None, "legs": [], "text": "The link says +1.00 [3]."},
            {"from_statement": 7, "legs": [1], "text": "From a statement that does not exist."}]}
        problems = verify.verify_results(bad, kept, [{"claim": "The ERK arm is active upstream"}], self.walker)
        self.assertTrue(any("+9.99" in p for p in problems))
        self.assertTrue(any("e42" in p for p in problems))
        self.assertTrue(any("link paragraph 2 quotes a value" in p for p in problems))
        self.assertTrue(any("link paragraph 2 cites a paper" in p for p in problems))
        self.assertTrue(any("statement 7" in p for p in problems))

    def test_a_range_is_not_a_quoted_value(self):
        self.assertEqual(verify.NUMBER_RE.findall("peaks late (18-24h) and 0h-2h"), [])
        self.assertEqual(verify.NUMBER_RE.findall("from -0.35 at 0h to +2.13, within ±0.6"), ["-0.35", "+2.13"])
        self.assertEqual(verify.NUMBER_RE.findall("miR-151-3p rose to +2.00"), ["+2.00"])

    def test_deterministic_card_marks_unlabeled_columns(self):
        card = card_mod.deterministic_card("Ikaros induction over time. Values are log2 fold changes.",
                                           ["a", "b", "c"], self.ov.labels)
        self.assertEqual(card["value"], "log2 fold change against the control")
        self.assertIn("UNLABELED", card["columns"])
        self.assertIn("miRNA-seq", card["columns"])
        self.assertEqual(card["axis"], "0h · 2h · 6h")
        self.assertTrue(card_mod.card_text(card).startswith("perturbation"))

    def test_planted_recall_runs_and_reports_metrics(self):
        out = evaluate.grid(self.graph, self.ov, params_for("pathway"), plants=5, seed=1,
                            ms=(3,), qs=(0.2,))
        self.assertEqual(len(out["summary"]), 1)
        cell = out["summary"][0]
        for key in ("seed_hit", "recall", "precision"):
            self.assertGreaterEqual(cell[key], 0.0)
            self.assertLessEqual(cell[key], 1.0)
        self.assertEqual(cell["plants"], 5)
        self.assertIsNone(out["pass"])


if __name__ == "__main__":
    unittest.main()
