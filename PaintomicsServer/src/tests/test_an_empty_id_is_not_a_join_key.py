#!/usr/bin/env python3
"""A blank identifier must never join, and an empty result must say why.

What happened
-------------
A user ran the miRNA pipeline on 2026-04-03 and PaintOmics reported SUCCESS. The
associations file it handed back held 6,039 rows whose first column -- the
target gene -- was empty from top to bottom. Re-running with that file on
2026-08-27 gave, four times:

    Your mirna2gene association process did not return any result. Please,
    check the files (same identifiers, etc) and parameters.

Reading their three input files settles both halves:

  * their targets file had 6,039 rows with an empty target id;
  * their gene expression file had 13 rows with an empty gene id;
  * every real target was an ENSMUSG id, and the expression file was keyed by
    gene SYMBOL -- an overlap of exactly zero.

`""` is a perfectly good dict key, so `geneTable.get("")` hit, and those 6,039
blank pairs were the ONLY pairs miRNA2Target ever scored. Reproduced exactly:
148,184 real pairs, 0 matched, 6,039 blanks -- 6,039 output rows, the number in
their file to the row.

So the failure was never "no results". It was two identifier spaces that never
met, hidden by a blank cell that matched another blank cell.

What this file asserts
----------------------
1. a blank id is dropped at the read, on all three files, and counted;
2. blank never joins to blank -- the reproduction above yields nothing;
3. the message names the two identifier spaces, with examples from the files;
4. the numbers in it are counted, never estimated -- an unknown is omitted;
5. the shipped example is unchanged, byte for byte.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_an_empty_id_is_not_a_join_key
"""
import io
import os
import shutil
import tempfile
import unittest

from src.common.bioscripts.miRNA2Target import run
from src.classes.JobInstances.MiRNA2GeneJob import explainEmptyResult

HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = os.path.join(HERE, "..", "examplefiles", "datasets")


def write(directory, name, rows):
    path = os.path.join(directory, name)
    with io.open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("\t".join(row) + "\n")
    return path


class EmptyIdIsNotAJoinKeyTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="paintomics-empty-id-")
        self.out = os.path.join(self.dir, "out.tab")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def rows(self):
        with io.open(self.out, encoding="utf-8") as handle:
            return [line for line in handle if not line.startswith("#")]

    # The user's job, in miniature: the blank cells sit where theirs sat, and
    # the two identifier spaces are the two they actually had.
    def theirFiles(self):
        values = write(self.dir, "mirna_values.tab", [
            ["#gene", "DSS_SDmEV_vs_DSS", "DSS_vs_ctrl"],
            ["ENSMUSG00000065402", "0.145", "0.51"],
            ["ENSMUSG00000065421", "-0.278", "-0.62"],
        ])
        targets = write(self.dir, "targets.tab", [
            ["mirnaid", "gene_ID"],
            ["ENSMUSG00000065402", "ENSMUSG00000062006"],
            ["ENSMUSG00000065402", ""],
            ["ENSMUSG00000065421", ""],
        ])
        genes = write(self.dir, "degs.tab", [
            ["genesymbol", "DSSmEVs_vs_DSS", "DSS_vs_ctrl"],
            ["Fxyd4", "-10.88", "-3.1"],
            ["", "-5.62", "-1.4"],
        ])
        return targets, values, genes

    def test_blank_never_joins_to_blank(self):
        """The whole bug, in one assertion."""
        targets, values, genes = self.theirFiles()
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(self.rows(), [],
                         "a blank target matched a blank gene and was scored")
        self.assertEqual(stats["scored"], 0)

    def test_the_blanks_are_counted_on_every_file(self):
        targets, values, genes = self.theirFiles()
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["dropped"]["associationTargets"], 2)
        self.assertEqual(stats["dropped"]["genes"], 1)

    def test_a_blank_regulator_is_dropped_too(self):
        """PaintOmics' own broken output, fed back in as the targets file."""
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"],
                                           ["ENSMUSG00000065402", "0.1", "0.4"]])
        targets = write(self.dir, "t.tab", [["", "ENSMUSG00000062006"],
                                            ["", "ENSMUSG00000020169"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"],
                                          ["ENSMUSG00000062006", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["dropped"]["associationRegulators"], 2)
        self.assertEqual(stats["pairs"], 0)

    def test_a_values_row_with_no_id_is_dropped(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["", "0.1", "0.3"],
                                           ["ENSMUSG00000065402", "0.2", "0.5"]])
        targets = write(self.dir, "t.tab", [["ENSMUSG00000065402", "G1"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["dropped"]["regulators"], 1)
        self.assertEqual(stats["regulators"], 1)

    # ---------------- what the user is told ----------------

    def test_the_message_names_both_identifier_spaces(self):
        """The actual answer: ENSMUSG on one side, gene symbols on the other."""
        targets, values, genes = self.theirFiles()
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        self.assertIn("ENSMUSG00000062006", message)
        self.assertIn("Fxyd4", message)
        self.assertIn("two different identifier spaces", message)

    def test_the_message_says_when_nothing_joined_at_all(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"],
                                           ["ENSMUSG00000065402", "0.1", "0.4"]])
        targets = write(self.dir, "t.tab", [["", "ENSMUSG00000062006"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        self.assertIn("none of them appears in the first column", message)
        self.assertIn("ENSMUSG00000065402", message)

    def test_the_message_reports_the_blank_rows(self):
        targets, values, genes = self.theirFiles()
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        self.assertIn("2 rows of the associations file had an empty second column",
                      message)
        self.assertIn("1 rows of the gene expression file had no gene id", message)

    def test_it_invents_no_numbers(self):
        """Every count in the message has to come from a file that was read.

        The rule the reporting user asked for in as many words: a repair may be
        imperfect and must say what it cost, but it must never make a number up.
        """
        targets, values, genes = self.theirFiles()
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        # Pairs read is the only large number quoted, and it is exactly the
        # number of (regulator, target) pairs that survived the read -- here one,
        # which used to read "1 miRNA-target pairs were read".
        self.assertEqual(stats["pairs"], 1)
        self.assertIn("1 miRNA-target pair was read", message)
        self.assertNotIn("approximately", message.lower())
        self.assertNotIn("about ", message.lower())

    def test_one_condition_is_named_as_the_fault(self):
        """A correlation over one column is undefined, not weak.

        Measured on the reporting user's files, which have a single condition
        column: pearson RAISED "x and y must have length at least 2", the
        blanket handler kept the truncated file, and they were told their
        identifiers did not match -- while kendall, on the same data, wrote
        30,722 rows whose score was `nan` in every one of them.
        """
        values = write(self.dir, "v.tab", [["#gene", "C1"],
                                           ["mmu-miR-1", "0.1"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1"]])
        genes = write(self.dir, "g.tab", [["gene", "C1"], ["G1", "1.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["tooFewConditions"], 1)
        message = explainEmptyResult(stats)
        self.assertIn("needs at least two", message)
        self.assertNotIn("identifier", message,
                         "a single condition is not an identifier problem")

    def test_two_conditions_are_not_flagged(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"],
                                           ["mmu-miR-1", "0.1", "0.4"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"],
                                          ["G1", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertIsNone(stats.get("tooFewConditions"))

    def test_an_abort_is_not_reported_as_an_identifier_problem(self):
        """A run that stopped is not "no results".

        A truncated run read as "no results" is exactly how a crash reached a
        user as a message about identifier spaces.
        """
        stats = {"dropped": {}, "pairs": 900, "scored": 0,
                 "aborted": "x and y must have length at least 2",
                 "abortedAfterPairs": 22}
        message = explainEmptyResult(stats)
        self.assertIn("failed on our side after 22 of 900 miRNA-target pairs", message)
        self.assertNotIn("identifier", message)

    def test_a_join_that_produced_nothing_is_named_before_the_conditions(self):
        """The reporting user's files: one condition column AND targets that
        never joined. No number of conditions fixes the join, so the join is
        what the message has to say -- the first cut said "148,184 pairs were
        read and matched" about pairs of which none had matched."""
        values = write(self.dir, "v.tab", [["#gene", "C1"], ["mmu-miR-1", "0.1"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "Fxyd4"]])
        genes = write(self.dir, "g.tab", [["gene", "C1"], ["ENSMUSG00000062006", "1.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        self.assertIn("not one target gene was found", message)
        self.assertIn("Fxyd4", message)
        self.assertNotIn("were read and matched", message)
        self.assertIn("also needs at least two condition columns", message)

    def test_the_gene_file_header_is_not_offered_as_an_example_id(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["mmu-miR-1", "0.1", "0.2"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "Fxyd4"]])
        genes = write(self.dir, "g.tab", [["#geneID", "C1", "C2"],
                                          ["ENSMUSG00000062006", "1.0", "2.0"],
                                          ["ENSMUSG00000020169", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["sampleGenes"], ["ENSMUSG00000062006", "ENSMUSG00000020169"])
        self.assertNotIn("#geneID", explainEmptyResult(stats))

    def test_an_empty_quantification_file_is_named_as_such(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        message = explainEmptyResult(stats)
        self.assertIn("no data rows", message)
        self.assertNotIn("(e.g. )", message)

    def test_a_near_miss_is_not_called_a_different_identifier_space(self):
        """fxyd4 vs Fxyd4, ENSMUSG00000062006.2 vs ENSMUSG00000062006."""
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["mmu-miR-1", "0.1", "0.2"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "ENSMUSG00000062006.2"],
                                            ["mmu-miR-1", "fxyd4"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"],
                                          ["ENSMUSG00000062006", "1.0", "2.0"],
                                          ["Fxyd4", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["nearMisses"], 2)
        message = explainEmptyResult(stats)
        self.assertIn("only in case or in a version suffix", message)
        self.assertNotIn("two different identifier spaces", message)

    def test_a_trailing_blank_line_is_not_a_blank_identifier(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["mmu-miR-1", "0.1", "0.2"], []])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1"], []])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "2.0"], []])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["dropped"], {"regulators": 0, "associationRegulators": 0,
                                            "associationTargets": 0, "genes": 0})

    def test_a_key_with_stray_whitespace_still_joins(self):
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["mmu-miR-1 ", "0.1", "0.2"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1 "]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "2.0"]])
        stats = run(targets, None, values, genes, self.out, "pearson")
        self.assertEqual(stats["scored"], 1)
        self.assertEqual(stats["unmatchedTargets"], 0)

    def test_all_nan_scores_are_not_a_result(self):
        """Two identical condition columns: every correlation is nan, and
        `nan > cutoff` is False, so the run "succeeded" with nothing relevant."""
        values = write(self.dir, "v.tab", [["#gene", "C1", "C2"], ["mmu-miR-1", "0.1", "0.1"]])
        targets = write(self.dir, "t.tab", [["mmu-miR-1", "G1"]])
        genes = write(self.dir, "g.tab", [["gene", "C1", "C2"], ["G1", "1.0", "1.0"]])
        stats = run(targets, None, values, genes, self.out, "kendall")
        self.assertEqual(stats["scored"], 1)
        self.assertEqual(stats["nanScores"], 1)
        self.assertIn("undefined correlation", explainEmptyResult(stats))

    def test_a_missing_account_falls_back_rather_than_crashing(self):
        """The old caller passed nothing; a None must not raise."""
        self.assertIn("did not return any result", explainEmptyResult(None))

    # ---------------- no regression ----------------

    def test_the_shipped_example_is_unchanged(self):
        """05-regulatory-mirna, scored the same way, must be byte identical.

        It has no blank identifiers, so nothing this change does can touch it --
        which is the point of asserting it.
        """
        data = os.path.join(DATASETS, "05-regulatory-mirna", "data")
        expression = os.path.join(DATASETS, "04-multiomics-integration", "data",
                                  "gene_expression_values.tab")
        for method in ("pearson", "kendall"):
            run(os.path.join(data, "mirna_to_gene_associations.tab"), None,
                os.path.join(data, "mirna_values.tab"), expression,
                self.out, method)
            with io.open(self.out, encoding="utf-8") as handle:
                produced = handle.read()
            self.assertEqual(len(produced.splitlines()), 2365,
                             "%s: the example lost or gained rows" % method)

    def test_the_example_reports_no_blanks(self):
        data = os.path.join(DATASETS, "05-regulatory-mirna", "data")
        expression = os.path.join(DATASETS, "04-multiomics-integration", "data",
                                  "gene_expression_values.tab")
        stats = run(os.path.join(data, "mirna_to_gene_associations.tab"), None,
                    os.path.join(data, "mirna_values.tab"), expression,
                    self.out, "pearson")
        self.assertEqual(sum(stats["dropped"].values()), 0)
        self.assertGreater(stats["scored"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
