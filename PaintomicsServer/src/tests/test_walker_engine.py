#!/usr/bin/env python3
"""The walker engine on a synthetic organism: network, overlay, heat, the two
scans, the six tools' rules, and the scripted policies. Offline; no Mongo.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_engine
"""
import math
import os
import re
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import heat as heat_mod      # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod    # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod     # noqa: E402
from src.classes.AIInterpret.walker import policies              # noqa: E402
from src.classes.AIInterpret.walker import verify                # noqa: E402
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

    def test_a_locus_tag_gene_id_is_its_own_node(self):
        """A bacterium's or a plant's KEGG gene id is its locus tag, not
        digits (SAUSA300_0001, b2029, AT1G01010): it mapped onto no node, and
        every walk on such an organism failed with "no seed candidate"."""
        net = net_mod.build_network("tst", self.data_dir)
        net.add_node("g:TST_0001", "TST_0001", "gene")
        self.assertEqual(ov_mod.node_for_feature(net, "TST_0001", "gene"), "g:TST_0001")
        self.assertIsNone(ov_mod.node_for_feature(net, "TST_9999", "gene"))
        self.assertEqual(ov_mod.node_for_feature(net, "P00002", "gene"), "g:2")
        self.assertEqual(ov_mod.node_for_feature(net, "4", "gene"), "g:4")

    def test_values_travel_as_text_with_the_users_labels(self):
        graph, ov = self.fresh()
        text = ov.layer_text("g:1")
        self.assertIn("0h +0.10 · 2h +1.50 · 6h +2.20", text)
        self.assertIn("relevant", text)
        self.assertEqual(ov.labels["Gene expression"], ["0h", "2h", "6h"])
        self.assertIsNone(ov.labels["miRNA-seq"])
        self.assertIn("c1 +0.40 · c2 +0.90", ov.layer_text("mir:tst-miR-1"))
        self.assertEqual(sorted(name for name, labels in ov.labels.items() if labels is None), ["miRNA-seq"])

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

    def test_vectorised_heat_matches_the_per_node_test(self):
        from scipy.stats import hypergeom
        graph, ov = self.fresh()
        N = ov.N
        K = ov.K
        for node_id, h in ov.heat.items():
            nb = graph.neighbours(node_id)
            n = sum(1 for w in nb if w in ov.measured)
            x = sum(1 for w in nb if w in ov.measured and ov.r.get(w))
            n_total = N - (1 if node_id in ov.measured else 0)
            k_total = K - (1 if (node_id in ov.measured and ov.r.get(node_id)) else 0)
            p = float(hypergeom.sf(x - 1, n_total, k_total, n)) if n else 1.0
            self.assertEqual((h["n"], h["x"], h["degree"]), (n, x, len(nb)), node_id)
            self.assertAlmostEqual(h["p"], p, places=12, msg=node_id)

    def test_a_nan_value_is_unmeasured_not_a_negative_reading(self):
        self.assertEqual(ov_mod.values_text([float("nan"), 1.5, "x", None], ["0h", "2h", "6h", "12h"]), "2h +1.50")

    def test_the_signature_sees_every_file_the_build_reads(self):
        data_dir = fx.make_data_dir()
        try:
            org = os.path.join(data_dir, "current", "tst")
            before = net_mod._signature(org)
            with open(os.path.join(org, "pathways.list"), "a") as handle:
                handle.write("path:tst00003\tA third pathway - Test organism (tst)\n")
            self.assertNotEqual(before, net_mod._signature(org))
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

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

    def test_a_scripted_walk_never_runs_the_value_guard(self):
        """A policy's readings are layer lines; the guard on every scripted
        step made the structure null take 335 s on vRmKBo4E60 (heat4)."""
        def boom(*args, **kwargs):
            raise AssertionError("the value guard ran on a scripted walk")
        with mock.patch.object(verify, "invented_values", side_effect=boom) as invented, \
                mock.patch.object(verify, "heats_given_as_values", side_effect=boom) as given:
            for policy in (policies.greedy, policies.random_walk):
                graph, ov = self.fresh()
                w = policy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
                self.assertTrue(w.chain and w.done and not w.guard_values)
            graph, ov = self.fresh()
            w = policies.greedy_from(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")), "g:1", 3)
            self.assertTrue(w.chain and not w.guard_values)
        invented.assert_not_called()
        given.assert_not_called()
        graph, ov = self.fresh()
        self.assertTrue(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")).guard_values)   # a model's

    def test_a_name_is_compiled_once_and_only_when_the_text_holds_it(self):
        verify._name_pattern.cache_clear()
        catalog = [("g:1", "Qzxa1"), ("g:2", "Qzxb2"), ("g:3", "Qzxc3"), ("g:3", "QZXC3-AS")]
        text = "Qzxa1 rises while qzxb2 falls; Qzxa1 again."
        first = verify._mentions(text, catalog)
        self.assertEqual([(m[2], text[m[0]:m[1]]) for m in first],
                         [("g:1", "Qzxa1"), ("g:2", "qzxb2"), ("g:1", "Qzxa1")])
        self.assertEqual(verify._name_pattern.cache_info().misses, 2)        # Qzxc3 and QZXC3-AS: absent
        for _ in range(5):
            self.assertEqual(verify._mentions(text, catalog), first)
        self.assertEqual(verify._name_pattern.cache_info().misses, 2)        # never compiled twice
        self.assertEqual(verify._mentions("nothing named here", catalog), [])
        self.assertEqual(verify._name_pattern.cache_info().misses, 2)
        self.assertEqual([m[2] for m in verify._mentions("mmu-Qzxc3 and Qzxc3x", catalog)], ["g:3"])


class LayerCapTest(unittest.TestCase):
    """A node's layer text costs the model at most MEMBERS_SHOWN members of
    one omic. On y712svrXTf (2026-10-29) one gene carried 241 "miRNA unmapped"
    layers, 28,473 characters, and the walk overflowed the model's window."""
    MIRNA = "miRNA unmapped"
    HIDDEN = "−7.77"           # the value of member 0, which is not relevant

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    @classmethod
    def member(cls, i):
        """Member i: relevant every sixth (40 of 241), value (i + 1) / 10."""
        values = [-7.77] if i == 0 else [(i + 1) / 10.0]
        return {"omic": cls.MIRNA, "member": "mmu-miR-%d" % i, "relevant": i % 6 == 1, "values": values,
                "text": ov_mod.values_text(values, None), "significance": [], "signed": None}

    def fresh(self):
        graph = net_mod.build_network("tst", self.data_dir)
        ov = ov_mod.overlay_job(graph, fx.make_job())
        ov.layers["g:1"].extend(self.member(i) for i in range(241))
        return graph, ov

    def mirna_lines(self, text):
        return [line for line in text.splitlines() if line.startswith(self.MIRNA)]

    def test_241_members_of_one_omic_render_in_a_few_lines_relevant_first(self):
        _graph, ov = self.fresh()
        text = ov.layer_text("g:1")
        lines = self.mirna_lines(text)
        self.assertEqual(len(text.splitlines()), 1 + ov_mod.MEMBERS_SHOWN + 1, text)
        shown, more = lines[:-1], lines[-1]
        self.assertEqual({line.split()[3] for line in shown}, {"relevant"}, shown)   # never "not"
        # the largest relevant values first: members 235, 229, ...
        self.assertEqual([line.split()[2] for line in shown],
                         ["mmu-miR-%d" % i for i in range(235, 235 - 6 * ov_mod.MEMBERS_SHOWN, -6)])
        self.assertTrue(more.endswith("… and 233 more members (32 relevant)"), more)
        self.assertEqual(verify.quoted_values(more), [], "the summary line must quote no value")
        self.assertIn("0h +0.10 · 2h +1.50 · 6h +2.20", text, "the other omic is untouched")
        self.assertLess(len(text), 2000)
        self.assertEqual(len(ov.layer_text("g:1", limit=None).splitlines()), 242)

    def test_a_node_with_few_layers_renders_unchanged(self):
        graph = net_mod.build_network("tst", self.data_dir)
        ov = ov_mod.overlay_job(graph, fx.make_job())
        for node_id in ov.layers:
            self.assertEqual(ov.layer_text(node_id), ov.layer_text(node_id, limit=None), node_id)
        self.assertEqual(ov.layer_text("g:1").splitlines()[0].split()[:3], ["Gene", "expression", "Aaa"])

    def test_a_p_value_column_does_not_decide_the_largest_member(self):
        _graph, ov = self.fresh()
        rows = [[0.01, 0.99]] + [[0.1 * k, 0.001] for k in range(1, 9)]
        ov.layers["g:2"] = [{"omic": "Limma", "member": "m%d" % k, "relevant": False, "values": values,
                             "text": ov_mod.values_text(values, ["logFC", "P.Value"]), "significance": [1],
                             "signed": [0]} for k, values in enumerate(rows)]
        text = ov.layer_text("g:2")
        self.assertNotIn(" m0 ", text, "a p-value of 0.99 ranked a logFC of 0.01 as the largest")
        self.assertTrue(text.endswith("… and 1 more member (0 relevant)"), text)

    def test_a_kept_layer_is_shown_whatever_the_cap(self):
        _graph, ov = self.fresh()
        hidden = next(layer for layer in ov.layers["g:1"] if layer.get("member") == "mmu-miR-0")
        self.assertNotIn(self.HIDDEN, ov.layer_text("g:1"))
        text = ov.layer_text("g:1", keep=[hidden])
        self.assertIn(self.HIDDEN, text)
        self.assertEqual(len(self.mirna_lines(text)), ov_mod.MEMBERS_SHOWN + 1)

    def test_the_walkers_answer_carries_the_capped_text(self):
        graph, ov = self.fresh()
        opening = Walker(graph, ov, "network", params_for("network")).start_at("g:1", 4, "test")
        self.assertLessEqual(opening.count("mmu-miR-"), ov_mod.MEMBERS_SHOWN)
        self.assertIn("… and 233 more members", opening)

    def test_verify_reads_a_member_the_text_no_longer_shows(self):
        graph, ov = self.fresh()
        walker = policies.greedy_from(Walker(graph, ov, "network", params_for("network")), "g:1", 2)
        self.assertIn("g:1", verify.chain_nodes(walker.record()["chain"]))
        self.assertNotIn(self.HIDDEN, ov.layer_text("g:1"))
        numbers = verify._record_numbers(walker)
        self.assertIn(verify._norm(self.HIDDEN), numbers)
        self.assertEqual(verify.unrecorded_values("miR-0 read %s on Aaa." % self.HIDDEN, numbers), [])
        self.assertEqual(verify.unrecorded_values("miR-0 read −7.78 on Aaa.", numbers), ["−7.78"])

    def test_the_chat_tools_cap_a_genes_layers_too(self):
        from src.classes.AIInterpret import tools
        gene = fx.FakeFeature("1", [fx.FakeOmicValue(self.MIRNA, "mmu-miR-%d" % i, i % 6 == 1, [(i + 1) / 10.0])
                                    for i in range(241)], name="Aaa")
        lines = tools._format_gene_omics(gene, {}).splitlines()
        self.assertEqual(len(lines), ov_mod.MEMBERS_SHOWN + 1)
        self.assertTrue(lines[-1].endswith("… and 233 more members (32 relevant)"), lines[-1])


class HeatIsNotAValueTest(unittest.TestCase):
    """The walker printed a neighbour as "rrm1 r=0 2.43", showed an r=0
    neighbour's layers nowhere, and a reading stepping onto it wrote "log2FC
    +2.43 (relevant)": the heat, quoted as the user's value (74Qe0uN7n6,
    2026-10-31). Here Ccc plays rrm1: r=0, heat 2.43, values +0.05 +0.02 −0.10."""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def walker(self, heats=None):
        graph = net_mod.build_network("tst", self.data_dir).filter_pathway("KEGG:tst00001")
        ov = ov_mod.overlay_job(graph, fx.make_job())
        for node_id, heat in dict({"g:2": 1.11, "g:3": 2.43}, **(heats or {})).items():
            ov.heat[node_id] = dict(ov.heat[node_id], heat=heat)
        w = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        opening = w.start_at("g:2", 6, "test")                     # standing on Bbb; Ccc is a neighbour
        return w, opening

    def test_a_neighbours_heat_is_labelled_heat(self):
        w, opening = self.walker()
        self.assertIn("Ccc r=0 heat 2.43 [", opening)
        self.assertIsNone(re.search(r"r=[\d—] \d", opening), opening)
        w.ranked = None
        scan = w.scan("graph")
        if "not candidates, hottest:" in scan:
            self.assertIsNone(re.search(r"r=\d \d", scan), scan)
            self.assertIn(" heat ", scan.split("not candidates, hottest:")[1])

    def test_a_reading_quoting_the_heat_as_a_value_is_refused_with_the_layers(self):
        w, _opening = self.walker()
        for reading in ("Gene expression on Ccc shows log2FC +2.43 (relevant), a strong induction",
                        "Ccc Gene expression rises to 2.43"):
            answer = w.step("Ccc", reading, "the hottest neighbour")
            self.assertTrue(answer.startswith("REFUSED"), answer)
            self.assertIn("2.43 is the heat of Ccc, not a measured value", answer)
            self.assertIn("Ccc's layers:", answer)
            self.assertIn(w.overlay.layer_text("g:3").splitlines()[0].split()[-1], answer)   # its real value
            self.assertTrue(answer.endswith("Nothing spent."), answer)
        answer = w.step("Ccc", "Ccc Gene expression stays flat", "Bbb's neighbour at +3.10")
        self.assertTrue(answer.startswith("REFUSED"), answer)        # the reason is read too
        self.assertIn("The reason quotes +3.10", answer)
        self.assertEqual((w.chain, w.budget["steps"], w.refusals), ([], 6, 3))

    def test_a_reading_quoting_a_layers_value_or_the_heat_as_heat_is_accepted(self):
        for reading in ("Ccc Gene expression 0h +0.05 · 6h −0.10, not relevant",          # the target's
                        "Ccc Gene expression is flat while Bbb sits at −1.20 at 2h",         # where it stands
                        "Ccc Gene expression is not relevant; heat 2.43 comes from its neighbours",
                        "Ccc Gene expression is not relevant, with a heat of 2.43"):
            w, _opening = self.walker()
            answer = w.step("Ccc", reading, "the hottest neighbour (heat 2.43)")
            self.assertTrue(answer.startswith("e1"), (reading, answer))
        w.step("Eee", "Eee Proteomics 2h +2.10 · 6h +2.50", "on")
        answer = w.step("Ddd", "Ddd Gene expression 0h -2.00, as Bbb fell to -1.20", "a chain node's value")
        self.assertTrue(answer.startswith("e3"), answer)
        self.assertEqual(w.refusals, 0)

    def test_a_heat_given_its_count_or_called_hottest_is_not_refused(self):
        """55 of 1846 legs in the final3 records (2026-10-31) wrote a heat this
        way, without the word "heat"; each refusal would have cost a turn."""
        for reason in ("Ccc is the hottest 1-step node from Bbb (2.43, 2 of 19 relevant)",
                       "Ccc sits on the hottest branch (Bbb at 1.11, Ccc at 2.43)"):
            w, _opening = self.walker()
            answer = w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", reason)
            self.assertTrue(answer.startswith("e1"), (reason, answer))
        for reason in ("Ccc (+2.43, 2 of 19)", "the hottest neighbour; Ccc shows 2.43",
                       "Ccc, the hottest node, rises 2.43-fold"):
            w, _opening = self.walker()
            answer = w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", reason)
            self.assertTrue(answer.startswith("REFUSED"), (reason, answer))

    def test_layers_shown_moves_ago_may_still_be_quoted(self):
        """Aaa's layers were printed at Bbb as a relevant neighbour's; two
        moves on, Aaa is neither adjacent nor on the chain."""
        w, opening = self.walker()
        self.assertIn("Aaa (r=1", opening)
        w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", "read it")
        answer = w.step("Eee", "Eee Proteomics 2h +2.10, as Aaa reached +2.20 at 6h", "on")
        self.assertTrue(answer.startswith("e2"), answer)
        self.assertNotIn("g:1", w.network.neighbours("g:3"))              # not adjacent to Ccc
        self.assertNotIn("g:1", {n for leg in w.chain for n in (leg.src, leg.dst)})   # nor on the chain
        self.assertIn("g:1", w.layers_shown)

    def test_a_detail_blocks_values_stand_two_steps_on_in_a_note_and_the_stop(self):
        """74Qe0uN7n6, round heat1 (2026-10-31): pkmb's "relevant neighbours'
        layers" showed gapdhs +0.92, eno1a +0.79, ldha +0.84; five steps on, a
        note and the stop quoting those true values were refused."""
        w, opening = self.walker()
        self.assertIn("relevant neighbours' layers:", opening)
        self.assertIn("6h +2.20", opening.split("relevant neighbours' layers:")[1])   # Aaa's, in the block
        w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", "read it")
        w.step("Eee", "Eee Proteomics 2h +2.10", "on")
        self.assertNotIn("g:1", w.network.neighbours(w.current))
        self.assertTrue(w.note("Aaa +2.20 (relevant) and Eee +2.50 rise together").startswith("noted"))
        self.assertTrue(w.note("Nodes hot without their own change: Ccc heat 2.43, Bbb 1.11").startswith("noted"))
        self.assertTrue(w.stop("Aaa 2h +1.50 · 6h +2.20 (relevant) leads; Eee 6h +2.50", "done").startswith("done ·"))
        self.assertEqual(w.refusals, 0)

    def test_a_heat_a_scan_printed_is_held_to_the_heat_check(self):
        w, _opening = self.walker()
        w.overlay.heat["g:5"] = dict(w.overlay.heat["g:5"], heat=3.15)   # Eee: two steps from Bbb
        reason = "Eee rises 3.15 two steps out"
        self.assertNotIn("g:5", w._readable("g:3"))
        self.assertIn("Eee", w.scan("here", 2))
        self.assertIn("g:5", w.heats_shown)
        answer = w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", reason)
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("3.15 is the heat of Eee", answer)
        self.assertNotIn("g:5", w._readable("g:3"))                      # a heat, never a value source

    def test_a_heat_given_to_its_node_is_refused_though_another_node_holds_it(self):
        """Bgm0v4EU01, round heat2 (2026-10-31): "Pkm ... shows Control +0.52,
        up like Ranbp9 (+0.52)"; Pkm reads +0.04, heat 0.52, and Ranbp9's
        +0.52 put the value in the pool. Here Ccc is Pkm (heat 1.50) and Aaa
        Ranbp9 (2h +1.50)."""
        w, _opening = self.walker({"g:3": 1.50, "g:1": 0.77})
        answer = w.step("Ccc", "Ccc Gene expression is not relevant and shows 2h +1.50, up like Aaa (+1.50)", "on")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("The reading gives Ccc +1.50, which its layers do not hold: 1.50 is Ccc's heat, "
                      "not a measured value.", answer)
        self.assertIn("Ccc's layers:", answer)
        self.assertNotIn("which no layer you can read holds", answer)
        self.assertEqual(w.chain, [])

    def test_a_heat_attribution_needs_the_value_given_to_that_node(self):
        cases = (
            # +1.50 sits after Aaa, not Ccc (Gli1 (+1.77) ... Gli2 (+1.75), Gli2's heat 1.77)
            ({"g:3": 1.50, "g:1": 0.77}, "Ccc Gene expression 6h −0.10, while Aaa (+1.50) rises"),
            # Ccc's own layers hold a value equal to its heat
            ({"g:3": 0.05}, "Ccc Gene expression 0h +0.05, not relevant"),
            # written as heats
            ({"g:3": 0.35}, "Ccc Gene expression is flat, its neighbours' heat 0.42–0.35"),
            ({"g:3": 3.54}, "Ccc Gene expression is flat; Ccc, the hottest (3.54), is not relevant"),
        )
        for heats, reading in cases:
            w, _opening = self.walker(heats)
            answer = w.step("Ccc", reading, "on")
            self.assertTrue(answer.startswith("e1"), (reading, answer))

    def test_a_value_after_a_group_of_names_is_not_given_to_the_nearest(self):
        """"Ranbp9 and Pkm both rise (+0.52, +0.04)" is a true reading: the
        values go to the names in order. Ccc is Pkm (heat 1.50), Aaa Ranbp9."""
        for reading in ("Aaa and Ccc both move in Gene expression (+1.50, −0.10)",
                        "Gene expression of Aaa and Ccc (+1.50)"):
            w, _opening = self.walker({"g:3": 1.50, "g:1": 0.77})
            answer = w.step("Ccc", reading, "on")
            self.assertTrue(answer.startswith("e1"), (reading, answer))

    def test_a_heat_hyphened_to_its_word_passes_the_guard(self):
        """"M1CVD2, the remaining heat-1.32 sibling, is two steps away" was
        refused in round heat5 (2026-10-31): the heat, read as a value."""
        w, _opening = self.walker()
        answer = w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant",
                        "Ccc, the remaining heat-2.43 sibling, is next")
        self.assertTrue(answer.startswith("e1"), answer)

    def test_a_value_is_reported_once_whichever_text_quotes_it(self):
        w, _opening = self.walker()
        answer = w.step("Ccc", "Ccc Gene expression shows +2.43", "Ccc at +2.43 leads")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("The reading and the reason quote +2.43, which no layer you can read holds.", answer)
        self.assertNotIn("gives Ccc", answer)
        self.assertEqual(answer.count("2.43 is the heat of"), 1, answer)

    def test_a_heat_quoted_in_scientific_form_is_named_as_printed(self):
        w, _opening = self.walker({"g:3": 0.52})
        answer = w.step("Ccc", "Ccc Gene expression shows +5.2e-1", "on")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("0.52 is the heat of Ccc", answer)
        self.assertNotIn("5.2e-1 is the heat", answer)

    def test_note_stop_and_jump_are_held_to_the_layers_too(self):
        w, _opening = self.walker()
        answer = w.note("Ccc +2.43 (relevant) leads this neighbourhood")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("2.43 is the heat of Ccc", answer)
        self.assertIn("Ccc's layers:", answer)
        self.assertEqual((w.notes, w.budget["notes"]), ([], 3))
        self.assertTrue(w.note("Ccc has heat 2.43 but its own values are flat").startswith("noted"))
        w.step("Ccc", "Ccc Gene expression 6h −0.10, not relevant", "read it")
        answer = w.jump("Bbb", "Bbb Gene expression +2.43", "back")
        self.assertTrue(answer.startswith("REFUSED"), answer)
        self.assertIn("Bbb's layers:", answer)                    # the target's, to quote instead
        self.assertIn("−1.20", answer)
        self.assertTrue(w.jump("Bbb", "Bbb Gene expression 2h −1.20", "back").startswith("J2"))
        answer = w.stop("Ccc rises +2.43", "done")
        self.assertTrue(answer.startswith("REFUSED") and not w.done, answer)
        self.assertTrue(w.stop("Bbb 2h −1.20 and Ccc flat (heat 2.43)", "done").startswith("done ·"))
        self.assertEqual(w.refusals, 3)


if __name__ == "__main__":
    unittest.main()
