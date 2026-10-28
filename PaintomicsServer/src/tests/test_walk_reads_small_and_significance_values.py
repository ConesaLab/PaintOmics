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
                                  "columns": ["p.Value", "adj.p.Value"], "index": [3, 4]}])

    def test_an_omic_of_p_values_alone_is_significance(self):
        kinds = card_mod.value_kinds({"Gene expression": ["#gene", "adj.p.Value"]})
        self.assertEqual(kinds[0]["kind"], "significance")

    def test_a_word_the_shared_prefix_hides_is_still_read(self):
        kinds = card_mod.value_kinds({"Gene expression": ["#gene", "padj_KO", "padj_WT"]})
        self.assertEqual(kinds, [{"omic": "Gene expression", "kind": "significance",
                                  "columns": ["KO", "WT"], "index": [0, 1]}])

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


class DesignTextTest(unittest.TestCase):

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
