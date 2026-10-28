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

    def test_a_leg_written_as_the_chain_labels_it_is_that_leg(self):
        """default/llm wrote "legs": ["e1"] where DeepSeek wrote [1]; every
        statement of a walk was refused as naming a leg "ee1" (2026-09-30)."""
        base = self.good_statement()
        if not self.ov.layers[self.chain[0]["to"]][0]["relevant"]:
            base["prose"] += " (not relevant)"
        for written in ("e1", "E1", " e1 ", "1", 1.0):
            stmt = dict(base, legs=[written], grounded_in=[{"leg": written, "db": "KEGG"}])
            self.assertEqual(verify.verify_statement(stmt, self.walker, {}), [], written)
            self.assertEqual(stmt["legs"], [1], "downstream code reads legs as numbers")
            self.assertEqual(stmt["grounded_in"][0]["leg"], 1)
        for junk in ("e1b", "leg one", None, True, [1]):
            stmt = dict(base, legs=[junk], grounded_in=[])
            self.assertTrue(any("not on the chain" in p for p in verify.verify_statement(stmt, self.walker, {})),
                            junk)

    def test_a_node_cited_by_position_is_told_how_to_name_it(self):
        """default/llm cited [[0, layer], [1, layer]] -- nodes by position --
        and resubmitted the same three times against an objection that only
        said "not a node on the chain" (2026-09-30)."""
        stmt = self.good_statement()
        layer = stmt["cites"][0][1]
        stmt["cites"] = [[0, layer]]
        problems = [p for p in verify.verify_statement(stmt, self.walker, {}) if "cite 0" in p]
        self.assertEqual(len(problems), 1, problems)
        first = self.walker.label(verify.chain_nodes(self.chain)[0])
        self.assertIn(repr(first), problems[0], "the objection names a node the way the chain writes it")
        self.assertIn("never by a number", problems[0])
        if len(self.chain) > 1:
            # A Writer is shown nodes of its own part, not another Writer's.
            last = len(self.chain)
            stmt = dict(self.good_statement(), cites=[[0, layer]], legs=[last])
            problems = [p for p in verify.verify_statement(stmt, self.walker, {}, part=(last, last)) if "cite 0" in p]
            own = self.walker.label(self.chain[-1]["to"])
            other = self.walker.label(self.chain[0]["from"])
            self.assertIn(repr(own), problems[0])
            self.assertNotIn(repr(other), problems[0], "a node of another Writer's part is no example")

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

    def test_a_retrieved_paper_named_by_its_pmid_is_its_reference(self):
        """default/llm wrote {"pmid": p} in every beyond entry instead of the
        [N] its search returned, was refused each time, and flagged every claim
        a hypothesis to pass: no reference reached the Results (2026-09-30)."""
        papers = {3: {"pmid": "21849978", "title": "Fumarase"}, 21849978 % 97: {"pmid": "1"}}
        stmt = self.good_statement()
        stmt["beyond"] = [{"claim": "FH hydrates fumarate", "pmid": 21849978, "hypothesis": False},
                          {"claim": "unknown", "pmid": 99999999, "hypothesis": False}]
        stmt["papers"] = ["PMID 21849978", 3]
        stmt["prose"] += " as FH does [21849978], (PMID 21849978) and [3]."
        problems = verify.verify_statement(stmt, self.walker, papers)
        self.assertEqual(stmt["beyond"][0]["paper"], 3)
        self.assertEqual(stmt["papers"], [3, 3])
        self.assertTrue(stmt["prose"].endswith(" as FH does [3], [3] and [3]."), stmt["prose"])
        refused = [p for p in problems if "neither a retrieved paper" in p]
        self.assertEqual(len(refused), 1, "a PMID that was never retrieved is still refused")
        self.assertIn("unknown", refused[0])
        self.assertEqual([p for p in problems if "never retrieved" in p and "beyond" not in p], [])
        # A value that already is an [N] on the list is never read as a PMID.
        stmt = self.good_statement()
        stmt["papers"] = [1]
        verify.verify_statement(stmt, self.walker, {1: {"pmid": "3"}, 3: {"pmid": "1"}})
        self.assertEqual(stmt["papers"], [1])

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

    def cited_paper_case(self):
        """Statement 1 cites [5], whose confirmed passage states p = 0.001: the
        sentence the pruner removed on hsa05219 (job 7O521E0Y0y, 2026-10-28),
        after which the whole Results section was lost."""
        node = self.chain[0]["to"]
        value = self.ov.layer_text(node).split("·")[0].split()[-1]
        stmt = {"n": 1, "claim": "c", "prose": "p [5]", "papers": [5], "beyond": [],
                "evidence": [{"ref": 5, "section": "Results", "full_text": True,
                              "claim": "FGFR3 mutations are mutually exclusive with RAS mutations in bladder "
                                       "cancer (p = 0.001)",
                              "quote": "FGFR3 mutations were strongly mutually exclusive with RAS mutations "
                                       "(p = 0.001)"}]}
        lead = "At the first time point in this series, the gene %s read %s [e1]. " % (self.walker.label(node), value)
        cited = ("In a cohort of 257 primary bladder tumors, FGFR3 mutations were mutually exclusive with RAS "
                 "mutations (p = 0.001) [5].")
        self.assertFalse(verify._in_record("0.001", verify._record_numbers(self.walker)), "the fixture records it")
        return stmt, lead, cited

    def test_a_cited_papers_own_number_survives_the_pruner_and_the_verifier(self):
        stmt, lead, cited = self.cited_paper_case()
        results = {"title": "t", "summary": "s",
                   "paragraphs": [{"from_statement": 1, "legs": [1], "text": lead * 12 + cited}]}
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker, [stmt]), 0)
        self.assertTrue(results["paragraphs"][0]["text"].endswith(cited))
        self.assertEqual(verify.verify_results(results, [stmt], [], self.walker), [])
        # Called without the statements, as before: the record is the only source.
        bare = {"summary": "", "paragraphs": [{"from_statement": 1, "legs": [1], "text": lead + cited}]}
        self.assertEqual(verify.drop_unrecorded_sentences(bare, self.walker), 1)
        self.assertEqual(bare["paragraphs"][0]["text"], lead.strip())

    def test_a_number_the_cited_passage_does_not_state_is_still_pruned(self):
        stmt, lead, cited = self.cited_paper_case()
        wrong = cited.replace("0.001", "0.004")
        logfc = "FGFR3 rose to +7.77 in these tumors [5]."          # a misquoted logFC beside the citation
        results = {"title": "t", "summary": "s",
                   "paragraphs": [{"from_statement": 1, "legs": [1], "text": lead * 12 + wrong + " " + logfc}]}
        problems = verify.verify_results(results, [stmt], [], self.walker)
        self.assertTrue(any("quotes 0.004, not in the record" in p for p in problems), problems)
        self.assertTrue(any("quotes +7.77, not in the record" in p for p in problems), problems)
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker, [stmt]), 2)
        self.assertNotIn("[5]", results["paragraphs"][0]["text"])
        self.assertTrue(any("drops [5]" in p for p in verify.verify_results(results, [stmt], [], self.walker)))

    def test_a_papers_number_needs_that_papers_citation_in_its_own_sentence(self):
        stmt, lead, cited = self.cited_paper_case()
        results = {"summary": "FGFR3 and RAS mutations were exclusive (p = 0.001) [5]. Aaa rose.",
                   "paragraphs": [
                       {"from_statement": 1, "legs": [1],
                        "text": lead + "The two were exclusive (p = 0.001). This is documented [5]."},
                       {"from_statement": None, "legs": [], "text": "The map draws it (p = 0.001) [5]."}]}
        problems = verify.verify_results(results, [stmt], [], self.walker)
        self.assertTrue(any("paragraph 1 quotes 0.001" in p for p in problems), problems)
        self.assertTrue(any("paragraph 2 quotes 0.001" in p for p in problems), problems)
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker, [stmt]), 3)
        self.assertEqual(results["summary"], "Aaa rose.")
        self.assertEqual([p["text"] for p in results["paragraphs"]], [lead.strip() + " This is documented [5]."])

    def test_the_repair_round_is_told_which_cited_sentence_went_and_why(self):
        stmt, lead, cited = self.cited_paper_case()
        results = {"summary": "It rose to 0.004.", "paragraphs": [
            {"from_statement": 1, "legs": [1], "text": lead + cited.replace("0.001", "0.004")}]}
        removed = []
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker, [stmt], removed), 2)
        self.assertEqual(len(removed), 1, "only the sentence that carried its statement's citation is explained")
        self.assertTrue(removed[0].startswith('paragraph 1 (from statement 1): the sentence "In a cohort of 257 '
                                              'primary bladder tumors, FGFR3'), removed)
        self.assertIn('…" citing [5] was removed because it quotes 0.004, which neither the record nor [5]\'s '
                      'confirmed passage holds; keep [5] and quote only the record\'s values or the paper\'s',
                      removed[0])

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



class WalkJargonFormsTest(unittest.TestCase):
    """Each form the wording check learned refuses a sentence on its own, not
    only alongside "seed miRNA miR-" in the one fixture that used to cover
    them all."""

    def dropped(self, sentence):
        return verify.drop_jargon_sentences({"title": "Results", "paragraphs": [], "summary": sentence + " Foxo1 rose."})

    def test_each_new_form_is_refused_on_its_own(self):
        for sentence in ("A graph walk from Syk reached Foxo1.",
                         "A pathway walk from Syk reached Foxo1.",
                         "This network walk reached Foxo1.",
                         "Our graph walker reached Foxo1.",
                         "The network walks converged on Foxo1.",
                         "Syk was the seed node of the module.",
                         "The seed nodes were Syk and Lyn.",
                         "The seed miRNA let-7a falls.",
                         "The seed miRNA hsa-let-7a falls."):
            self.assertEqual(self.dropped(sentence), 1, sentence)


# Results summaries stored on paintomics.org on 2026-10-28, verbatim: the
# Narrator's planning (a restatement of narrate.BRIEF) where the summary goes.
LEAKED_SUMMARIES = {
    "7O521E0Y0y network": (
        "We walked a graph over recurrent vs primary bladder cancer expression data, starting from the hottest "
        "relevant genes and following drawn edges. The kept statements are the facts; the prose is mine. I must "
        "produce exactly 12 fact paragraphs, one per kept statement, in walk order, each tagged from_statement = n, "
        "with legs. Between them at most 11 connective paragraphs of one sentence each, tagged from_statement null, "
        "containing no value and no paper. I must quote values exactly as in the chain with sign and two decimals. "
        "Cite legs as [eN], layers as [node · layer], papers as [N] only where the statement did. Never mention "
        "dropped statements. Past tense, no hedging words the statements do not use. Unlabeled omic has no time "
        "points: describe values by column (c1..cN) and give them no early, late, baseline, peak or over-time word, "
        "even in a sentence about another gene. Length 600-2000 words."),
    "rJMPHKo7n6 network": (
        "The kept statements cover pkmb→eno3, mipa→mipb, and pgk1→gapdhs. The prose must report these three facts "
        "in walk order, with connective paragraphs between them that only state what a drawn edge or the design "
        "card says, no value and no paper. The design card says perturbation is knockout of gene in mouse liver, "
        "value log2 fold change of knockout over wildtype at 24 h, baseline wildtype (C) is baseline, text says "
        "'wildtype vs knockout' so baseline precedes perturbation, columns labeled Gene expression, no gene named, "
        "system mouse liver, axis kind conditions with no order; compare them by name, never as a trajectory. The "
        "chain has edges e1, e3, e5, e6, e7, e8, e11, e12, e14, e15, and jumps J2, J4, J9, J10, J13. The kept "
        "statements are n=1 (e1), n=2 (e3), n=3 (e5). The prose must be in walk order: statement 1, then "
        "statement 2, then statement 3. Between them at most 2 connective paragraphs of one sentence each, tagged "
        "from_statement null, may only say what a drawn edge or the design card says, and contain no value and no "
        "paper. So I need to write exactly 3 fact paragraphs, each tagged from_statement = n, with legs, and "
        "between them up to 2 connective paragraphs. The connective paragraphs could be placed between e1 and e3, "
        "and between e3 and e5. They can only say what a drawn edge or the design card says. The design card says "
        "perturbation is knockout of gene in mouse liver, value log2 fold change of knockout over wildtype at 24 h, "
        "baseline wildtype (C) is baseline, text says 'wildtype vs knockout' so baseline precedes perturbation, "
        "columns labeled Gene expression, no gene named, system mouse liver, axis kind conditions with no order; "
        "compare them by name, never as a trajectory. The drawn edges: e1 is pkmb → eno3, KEGG "
        "Glycolysis/Gluconeogenesis, compound ?, with the arrow. e3 is mipa → mipb, Reactome Vasopressin regulates "
        "renal water homeostasis via Aquaporins, reaction +, with the arrow. e5 is pgk1 → gapdhs, KEGG "
        "Glycolysis/Gluconeogenesis, compound ?, against the arrow. The connective paragraphs can say something "
        "like "),
    "y712svrXTf pathway:mmu04919": (
        "We traced the Ikzf1 perturbation through the thyroid hormone signalling pathway. The chain separates into "
        "a baseline-up miRNA module (188-3p/188-5p/532-3p/532-5p/501-5p/362-3p/362-5p/542-3p/542-5p) that is "
        "already high at 0h and rises further, and a timed-down component (Thrb, Thra, Hdac2 and their late-falling "
        "miRs 34B-5p/34B-3p/363-5p/1187/669F-3p/466N-3p/3074-1-3p) that only drops after 12h. The baseline-up "
        "module is consistent with a PI3K-suppressive brake; the timed-down component is consistent with loss of "
        "TRα1/TRβ-dependent miR-34 expression as Thrb/Thra mRNA collapses."),
    "7O521E0Y0y pathway:hsa05219": (
        "Then TP53 (−1.58) and back through MDM2, CDKN2A, CDK4, CDKN1A. The kept statements are about CDKN1A down "
        "and CDKN2A/MDM2 unchanged."),
    "7mE2o46g43 network": (
        "We walked a signed, directed interaction network from XPO1 to SMAD4, SESN1, KEAP1, NFE2L2, KRT17, NCOA2, "
        "MPST, CTH and CDO1, reading the transcript and protein values of each node in the Xpo1-knockout mouse "
        "liver."),
}
# Stored summaries and a paragraph that report biology; the paragraph says
# "the chain" twice, which the summary rule refuses.
BIOLOGY_SUMMARIES = [
    "OSKM expression in senescent MEFs reversed a senescence-associated loss of 14-3-3ε (Ywhae) only partially and "
    "transiently. Ywhae mRNA and protein were both lower in senescent MEFs than in young MEFs at baseline "
    "(Sen_MEF_D0_vs_Young_MEF −0.52 for Gene expression and −0.60 for Proteomics). After OSKM expression, the mRNA "
    "rose slightly at OSKM_D1 (+0.07) but fell again at OSKM_D2 (−0.14), while the protein continued to decline at "
    "both time points (−0.15 and −0.43).",
    "Ikaros perturbation collapses Tnf expression while Nox1 chromatin opens; the drawn Nox1→Tnf stimulation is "
    "contradicted by the data; literature places both genes in a TNF–NOX feedback loop.",
    "Cdkn1b's miRNA layer shows MMU-MIR-532-3P rising from +1.51 to +3.06, MMU-MIR-221-3P down throughout, "
    "MMU-MIR-222-3P dipping at 12h; interpreted as relieving p27 brake. Then Shc1's miRNA layer shows "
    "MMU-MIR-188-3P/5P and 362-3P rising, MMU-MIR-3091-3P collapsing late, MMU-MIR-466N-3P drifting down.",
    "The RPA/BTR recombination core, the BER arm, the RFC clamp loader, the Pol α/ε replisome, the SLX1-MUS81 "
    "module, and the chondroitin/dermatan sulfate synthases each showed coherent late induction, while the CCR4-NOT "
    "paralogs Cnot7/Cnot8, the RFC subunits Rfc2/Rfc3/Rfc5, the HR core Palb2/Brca2/Rad51, and the "
    "galactosyltransferases B4galt1/Chpf2/B3galt6 moved in opposite directions within their complexes. The results "
    "reveal a transcriptome-wide timing architecture in which late up-regulation of repair and recombination "
    "nucleases is accompanied by repression of their regulatory or catalytic partners.",
]
CHAIN_PARAGRAPH = (
    "Thrb is the timed hub of this chain: its Gene expression ENSMUSG00000021779 (not relevant) declines steadily "
    "from −0.24 at 0h to −3.68 at 24h, the only clean monotone fall on the chain, and its relevant miRNA layer shows "
    "the same late-falling set (MMU-MIR-34B-5P, MMU-MIR-34B-3P, MMU-MIR-363-5P, MMU-MIR-1187, MMU-MIR-669F-3P) "
    "dropping only after 12h while the baseline-up set (MMU-MIR-188-5P, MMU-MIR-188-3P, MMU-MIR-532-5P, "
    "MMU-MIR-501-5P, MMU-MIR-362-3P) stays high.")


class ResultsSummaryIsNotTheBriefTest(unittest.TestCase):
    """The summary DeepSeek fills with its planning reaches no reader: 5 of the
    19 Results sections stored on 2026-10-28 showed it."""

    def pruned(self, summary, title="Results", paragraphs=()):
        results = {"title": title, "summary": summary,
                   "paragraphs": [{"from_statement": 1, "legs": [1], "text": text} for text in paragraphs]}
        return results, verify.drop_meta_sentences(results)

    def test_every_leaked_summary_is_removed_whole(self):
        for where, summary in LEAKED_SUMMARIES.items():
            results, removed = self.pruned(summary)
            self.assertEqual(results["summary"], "", where)
            self.assertEqual(sorted(removed), sorted(s for s in verify.sentences(summary) if s.strip()), where)
            self.assertEqual(results["title"], "Results", where)

    def test_a_summary_reporting_biology_and_the_paragraphs_are_untouched(self):
        for summary in BIOLOGY_SUMMARIES:
            results, removed = self.pruned(summary, paragraphs=[CHAIN_PARAGRAPH])
            self.assertEqual(removed, [], summary)
            self.assertEqual(results["summary"], summary)
            self.assertEqual(results["paragraphs"][0]["text"], CHAIN_PARAGRAPH)
        self.assertTrue(verify.META_RE.search(CHAIN_PARAGRAPH), "the paragraph is why paragraphs are left alone")

    def test_a_sentence_left_dangling_by_a_removal_goes_too(self):
        results, removed = self.pruned("The kept statements are about Aaa. Then Bbb rose (+1.20). Ccc fell.")
        self.assertEqual(results["summary"], "Ccc fell.")
        self.assertEqual(removed, ["The kept statements are about Aaa.", "Then Bbb rose (+1.20)."])
        # hsa05219: the sentence the "Then" followed was taken at seal time
        results, removed = self.pruned(LEAKED_SUMMARIES["7O521E0Y0y pathway:hsa05219"])
        self.assertEqual(removed[-1], "Then TP53 (−1.58) and back through MDM2, CDKN2A, CDK4, CDKN1A.")

    def test_a_title_in_the_briefs_words_goes_whole(self):
        results, removed = self.pruned("Aaa rose.", title="The kept statements in walk order")
        self.assertEqual(results["title"], "")
        self.assertEqual(removed, ["The kept statements in walk order"])
        self.assertEqual(results["summary"], "Aaa rose.")

    def test_the_repair_is_told_what_went_and_what_to_write(self):
        _, removed = self.pruned(LEAKED_SUMMARIES["7O521E0Y0y pathway:hsa05219"])
        self.assertEqual(
            verify.meta_note(removed),
            "code removed 2 sentences of your summary or title that restated your instructions instead of "
            "reporting the biology (\"The kept statements are about CDKN1A down and CDKN2A/MDM2…\", \"Then TP53 "
            "(−1.58) and back through MDM2, CDKN2A, CDK4…\"); write the summary in plain biological terms: "
            "what changed in this experiment, naming the genes, as a paper's own summary of its results, never "
            "the rules, the statements or how the section was planned")


if __name__ == "__main__":
    unittest.main()
