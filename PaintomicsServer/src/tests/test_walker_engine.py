#!/usr/bin/env python3
"""The walker engine on a synthetic organism: network, overlay, heat, the two
scans, the six tools' rules, and the scripted policies. Offline; no Mongo.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_engine
"""
import math
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import heat as heat_mod      # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod    # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod     # noqa: E402
from src.classes.AIInterpret.walker import policies              # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                       # noqa: E402


class WalkerEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def fresh(self, scope="KEGG:tst00001"):
        net = net_mod.build_network("tst", self.data_dir)
        graph = net.filter_pathway(scope) if scope != "network" else net
        ov = ov_mod.overlay_job(graph, fx.make_job())
        return graph, ov

    # ---- network -------------------------------------------------------
    def test_network_reads_signs_tags_and_symbols(self):
        net = net_mod.build_network("tst", self.data_dir)
        self.assertEqual(set(net.nodes), {"g:1", "g:2", "g:3", "g:4", "g:5", "g:6", "g:7"})
        self.assertEqual(net.nodes["g:1"]["label"], "Aaa")
        self.assertEqual(net.edges[("g:1", "g:2")]["sign"], 1)
        self.assertEqual(net.edges[("g:2", "g:3")]["sign"], -1)
        self.assertEqual(net.edges[("g:4", "g:5")]["sign"], 0)
        self.assertEqual(net.edges[("g:1", "g:2")]["tags"], ["KEGG:tst00001"])
        self.assertEqual(sorted(net.nodes["g:1"]["pathways"]), ["KEGG:tst00001", "KEGG:tst00002"])
        self.assertEqual(net.pathway_name("KEGG:tst00001"), "Test pathway one")
        self.assertEqual(net.uniprot_to_kegg["P00002"], "2")

    def test_pathway_filter_keeps_only_that_pathways_edges(self):
        net = net_mod.build_network("tst", self.data_dir)
        one = net.filter_pathway("KEGG:tst00001")
        self.assertEqual(set(one.nodes), {"g:1", "g:2", "g:3", "g:4", "g:5"})
        self.assertNotIn(("g:1", "g:6"), one.edges)

    def test_cache_round_trip(self):
        net = net_mod.load_or_build("tst", self.data_dir)
        again = net_mod.load_or_build("tst", self.data_dir)
        self.assertEqual(net.to_dict()["edges"], again.to_dict()["edges"])
        self.assertTrue(os.path.exists(os.path.join(
            self.data_dir, "current", "tst", "universal_network.v%d.json.gz" % net_mod.CACHE_VERSION)))

    # ---- overlay -------------------------------------------------------
    def test_r_is_the_or_of_a_nodes_own_layers(self):
        graph, ov = self.fresh()
        self.assertTrue(ov.r["g:2"])          # relevant only in the second condition
        self.assertFalse(ov.r["g:3"])
        self.assertTrue(ov.r["g:5"])          # by proteomics alone
        self.assertEqual(ov.N, 5 + 2)         # five genes + two miRNA nodes
        self.assertEqual(ov.K, 4 + 1)         # A, B, D, E + miR-1

    def test_values_travel_as_text_with_the_users_labels(self):
        graph, ov = self.fresh()
        text = ov.layer_text("g:1")
        self.assertIn("0h +0.10 · 2h +1.50 · 6h +2.20", text)
        self.assertIn("relevant", text)
        self.assertEqual(ov.labels["Gene expression"], ["0h", "2h", "6h"])
        self.assertIsNone(ov.labels["miRNA-seq"])
        self.assertIn("c1 +0.40 · c2 +0.90", ov.layer_text("mir:tst-miR-1"))
        self.assertEqual(ov_mod.relabel_unlabeled(ov), ["miRNA-seq"])

    def test_a_header_stored_as_the_string_none_is_no_header(self):
        self.assertIsNone(ov_mod._shorten_labels("None"))
        self.assertIsNone(ov_mod._shorten_labels(None))
        self.assertIsNone(ov_mod._shorten_labels(["#id"]))
        self.assertEqual(ov_mod._shorten_labels(["#id", "Ikaros/Control_0h", "Ikaros/Control_24h"]), ["0h", "24h"])
        self.assertEqual(ov_mod.values_text([0.5, -1.25], None), "c1 +0.50 · c2 −1.25")

    def test_mirna_rows_become_nodes_with_target_edges(self):
        graph, ov = self.fresh()
        self.assertIn("mir:tst-miR-1", graph.nodes)
        self.assertEqual(graph.nodes["mir:tst-miR-1"]["kind"], "miRNA")
        self.assertEqual(graph.edges[("mir:tst-miR-1", "g:2")]["sign"], -1)
        self.assertEqual(graph.edges[("mir:tst-miR-1", "g:4")]["tags"], ["job:miRNA-seq"])
        self.assertTrue(ov.r["mir:tst-miR-1"])
        self.assertFalse(ov.r["mir:tst-miR-2"])

    # ---- heat ----------------------------------------------------------
    def test_a_degree_one_node_cannot_beat_the_base_rate(self):
        graph, ov = self.fresh()
        ceiling = -math.log10(ov.K / ov.N) + 1e-9
        for node_id, h in ov.heat.items():
            if h["n"] == 1:
                self.assertLessEqual(h["heat"], ceiling + 0.3, node_id)
        self.assertGreater(ov.heat["g:1"]["heat"], ov.heat["g:3"]["heat"])

    def test_graph_scan_flags_non_adjacent_candidates(self):
        graph, ov = self.fresh()
        rows = heat_mod.scan_graph(graph, ov, sep=1, limit=12)
        cands = [r for r in rows if r["candidate"]]
        self.assertTrue(cands)
        ids = [r["id"] for r in cands]
        for a in ids:
            for b in ids:
                if a != b:
                    self.assertNotIn(b, graph.neighbours(a), "%s and %s are adjacent" % (a, b))
        self.assertTrue(all(r["r"] == 1 for r in cands))
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r["heat"], -r["degree"], r["label"])))

    def test_here_scan_reports_distance_and_first_hop(self):
        graph, ov = self.fresh()
        rows = heat_mod.scan_here(graph, ov, "g:1", 2, {"g:1"})
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["g:2"]["distance"], 1)
        self.assertEqual(by_id["g:3"]["distance"], 2)
        self.assertEqual(by_id["g:3"]["via"], "Bbb")
        self.assertNotIn("g:1", by_id)

    # ---- the six tools -------------------------------------------------
    def test_plan_refuses_what_the_spec_says(self):
        graph, ov = self.fresh()
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        w.scan("graph")
        cands = [r for r in w.ranked if r["candidate"]]
        non = [r for r in w.ranked if not r["candidate"]]
        self.assertTrue(w.plan_walk([non[0]["label"]], 5).startswith("REFUSED"))
        self.assertTrue(w.plan_walk([], 5).startswith("REFUSED"))
        self.assertTrue(w.plan_walk([cands[0]["label"]], 2).startswith("REFUSED"))
        self.assertTrue(w.plan_walk([cands[0]["label"]], 41).startswith("REFUSED"))
        self.assertFalse(w.plan_walk([cands[0]["label"]], 6).startswith("REFUSED"))
        self.assertTrue(w.plan_walk([cands[0]["label"]], 6).startswith("REFUSED"))   # once
        self.assertEqual(w.budget, {"steps": 6, "jumps": 1, "notes": 3})
        self.assertEqual(w.current, cands[0]["id"])

    def test_step_rules(self):
        graph, ov = self.fresh()
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        self.assertTrue(w.step("Bbb", "x", "").startswith("REFUSED"))        # before plan
        w.scan("graph")
        seed = [r for r in w.ranked if r["candidate"]][0]
        w.plan_walk([seed["label"]], 6)
        first = w.neighbour_rows()[0]
        self.assertTrue(w.step("Ggg", "x", "").startswith("REFUSED"))        # not a neighbour
        self.assertTrue(w.step(first["label"], "no layer named", "").startswith("REFUSED"))
        out = w.step(first["label"], "%s moves" % first["label"], "the first neighbour")
        self.assertTrue(out.startswith("e1 · %s → %s · KEGG · Test pathway one · " % (
            seed["label"], first["label"])), out)
        self.assertEqual(w.budget["steps"], 5)
        back = w.step(seed["label"], "%s again" % seed["label"], "back")     # the other direction
        self.assertTrue(back.startswith("e2"), back)
        self.assertTrue(w.step(first["label"], first["label"], "again").startswith("REFUSED"))  # closed
        self.assertIn((seed["id"], first["id"]), w.closed)
        shown = {r["id"] for r in w.neighbour_rows(seed["id"])}
        self.assertTrue(shown <= set(w.seen))                                  # shown = seen
        self.assertTrue(any(v not in w.visited for v in shown))

    def test_a_move_shows_the_relevant_neighbours_layers(self):
        graph, ov = self.fresh()
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        w.scan("graph")
        seed = [r for r in w.ranked if r["candidate"]][0]
        answer = w.plan_walk([seed["label"]], 6)
        relevant = [r for r in w.neighbour_rows() if r["r"] == 1 and r["open"]]
        if relevant:
            self.assertIn("relevant neighbours' layers:", answer)
            self.assertIn(relevant[0]["label"] + " (r=1", answer)
            self.assertIn(ov.layer_text(relevant[0]["id"]).splitlines()[0].split()[-1], answer)  # a value
        else:
            self.assertNotIn("relevant neighbours' layers:", answer)

    def test_jump_note_stop_rules(self):
        graph, ov = self.fresh()
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        w.scan("graph")
        cands = [r["id"] for r in w.ranked if r["candidate"]]
        w.plan_walk(cands[:2], 6)
        self.assertTrue(w.jump("Ggg", "x", "").startswith("REFUSED"))
        self.assertTrue(w.jump(cands[0], "x", "").startswith("REFUSED"))      # standing on it
        if any(r["r"] == 1 and r["open"] for r in w.neighbour_rows()):
            self.assertIn("Step first", w.jump(cands[1], "next seed", ""))    # neighbourhood not read
            first = next(r for r in w.neighbour_rows() if r["r"] == 1 and r["open"])
            w.step(first["label"], first["label"], "read it")
        self.assertTrue(w.jump(cands[1], "next seed", "").startswith("J"))
        self.assertTrue(w.note("").startswith("REFUSED"))
        self.assertTrue(w.note("x" * 401).startswith("REFUSED"))
        self.assertTrue(w.note("a note").startswith("noted after e%d" % len(w.chain)))
        self.assertTrue(w.stop("done", "enough").startswith("done ·"))
        self.assertTrue(w.done)
        self.assertTrue(w.step("Aaa", "Aaa", "").startswith("REFUSED"))
        rec = w.record()
        self.assertEqual(rec["chain"][-1]["kind"], "jump")
        self.assertEqual(rec["notes"][0]["after_leg"], len(rec["chain"]))
        self.assertEqual(rec["stop_reason"], "enough")

    def test_here_scan_needs_a_plan_and_a_legal_radius(self):
        graph, ov = self.fresh()
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        self.assertTrue(w.scan("here", 2).startswith("REFUSED"))
        w.scan("graph")
        seed = [r for r in w.ranked if r["candidate"]][0]
        w.plan_walk([seed["label"]], 6)
        self.assertTrue(w.scan("here", 7).startswith("REFUSED"))
        self.assertIn("within 2 step(s) of %s" % seed["label"], w.scan("here", 2))
        self.assertEqual(w.scans, 2)

    # ---- policies ------------------------------------------------------
    def test_greedy_is_deterministic_and_stays_legal(self):
        runs = []
        for _ in range(2):
            graph, ov = self.fresh()
            w = policies.greedy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
            self.assertTrue(w.done)
            self.assertEqual(w.refusals, 0)
            runs.append([(l["kind"], l["from"], l["to"]) for l in w.record()["chain"]])
        self.assertEqual(runs[0], runs[1])
        self.assertTrue(runs[0])

    def test_a_policy_returns_the_walker_when_nothing_is_relevant(self):
        graph, ov = self.fresh()
        for node_id in list(ov.r):
            ov.r[node_id] = False
        ov.K = 0
        ov.heat = heat_mod.compute_heat(graph, ov.measured, ov.r)
        for policy in (policies.greedy, policies.random_walk):
            w = policy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
            self.assertIsInstance(w, Walker)
            self.assertTrue(w.done)
            self.assertEqual(w.plan, None)
            self.assertEqual(w.record()["chain"], [])

    def test_the_cache_never_serves_an_omnipath_free_graph_to_a_caller_with_mongo(self):
        data_dir = fx.make_data_dir()
        try:
            without = net_mod.load_or_build("tst", data_dir)                        # no Mongo: cached
            self.assertEqual(without.sources.get("omnipath"), 0)
            self.assertNotIn(("g:1", "g:2"), {k for k, e in without.edges.items() if "OmniPath:opneTestPathway" in e["tags"]})
            with_db = net_mod.load_or_build("tst", data_dir, fx.FakeMongo(fx.OMNIPATH_DOCS))   # must rebuild
            self.assertEqual(with_db.sources.get("omnipath"), 1)
            self.assertIn("OmniPath:opneTestPathway", with_db.edges[("g:1", "g:2")]["tags"])
            again = net_mod.load_or_build("tst", data_dir, fx.FakeMongo(fx.OMNIPATH_DOCS))     # now cached
            self.assertEqual(again.sources.get("omnipath"), 1)
            self.assertEqual(net_mod.load_or_build("tst", data_dir, fx.FakeMongo([])).sources.get("omnipath"), 1)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        data_dir = fx.make_data_dir()
        try:
            empty = net_mod.load_or_build("tst", data_dir, fx.FakeMongo([]))       # asked, got nothing: not cached
            self.assertEqual(empty.sources.get("omnipath"), 0)
            self.assertFalse(os.path.exists(os.path.join(
                data_dir, "current", "tst", "universal_network.v%d.json.gz" % net_mod.CACHE_VERSION)))
            self.assertEqual(net_mod.load_or_build("tst", data_dir, fx.FakeMongo(fx.OMNIPATH_DOCS)).sources.get("omnipath"), 1)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_random_policy_stays_legal(self):
        graph, ov = self.fresh()
        w = policies.random_walk(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
        self.assertTrue(w.done)
        self.assertEqual(w.refusals, 0)


if __name__ == "__main__":
    unittest.main()
