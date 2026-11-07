#!/usr/bin/env python3
"""The logo asks before starting over only when starting over loses something.

Why this exists
---------------
Clicking the PaintOmics wordmark, or Reset on Step 1, runs
JobController.resetButtonClickHandler, and it asked "Are you sure you want to exit
the current job?" on every page. On an untouched Step 1 form, the page every
visit opens on, no job is open, so the question was false, and a question that is
false every time is one people learn to answer without reading.

Starting over drops every view (cleanStoredApplicationData), so what it can lose
is an open job, or what someone has entered into a form and not run. The rule
pinned here:

  * a job is open: the question about the job, as before;
  * no job, but a form holds a value its user gave it (an organism, a file, a
    changed setting, which is what ExtJS's isDirty() reports): a question that
    says that is what will be cleared;
  * neither: no question, and the logo goes straight to a new analysis.

The real handler runs under node, against a main view whose forms are stand-ins
that answer isDirty(). Which fields are dirty on the real Step 1 form was checked
in headless Chromium: none on an untouched form, the organism once one is chosen,
the file fields once a file is.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_logo_asks_only_about_what_it_would_lose
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from src.tests.test_status_poll_retries_unreadable_answers import JOB_CONTROLLER

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(%(controller)s, "utf8");

const field = (dirty) => ({isDirty() { return dirty; }});
const model = (jobID) => ({getJobID() { return jobID; }});
const view = (fields, jobID) => ({
    component: {isDestroyed: false, query(selector) { return selector === "field" ? fields : []; }},
    getModel() { return model(jobID === undefined ? null : jobID); }
});

/* One click on the logo. `pages` is what the main view holds; `last` names the
   step view getLastJobView returns. `answer` is the button pressed, if asked. */
function click(pages, last, answer, force) {
    const calls = [];
    let asked = null;
    const mainView = {
        subviews: pages,
        getLastJobView() { return last ? pages[last] : null; },
        getSubView(name) { return pages[name]; }
    };
    const context = vm.createContext({
        console: {log() {}, info() {}, warn() {}, error() {}},
        application: {getMainView() { return mainView; }},
        Ext: {MessageBox: {confirm(title, question, callback) { asked = question; if (answer) { callback(answer); } }}},
        window: {location: {pathname: "/"}, history: {replaceState(a, b, url) { calls.push("address " + url); }}},
        JobInstance: function (jobID) { this.jobID = jobID; }
    });
    vm.runInContext(source, context, {filename: "JobController.js"});
    const controller = vm.runInContext("new JobController()", context);
    controller.cleanStoredApplicationData = () => calls.push("clean");
    controller.abandonStep1Submission = () => calls.push("abandon");
    controller.showJobInstance = (job) => calls.push(job.jobID === null ? "new analysis" : "job " + job.jobID);
    controller.resetButtonClickHandler(null, force === true, force === true ? () => calls.push("callback") : undefined);
    return {asked: asked, calls: calls};
}

const untouched = () => view([field(false), field(false), field(false)]);
const results = {
    untouchedStep1: click({PA_Step1JobView: untouched()}, "PA_Step1JobView"),
    blankPage: click({}, null),
    myJobs: click({DM_MyDataListView: view([field(false)])}, null),
    organismChosenYes: click({PA_Step1JobView: view([field(true), field(false)])}, "PA_Step1JobView", "yes"),
    organismChosenNo: click({PA_Step1JobView: view([field(true), field(false)])}, "PA_Step1JobView", "no"),
    toolFormFilledIn: click({PA_Step1JobView: untouched(), fromBEDtoGenes: view([field(true)])}, "PA_Step1JobView", "no"),
    step1FilledInBehindMyJobs: click({PA_Step1JobView: view([field(true)]), DM_MyDataListView: view([])}, "PA_Step1JobView", "no"),
    jobOpenYes: click({PA_Step1JobView: view([], "J1"), PA_Step2JobView: view([field(true)], "J1")}, "PA_Step2JobView", "yes"),
    jobOpenNo: click({PA_Step2JobView: view([], "J1")}, "PA_Step2JobView", "no"),
    backOnStep1OfAJob: click({PA_Step1JobView: view([], "J1"), PA_Step2JobView: view([], "J1")}, "PA_Step2JobView", "no"),
    destroyedForm: click({PA_Step1JobView: untouched(), old: {component: {isDestroyed: true, query() { return [field(true)]; }}}}, "PA_Step1JobView"),
    componentThatIsNoContainer: click({PA_Step1JobView: untouched(), box: {component: {isDestroyed: false}}}, "PA_Step1JobView"),
    emptySlot: click({PA_Step1JobView: untouched(), cleared: null}, "PA_Step1JobView"),
    forced: click({PA_Step2JobView: view([field(true)], "J1")}, "PA_Step2JobView", null, true)
};
process.stdout.write(JSON.stringify(results));
"""

GO_HOME = ["clean", "abandon", "new analysis", "address /"]


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheLogoTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        directory = tempfile.mkdtemp(prefix="paintomics-logo-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"controller": json.dumps(JOB_CONTROLLER)})
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_an_untouched_step_1_goes_home_without_a_question(self):
        result = self.results["untouchedStep1"]
        self.assertIsNone(result["asked"], "asked on a page where nothing would be lost")
        self.assertEqual(result["calls"], GO_HOME)

    def test_pages_with_nothing_entered_go_home_too(self):
        for name in ("blankPage", "myJobs", "destroyedForm", "componentThatIsNoContainer", "emptySlot"):
            with self.subTest(page=name):
                self.assertIsNone(self.results[name]["asked"])
                self.assertEqual(self.results[name]["calls"], GO_HOME)

    def test_no_question_about_a_job_when_none_is_open(self):
        for name in ("organismChosenYes", "toolFormFilledIn", "step1FilledInBehindMyJobs"):
            with self.subTest(page=name):
                asked = self.results[name]["asked"]
                self.assertIsNotNone(asked, "a form with entries in it was cleared without asking")
                self.assertNotIn("job", asked)
                self.assertIn("What you have entered so far will be cleared", asked)

    def test_entries_are_cleared_only_on_yes(self):
        self.assertEqual(self.results["organismChosenYes"]["calls"], GO_HOME)
        self.assertEqual(self.results["organismChosenNo"]["calls"], [], "No still cleared the form")

    def test_an_open_job_is_asked_about_as_before(self):
        for name in ("jobOpenYes", "jobOpenNo", "backOnStep1OfAJob"):
            with self.subTest(page=name):
                self.assertEqual(self.results[name]["asked"], "Are you sure you want to exit the current job?")
        self.assertEqual(self.results["jobOpenYes"]["calls"], GO_HOME)
        self.assertEqual(self.results["jobOpenNo"]["calls"], [])

    def test_a_forced_reset_still_asks_nothing_and_leaves_the_address(self):
        """Signing in and out force it; they reload or set the address themselves."""
        result = self.results["forced"]
        self.assertIsNone(result["asked"])
        self.assertEqual(result["calls"], ["clean", "abandon", "new analysis", "callback"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
