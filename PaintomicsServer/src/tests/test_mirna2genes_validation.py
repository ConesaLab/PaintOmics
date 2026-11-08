#!/usr/bin/env python3
"""The miRNA omic's input guard, which nothing exercised.

Why this exists
---------------
`MiRNA2GeneJob.validateInput` and `validateFile` had no test naming them. The
miRNA path is the one that pairs two omics in a single job -- a miRNA table and
optionally the gene-expression table its targets live in -- and it picks them
apart by *name*:

    miRNAdataInput = next((x for x in geneDataInputs
                           if x["omicName"].lower() != "gene expression"))

`next()` with no default raises `StopIteration` rather than returning None, so
the shape of the omic list is load-bearing in a way the surrounding
accumulate-a-message style does not suggest. The tests below pin what happens
at those edges, so a future refactor of the naming convention fails here rather
than in a queue worker.

The relevant-features guard is also unusual and worth pinning: rather than
parsing, it rejects on **line length over 80 characters**, as a heuristic for
"you uploaded the values file by mistake". That is a real user error -- the two
fields sit next to each other in the form -- and the heuristic is invisible
from the code that calls it.

None of this is reachable from the bundled example: `validateFile` returns
immediately when `isExample` is set. Every case is built from `fake_omics`.

The selection method is checked here too. The form offered "by max. fold-change
of feature expression" (value "fc"), which computed no fold change and selected
as positive correlation does, and so did any other value the selection does not
name. The form now offers the three it has, and the server
refuses any other value but "fc", which it still runs as positive correlation.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_mirna2genes_validation
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob
from src.common.ServerErrorManager import InputFileError
from src.tests import fake_omics

# Written out rather than imported, so that a change to the server's list has to
# be a change here too.
SELECTION_METHODS = ("negative_correlation", "positive_correlation", "abs_correlation")

STEP1_VIEWS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "PaintomicsClient",
    "public_html", "app", "view", "PathwayAcquisitionViews", "PA_Step1Views.js"))


def _withoutComments(source):
    """The source without /* */ and // comments. Good enough for the miRNA card's
    correlation options, whose strings hold no "//" and no "/*"."""
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _correlationOptionsText():
    """The miRNA card's correlation options, from Report to the end of the cutoff
    field, as the browser gets them, comments left out."""
    with open(STEP1_VIEWS, encoding="utf-8") as handle:
        source = handle.read()
    start = source.index('itemId: "reportMethodField"')
    cutoff = source.index('itemId: "cutoffField"', start)
    end = source.index("helpTip:", cutoff)
    end = source.index("\n", end)
    return _withoutComments(source[start:end])


def _offeredSelections():
    """(label, value) of each entry in the Selection method combo."""
    text = _correlationOptionsText()
    start = text.index('itemId: "selectionMethodField"')
    block = text[start:text.index("helpTip:", start)]
    return re.findall(r'\[\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\]', block)


class _Job(MiRNA2GeneJob):
    """Real validators, scratch input directory."""

    def __init__(self, inputDir):
        self._inputDir = inputDir
        self.geneBasedInputOmics = []
        self.cutoff = 0.5
        self.selection_method = "negative_correlation"

    def getInputDir(self):
        return self._inputDir

    def getGeneBasedInputOmics(self):
        return self.geneBasedInputOmics


class MiRNAOmicValidationTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="paintomics_mirna_")
        self.job = _Job(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _error(self):
        try:
            self.job.validateInput()
            return ""
        except Exception as exc:
            return str(exc)

    def _miRNAOnly(self, dataFile=None, relevantFile=None):
        dataFile = dataFile or fake_omics.miRNAFile(self.dir)
        self.job.geneBasedInputOmics = [
            fake_omics.omicInput(dataFile, relevantFile, omicName="miRNA-seq")]

    # -- happy paths --------------------------------------------------------

    def test_a_well_formed_mirna_file_alone_is_accepted(self):
        self._miRNAOnly()

        self.assertEqual(self._error(), "",
                         "a valid miRNA omic was rejected")

    def test_a_mirna_file_paired_with_gene_expression_is_accepted(self):
        """The two-omic shape the servlet exists to support."""
        self.job.geneBasedInputOmics = [
            fake_omics.omicInput(fake_omics.miRNAFile(self.dir),
                                 omicName="miRNA-seq"),
            fake_omics.omicInput(fake_omics.geneExpressionFile(self.dir),
                                 omicName="Gene expression"),
        ]

        self.assertEqual(self._error(), "")

    def test_the_pairing_is_case_insensitive(self):
        """The lookup lowercases, so 'GENE EXPRESSION' must pair too."""
        self.job.geneBasedInputOmics = [
            fake_omics.omicInput(fake_omics.miRNAFile(self.dir),
                                 omicName="miRNA-seq"),
            fake_omics.omicInput(fake_omics.geneExpressionFile(self.dir),
                                 omicName="GENE EXPRESSION"),
        ]

        self.assertEqual(self._error(), "")

    # -- the cutoff ---------------------------------------------------------

    def test_a_non_numeric_cutoff_is_refused(self):
        self._miRNAOnly()
        self.job.cutoff = "strict"

        self.assertIn("Cutoff", self._error())

    def test_a_numeric_string_cutoff_is_coerced_not_refused(self):
        """Form fields arrive as strings; that must not be an error."""
        self._miRNAOnly()
        self.job.cutoff = "0.8"

        self.assertEqual(self._error(), "")
        self.assertEqual(self.job.cutoff, 0.8)

    # -- the selection method -----------------------------------------------

    def test_every_selection_method_the_form_offers_is_accepted(self):
        """And "fc", which the form offered until it was found to be positive
        correlation under another name: a page opened before the change still sends it."""
        for method in SELECTION_METHODS + ("fc",):
            with self.subTest(method=method):
                self._miRNAOnly()
                self.job.selection_method = method
                self.assertEqual(self._error(), "")

    def test_an_unknown_selection_method_is_refused_by_name(self):
        """It fell through every branch of the selection, as "fc" did, and kept the
        pairs whose correlation was above the cutoff under the name it came with."""
        for method in ("max_fc", "similar_fc", "", "Negative_Correlation"):
            with self.subTest(method=method):
                self._miRNAOnly()
                self.job.selection_method = method
                with self.assertRaises(InputFileError) as refused:
                    self.job.validateInput()
                self.assertIn(
                    "The selection method %s is not one this tool has. Use "
                    "negative_correlation, positive_correlation or abs_correlation."
                    % ("\"%s\"" % method if method else "sent"), str(refused.exception))

    def test_a_selection_method_that_is_not_plain_text_is_not_repeated(self):
        self._miRNAOnly()
        self.job.selection_method = "<img src=x onerror=alert(1)>"
        error = self._error()
        self.assertIn("The selection method sent is not one this tool has.", error)
        self.assertNotIn("<img", error)

    def test_the_form_offers_exactly_these_selection_methods(self):
        """What the Step 1 miRNA card and the tool page (the same panel) offer."""
        self.assertEqual(sorted(value for _label, value in _offeredSelections()),
                         sorted(SELECTION_METHODS))

    def test_no_correlation_option_on_the_form_speaks_of_a_fold_change(self):
        """The cutoff's help said "Threshold for the correlation or fold change", and
        nothing computes a fold change."""
        self.assertNotIn("fold", _correlationOptionsText().lower())

    # -- the values file ----------------------------------------------------

    def test_a_ragged_row_is_refused(self):
        self._miRNAOnly(fake_omics.raggedFile(self.dir, nConditions=4))

        self.assertIn("Expected 5 columns but found 4", self._error())

    def test_a_non_numeric_value_is_refused(self):
        """Regression: this check was dead code until 2026-08-08.

        The guard was `map(float, ...)` inside a try. On Python 3 map() is lazy,
        so nothing was converted, nothing raised, and the except branch could
        never run -- a miRNA table of text validated clean and reached the
        analysis. `list(...)` around it is the whole fix.
        """
        self._miRNAOnly(fake_omics.nonNumericValuesFile(self.dir, nConditions=4))

        self.assertIn("invalid values", self._error())

    def test_comma_decimal_marks_are_named_specifically(self):
        """The friendliest branch of that guard, also unreachable until fixed.

        European locales export `1,5` for 1.5, and the message says so rather
        than leaving the user to guess what "invalid" meant.
        """
        path = os.path.join(self.dir, "commas.tab")
        with open(path, "w") as handle:
            handle.write("#ID\tC1\tC2\n")
            handle.write("mmu-miR-100-5p\t1,5\t2,5\n")
        self._miRNAOnly(path)

        self.assertIn("commas instead of dots", self._error())

    def test_a_header_without_a_hash_is_accepted_for_mirna(self):
        """Deliberately unlike the region-based guard, which demands the hash.

        A non-numeric first row is simply skipped here. Pinned so that
        harmonising the two validators is a decision someone makes on purpose.
        """
        self._miRNAOnly(fake_omics.headerWithoutHash(self.dir, nConditions=4))

        self.assertEqual(self._error(), "")

    def test_a_missing_values_file_is_refused_by_name(self):
        self.job.geneBasedInputOmics = [{"omicName": "miRNA-seq",
                                         "inputDataFile": "absent.tab",
                                         "isExample": False}]

        error = self._error()
        self.assertIn("absent.tab", error)
        self.assertIn("not found", error)

    # -- the relevant-features heuristic ------------------------------------

    def test_a_relevant_features_file_of_plain_ids_is_accepted(self):
        self._miRNAOnly(relevantFile=fake_omics.relevantFeaturesFile(
            self.dir, ids=["mmu-miR-100-5p", "mmu-miR-101-5p"]))

        self.assertEqual(self._error(), "")

    def test_a_values_file_uploaded_as_relevant_features_is_refused(self):
        """The long-line heuristic: the two form fields sit side by side."""
        wide = os.path.join(self.dir, "wrong_field.tab")
        with open(wide, "w") as handle:
            handle.write("mmu-miR-100-5p\t" + "\t".join(["1.234567"] * 20) + "\n")
        self._miRNAOnly(relevantFile=wide)

        self.assertIn("does not look like a Relevant Features file",
                      self._error())

    # -- the shape of the omic list, which next() makes load-bearing --------

    def test_a_job_whose_only_omic_is_gene_expression_fails_readably(self):
        """`next()` has no default here, so this must not be a bare StopIteration.

        A StopIteration escaping validateInput reaches the queue worker as an
        empty-message crash, and the user is told nothing at all. Whatever the
        guard does, it has to say something.
        """
        self.job.geneBasedInputOmics = [
            fake_omics.omicInput(fake_omics.geneExpressionFile(self.dir),
                                 omicName="Gene expression")]

        try:
            self.job.validateInput()
            refusal = ""
        except StopIteration:
            self.fail("validateInput raised a bare StopIteration: a miRNA job "
                      "with no miRNA omic crashes instead of being refused")
        except Exception as exc:
            refusal = str(exc)

        self.assertNotEqual(refusal, "",
                            "a miRNA job carrying no miRNA omic was accepted")

    def test_two_omics_and_neither_is_gene_expression_fails_readably(self):
        """The second `next()` runs whenever len > 1, matching on the name."""
        self.job.geneBasedInputOmics = [
            fake_omics.omicInput(fake_omics.miRNAFile(self.dir, "a.tab"),
                                 omicName="miRNA-seq"),
            fake_omics.omicInput(fake_omics.miRNAFile(self.dir, "b.tab"),
                                 omicName="Transcription factor"),
        ]

        try:
            self.job.validateInput()
        except StopIteration:
            self.fail("validateInput raised a bare StopIteration when two "
                      "omics were supplied and neither was gene expression")
        except Exception:
            pass

    def test_an_example_omic_skips_parsing_entirely(self):
        self.job.geneBasedInputOmics = [{"omicName": "miRNA-seq",
                                         "inputDataFile": "nothing_here.tab",
                                         "isExample": True}]

        self.assertEqual(self._error(), "")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
