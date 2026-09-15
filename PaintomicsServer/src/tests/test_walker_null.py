#!/usr/bin/env python3
"""The structure null (check 1 at request time): permuting the relevant flags
keeps K, a planted module is not what permuted flags give, and the gate dict
has the shape the view renders. Offline, synthetic organism."""
import json
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

    def test_a_permutation_keeps_k_and_moves_the_measurements(self):
        ov = null_mod.permute_flags(self.graph, self.ov, random.Random(3))
        self.assertEqual(ov.K, self.ov.K)
        self.assertEqual(set(ov.r), set(self.ov.measured))
        self.assertEqual(sum(ov.r.values()), sum(bool(v) for v in self.ov.r.values()))
        self.assertIsNot(ov.heat, self.ov.heat)
        self.assertEqual(self.ov.r, ov_mod.overlay_job(self.graph, fx.make_job()).r)   # the original is untouched
        self.assertEqual(self.ov.layers, ov_mod.overlay_job(self.graph, fx.make_job()).layers)
        # the layers move with the flag: every permuted node's flag is the OR of
        # the layers it now holds, and the bundles are the original bundles
        for v in self.ov.measured:
            self.assertEqual(ov.r[v], any(layer["relevant"] for layer in ov.layers[v]), v)
        self.assertEqual(sorted(json.dumps(b, sort_keys=True) for b in ov.layers.values()),
                         sorted(json.dumps(b, sort_keys=True) for b in self.ov.layers.values()))
        self.assertNotEqual([ov.layers[v] for v in sorted(ov.layers)],
                            [self.ov.layers[v] for v in sorted(self.ov.layers)])   # something moved

    def test_segments_concordance_and_modules(self):
        chain = [{"n": 1, "kind": "step", "from": "g:1", "to": "g:2", "edge": {"sign": 1}},
                 {"n": 2, "kind": "step", "from": "g:2", "to": "g:3", "edge": {"sign": -1}},
                 {"n": 3, "kind": "jump", "from": "g:3", "to": "g:4", "edge": None},
                 {"n": 4, "kind": "step", "from": "g:4", "to": "g:5", "edge": {"sign": 0}}]
        self.assertEqual([[l["n"] for l in s] for s in null_mod.segments(chain)], [[1, 2], [4]])
        # A up, B down, D down (relevant); C not relevant, E relevant by proteomics up
        self.assertEqual(null_mod.node_direction(self.ov, "g:1"), 1)
        self.assertEqual(null_mod.node_direction(self.ov, "g:2"), -1)
        self.assertEqual(null_mod.node_direction(self.ov, "g:3"), 0)          # no relevant layer
        self.assertFalse(null_mod.leg_concordance(chain[0], self.ov))        # activation, A up while B down
        self.assertIsNone(null_mod.leg_concordance(chain[1], self.ov))       # C has no direction
        self.assertIsNone(null_mod.leg_concordance(chain[3], self.ov))       # unsigned
        self.assertTrue(null_mod.leg_concordance({"kind": "step", "from": "g:1", "to": "g:4", "edge": {"sign": -1}}, self.ov))
        # a module needs three relevant nodes and concordant signed legs
        good = [{"n": 1, "kind": "step", "from": "g:1", "to": "g:4", "edge": {"sign": -1}},
                {"n": 2, "kind": "step", "from": "g:4", "to": "g:2", "edge": {"sign": 1}}]
        self.assertTrue(null_mod.is_module(good, self.ov))
        self.assertFalse(null_mod.is_module(good[:1], self.ov))                # two relevant nodes only
        bad = [{"n": 1, "kind": "step", "from": "g:1", "to": "g:2", "edge": {"sign": 1}},
               {"n": 2, "kind": "step", "from": "g:2", "to": "g:4", "edge": {"sign": -1}}]
        self.assertFalse(null_mod.is_module(bad, self.ov))                     # both legs against the arrows
        unsigned = [{"n": 1, "kind": "step", "from": "g:1", "to": "g:2", "edge": {"sign": 0}},
                    {"n": 2, "kind": "step", "from": "g:2", "to": "g:4", "edge": {"sign": 0}}]
        self.assertFalse(null_mod.is_module(unsigned, self.ov))                # no signed leg to judge

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
        self.assertEqual(gate["p_heat"], 1.0)                                    # heat reads the flags only
        self.assertGreaterEqual(gate["p_modules"], 1 / 11.0)                     # the values still move
        self.assertFalse(gate["pass"])
        self.assertIn("no structure a walk could find", gate["why"])
        self.assertEqual(gate["real"]["modules"], null_mod.modules_found(walker, planted))


if __name__ == "__main__":
    unittest.main()
