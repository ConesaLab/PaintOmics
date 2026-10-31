#!/usr/bin/env python3
"""The design card, not a time-course assumption, decides how values are read.

A user can upload a time course, a dose series, or conditions with no order at
all (tumour against normal). The chat tool used to be called
get_gene_timecourse, every prompt carried a "temporal data guidance" block, a
profile was classified as monotonic-up or transient whatever its columns were,
and an omic whose file had no header borrowed the labels of another omic with
the same column count, so six miRNA columns were printed as 0h..24h. These
tests pin the replacement: the user's own labels, an axis read from them and
the design text, guidance that allows trajectory words only on an ordered
axis, no borrowing, and a sense check that asks about timing after a
perturbation only when the card states one or orders its columns.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_design_card_drives_the_prompts
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret import prompts                     # noqa: E402
from src.classes.AIInterpret import tools                       # noqa: E402
from src.classes.AIInterpret.walker import card as card_mod     # noqa: E402
from src.classes.AIInterpret.walker import sense as sense_mod   # noqa: E402
from src.classes.AIInterpret.walker import service              # noqa: E402
from src.tests import walker_fixture as fx                      # noqa: E402

TRAJECTORY_WORDS = ("monotonic", "transient", "early-peak", "late-peak", "biphasic", "oscillat")


def conditions_job(design="Tumour against matched normal tissue. Values are log2 fold changes."):
    """Two unordered conditions, and a miRNA file with no header and the same
    column count: the case that used to borrow the expression labels."""
    ge = "Gene expression"
    genes = {
        "1": fx.FakeFeature("1", [fx.FakeOmicValue(ge, "Aaa", [True, False], [2.1, -0.2]),
                                  fx.FakeOmicValue("miRNA-seq", "tst-miR-1", [True], [0.9, 1.7])], name="Aaa"),
        "2": fx.FakeFeature("2", [fx.FakeOmicValue(ge, "Bbb", [False, False], [0.1, 0.05])], name="Bbb"),
    }
    job = fx.FakeJob(genes, headers={"Gene expression": ["#geneID", "Study/Tumour", "Study/Normal"],
                                     "miRNA-seq": "None"})
    job.getExperimentDesign = lambda: design
    return job


class LabelsTest(unittest.TestCase):
    def test_a_missing_header_stored_as_the_string_none_is_no_header(self):
        self.assertIsNone(card_mod.shorten_labels("None"))
        self.assertIsNone(card_mod.shorten_labels(None))
        self.assertIsNone(card_mod.shorten_labels(["#geneID"]))

    def test_a_shared_prefix_is_stripped_at_its_last_separator(self):
        self.assertEqual(card_mod.shorten_labels(["id", "Ikaros/Control_0h", "Ikaros/Control_24h"]), ["0h", "24h"])
        self.assertEqual(card_mod.shorten_labels(["id", "Tumour", "Normal"]), ["Tumour", "Normal"])

    def test_labels_by_omic_keeps_an_unlabeled_omic_as_none(self):
        labels = card_mod.labels_by_omic(conditions_job())
        self.assertEqual(labels, {"Gene expression": ["Tumour", "Normal"], "miRNA-seq": None})


class AxisTest(unittest.TestCase):
    def test_time_tokens_make_a_time_axis(self):
        for labels in (["0h", "2h", "24h"], ["T0", "T1"], ["day7", "Day14"], ["Ikaros_24h_rep1", "Ikaros_0h_rep2"]):
            self.assertEqual(card_mod.axis_kind({"x": labels}), "time", labels)

    def test_named_conditions_are_groups_even_with_digits_inside(self):
        for labels in (["Tumour", "Normal"], ["H2O2", "Ctrl"], ["WT", "KO1"]):
            self.assertEqual(card_mod.axis_kind({"x": labels}), "groups", labels)

    def test_bare_numbers_are_ordered_unless_the_design_says_time(self):
        self.assertEqual(card_mod.axis_kind({"x": ["0", "10", "100"]}), "ordered")
        self.assertEqual(card_mod.axis_kind({"x": ["0", "10", "100"]}, "A time course in three samples"), "time")

    def test_no_header_anywhere_is_unlabeled(self):
        self.assertEqual(card_mod.axis_kind({"a": None, "b": None}), "unlabeled")

    def test_the_job_card_carries_the_axis_labels_and_the_unlabeled_omics(self):
        card = card_mod.job_card(conditions_job())
        self.assertEqual(card["axis_kind"], "groups")
        self.assertEqual(card["unlabeled"], ["miRNA-seq"])
        text = "\n".join(card_mod.card_lines(card))
        self.assertIn("no order", text)
        self.assertIn("miRNA-seq", text)
        self.assertEqual(card_mod.job_card(fx.make_job())["axis_kind"], "time")


class ChatToolTest(unittest.TestCase):
    def test_the_chat_tools_and_their_executors_are_the_same_set(self):
        names = {t["function"]["name"] for t in tools.CHAT_TOOLS}
        self.assertEqual(names, set(tools._EXECUTORS))
        self.assertNotIn("get_gene_timecourse", names)
        self.assertIn("get_feature_values", names)
        for tool in tools.CHAT_TOOLS:
            self.assertNotIn("timepoint", tool["function"]["description"].lower())
            self.assertNotIn("temporal", tool["function"]["description"].lower())

    def test_an_unlabeled_omic_never_borrows_another_omics_labels(self):
        out = tools.execute_tool("get_feature_values", conditions_job(), {"gene_symbol": "Aaa"})
        mirna = [line for line in out.splitlines() if "miRNA-seq" in line]
        self.assertEqual(len(mirna), 1, out)
        self.assertNotIn("Tumour", mirna[0])
        self.assertIn("columns unlabeled", mirna[0])
        expression = [line for line in out.splitlines() if "Gene expression" in line][0]
        self.assertIn("Tumour", expression)
        self.assertIn("relevant", expression)

    def test_compare_and_pathway_listing_share_the_rule(self):
        out = tools.execute_tool("compare_genes", conditions_job(), {"gene_symbols": ["Aaa", "Bbb"]})
        self.assertNotIn("Error", out)
        for line in out.splitlines():
            if "miRNA-seq" in line:
                self.assertNotIn("Tumour", line)


class PromptTest(unittest.TestCase):
    def test_the_guidance_block_follows_the_axis(self):
        self.assertIs(prompts.design_guidance("time"), prompts.TEMPORAL_GUIDANCE_BLOCK)
        self.assertIs(prompts.design_guidance("ordered"), prompts.TEMPORAL_GUIDANCE_BLOCK)
        self.assertIs(prompts.design_guidance("groups"), prompts.CONDITION_GUIDANCE_BLOCK)
        self.assertIs(prompts.design_guidance("unlabeled"), prompts.UNLABELED_GUIDANCE_BLOCK)
        self.assertNotIn("over time", prompts.SYSTEM_PROMPT_CHAT)
        for word in TRAJECTORY_WORDS:
            self.assertNotIn(word, prompts.CONDITION_GUIDANCE_BLOCK.lower())

    def test_the_chat_prompt_names_the_renamed_tool(self):
        self.assertIn("get_feature_values", prompts.SYSTEM_PROMPT_CHAT)
        self.assertNotIn("get_gene_timecourse", prompts.SYSTEM_PROMPT_CHAT)


class _SenseClient(object):
    """Answers each sense call with the next scripted verdict list and keeps
    the system and user prompts it was sent."""

    def __init__(self, *answers):
        self.answers, self.systems, self.users = list(answers), [], []

    def complete_json(self, messages, name, schema, parser, max_tokens=0, temperature=0.0, budget_seconds=None):
        self.systems.append(messages[0]["content"])
        self.users.append(messages[1]["content"])
        return {"verdicts": self.answers.pop(0)}


def _verdict(n, timing_ok=True, note=""):
    v = {f: {"ok": True, "note": ""} for f in sense_mod.FIELDS}
    v["n"], v["timing"] = n, {"ok": timing_ok, "note": note}
    return v


def _card(job):
    return service.design_card(job, card_mod.labels_by_omic(job), "greedy")


def _time_job(design):
    job = fx.make_job()
    job.getExperimentDesign = lambda: design
    return job


# Job UJl1G1b334's n3 (30 Oct 2026): no claim about time or order, dropped on
# timing because the card stated no perturbation.
N3 = {"n": 3, "claim": "hisIE/SGO_1403 is the only node of the walked chain with MSr.MSt down (-3.85), "
                       "opposite to hisH; the his genes are not uniformly co-regulated across the axes."}


class SenseTimingTest(unittest.TestCase):
    """The sense check's timing question follows the card. Asked about "an
    effect after the perturbation" on a card with none, the model failed 4 of
    job UJl1G1b334's 10 statements on timing though none made a timing claim."""

    def asked(self, card):
        client = _SenseClient([_verdict(3)])
        text = service.walk_card_text(card)
        self.assertTrue(sense_mod.sense_check(client, text, [N3], "chain", card=card)[3]["timing"]["ok"])
        self.assertTrue(client.users[0].startswith("DESIGN CARD\n%s\n\nCHAIN" % text), client.users[0])
        return client.systems[0]

    def test_no_perturbation_and_unordered_columns_ask_only_for_a_time_course_or_order(self):
        card = _card(conditions_job(design=""))
        self.assertEqual((card["perturbation"], card["axis_kind"], card["perturbation_stated"]),
                         ("not stated", "groups", False))
        system = self.asked(card)
        self.assertIn(sense_mod.UNORDERED_TIMING_QUESTION, system)
        self.assertNotIn(sense_mod.TIMING_QUESTION, system)
        self.assertNotIn("an effect after the perturbation", system)
        for words in ("time course or an order", "early or late", "a treatment the design does not state",
                      "a difference between columns read as a difference", "a fact from the literature are ok"):
            self.assertIn(words, system)
        # The model card UJl1G1b334's walk read, and a job with no headers.
        for other in ({"perturbation": "not stated", "axis": "LSr.LSt, MSr.MSt, MSr.LSr", "axis_kind": "groups",
                       "baseline": "not stated; a difference at baseline is read as baseline, not effect"},
                      {"perturbation": "not stated", "axis_kind": "unlabeled"}):
            self.assertIn(sense_mod.UNORDERED_TIMING_QUESTION, self.asked(other))

    def test_a_time_course_with_a_perturbation_keeps_the_perturbation_question(self):
        card = _card(_time_job("Ikaros induction over three time points. Values are log2 fold changes."))
        self.assertEqual(card["axis_kind"], "time")
        system = self.asked(card)
        self.assertIn(sense_mod.TIMING_QUESTION, system)
        self.assertNotIn(sense_mod.UNORDERED_TIMING_QUESTION, system)
        self.assertTrue(system.startswith(sense_mod.BRIEF), "the brief is not today's")

    def test_either_a_perturbation_or_an_ordered_axis_keeps_the_perturbation_question(self):
        stated_groups = _card(conditions_job())                     # "Tumour against matched normal tissue."
        self.assertEqual(stated_groups["axis_kind"], "groups")
        for card in (stated_groups, {"perturbation": "not stated", "axis_kind": "time"},
                     {"perturbation": "not stated", "axis_kind": "ordered"},
                     {"perturbation": "not stated", "perturbed_genes": ["Ikzf1"], "axis_kind": "groups"}):
            self.assertEqual(sense_mod.timing_question(card), sense_mod.TIMING_QUESTION, card)
        self.assertEqual(sense_mod.brief(None), sense_mod.BRIEF)
        self.assertIn("timing: does the timing fit the design -- an effect after the perturbation, not a "
                      "difference already present at the baseline?\n", sense_mod.BRIEF)

    def test_how_a_card_says_no_perturbation(self):
        # The whole text is the phrase: a prefix match read the last four of
        # the stated list as "not stated" (review of #212).
        for text in ("not stated", "Not stated in the design text.", "none", "None given", "N/A", "unknown",
                     "", "  ", "No perturbation is described", "Not explicitly stated", "No design provided",
                     "The design text does not describe a perturbation."):
            self.assertFalse(card_mod.perturbation_stated({"perturbation": text}), text)
        for text in ("Histidine starvation of S. gordonii", "Knockout of hisH", "NO donor treatment",
                     "Na+ stress (150 mM) vs control", "Na-butyrate treatment", "None-treated vs LPS",
                     "Unknown compound X exposure"):
            self.assertTrue(card_mod.perturbation_stated({"perturbation": text}), text)
        self.assertTrue(card_mod.perturbation_stated({"perturbation": "not stated", "perturbed_genes": ["Ikzf1"]}))

    def test_no_design_text_is_no_perturbation_unless_the_user_names_one(self):
        # A model card on an empty design may word anything there; only the
        # user's own edit names a perturbation the job never described.
        self.assertFalse(card_mod.perturbation_stated({"perturbation": "RNA-seq of two strains"}, ""))
        self.assertFalse(card_mod.perturbation_stated({"perturbation": "x", "perturbed_genes": ["hisH"]}, " "))
        self.assertTrue(card_mod.perturbation_stated({"perturbation": "Histidine starvation", "source": "user"}, ""))
        self.assertTrue(card_mod.perturbation_stated({"perturbation": "Histidine starvation"}, "Histidine starvation."))

    def test_the_walks_card_carries_the_flag_for_every_source(self):
        prompts_seen = []

        class Client(object):
            def __init__(self, perturbation):
                self.perturbation = perturbation

            def complete_json(self, messages, *args, **kwargs):
                prompts_seen.append(messages[1]["content"])
                return {"perturbation": self.perturbation, "value": "as given", "axis": "Tumour, Normal",
                        "baseline": "not stated", "relevant": "not stated", "perturbed_genes": [],
                        "perturbation_direction": "unknown", "system": "not stated"}

        empty, described = conditions_job(design=""), conditions_job()
        labels = card_mod.labels_by_omic(empty)
        model = service.design_card(empty, labels, "model", client=Client("Comparison of tumour and normal"))
        self.assertFalse(model["perturbation_stated"], "an empty design named a perturbation")
        self.assertIn("perturbation: what was done, against what, in which system; 'not stated' if the text "
                      "names none.", prompts_seen[0])
        self.assertTrue(service.design_card(described, labels, "model", client=Client("Tumour vs normal tissue"))
                        ["perturbation_stated"])
        self.assertFalse(service.design_card(described, labels, "model", client=Client("Not explicitly stated."))
                         ["perturbation_stated"])
        self.assertTrue(service.design_card(empty, labels, "greedy", override={"perturbation": "Histidine starvation"})
                        ["perturbation_stated"])
        self.assertFalse(service.design_card(empty, labels, "greedy", override={"axis": "Tumour, Normal"})
                         ["perturbation_stated"])

    def test_the_timing_question_reads_the_cards_flag_first(self):
        stated_text = {"perturbation": "Knockout of hisH", "axis_kind": "groups"}
        self.assertEqual(sense_mod.timing_question(stated_text), sense_mod.TIMING_QUESTION)
        self.assertEqual(sense_mod.timing_question(dict(stated_text, perturbation_stated=False)),
                         sense_mod.UNORDERED_TIMING_QUESTION)
        self.assertEqual(sense_mod.timing_question({"perturbation": "not stated", "perturbation_stated": True,
                                                    "axis_kind": "groups"}), sense_mod.TIMING_QUESTION)
        for kind in card_mod.ORDERED_AXES:
            self.assertEqual(sense_mod.timing_question({"perturbation_stated": False, "axis_kind": kind}),
                             sense_mod.TIMING_QUESTION, kind)

    def test_the_verdicts_are_enforced_as_before_and_the_recheck_asks_the_same_question(self):
        # Statement 4 reads the unordered columns as a trajectory: it still
        # fails timing, goes to the rewrite once and drops; statement 3 stays.
        card = _card(conditions_job(design=""))
        rise = {"n": 4, "claim": "Aaa rises from Tumour to Normal."}
        note = "the columns have no order"
        client = _SenseClient([_verdict(3), _verdict(4, False, note)], [_verdict(4, False, note)])
        statements, dropped, rewritten = [dict(N3), rise], [], []
        with mock.patch.object(service, "_rewrite_and_recheck",
                               lambda client, text, chain, bad, notes, *a, **k: rewritten.append(notes) or {
                                   s["n"]: [] for s in bad}):
            service._sense_pass(client, service.walk_card_text(card), "chain", statements, dropped, None, None,
                                set(), {}, card=card)
        self.assertEqual([s["n"] for s in statements], [3])
        self.assertTrue(statements[0]["sense"]["timing"]["ok"])
        self.assertEqual(rewritten, [{4: {"timing": note}}])
        self.assertEqual(dropped, [{"n": 4, "claim": rise["claim"], "prose": None, "why": "timing: " + note,
                                    "by": "sense check"}])
        self.assertEqual(len(client.systems), 2)
        for system in client.systems:
            self.assertIn(sense_mod.UNORDERED_TIMING_QUESTION, system)


if __name__ == "__main__":
    unittest.main()
