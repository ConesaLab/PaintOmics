#!/usr/bin/env python3
"""The structure null (check 1 at request time): permuting the relevant flags
keeps K, a planted module is not what permuted flags give, and the gate dict
has the shape the view renders, with the adjacency count and whether a module
could form on the data at all. Offline, synthetic organism and hand-built
paths."""
import json
import os
import random
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import evaluate               # noqa: E402
from src.classes.AIInterpret.walker import heat as heat_mod       # noqa: E402
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
        # every pair is relevant on every permutation: the adjacency count
        # cannot tell them apart either, and the reason says what was compared
        self.assertFalse(gate["untestable"])
        self.assertEqual(gate["adjacent_pairs"]["null_mean"], gate["adjacent_pairs"]["real"])
        self.assertEqual(gate["adjacent_pairs"]["p"], 1.0)
        self.assertIn("could not tell this data from shuffled data", gate["why"])
        self.assertNotIn("no structure", gate["why"])
        self.assertEqual(gate["real"]["modules"], null_mod.modules_found(walker, planted))

    def test_the_gate_reports_the_overlay_it_tested(self):
        walker = policies.greedy(Walker(self.graph, self.ov, "KEGG:tst00001", params_for("pathway")))
        gate = null_mod.structure_null(self.graph, self.ov, params_for("pathway"), walker, k=12, seed=1)
        self.assertEqual(gate["measured"], len(self.ov.measured))
        self.assertEqual(gate["relevant"], self.ov.K)
        self.assertEqual(sorted(gate["adjacent_pairs"]), ["null_mean", "p", "real"])
        self.assertIsInstance(gate["adjacent_pairs"]["real"], int)
        self.assertTrue(0.0 < gate["adjacent_pairs"]["p"] <= 1.0)
        self.assertIsInstance(gate["untestable"], bool)


def _path_graph(n):
    """g:1 -> g:2 -> ... -> g:n, every edge an activation."""
    graph = net_mod.Network("tst")
    for i in range(1, n + 1):
        graph.add_node("g:%d" % i, "G%d" % i, "gene")
    for i in range(1, n):
        graph.add_edge("g:%d" % i, "g:%d" % (i + 1), 1, "KEGG:tst00009", "activation")
    return graph


def _overlay(graph, relevant):
    """Every node of ``graph`` measured, those in ``relevant`` up 2.0 and
    relevant, the others at 0.1 and not."""
    ov = ov_mod.Overlay()
    for node_id in graph.nodes:
        hit = node_id in relevant
        ov.measured.add(node_id)
        ov.r[node_id] = hit
        ov.layers[node_id] = [{"omic": "Gene expression", "member": node_id, "relevant": hit,
                               "values": [2.0 if hit else 0.1], "text": ""}]
    ov.heat = heat_mod.compute_heat(graph, ov.measured, ov.r)
    ov.N, ov.K = len(ov.measured), len(relevant)
    return ov


def _gate(graph, ov, k=20):
    walker = policies.greedy(Walker(graph, ov, "network", params_for("network")))
    return null_mod.structure_null(graph, ov, params_for("network"), walker, k=k, seed=4)


class UntestableTest(unittest.TestCase):
    """Whether a module could form on the real data at all, and the adjacency
    count beside the module test. Hand-built paths, no organism."""

    def test_pairs_are_unordered_and_measured_only(self):
        graph = _path_graph(4)
        graph.add_edge("g:2", "g:1", -1, "KEGG:tst00009", "inhibition")     # the reverse arrow: same pair
        left, right = null_mod.measured_pairs(graph, ["g:1", "g:2", "g:3"])  # g:4 not measured
        self.assertEqual(sorted(zip(left.tolist(), right.tolist())), [(0, 1), (1, 2)])
        flags = null_mod.relevant_flags({"g:1": True, "g:2": True, "g:3": False}, ["g:1", "g:2", "g:3"])
        self.assertEqual(null_mod.adjacent_relevant(flags, left, right), 1)
        self.assertEqual(null_mod.largest_relevant_group(flags, left, right), 2)
        nothing = null_mod.relevant_flags({}, ["g:1", "g:2", "g:3"])
        self.assertEqual(null_mod.largest_relevant_group(nothing, left, right), 0)

    def test_relevant_nodes_no_two_of_which_touch_are_untestable(self):
        graph = _path_graph(7)
        gate = _gate(graph, _overlay(graph, {"g:1", "g:3", "g:5", "g:7"}))
        self.assertTrue(gate["untestable"])
        self.assertEqual((gate["measured"], gate["relevant"]), (7, 4))
        self.assertEqual(gate["adjacent_pairs"]["real"], 0)
        self.assertEqual(gate["adjacent_pairs"]["p"], 1.0)                    # nothing is fewer than 0
        self.assertEqual(gate["real"]["modules"], 0)
        self.assertEqual(gate["p_modules"], 1.0)
        self.assertFalse(gate["pass"])
        self.assertTrue(gate["why"].startswith(
            "could not be tested on this data: no three of the 4 relevant genes on this graph are connected to one "
            "another (0 adjacent pairs among them)"), gate["why"])
        self.assertNotIn("no structure", gate["why"])

    def test_too_few_relevant_nodes_are_untestable(self):
        graph = _path_graph(5)
        gate = _gate(graph, _overlay(graph, {"g:2", "g:3"}))
        self.assertTrue(gate["untestable"])
        self.assertIn("this graph holds 2 relevant genes, fewer than the three a module needs", gate["why"])

    def test_three_relevant_nodes_on_a_path_are_testable(self):
        graph = _path_graph(7)
        gate = _gate(graph, _overlay(graph, {"g:3", "g:4", "g:5"}))
        self.assertFalse(gate["untestable"])
        self.assertEqual(gate["adjacent_pairs"]["real"], 2)
        self.assertTrue(0.0 <= gate["adjacent_pairs"]["null_mean"] <= 2.0)
        # The walker seeds on g:4, the hottest, and steps to one end: a
        # stretch of two relevant nodes, no module. Testable, and failed.
        self.assertEqual(gate["real"]["modules"], 0)
        self.assertFalse(gate["pass"])
        self.assertTrue(gate["why"].startswith(
            "the check's walker, which steps only from relevant gene to relevant gene, found 0 modules on the "
            "data and "), gate["why"])
        self.assertIn("Of 7 measured genes, 3 are relevant; 2 pairs of them are adjacent, ", gate["why"])
        self.assertNotIn("no structure", gate["why"])

    def test_a_job_with_other_kinds_of_node_says_features(self):
        graph = _path_graph(7)
        graph.nodes["g:7"]["kind"] = "compound"
        gate = _gate(graph, _overlay(graph, {"g:1", "g:3", "g:5", "g:7"}), k=5)
        self.assertIn("4 relevant features", gate["why"])


if __name__ == "__main__":
    unittest.main()
