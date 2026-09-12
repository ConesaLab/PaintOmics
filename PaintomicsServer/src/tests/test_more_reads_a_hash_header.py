#!/usr/bin/env python3
"""MORE must read PaintOmics' own `#gene` header as a header, not a comment.

The behaviour this guards
-------------------------
PaintOmics writes its values files with the identifier column headed `#gene`,
and twelve of the shipped example datasets do it -- as does every file a user
exports from PaintOmics. R's read.table defaults to `comment.char="#"`, so on
the R engine that line was stripped before it could be used as a header, the
first DATA row became the header, and its VALUES became the sample names.
Measured on a three-feature file headed `#gene<TAB>s1<TAB>s2<TAB>s3<TAB>s4`:

    R as shipped     nrow=2  samples "1.0,2.0,3.0,4.0"  features G2,G3 (G1 LOST)
    R with the fix   nrow=3  samples "s1,s2,s3,s4"      features G1,G2,G3
    the Rust port    nrow=3  samples "s1,s2,s3,s4"      features G1,G2,G3

Downstream that surfaced as

    MORE ERROR: No common sample names across input files.

which sends the user to inspect a sample naming that has nothing wrong with it.

What changed
------------
The R engine is gone, and with it the defect and the source-level guard that
pinned its fix (`comment.char=""` on every reader in runMORE.R). The port never
had the problem -- it has no notion of a comment character -- but "never had
it" is not the same as "is checked for it", and the convention is PaintOmics'
own, so the property is pinned here against the engine that actually runs now.

It escaped CI originally because 06-regulatory-more, the only MORE fixture,
writes plain `Sample` / `GeneID` / `RegulatorID` headers and never exercises
the convention. That is still true, which is why this file builds its own.

Usage:
    cd PaintomicsServer
    PAINTOMICS_MORE_RS=/path/to/more-rs \\
        python -m src.tests.test_more_reads_a_hash_header
"""
import io
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATASETS = os.path.join(REPO, "PaintomicsServer", "src", "examplefiles", "datasets")
BUNDLED = os.path.join(REPO, "PaintomicsServer", "src", "common", "bioscripts", "more-rs")

# The header PaintOmics itself writes.
HASHED = "#gene\ts1\ts2\ts3\ts4\n"
TARGETS = HASHED + "G1\t1.0\t2.0\t3.0\t4.0\nG2\t2.0\t1.0\t4.0\t3.0\nG3\t5.0\t6.0\t7.0\t8.0\n"
REGULATORS = HASHED + "R1\t0.5\t1.5\t2.5\t3.5\nR2\t4.0\t3.0\t2.0\t1.0\n"
DESIGN = "#sample\tCtrl\tTrt\ns1\t1\t0\ns2\t1\t0\ns3\t0\t1\ns4\t0\t1\n"
ASSOCIATIONS = "Target\tRegulator\nG1\tR1\nG1\tR2\nG2\tR1\nG2\tR2\nG3\tR1\nG3\tR2\n"


def binary():
    configured = os.environ.get("PAINTOMICS_MORE_RS", "").strip()
    for candidate in (configured, BUNDLED, shutil.which("more-rs") or ""):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


class TheConventionIsRealTest(unittest.TestCase):
    """Engine-independent: the shipped data actually uses `#` headers.

    If this stops being true the behavioural test below is arguing with nobody.
    """

    def test_the_convention_is_actually_used_by_the_shipped_data(self):
        hashed = []
        for base, _dirs, files in os.walk(DATASETS):
            for name in files:
                if not name.endswith(".tab"):
                    continue
                path = os.path.join(base, name)
                with io.open(path, encoding="utf-8", errors="replace") as handle:
                    if handle.read(1) == "#":
                        hashed.append(os.path.relpath(path, DATASETS))
        self.assertGreater(len(hashed), 5,
                           "expected the `#` header convention in the shipped "
                           "datasets; found %d" % len(hashed))


@unittest.skipIf(not binary(), "no more-rs binary (set PAINTOMICS_MORE_RS)")
class ThePortReadsItAsAHeaderTest(unittest.TestCase):
    """Behavioural: every feature and every sample name survives the read.

    Run through the real binary rather than a unit of it, because the failure
    this guards was never in one function: the header was already lost by the
    time anything downstream could notice, and what the user saw was a
    complaint about sample names.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="paintomics-hash-header-")
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, body):
        path = os.path.join(self.dir, name)
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        return path

    def run_more(self):
        argv = [binary(),
                "--target_file", self.write("targets.tab", TARGETS),
                "--condition_file", self.write("design.tab", DESIGN),
                "--omic_names", "TF",
                "--data_files", self.write("regs.tab", REGULATORS),
                "--assoc_files", self.write("assoc.tab", ASSOCIATIONS),
                "--min_variation", "NA", "--method", "PLS1",
                "--output_dir", self.out, "--date_seed", "seed"]
        return subprocess.run(argv, capture_output=True, text=True, timeout=300)

    def test_a_hash_headed_matrix_keeps_every_feature_and_sample(self):
        done = self.run_more()
        self.assertEqual(done.returncode, 0,
                         "a `#gene` header must not break the run:\n%s%s"
                         % (done.stdout, done.stderr))
        self.assertIn("Loaded target data with 3 features", done.stdout,
                      "a feature was lost to the header line")

    def test_the_sample_names_come_from_the_header_row(self):
        """The symptom the user actually saw.

        With the header eaten, the sample names became "1.0,2.0,3.0,4.0" and
        nothing matched the design file, so the job died complaining about
        sample naming that was correct all along.
        """
        done = self.run_more()
        self.assertNotIn("No common sample names", done.stdout + done.stderr)
        self.assertIn("Found 4 common samples", done.stdout)

    def test_the_identifier_column_head_is_not_taken_for_a_feature(self):
        """`#gene` names the column, so it must not become a row of its own."""
        values = os.path.join(self.out, "MORE_output_TF_seed.tab")
        self.run_more()
        self.assertTrue(os.path.exists(values), "no values file was written")
        with io.open(values, encoding="utf-8") as handle:
            keys = [line.split("\t")[0] for line in handle.read().splitlines()[1:]]
        self.assertTrue(keys, "the values file is empty")
        for key in keys:
            self.assertNotIn("#gene", key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
