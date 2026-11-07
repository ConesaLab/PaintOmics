#!/usr/bin/env python3
"""A bare address boots a new analysis without a query that could never work.

Why this exists
---------------
Application.loadFromIndexedDB looked for "the most recent job" on a bare address
with db.table("jobs").orderBy('timestamp').last(). The store's only index is
jobID (openPaintomicsDB), so Dexie rejected that every time, the rejection
handler booted a fresh job, and every visit logged "could not read the cached job
from IndexedDB" (seen in headless Chromium on every bare boot).

Making the query work was not the fix. Every job opened is written to that store,
and the logo's "exit the current job" clears sessionStorage only, so a working
query would reopen on the next load the job that was just left. What the page
did, a new analysis on a bare address, is what it still does; the query and its
false error are gone. A job in the address still reads its row.

The real loadFromIndexedDB and restoreCachedModel run under node, against a Dexie
stand-in that refuses orderBy on an unindexed key as Dexie does.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_bare_address_asks_indexeddb_nothing
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

APP_JS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "PaintomicsClient", "public_html", "app.js"))

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(%(app)s, "utf8");

async function visit(URLjobID, rows, reads) {
    const asked = [], errors = [], booted = [];
    let returned = false;
    function Dexie(name) {
        asked.push("open " + name);
        this.version = function () { return {stores() {}}; };
        this.table = function (store) {
            return {
                orderBy(key) {
                    asked.push("orderBy " + key);
                    return {last() { return Promise.reject(new Error("KeyPath " + key + " on object store " + store + " is not indexed")); }};
                },
                get(id) {
                    asked.push("get " + id);
                    return reads === "fail" ? Promise.reject(new Error("read failed")) : Promise.resolve(rows[id]);
                },
                delete() { return Promise.resolve(); },
                clear() { return Promise.resolve(); }
            };
        };
    }
    function JobInstance(jobID) {
        this.jobID = jobID;
        this.getJobID = function () { return this.jobID; };
        this.loadFromJSON = function (json) { this.jobID = json.jobID; };
    }
    const sandbox = {
        debugging: false, Dexie: Dexie, JobInstance: JobInstance, document: {},
        console: {info() {}, warn() {}, log() {}, error(message) { errors.push(String(message)); }},
        $: function () { return {ready() {}}; }
    };
    sandbox.window = sandbox;
    const context = vm.createContext(sandbox);
    vm.runInContext("Date.logFormat = function () { return ''; };", context);
    vm.runInContext(source, context);
    const app = vm.runInContext("new Application()", context);
    app.continueBoot = function (model, id) { booted.push({jobID: model.getJobID(), address: id, beforeReturn: !returned}); };
    app.loadFromIndexedDB(new JobInstance(null), URLjobID);
    returned = true;
    for (let i = 0; i < 5; i++) { await new Promise((resolve) => setImmediate(resolve)); }
    return {asked: asked, booted: booted, errors: errors.filter((e) => e.indexOf("IndexedDB") !== -1)};
}

(async function () {
    const schema = Number(/var PA_JOB_CACHE_SCHEMA = (\d+);/.exec(source)[1]);
    const stored = {jobID: "J1", timestamp: 9999999999, cacheSchema: schema};
    const results = {
        bareAddress: await visit(null, {}),
        bareAddressWithAJobStored: await visit(null, {J1: stored}),
        emptyJobInTheAddress: await visit("", {J1: stored}),
        jobInTheAddress: await visit("J1", {J1: stored}),
        jobNotStored: await visit("J2", {J1: stored}),
        readFails: await visit("J1", {J1: stored}, "fail")
    };
    process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class BareAddressTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        directory = tempfile.mkdtemp(prefix="paintomics-bare-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"app": json.dumps(APP_JS)})
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_a_bare_address_asks_indexeddb_nothing(self):
        for name in ("bareAddress", "bareAddressWithAJobStored", "emptyJobInTheAddress"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["asked"], [], "a query on the jobs store for a bare address")

    def test_it_boots_a_new_analysis_at_once_and_logs_no_error(self):
        for name in ("bareAddress", "bareAddressWithAJobStored", "emptyJobInTheAddress"):
            with self.subTest(case=name):
                result = self.results[name]
                self.assertEqual(len(result["booted"]), 1)
                self.assertIsNone(result["booted"][0]["jobID"], "a stored job was reopened on a bare address")
                self.assertTrue(result["booted"][0]["beforeReturn"])
                self.assertEqual(result["errors"], [], "a read that never happened was reported as failing")

    def test_a_job_in_the_address_still_reads_its_row(self):
        result = self.results["jobInTheAddress"]
        self.assertEqual(result["asked"], ["open paintomics", "get J1"])
        self.assertEqual([b["jobID"] for b in result["booted"]], ["J1"])
        self.assertEqual(result["booted"][0]["address"], "J1")
        self.assertEqual(result["errors"], [])

    def test_a_job_with_no_row_boots_fresh_for_the_server_to_answer(self):
        result = self.results["jobNotStored"]
        self.assertEqual(result["asked"], ["open paintomics", "get J2"])
        self.assertEqual(result["booted"], [{"jobID": None, "address": "J2", "beforeReturn": False}])

    def test_a_read_that_fails_is_still_contained_and_reported(self):
        result = self.results["readFails"]
        self.assertEqual([b["jobID"] for b in result["booted"]], [None])
        self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
