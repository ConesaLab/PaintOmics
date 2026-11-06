#!/usr/bin/env python3
"""A boot that fails says what failed, and does not blame the stored analysis.

Why this exists
---------------
Error report of 2026-10-27 06:48 UTC on paintomics.org:

    PaintOmics AI could not start
    The analysis stored in this browser could not be restored

nginx shows what happened: boot fetched /app/model/JobInstanceModels.js (200),
the next module, FeatureModels.js, never reached the server, and the client's IP
changed in between. A reload 81 s later worked. Nothing was wrong with any
stored analysis, and the report told us nothing we could act on.

`Application.loadModule` was a synchronous $.ajax with no error handling, so a
failed fetch was silent until new MainView() or a model constructor raised a
ReferenceError, and launch()'s catch turned that into the one dialog boot had,
which blamed the same thing whatever had happened.

Pinned here, on the shipped app.js under a stubbed jQuery:

  * a fetch that fails (no answer, or an HTTP error) is asked again a small
    number of times and then throws an error naming the module;
  * a script that arrived and then threw is not asked again and is not called a
    connection problem. jQuery 3.1 reports that as success (it evaluates through
    an injected <script>, so the exception goes to window; see fetchModuleOnce),
    other versions as textStatus "parsererror", and both are covered;
  * the dialog for a load failure names the file and offers a reload, with no
    discard and no Report button;
  * a module that threw gets a reload too and no discard, because launch() has
    read neither store at that point, and its exception goes with the report;
  * the dialog for anything else stops asserting the stored analysis is the
    cause, still offers the discard as something that may help, and puts the
    real exception in the hidden field the Report button sends;
  * launch() goes through all that: it stops at the first module that fails, and
    its catch raises the dialog.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_boot_failure_names_the_real_cause
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

APP_JS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html", "app.js"))

MODULE = "app/model/FeatureModels.js"

# The whole of app.js in a vm context, so what runs is what ships. `respond(opts,
# n, context, raiseOnWindow)` decides what the n-th request for the module does.
# The clock advances on every read, which ends pauseBoot's wait at once and leaves
# in bootClock how long it would have waited.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(%(app)s, "utf8");

function boot(respond) {
    const requests = [];
    const dialogs = [];
    const listeners = new Set();
    const sandbox = {
        debugging: false,
        document: {},
        console: {info() {}, warn() {}, error() {}, log() {}},
        showErrorMessage(title, data) { dialogs.push({kind: "error", title: title, data: data}); },
        showWarningMessage(title, data) { dialogs.push({kind: "warning", title: title, data: data}); },
        addEventListener(type, listener) { if (type === "error") { listeners.add(listener); } },
        removeEventListener(type, listener) { listeners.delete(listener); }
    };
    sandbox.window = sandbox;
    let onReady = null;
    sandbox.$ = function () { return {ready(fn) { onReady = fn; }}; };
    sandbox.Ext = {application(config) { config.launch(); },
                   form: {field: {File: {override() {}}}}};
    const context = vm.createContext(sandbox);
    vm.runInContext(
        "var bootClock = 0;" +
        " Date.now = function () { bootClock += 100; return bootClock; };" +
        " Date.logFormat = function () { return ''; };", context);
    vm.runInContext(source, context);

    const raiseOnWindow = function (event) { listeners.forEach(function (fn) { fn(event); }); };
    sandbox.$.ajax = function (opts) {
        requests.push(opts.url);
        respond(opts, requests.length, context, raiseOnWindow);
    };
    return {sandbox: sandbox, context: context, requests: requests, dialogs: dialogs, listeners: listeners,
            ready: function () { onReady(); }};
}

function plain(dialog) {
    return dialog && {kind: dialog.kind, title: dialog.title, message: dialog.data.message,
                      hidden: dialog.data.hidden, showReportButton: dialog.data.showReportButton,
                      showButton: dialog.data.showButton};
}

/* The whole boot: ready -> Ext.application -> launch() -> its catch. */
function realBoot(respond) {
    const app = boot(respond);
    app.ready();
    return {requests: app.requests.slice(), dialog: plain(app.dialogs[0])};
}

/* Load one module, then hand whatever it threw to the boot dialog, as launch()'s
   catch does. Shorter to read than realBoot when the module is the point. */
function load(respond) {
    const app = boot(respond);
    app.sandbox.application = null;
    let thrown = null;
    try {
        vm.runInContext("new Application().loadModule('app/model/', 'FeatureModels')", app.context);
    } catch (error) {
        thrown = error;
    }
    if (thrown) { app.sandbox.showBootFailureMessage(thrown); }
    return {
        requests: app.requests.length,
        listenersLeft: app.listeners.size,
        threw: thrown && {name: thrown.name, message: thrown.message, moduleFile: thrown.moduleFile},
        dialog: plain(app.dialogs[0]),
        waitedMs: app.sandbox.bootClock
    };
}

const answered = function (status, statusText) { return {status: status, statusText: statusText}; };
const dropped = function (opts) { if (opts.error) { opts.error(answered(0, "NetworkError"), "error", "NetworkError"); } };
const arrived = function (opts) { if (opts.success) { opts.success("", "success", answered(200, "OK")); } };

const results = {};

results.droppedOnce = load(function (opts, n) { if (n === 1) { dropped(opts); } else { arrived(opts); } });
results.alwaysDropped = load(dropped);

/* jQuery 3.1 evaluates the body of an HTTP error too, so nginx's page raises a
   SyntaxError on window before the error callback says 502. */
results.always502 = load(function (opts, n, context, raiseOnWindow) {
    raiseOnWindow({error: vm.runInContext("new SyntaxError(\"Unexpected token '<'\")", context), message: "Uncaught SyntaxError"});
    opts.error(answered(502, "Bad Gateway"), "error", "Bad Gateway");
});

/* jQuery 3.1: the script runs from an injected <script>, the exception goes to
   window, and jQuery then calls it a success. */
results.threwOnWindow = load(function (opts, n, context, raiseOnWindow) {
    raiseOnWindow({error: vm.runInContext("new TypeError('Ext is not ready')", context), message: "Uncaught TypeError"});
    arrived(opts);
});

/* The way other jQuery versions report the same thing. */
results.parserError = load(function (opts, n, context) {
    opts.error(answered(200, "OK"), "parsererror", vm.runInContext("new SyntaxError('Unexpected token')", context));
});

results.bootDropped = realBoot(dropped);

results.bootThrew = realBoot(function (opts, n, context, raiseOnWindow) {
    if (opts.url === %(module)s) {
        raiseOnWindow({error: vm.runInContext("new TypeError('Ext is not ready')", context), message: "Uncaught TypeError"});
    }
    arrived(opts);
});

/* Every module arrives and defines nothing, so launch() itself is what throws. */
results.bootNoMainView = realBoot(arrived);

/* What the boot used to die on when a module was missing, or anything else. */
function otherFailure(expression) {
    const app = boot(arrived);
    app.sandbox.showBootFailureMessage(vm.runInContext(expression, app.context));
    return plain(app.dialogs[0]);
}
results.referenceError = otherFailure("new ReferenceError('MainView is not defined')");
results.nothingThrown = otherFailure("undefined");

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class BootFailureTest(unittest.TestCase):

    results = None

    @classmethod
    def setUpClass(cls):
        scratch = tempfile.mkdtemp(prefix="paintomics_boot_")
        try:
            script = os.path.join(scratch, "harness.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"app": json.dumps(APP_JS), "module": json.dumps(MODULE)})
            done = subprocess.run(["node", script], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)


class ModuleFetchTest(BootFailureTest):

    def test_a_dropped_fetch_is_asked_again_and_the_boot_carries_on(self):
        result = self.results["droppedOnce"]
        self.assertEqual(result["requests"], 2)
        self.assertIsNone(result["threw"], "the retry worked, so there is nothing to report")

    def test_a_fetch_that_keeps_failing_throws_an_error_naming_the_module(self):
        result = self.results["alwaysDropped"]
        self.assertGreater(result["requests"], 1)
        self.assertLessEqual(result["requests"], 5, "a small number of times, on a blocking request")
        self.assertIsNotNone(result["threw"], "a failed fetch is silent again")
        self.assertEqual(result["threw"]["name"], "ModuleFetchError")
        self.assertEqual(result["threw"]["moduleFile"], MODULE)
        self.assertIn(MODULE, result["threw"]["message"])

    def test_an_http_error_is_retried_like_no_answer_at_all(self):
        result = self.results["always502"]
        self.assertEqual(result["requests"], self.results["alwaysDropped"]["requests"])
        self.assertIsNotNone(result["threw"])
        self.assertEqual(result["threw"]["name"], "ModuleFetchError")
        self.assertIn("502", result["threw"]["message"])

    def test_a_script_that_threw_on_window_is_not_a_fetch_failure(self):
        """What jQuery 3.1 does: it reports success and the exception goes to window."""
        result = self.results["threwOnWindow"]
        self.assertEqual(result["requests"], 1, "evaluating it again would throw again")
        self.assertIsNotNone(result["threw"], "a module that threw reads as loaded")
        self.assertEqual(result["threw"]["name"], "TypeError")
        self.assertEqual(result["threw"]["moduleFile"], MODULE)
        self.assertEqual(result["listenersLeft"], 0, "the window listener outlived the request")

    def test_a_parsererror_is_not_a_fetch_failure(self):
        result = self.results["parserError"]
        self.assertEqual(result["requests"], 1)
        self.assertIsNotNone(result["threw"], "a parsererror is silent again")
        self.assertEqual(result["threw"]["name"], "SyntaxError")
        self.assertEqual(result["threw"]["moduleFile"], MODULE)

    def test_the_retries_wait_for_the_connection_but_not_for_long(self):
        """A dead connection fails in milliseconds, and this wait blocks the main thread."""
        waited = self.results["alwaysDropped"]["waitedMs"]
        self.assertGreaterEqual(waited, 1000, "the retries are over before a connection can return")
        self.assertLess(waited, 10000, "the page is frozen for this long")


class RealBootTest(BootFailureTest):
    """launch() and its catch, which everything else in this file only stands in for."""

    def test_launch_stops_at_the_first_module_that_fails_and_says_which(self):
        result = self.results["bootDropped"]
        first = "app/model/JobInstanceModels.js"
        self.assertEqual(set(result["requests"]), {first}, "launch() carried on past a module it never got")
        self.assertGreater(len(result["requests"]), 1, "launch() did not retry it")
        self.assertIsNotNone(result["dialog"], "launch() failed and its catch raised no dialog")
        self.assertIn(first, result["dialog"]["message"])
        self.assertIs(result["dialog"]["showReportButton"], False)

    def test_launch_stops_at_a_module_that_threw_and_offers_a_reload(self):
        result = self.results["bootThrew"]
        self.assertEqual(result["requests"], ["app/model/JobInstanceModels.js", MODULE])
        self.assertIsNotNone(result["dialog"], "launch() failed and its catch raised no dialog")
        self.assertIn("location.reload()", result["dialog"]["message"])
        self.assertNotIn("resetPaintomicsSession", result["dialog"]["message"])
        self.assertIn(MODULE, result["dialog"]["hidden"])

    def test_launch_failing_after_the_modules_offers_the_discard(self):
        result = self.results["bootNoMainView"]
        self.assertEqual(len(result["requests"]), 16, "launch() no longer asks for every module")
        self.assertIsNotNone(result["dialog"], "launch() failed and its catch raised no dialog")
        self.assertIn("resetPaintomicsSession()", result["dialog"]["message"])
        self.assertIn("MainView is not defined", result["dialog"]["hidden"])


class BootDialogTest(BootFailureTest):

    def test_a_dropped_connection_is_told_as_that(self):
        dialog = self.results["alwaysDropped"]["dialog"]
        self.assertIsNotNone(dialog, "a failed fetch raised no dialog at all")
        message = dialog["message"]
        self.assertIn("could not finish loading", message)
        self.assertIn("connection dropped", message)
        self.assertIn(MODULE, message)
        self.assertIn("not affected", message)
        self.assertIn("location.reload()", message)

    def test_a_load_failure_offers_neither_the_discard_nor_a_report(self):
        for case in ("alwaysDropped", "always502"):
            with self.subTest(case=case):
                dialog = self.results[case]["dialog"]
                self.assertIsNotNone(dialog, "a failed fetch raised no dialog at all")
                self.assertNotIn("resetPaintomicsSession", dialog["message"])
                self.assertNotIn("stored analysis", dialog["message"])
                self.assertIs(dialog["showReportButton"], False)
                self.assertIsNot(dialog.get("showButton"), False, "a modal with no way to close it")

    def test_an_http_error_is_not_called_a_dropped_connection(self):
        dialog = self.results["always502"]["dialog"]
        self.assertIsNotNone(dialog, "a failed fetch raised no dialog at all")
        self.assertIn("HTTP 502", dialog["message"])
        self.assertNotIn("connection dropped", dialog["message"])

    def test_a_script_that_threw_is_not_called_a_dropped_connection(self):
        for case in ("threwOnWindow", "parserError"):
            with self.subTest(case=case):
                dialog = self.results[case]["dialog"]
                self.assertIsNotNone(dialog, "a module that threw raised no dialog")
                self.assertNotIn("connection dropped", dialog["message"])
                self.assertIn(MODULE, dialog["hidden"])

    def test_a_script_that_threw_offers_a_reload_and_no_discard(self):
        """launch() has read neither store yet, so discarding them cannot help."""
        for case in ("threwOnWindow", "parserError"):
            with self.subTest(case=case):
                dialog = self.results[case]["dialog"]
                self.assertIn("location.reload()", dialog["message"])
                self.assertNotIn("resetPaintomicsSession", dialog["message"])
                self.assertNotIn("Discard", dialog["message"])
                self.assertIn("not affected", dialog["message"])
                self.assertIsNot(dialog.get("showReportButton"), False, "this one is ours to hear about")
                self.assertIsNot(dialog.get("showButton"), False, "a modal with no way to close it")

    def test_any_other_failure_does_not_assert_the_stored_analysis_is_the_cause(self):
        message = self.results["referenceError"]["message"]
        self.assertNotIn("could not be restored", message)
        self.assertIn("may help", message)
        self.assertIn("resetPaintomicsSession()", message, "the recovery is still offered")

    def test_the_real_exception_reaches_the_report(self):
        dialog = self.results["referenceError"]
        self.assertIn("ReferenceError: MainView is not defined", dialog.get("hidden") or "")
        self.assertIn("    at ", dialog.get("hidden") or "", "no location: a report nobody can act on")
        self.assertIsNot(dialog.get("showReportButton"), False)
        self.assertIsNot(dialog.get("showButton"), False, "a modal with no way to close it")

    def test_a_rejection_with_nothing_in_it_still_says_something(self):
        """.catch(showBootFailureMessage) passes whatever was rejected."""
        self.assertIn("undefined", self.results["nothingThrown"].get("hidden") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
