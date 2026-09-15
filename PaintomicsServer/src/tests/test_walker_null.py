#!/usr/bin/env python3
"""The structure null (check 1 at request time): permuting the relevant flags
keeps K, a planted module is not what permuted flags give, and the gate dict
has the shape the view renders. Offline, synthetic organism."""
import os
import random
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import evaluate               # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker import null as null_mod       # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod      # noqa: E402
from src.classes.AIInterpret.walker import policies               # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                        # noqa: E402


class StructureNullTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.net = net_mod.build_network("tst", cls.data_dir)
        cls.graph = cls.net.filter_pathway("KEGG:tst00001")
        cls.ov = ov_mod.overlay_job(cls.graph, fx.make_job())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def test_a_permutation_keeps_k_and_moves_the_flags(self):
        ov = null_mod.permute_flags(self.graph, self.ov, random.Random(3))
        self.assertEqual(ov.K, self.ov.K)
        self.assertEqual(set(ov.r), set(self.ov.measured))
        self.assertEqual(sum(ov.r.values()), sum(bool(v) for v in self.ov.r.values()))
        self.assertIsNot(ov.heat, self.ov.heat)
        self.assertEqual(self.ov.r, ov_mod.overlay_job(self.graph, fx.make_job()).r)   # the original is untouched

    def test_modules_and_seed_heat(self):
        walker = policies.greedy(Walker(self.graph, self.ov, "KEGG:tst00001", params_for("pathway")))
        modules = null_mod.modules_found(walker, self.ov)
        self.assertGreaterEqual(modules, 0)
        self.assertGreaterEqual(null_mod.seed_heat(walker, self.ov), 0.0)
        empty = Walker(self.graph, self.ov, "KEGG:tst00001", params_for("pathway"))
        self.assertEqual(null_mod.modules_found(empty, self.ov), 0)
        self.assertEqual(null_mod.seed_heat(empty, self.ov), 0.0)

    def test_the_gate_dict(self):
        walker = policies.greedy(Walker(self.graph, self.ov, "KEGG:tst00001", params_for("pathway")))
        gate = null_mod.structure_null(self.graph, self.ov, params_for("pathway"), walker, k=12, seed=1)
        for key in ("k", "real", "null_mean", "p_modules", "p_heat", "pass", "why"):
            self.assertIn(key, gate)
        self.assertEqual(gate["k"], 12)
        self.assertTrue(0.0 < gate["p_modules"] <= 1.0)
        self.assertTrue(0.0 < gate["p_heat"] <= 1.0)
        self.assertIsInstance(gate["pass"], bool)
        self.assertEqual(bool(gate["why"]), not gate["pass"])

    def test_flags_that_permute_to_themselves_cannot_pass(self):
        # every measured node relevant: a permutation changes nothing, so the
        # null reproduces the real walk every time and the gate must fail
        planted = evaluate.plant(self.graph, self.ov, set(), q=1.0, rng=random.Random(0), p_in=1.0)
        self.assertTrue(all(planted.r[v] for v in planted.measured))
        walker = policies.greedy(Walker(self.graph, planted, "KEGG:tst00001", params_for("pathway")))
        gate = null_mod.structure_null(self.graph, planted, params_for("pathway"), walker, k=10, seed=2)
        self.assertEqual(gate["p_modules"], 1.0)
        self.assertEqual(gate["p_heat"], 1.0)
        self.assertFalse(gate["pass"])
        self.assertIn("not distinguishable", gate["why"])
        self.assertEqual(gate["real"]["modules"], null_mod.modules_found(walker, planted))


if __name__ == "__main__":
    unittest.main()
