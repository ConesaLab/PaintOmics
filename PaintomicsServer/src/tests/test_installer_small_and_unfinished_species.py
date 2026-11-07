#!/usr/bin/env python3
"""Two installer checks that went wrong at the edges of their input.

1. How many pathway diagrams may fail to download (getSpecieKeggData).
   The allowance was int(total * 0.05), counted down and tested with "== 0".
   Under 20 pathways it started at 0, went negative on the first failure and
   never hit 0 again, so a small species could lose every diagram without the
   download stopping. From 20 to 39 pathways it stopped at the first failure,
   and above that it stopped one short of the 5% it promised (100 pathways:
   the fifth failure stopped it). The rule now is 5% rounded down and never
   fewer than one, and the download stops on the first failure past it.

2. Listing the installed species (getCurrentInstalledSpecies).
   A species database without its KEGG or MAPPING record in `versions` --
   both installers write that collection last, so this is a build that did
   not finish -- raised IndexError from find(...)[0], and that one database
   stopped every later install at step 1. It is now left out of the list and
   the other species are listed as before. The run's summary names it, says
   how to repair it (download and install for a KEGG organism,
   customSpeciesInstaller.py for a custom one), or, when the same run built
   it again, says that instead. customSpeciesInstaller's own species.json
   rewrite leaves it out too, so neither installer offers it in the picker.
   And the summary's warnings header no longer says "(installed anyway)",
   which was false for every warning about a species that was not installed.
   The allspecies runner's census uses the same rule, so a restart builds
   such a species again instead of recording it as installed.

The KEGG downloads and MongoDB are replaced by fakes here, so the suite runs
offline and touches no real database. The fake collection answers find() with
a plain list, so on the old code a missing record raises IndexError, as the
real cursor does. The runner's census is real JavaScript, run by node against a
fake `db`.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_installer_small_and_unfinished_species
"""
import argparse
import importlib.util
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))
if SRC not in sys.path:
    # DBManager imports `conf.serverconf` and `AdminTools.species_json`
    # relative to src/, as the installers run it.
    sys.path.insert(0, SRC)

# DBManager reads the server configuration at import (conf/serverconf.py is
# gitignored; scripts/ci/run-unit-tests.sh copies the template in). A missing
# one must fail here, not skip: a suite that skips is a green result that ran
# nothing.
from AdminTools import DBManager  # noqa: E402

SPECIE = "tst"


def loadRunner():
    """deploy/species/allspecies_runner.py, a standalone script outside src/."""
    path = os.path.join(SRC, "..", "..", "deploy", "species", "allspecies_runner.py")
    spec = importlib.util.spec_from_file_location("allspecies_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


class FakeKegg(object):
    """Stands in for downloadKEGGFile: writes the two lists, and a KGML file
    for every pathway not named in `failing`, which raise instead."""

    def __init__(self, total, failing=()):
        self.pathways = ["%s%05d" % (SPECIE, n) for n in range(1, total + 1)]
        self.failing = set(failing)
        self.kgmlAttempts = []

    def __call__(self, message, logFile, URL, dirName, fileName, delay, maxTries):
        target = os.path.join(dirName, fileName)
        if fileName == "gene2pathway.list":
            body = "".join("%s:g%d\tpath:%s\n" % (SPECIE, n, p) for n, p in enumerate(self.pathways))
        elif fileName == "pathways.list":
            body = "".join("path:%s\tPathway %s\n" % (p, p) for p in self.pathways)
        else:
            pathway = fileName[:-len(".kgml")]
            self.kgmlAttempts.append(pathway)
            if pathway in self.failing:
                raise Exception("Unable to retrieve " + fileName + " from " + URL + ": 503 Server Error")
            body = "<pathway name=\"path:%s\"/>\n" % pathway
        with open(target, "w") as handle:
            handle.write(body)
        return True


class KgmlFailureAllowance(unittest.TestCase):
    """getSpecieKeggData, end to end over a temp directory, KEGG faked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kgml-allowance-")
        self.dirName = self.tmp + "/"
        self.downloadLog = os.path.join(self.tmp, "download.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def download(self, total, failing):
        fake = FakeKegg(total, ["%s%05d" % (SPECIE, n) for n in failing])
        with mock.patch.object(DBManager, "downloadKEGGFile", fake), \
                mock.patch.object(DBManager, "wait", lambda seconds: None):
            try:
                errors = DBManager.getSpecieKeggData(SPECIE, self.downloadLog, self.dirName, "1/1")
            except Exception as stopped:
                return fake, None, str(stopped)
        return fake, errors, None

    def assertCarriesOn(self, total, failing):
        fake, errors, stopped = self.download(total, failing)
        self.assertIsNone(stopped, "%d of %d failing must not stop the download" % (len(failing), total))
        self.assertEqual(total, len(fake.kgmlAttempts), "every pathway is still tried")
        self.assertEqual(sorted("%s%05d" % (SPECIE, n) for n in failing), sorted(errors.split()),
                         "the failed pathways are named for the caller")

    def assertStopsAt(self, total, failing, stopAt):
        fake, errors, stopped = self.download(total, failing)
        self.assertIsNotNone(stopped, "%d of %d failing must stop the download" % (len(failing), total))
        self.assertIn("Too many errors while downloading the KGML files for organism " + SPECIE, stopped)
        failedPathways = ["%s%05d" % (SPECIE, n) for n in failing]
        self.assertEqual(failedPathways[stopAt - 1], fake.kgmlAttempts[-1],
                         "the download stops on failure number %d, not later" % stopAt)

    def test_the_allowance_is_five_percent_rounded_down_and_at_least_one(self):
        expected = {0: 1, 1: 1, 5: 1, 19: 1, 20: 1, 39: 1, 40: 2, 59: 2, 60: 3, 100: 5, 1000: 50}
        self.assertEqual(expected, {total: DBManager.kgmlFailuresAllowed(total) for total in expected})

    def test_one_pathway_that_fails_is_reported_not_stopped_on(self):
        self.assertCarriesOn(1, [1])

    def test_five_pathways_stop_at_the_second_failure(self):
        # The old allowance was 0 here and the "== 0" test never fired: all
        # five failed and the download went on to the end.
        self.assertCarriesOn(5, [3])
        self.assertStopsAt(5, [1, 2, 3, 4, 5], stopAt=2)

    def test_the_message_says_when_the_download_stops(self):
        # Not that one failure is fine: download_command fails any species with
        # a failed diagram, so "at most 1 may fail" would be false.
        _, _, stopped = self.download(5, [1, 2, 3, 4, 5])
        self.assertIn("2 of 5 pathways failed, and the download stops after more than 1: "
                      "tst00001 tst00002", stopped)

    def test_nineteen_pathways_allow_one_failure(self):
        self.assertCarriesOn(19, [7])
        self.assertStopsAt(19, [2, 9, 15, 19], stopAt=2)

    def test_twenty_pathways_allow_one_failure(self):
        # The old allowance was 1 here, so the first failure stopped it.
        self.assertCarriesOn(20, [11])
        self.assertStopsAt(20, [4, 11], stopAt=2)

    def test_a_hundred_pathways_allow_five_failures(self):
        # The old code stopped on the fifth: 4%, not the 5% it promised.
        self.assertCarriesOn(100, [10, 20, 30, 40, 50])
        self.assertStopsAt(100, [10, 20, 30, 40, 50, 60, 70], stopAt=6)


class FakeCollection(object):
    def __init__(self, documents):
        self.documents = list(documents)

    def _matching(self, query):
        return [d for d in self.documents if all(d.get(k) == v for k, v in query.items())]

    def find(self, query):
        # A list, so [0] on no match raises IndexError the way the cursor did.
        return self._matching(query)

    def find_one(self, query):
        found = self._matching(query)
        return found[0] if found else None


class FakeDatabase(object):
    def __init__(self, versions):
        self.versions = FakeCollection(versions)


FINISHED = [{"name": "KEGG", "date": "20261101 1200"}, {"name": "MAPPING", "date": "20261101 1300"}]


class FakeMongo(object):
    DATABASES = {
        "admin": [],
        "PaintomicsDB": [],
        "global-paintomics": [{"name": "COMMON", "date": "20261101 1200"}],
        "mmu-paintomics": FINISHED + [{"name": "ACCEPTED_IDS", "ids": "Gene name, Entrez Gene"}],
        "hsa-paintomics": [{"name": "KEGG", "date": "20261102 0800"},
                           {"name": "MAPPING", "date": "20261102 0900"}],
        # A KEGG organism whose build stopped between its two records.
        "buc-paintomics": [{"name": "KEGG", "date": "20261101 1200"}],
        # A custom species whose RE-RUN of customSpeciesInstaller stopped in
        # write_mongo, between dropping versions and inserting it again. Only
        # a re-run reaches this with the row in organisms_custom.list: main()
        # registers a new organism after write_mongo.
        "thaz-paintomics": [],
        # A code in neither organisms list. This is what the FIRST
        # customSpeciesInstaller run of a new species leaves if it stops in
        # write_mongo, for the reason above.
        "half-paintomics": [],
    }

    def __init__(self, *args, **kwargs):
        pass

    def list_database_names(self):
        return list(self.DATABASES)

    def __getitem__(self, name):
        return FakeDatabase(self.DATABASES[name])

    def close(self):
        pass


class KeggDataTree(unittest.TestCase):
    """A temp KEGG_DATA/current/common with both organisms lists, as
    customSpeciesInstaller.register_in_organisms_list leaves them: the custom
    row in organisms_all.list and in organisms_custom.list."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kegg-data-")
        common = os.path.join(self.tmp, "current", "common")
        os.makedirs(common)
        with open(os.path.join(common, "organisms_all.list"), "w") as handle:
            handle.write("T01002\tmmu\tMus musculus (house mouse)\tEukaryotes;Animals\n")
            handle.write("T01001\thsa\tHomo sapiens (human)\tEukaryotes;Animals\n")
            handle.write("T00041\tbuc\tBuchnera aphidicola APS\tProkaryotes;Bacteria\n")
            handle.write("T90433\tthaz\tTrichoderma harzianum\tEukaryotes;Custom\n")
        with open(os.path.join(common, "organisms_custom.list"), "w") as handle:
            handle.write("T90433\tthaz\tTrichoderma harzianum\tEukaryotes;Custom\n")
        self.savedWarnings = list(DBManager.INSTALL_WARNINGS)
        self.savedUnfinished = list(DBManager.UNFINISHED_BUILDS)
        del DBManager.INSTALL_WARNINGS[:]
        del DBManager.UNFINISHED_BUILDS[:]

    def tearDown(self):
        DBManager.INSTALL_WARNINGS[:] = self.savedWarnings
        DBManager.UNFINISHED_BUILDS[:] = self.savedUnfinished
        shutil.rmtree(self.tmp, ignore_errors=True)

    def listInstalled(self):
        import pymongo
        with mock.patch.object(pymongo, "MongoClient", FakeMongo), \
                mock.patch.object(DBManager, "KEGG_DATA_DIR", self.tmp + "/"):
            return DBManager.getCurrentInstalledSpecies()


class UnfinishedSpeciesDatabase(KeggDataTree):
    """getCurrentInstalledSpecies, and the summary install_command writes."""

    def test_the_finished_species_are_listed_and_the_unfinished_left_out(self):
        listed = {entry["organism_code"]: entry for entry in self.listInstalled()}
        self.assertEqual({"mmu", "hsa"}, set(listed))
        self.assertEqual("Mus musculus (house mouse)", listed["mmu"]["organism_name"])
        self.assertEqual("20261101 1200", listed["mmu"]["kegg_date"])
        self.assertEqual("20261101 1300", listed["mmu"]["mapping_date"])
        self.assertEqual("Gene name, Entrez Gene", listed["mmu"]["acceptedIDs"])
        self.assertEqual("", listed["hsa"]["acceptedIDs"])

    def test_each_unfinished_species_is_recorded_with_who_built_it(self):
        self.listInstalled()
        self.assertEqual({("buc", "buc-paintomics", ("MAPPING",), "kegg"),
                          ("thaz", "thaz-paintomics", ("KEGG", "MAPPING"), "custom"),
                          ("half", "half-paintomics", ("KEGG", "MAPPING"), None)},
                         {(code, db, tuple(missing), kind)
                          for code, db, missing, kind in DBManager.UNFINISHED_BUILDS})
        # Listed twice (every install calls it once), recorded once.
        self.listInstalled()
        self.assertEqual(3, len(DBManager.UNFINISHED_BUILDS))

    def test_the_summary_says_how_to_repair_each_one_by_who_built_it(self):
        self.listInstalled()
        warnings = dict(DBManager.unfinishedBuildWarnings([]))
        self.assertEqual({"buc", "thaz", "half"}, set(warnings))
        self.assertIn("buc-paintomics has no MAPPING record in its versions collection, so its build "
                      "never finished; species.json leaves buc out", warnings["buc"])
        self.assertIn("run download and install for buc again", warnings["buc"])
        # KEGG has no Trichoderma harzianum: DBManager download cannot fetch it.
        self.assertIn("thaz-paintomics has no KEGG and MAPPING record", warnings["thaz"])
        self.assertIn("run customSpeciesInstaller.py for thaz again", warnings["thaz"])
        self.assertNotIn("download", warnings["thaz"])
        self.assertIn("To repair it, run download and install for half again if it is a KEGG organism, "
                      "or customSpeciesInstaller.py if it is a custom one (half is in neither "
                      "organisms_all.list nor organisms_custom.list)", warnings["half"])

    def test_a_species_this_run_built_again_is_not_told_to_build_it_again(self):
        # The allspecies runner redoes an install killed part-way, whether the
        # same batch goes on or the runner restarts (see
        # AllSpeciesRunnerCountsFinishedBuildsOnly), which is this case: the
        # run rebuilt buc, species.json lists it, and the summary used to tell
        # the operator to run download and install again.
        self.listInstalled()
        warnings = dict(DBManager.unfinishedBuildWarnings(["buc", "mmu"]))
        self.assertIn("buc-paintomics had no MAPPING record in its versions collection when this run "
                      "started, so an earlier build never finished; this run built buc again and "
                      "species.json lists it", warnings["buc"])
        self.assertNotIn("repair", warnings["buc"])
        self.assertNotIn("leaves buc out", warnings["buc"])
        self.assertIn("run customSpeciesInstaller.py for thaz again", warnings["thaz"])

    def test_the_warnings_header_does_not_say_installed_anyway(self):
        # Skipped species and unfinished builds are listed under it too, and
        # they were not installed.
        self.listInstalled()
        lines = DBManager.installSummaryLines([], [], ["zzz"], DBManager.unfinishedBuildWarnings([]))
        self.assertIn("  warnings  : 3", lines)
        self.assertFalse([line for line in lines if "installed anyway" in line], lines)
        self.assertIn("  installed : 0  -", lines)
        self.assertIn("  skipped   : 1  zzz", lines)
        self.assertEqual("  warnings  : none", DBManager.installSummaryLines(["mmu"], [], [], [])[-2])

    def test_the_all_species_runner_reads_the_same_verdicts_with_the_warnings_in_the_log(self):
        # deploy/species/allspecies_runner.py decides each species' fate by
        # scanning the WHOLE install log for "installed : <n> <codes>" style
        # lines and "INSTALL <n> <code>...SUCCESS" verdicts. The unfinished
        # builds are written twice -- when the listing meets the database, and
        # again in the INSTALL SUMMARY -- and neither copy may read as a
        # verdict, or one unfinished database would rewrite a whole batch's
        # results.
        runner = loadRunner()

        with self.assertLogs(level=logging.INFO) as listing:
            self.listInstalled()
        prefix = "2026-11-07 12:00:00,000 - INFO - DBManager.py : log - "
        lines = [prefix + record.getMessage() for record in listing.records]
        self.assertEqual(3, len([line for line in lines if "so its build never finished" in line]))
        lines.append(prefix + "INSTALL  1 buc...SUCCESS")
        lines += [prefix + line for line in DBManager.installSummaryLines(
            ["buc"], [], [], DBManager.unfinishedBuildWarnings(["buc"]))]
        logPath = os.path.join(self.tmp, "install.log")
        with open(logPath, "w") as handle:
            handle.write("\n".join(lines) + "\n")

        parsed = runner.Runner.parseSummary(runner.Runner.__new__(runner.Runner), logPath)
        self.assertEqual({"buc"}, parsed["installed"])
        self.assertEqual(set(), parsed["failed"])
        self.assertEqual(set(), parsed["skipped"])
        self.assertEqual(set(), parsed["from_verdict_lines"])


class InstallSummaryOfARun(KeggDataTree):
    """The real install_command over the same databases, with only the build
    of a species faked: the summary it logs and the species.json it writes. The
    tests above call the pieces; these fail if install_command stops joining
    them up."""

    def setUp(self):
        super().setUp()
        for code in ("mmu", "buc"):
            os.makedirs(os.path.join(self.tmp, "current", code))

    def install(self, species, failing=()):
        """The lines the run logged from INSTALL SUMMARY on. A species in
        `failing` fails its build, and the rollback's second build of it works."""
        logged = []
        builds = []

        def build(specie, *args):
            builds.append(specie)
            if specie in failing and builds.count(specie) == 1:
                raise RuntimeError("the build stopped")

        import pymongo
        with mock.patch.object(pymongo, "MongoClient", FakeMongo), \
                mock.patch.object(DBManager, "KEGG_DATA_DIR", self.tmp + "/"), \
                mock.patch.object(DBManager, "ROOT_DIRECTORY", "/nowhere/", create=True), \
                mock.patch.object(DBManager, "readConfigurationFile", lambda: None), \
                mock.patch.object(DBManager, "sleep", lambda seconds: None), \
                mock.patch.object(DBManager, "log", logged.append), \
                mock.patch.object(DBManager, "errorlog", logged.append), \
                mock.patch.object(DBManager, "restorePreviousVersionData", lambda *args: None), \
                mock.patch.object(DBManager, "installSpecieData", build):
            DBManager.install_command(species=species, common=0, reinstall=1)
        return logged[logged.index("INSTALL SUMMARY"):]

    def warningsIn(self, summary):
        """{subject: its warning line}, in the order the summary lists them."""
        rows = [line.strip().split(": ", 1) for line in summary if line.startswith("      ")]
        return {subject: detail for subject, detail in rows}

    def speciesInJson(self):
        with io.open(os.path.join(self.tmp, "current", "species.json"), encoding="utf-8") as handle:
            return sorted(row["value"] for row in json.load(handle)["species"])

    def test_a_species_the_run_built_again_is_reported_as_built_and_is_offered(self):
        summary = self.install("buc")
        self.assertIn("  installed : 1  buc", summary)
        self.assertIn("  warnings  : 3", summary)
        warnings = self.warningsIn(summary)
        self.assertIn("this run built buc again and species.json lists it", warnings["buc"])
        self.assertNotIn("repair", warnings["buc"])
        self.assertIn("To repair it, run customSpeciesInstaller.py for thaz again", warnings["thaz"])
        self.assertIn("half is in neither organisms_all.list nor organisms_custom.list", warnings["half"])
        self.assertEqual(["buc", "hsa", "mmu"], self.speciesInJson())

    def test_a_species_the_run_did_not_build_is_told_how_to_repair_it_and_is_not_offered(self):
        summary = self.install("mmu")
        self.assertIn("  installed : 1  mmu", summary)
        warnings = self.warningsIn(summary)
        self.assertIn("species.json leaves buc out. To repair it, run download and install for buc again",
                      warnings["buc"])
        self.assertEqual(["hsa", "mmu"], self.speciesInJson())

    def test_a_species_the_run_tried_and_failed_is_told_how_to_repair_it(self):
        summary = self.install("buc", failing=["buc"])
        self.assertIn("  failed    : 1  buc", summary)
        warnings = self.warningsIn(summary)
        self.assertNotIn("built buc again", warnings["buc"])
        self.assertIn("run download and install for buc again", warnings["buc"])
        self.assertEqual(["hsa", "mmu"], self.speciesInJson())

    def test_the_header_is_only_a_count_and_the_unfinished_builds_come_first(self):
        # zzz has no data, so it is skipped: it was not "installed anyway",
        # which is what this header used to say of every warning under it.
        summary = self.install("mmu,zzz")
        self.assertIn("  skipped   : 1  zzz", summary)
        self.assertIn("  warnings  : 4", summary)
        self.assertEqual(["buc", "thaz", "half", "zzz"], list(self.warningsIn(summary)))


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class AllSpeciesRunnerCountsFinishedBuildsOnly(unittest.TestCase):
    """deploy/species/allspecies_runner.py when it starts, or restarts, a run.

    A database with pathways and no version records is a build killed part-way.
    The runner used to count it by its pathways alone and record the species as
    installed, while DBManager leaves it out of species.json, so RUN COMPLETE
    counted a species the picker did not offer, and nothing built it again.
    """

    DATABASES = {
        "global-paintomics": {"kegg": 9, "versions": ["COMMON"]},
        "mmu-paintomics": {"kegg": 364, "versions": ["KEGG", "MAPPING", "ACCEPTED_IDS"]},
        # Killed after the kegg import, before versions.
        "buc-paintomics": {"kegg": 86, "versions": []},
        # Killed between the two records.
        "eco-paintomics": {"kegg": 120, "versions": ["KEGG"]},
    }

    # The calls census() makes on mongosh's `db`.
    FAKE_DB = """
        const DATABASES = %s;
        const db = {
            adminCommand: () => ({databases: Object.keys(DATABASES).map(name => ({name}))}),
            getSiblingDB: name => ({
                kegg: {countDocuments: () => DATABASES[name].kegg},
                versions: {distinct: (field, query) =>
                    DATABASES[name].versions.filter(record => query.name.$in.includes(record))},
            }),
        };
        const print = console.log;
    """

    def setUp(self):
        self.module = loadRunner()
        self.tmp = tempfile.mkdtemp(prefix="allspecies-")
        self.stateDir = os.path.join(self.tmp, "state")
        os.makedirs(self.stateDir)
        manifest = os.path.join(self.tmp, "manifest.tsv")
        with open(manifest, "w") as handle:
            handle.write("code\tkingdom\tpriority\taction\tkegg\treactome\n")
            for code in ("mmu", "buc", "eco"):
                handle.write("%s\tBacteria\t1\tinstall\t1\t0\n" % code)
        self.runner = self.module.Runner(argparse.Namespace(
            manifest=manifest, kinds="Bacteria", only=None, max_species=0, order="kinds",
            state_dir=self.stateDir, log_dir=os.path.join(self.tmp, "logs"),
            workers=0, stagger=0, requeue_every=1800, retry_failed=False))
        mock.patch.object(self.runner, "mongoEval", self.mongoEvalInNode).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.runner.logFile.close)

    def mongoEvalInNode(self, js, database="admin"):
        done = subprocess.run(["node", "-e", self.FAKE_DB % json.dumps(self.DATABASES) + js],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout.strip()

    def restart(self, states):
        """Runner.run() over state files left by a stopped run, download and
        install workers doing nothing: only what it decides at the start."""
        for code, state in states.items():
            with open(os.path.join(self.stateDir, code + ".json"), "w") as handle:
                json.dump({"code": code, "state": state, "attempts": 0, "action": "install"}, handle)
        with mock.patch.object(self.runner, "installWorker", lambda rows, total: None), \
                mock.patch.object(self.module.time, "sleep", lambda seconds: None):
            self.runner.run()
        return {code: self.runner.getState(code)["state"] for code in states}

    def test_the_census_counts_a_database_only_when_its_build_finished(self):
        self.assertEqual({"mmu": 364, "buc": 0, "eco": 0}, self.runner.census())

    def test_a_restart_builds_an_unfinished_species_again(self):
        states = self.restart({"mmu": "installing", "buc": "installing", "eco": "downloading"})
        self.assertEqual("installed", states["mmu"])
        self.assertEqual("found installed before this run", self.runner.getState("mmu")["reason"])
        self.assertEqual("downloaded", states["buc"], "its staged data goes back to the install worker")
        self.assertEqual("pending", states["eco"], "its download starts again")

    def test_the_runner_and_the_installers_use_one_rule(self):
        from AdminTools.species_json import FINISHED_BUILD_RECORDS
        self.assertEqual(FINISHED_BUILD_RECORDS, self.module.FINISHED_BUILD_RECORDS)


class CustomInstallerLeavesUnfinishedOut(KeggDataTree):
    """customSpeciesInstaller.regenerate_species_json over the same databases."""

    def test_species_json_lists_the_finished_builds_only(self):
        # It used to list every *-paintomics database, so the next custom
        # install put the species DBManager had left out back in the picker.
        import pymongo
        from AdminTools import customSpeciesInstaller
        logged = []
        with mock.patch.object(pymongo, "MongoClient", FakeMongo), \
                mock.patch.object(customSpeciesInstaller, "log", logged.append):
            target, count = customSpeciesInstaller.regenerate_species_json(self.tmp, "localhost", 27017)
        with io.open(target, encoding="utf-8") as handle:
            species = json.load(handle)["species"]
        self.assertEqual(["hsa", "mmu"], [s["value"] for s in species])
        self.assertEqual(2, count)
        for database in ("buc-paintomics", "thaz-paintomics", "half-paintomics"):
            self.assertTrue([line for line in logged if database in line and "build never finished" in line],
                            "%s is named: %r" % (database, logged))

    def test_both_installers_agree_on_what_is_installed(self):
        import pymongo
        from AdminTools import customSpeciesInstaller
        with mock.patch.object(pymongo, "MongoClient", FakeMongo), \
                mock.patch.object(customSpeciesInstaller, "log", lambda message: None):
            customSpeciesInstaller.regenerate_species_json(self.tmp, "localhost", 27017)
        target = os.path.join(self.tmp, "current", "species.json")
        with io.open(target, encoding="utf-8") as handle:
            custom = handle.read()
        DBManager.generateAvailableSpeciesFile(self.listInstalled(),
                                               os.path.join(self.tmp, "current", "common", "organisms_all.list"),
                                               target)
        with io.open(target, encoding="utf-8") as handle:
            self.assertEqual(custom, handle.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
