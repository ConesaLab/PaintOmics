#!/usr/bin/env python3
"""Moving the Step 3 cluster slider for one omic leaves every other picture alone.

The defect
----------
The Step 3 slider ("Generate N clusters") reruns generateMetaGenes.R for ONE
omic over ONE database: pathwayAcquisitionMetagenes_PART2 calls
generateMetagenesList with [omic] and [database]. generateMetagenesList then
removed every *.png in the job's output folder before moving the new pictures
in. So moving the slider for omic A deleted

* the cluster thumbnails of every other omic, in every database, and of A in
  the other databases, which Step 3 then showed as broken images;
* the elbow plot of every omic;
* any pathway picture exported into that folder (paintomics_<name>_<job>.png,
  which /pa_save_image writes there).

It now removes only the old thumbnails of the omic and database it recomputed
(matched by the exact names the script writes, the omic's name compared as
text), and a Step 2 run, which recomputes every omic and database, still
clears the old ones of all of them.

What this file pins
-------------------
* RealScriptTest runs the real generateMetagenesList and the real R script on
  a job with two omics, "RNA 100%" (a space and a "%") and "Prot B", over KEGG
  and Reactome: Step 2's metagenes phase, an exported pathway picture in the
  folder, the slider for "RNA 100%" on KEGG, then Step 2 again with a cluster
  count for each omic, as after "Go back". It checks every picture of the other
  omic and database is still there byte for byte after the slider, the
  slider's omic has exactly the thumbnails of its new clusters, and Step 2 again
  leaves exactly the pictures of its own run. On master the slider removes
  them all.
* FakeScriptTest puts a stand-in for the R script under the same job code (no
  R needed) to pin the naming rules: omics named "RNA [1]" and "RNA 1" (as a
  glob, the first name matches the second's files), "A" and "A_cluster_1"; a
  database whose script stopped on data too small to cluster keeps its
  pictures; the elbow plot, which has no database in its name, goes only when
  the call recomputes the omic for every database of the job.
* IsClusterThumbnailTest checks the name rule on its own.

On master 11 of the 12 tests fail; the check that Step 2 draws every
thumbnail Step 3 asks for is the control.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_cluster_slider_keeps_other_pictures
"""
import hashlib
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

JOB_ID = "sliderJob01"
EXPORT = "paintomics_mmu90001_" + JOB_ID + ".png"  # what /pa_save_image writes

# Three profiles that do not correlate, over six conditions.
SHAPES = [
    [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0],
    [3.0, 2.0, 1.0, -1.0, -2.0, -3.0],
    [2.0, -1.0, -3.0, -3.0, -1.0, 2.0],
]
PATHWAYS_PER_SHAPE = 4
GENES_PER_PATHWAY = 6


def thumbnail_name(omic_name, cluster, database):
    """The file PA_Step3Views.js asks /get_cluster_image for: the omic's name,
    "_cluster_", the cluster, "_<database>" in lower case unless it is KEGG,
    ".png"."""
    return omic_name + "_cluster_" + str(cluster) + \
        ("_" + database.lower() if database != "KEGG" else "") + ".png"


def thumbnails_of(names, omic_name, database):
    """The names among `names` that are thumbnails of this omic and database,
    for any cluster the slider can ask for (1 to 20). Written here from the
    names above, not with the job code's own rule, which is what is tested."""
    possible = {thumbnail_name(omic_name, k, database) for k in range(1, 21)}
    return sorted(name for name in names if name in possible)


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


def _build_kegg(kegg_dir, omic_names):
    """gene2pathway.list and gene2pathway_reactome.list for mmu, in the real
    layouts; return ({omic: matched.txt rows}, {pathwayID: source}). Every omic
    measures the same genes, each with its own values."""
    species_dir = os.path.join(kegg_dir, "current", "mmu")
    os.makedirs(species_dir)
    kegg_rows, reactome_rows, pathways = [], [], {}
    matched = {name: [] for name in omic_names}
    gene = 0
    for shape_index in range(len(SHAPES)):
        for p in range(PATHWAYS_PER_SHAPE):
            number = 90001 + shape_index * PATHWAYS_PER_SHAPE + p
            kegg_id, reactome_id = "mmu%d" % number, "R-MMU-%d" % number
            pathways[kegg_id], pathways[reactome_id] = "KEGG", "Reactome"
            for _ in range(GENES_PER_PATHWAY):
                gene += 1
                gene_id = "%d" % (100000 + gene)
                kegg_rows.append("mmu:%s\tpath:%s" % (gene_id, kegg_id))
                reactome_rows.append("%s\t%s" % (gene_id, reactome_id))
                for omic_index, name in enumerate(omic_names):
                    # Each omic sees the shapes in another order; deterministic
                    # jitter, so the fixture is the same on every run.
                    profile = SHAPES[(shape_index + omic_index) % len(SHAPES)]
                    values = ["%.6f" % ((1 + p) * value + 0.01 * (((gene * 7 + c * 13 + omic_index) % 11) - 5))
                              for c, value in enumerate(profile)]
                    # inputName | featureName | featureID | matchingDB | values | relevance flag
                    matched[name].append("\t".join(["ENSMUSG%011d" % gene, "G%d" % gene, gene_id, "KEGG"]
                                                   + values + ["1"]))
    with open(os.path.join(species_dir, "gene2pathway.list"), "w") as handle:
        handle.write("\n".join(kegg_rows) + "\n")
    with open(os.path.join(species_dir, "gene2pathway_reactome.list"), "w") as handle:
        handle.write("\n".join(reactome_rows) + "\n")
    return matched, pathways


class _Job(object):
    """One job with its omics, its fixture KEGG folder and its own folders."""

    def __init__(self, omic_names, databases):
        self.omic_names = list(omic_names)
        self.databases = list(databases)
        self.root = tempfile.mkdtemp(prefix="paintomics-cluster-slider-")
        self.kegg_dir = os.path.join(self.root, "KEGG_DATA") + os.sep
        matched, sources = _build_kegg(self.kegg_dir, self.omic_names)
        self.job = PathwayAcquisitionJob(JOB_ID, None, os.path.join(self.root, "CLIENT_TMP") + os.sep)
        self.job.setOrganism("mmu")
        self.job.setDatabases(list(databases))
        for name in self.omic_names:
            self.job.addGeneBasedInputOmic({"omicName": name})
        os.makedirs(self.job.getOutputDir())
        with zipfile.ZipFile(self.job.getOutputDir() + "/mapping_results_%s.zip" % JOB_ID, "w") as archive:
            for name in self.omic_names:
                archive.writestr(name + "_matched.txt", "\n".join(matched[name]) + "\n")
        for pathway_id, source in sources.items():
            if source in self.databases:
                pathway = Pathway(pathway_id)
                pathway.setSource(source)
                self.job.matchedPathways[pathway_id] = pathway

    def metagenes(self, cluster_number, omic_list=None, databases=None):
        """Step 2's metagenes phase, or with omic_list and databases the Step 3
        slider's. Each request runs in a fresh temporal folder and removes it
        after, as the servlets do (cleanDirectories)."""
        os.makedirs(self.job.getTemporalDir(), exist_ok=True)
        try:
            with mock.patch.object(jobModule, "KEGG_DATA_DIR", self.kegg_dir), \
                    mock.patch.object(jobModule, "JobProgress", mock.MagicMock()):
                self.job.generateMetagenesList(SRC_DIR + os.sep, cluster_number, omic_list, databases)
        finally:
            shutil.rmtree(self.job.getTemporalDir(), ignore_errors=True)

    def slider(self, omic_name, database, clusters):
        """What pathwayAcquisitionMetagenes_PART2 calls."""
        self.metagenes({omic_name: clusters}, [omic_name], [database])

    def clusters(self, omic_name, source):
        """The cluster numbers the pathways of `source` carry for the omic,
        which are the clusters the Step 3 page draws a thumbnail for."""
        return sorted({metagene["cluster"]
                       for pathway in self.job.matchedPathways.values()
                       if pathway.getSource() == source
                       for metagene in pathway.metagenes.get(omic_name, [])}, key=int)

    def asked_for(self):
        """Every thumbnail the Step 3 page asks for, for every omic and database."""
        return {thumbnail_name(name, k, database)
                for name in self.omic_names for database in self.databases
                for k in self.clusters(name, database)}

    def pictures(self):
        """{file name: sha1 of its bytes} for every .png in the output folder."""
        found = {}
        for name in os.listdir(self.job.getOutputDir()):
            if name.endswith(".png"):
                with open(os.path.join(self.job.getOutputDir(), name), "rb") as handle:
                    found[name] = hashlib.sha1(handle.read()).hexdigest()
        return found

    def export(self):
        """A pathway picture exported into the folder, as /pa_save_image does."""
        with open(os.path.join(self.job.getOutputDir(), EXPORT), "wb") as handle:
            handle.write(b"\x89PNG exported pathway")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


@unittest.skipUnless(_r_packages_available(), "Rscript or the CRAN packages generateMetaGenes.R loads are missing")
class RealScriptTest(unittest.TestCase):
    """The real script, two omics, two databases."""

    OMIC_A = "RNA 100%"
    OMIC_B = "Prot B"
    DATABASES = ("KEGG", "Reactome")
    SLIDER = 1          # the slider's count for OMIC_A on KEGG
    AGAIN = {OMIC_A: "2", OMIC_B: "1"}   # Step 2 again, a count for each omic

    @classmethod
    def setUpClass(cls):
        cls.run_ = _Job([cls.OMIC_A, cls.OMIC_B], cls.DATABASES)
        run = cls.run_
        # Step 2, the cluster count left to the script (it draws the elbow plot).
        run.metagenes({})
        cls.step2 = {"pictures": run.pictures(), "asked": run.asked_for(),
                     "clustersA": run.clusters(cls.OMIC_A, "KEGG")}
        run.export()
        cls.before_slider = run.pictures()
        run.slider(cls.OMIC_A, "KEGG", cls.SLIDER)
        cls.after_slider = {"pictures": run.pictures(), "asked": run.asked_for(),
                            "clustersA": run.clusters(cls.OMIC_A, "KEGG")}
        run.metagenes(dict(cls.AGAIN))
        cls.again = {"pictures": run.pictures(), "asked": run.asked_for()}

    @classmethod
    def tearDownClass(cls):
        cls.run_.close()

    def test_step2_draws_what_step3_asks_for(self):
        """The fixture: every thumbnail is there, and the slider has stale
        clusters to remove (Step 2 made more than the slider asks for)."""
        pictures = set(self.step2["pictures"])
        self.assertEqual(sorted(self.step2["asked"] - pictures), [])
        self.assertEqual(sorted(pictures - self.step2["asked"]),
                         sorted([self.OMIC_A + "_elbow.png", self.OMIC_B + "_elbow.png"]))
        self.assertGreater(len(self.step2["clustersA"]), self.SLIDER)

    def test_the_slider_keeps_the_other_omics_and_databases_pictures(self):
        before, after = self.before_slider, self.after_slider["pictures"]
        moved = set(thumbnails_of(before, self.OMIC_A, "KEGG"))
        self.assertTrue(moved)
        kept = {name: digest for name, digest in before.items() if name not in moved}
        self.assertTrue(any(name.startswith(self.OMIC_B + "_cluster_") for name in kept))
        self.assertTrue(any(name.endswith("_reactome.png") and name.startswith(self.OMIC_A) for name in kept))
        missing = sorted(name for name in kept if name not in after)
        self.assertEqual(missing, [], "pictures the slider for %r on KEGG removed" % self.OMIC_A)
        changed = sorted(name for name in kept if after.get(name) != kept[name])
        self.assertEqual(changed, [], "pictures the slider rewrote")
        self.assertIn(EXPORT, after)
        self.assertIn(self.OMIC_A + "_elbow.png", after)

    def test_the_slider_leaves_exactly_its_new_clusters(self):
        self.assertEqual(self.after_slider["clustersA"], [str(k) for k in range(1, self.SLIDER + 1)])
        mine = thumbnails_of(self.after_slider["pictures"], self.OMIC_A, "KEGG")
        self.assertEqual(mine, sorted(thumbnail_name(self.OMIC_A, k, "KEGG")
                                      for k in self.after_slider["clustersA"]))
        self.assertEqual(sorted(self.after_slider["asked"] - set(self.after_slider["pictures"])), [])

    def test_step2_again_clears_the_old_pictures_of_every_omic(self):
        """After "Go back", Step 2 recomputes every omic and database: what is
        left is the pictures of its own run, and the export."""
        pictures = set(self.again["pictures"])
        self.assertEqual(sorted(self.again["asked"] - pictures), [])
        # A count was given for each omic, so the script drew no elbow plot,
        # and the old ones went with the rest.
        self.assertEqual(sorted(pictures - self.again["asked"]), [EXPORT])


class _FakeScript(object):
    """Stands in for _runMetagenesScripts: for each (omic, database) it writes
    what generateMetaGenes.R writes into --data_dir (the thumbnails, under the
    names the script uses, the elbow plot when the count is left to it, and the
    metagenes table), or fails the way it does on data too small to cluster."""

    DYNAMIC = 4  # the count "the script" picks

    def __init__(self, job, degenerate=()):
        self.job = job
        self.degenerate = set(degenerate)
        self.generation = 0

    def __call__(self, omicNames, databases, commands):
        self.generation += 1
        results = {}
        for (omic, database), (argv, _kClusters) in commands.items():
            args = {}
            for item in argv:
                if item.startswith("--") and "=" in item:
                    key, value = item[2:].split("=", 1)
                    args[key] = value
            if (omic, database) in self.degenerate:
                results[(omic, database)] = (1, b"Error in Mclust: no available data for fitting")
                continue
            prefix = os.path.join(args["data_dir"], args["output_prefix"])
            suffix = "_" + args["database"].lower() if args.get("database") else ""
            count = int(args["kclusters"]) if args.get("kclusters") else self.DYNAMIC
            for k in range(1, count + 1):
                with open(prefix + "_cluster_" + str(k) + suffix + ".png", "wb") as handle:
                    handle.write(("%s %s %d %d" % (omic, database, k, self.generation)).encode("utf-8"))
            if not args.get("kclusters"):
                with open(prefix + "_elbow.png", "wb") as handle:
                    handle.write(("elbow %s %d" % (omic, self.generation)).encode("utf-8"))
            with open(prefix + "_metagenes" + (suffix + ".tab" if suffix else ".tab"), "w") as handle:
                pathways = [pid for pid, p in self.job.matchedPathways.items() if p.getSource() == database]
                for i, pathway_id in enumerate(sorted(pathways)):
                    handle.write("%s\t%s_1\t%d\t0.5\t-0.5\n" % (pathway_id, pathway_id, i % count + 1))
            results[(omic, database)] = (0, b"")
        return results


class FakeScriptTest(unittest.TestCase):
    """The job code's rules for which pictures go, with a stand-in for R."""

    def make(self, omics, databases=("KEGG", "Reactome"), degenerate=()):
        run = _Job(omics, databases)
        self.addCleanup(run.close)
        fake = _FakeScript(run.job, degenerate)
        patcher = mock.patch.object(jobModule, "_runMetagenesScripts", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return run, fake

    def assert_slider_keeps_the_others(self, omics, moved):
        run, _ = self.make(omics)
        run.metagenes({})
        run.export()
        before = run.pictures()
        run.slider(moved, "KEGG", 2)
        after = run.pictures()
        others = {name: digest for name, digest in before.items()
                  if name not in thumbnails_of(before, moved, "KEGG")}
        self.assertEqual(sorted(name for name in others if after.get(name) != others[name]), [],
                         "pictures the slider for %r removed or rewrote" % moved)
        self.assertEqual(thumbnails_of(after, moved, "KEGG"),
                         sorted([thumbnail_name(moved, 1, "KEGG"), thumbnail_name(moved, 2, "KEGG")]))
        self.assertEqual(sorted(run.asked_for() - set(after)), [])

    def test_a_name_that_is_a_glob_pattern_keeps_its_twin(self):
        # As a glob, "RNA [1]_cluster_*.png" matches "RNA 1_cluster_2.png".
        self.assert_slider_keeps_the_others(["RNA [1]", "RNA 1"], "RNA [1]")
        self.assert_slider_keeps_the_others(["RNA *", "RNA 1"], "RNA *")

    def test_a_name_that_holds_another_name_and_cluster(self):
        self.assert_slider_keeps_the_others(["A", "A_cluster_1"], "A")
        self.assert_slider_keeps_the_others(["A", "A_cluster_1"], "A_cluster_1")

    def test_percent_equals_and_quote_in_the_name(self):
        self.assert_slider_keeps_the_others(["RNA 100%", 'x%dy=1 "q"'], "RNA 100%")

    def test_a_database_too_small_to_cluster_keeps_its_pictures(self):
        """Its script stops before writing anything and its pathways keep the
        metagenes they had, so its thumbnails stay with them."""
        run, fake = self.make(["A", "B"])
        run.metagenes({})
        before = run.pictures()
        fake.degenerate.add(("A", "KEGG"))
        run.slider("A", "KEGG", 2)
        self.assertEqual(run.pictures(), before)
        self.assertEqual(sorted(run.asked_for() - set(run.pictures())), [])

    def test_the_elbow_plot_goes_only_with_a_step2_run(self):
        run, _ = self.make(["A", "B"])
        run.metagenes({})
        self.assertIn("A_elbow.png", run.pictures())
        run.slider("A", "KEGG", 2)
        run.slider("A", "Reactome", 2)
        self.assertIn("A_elbow.png", run.pictures(), "a slider recomputes one database, not the omic")
        run.metagenes({"A": "3"})
        pictures = run.pictures()
        self.assertNotIn("A_elbow.png", pictures, "Step 2 with a count for A draws no elbow plot")
        self.assertIn("B_elbow.png", pictures, "Step 2 left B's count to the script, which drew a new one")

    def test_a_job_with_one_database(self):
        """The slider then recomputes the omic for every database the job has."""
        run, _ = self.make(["A", "B"], databases=("KEGG",))
        run.metagenes({})
        run.export()
        run.slider("A", "KEGG", 2)
        pictures = set(run.pictures())
        self.assertEqual(pictures - run.asked_for(), {EXPORT, "B_elbow.png"})
        self.assertEqual(sorted(run.asked_for() - pictures), [])

    def test_step2_again_clears_stale_clusters(self):
        run, _ = self.make(["A", "B"])
        run.metagenes({})
        run.export()
        run.metagenes({"A": "2", "B": "3"})
        pictures = set(run.pictures())
        self.assertEqual(sorted(pictures - run.asked_for()), [EXPORT])
        self.assertEqual(sorted(run.asked_for() - pictures), [])


class IsClusterThumbnailTest(unittest.TestCase):

    def test_names(self):
        # Imported here, so on a tree without it the tests above still run.
        from src.classes.JobInstances.PathwayAcquisitionJob import isClusterThumbnail
        yes = [("RNA 100%_cluster_1.png", "RNA 100%", "KEGG"),
               ("RNA 100%_cluster_12_reactome.png", "RNA 100%", "Reactome"),
               ("A_cluster_1_cluster_3.png", "A_cluster_1", "KEGG"),
               ("RNA [1]_cluster_2_mapman.png", "RNA [1]", "MapMan")]
        no = [("RNA 1_cluster_2.png", "RNA [1]", "KEGG"),
              ("A_cluster_1_cluster_3.png", "A", "KEGG"),
              ("A_cluster_2_reactome.png", "A", "KEGG"),
              ("A_cluster_2.png", "A", "Reactome"),
              ("A_cluster_.png", "A", "KEGG"),
              ("A_cluster_x.png", "A", "KEGG"),
              ("A_cluster_².png", "A", "KEGG"),
              ("A_elbow.png", "A", "KEGG"),
              ("A_metagenes.tab", "A", "KEGG"),
              (EXPORT, "paintomics", "KEGG"),
              ("B_cluster_1.png", "A", "KEGG")]
        for name, omic, database in yes:
            self.assertTrue(isClusterThumbnail(name, omic, database), (name, omic, database))
        for name, omic, database in no:
            self.assertFalse(isClusterThumbnail(name, omic, database), (name, omic, database))


if __name__ == "__main__":
    unittest.main(verbosity=2)
