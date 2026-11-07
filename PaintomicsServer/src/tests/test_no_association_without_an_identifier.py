#!/usr/bin/env python3
"""miRNA2Genes must not write an association row with no target gene ID.

The behaviour this guards
-------------------------
A user's own regulator_associations file -- produced by this method and handed
back to them as a successful result -- was measured at

    6,039 rows, 6,039 of them with an EMPTY target gene identifier (100%)

An identifier is what makes a row mean anything. Written without one, a row is
not a weak result; it is not a result. The job reported success anyway.

Where it goes: three steps later MORE refuses the analysis with

    Association file for omic 'miRNA' shares no target IDs with the target
    expression file.
      association targets:
      expression features: Fxyd4, Klk15, Gm49311

-- nothing after "association targets:", because there was nothing to print.
The user is sent to check that both files "identify features the same way",
which is unanswerable: one side has no identifiers at all.

`geneID` comes from `line[1].upper()` at MiRNA2GeneJob.py:413 with nothing
asserting it is non-empty, and none of the five writes in the output loop
looked at it.

Rows without an identifier are now skipped and counted, the count is logged,
and a run where EVERY row was unnamed raises instead of shipping a file of
empty strings -- matching this file's existing habit of raising when the
association process returns nothing at all (MiRNA2GeneJob.py:496).

Usage:
    cd PaintomicsServer
    python -m src.tests.test_no_association_without_an_identifier
"""
import io
import os
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
JOB = os.path.join(REPO, "PaintomicsServer", "src", "classes", "JobInstances",
                   "MiRNA2GeneJob.py")

# The five writes in the output loop, each of which used to take geneID
# unchecked.
WRITES = ("genesToMiRNAFile.write", "regulator2genesOutput.write",
          "regulatorAssociations.write", "regulator2genesRelevant.write",
          "regulatorRelevantAssociations.write")


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def output_loop(source):
    """The body of `for geneID, gene in self.getInputGenesData().items():`."""
    header = "for geneID, gene in self.getInputGenesData().items():"
    start = source.index(header)
    indent = len(source[:start].split("\n")[-1])
    body = []
    for line in source[start:].split("\n")[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        body.append(line)
    return "\n".join(body)


class NoAssociationWithoutAnIdentifierTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = read(JOB)
        cls.loop = output_loop(cls.source)

    def test_an_unnamed_gene_is_skipped_before_anything_is_written(self):
        """The regression: five writes, none of which looked at geneID."""
        guard = 'if not str(geneID).strip():'
        self.assertIn(guard, self.loop)
        skip = self.loop.index("continue")
        for write in WRITES:
            if write in self.loop:
                self.assertLess(skip, self.loop.index(write),
                                "%s runs before the guard" % write)

    def test_every_write_in_the_loop_is_behind_the_guard(self):
        """So a sixth output file cannot be added in front of it."""
        found = [w for w in WRITES if w in self.loop]
        self.assertGreaterEqual(len(found), 4,
                                "the output loop has changed shape: %s" % found)

    def test_the_count_is_reported(self):
        """Silence is what turned a broken run into a "successful" one."""
        self.assertIn("skippedUnnamed", self.source)
        self.assertIn("logging.warning", self.source)
        after = self.source[self.source.index("skippedUnnamed = 0"):]
        self.assertIn("had no identifier", after)

    def test_an_entirely_unnamed_run_raises_instead_of_shipping_a_file(self):
        after = self.source[self.source.index("skippedUnnamed = 0"):]
        self.assertIn("written == 0", after)
        raised = after[after.index("written == 0"):]
        # A refusal of the files, so the dialog says so and offers no Report
        # (test_mirna_scoring_keeps_no_partial_result runs it).
        self.assertIn("raise InputFileError", raised)
        self.assertIn("empty name", raised)

    def test_the_message_says_which_column_to_look_at(self):
        """"Check your files" is what the old dead end already said."""
        after = self.source[self.source.index("skippedUnnamed = 0"):]
        self.assertIn("second column", after)

    def test_the_identifier_still_comes_from_column_one(self):
        """The premise. If the parse moves, this guard is in the wrong place."""
        self.assertIn("geneID     = line[1].upper()", self.source)

    def test_the_guard_does_not_swallow_a_legitimate_zero(self):
        """A gene called "0" is a name; only blank is not."""
        self.assertIn("str(geneID).strip()", self.loop)
        self.assertNotIn("if not geneID:", self.loop)


if __name__ == "__main__":
    unittest.main(verbosity=2)
