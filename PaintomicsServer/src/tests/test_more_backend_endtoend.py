#!/usr/bin/env python3
"""Drive fromMOREtoGenes_STEP2 through the real `more-rs` binary.

test_more_backend_selection pins *which* backend gets chosen; this pins that
the chosen one actually works. subprocess.Popen is NOT replaced here -- the
servlet builds its argument vector, launches the binary, and post-processes
whatever lands in output_dir, against the bundled 06-regulatory-more dataset.

The contract that matters, and the one a real run got wrong: the values and
association files are an *unfiltered* snapshot of the input. Every
(target, regulator) pair from the association file belongs there, including
pairs whose regulator MORE dropped for low variation and never modelled --
significance only drives the yellow-star overlay. The port used to build them
from its modelling matrix instead, silently losing 94 of 750 TF pairs.

Skipped unless PAINTOMICS_MORE_RS points at an executable, which is how the
server itself decides. To run it:

    cd PaintomicsServer
    PAINTOMICS_MORE_RS=/path/to/more-rs \\
        python -m src.tests.test_more_backend_endtoend
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MOREJob import MOREJob
from src.servlets import MOREServlet

DATA = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "examplefiles", "datasets",
    "06-regulatory-more", "data"))

# The same binary a real PLS1 job would get. Reading only PAINTOMICS_MORE_RS
# used to mean this whole file skipped itself on every machine that had not
# exported it -- reporting `OK (skipped=5)`, which a suite sweep counts as a
# pass, so a green run said nothing whatever about the Rust backend. Now that
# the port is the default for PLS1, the default is what has to be exercised:
# fall through to the servlet's own discovery, so the tests run wherever a job
# would actually use it, and skip only where a job would fall back to R.
BINARY = os.environ.get("PAINTOMICS_MORE_RS", "") or MOREServlet._discoverMoreRs()

# Regulators the automatic threshold drops from the fit on this dataset. Their
# pairs must survive into the values file all the same.
DROPPED_TFS = ("TF005", "TF007", "TF009", "TF017", "TF039")

OMICS = [
    ("Transcription factor", "transcription_factor_regulators.tab",
     "transcription_factor_associations.tab"),
    ("miRNA-seq", "mirna_regulators.tab", "mirna_associations.tab"),
]


class FakeResponse(object):
    def __init__(self):
        self.content = None
        self.status = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        self.status = status


@unittest.skipUnless(
    BINARY and os.path.isfile(BINARY) and os.access(BINARY, os.X_OK),
    "no more-rs binary is configured or discoverable, so PLS1 runs on R here")
class MoreRsEndToEndTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="more_rs_e2e_")
        self._realJIM = MOREServlet.JobInformationManager
        self._realBinary = MOREServlet.MORE_RS_BINARY
        MOREServlet.JobInformationManager = lambda: self
        MOREServlet.MORE_RS_BINARY = BINARY

        self.job = MOREJob("JOBRS", "u1", self.tmp + os.sep)
        self.job.initializeDirectories()
        self.job.date = "202601011200"
        self.job.method = "PLS1"
        self.job.alpha = 0.05
        self.job.vip = 0.8
        self.job.filter_r2 = 0.0

        inputDir = self.job.getInputDir()
        shutil.copy2(os.path.join(DATA, "gene_expression_targets.tab"), inputDir)
        shutil.copy2(os.path.join(DATA, "experimental_design.tab"), inputDir)
        self.job.targetExpressionFile = "gene_expression_targets.tab"
        self.job.conditionsFile = "experimental_design.tab"

        for name, dataFile, assocFile in OMICS:
            shutil.copy2(os.path.join(DATA, dataFile), inputDir)
            shutil.copy2(os.path.join(DATA, assocFile), inputDir)
            self.job.addRegulatoryOmic(name, dataFile, name,
                                       associationsFile=assocFile,
                                       relevantFile=None, minVariation="NA")

        self.response = FakeResponse()

    # JobInformationManager() stand-in
    def storeJobInstance(self, jobInstance, step):
        pass

    def tearDown(self):
        MOREServlet.JobInformationManager = self._realJIM
        MOREServlet.MORE_RS_BINARY = self._realBinary
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runStep2(self):
        MOREServlet.fromMOREtoGenes_STEP2(self.job, "u1", self.response, {})
        self.assertIsNotNone(self.response.content, "STEP2 produced no response")
        self.assertTrue(
            self.response.content.get("success"),
            "STEP2 failed: %r" % (self.response.content,))
        return self.job.getOutputDir()

    def readValues(self, outDir, safeName):
        path = os.path.join(
            outDir, "MORE_output_%s_%s.tab" % (safeName, self.job.date))
        with open(path) as fh:
            lines = [l.rstrip("\n") for l in fh if l.strip()]
        return lines[0], lines[1:]

    def test_the_run_succeeds_and_writes_every_expected_file(self):
        outDir = self.runStep2()
        expected = ["MORE_rpc_%s.tab" % self.job.date]
        for name, _, _ in OMICS:
            safe = name.replace(" ", "_")
            for prefix in ("MORE_output", "MORE_relevant_assoc",
                           "MORE_relevant_pairs"):
                expected.append("%s_%s_%s.tab" % (prefix, safe, self.job.date))
        for fn in expected:
            self.assertTrue(os.path.exists(os.path.join(outDir, fn)),
                            "backend did not write %s" % fn)

    def test_the_values_file_carries_every_input_pair(self):
        """750 associations in, 750 rows out -- for both omics."""
        outDir = self.runStep2()
        for name, _, assocFile in OMICS:
            with open(os.path.join(self.job.getInputDir(), assocFile)) as fh:
                inputPairs = sum(1 for l in fh if l.strip()) - 1  # minus header
            _, rows = self.readValues(outDir, name.replace(" ", "_"))
            self.assertEqual(
                len(rows), inputPairs,
                "%s: %d association pairs in, %d values rows out"
                % (name, inputPairs, len(rows)))

    def test_a_regulator_dropped_for_low_variation_keeps_its_pairs(self):
        outDir = self.runStep2()
        _, rows = self.readValues(outDir, "Transcription_factor")
        present = {r.split("\t")[0].split(":::")[1] for r in rows}
        for tf in DROPPED_TFS:
            self.assertIn(
                tf, present,
                "%s was filtered out of the fit, but its GENE:::REGULATOR "
                "rows still belong in the values file" % tf)

    def test_the_yellow_star_file_stays_significance_filtered(self):
        """The unfiltered snapshot must not leak into the star overlay."""
        outDir = self.runStep2()
        path = os.path.join(
            outDir,
            "MORE_relevant_pairs_Transcription_factor_%s.tab" % self.job.date)
        with open(path) as fh:
            starred = fh.read()
        for tf in DROPPED_TFS:
            self.assertNotIn(
                tf, starred,
                "%s was never modelled, so it cannot be significant" % tf)

    def test_the_values_row_reproduces_the_raw_regulator_values(self):
        """PA Step 1 plots these numbers; they are the input, not a model output."""
        outDir = self.runStep2()
        with open(os.path.join(self.job.getInputDir(),
                               "transcription_factor_regulators.tab")) as fh:
            fh.readline()
            raw = {}
            for line in fh:
                fields = line.rstrip("\n").split("\t")
                raw[fields[0]] = [float(v) for v in fields[1:]]

        _, rows = self.readValues(outDir, "Transcription_factor")
        checked = 0
        for row in rows:
            key, *values = row.split("\t")
            regulator = key.split(":::")[1]
            if regulator not in raw:
                continue
            self.assertEqual([float(v) for v in values], raw[regulator],
                             "values row for %s does not match the input" % key)
            checked += 1
        self.assertGreater(checked, 0, "no rows were checked")


class MoreRsMlrEndToEndTest(MoreRsEndToEndTest):
    """The same job on the MLR path, which reaches the port only by name.

    Subclassed so every assertion in the PLS1 case is re-run against MLR: the
    output contract (`MORE_output_*` carrying every input pair, unfiltered, and
    the star file staying significance-filtered) is method-independent, and it
    was method-independent code that broke last time.

    MLR used to reach the port only when a caller named it: with `auto`, or
    with no engine at all, it stayed on R so that stored jobs and older clients
    kept the numbers they had already seen. There is no R to stay on now, so
    that asymmetry is gone and `engine` is set here only because the job object
    carries the field.
    """

    def setUp(self):
        super(MoreRsMlrEndToEndTest, self).setUp()
        self.job.method = "MLR"
        self.job.engine = "rust"

    def test_the_backend_actually_chosen_is_the_port(self):
        """Otherwise this whole class could be silently exercising nothing."""
        self.assertEqual(MOREServlet.moreBinary(BINARY), BINARY)

    def test_mlr_reaches_the_port_however_the_engine_is_named(self):
        """Every spelling a stored job or an old client can send, including
        the literal "r" that names an engine this server no longer has."""
        for engine in (None, "", "auto", "rust", "r"):
            self.assertEqual(MOREServlet.engineIdFor("MLR", engine), "rust-mlr",
                             "MLR with engine=%r must resolve to the port"
                             % (engine,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
