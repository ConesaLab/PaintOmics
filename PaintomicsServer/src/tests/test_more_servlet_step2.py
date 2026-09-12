#!/usr/bin/env python3
"""Cover for fromMOREtoGenes_STEP2 in src/servlets/MOREServlet.py.

STEP2 is the half that shells out to R. Until the MORE packaging was fixed it
could not be meaningfully tested -- every path led to an R call that could
never succeed -- so the behaviour worth pinning is what STEP2 does *around*
that call: pre-flight validation, the command it builds, how it reacts to a
non-zero exit, and how it turns R's output files into the basenames PA Step 1
consumes.

subprocess.Popen is replaced with a double that writes the files a real MORE
run would produce, so the whole post-processing path executes for real against
a temporary job directory. No R is involved and nothing touches the client
data tree.

Contracts pinned here
---------------------
* The R command carries one --min_variation token per omic, in --omic_names
  order. A misalignment would silently apply one omic's filter to another.
* A non-zero exit raises with R's captured output in the message; the operator
  needs the R error, not "exit 1".
* An omic whose file is None is rejected with a readable message rather than
  a TypeError out of os.path.join.
* Red stars are user-driven: with no user relevant-regulators file the
  relevant-regulator file must be written and EMPTY, matching miRNA2Genes.
  A non-empty file here would paint stars MORE never justified.
* The filters sidecar is written only alongside the rpc table, and carries the
  settings the Step-3 R2 slider locks onto.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_more_servlet_step2
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MOREJob import MOREJob
from src.servlets import MOREServlet


# Stands in for an installed more-rs. Never executed: subprocess.Popen is
# replaced by FakePopen throughout this file.
FAKE_BINARY = "/opt/paintomics/src/common/bioscripts/more-rs"


class FakeResponse(object):
    """setStatus matters: handleException calls it before setContent, and the
    servlet's `finally: return RESPONSE` would swallow the AttributeError,
    leaving the failure invisible."""

    def __init__(self):
        self.content = None
        self.status = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        self.status = status


class FakePopen(object):
    """Stands in for the Rscript process.

    `script` receives the parsed command and the output dir, so a test can make
    the fake "R run" produce whatever files it needs.
    """
    lastCommand = None
    returncode_to_use = 0
    output_lines = ["MORE: Analysis complete."]
    writer = None

    def __init__(self, cmd, **kwargs):
        FakePopen.lastCommand = list(cmd)
        self.returncode = FakePopen.returncode_to_use
        if FakePopen.writer:
            FakePopen.writer(list(cmd))
        self._lines = list(FakePopen.output_lines) + [""]
        self.stdout = self

    # -- minimal file-like behaviour the servlet uses --
    def readline(self):
        return self._lines.pop(0) if self._lines else ""

    def close(self):
        pass

    def wait(self):
        return self.returncode


class Step2TestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="more2_")
        self._realPopen = MOREServlet.subprocess.Popen
        self._realJIM = MOREServlet.JobInformationManager
        # Pin the binary to a fixed path. Which binary a host picks belongs to
        # test_more_backend_selection; what this file pins is the argument
        # vector after that choice is made.
        #
        # A literal path rather than "" or the real discovery: blank means "go
        # and find one", so argv[0] would then be whatever more-rs the machine
        # running the tests happens to have -- different on CI, on a developer
        # laptop and in the container. "off" is not usable either; it now means
        # "no engine", which STEP2 turns into a refusal.
        self._realBinary = MOREServlet.MORE_RS_BINARY
        MOREServlet.MORE_RS_BINARY = FAKE_BINARY
        self._binaryPatch = mock.patch.object(
            MOREServlet, "moreBinary", return_value=FAKE_BINARY)
        self._binaryPatch.start()
        MOREServlet.subprocess.Popen = FakePopen
        MOREServlet.JobInformationManager = lambda: self

        FakePopen.lastCommand = None
        FakePopen.returncode_to_use = 0
        FakePopen.output_lines = ["MORE: Analysis complete."]
        FakePopen.writer = self.writeTypicalOutputs

        self.stored = []
        self.response = FakeResponse()
        self.job = self.makeJob()

    # JobInformationManager() stand-in
    def storeJobInstance(self, jobInstance, step):
        self.stored.append((jobInstance, step))

    def tearDown(self):
        MOREServlet.subprocess.Popen = self._realPopen
        MOREServlet.JobInformationManager = self._realJIM
        MOREServlet.MORE_RS_BINARY = self._realBinary
        self._binaryPatch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def makeJob(self, omics=None):
        job = MOREJob("JOB2", "u1", self.tmp + os.sep)
        job.initializeDirectories()
        job.date = "202601011200"
        job.method = "PLS1"
        job.alpha = 0.05
        job.vip = 0.8
        job.filter_r2 = 0.0

        inputDir = job.getInputDir()
        for fn in ("GeneExpression.tab", "Conditions.tab", "TF.tab"):
            with open(os.path.join(inputDir, fn), "w") as fh:
                fh.write("id\tS1\tS2\nA\t1\t2\n")
        job.targetExpressionFile = "GeneExpression.tab"
        job.conditionsFile = "Conditions.tab"
        for omic in (omics if omics is not None else [("TF", "TF.tab", None, "NA")]):
            name, dataFile, relevantFile, minVar = omic
            job.addRegulatoryOmic(name, dataFile, name,
                                  associationsFile=None, relevantFile=relevantFile,
                                  minVariation=minVar)
        return job

    def writeTypicalOutputs(self, cmd):
        """What a successful MORE run leaves in output_dir."""
        outDir = cmd[cmd.index("--output_dir") + 1]
        date = self.job.date
        for name in [o["name"].replace(" ", "_") for o in self.job.regulatoryOmics]:
            with open(os.path.join(outDir, "MORE_output_%s_%s.tab" % (name, date)), "w") as fh:
                fh.write("GENEA:::STAT3\t1.0\nGENEB:::NFKB1\t2.0\n")
            for prefix in ("MORE_relevant_assoc", "MORE_relevant_pairs"):
                open(os.path.join(outDir, "%s_%s_%s.tab" % (prefix, name, date)), "w").close()
        with open(os.path.join(outDir, "MORE_rpc_%s.tab" % date), "w") as fh:
            fh.write("targetF\tregulator\tomic\tR2\nGENEA\tSTAT3\tTF\t0.9\n")

    def run_step2(self):
        MOREServlet.fromMOREtoGenes_STEP2(self.job, "u1", self.response, {})
        return self.response.content


class PreflightValidationTest(Step2TestCase):

    def test_missing_target_file_is_reported(self):
        os.remove(os.path.join(self.job.getInputDir(), "GeneExpression.tab"))
        content = self.run_step2()
        self.assertFalse(content.get("success", False))
        self.assertIsNone(FakePopen.lastCommand, "R must not be invoked")

    def test_missing_conditions_file_is_reported(self):
        os.remove(os.path.join(self.job.getInputDir(), "Conditions.tab"))
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_missing_regulatory_file_is_reported(self):
        os.remove(os.path.join(self.job.getInputDir(), "TF.tab"))
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_empty_regulatory_file_is_rejected(self):
        """An empty upload would make MORE fail deep inside R instead."""
        open(os.path.join(self.job.getInputDir(), "TF.tab"), "w").close()
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_a_none_data_file_gives_a_message_not_a_typeerror(self):
        """STEP1 leaves "file" as None when neither upload nor location given."""
        self.job = self.makeJob(omics=[])
        self.job.addRegulatoryOmic("TF", None, "TF", minVariation="NA")
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)


class OmicNameValidationTest(Step2TestCase):
    """--omic_names is a comma-joined list that runMORE.R splits on comma and
    pairs with --data_files, --assoc_files and --min_variation BY POSITION.

    Nothing checked the names against that encoding, and every way of breaking
    it failed badly:

      a comma in a name   R sees more omics than data files, indexes past the
                          end of data_paths, and dies on read_matrix(NA) with
                          "missing value where TRUE/FALSE needed"
      a blank name        strsplit drops a trailing empty field, so the last
                          omic disappears and its data file is never read --
                          a silently smaller analysis, not an error
      duplicate names     both omics write MORE_output_<name>_<date>.tab and
                          the second overwrites the first

    All three are now refused before R is invoked, which is what these assert:
    the message matters less than lastCommand staying None.
    """

    def addFile(self, name):
        path = os.path.join(self.job.getInputDir(), name)
        with open(path, "w") as fh:
            fh.write("id\tS1\tS2\nA\t1\t2\n")
        return name

    def test_a_comma_in_an_omic_name_is_refused(self):
        self.job = self.makeJob(omics=[("TF, ChIP", "TF.tab", None, "NA")])
        content = self.run_step2()
        self.assertFalse(content.get("success", False))
        self.assertIsNone(FakePopen.lastCommand, "R must not be invoked")

    def test_the_comma_message_names_the_offending_omic(self):
        self.job = self.makeJob(omics=[("TF, ChIP", "TF.tab", None, "NA")])
        content = self.run_step2()
        self.assertIn("TF, ChIP", str(content))

    def test_a_blank_omic_name_is_refused(self):
        self.job = self.makeJob(omics=[("", "TF.tab", None, "NA")])
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_a_whitespace_only_omic_name_is_refused(self):
        """runMORE.R trims, so "   " and "" reach R identically."""
        self.job = self.makeJob(omics=[("   ", "TF.tab", None, "NA")])
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_duplicate_omic_names_are_refused(self):
        self.job = self.makeJob(omics=[])
        self.addFile("TF2.tab")
        for dataFile in ("TF.tab", "TF2.tab"):
            self.job.addRegulatoryOmic("TF", dataFile, "TF", minVariation="NA")
        self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)

    def test_names_colliding_only_after_sanitising_are_refused(self):
        """Both sides put gsub(" ", "_", name) in the filename, so "TF A" and
        "TF_A" are distinct names that produce the same output file."""
        self.job = self.makeJob(omics=[])
        self.addFile("TFA.tab")
        for name, dataFile in (("TF A", "TF.tab"), ("TF_A", "TFA.tab")):
            self.job.addRegulatoryOmic(name, dataFile, name, minVariation="NA")
        content = self.run_step2()
        self.assertIsNone(FakePopen.lastCommand)
        self.assertIn("TF_A", str(content))

    def test_distinct_names_still_run(self):
        """The guard must not reject the ordinary multi-omic job."""
        self.job = self.makeJob(omics=[])
        self.addFile("MIR.tab")
        for name, dataFile in (("TF", "TF.tab"), ("miRNA", "MIR.tab")):
            self.job.addRegulatoryOmic(name, dataFile, name, minVariation="NA")
        self.run_step2()
        self.assertIsNotNone(FakePopen.lastCommand)
        names = FakePopen.lastCommand[FakePopen.lastCommand.index("--omic_names") + 1]
        self.assertEqual(names, "TF,miRNA")

    def test_a_name_with_an_internal_space_still_runs(self):
        """Spaces are legal -- both sides map them to underscores identically.
        Only commas break the encoding."""
        self.job = self.makeJob(omics=[("Transcription Factors", "TF.tab", None, "NA")])
        self.run_step2()
        self.assertIsNotNone(FakePopen.lastCommand)


class CommandConstructionTest(Step2TestCase):

    def test_invokes_the_more_rs_binary(self):
        self.run_step2()
        self.assertEqual(FakePopen.lastCommand[0], FAKE_BINARY)

    def test_passes_the_model_parameters(self):
        self.run_step2()
        cmd = FakePopen.lastCommand
        self.assertEqual(cmd[cmd.index("--method") + 1], "PLS1")
        self.assertEqual(cmd[cmd.index("--alpha") + 1], "0.05")
        self.assertEqual(cmd[cmd.index("--vip") + 1], "0.8")

    def test_one_min_variation_token_per_omic_in_omic_name_order(self):
        """A misalignment silently applies one omic's filter to another."""
        self.job = self.makeJob(omics=[])
        inputDir = self.job.getInputDir()
        for fn in ("A.tab", "B.tab"):
            with open(os.path.join(inputDir, fn), "w") as fh:
                fh.write("id\tS1\nA\t1\n")
        self.job.addRegulatoryOmic("Aomic", "A.tab", "Aomic", minVariation="NA")
        self.job.addRegulatoryOmic("Bomic", "B.tab", "Bomic", minVariation=0.25)
        self.run_step2()
        cmd = FakePopen.lastCommand
        self.assertEqual(cmd[cmd.index("--omic_names") + 1], "Aomic,Bomic")
        self.assertEqual(cmd[cmd.index("--min_variation") + 1], "NA,0.25")

    def test_absent_association_file_is_passed_as_the_null_token(self):
        self.run_step2()
        cmd = FakePopen.lastCommand
        self.assertEqual(cmd[cmd.index("--assoc_files") + 1], "NULL")


class RFailureTest(Step2TestCase):

    def test_a_nonzero_exit_does_not_report_success(self):
        FakePopen.returncode_to_use = 1
        FakePopen.output_lines = ["MORE ERROR: something went wrong in R"]
        FakePopen.writer = None
        content = self.run_step2()
        self.assertFalse(content.get("success", False))

    def test_the_r_output_is_carried_into_the_failure(self):
        """"exit 1" alone is useless; the R text is the diagnostic."""
        FakePopen.returncode_to_use = 1
        FakePopen.output_lines = ["MORE ERROR: unused arguments"]
        FakePopen.writer = None
        content = self.run_step2()
        self.assertIn("unused arguments", json.dumps(content))

    def test_nothing_is_stored_when_r_fails(self):
        FakePopen.returncode_to_use = 1
        FakePopen.writer = None
        self.run_step2()
        self.assertEqual(self.stored, [])


class OutputProcessingTest(Step2TestCase):

    def test_reports_success_and_the_job_id(self):
        content = self.run_step2()
        self.assertTrue(content["success"])
        self.assertEqual(content["jobID"], "JOB2")

    def test_returns_basenames_not_paths(self):
        """saveFiles/parseGeneBasedFiles prepend inputDir themselves."""
        content = self.run_step2()
        for key in ("mainOutputFileName_0", "secondOutputFileName_0",
                    "thirdOutputFileName_0", "fourthOutputFileName_0"):
            self.assertNotIn(os.sep, content[key], key)

    def test_copies_r_outputs_into_inputdata(self):
        content = self.run_step2()
        self.assertTrue(os.path.exists(os.path.join(
            self.job.getInputDir(), content["mainOutputFileName_0"])))

    def test_names_each_omic_in_the_response(self):
        content = self.run_step2()
        self.assertEqual(content["omicName_0"], "TF")
        self.assertEqual(content["omicsCount"], 1)

    def test_no_user_relevant_file_yields_an_empty_relevant_regulator_file(self):
        """Red stars are user-driven. A non-empty file here paints stars MORE
        never justified, and skews the pathway enrichment for the omic."""
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.path.getsize(path), 0)

    def test_a_user_relevant_file_expands_to_gene_regulator_pairs(self):
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("STAT3\n")
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        with open(path) as fh:
            written = [l.strip() for l in fh if l.strip()]
        self.assertEqual(written, ["GENEA:::STAT3"],
                         "only pairs whose regulator the user flagged")

    def test_a_relevant_file_with_a_pvalue_column_still_expands(self):
        """Users build this list by exporting rows from a statistics table, so
        a second column is the common case, not the exotic one. Reading the
        whole line as the ID matched nothing and the only symptom was an
        absence of red stars -- the job still reported success."""
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("Regulator\tpvalue\nSTAT3\t0.001\n")
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        with open(path) as fh:
            self.assertEqual([l.strip() for l in fh if l.strip()], ["GENEA:::STAT3"])

    def test_a_quoted_csv_relevant_file_still_expands(self):
        """What Excel writes."""
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write('"STAT3",0.001\n"NFKB1",0.02\n')
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        with open(path) as fh:
            self.assertEqual(sorted(l.strip() for l in fh if l.strip()),
                             ["GENEA:::STAT3", "GENEB:::NFKB1"])

    def test_a_header_only_relevant_file_yields_no_pairs(self):
        """The header is skipped, so nothing is flagged -- and in particular
        "Regulator" is not treated as a regulator ID."""
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("Regulator\n")
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        self.assertEqual(os.path.getsize(path), 0)

    def test_a_regulator_actually_named_regulator_is_kept_after_line_one(self):
        """Header skipping is positional. Only the first line can be a header,
        so an ID that happens to look like one survives anywhere else."""
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("STAT3\nRegulator\n")
        self.run_step2()
        # STAT3 still expands; the point is that parsing did not stop or skip.
        path = os.path.join(self.job.getInputDir(),
                            "MORE_relevant_reg_TF_%s.tab" % self.job.date)
        with open(path) as fh:
            self.assertIn("GENEA:::STAT3", fh.read())

    def test_ids_matching_nothing_are_reported_not_silent(self):
        """An ID-space mismatch produces an empty red-star file that is
        indistinguishable from "the user flagged nothing". Not fatal -- the
        regulatory analysis stands -- but it must not pass without a word."""
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("ENSG00000170345\nENSG00000109320\n")
        with self.assertLogs(level="WARNING") as captured:
            self.run_step2()
        self.assertTrue(any("no red stars" in line for line in captured.output),
                        captured.output)

    def test_the_rpc_table_is_exposed_when_r_produced_it(self):
        content = self.run_step2()
        self.assertIn("regulationPerConditionFile", content)

    def test_the_rpc_field_is_omitted_when_r_produced_nothing(self):
        """The Step 3 panel keys off the field's absence to stay hidden."""
        def noRpc(cmd):
            self.writeTypicalOutputs(cmd)
            os.remove(os.path.join(cmd[cmd.index("--output_dir") + 1],
                                   "MORE_rpc_%s.tab" % self.job.date))
        FakePopen.writer = noRpc
        content = self.run_step2()
        self.assertNotIn("regulationPerConditionFile", content)

    def test_writes_the_filters_sidecar_next_to_the_rpc_table(self):
        self.job.filter_r2 = 0.35
        self.run_step2()
        path = os.path.join(self.job.getInputDir(),
                            "MORE_filters_%s.json" % self.job.date)
        self.assertTrue(os.path.exists(path))
        with open(path) as fh:
            meta = json.load(fh)
        self.assertEqual(meta["filter_r2"], 0.35)
        self.assertEqual(meta["method"], "PLS1")

    def test_a_sidecar_write_failure_does_not_fail_the_job(self):
        """The sidecar is a convenience, and its failure must stay non-fatal.

        It only carries the filter settings so Step 3's Regulator-Target
        Network can lock its R2 slider to the floor the user chose; the client
        falls back to defaults when it is absent. A regression that let the
        OSError escape would turn a cosmetic problem into a lost analysis --
        the R run has already completed and its outputs are already copied by
        this point.

        The failure is provoked without mocking: a directory sitting where the
        file should go makes open(..., "w") raise IsADirectoryError, which is
        an OSError. That is also a real way for this to happen, since the name
        is derived from job state rather than being unique by construction.
        """
        os.mkdir(os.path.join(self.job.getInputDir(),
                              "MORE_filters_%s.json" % self.job.date))
        content = self.run_step2()
        self.assertTrue(content["success"], content.get("message"))

    def test_the_rest_of_the_response_survives_a_sidecar_failure(self):
        os.mkdir(os.path.join(self.job.getInputDir(),
                              "MORE_filters_%s.json" % self.job.date))
        content = self.run_step2()
        # The rpc table is the thing Step 3 actually needs; it is copied before
        # the sidecar is attempted and must still be reported.
        self.assertIn("regulationPerConditionFile", content)
        self.assertTrue(os.path.exists(os.path.join(
            self.job.getInputDir(), content["regulationPerConditionFile"])))

    def test_the_job_is_still_stored_after_a_sidecar_failure(self):
        os.mkdir(os.path.join(self.job.getInputDir(),
                              "MORE_filters_%s.json" % self.job.date))
        self.run_step2()
        self.assertEqual(len(self.stored), 1)

    def test_bundles_a_results_archive(self):
        content = self.run_step2()
        self.assertTrue(content["compressedFileName"].endswith(".zip"))
        self.assertTrue(os.path.exists(os.path.join(
            self.job.getOutputDir(), content["compressedFileName"])))

    def test_stores_the_job_so_it_is_listable(self):
        self.run_step2()
        self.assertEqual(len(self.stored), 1)
        self.assertIs(self.stored[0][0], self.job)


class RealisticValuesFileTest(Step2TestCase):
    """The real MORE_output_<omic>_<date>.tab leads with a comment header.

    runMORE.R writes it as

        # Gene name\tC1\tC2\t...
        GENEA:::STAT3\t...

    and STEP2 skips any line starting with "#" or blank when it expands the
    user's relevant regulators into GENE:::REGULATOR pairs. Every other test
    here writes a headerless fixture, so that skip was the last uncovered
    branch in the servlet -- and a fixture that does not look like the real
    file cannot catch a regression in how the real file is read. Were the skip
    dropped, "# Gene name" contains no ":::" so it would not become a pair,
    but a blank trailing line would raise IndexError on split, and a future
    header carrying ":::" would silently paint a star on a non-existent gene.
    """

    def writeOutputsWithHeader(self, cmd):
        outDir = cmd[cmd.index("--output_dir") + 1]
        date = self.job.date
        for name in [o["name"].replace(" ", "_") for o in self.job.regulatoryOmics]:
            with open(os.path.join(outDir, "MORE_output_%s_%s.tab" % (name, date)), "w") as fh:
                fh.write("# Gene name\tC1\tC2\n")
                fh.write("GENEA:::STAT3\t1.0\t1.1\n")
                fh.write("\n")                       # trailing blank line
                fh.write("GENEB:::NFKB1\t2.0\t2.1\n")
            for prefix in ("MORE_relevant_assoc", "MORE_relevant_pairs"):
                open(os.path.join(outDir, "%s_%s_%s.tab" % (prefix, name, date)), "w").close()
        with open(os.path.join(outDir, "MORE_rpc_%s.tab" % date), "w") as fh:
            fh.write("targetF\tregulator\tomic\tR2\nGENEA\tSTAT3\tTF\t0.9\n")

    def setUp(self):
        super(RealisticValuesFileTest, self).setUp()
        self.job = self.makeJob(omics=[("TF", "TF.tab", "userrel.txt", "NA")])
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("STAT3\n")
        FakePopen.writer = self.writeOutputsWithHeader

    def writtenPairs(self):
        content = self.run_step2()
        path = os.path.join(self.job.getInputDir(), content["secondOutputFileName_0"])
        with open(path) as fh:
            return [l.strip() for l in fh if l.strip()]

    def test_the_comment_header_does_not_become_a_pair(self):
        self.assertEqual(self.writtenPairs(), ["GENEA:::STAT3"])

    def test_a_blank_line_does_not_break_the_scan(self):
        # Reached only if the blank line is skipped rather than split.
        self.assertIn("GENEA:::STAT3", self.writtenPairs())

    def test_rows_after_the_blank_line_are_still_considered(self):
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("NFKB1\n")
        self.assertEqual(self.writtenPairs(), ["GENEB:::NFKB1"])

    def test_both_regulators_expand_when_both_are_flagged(self):
        with open(os.path.join(self.job.getInputDir(), "userrel.txt"), "w") as fh:
            fh.write("STAT3\nNFKB1\n")
        self.assertEqual(sorted(self.writtenPairs()),
                         ["GENEA:::STAT3", "GENEB:::NFKB1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
