#!/usr/bin/env python3
"""The R scripts read every "--name=value" argument whole, "=" and quotes included.

The defect
----------
Step 2 runs generateMetaGenes.R once per omic mapped to genes and database
(an omic mapped to metabolites never reaches it), passing the omic's name
inside two arguments:

    --input_file=<job tmp dir>/RNA=seq (x=2)_matched.txt
    --output_prefix=RNA=seq (x=2)

The script split each argument on EVERY "=", so it read the input file as
"<job tmp dir>/RNA", did not find it, and Step 2 failed with "Oops..Internal
error" and a Report button for any such omic whose name holds "=". It also removed
every '"' from every argument first, so an omic named 'Prot "A"' failed the
same way. processReactomeData.R, which the installer runs, parsed its
arguments with the same line, so a KEGG data folder whose path holds "=" cut
its --root.

Both scripts now cut an argument at its first "=" only and keep the value as
given. The job passes its arguments without a shell (Popen with a list), and
the installer's shell removes its own quotes, so no caller relies on the
removal of '"'.

What this file pins
-------------------
* ParserTest runs each script's own parsing statements under R on the
  arguments the job really passes, blanks for options it leaves out included,
  and checks both scripts carry the same parser. That last check guards the
  two copies against drifting apart; it is not a test of the defect (master's
  two copies behave the same and differ only in spacing).
* MetagenesStepTest runs Step 2's metagenes phase (the real
  generateMetagenesList, which builds the argument list, and the real R
  script) for an omic named "RNA=seq (x=2)" and for one named 'Prot "A"', and
  checks every pathway gets its metagenes, with the cluster count and the
  Reactome database the job asked for.
* ReactomeRootTest runs processReactomeData.R with a --root that holds "=".

Usage:
    cd PaintomicsServer
    python -m src.tests.test_r_arguments_keep_equals
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("PAINTOMICS_KEGG_DATA", tempfile.mkdtemp(prefix="kegg-"))
os.environ.setdefault("PAINTOMICS_CLIENT_TMP", tempfile.mkdtemp(prefix="client-"))

from src.classes.JobInstances import PathwayAcquisitionJob as jobModule  # noqa: E402
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob  # noqa: E402
from src.classes.Pathway import Pathway  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TESTS_DIR)
METAGENES_R = os.path.join(SRC_DIR, "common", "bioscripts", "generateMetaGenes.R")
REACTOME_R = os.path.join(SRC_DIR, "AdminTools", "scripts", "processReactomeData.R")
SCRIPTS = (METAGENES_R, REACTOME_R)

RSCRIPT = shutil.which("Rscript")

EQUALS_NAME = "RNA=seq (x=2)"
QUOTES_NAME = 'Prot "A"'

# The arguments PathwayAcquisitionJob passes for an omic named EQUALS_NAME, in
# its order and form, with "" where it leaves --kclusters out. Three are not
# the job's, to show values are kept exactly as given and blanks add nothing:
# --sources_dir carries '"' and spaces, --cutoff is given empty, and a last "".
JOB_ARGS = [
    "--specie=mmu",
    "--input_file=/data/CLIENT_TMP/u1/tmp/job1/" + EQUALS_NAME + "_matched.txt",
    "--output_prefix=" + EQUALS_NAME,
    "--data_dir=/data/CLIENT_TMP/u1/tmp/job1",
    "--kegg_dir=/data/KEGG_DATA/",
    '--sources_dir=/app/a "quoted" dir/common/bioscripts/',
    "",
    "--database=Reactome",
    "--cutoff=",
    "",
]
PARSED = {
    "specie": "mmu",
    "input_file": "/data/CLIENT_TMP/u1/tmp/job1/" + EQUALS_NAME + "_matched.txt",
    "output_prefix": EQUALS_NAME,
    "data_dir": "/data/CLIENT_TMP/u1/tmp/job1",
    "kegg_dir": "/data/KEGG_DATA/",
    "sources_dir": '/app/a "quoted" dir/common/bioscripts/',
    "database": "Reactome",
    "cutoff": "",
}


def _r_packages_available():
    """Rscript plus every package generateMetaGenes.R loads at top level."""
    if RSCRIPT is None:
        return False
    probe = ('q(status = if (all(sapply(c("cluster","amap","mclust","factoextra"), '
             'requireNamespace, quietly = TRUE))) 0 else 1)')
    try:
        return subprocess.run([RSCRIPT, "-e", probe], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def parse_block(path):
    """The script's own parsing statements: from `parseArgs <- function` to the
    `args <- as.data.frame(` line that turns the result into `args`."""
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.startswith("parseArgs <- function")), None)
    if start is None:
        raise AssertionError("no parseArgs in %s" % path)
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("args <- as.data.frame(")), None)
    if end is None:
        raise AssertionError("parseArgs in %s no longer ends in args <- as.data.frame(" % path)
    return "\n".join(lines[start:end + 1])


def parse_in_r(path, argv):
    """Run `path`'s parsing statements on argv under R; return {name: value}."""
    directory = tempfile.mkdtemp(prefix="paintomics-r-args-")
    try:
        argv_path = os.path.join(directory, "argv.txt")
        out_path = os.path.join(directory, "parsed.tsv")
        with open(argv_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(argv) + "\n")
        script_path = os.path.join(directory, "check.R")
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join([
                'args <- readLines(%s, encoding = "UTF-8")' % _r_string(argv_path),
                parse_block(path),
                'writeLines(paste(names(args), vapply(args, as.character, ""), sep = "\\t"), %s)'
                % _r_string(out_path),
            ]) + "\n")
        completed = subprocess.run([RSCRIPT, "--vanilla", script_path], capture_output=True,
                                   text=True, timeout=120)
        if completed.returncode != 0:
            raise AssertionError("R failed on the parser of %s:\n%s" % (path, completed.stderr))
        with open(out_path, "r", encoding="utf-8") as handle:
            return dict(line.split("\t", 1) for line in handle.read().splitlines())
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _r_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ParserTest(unittest.TestCase):

    def test_both_scripts_carry_the_same_parser(self):
        """Holds without R. One fix in one script and not the other was how
        the installer's copy would drift. This guards against that drift and
        is not a test of the defect: it compares the text, and master's two
        copies, which behave the same, differ in spacing."""
        functions = [[line for line in parse_block(path).split("\n")[:-1] if line.strip()]
                     for path in SCRIPTS]
        self.assertEqual(functions[0], functions[1])

    @unittest.skipUnless(RSCRIPT, "Rscript is not installed")
    def test_a_value_keeps_every_equals_after_the_first(self):
        for path in SCRIPTS:
            with self.subTest(script=os.path.basename(path)):
                parsed = parse_in_r(path, JOB_ARGS)
                self.assertEqual(parsed.get("input_file"), PARSED["input_file"])
                self.assertEqual(parsed.get("output_prefix"), EQUALS_NAME)

    @unittest.skipUnless(RSCRIPT, "Rscript is not installed")
    def test_a_value_keeps_its_double_quotes(self):
        for path in SCRIPTS:
            with self.subTest(script=os.path.basename(path)):
                self.assertEqual(parse_in_r(path, JOB_ARGS).get("sources_dir"),
                                 PARSED["sources_dir"])

    @unittest.skipUnless(RSCRIPT, "Rscript is not installed")
    def test_every_argument_and_only_those_comes_through(self):
        """The blanks the job passes for options it leaves out add nothing, and
        an empty value stays empty instead of taking the option's name."""
        for path in SCRIPTS:
            with self.subTest(script=os.path.basename(path)):
                self.assertEqual(parse_in_r(path, JOB_ARGS), PARSED)


# ----------------------------------------------------------------------------
# Step 2's metagenes phase, end to end
# ----------------------------------------------------------------------------

# Three profiles that do not correlate, over six conditions.
SHAPES = [
    [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0],
    [3.0, 2.0, 1.0, -1.0, -2.0, -3.0],
    [2.0, -1.0, -3.0, -3.0, -1.0, 2.0],
]
PATHWAYS_PER_SHAPE = 4
GENES_PER_PATHWAY = 6
# Not 3: the dynamic choice would likely find the three planted shapes, and
# the test must tell a requested count from a guessed one.
KCLUSTERS = 4


def _build_kegg(kegg_dir):
    """gene2pathway.list and gene2pathway_reactome.list for mmu, in the real
    layouts; return (matched.txt rows, {pathwayID: source})."""
    species_dir = os.path.join(kegg_dir, "current", "mmu")
    os.makedirs(species_dir)
    kegg_rows, reactome_rows, matched, pathways = [], [], [], {}
    gene = 0
    for shape_index, profile in enumerate(SHAPES):
        for p in range(PATHWAYS_PER_SHAPE):
            number = 90001 + shape_index * PATHWAYS_PER_SHAPE + p
            kegg_id, reactome_id = "mmu%d" % number, "R-MMU-%d" % number
            pathways[kegg_id], pathways[reactome_id] = "KEGG", "Reactome"
            for _ in range(GENES_PER_PATHWAY):
                gene += 1
                gene_id = "%d" % (100000 + gene)
                kegg_rows.append("mmu:%s\tpath:%s" % (gene_id, kegg_id))
                reactome_rows.append("%s\t%s" % (gene_id, reactome_id))
                # Deterministic jitter, so the fixture is the same on every run.
                values = ["%.6f" % ((1 + p) * value + 0.01 * (((gene * 7 + c * 13) % 11) - 5))
                          for c, value in enumerate(profile)]
                # inputName | featureName | featureID | matchingDB | values | relevance flag
                matched.append("\t".join(["ENSMUSG%011d" % gene, "G%d" % gene, gene_id, "KEGG"]
                                         + values + ["1"]))
    with open(os.path.join(species_dir, "gene2pathway.list"), "w") as handle:
        handle.write("\n".join(kegg_rows) + "\n")
    with open(os.path.join(species_dir, "gene2pathway_reactome.list"), "w") as handle:
        handle.write("\n".join(reactome_rows) + "\n")
    return matched, pathways


def _run_metagenes_phase(omic_name, databases):
    """Step 2's metagenes phase for one omic; return (pathways, error or None)."""
    root = tempfile.mkdtemp(prefix="paintomics-metagenes-equals-")
    try:
        kegg_dir = os.path.join(root, "KEGG_DATA") + os.sep
        matched, sources = _build_kegg(kegg_dir)
        job = PathwayAcquisitionJob("equalsJob01", None, os.path.join(root, "CLIENT_TMP") + os.sep)
        job.setOrganism("mmu")
        job.setDatabases(list(databases))
        job.addGeneBasedInputOmic({"omicName": omic_name})
        os.makedirs(job.getOutputDir())
        os.makedirs(job.getTemporalDir())
        with zipfile.ZipFile(job.getOutputDir() + "/mapping_results_equalsJob01.zip", "w") as archive:
            archive.writestr(omic_name + "_matched.txt", "\n".join(matched) + "\n")
        for pathway_id, source in sources.items():
            pathway = Pathway(pathway_id)
            pathway.setSource(source)
            job.matchedPathways[pathway_id] = pathway
        error = None
        with mock.patch.object(jobModule, "KEGG_DATA_DIR", kegg_dir), \
                mock.patch.object(jobModule, "JobProgress", mock.MagicMock()):
            try:
                job.generateMetagenesList(SRC_DIR + os.sep, {omic_name: str(KCLUSTERS)})
            except Exception as ex:  # reported by the tests, which say which omic failed
                error = ex
        return job.matchedPathways, error
    finally:
        shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(_r_packages_available(), "Rscript or the CRAN packages generateMetaGenes.R loads are missing")
class MetagenesStepTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.equals = _run_metagenes_phase(EQUALS_NAME, ["KEGG", "Reactome"])
        cls.quotes = _run_metagenes_phase(QUOTES_NAME, ["KEGG"])

    def assertEveryPathwayHasMetagenes(self, result, omic_name, source):
        pathways, error = result
        self.assertIsNone(error, "Step 2's metagenes phase failed for the omic %r: %s"
                          % (omic_name, error))
        missing = [pid for pid, pathway in pathways.items()
                   if pathway.getSource() == source and not pathway.metagenes.get(omic_name)]
        self.assertEqual(missing, [], "%s pathways without metagenes for %r" % (source, omic_name))

    def test_an_omic_named_with_equals_gets_its_metagenes(self):
        self.assertEveryPathwayHasMetagenes(self.equals, EQUALS_NAME, "KEGG")

    def test_an_omic_named_with_double_quotes_gets_its_metagenes(self):
        self.assertEveryPathwayHasMetagenes(self.quotes, QUOTES_NAME, "KEGG")

    def test_the_database_argument_still_reaches_r(self):
        """--database=Reactome makes R read gene2pathway_reactome.list and write
        <omic>_metagenes_reactome.tab; a lost argument would leave these empty."""
        self.assertEveryPathwayHasMetagenes(self.equals, EQUALS_NAME, "Reactome")

    def test_the_requested_cluster_count_still_reaches_r(self):
        pathways, error = self.equals
        self.assertIsNone(error)
        clusters = {metagene["cluster"]
                    for pathway in pathways.values() if pathway.getSource() == "KEGG"
                    for metagene in pathway.metagenes.get(EQUALS_NAME, [])}
        self.assertEqual(clusters, {str(k) for k in range(1, KCLUSTERS + 1)})


# ----------------------------------------------------------------------------
# The installer's Reactome step
# ----------------------------------------------------------------------------

@unittest.skipUnless(RSCRIPT, "Rscript is not installed")
class ReactomeRootTest(unittest.TestCase):

    def test_a_root_holding_equals_is_read_whole(self):
        root = tempfile.mkdtemp(prefix="paintomics-reactome-equals-")
        try:
            common = os.path.join(root, "KEGG=2026", "current", "common")
            os.makedirs(common)
            row = ["ENSG00000000001", "R-HSA-1001", "GENE1 [nucleoplasm]", "R-HSA-9001",
                   "http://x", "Event", "IEA", "Homo sapiens"]
            for name in ("Ensembl2Reactome", "NCBI2Reactome", "UniProt2Reactome"):
                with open(os.path.join(common, name + "_PE_All_Levels.txt"), "w") as handle:
                    handle.write("\t".join(row) + "\n")
            completed = subprocess.run(
                [RSCRIPT, REACTOME_R, "--specie=hsa", "--root=" + common + os.sep],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            written = os.path.join(root, "KEGG=2026", "current", "hsa", "mapping", "reactome",
                                   "Ensembl2Reactome.txt")
            with open(written) as handle:
                self.assertIn("R-HSA-1001", handle.read())
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
