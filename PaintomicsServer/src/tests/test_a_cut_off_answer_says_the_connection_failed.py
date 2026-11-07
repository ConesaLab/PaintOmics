#!/usr/bin/env python3
"""An answer that did not arrive whole says the connection failed.

Why this exists
---------------
A connection that breaks part-way through a body with no declared length reaches
the page as a 200 holding half a JSON document. jQuery fails that request with
"parsererror", and a request that got no answer at all fails with status 0. Both
then came to ajaxErrorHandler, which read

    Oops..Internal error!
    The server did not send a readable answer (HTTP 200 OK).
    Try running it in private mode or clear your web cache ...

with a Report button. "HTTP 200 OK" is not a fault, the advice cannot help, and a
report of it tells us nothing. Every Step 1 upload goes through a hidden frame and
got the same through extJSErrorHandler, there with "the connection was dropped
before any answer" even when the frame held half the answer.

The rule pinned here: no readable answer and no HTTP error means the connection
failed. The dialog says so, with a link to reload and no Report. An HTTP error, or
a page some proxy wrote into the upload frame, is not that, and keeps the dialog it
had. A readable answer is never that, whatever its status.

A Step 1 upload did not even reach that dialog. ExtJS learns the answer from the
hidden frame's load event, and a connection broken part-way through the answer never
fires it: the frame stays at readyState "loading", and the page said "Uploading
files..." for ever with the form's submit lock held. watchUploadFrame ends that wait
once an answer has begun and stopped growing.

The real handlers from Util.js run under node, down to what goes into the dialog's
elements. The browser check with bodies really cut off on the wire is in the PR.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_a_cut_off_answer_says_the_connection_failed
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests.test_an_invalid_session_is_told_by_its_own_answer import DIALOG_FUNCTIONS, DIALOG_HARNESS
from src.tests.test_status_poll_retries_unreadable_answers import CLIENT_ROOT, UTIL, extract_function, read

CUT_OFF = '{"success":true,"jobID":"abc'
SERVER_ERROR = json.dumps({"success": False, "message": "Exception: AT X.py: f. ERROR MESSAGE: It broke",
                           "extra": {"exc_type": "Exception", "file_name": "X.py"}})
NGINX_502 = "<html><body><center><h1>502 Bad Gateway</h1></center></body></html>"

# The dialog harness up to where it reads its cases, then this file's own: the
# whole response object, where the other suites only vary its body.
HARNESS = DIALOG_HARNESS[:DIALOG_HARNESS.index("const results = {};")] + r"""
const JOB_STATUS_REQUEST_TIMEOUT = 65000;
const results = {};
const answers = %(answers)s;
const forms = %(forms)s;
for (const name of Object.keys(answers)) {
    for (const key of Object.keys(nodes)) { delete nodes[key]; }
    ignoreOtherErrors = false;
    if (name in forms) {
        extJSErrorHandler(null, {failureType: forms[name], response: answers[name]});
    } else {
        ajaxErrorHandler(answers[name]);
    }
    results[name] = {
        kind: nodes["#messageDialog.class"],
        title: nodes["#messageDialogTitle.html"],
        body: nodes["#messageDialogBody.html"],
        report: nodes["#reportErrorButton.display"],
        close: nodes["#messageDialogButton.display"]
    };
}
results.described = {
    cutOff: describeUnansweredRequest({status: 200, statusText: "OK", responseText: %(cutOff)s}),
    noContent: describeUnansweredRequest({status: 204, statusText: "No Content", responseText: ""}),
    frame: describeUnansweredRequest({responseText: %(cutOff)s}),
    dropped: describeUnansweredRequest({status: 0, statusText: "error", responseText: ""}),
    timedOut: describeUnansweredRequest({status: 0, statusText: "timeout"}, "timeout"),
    gateway: describeUnansweredRequest({status: 504, statusText: "Gateway Time-out", responseText: "<html>"})
};
process.stdout.write(JSON.stringify(results));
"""

ANSWERS = {
    "cutOff200": {"status": 200, "statusText": "OK", "responseText": CUT_OFF},
    "empty200": {"status": 200, "statusText": "OK", "responseText": ""},
    "proxyPage200": {"status": 200, "statusText": "OK", "responseText": "<html>Sign in to this network</html>"},
    "dropped": {"status": 0, "statusText": "error", "responseText": ""},
    "partOfABodyThenDropped": {"status": 0, "statusText": "error", "responseText": CUT_OFF},
    "serverError": {"status": 400, "statusText": "BAD REQUEST", "responseText": SERVER_ERROR},
    "nginx502": {"status": 502, "statusText": "Bad Gateway", "responseText": NGINX_502},
    "readable200": {"status": 200, "statusText": "OK", "responseText": SERVER_ERROR},
    "readableWithNoStatus": {"responseText": SERVER_ERROR},
    # Through a form: the upload frame has no status.
    "frameCutOff": {"responseText": CUT_OFF},
    "frameEmpty": {"responseText": ""},
    "frameExtCouldNotRead": {"responseText": '{success:false,message:"Blocked a frame"}'},
    "frameProxyPage": {"responseText": "413 Request Entity Too Large nginx"},
    "frameServerError": {"responseText": SERVER_ERROR},
    "xhrFormCutOff": {"status": 200, "statusText": "OK", "responseText": CUT_OFF},
}
FORMS = {"frameCutOff": "server", "frameEmpty": "server", "frameExtCouldNotRead": "connect",
         "frameProxyPage": "server", "frameServerError": "server", "xhrFormCutOff": "server"}
LOST = ("cutOff200", "empty200", "proxyPage200", "dropped", "partOfABodyThenDropped",
        "frameCutOff", "frameEmpty", "frameExtCouldNotRead", "xhrFormCutOff")
NOT_LOST = ("serverError", "nginx502", "readable200", "readableWithNoStatus", "frameProxyPage", "frameServerError")


def run():
    util = read(UTIL)
    client = []
    for name in DIALOG_FUNCTIONS:
        try:
            client.append(extract_function(util, name, "Util.js"))
        except (AssertionError, ValueError):
            pass   # a helper this tree does not have; the handler then says so
    script = HARNESS % {"client": "\n".join(client), "answers": json.dumps(ANSWERS), "forms": json.dumps(FORMS),
                        "cutOff": json.dumps(CUT_OFF)}
    directory = tempfile.mkdtemp(prefix="paintomics-cutoff-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    if done.returncode != 0:
        raise AssertionError("the harness failed:\n" + done.stderr)
    return json.loads(done.stdout)


def plain(html):
    """A title as a reader reads it: showMessage puts an icon in front."""
    return re.sub(r"<[^>]+>", "", html or "").strip()


# watchUploadFrame on a virtual clock. `timeline` is what the frame's document
# holds at each poll: null for a document this page may not read (a failed
# navigation), or [readyState, length of its text]; the frame is gone after the last.
WATCH_HARNESS = r"""
let clock = 1000, timer = null, cleared = false;
const realDateNow = Date.now;
Date.now = function () { return clock; };
function setInterval(fn, delay) { timer = {fn: fn, delay: delay}; return 7; }
function clearInterval(id) { cleared = id === 7; }
%(watch)s
const results = {};
const timelines = %(timelines)s;
for (const name of Object.keys(timelines)) {
    clock = 1000; timer = null; cleared = false;
    let doc = null, stalledAt = null, stalls = 0;
    const frame = {parentNode: {}, get contentDocument() {
        if (doc === "throws") { throw new Error("Blocked a frame"); }
        return doc;
    }};
    watchUploadFrame(frame, function () { stalls += 1; stalledAt = clock; });
    const start = clock;
    for (const moment of timelines[name]) {
        if (moment === "throws") { doc = "throws"; }
        else if (moment === null) { doc = null; }
        else { doc = {readyState: moment[0], body: {textContent: "x".repeat(moment[1])}}; }
        clock += timer.delay;
        if (!cleared) { timer.fn(); }
    }
    frame.parentNode = null;
    clock += timer.delay;
    if (!cleared) { timer.fn(); }
    results[name] = {stalls: stalls, stalledAfter: stalledAt === null ? null : stalledAt - start, cleared: cleared, poll: timer.delay};
}
process.stdout.write(JSON.stringify(results));
"""

STALLED = [["loading", 19]] * 40
TIMELINES = {
    "stoppedPartWay": STALLED,
    "nothingYet": [["complete", 0]] * 40,
    "failedNavigation": [None] * 40,
    "aDocumentItMayNotRead": ["throws"] * 40,
    "stillGrowing": [["loading", n * 10] for n in range(40)],
    "finishedInTime": [["loading", 19]] * 5 + [["complete", 38]] * 35,
    "stoppedAfterGrowing": [["loading", n * 10] for n in range(10)] + [["loading", 90]] * 30,
    "emptySoFar": [["loading", 0]] * 40,
}


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class UploadFrameTest(unittest.TestCase):
    """An answer that started and stopped ends the wait, and nothing else does."""

    @classmethod
    def setUpClass(cls):
        script = WATCH_HARNESS % {
            "watch": "var UPLOAD_ANSWER_STALL_LIMIT = 15000; var UPLOAD_ANSWER_POLL = 1000;\n" +
                     extract_function(read(UTIL), "watchUploadFrame", "Util.js"),
            "timelines": json.dumps(TIMELINES)}
        directory = tempfile.mkdtemp(prefix="paintomics-frame-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script)
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_an_answer_that_stops_part_way_ends_the_wait_once(self):
        result = self.results["stoppedPartWay"]
        self.assertEqual(result["stalls"], 1)
        self.assertGreaterEqual(result["stalledAfter"], 15000)
        self.assertLess(result["stalledAfter"], 20000)
        self.assertTrue(result["cleared"], "it went on polling a frame already answered for")

    def test_the_time_counts_from_the_last_byte(self):
        result = self.results["stoppedAfterGrowing"]
        self.assertEqual(result["stalls"], 1)
        self.assertGreaterEqual(result["stalledAfter"], 25000)

    def test_no_answer_yet_is_not_a_stalled_answer(self):
        """A large file takes as long as it takes."""
        self.assertEqual(self.results["nothingYet"]["stalls"], 0)

    def test_an_answer_still_coming_is_left_alone(self):
        self.assertEqual(self.results["stillGrowing"]["stalls"], 0)

    def test_a_failed_navigation_is_left_to_its_load_event(self):
        self.assertEqual(self.results["failedNavigation"]["stalls"], 0)
        self.assertEqual(self.results["aDocumentItMayNotRead"]["stalls"], 0)

    def test_an_answer_that_finishes_is_left_to_extjs(self):
        self.assertEqual(self.results["finishedInTime"]["stalls"], 0)

    def test_it_stops_when_extjs_has_taken_the_frame_away(self):
        for name in ("nothingYet", "stillGrowing", "finishedInTime"):
            with self.subTest(name):
                self.assertTrue(self.results[name]["cleared"], "the poll outlived the upload")

    def test_every_upload_is_watched(self):
        """ExtJS_extensions.js wires it to the frame ExtJS's own upload makes."""
        source = read(os.path.join(CLIENT_ROOT, "app", "view", "common", "ExtJS_extensions.js"))
        at = source.index("Ext.override(Ext.data.Connection,")
        block = source[at:]
        self.assertIn("me.callParent(arguments);", block, "the upload itself must still happen")
        self.assertIn("watchUploadFrame(frame,", block)
        self.assertIn("clearListeners()", block, "a load that came late would answer the form twice")
        self.assertIn("me.onUploadComplete(frame, options || {})", block)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class ConnectionFailedTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = run()

    def test_no_whole_answer_says_the_connection_failed(self):
        for name in LOST:
            with self.subTest(name):
                self.assertEqual(plain(self.results[name]["title"]), "The connection failed")

    def test_it_offers_a_reload_and_a_way_to_close_and_no_report(self):
        for name in LOST:
            with self.subTest(name):
                shown = self.results[name]
                self.assertIn("onclick='location.reload(); return false;'>Reload the page</a>", shown["body"])
                self.assertEqual(shown["report"], "none", "a report of a broken connection tells us nothing")
                self.assertNotEqual(shown["close"], "none", "a reload loses what a form holds, so Close stays")

    def test_it_gives_no_advice_about_the_browser_cache(self):
        for name in LOST:
            with self.subTest(name):
                self.assertNotIn("private mode", self.results[name]["body"])
                self.assertNotIn("HTTP 200", self.results[name]["body"])

    def test_what_the_server_said_is_not_a_connection_failure(self):
        for name in NOT_LOST:
            with self.subTest(name):
                self.assertNotEqual(plain(self.results[name]["title"]), "The connection failed")
                self.assertNotEqual(self.results[name]["report"], "none", "these are ours, so Report stays")

    def test_the_words_say_which_it_was(self):
        described = self.results["described"]
        self.assertEqual(described["cutOff"], "the answer arrived cut off or unreadable")
        self.assertEqual(described["noContent"], "the answer arrived cut off or unreadable")
        self.assertEqual(described["frame"], "the answer arrived cut off or unreadable")
        self.assertEqual(described["dropped"], "the connection was dropped before any answer")
        self.assertEqual(described["timedOut"], "no answer within 65 s")
        self.assertEqual(described["gateway"], "HTTP 504 Gateway Time-out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
