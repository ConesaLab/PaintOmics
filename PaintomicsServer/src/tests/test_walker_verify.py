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

    def test_paper_refs_from_a_model_are_coerced_not_trusted(self):
        self.assertEqual(verify.paper_ref(3), 3)
        self.assertEqual(verify.paper_ref("3"), 3)
        self.assertEqual(verify.paper_ref("[3]"), 3)
        self.assertIsNone(verify.paper_ref(None))
        self.assertIsNone(verify.paper_ref("abc"))
        self.assertIsNone(verify.paper_ref(True))
        stmt = self.good_statement()
        stmt["papers"] = ["[3]"]
        self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, {3: {}}) if "paper" in p], [])
        stmt["papers"] = ["abc"]
        problems = verify.verify_statement(stmt, self.walker, {3: {}})              # no exception
        self.assertTrue(any("never retrieved" in p for p in problems))
        stmt["papers"] = []
        stmt["beyond"] = [{"claim": "x", "paper": "[3]", "hypothesis": False}]
        self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, {3: {}}) if "beyond" in p], [])

    def test_region_scene_keeps_the_seen_nodes_measured(self):
        from src.classes.AIInterpret.walker import record as record_mod
        from src.classes.AIInterpret.walker import report
        rec = record_mod.seal("job", "KEGG:tst00001", self.graph, self.ov, self.walker, {}, [], [], None, {},
                              "none", {})
        svg, size = report.region_scene(rec, self.graph)
        self.assertIn("<svg", svg)
        self.assertNotIn("#e6e8eb", svg)      # every node of this graph is measured; none may draw as unmeasured
        html_page = report.render(rec, self.graph)
        self.assertIn("Seen, not walked", html_page)

    def test_a_rewrite_with_a_bad_n_is_skipped_not_fatal(self):
        from src.classes.AIInterpret.walker import service

        class _Client(object):
            def complete_json(self, *args, **kwargs):
                return {"statements": [{"n": "one", "claim": "x"}, {"n": 2, "claim": "y"}, "junk", {"claim": "no n"}]}
        out = service.rewrite_once(_Client(), "card", "chain", [{"n": 2, "claim": "y"}], {2: {}})
        self.assertEqual(list(out), [2])

    def test_statement_count(self):
        out, problem = verify.verify_statements([self.good_statement()], self.walker, {})
        self.assertIn("1 statements", problem)

    def test_results_checks(self):
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]   # the first quoted value
        kept = [{"n": 1}]
        text = ("At the first time point in this series, the gene %s read %s [e1]. " % (
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

    def test_citations_are_renumbered_in_order_of_first_use(self):
        from src.classes.AIInterpret.walker import record as record_mod
        papers = {3: {"pmid": "3"}, 7: {"pmid": "7"}, 9: {"pmid": "9"}, 11: {"pmid": "11"}}
        results = {"title": "t", "summary": "first [7].", "paragraphs": [{"from_statement": 1, "legs": [1],
                                                                          "text": "then [3, 7] and [e1]."}]}
        statements = [{"n": 1, "claim": "c", "prose": "p [3]", "papers": [3, 9],
                       "beyond": [{"claim": "b", "paper": 9, "hypothesis": False}]}]
        out = record_mod.renumber_citations(statements, [], results, papers)
        self.assertEqual(sorted(out), [1, 2, 3])
        self.assertEqual(out[1]["pmid"], "7")
        self.assertEqual(out[2]["pmid"], "3")
        self.assertEqual(out[3]["pmid"], "9")
        self.assertEqual(results["summary"], "first [1].")
        self.assertEqual(results["paragraphs"][0]["text"], "then [2, 1] and [e1].")
        self.assertEqual(statements[0]["papers"], [2, 3])
        self.assertEqual(statements[0]["beyond"][0]["paper"], 3)
        self.assertNotIn("11", [p["pmid"] for p in out.values()], "an uncited paper stayed in the list")

    def test_a_cited_paper_must_be_read_and_about_the_claim(self):
        node = self.chain[0]["to"]
        label = self.walker.label(node)
        stmt = self.good_statement()
        stmt["beyond"] = [{"claim": "%s is repressed by the upstream factor" % label, "paper": 3, "hypothesis": False}]
        off_topic = {3: {"title": "A herbal extract and muscle", "abstract": "nothing about it"}}
        self.assertTrue(any("without being read" in p for p in verify.verify_statement(stmt, self.walker, off_topic, set())))
        self.assertTrue(any("does not mention" in p for p in verify.verify_statement(stmt, self.walker, off_topic, {3})))
        on_topic = {3: {"title": "%s repression" % label, "abstract": ""}}
        self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, on_topic, {3}) if "[3]" in p], [])

    def test_a_paragraph_keeps_its_statements_citations(self):
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]
        text = ("At the first time point in this series, the gene %s read %s [e1]. " % (
            self.walker.label(node), value)) * 12
        results = {"title": "t", "summary": "s", "paragraphs": [{"from_statement": 1, "legs": [1], "text": text}]}
        problems = verify.verify_results(results, [{"n": 1, "papers": [2]}], [], self.walker)
        self.assertTrue(any("drops [2]" in p for p in problems), problems)
        results["paragraphs"][0]["text"] = text + "It is documented [2]."
        self.assertFalse(any("drops" in p for p in verify.verify_results(results, [{"n": 1, "papers": [2]}], [],
                                                                         self.walker)))

    def test_a_connective_paragraph_that_quotes_a_value_is_pruned(self):
        from src.classes.AIInterpret.walker import narrate
        results = {"paragraphs": [{"from_statement": 1, "legs": [1], "text": "Aaa rose to +1.00 [1]."},
                                  {"from_statement": None, "legs": [], "text": "The map draws that edge."},
                                  {"from_statement": None, "legs": [], "text": "Bbb stood at +2.00."},
                                  {"from_statement": None, "legs": [], "text": "As the literature shows [2]."}]}
        self.assertEqual(narrate.prune_connectives(results), 2)
        self.assertEqual([p["text"] for p in results["paragraphs"]],
                         ["Aaa rose to +1.00 [1].", "The map draws that edge."])
        self.assertIn("Exactly 5 paragraphs", narrate.BRIEF % (5, 4, 300, 900))

    def test_a_sentence_with_a_misquoted_value_is_dropped_not_the_section(self):
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]
        results = {"summary": "It moved to +9.99. Aaa rose.", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": "It read %s at the start [e1]. It then reached +9.99 at 6h." % value},
            {"from_statement": 2, "legs": [2], "text": "Only +8.88 here."}]}
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker), 3)
        self.assertEqual(results["summary"], "Aaa rose.")
        self.assertEqual([p["text"] for p in results["paragraphs"]], ["It read %s at the start [e1]." % value])

    def test_no_timing_word_on_an_unlabeled_layer(self):
        walker = Walker(self.graph, self.ov, "KEGG:tst00001", params_for("pathway"))
        walker.start_at("g:2", 5)
        answer = walker.step("tst-miR-1", "miRNA-seq values rise across the columns", "the regulator")
        self.assertNotIn("REFUSED", answer)
        self.assertEqual(verify.timing_on_unlabeled("tst-miR-1 rose late over the time course.", walker),
                         ["miR-1"])
        # With or without the organism prefix: the walker's label and the layer
        # text name the miRNA differently, and the model writes either.
        self.assertEqual(verify.timing_on_unlabeled("The miRNA miR-1 peaked at c3.", walker), ["miR-1"])
        # A timing word belongs to the node named nearest before it: the three
        # statements a live walk lost gave the word to a gene, not the miRNA.
        bbb = walker.label("g:2")
        self.assertEqual(verify.timing_on_unlabeled(
            "tst-miR-1 targets %s, which fell late in gene expression." % bbb, walker), [])
        self.assertEqual(verify.timing_on_unlabeled(
            "%s is partly baseline-elevated, and its neighbour miR-1 is up." % bbb, walker), [])
        self.assertEqual(verify.timing_on_unlabeled("%s fell while miR-1 rose late." % bbb, walker), ["miR-1"])
        self.assertEqual(verify.timing_on_unlabeled("Late in the series, miR-1 rose.", walker), ["miR-1"])
        self.assertEqual(verify.timing_on_unlabeled("tst-miR-1 rose from c1 +0.40 to c3 +1.60.", walker), [])
        self.assertEqual(verify.timing_on_unlabeled("Bbb fell late in gene expression.", walker), [])

    def test_every_paper_a_statement_leans_on_is_read_and_on_topic(self):
        label = self.walker.label(self.chain[0]["to"])
        off_topic = {4: {"title": "An unrelated herbal extract", "abstract": "nothing"}}
        on_topic = {4: {"title": "%s in muscle" % label, "abstract": ""}}
        listed = dict(self.good_statement(), claim="%s is repressed [4]" % label, papers=[4])
        in_words = dict(self.good_statement(), claim="%s is repressed" % label, prose="%s falls [4]." % label)
        for stmt in (listed, in_words):
            self.assertTrue(any("without being read" in p
                                for p in verify.verify_statement(stmt, self.walker, off_topic, set())), stmt)
            self.assertTrue(any("does not mention" in p
                                for p in verify.verify_statement(stmt, self.walker, off_topic, {4})), stmt)
            self.assertEqual([p for p in verify.verify_statement(stmt, self.walker, on_topic, {4}) if "[4]" in p], [])
        unknown = dict(self.good_statement(), prose="as shown [8].")
        self.assertTrue(any("[8] was never retrieved" in p for p in verify.verify_statement(unknown, self.walker, {}, set())))

    def test_a_paragraph_cites_only_what_its_statement_cites(self):
        from src.classes.AIInterpret.walker import record as record_mod
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]
        kept = [{"n": 1, "claim": "c", "prose": "p [1]", "papers": [1]}]
        text = ("At the first time point in this series, the gene %s read %s [e1] [1]. " % (
            self.walker.label(node), value)) * 12
        results = {"title": "t", "summary": "It moved [2].",
                   "paragraphs": [{"from_statement": 1, "legs": [1], "text": text + "It drives wasting [2]."}]}
        problems = verify.verify_results(results, kept, [], self.walker)
        self.assertTrue(any("cites [2], which its statement does not cite" in p for p in problems), problems)
        self.assertTrue(any("summary cites [2]" in p for p in problems), problems)
        self.assertEqual(verify.drop_uncited_sentences(results, kept), 2)
        self.assertNotIn("[2]", results["paragraphs"][0]["text"] + results["summary"])
        self.assertFalse(any("[2]" in p for p in verify.verify_results(results, kept, [], self.walker)))
        papers = {1: {"pmid": "11"}, 2: {"pmid": "22"}}
        self.assertEqual(sorted(record_mod.renumber_citations(kept, [], results, papers)), [1])

    def test_model_shaped_garbage_is_an_objection_not_a_crash(self):
        stmt = dict(self.good_statement(), papers=3, grounded_in=1, beyond="x", legs="1")
        problems = verify.verify_statement(stmt, self.walker, {}, set())
        for field in ("papers", "grounded_in", "beyond", "legs"):
            self.assertTrue(any(field + " is not a list" in p for p in problems), (field, problems))
        self.assertEqual(verify.statement_papers(stmt), [])
        results = {"summary": "s", "paragraphs": ["Aaa rose.", {"from_statement": "S1", "legs": "e1", "text": "x"}]}
        problems = verify.verify_results(results, [{"n": 1}], [], self.walker)
        self.assertTrue(any("paragraph 1 is not an object" in p for p in problems), problems)
        self.assertTrue(any("statement S1, which was not kept" in p for p in problems), problems)
        from src.classes.AIInterpret.walker import narrate
        self.assertEqual(narrate.prune_connectives(results), 0)
        verify.drop_unrecorded_sentences(results, self.walker)
        verify.drop_uncited_sentences(results, [{"n": 1}])

    def test_an_unsigned_value_must_be_a_recorded_magnitude(self):
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]          # "+1.90" or "−0.35"
        magnitude = value.lstrip("+−-")
        results = {"summary": "", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": "It changed by %s at the start [e1]. It rose to 9.99 at 6h." % magnitude}]}
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker), 1)
        self.assertEqual(results["paragraphs"][0]["text"], "It changed by %s at the start [e1]." % magnitude)
        self.assertEqual(verify.quoted_values("relevant when |value| > 1.00 or p < 0.05, within ±0.6"), [])
        self.assertEqual(verify.quoted_values("it fell by 3.24 and rose to +2.13"), ["3.24", "+2.13"])

    def test_a_dropped_statements_reason_names_papers_the_list_holds(self):
        from src.classes.AIInterpret.walker import record as record_mod
        papers = {3: {"pmid": "33"}, 7: {"pmid": "77"}}
        kept = [{"n": 1, "claim": "c [3]", "prose": "p", "papers": [3]}]
        dropped = [{"n": 2, "claim": "d", "why": "[7] does not mention Foxo1; [3] is fine; paper [9] was never retrieved"}]
        out = record_mod.renumber_citations(kept, dropped, None, papers)
        self.assertEqual(out, {1: {"pmid": "33"}})
        self.assertEqual(dropped[0]["why"], "PMID 77 does not mention Foxo1; [1] is fine; paper [?] was never retrieved")

    def test_a_dropped_claim_is_recognised_by_its_first_eight_words(self):
        stmt = self.good_statement()
        stmt["prose"] = "%s moves." % self.walker.label(self.chain[0]["to"])
        results = {"title": "t", "summary": "",
                   "paragraphs": [{"from_statement": 1, "legs": [1],
                                   "text": "%s gene expression surges late in the course, a rise. " % self.walker.label(self.chain[0]["to"])
                                           * 12}]}
        gene = self.walker.label(self.chain[0]["to"])
        # five shared words are how any statement about the gene begins: not a mention
        dropped = [{"claim": "%s gene expression surges late under the perturbation and drives arrest" % gene}]
        problems = verify.verify_results(results, [stmt], dropped, self.walker, "pathway", (10, 5000))
        self.assertFalse(any("dropped statement" in p for p in problems), problems)
        # the first eight words verbatim are
        dropped = [{"claim": "%s gene expression surges late in the course, a rise" % gene}]
        problems = verify.verify_results(results, [stmt], dropped, self.walker, "pathway", (10, 5000))
        self.assertTrue(any("dropped statement" in p for p in problems), problems)

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
