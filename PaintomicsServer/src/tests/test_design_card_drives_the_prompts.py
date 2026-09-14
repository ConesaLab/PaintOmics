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
axis, and no borrowing.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_design_card_drives_the_prompts
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret import prompts                     # noqa: E402
from src.classes.AIInterpret import tools                       # noqa: E402
from src.classes.AIInterpret.walker import card as card_mod     # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
