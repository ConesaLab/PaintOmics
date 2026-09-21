#!/usr/bin/env python3
"""The regulator panel behind the direction check: its shape (two classes,
real-looking PubMed ids, unique symbols), how a label or layer member resolves
to a row, and the reading rule -- with the pathway, against it. Offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import regulators  # noqa: E402


class WalkerRegulatorsTest(unittest.TestCase):
    def rows(self, cls):
        return [r for r in regulators.PANEL if r["class"] == cls]

    def test_at_least_fifteen_rows_per_class_and_no_other_class(self):
        self.assertGreaterEqual(len(self.rows(regulators.FEEDBACK)), 15)
        self.assertGreaterEqual(len(self.rows(regulators.INHIBITOR)), 15)
        self.assertEqual({r["class"] for r in regulators.PANEL}, {regulators.FEEDBACK, regulators.INHIBITOR})

    def test_every_row_is_complete(self):
        for row in regulators.PANEL:
            self.assertEqual(set(row), {"symbol", "class", "pathway", "pmid", "note"}, row)
            for key, value in row.items():
                self.assertIsInstance(value, str, (row["symbol"], key))
                self.assertTrue(value.strip(), (row["symbol"], key))

    def test_pmids_are_seven_or_eight_digits(self):
        for row in regulators.PANEL:
            self.assertTrue(row["pmid"].isdigit(), row)
            self.assertIn(len(row["pmid"]), (7, 8), row)

    def test_symbols_are_unique_case_insensitively(self):
        upper = [r["symbol"].upper() for r in regulators.PANEL]
        self.assertEqual(len(upper), len(set(upper)))
        self.assertEqual(len(regulators.BY_SYMBOL), len(regulators.PANEL))

    def test_panel_row_resolves_symbols_and_box_labels(self):
        self.assertEqual(regulators.panel_row("cish")["symbol"], "Cish")
        self.assertEqual(regulators.panel_row("  PTEN ")["symbol"], "Pten")
        self.assertEqual(regulators.panel_row("Socs1, Socs3")["symbol"], "Socs1")
        self.assertEqual(regulators.panel_row("Foxo1; Socs3 ...")["symbol"], "Socs3")

    def test_panel_row_is_none_off_panel(self):
        self.assertIsNone(regulators.panel_row("Foxo1"))
        self.assertIsNone(regulators.panel_row("Foxo1, Stat3"))
        self.assertIsNone(regulators.panel_row(""))
        self.assertIsNone(regulators.panel_row(None))
        self.assertIsNone(regulators.panel_row(["Cish"]))

    def test_feedback_reporter_reads_with_the_pathway(self):
        row = regulators.panel_row("Cish")
        self.assertEqual(regulators.implied_direction(row, -1), "down")
        self.assertEqual(regulators.implied_direction(row, 1), "up")
        self.assertEqual(regulators.implied_direction(row, -2.5), "down")

    def test_true_inhibitor_reads_against_the_pathway(self):
        row = regulators.panel_row("Pten")
        self.assertEqual(regulators.implied_direction(row, -1), "up")
        self.assertEqual(regulators.implied_direction(row, 1), "down")

    def test_flat_missing_or_unknown_reads_none(self):
        row = regulators.panel_row("Pten")
        self.assertEqual(regulators.implied_direction(row, 0), "none")
        self.assertEqual(regulators.implied_direction(row, None), "none")
        self.assertEqual(regulators.implied_direction(row, float("nan")), "none")
        self.assertEqual(regulators.implied_direction(None, -1), "none")
        self.assertEqual(regulators.implied_direction({"class": "other"}, -1), "none")

    def test_reading_rule_names_both_classes_with_examples(self):
        rule = regulators.reading_rule()
        self.assertIn("feedback", rule)
        self.assertIn("inhibitor", rule)
        self.assertEqual(len(rule.splitlines()), 2)
        self.assertIn("Cish down means JAK-STAT down", rule)
        self.assertIn("Pten down means PI3K-AKT up", rule)


if __name__ == "__main__":
    unittest.main()
