#!/usr/bin/env python3
"""An omic whose name holds "%" gets its metagenes and its cluster thumbnails.

The defect
----------
Step 2 runs generateMetaGenes.R once per omic mapped to genes and database.
The script names its pictures after the omic: the cluster thumbnails
"<omic>_cluster_<k>[_<database>].png", which the Step 3 page asks for by that
exact name (PA_Step3Views.js), and the elbow plot "<omic>_elbow.png". It drew
them with png() and ggsave(), and both read the name they are given as a
pattern: "%d" stands for the page number and any other "%" is refused. So

* an omic named "RNA 100%" stopped the script with "invalid 'filename'", and
  Step 2 failed with "Oops..Internal error!" and a Report button;
* an omic named "x%dy" passed Step 2, but its thumbnails were saved as
  "x1y_cluster_1.png", and Step 3 showed broken images.

The script now draws each picture into a file R names itself and copies it to
its real name. It also loads its packages quietly: their start-up text (mclust's
version and citation lines, factoextra's "Welcome! Want to learn more?") went
into the output Step 2 shows when the script fails.

What this file pins
-------------------
* PictureNamesTest runs Step 2's metagenes phase (the real
  generateMetagenesList and the real R script) for "RNA 100%", "x%dy" and a
  plain name, over KEGG and Reactome, with the cluster count left to the
  script (so the elbow plot is drawn too), then once more with a cluster count
  asked for, as the Step 3 slider does. It checks every pathway gets its
  metagenes and that the output folder holds the thumbnail the Step 3 page asks
  for, under exactly the name it asks for, for every cluster the pathways carry,
  and no picture under any other name. The plain name passes on master too;
  it is the control.
* StartupTextTest makes the script fail after its packages are loaded (a
  folder sits where the metagenes table goes) and checks the error Step 2
  raises names the real failure and none of the packages' start-up text.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_omic_name_with_percent
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

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RSCRIPT = shutil.which("Rscript")

PERCENT_NAME = "RNA 100%"
PATTERN_NAME = "x%dy"      # a page-number pattern: png() turned it into "x1y"
PLAIN_NAME = "RNA plain"   # the control
JOB_ID = "percentJob01"

# Three profiles that do not correlate, over six conditions.
SHAPES = [
    [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0],
    [3.0, 2.0, 1.0, -1.0, -2.0, -3.0],
    [2.0, -1.0, -3.0, -3.0, -1.0, 2.0],
]
PATHWAYS_PER_SHAPE = 4
GENES_PER_PATHWAY = 6
SLIDER_CLUSTERS = 4  # what the Step 3 slider asks for in the second run

# What the start-up text of the packages the script loads begins with.
STARTUP_TEXT = ("Welcome! Want to learn more?", "citation(", "Package 'mclust' version",
                "Loading required package")


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


class _Run(object):
    """One job with one omic, its fixture KEGG folder and its own folders."""

    def __init__(self, omic_name, databases):
        self.omic_name = omic_name
        self.root = tempfile.mkdtemp(prefix="paintomics-metagenes-percent-")
        self.kegg_dir = os.path.join(self.root, "KEGG_DATA") + os.sep
        matched, sources = _build_kegg(self.kegg_dir)
        self.job = PathwayAcquisitionJob(JOB_ID, None, os.path.join(self.root, "CLIENT_TMP") + os.sep)
        self.job.setOrganism("mmu")
        self.job.setDatabases(list(databases))
        self.job.addGeneBasedInputOmic({"omicName": omic_name})
        os.makedirs(self.job.getOutputDir())
        os.makedirs(self.job.getTemporalDir())
        with zipfile.ZipFile(self.job.getOutputDir() + "/mapping_results_%s.zip" % JOB_ID, "w") as archive:
            archive.writestr(omic_name + "_matched.txt", "\n".join(matched) + "\n")
        for pathway_id, source in sources.items():
            pathway = Pathway(pathway_id)
            pathway.setSource(source)
            self.job.matchedPathways[pathway_id] = pathway

    def metagenes(self, cluster_number, omic_list=None, databases=None):
        """Step 2's metagenes phase (or, with omic_list and databases, the
        Step 3 slider's); return the exception it raised, or None."""
        with mock.patch.object(jobModule, "KEGG_DATA_DIR", self.kegg_dir), \
                mock.patch.object(jobModule, "JobProgress", mock.MagicMock()):
            try:
                self.job.generateMetagenesList(SRC_DIR + os.sep, cluster_number, omic_list, databases)
            except Exception as ex:  # reported by the tests, which name the omic
                return ex
        return None

    def clusters(self, source):
        """The cluster numbers the pathways of `source` carry for the omic,
        which are the clusters the Step 3 page draws a thumbnail for."""
        return sorted({metagene["cluster"]
                       for pathway in self.job.matchedPathways.values()
                       if pathway.getSource() == source
                       for metagene in pathway.metagenes.get(self.omic_name, [])})

    def pictures(self):
        return sorted(name for name in os.listdir(self.job.getOutputDir()) if name.endswith(".png"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def thumbnail_name(omic_name, cluster, database):
    """The file PA_Step3Views.js asks /get_cluster_image for: the omic's name
    (sent encoded, so the server sees it as it is), "_cluster_", the cluster,
    "_<database>" in lower case unless it is KEGG, ".png"."""
    return omic_name + "_cluster_" + str(cluster) + \
        ("_" + database.lower() if database != "KEGG" else "") + ".png"


@unittest.skipUnless(_r_packages_available(), "Rscript or the CRAN packages generateMetaGenes.R loads are missing")
class PictureNamesTest(unittest.TestCase):

    DATABASES = ("KEGG", "Reactome")

    @classmethod
    def setUpClass(cls):
        cls.runs, cls.errors, cls.found, cls.slider = {}, {}, {}, {}
        for name in (PERCENT_NAME, PATTERN_NAME, PLAIN_NAME):
            run = _Run(name, cls.DATABASES)
            try:
                # "dynamic": the script picks the count, and draws the elbow plot.
                cls.errors[name] = run.metagenes({})
                cls.found[name] = {
                    "clusters": {db: run.clusters(db) for db in cls.DATABASES},
                    "pictures": run.pictures(),
                }
                # The Step 3 slider: one omic, one database, a count asked for.
                cls.slider[name] = (run.metagenes({name: str(SLIDER_CLUSTERS)}, [name], ["KEGG"]),
                                    run.clusters("KEGG"), run.pictures())
            finally:
                run.close()

    def check_name(self, name):
        error = self.errors[name]
        self.assertIsNone(error, "Step 2's metagenes phase failed for the omic %r: %s" % (name, error))
        clusters, pictures = self.found[name]["clusters"], self.found[name]["pictures"]
        expected = set()
        for database in self.DATABASES:
            self.assertTrue(clusters[database], "no %s pathway got metagenes for %r" % (database, name))
            expected.update(thumbnail_name(name, k, database) for k in clusters[database])
        missing = sorted(expected - set(pictures))
        self.assertEqual(missing, [], "thumbnails the Step 3 page asks for and does not find, for %r" % name)
        # The script chose the cluster count with k-means, so it drew the elbow
        # plot; it must be under the omic's name too.
        stray = sorted(set(pictures) - expected - {name + "_elbow.png"})
        self.assertEqual(stray, [], "pictures saved under a name nothing asks for, for %r" % name)
        self.assertIn(name + "_elbow.png", pictures)

        error, slider_clusters, slider_pictures = self.slider[name]
        self.assertIsNone(error, "the Step 3 slider failed for the omic %r: %s" % (name, error))
        self.assertEqual(slider_clusters, [str(k) for k in range(1, SLIDER_CLUSTERS + 1)])
        for k in slider_clusters:
            self.assertIn(thumbnail_name(name, k, "KEGG"), slider_pictures)

    def test_an_omic_named_with_a_percent_sign(self):
        self.check_name(PERCENT_NAME)

    def test_an_omic_named_with_a_page_number_pattern(self):
        self.check_name(PATTERN_NAME)

    def test_an_omic_with_a_plain_name(self):
        self.check_name(PLAIN_NAME)


@unittest.skipUnless(_r_packages_available(), "Rscript or the CRAN packages generateMetaGenes.R loads are missing")
class StartupTextTest(unittest.TestCase):

    def test_a_failure_shows_the_error_and_no_package_start_up_text(self):
        run = _Run(PLAIN_NAME, ["KEGG"])
        try:
            # write.table cannot write the metagenes table over a folder; that
            # happens at the very end, after every package has loaded.
            os.makedirs(os.path.join(run.job.getTemporalDir(), PLAIN_NAME + "_metagenes.tab"))
            error = run.metagenes({})
        finally:
            run.close()
        self.assertIsNotNone(error, "the script was meant to fail")
        message = str(error)
        self.assertIn("Metagenes generation failed", message)
        self.assertIn("Error", message)
        shown = [text for text in STARTUP_TEXT if text in message]
        self.assertEqual(shown, [], "package start-up text in the error Step 2 raises:\n" + message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
