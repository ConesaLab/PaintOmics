#!/usr/bin/env python3
"""Edge tiers, currency metabolites and the title lexicon (checks 1 and 2 of
the five): the parser records the compound a relation is drawn through, the
network drops a relation through ATP, the walk never steps onto a currency
compound, every leg carries its tier, and a title may say "drives" only when a
mechanism statement stands behind it. Offline, synthetic organism."""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import network as net_mod    # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod     # noqa: E402
from src.classes.AIInterpret.walker import policies              # noqa: E402
from src.classes.AIInterpret.walker import tiers                 # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.common.KeggGraph import parser                          # noqa: E402
from src.tests import walker_fixture as fx                       # noqa: E402

# Pathway one of the fixture plus two enzymes joined through ATP (dropped) and
# two joined through pyruvate (kept), and pyruvate itself as a reaction partner
# of C, so the graph holds a compound node a walk could step onto.
KGML_CURRENCY = fx.KGML_1.replace(
    '  <relation entry1="1" entry2="2" type="PPrel">',
    '  <entry id="20" name="cpd:C00002" type="compound"><graphics name="ATP" type="circle" x="10" y="10" width="8" height="8"/></entry>\n'
    '  <entry id="21" name="cpd:C00022" type="compound"><graphics name="Pyruvate" type="circle" x="20" y="20" width="8" height="8"/></entry>\n'
    '  <entry id="22" name="tst:8" type="gene"><graphics name="H" type="rectangle" x="400" y="100" width="46" height="17"/></entry>\n'
    '  <entry id="23" name="tst:9" type="gene"><graphics name="I" type="rectangle" x="500" y="100" width="46" height="17"/></entry>\n'
    '  <relation entry1="22" entry2="23" type="ECrel"><subtype name="compound" value="20"/></relation>\n'
    '  <relation entry1="22" entry2="3" type="ECrel"><subtype name="compound" value="21"/></relation>\n'
    '  <reaction id="3" name="rn:R00200" type="reversible"><substrate id="21" name="cpd:C00022"/><product id="20" name="cpd:C00002"/></reaction>\n'
    '  <relation entry1="1" entry2="2" type="PPrel">')


class ParserViaTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = fx.make_data_dir()
        with open(os.path.join(self.data_dir, "current", "tst", "kgml", "tst00001.kgml"), "w") as handle:
            handle.write(KGML_CURRENCY)

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_a_compound_relation_names_its_mediator(self):
        edges, types = parser.parse_pathway(os.path.join(self.data_dir, "current", "tst", "kgml", "tst00001.kgml"))
        by_pair = {(e.a, e.b): e for e in edges}
        self.assertEqual(by_pair[("8", "9")].via, "C00002")
        self.assertEqual(by_pair[("8", "3")].via, "C00022")
        self.assertIsNone(by_pair[("1", "2")].via)
        self.assertEqual(by_pair[("1", "2")].subtype, "activation")
        self.assertEqual(types["C00002"], "compound")
        # six positional arguments still build an Edge (the hub code does this)
        self.assertIsNone(parser.Edge("a", "b", "PPrel", "", "p", False).via)

    def test_the_network_drops_the_relation_through_atp_and_keeps_the_other(self):
        net = net_mod.build_network("tst", self.data_dir)
        self.assertNotIn(("g:8", "g:9"), net.edges)
        self.assertIn(("g:8", "g:3"), net.edges)
        self.assertIn("c:C00002", net.nodes)                 # the node stays; the walk skips it
        self.assertIn(("g:3", "c:C00002"), net.edges)

    def test_the_walk_never_steps_onto_a_currency_compound(self):
        net = net_mod.build_network("tst", self.data_dir)
        graph = net.filter_pathway("KEGG:tst00001")
        job = fx.make_job()
        # make ATP measured and relevant, the hottest thing around C
        job._compounds = {"C00002": fx.FakeFeature("C00002", [fx.FakeOmicValue("Metabolomics", "ATP", True, [2.0, 2.0, 2.0])]),
                          "C00022": fx.FakeFeature("C00022", [fx.FakeOmicValue("Metabolomics", "Pyruvate", True, [1.0, 1.0, 1.0])])}
        ov = ov_mod.overlay_job(graph, job)
        walker = Walker(graph, ov, "KEGG:tst00001", params_for("pathway"))
        walker.scan("graph")
        self.assertFalse(any(row["candidate"] for row in walker.ranked if row["id"] == "c:C00002"))
        policies.greedy(walker)
        touched = {n for leg in walker.chain for n in (leg.src, leg.dst)}
        self.assertNotIn("c:C00002", touched)
        for leg in walker.chain:
            if leg.kind == "step":
                self.assertIn(leg.edge["tier"], (1, 2))
        answer = walker.step("ATP", "ATP Metabolomics +2.00", "test")
        self.assertTrue(answer.startswith("REFUSED"))


class TierRulesTest(unittest.TestCase):
    def test_edge_tiers(self):
        one = [{"db": "KEGG", "subtype": "activation"}, {"db": "KEGG", "subtype": "inhibition,phosphorylation"},
               {"db": "KEGG", "subtype": "expression"}, {"db": "KEGG", "subtype": "rn:R00200"},
               {"db": "Reactome", "subtype": "reaction"}, {"db": "Reactome", "subtype": "inhibition"},
               {"db": "OmniPath", "subtype": "stimulation"}, {"db": "anchor", "subtype": "transcriptional regulation"}]
        two = [{"db": "KEGG", "subtype": "binding/association"}, {"db": "KEGG", "subtype": "activation,indirect effect"},
               {"db": "KEGG", "subtype": "compound"}, {"db": "KEGG", "subtype": "compound,activation"},
               {"db": "KEGG", "subtype": ""}, {"db": "OmniPath", "subtype": "unsigned"},
               {"db": "job", "subtype": "miRNA target"}, None]
        for edge in one:
            self.assertEqual(tiers.edge_tier(edge), 1, edge)
        for edge in two:
            self.assertEqual(tiers.edge_tier(edge), 2, edge)

    def test_statement_tier(self):
        chain = [{"n": 1, "kind": "step", "edge": {"db": "KEGG", "subtype": "activation", "tier": 1}},
                 {"n": 2, "kind": "jump", "edge": None},
                 {"n": 3, "kind": "step", "edge": {"db": "KEGG", "subtype": "binding/association", "tier": 2}}]
        self.assertEqual(tiers.statement_tier({"legs": [1]}, chain), "mechanism")
        self.assertEqual(tiers.statement_tier({"legs": [1, 2]}, chain), "mechanism")
        self.assertEqual(tiers.statement_tier({"legs": [1, 3]}, chain), "association")
        self.assertEqual(tiers.statement_tier({"legs": [2]}, chain), "association")
        self.assertEqual(tiers.statement_tier({"legs": []}, chain), "association")
        self.assertEqual(tiers.statement_tier({"legs": [1, True, "x"]}, chain), "mechanism")

    def test_verbs(self):
        self.assertEqual(tiers.mechanistic_verbs("Ikaros perturbation drives a BCR programme and shuts down IRS1"),
                         ["drives", "shuts down"])
        self.assertEqual(tiers.mechanistic_verbs("Foxo1 is induced by Ikaros"), ["induced"])
        self.assertEqual(tiers.mechanistic_verbs("Foxo1 is induced late and Ccnd2 is repressed"), [])
        self.assertEqual(tiers.mechanistic_verbs("Foxo1 rises to +2.13 while Ccnd2 falls; both are higher at 24h"), [])
        self.assertEqual(tiers.mechanistic_verbs("Cish suppression derepresses JAK-STAT signalling"), ["derepresses"])
        self.assertEqual(tiers.mechanistic_verbs("the induction of Il2rg accompanies a fall in Cish"), [])
        # nouns of the trade are not verbs
        self.assertEqual(tiers.mechanistic_verbs("Ikaros over control; calcium release coupled with a fall in Ccnd2"), [])
        self.assertEqual(tiers.mechanistic_verbs("Foxo1 controls Ccnd2 and releases the brake"), ["controls", "releases"])
        self.assertIn("inhibits", tiers.mechanistic_verbs("Pten inhibits PI3K signalling"))
        self.assertEqual(tiers.mechanistic_verbs("Ikaros perturbation engages a PI3K/FoxO axis and recruits Smad3"),
                         ["engages", "recruits"])
        self.assertEqual(tiers.mechanistic_verbs("Foxo1 was engaged by Ikaros; Ccnd2 was released"), ["engaged"])

    def test_currency_ids(self):
        self.assertTrue(tiers.is_currency("c:C00002"))
        self.assertFalse(tiers.is_currency("c:C00022"))
        self.assertFalse(tiers.is_currency("g:22778"))
        for cid in ("C00001", "C00009", "C00013", "C00003"):
            self.assertIn(cid, tiers.CURRENCY)

    def test_neutral_title(self):
        card = {"perturbation": "Ikaros induced by tamoxifen in mouse B3 pre-B cells, six time points"}
        # the card's clause is cut before its own verb
        self.assertEqual(tiers.neutral_title("FoxO signaling pathway", card),
                         "FoxO signaling pathway: what changed after Ikaros")
        self.assertEqual(tiers.neutral_title("FoxO signaling pathway", {"perturbation": "Ikaros perturbation over control in mouse"}),
                         "FoxO signaling pathway: what changed after Ikaros perturbation over control in mouse")
        self.assertEqual(tiers.neutral_title("The whole network", card, {"gene": "Ikzf1", "direction": "up"}),
                         "The whole network: what changed after Ikzf1 induction")
        self.assertEqual(tiers.neutral_title("x", card, {"gene": "Pten", "direction": "down"}), "x: what changed after Pten loss")
        self.assertTrue(tiers.neutral_title("network", {}).endswith("after the perturbation"))
        self.assertLessEqual(len(tiers.neutral_title("x", {"perturbation": "a" * 200}).split(": ")[1]), 80)
        for title in (tiers.neutral_title("x", card), tiers.neutral_title("x", card, {"gene": "Ikzf1", "direction": "up"})):
            self.assertEqual(tiers.mechanistic_verbs(title), [])


class TitleGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        net = net_mod.build_network("tst", cls.data_dir)
        cls.graph = net.filter_pathway("KEGG:tst00001")
        cls.ov = ov_mod.overlay_job(cls.graph, fx.make_job())
        cls.walker = policies.greedy(Walker(cls.graph, cls.ov, "KEGG:tst00001", params_for("pathway")))
        cls.chain = cls.walker.record()["chain"]
        cls.mech_leg = next(leg for leg in cls.chain if leg["kind"] == "step" and leg["edge"]["tier"] == 1)
        cls.assoc_leg = next((leg for leg in cls.chain if leg["kind"] == "step" and leg["edge"]["tier"] == 2), None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def statement(self, n, leg):
        node = leg["to"]
        return {"n": n, "claim": "%s changes" % self.walker.label(node), "prose": "",
                "cites": [[self.walker.label(node), "Gene expression"]], "legs": [leg["n"]],
                "tier": tiers.statement_tier({"legs": [leg["n"]]}, self.chain)}

    def test_a_mechanism_statement_backs_a_mechanistic_title_about_its_gene(self):
        stmt = self.statement(1, self.mech_leg)
        self.assertEqual(stmt["tier"], "mechanism")
        gene = self.walker.label(self.mech_leg["to"])
        results = {"title": "Aaa drives %s" % gene, "summary": "%s rose after the perturbation." % gene}
        self.assertEqual(tiers.title_outruns_body(results, [stmt], self.walker), [])

    def test_an_all_association_body_allows_no_mechanistic_verb(self):
        if self.assoc_leg is None:
            self.skipTest("the fixture walk took no association leg")
        stmt = self.statement(1, self.assoc_leg)
        self.assertEqual(stmt["tier"], "association")
        results = {"title": "Aaa drives Eee", "summary": "Eee rose."}
        problems = tiers.title_outruns_body(results, [stmt], self.walker)
        self.assertEqual(len(problems), 1)
        self.assertIn("no kept statement rests on a mechanism edge", problems[0])
        results = {"title": "Aaa and Eee", "summary": "Eee rose while Ddd fell."}
        self.assertEqual(tiers.title_outruns_body(results, [stmt], self.walker), [])

    def test_a_named_gene_needs_a_mechanism_statement_about_it(self):
        stmt = self.statement(1, self.mech_leg)
        other = [name for name in ("Aaa", "Bbb", "Ccc", "Ddd", "Eee")
                 if name != self.walker.label(self.mech_leg["to"]) and name != self.walker.label(self.mech_leg["from"])][0]
        results = {"title": "Ikaros induction rewires %s" % other, "summary": ""}
        problems = tiers.title_outruns_body(results, [stmt], self.walker, anchor=None)
        self.assertEqual(len(problems), 1)
        self.assertIn("names no perturbed gene", problems[0])
        results = {"title": "%s is rewired" % other, "summary": "The data rewires %s." % other}
        problems = tiers.title_outruns_body(results, [stmt], self.walker)
        self.assertEqual(len(problems), 1)
        self.assertIn(other, problems[0])

    def test_the_perturbation_as_subject_needs_an_anchored_mechanism(self):
        stmt = self.statement(1, self.mech_leg)
        results = {"title": "Ikaros perturbation drives the response", "summary": ""}
        unanchored = tiers.title_outruns_body(results, [stmt], self.walker, anchor={"gene": "Ikzf1", "in_graph": False})
        self.assertEqual(len(unanchored), 1)
        far = tiers.title_outruns_body(results, [stmt], self.walker,
                                       anchor={"gene": "Zzz", "node": "g:999", "in_graph": True})
        self.assertEqual(len(far), 1)
        self.assertIn("no mechanism statement rests on a leg at Zzz", far[0])
        near = tiers.title_outruns_body(results, [stmt], self.walker,
                                        anchor={"gene": "Aaa", "node": self.mech_leg["from"], "in_graph": True})
        self.assertEqual(near, [])

    def test_dropping_mechanistic_sentences(self):
        results = {"title": "t", "summary": "Foxo1 drives Ccnd2 down. Ccnd2 fell to −3.25. Ikaros rewires the cycle."}
        self.assertEqual(tiers.drop_mechanistic_sentences(results), 2)
        self.assertEqual(results["summary"], "Ccnd2 fell to −3.25.")


if __name__ == "__main__":
    unittest.main()
