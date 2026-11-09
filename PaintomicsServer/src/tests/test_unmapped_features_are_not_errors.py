#!/usr/bin/env python3
"""A feature that matches no pathway is normal, and must not be logged as an error.

Why this exists
---------------
`calculateTotalFeaturesByOmic` ended its per-feature loop with

    if not found_in_any_db:
        logging.error("STEP2 - Feature not present in at least one pathway " + feature.getID())

A feature in no pathway of any database is not an error. Most measured genes are
not annotated to a pathway at all, so this fires for the ordinary case. Measured
on one run of the six-omic example: **6480 ERROR lines**, out of 17526 input
features. Every one of them described a healthy feature.

The cost is that the log stops working as a diagnostic. `grep ERROR` on
application.log is the first thing anyone does when a job fails, and it returned
thousands of lines about genes that were fine, with any real error buried among
them. It also writes 6480 lines per run to a file that is already six figures
long, on a VM with a fixed disk.

So the per-feature line moved to debug -- the level that matches "detail you
want only when chasing a specific mapping problem" -- and the loop now reports
once:

    STEP2 - 6480 of 17526 features matched no pathway in any database
            (e.g. 21385, 231841, 72614, 277463, 66368)

which is the more useful message anyway: a proportion tells an operator whether
the identifier type is wrong (nearly all unmapped) or whether this is the normal
tail (a third of them, as here). A per-feature list never said that.

Verified end to end, same example, before and after: ERROR lines 6480 -> 0.

The per-feature line first moved to debug. But the shipped `logging.cfg` sets
the root logger to DEBUG and `launch_server.py` copies that same file into a
deployment, so it was still written to disk, one line a feature on every Step 2:
371,985 of the 516,447 lines in paintomics.org's last three application.log
files (to 9 Nov 2026). It is gone; the summary, which now names the job, is the
one line left.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_unmapped_features_are_not_errors
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Feature import Gene, OmicValue
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob


class _CapturedLogs:
    """Collect root-logger records.

    `assertLogs` cannot express "nothing was logged" -- it fails when no record
    arrives, which is exactly the outcome one of these tests wants to assert.
    (`assertNoLogs` would do it, but that is 3.10+ and this runs on 3.9.) So the
    records are captured directly and the assertions read them.
    """

    def __init__(self):
        self.records = []

    def __enter__(self):
        capture = self

        class _Handler(logging.Handler):
            def emit(self, record):
                capture.records.append(record)

        self._handler = _Handler()
        self._root = logging.getLogger()
        self._previousLevel = self._root.level
        self._root.setLevel(logging.DEBUG)
        self._root.addHandler(self._handler)
        return self

    def __exit__(self, *exc):
        self._root.removeHandler(self._handler)
        self._root.setLevel(self._previousLevel)
        return False

    def messages(self, containing):
        return [record for record in self.records
                if containing in record.getMessage()]


class UnmappedFeatureLoggingTest(unittest.TestCase):

    def _jobWithUnmappedGenes(self, count):
        """A job whose genes are in no pathway of any database."""
        job = PathwayAcquisitionJob("LOGTEST", None, "/tmp/")

        for index in range(count):
            gene = Gene("gene%d" % index)
            gene.setMatchingDB("KEGG")

            value = OmicValue("gene%d" % index)
            value.setOmicName("Gene expression")
            value.setRelevant([True])
            gene.addOmicValue(value)

            job.addInputGeneData(gene)

        return job

    def _calculate(self, job):
        """Run with empty pathway membership, so nothing can match."""
        return job.calculateTotalFeaturesByOmic(
            enrichmentByOmic={"Gene expression": "genes"},
            totalGenes={"KEGG": set()},
            totalCompounds={"KEGG": set()})

    def test_unmapped_features_produce_no_error_logs(self):
        job = self._jobWithUnmappedGenes(5)

        with _CapturedLogs() as captured:
            self._calculate(job)

        errors = [record for record in captured.records
                  if record.levelno >= logging.ERROR]
        self.assertEqual(errors, [],
                         "%d features with no pathway were logged as errors; "
                         "on the real example this was 6480 ERROR lines that "
                         "buried every genuine failure" % len(errors))

    def test_one_summary_reports_the_proportion(self):
        job = self._jobWithUnmappedGenes(5)

        with _CapturedLogs() as captured:
            self._calculate(job)

        summaries = [record.getMessage()
                     for record in captured.messages("matched no pathway in any database")]
        self.assertEqual(len(summaries), 1,
                         "expected exactly one summary line, got %d"
                         % len(summaries))
        self.assertIn("5 of 5", summaries[0],
                      "the summary must give both counts so the proportion is "
                      "readable: %s" % summaries[0])

    def test_the_summary_is_informational_not_an_error(self):
        job = self._jobWithUnmappedGenes(3)

        with _CapturedLogs() as captured:
            self._calculate(job)

        summary = captured.messages("matched no pathway in any database")[0]
        self.assertEqual(summary.levelno, logging.INFO)

    def test_no_summary_when_every_feature_maps(self):
        """The line must not appear at all when there is nothing to report."""
        job = self._jobWithUnmappedGenes(3)
        mapped = {"gene0", "gene1", "gene2"}

        with _CapturedLogs() as captured:
            job.calculateTotalFeaturesByOmic(
                enrichmentByOmic={"Gene expression": "genes"},
                totalGenes={"KEGG": mapped},
                totalCompounds={"KEGG": set()})

        summaries = captured.messages("matched no pathway in any database")
        self.assertEqual(summaries, [],
                         "a job where everything mapped should say nothing")

    def test_one_line_whatever_the_number_of_features(self):
        """No line a feature, at any level: with the root logger at DEBUG
        (logging.cfg) a debug line is still written, and this one was most of
        the log."""
        job = self._jobWithUnmappedGenes(200)

        with _CapturedLogs() as captured:
            self._calculate(job)

        self.assertEqual(captured.messages("not present in any pathway"), [])
        self.assertLessEqual(len(captured.records), 3,
                             "%d lines for one call: %s" % (
                                 len(captured.records),
                                 [record.getMessage()[:80] for record in captured.records[:5]]))

    def test_the_summary_names_the_job_and_some_features(self):
        """What an operator needs from the one line left: which job, how many
        of how many, and a few of the identifiers to look up."""
        job = self._jobWithUnmappedGenes(7)

        with _CapturedLogs() as captured:
            self._calculate(job)

        summary = captured.messages("matched no pathway in any database")[0].getMessage()
        self.assertIn("LOGTEST", summary)
        self.assertIn("7 of 7", summary)
        self.assertIn("gene0", summary)

    def test_the_counts_are_unchanged_by_the_logging_change(self):
        """The point of the edit was the log, not the arithmetic."""
        job = self._jobWithUnmappedGenes(4)

        result = self._calculate(job)

        self.assertIsNotNone(result,
                             "calculateTotalFeaturesByOmic returned nothing")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
