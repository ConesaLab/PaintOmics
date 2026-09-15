#!/usr/bin/env python3
"""The five gates on a whole run: service.run on the synthetic organism with
the scripted policy seals a record whose checks carry five verdicts, a
rendered flag and the anchor, and the view hands them to the client. The
context gate and the title gate are exercised on hand-built statements and
Results. Offline, no model."""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod      # noqa: E402
from src.classes.AIInterpret.walker import policies               # noqa: E402
from src.classes.AIInterpret.walker import service                # noqa: E402
from src.classes.AIInterpret.walker import tiers                  # noqa: E402
from src.classes.AIInterpret.walker import verify                 # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                        # noqa: E402


class AnchoredJob(fx.FakeJob):
    """The fixture job whose design names Aaa as the induced gene."""

    def getExperimentDesign(self):
        return "Aaa was induced by tamoxifen in a mouse cell line over three time points. Values are log2 fold changes."


class GatesOnARunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        org = os.path.join(cls.data_dir, "current", "tst")
        with open(os.path.join(org, "mapping", "tf_targets.tsv"), "w") as handle:
            handle.write("tf\ttarget\tsign\tsource\treferences\nAaa\tEee\t1\tTEST\t1\nAaa\tGgg\t1\tTEST\t2\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def test_a_greedy_run_carries_the_five_gates(self):
        job = fx.make_job()
        job.__class__ = AnchoredJob
        rec, network, graph, tag = service.run(job, "job1", "pathway:tst00001", policy="greedy",
                                               data_dir=self.data_dir, use_mongo=False)
        checks = rec["checks"]
        self.assertEqual(rec["schema"], 2)
        self.assertEqual(sorted(checks["gates"]), sorted(service.GATES))
        for name in service.GATES:
            self.assertIsInstance(checks["gates"][name]["pass"], bool, name)
        self.assertIn("rendered", checks)
        self.assertEqual(checks["rendered"], all(checks["gates"][n]["pass"] for n in service.GATES))
        # the anchor was read from the design, connected by its target edge, and measured
        anchor = checks["anchor"]
        self.assertEqual(anchor["gene"], "Aaa")
        self.assertEqual(anchor["node"], "g:1")
        self.assertTrue(anchor["in_graph"])
        self.assertIn(("g:1", "g:5"), graph.edges)                # Eee is in pathway one
        self.assertEqual(rec["design_card"]["perturbed_genes"], ["Aaa"])
        self.assertEqual(rec["design_card"]["perturbation_direction"], "up")
        self.assertEqual(rec["design_card"]["organism"], "tst")
        # the artifact gate ran on this walk
        artifact = checks["gates"]["artifact"]
        self.assertEqual(artifact["k"], service.NULL_K)
        self.assertEqual(artifact["currency_legs"], 0)
        self.assertIn("p_modules", artifact)
        # the anchor gate compares the walk with the measured nodes at large
        gate = checks["gates"]["anchor"]
        self.assertFalse(gate["not_applicable"])
        self.assertIsNotNone(gate["reachability"])
        # every leg carries its tier and every segment its distance
        for leg in rec["walk"]["chain"]:
            if leg["kind"] == "step":
                self.assertIn(leg["edge"]["tier"], (1, 2))
        # no statements were written, so the three model gates are not applicable and pass
        for name in ("title", "direction", "context"):
            self.assertTrue(checks["gates"][name]["not_applicable"])
            self.assertTrue(checks["gates"][name]["pass"])
        view = service.view(rec)
        self.assertEqual(view["checks"]["gates"], checks["gates"])
        self.assertEqual(view["checks"]["anchor"]["gene"], "Aaa")
        self.assertIsInstance(view["checks"]["rendered"], bool)
        self.assertIn("segments", view)
        self.assertEqual(view["design_card"]["system"], "not stated")

    def test_an_unanchored_run_says_so(self):
        rec, _network, _graph, _tag = service.run(fx.make_job(), "job2", "pathway:tst00001", policy="greedy",
                                                  data_dir=self.data_dir, use_mongo=False)
        self.assertIsNone(rec["checks"]["anchor"])
        gate = rec["checks"]["gates"]["anchor"]
        self.assertTrue(gate["not_applicable"])
        self.assertTrue(gate["pass"])
        self.assertEqual(rec["design_card"]["perturbed_genes"], [])

    def test_the_user_card_names_the_gene(self):
        card = service.clean_card({"perturbation": "x", "perturbed_genes": "Alpha, aaa, zzz",
                                   "perturbation_direction": "Down", "system": "B cells"})
        self.assertEqual(card["perturbed_genes"], ["Alpha", "aaa", "zzz"])
        self.assertEqual(card["perturbation_direction"], "down")
        self.assertEqual(card["system"], "B cells")
        self.assertNotIn("perturbation_direction", service.clean_card({"perturbation_direction": "sideways"}) or {})
        rec, _n, _g, _t = service.run(fx.make_job(), "job3", "pathway:tst00001", policy="greedy",
                                      data_dir=self.data_dir, use_mongo=False,
                                      card_override={"perturbed_genes": ["Aaa"], "perturbation_direction": "down"})
        self.assertEqual(rec["checks"]["anchor"]["direction"], "down")
        self.assertEqual(rec["design_card"]["source"], "user")

    def test_a_currency_leg_fails_the_artifact_gate(self):
        net = net_mod.build_network("tst", self.data_dir)
        graph = net.filter_pathway("KEGG:tst00001")
        ov = ov_mod.overlay_job(graph, fx.make_job())
        walker = policies.greedy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
        gate = service.artifact_gate(walker)
        self.assertEqual(gate["currency_legs"], 0)
        # forge a leg through ATP onto the sealed chain
        walker.chain[0].src = "c:C00002"
        gate = service.artifact_gate(walker)
        self.assertEqual(gate["currency_legs"], 1)
        self.assertFalse(gate["pass"])
        self.assertIn("currency metabolite", gate["why"])


class ContextGateTest(unittest.TestCase):
    def test_the_gate_counts_qualified_and_unqualified_citations(self):
        papers = {1: {"context": {"organism": "mouse", "system": "B-Lymphocytes",
                                  "match": {"organism": "same", "system": "same"}}},
                  2: {"context": {"organism": "human", "system": "Hepatocytes",
                                  "match": {"organism": "other", "system": "other"}}},
                  3: {"context": {"organism": "human", "system": "unknown",
                                  "match": {"organism": "other", "system": "unknown"}}}}
        kept = [{"n": 1, "claim": "c", "prose": "Foxo1 rose [1]. In human hepatocytes Foxo1 does the same [2].",
                 "papers": [1, 2], "evidence": [{"ref": 1}, {"ref": 2}]},
                {"n": 2, "claim": "c2", "prose": "Cish is a SOCS protein [3].", "papers": [3], "evidence": [{"ref": 3}]}]
        gate = service.context_gate(kept, papers)
        self.assertEqual((gate["cited"], gate["in_context"], gate["qualified"], gate["unqualified_other"]), (3, 1, 1, 1))
        self.assertFalse(gate["pass"])
        self.assertIn("another organism", gate["why"])
        kept[1]["prose"] = "In human cells, Cish is a SOCS protein [3]."
        gate = service.context_gate(kept, papers)
        self.assertEqual(gate["unqualified_other"], 0)
        self.assertTrue(gate["pass"])
        kept[1]["evidence"] = []
        gate = service.context_gate(kept, papers)
        self.assertEqual(gate["unconfirmed"], 1)
        self.assertFalse(gate["pass"])
        self.assertTrue(service.context_gate([], papers)["not_applicable"])

    def test_citing_sentences_and_context_named(self):
        stmt = {"claim": "the claim", "prose": "Foxo1 rose to +2.13 [e3]. FoxO drives Bim [2, 5]. Bim is pro-apoptotic.",
                "beyond": [{"claim": "Bim is a FoxO target", "paper": 2}, {"claim": "x", "paper": 9}]}
        self.assertEqual(verify.citing_sentences(stmt, 2), ["FoxO drives Bim [2, 5]."])
        self.assertEqual(verify.citing_sentences(stmt, 5), ["FoxO drives Bim [2, 5]."])
        self.assertEqual(verify.citing_sentences(stmt, 9), ["x"])
        self.assertEqual(verify.citing_sentences(stmt, 7), ["the claim"])
        ctx = {"organism": "human", "system": "T-Lymphocytes", "match": {"organism": "other", "system": "other"}}
        self.assertTrue(verify.context_named("as shown in human T cells [3]", ctx))
        self.assertTrue(verify.context_named("in patients' lymphocytes [3]", ctx))
        self.assertFalse(verify.context_named("Cish is a SOCS protein [3]", ctx))
        # a system named only by generic words ("HL-60 Cells" -> "HL-60") is not satisfied by "cells"
        hl60 = {"organism": "human", "system": "HL-60 Cells", "match": {"organism": "same", "system": "other"}}
        self.assertFalse(verify.context_named("in these cells Tspan3 falls [2]", hl60))
        self.assertTrue(verify.context_named("in HL-60 cells Tspan3 falls [2]", hl60))
        generic = {"organism": "mouse", "system": "Cells, Cultured", "match": {"organism": "same", "system": "other"}}
        self.assertTrue(verify.context_named("anything", generic))
        self.assertTrue(verify.context_named("anything", {"match": {"organism": "same"}}))
        self.assertTrue(verify.context_named("anything", None))

    def test_an_out_of_context_citation_needs_its_qualifier(self):
        data_dir = fx.make_data_dir()
        try:
            net = net_mod.build_network("tst", data_dir)
            graph = net.filter_pathway("KEGG:tst00001")
            ov = ov_mod.overlay_job(graph, fx.make_job())
            walker = policies.greedy(Walker(graph, ov, "KEGG:tst00001", params_for("pathway")))
            leg = walker.record()["chain"][0]
            node = leg["to"]
            layer = ov.layers[node][0]
            papers = {4: {"pmid": "4", "title": "t", "abstract": "%s in liver" % walker.label(node),
                          "context": {"organism": "human", "system": "Hepatocytes",
                                      "match": {"organism": "other", "system": "other"}}}}
            stmt = {"n": 1, "claim": "a claim", "prose": "%s moves; it is a hub [4]." % walker.label(node),
                    "cites": [[walker.label(node), layer["omic"]]], "legs": [1],
                    "grounded_in": [{"leg": 1, "db": "KEGG"}], "beyond": [], "papers": [4]}
            if not layer["relevant"]:
                stmt["prose"] += " (not relevant)"
            problems = verify.verify_statement(stmt, walker, papers, read={4}, names_genes=False)
            self.assertTrue(any("is a human Hepatocytes paper" in p for p in problems), problems)
            stmt["prose"] = stmt["prose"].replace("it is a hub [4]", "in human hepatocytes it is a hub [4]")
            problems = verify.verify_statement(stmt, walker, papers, read={4}, names_genes=False)
            self.assertFalse(any("paper; say so" in p for p in problems), problems)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


class TitleGateOnResultsTest(unittest.TestCase):
    def test_tiers_are_the_verbs_licence(self):
        chain = [{"n": 1, "kind": "step", "from": "a", "to": "b", "edge": {"db": "KEGG", "subtype": "activation", "tier": 1}}]
        self.assertEqual(tiers.statement_tier({"legs": [1]}, chain), "mechanism")
        results = {"title": "Results", "summary": "Foxo1 rose."}
        self.assertEqual(tiers.mechanistic_verbs(results["title"] + " " + results["summary"]), [])


if __name__ == "__main__":
    unittest.main()
