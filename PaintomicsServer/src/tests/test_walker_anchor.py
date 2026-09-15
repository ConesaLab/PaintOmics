#!/usr/bin/env python3
"""Check 5, the anchor: the design card names the perturbed gene, the gene gets
its known targets as edges for the run, every module is labelled by its
distance from it, and the gate compares how close the walk stayed with how
close the measured nodes are at large. Offline, synthetic organism."""
import os
import random
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import anchor as anchor_mod   # noqa: E402
from src.classes.AIInterpret.walker import card as card_mod       # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod      # noqa: E402
from src.classes.AIInterpret.walker import policies               # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                        # noqa: E402

TF_TARGETS = ("tf\ttarget\tsign\tsource\treferences\n"
              "Aaa\tGgg\t1\tTEST\t123\n"          # Ggg is in pathway two only
              "Aaa\tEee\t-1\tTEST\t124\n"         # Eee is in pathway one: the walked graph gains it
              "Aaa\tZzz\t1\tTEST\t125\n"          # unknown symbol: skipped
              "Fff\tBbb\t1\tTEST\t126\n"
              "Fff\tDdd\t0\tOTHER\t127\n")


class AnchorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.org_dir = os.path.join(cls.data_dir, "current", "tst")
        with open(os.path.join(cls.org_dir, "mapping", "kegg2genesymbol.list"), "a") as handle:
            handle.write("tst:1\tCDS\t1:1..2\tAaa, Alpha, Ikaros-like; the first gene\n")   # an alias row
        with open(os.path.join(cls.org_dir, "mapping", "tf_targets.tsv"), "w") as handle:
            handle.write(TF_TARGETS)
        cls.aliases = anchor_mod.alias_map(cls.org_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def fresh(self):
        network = net_mod.build_network("tst", self.data_dir)
        graph = network.filter_pathway("KEGG:tst00001")
        return network, graph

    def test_aliases_and_the_deterministic_card(self):
        self.assertEqual(self.aliases["AAA"], "Aaa")
        self.assertEqual(self.aliases["ALPHA"], "Aaa")
        self.assertEqual(self.aliases["IKAROS-LIKE"], "Aaa")
        genes, direction = anchor_mod.find_perturbed_genes(
            "Alpha was induced by tamoxifen in a mouse cell line over 24 hours.", self.aliases)
        self.assertEqual((genes, direction), (["Aaa"], "up"))
        genes, direction = anchor_mod.find_perturbed_genes("Bbb knockout versus wild type, log2 KO over WT.", self.aliases)
        self.assertEqual((genes, direction), (["Bbb"], "down"))
        genes, direction = anchor_mod.find_perturbed_genes("Six time points of a drug treatment.", self.aliases)
        self.assertEqual((genes, direction), ([], "unknown"))
        # a gene name far from any perturbation word is not the perturbation
        genes, _direction = anchor_mod.find_perturbed_genes(
            "Cells were treated with a knockdown of Alpha. " + "x" * 200 + " Bbb is a marker.", self.aliases)
        self.assertEqual(genes, ["Aaa"])
        card = card_mod.deterministic_card("Alpha induction in B3 cells. Values are log2 fold changes.", [],
                                           {"Gene expression": ["0h", "2h"]}, self.aliases)
        self.assertEqual(card["perturbed_genes"], ["Aaa"])
        self.assertEqual(card["perturbation_direction"], "up")
        self.assertIn("perturbed     Aaa, up", card_mod.card_text(card))
        self.assertEqual(card_mod.normalise_genes("ikaros-like, zzz, Aaa", self.aliases), ["Aaa"])
        self.assertEqual(card_mod.normalise_genes(["X", "y", "X"]), ["X", "y"])

    def test_the_anchor_gets_its_targets_as_edges_for_the_run(self):
        network, graph = self.fresh()
        card = {"perturbed_genes": ["Alpha"], "perturbation_direction": "up"}
        anchor = anchor_mod.resolve_anchor(card, network, self.org_dir, self.aliases)
        self.assertEqual(anchor["gene"], "Aaa")
        self.assertEqual(anchor["node"], "g:1")
        self.assertTrue(anchor["in_graph"])                     # Aaa already has pathway edges
        rows = anchor_mod.load_tf_targets(self.org_dir)
        self.assertEqual(len(rows), 5)
        added = anchor_mod.add_anchor_edges(graph, network, anchor, rows, self.aliases)
        self.assertEqual(added, 1)                              # Eee is in the walked pathway; Ggg is not
        self.assertIn(("g:1", "g:5"), graph.edges)
        self.assertEqual(graph.edges[("g:1", "g:5")]["tags"], ["anchor:TEST"])
        self.assertEqual(graph.edges[("g:1", "g:5")]["sign"], -1)
        self.assertIn(("g:1", "g:7"), network.edges)            # the universal network gained both
        self.assertEqual(anchor["source"], "TEST")
        self.assertIsNone(anchor_mod.resolve_anchor({"perturbed_genes": ["Zzz"]}, network, self.org_dir, self.aliases))
        self.assertIsNone(anchor_mod.resolve_anchor({}, network, self.org_dir, self.aliases))

    def test_an_anchor_with_no_pathway_edges_is_connected_by_its_targets_only(self):
        network, graph = self.fresh()
        # Fff has edges in pathway two only; in pathway one's graph it is absent
        anchor = anchor_mod.resolve_anchor({"perturbed_genes": ["Fff"], "perturbation_direction": "down"},
                                           graph, self.org_dir, self.aliases)
        self.assertFalse(anchor["in_graph"])
        rows = anchor_mod.load_tf_targets(self.org_dir)
        added = anchor_mod.add_anchor_edges(graph, graph, anchor, rows, self.aliases)
        self.assertEqual(added, 2)
        self.assertTrue(anchor["in_graph"])
        self.assertIn("g:6", graph.nodes)

    def test_distances_modules_reachability_and_the_gate(self):
        network, graph = self.fresh()
        ov = ov_mod.overlay_job(graph, fx.make_job())
        dist = anchor_mod.distances(network, "g:1")
        self.assertEqual(dist["g:1"], 0)
        self.assertEqual(dist["g:2"], 1)
        self.assertEqual(dist["g:3"], 2)
        self.assertEqual(anchor_mod.distances(network, "g:999"), {})
        walker = policies.greedy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
        walker.segments = [{"seed": walker.plan["seeds"][0], "label": "s", "first": 1, "last": len(walker.chain), "stop": ""}]
        anchor_mod.label_segments(walker, dist)
        self.assertIsNotNone(walker.segments[0]["distance"])
        nodes = anchor_mod.walked_nodes(walker)
        self.assertEqual(nodes, anchor_mod.walked_nodes(walker.record()["chain"]))   # a sealed chain reads the same
        reach = anchor_mod.reachability(nodes, dist)
        base = anchor_mod.base_rate(ov, dist)
        self.assertTrue(0.0 <= reach <= 1.0)
        self.assertTrue(0.0 <= base <= 1.0)
        anchor = {"gene": "Aaa", "node": "g:1", "direction": "up", "in_graph": True}
        gate = anchor_mod.anchor_gate(anchor, walker, ov, dist, targets=["Bbb", "Ddd", "Zzz"], network=network,
                                      aliases=self.aliases)
        self.assertEqual(gate["reachability"], round(reach, 3))
        self.assertEqual(gate["targets"]["targets"], 2)
        self.assertTrue(0.0 < gate["targets"]["p"] <= 1.0)
        self.assertEqual(gate["pass"], reach > base)
        self.assertEqual(anchor_mod.anchor_gate(None, walker, ov, dist), {"pass": True, "not_applicable": True,
                                                                          "why": "the design names no perturbed gene"})
        off = anchor_mod.anchor_gate(dict(anchor, in_graph=False), walker, ov, dist)
        self.assertFalse(off["pass"])
        self.assertIn("not connected", off["why"])

    def test_an_anchored_scan_starts_the_walk_in_the_anchor_neighbourhood(self):
        from src.classes.AIInterpret.walker import heat as heat_mod
        network, graph = self.fresh()
        ov = ov_mod.overlay_job(graph, fx.make_job())
        # anchor on E: its neighbours (C, D) are 1 step away, A and B further
        dist = anchor_mod.distances(network, "g:5")
        rows = heat_mod.scan_graph(graph, ov, 1, 12, dist)
        candidates = [r for r in rows if r["candidate"]]
        self.assertTrue(candidates)
        self.assertEqual([r["dist"] for r in candidates], sorted(r["dist"] for r in candidates))   # nearest first
        self.assertTrue(all(r["dist"] is not None for r in candidates))
        walker = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        walker.dist, walker.anchor = dist, {"gene": "Eee", "node": "g:5", "direction": "up", "in_graph": True}
        walker.scan("graph")
        far = max(candidates, key=lambda r: r["dist"])
        near = min(candidates, key=lambda r: r["dist"])
        if far["dist"] > near["dist"]:
            answer = walker.plan_walk([far["id"], near["id"]], 3, "far first")
            self.assertTrue(answer.startswith("REFUSED"))
            self.assertIn("nearest Eee", answer)
        answer = walker.plan_walk([near["id"]], 3, "near first")
        self.assertTrue(answer.startswith("plan set"))
        self.assertIn("d=", walker.turns[-1]["answer"])                    # neighbours show their distance

    def test_an_anchored_walk_reads_the_neighbourhood_before_it_leaves(self):
        network, _graph = self.fresh()
        ov = ov_mod.overlay_job(network, fx.make_job())             # the whole network: A -> F lies in pathway two
        dist = anchor_mod.distances(network, "g:5")               # anchor E: C, D at 1; A, B at 2; F at 3
        self.assertEqual((dist["g:4"], dist["g:1"], dist["g:6"]), (1, 2, 3))
        walker = Walker(network, ov, "network", params_for("pathway"))
        walker.dist, walker.anchor = dist, {"gene": "Eee", "node": "g:5", "direction": "up", "in_graph": True}
        walker.start_at("g:1", 4, "test")                          # standing on A, two steps from E
        # F is three steps out while D (one step from E, relevant) is unread: refused
        answer = walker.step("Fff", "Fff Gene expression +0.10", "out")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("Stay within 2 steps of Eee", answer)
        self.assertIn("Ddd", answer)
        answer = walker.step("Ddd", "Ddd Gene expression −2.00", "in")
        self.assertTrue(answer.startswith("e1"), answer)

    def test_a_decoy_has_a_regulon_of_similar_size(self):
        network, _graph = self.fresh()
        rows = anchor_mod.load_tf_targets(self.org_dir)
        anchor = {"gene": "Aaa", "node": "g:1"}
        decoy = anchor_mod.decoy_for(anchor, rows, network, random.Random(0), self.aliases)
        self.assertEqual(decoy, "Fff")                           # 2 targets in the network each
        self.assertIsNone(anchor_mod.decoy_for({"gene": "Zzz", "node": "g:0"}, rows, network, random.Random(0), self.aliases))


if __name__ == "__main__":
    unittest.main()
