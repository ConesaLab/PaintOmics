#!/usr/bin/env python3
"""Small values, p-values and q-values reach the walk as what they are.

Three faults, one production job each (paintomics.org, 27 Oct 2026):

  * Every user value was printed "%.2f": a p-value of 3e-05 read "+0.00".
    Job u36I21KMxV (value column adj.p.Value) was told that GSTA4, EPHX1,
    AKR1C1, AKR1C2 and NQO1 "all read adj.p.Value +0.00", and wrote that the
    whole arm was "unchanged" -- the job's most significant genes, as zero.
  * Nothing said a p-value carries no direction. Every p-value is positive,
    so the direction machinery read each one as "up", and the agents wrote
    "flat" and "the only non-zero adjusted p-value".
  * Job laW30Q7bYU uploaded a raw limma table and no relevant file and got
    only the generic "no seed" refusal; both jobs' stored designs began with
    the model's leaked reasoning, which the card took as the perturbation.

These tests pin the replacement: one formatter, numbers read back in any
scientific spelling, a significance detector from the headers alone, and the
design text without its reasoning.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walk_reads_small_and_significance_values
"""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import card as card_mod        # noqa: E402
from src.classes.AIInterpret.walker import narrate as narrate_mod  # noqa: E402
from src.classes.AIInterpret.walker import network as net_mod      # noqa: E402
from src.classes.AIInterpret.walker import null as null_mod        # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod       # noqa: E402
from src.classes.AIInterpret.walker import policies                # noqa: E402
from src.classes.AIInterpret.walker import verify                  # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                         # noqa: E402

LIMMA = ["#geneID", "logFC", "AveExpr", "t-statistic", "p.Value", "adj.p.Value", "B-statistic"]
LEAKED = ("The user asks for a description of the experiment design based on column headers. We need to "
          "infer... Let's craft.</think>I performed a differential gene expression analysis of treated "
          "against control liver. Values are adjusted p-values.")


class ValueTextTest(unittest.TestCase):

    def test_values_of_a_hundredth_and_over_look_as_they_always_did(self):
        self.assertEqual(ov_mod.value_text(1.9), "+1.90")
        self.assertEqual(ov_mod.value_text(-0.35), "−0.35")
        self.assertEqual(ov_mod.value_text(0.01), "+0.01")
        self.assertEqual(ov_mod.value_text(0.0), "+0.00")
        self.assertEqual(ov_mod.value_text(-0.0), "+0.00")

    def test_a_small_value_keeps_two_significant_digits(self):
        self.assertEqual(ov_mod.value_text(3.2e-05), "+3.2e-05")
        self.assertEqual(ov_mod.value_text(-0.0041), "−4.1e-03")
        self.assertEqual(ov_mod.value_text(0.004), "+4.0e-03")
        self.assertNotEqual(ov_mod.value_text(1e-300), "+0.00")

    def test_values_text_uses_it_for_every_column(self):
        self.assertEqual(ov_mod.values_text([3.2e-05, 1.9, float("nan"), "x"], ["adj.p.Value", "logFC", "a", "b"]),
                         "adj.p.Value +3.2e-05 · logFC +1.90")

    def test_reversing_signs_leaves_an_exponent_alone(self):
        self.assertEqual(ov_mod.flipped_text("a +3.2e-05 · b −1.90"), "a −3.2e-05 · b +1.90")


class NumberReadBackTest(unittest.TestCase):
    """The pruner and the verifier compare a quoted number with the record."""

    record = {verify._norm(v) for v in ("+3.2e-05", "−4.1e-03", "+1.90", "+0.00")}

    def test_every_plausible_spelling_of_a_small_value_is_one_quoted_value(self):
        text = ("NQO1 read +3.2e-05, GSTA4 3.2e-5, EPHX1 3.2 × 10⁻⁵, AKR1C1 3.2x10^-5, AKR1C2 3.2E-05 "
                "and 3e-05.")
        self.assertEqual(verify.quoted_values(text),
                         ["+3.2e-05", "3.2e-5", "3.2 × 10⁻⁵", "3.2x10^-5", "3.2E-05", "3e-05"])

    def test_what_was_no_value_stays_none_and_what_was_one_stays_the_same(self):
        self.assertEqual(verify.quoted_values("2 x 10^3 cells in 18-24h"), [])
        self.assertEqual(verify.quoted_values("a 1.5e3-fold rise"), ["1.5"])
        self.assertEqual(verify.quoted_values("p < 3.2e-05"), [])

    def test_a_small_value_is_in_the_record_however_it_is_written(self):
        for written in ("+3.2e-05", "3.2e-05", "3.2e-5", "3.2E-05", "3.2 × 10⁻⁵", "3.2x10^-5", "0.000032",
                        "3.20e-05", "4.1e-03", "−4.1e-03", "0.0041"):
            self.assertTrue(verify._in_record(written, self.record), written)

    def test_a_different_or_a_reversed_small_value_is_not(self):
        for written in ("−3.2e-05", "3.3e-05", "3.2e-04", "3.2", "+4.1e-03"):
            self.assertFalse(verify._in_record(written, self.record), written)

    def test_the_old_two_decimal_numbers_still_match(self):
        for written in ("+1.90", "1.90", "+0.00", "0.00"):
            self.assertTrue(verify._in_record(written, self.record), written)
        self.assertFalse(verify._in_record("−1.90", self.record))
        self.assertFalse(verify._in_record("0.000032", {verify._norm("+0.00")}),
                         "a small value must not pass as the zero the old format printed")


class PrunerOnScientificValuesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        graph = net_mod.build_network("tst", cls.data_dir).filter_pathway("KEGG:tst00001")
        cls.ov = ov_mod.overlay_job(graph, fx.make_job())
        cls.walker = policies.greedy(Walker(graph, cls.ov, "KEGG:tst00001", params_for("pathway")))
        node = cls.walker.record()["chain"][0]["to"]
        layer = cls.ov.layers[node][0]
        layer["values"] = [3.2e-05, 1.5, 2.2]
        layer["text"] = ov_mod.values_text(layer["values"], ["0h", "2h", "6h"])
        cls.gene = cls.walker.label(node)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def test_a_quoted_small_value_survives_and_a_misquoted_one_goes(self):
        results = {"summary": "", "paragraphs": [{"from_statement": 1, "legs": [1], "text": (
            "%s read 3.2 × 10⁻⁵ at 0h [e1]. It read 4.4e-05 at 2h." % self.gene)}]}
        self.assertEqual(verify.drop_unrecorded_sentences(results, self.walker), 1)
        self.assertEqual(results["paragraphs"][0]["text"], "%s read 3.2 × 10⁻⁵ at 0h [e1]." % self.gene)

    def test_a_connective_paragraph_quoting_a_small_value_is_pruned(self):
        results = {"paragraphs": [{"from_statement": None, "text": "Both read 3.2e-05."}]}
        self.assertEqual(narrate_mod.prune_connectives(results), 1)


class SignificanceDetectorTest(unittest.TestCase):

    def test_the_usual_headers_are_significance_columns(self):
        labels = ["p.value", "pvalue", "p_val", "P-value", "PValue", "adj.p.Value", "adj.P.Val", "padj",
                  "p.adjusted", "FDR", "qvalue", "q.value", "q_val", "KO_vs_WT_padj", "pvalue_24h", "FDR.BH"]
        self.assertEqual(card_mod.significance_columns(labels), list(range(len(labels))))

    def test_labels_that_only_contain_the_letters_are_not(self):
        labels = ["Ip", "pH", "Ctr_0H", "PDX", "dpi", "logFC", "AveExpr", "t-statistic", "B-statistic", "p",
                  "Pval0h", "pvalley", "FDRx", "Day7"]
        self.assertEqual(card_mod.significance_columns(labels), [])

    def test_a_limma_table_is_mixed_and_names_its_columns(self):
        kinds = card_mod.value_kinds({"Gene expression": LIMMA, "Metabolomics": ["id", "0h", "2h"]})
        self.assertEqual(kinds, [{"omic": "Gene expression", "kind": "mixed",
                                  "columns": ["p.Value", "adj.p.Value"], "index": [3, 4], "signed_index": [0]}])

    def test_an_omic_of_p_values_alone_is_significance(self):
        kinds = card_mod.value_kinds({"Gene expression": ["#gene", "adj.p.Value"]})
        self.assertEqual(kinds[0]["kind"], "significance")

    def test_a_word_the_shared_prefix_hides_is_still_read(self):
        kinds = card_mod.value_kinds({"Gene expression": ["#gene", "padj_KO", "padj_WT"]})
        self.assertEqual(kinds, [{"omic": "Gene expression", "kind": "significance",
                                  "columns": ["KO", "WT"], "index": [0, 1], "signed_index": []}])

    def test_condition_names_speak_only_for_a_sole_unlabeled_omic(self):
        self.assertEqual(card_mod.value_kinds({"Gene expression": None}, ["FDR"])[0]["kind"], "significance")
        self.assertEqual(card_mod.value_kinds({"Gene expression": None, "Proteomics": ["id", "a", "b"]},
                                              ["FDR"]), [])

    def test_the_card_line_and_the_refusal_hint(self):
        kinds = card_mod.value_kinds({"Gene expression": LIMMA})
        line = card_mod.value_kind_text(kinds)
        self.assertTrue(line.startswith("Gene expression (columns p.Value, adj.p.Value): p-values or q-values"))
        self.assertIn("never call a gene up, down, flat or unchanged", line)
        self.assertEqual(card_mod.relevance_hint(kinds),
                         " Your values include adj.p.Value: a Relevant features file listing the features with "
                         "adj.p.Value below 0.05 would give the walk its seeds.")
        self.assertEqual(card_mod.value_kind_text([]), "")
        self.assertEqual(card_mod.relevance_hint([]), "")


def significance_job():
    """The fixture with its expression columns as p.Value, adj.p.Value and logFC."""
    job = fx.make_job()
    job._headers["Gene expression"] = ["#geneID", "p.Value", "adj.p.Value", "logFC"]
    return job


class NoDirectionFromSignificanceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.graph = net_mod.build_network("tst", cls.data_dir).filter_pathway("KEGG:tst00001")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def test_layers_carry_their_significance_columns(self):
        ov = ov_mod.overlay_job(self.graph, significance_job())
        self.assertEqual(ov.significance, {"Gene expression": [0, 1]})
        self.assertEqual(ov.layers["g:1"][0]["significance"], [0, 1])

    def test_only_the_signed_columns_give_a_sign(self):
        layer = {"values": [0.001, 0.04, -1.5], "significance": [0, 1]}
        self.assertEqual(list(ov_mod.signed_values(layer)), [-1.5])
        # Gene 4 of the fixture: -2.0 is a p-value column here, -0.5 its logFC.
        ov = ov_mod.overlay_job(self.graph, significance_job())
        for layer in ov.layers["g:1"]:
            layer["values"] = [0.001, 0.002, 0.0]
        self.assertEqual(null_mod.node_direction(ov, "g:1"), 0,
                         "p-values read as a sign: every gene went 'up'")
        self.assertEqual(null_mod.node_direction(ov, "g:4"), -1)

    def test_a_flipped_layer_keeps_its_p_values(self):
        ov = ov_mod.overlay_job(self.graph, significance_job())
        layer = ov.layers["g:1"][0]
        layer["values"] = [3.2e-05, 0.004, 1.9]
        layer["text"] = ov_mod.values_text(layer["values"], ov.labels["Gene expression"])
        flipped = ov.layer_text("g:1", flip=True)
        self.assertIn("p.Value +3.2e-05 · adj.p.Value +4.0e-03 · logFC −1.90", flipped)


class TransformedSignificanceTest(unittest.TestCase):
    """A -log10 p-value names a p-value but holds its transform: larger is the
    stronger difference, and a signed one carries the direction. Taken for a
    p-value, the card told every agent "smaller means stronger" and the hint
    "below 0.05" about it -- exactly wrong -- and its sign was never read."""

    TRANSFORMED = ["-log10(padj)", "neg_log10_FDR", "log10_pvalue", "signed_-log10_pval", "-lg(p.value)",
                   "ln_qvalue", "mlog10p_KO", "neglog_FDR"]

    def test_a_log_transformed_p_value_is_no_significance_column(self):
        self.assertEqual(card_mod.significance_columns(self.TRANSFORMED), [])
        self.assertEqual(card_mod.value_kinds({"Gene expression": ["#gene"] + self.TRANSFORMED}), [],
                         "transformed p-values were read as p-values")

    def test_the_untransformed_ones_still_are_and_the_lookalikes_still_are_not(self):
        self.assertEqual(card_mod.significance_columns(["adj.p.Value", "padj", "FDR", "qvalue", "P.Value"]),
                         [0, 1, 2, 3, 4])
        self.assertEqual(card_mod.significance_columns(["Ip", "pH", "Ctr_0H", "PDX", "dpi", "logFC_pvalue"]), [5])

    def test_a_transform_a_shared_prefix_hid_is_still_read(self):
        # Shortened, "-log10_KO_padj" / "-log10_WT_padj" print as KO_padj / WT_padj.
        self.assertEqual(card_mod.value_kinds({"Gene expression": ["#gene", "-log10_KO_padj", "-log10_WT_padj"]}),
                         [])

    def test_a_signed_transform_keeps_its_sign(self):
        data_dir = fx.make_data_dir()
        try:
            graph = net_mod.build_network("tst", data_dir).filter_pathway("KEGG:tst00001")
            job = fx.make_job()
            job._headers["Gene expression"] = ["#geneID", "signed_-log10_pval", "a", "b"]
            ov = ov_mod.overlay_job(graph, job)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        layer = ov.layers["g:1"][0]
        layer["values"] = [-6.5, 0.1, 0.2]
        self.assertEqual(ov.value_kind, [])
        self.assertEqual(ov_mod.layer_sign(layer), -1)


class EffectColumnTest(unittest.TestCase):
    """In a table that also holds p-values only the fold change carries a
    direction. A limma export (logFC, AveExpr, t, P.Value, adj.P.Val, B) --
    the very case value_kinds labels "mixed" -- read AveExpr, always positive
    and the largest number on the row, as the sign: every gene went "up", and
    the counter-hypothesis negated the mean expression."""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.graph = net_mod.build_network("tst", cls.data_dir).filter_pathway("KEGG:tst00001")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def limma_overlay(self, values):
        job = fx.make_job()
        job._headers["Gene expression"] = LIMMA
        ov = ov_mod.overlay_job(self.graph, job)
        layer = ov.layers["g:1"][0]
        layer["values"] = values
        layer["text"] = ov_mod.values_text(values, ov.labels["Gene expression"])
        return ov, layer

    def test_the_fold_change_headers(self):
        labels = ["logFC", "log2FC", "log2FoldChange", "FC", "fold change", "Log2 fold change", "FC_KO", "logfc"]
        self.assertEqual(card_mod.effect_columns(labels), list(range(len(labels))))
        self.assertEqual(card_mod.effect_columns(["AveExpr", "lfcSE", "baseMean", "PFC", "FCS", "t", "B"]), [])

    def test_signed_columns_by_table(self):
        deseq = card_mod.value_kinds({"Gene expression": ["id", "baseMean", "log2FoldChange", "lfcSE", "stat",
                                                          "pvalue", "padj"]})
        self.assertEqual(deseq[0]["signed_index"], [1])
        no_effect = card_mod.value_kinds({"Gene expression": ["id", "t", "B", "pvalue"]})
        self.assertEqual(no_effect[0]["signed_index"], [0], "no fold change: the t statistic, never B")

    def test_effects_under_other_names_carry_the_direction(self):
        """A mixed table whose effect is named M, log2 Ratio, estimate or diff
        fell back to every non-p column, AveExpr and baseMean among them, and
        every gene read "up" again."""
        for effect in ("M", "log2 Ratio", "logratio", "estimate", "diff", "difference", "Estimate"):
            with self.subTest(effect=effect):
                kinds = card_mod.value_kinds({"Gene expression": ["id", "AveExpr", effect, "P.Value", "B"]})
                self.assertEqual(kinds[0]["signed_index"], [1])
        for statistic in ("t", "t-statistic", "t.stat", "stat", "z", "z-score", "zscore"):
            with self.subTest(statistic=statistic):
                kinds = card_mod.value_kinds({"Gene expression": ["id", "baseMean", statistic, "padj", "B-statistic"]})
                self.assertEqual(kinds[0]["signed_index"], [1])
        self.assertEqual(card_mod.effect_columns(["m", "ratio", "Mean", "AveExpr", "Mdiff"]), [],
                         "a lowercase m, a plain ratio and words containing the letters are not")
        self.assertEqual(card_mod.statistic_columns(["B-statistic", "test", "zone", "Ctr_0H"]), [])

    def test_a_mixed_table_with_no_effect_column_carries_no_sign(self):
        kinds = card_mod.value_kinds({"Gene expression": ["id", "AveExpr", "baseMean", "P.Value", "adj.P.Val", "B"]})
        self.assertEqual((kinds[0]["kind"], kinds[0]["signed_index"]), ("mixed", []))
        job = fx.make_job()
        job._headers["Gene expression"] = ["#gene", "AveExpr", "P.Value", "B"]
        ov = ov_mod.overlay_job(self.graph, job)
        layer = ov.layers["g:1"][0]
        layer["values"] = [9.5, 0.001, 2.0]
        self.assertEqual(list(ov_mod.signed_values(layer)), [])
        from src.classes.AIInterpret.walker import direction as direction_mod
        self.assertEqual(direction_mod.data_direction(layer), 0, "AveExpr 9.5 read as up")
        self.assertEqual(null_mod.node_direction(ov, "g:1"), 0)

    def test_the_direction_follows_the_fold_change(self):
        ov, layer = self.limma_overlay([-0.8, 9.5, -3.1, 0.001, 0.01, 2.0])
        self.assertEqual(ov.signed, {"Gene expression": [0]})
        self.assertEqual(layer["signed"], [0])
        self.assertEqual(list(ov_mod.signed_values(layer)), [-0.8])
        from src.classes.AIInterpret.walker import direction as direction_mod
        self.assertEqual(direction_mod.data_direction(layer), -1, "AveExpr 9.5 read as the direction")
        self.assertEqual(null_mod.node_direction(ov, "g:1"), -1)

    def test_the_counter_hypothesis_flips_the_fold_change_only(self):
        ov, _ = self.limma_overlay([-0.8, 9.5, -3.1, 0.001, 0.01, 2.0])
        flipped = ov.layer_text("g:1", flip=True)
        self.assertIn("logFC +0.80 · AveExpr +9.50 · t-statistic −3.10 · p.Value +1.0e-03 · "
                      "adj.p.Value +0.01 · B-statistic +2.00", flipped)


class HundredthBoundaryTest(unittest.TestCase):
    """0.00996 in two significant digits is 1.0e-02, which is 0.01: printed
    "+1.0e-02" it normalised to 1e-2, while a model's correct "0.01" normalised
    to 0.01, and the sentence quoting it was pruned as unrecorded."""

    def test_a_value_that_rounds_to_a_hundredth_is_printed_as_one(self):
        self.assertEqual(ov_mod.value_text(0.00996), "+0.01")
        self.assertEqual(ov_mod.value_text(-0.009951), "−0.01")
        self.assertEqual(ov_mod.value_text(0.00994), "+9.9e-03", "below the boundary nothing changes")

    def test_every_spelling_of_it_is_in_the_record(self):
        record = {verify._norm(value) for value in verify.quoted_values("a " + ov_mod.value_text(0.00996))}
        for written in ("0.01", "+0.01", "1.0e-02", "1e-2", "1.0E-02", "1.0 × 10⁻²"):
            self.assertTrue(verify._in_record(written, record), written)
        for written in ("−0.01", "0.02", "1.0e-03"):
            self.assertFalse(verify._in_record(written, record), written)

    def test_values_over_a_hundredth_written_with_an_exponent_compare_in_fixed_form(self):
        self.assertEqual(verify._norm("1.5e-1"), "0.15")
        self.assertEqual(verify._norm("+1.90"), "+1.90")
        self.assertEqual(verify._norm("−4.1e-03"), "−4.1e-3")


class DesignTextTest(unittest.TestCase):

    def test_a_design_that_is_reasoning_and_nothing_else_is_no_design(self):
        # Saved under the old 300-token cap: the reasoning ended before its "</think>".
        job = fx.make_job()
        job.getExperimentDesign = lambda: (
            "The user asks for a description of the experiment design based on the column headers. "
            "The headers are Ctr_0H, Ik_24H. I need to write 2-3 sentences")
        self.assertEqual(card_mod.design_text_of(job), "")
        self.assertNotIn("The user asks", card_mod.job_card(job).get("perturbation") or "")

    def test_a_typed_design_that_opens_like_reasoning_is_kept(self):
        """Only a text about the drafting task is the model's reasoning; a
        user may well write "We need to compare ..." as the design."""
        job = fx.make_job()
        for typed in ("We need to compare KO vs WT at 0h, 2h and 6h after LPS.",
                      "OK, three groups: vehicle, 1 µM and 10 µM drug, in triplicate.",
                      "Let me be brief: knockout against wild-type liver, 4 mice each."):
            with self.subTest(typed=typed):
                job.getExperimentDesign = lambda typed=typed: typed
                self.assertEqual(card_mod.design_text_of(job), typed)
        for leaked in ("We need to describe the experiment design these columns represent.",
                       "Okay, the column headers are Ctr_0H and Ik_24H; write it in the first person.",
                       "Let me write 2-3 sentences about the groups."):
            with self.subTest(leaked=leaked):
                job.getExperimentDesign = lambda leaked=leaked: leaked
                self.assertEqual(card_mod.design_text_of(job), "")

    def test_a_first_person_design_is_kept(self):
        job = fx.make_job()
        job.getExperimentDesign = lambda: "First, I treated HepG2 cells with sorafenib for 24 h. Values are logFC."
        self.assertEqual(card_mod.design_text_of(job),
                         "First, I treated HepG2 cells with sorafenib for 24 h. Values are logFC.")

    def test_the_card_never_reads_the_leaked_reasoning(self):
        job = fx.make_job()
        job.getExperimentDesign = lambda: LEAKED
        self.assertTrue(card_mod.design_text_of(job).startswith("I performed a differential"))
        card = card_mod.job_card(job)
        self.assertEqual(card["perturbation"], "I performed a differential gene expression analysis of "
                                               "treated against control liver.")
        self.assertNotIn("</think>", "\n".join(card_mod.card_lines(card)))

    def test_the_chat_card_names_the_significance_columns(self):
        card = card_mod.job_card(significance_job())
        self.assertEqual(card["value_kind"][0]["columns"], ["p.Value", "adj.p.Value"])
        self.assertTrue(any(line.startswith("- value kind: Gene expression (columns p.Value, adj.p.Value)")
                            for line in card_mod.card_lines(card)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
