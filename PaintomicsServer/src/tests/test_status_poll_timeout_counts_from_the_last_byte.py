"""The job status poll's timeout must count from the last byte, not the first.

A finished Step 2 comes back as ONE status answer of about 1 MB gzipped. The
poll used jQuery's `timeout`, which is wall-clock time for the whole request,
so on a slow connection the answer was still arriving when the timer ran out.

2026-09-18, paintomics.org, job Ap16613d5J: five polls in five minutes, each
logged by nginx as 200 with 0.8-1.06 MB of the 1.12 MB sent when the browser
gave up at 65 s. The chain then reported "The server did not answer the status
check ... (no answer within 65 s)" and the user filed an error report --
although the server had answered every time, in milliseconds. The same user
lost two more jobs that way the same morning.

`ajaxWithStallTimeout` (Util.js) keeps the 65 s limit for a server that sends
nothing, and restarts it on every download progress event. These cases drive
the function itself in node against a jQuery stand-in and a virtual clock.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_status_poll_timeout_counts_from_the_last_byte
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html"))
UTIL = os.path.join(CLIENT_ROOT, "app", "view", "common", "Util.js")
JOB_CONTROLLER = os.path.join(CLIENT_ROOT, "app", "controller", "JobController.js")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_function(source, name):
    header = "function %s(" % name
    start = source.find(header)
    if start == -1:
        raise AssertionError("%s is missing from Util.js" % name)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces after %s" % header)


HARNESS = r"""
// A virtual clock: the function under test reaches setTimeout/clearTimeout at
// module scope, so these shadow node's real timers for it.
let clock = 0;
let timers = [];
let nextId = 1;
function setTimeout(fn, delay) { const id = nextId++; timers.push({id: id, at: clock + delay, fn: fn}); return id; }
function clearTimeout(id) { timers = timers.filter(function (t) { return t.id !== id; }); }
function advanceTo(t) {
    for (;;) {
        timers.sort(function (a, b) { return a.at - b.at; });
        if (!timers.length || timers[0].at > t) { break; }
        const next = timers.shift();
        clock = next.at;
        next.fn();
    }
    clock = t;
}

// The part of jQuery the wrapper touches, with jQuery 3.1's semantics:
// jqXHR.abort(text) ends the request with textStatus `text`; `always` runs
// after success or error; a `timeout` of 0 means none.
let lastRequest = null;
const $ = {
    extend: function (target) {
        for (let i = 1; i < arguments.length; i++) { Object.assign(target, arguments[i]); }
        return target;
    },
    ajaxSettings: {xhr: function () {
        const listeners = {};
        return {
            addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
            emit: function (type) { (listeners[type] || []).forEach(function (fn) { fn({}); }); }
        };
    }},
    ajax: function (opts) {
        const xhr = opts.xhr ? opts.xhr() : null;
        const always = [];
        let ended = null;
        const finish = function (outcome) {
            if (ended) { return; }
            ended = outcome;
            always.forEach(function (fn) { fn(); });
        };
        const jqXHR = {
            status: 0, statusText: "",
            abort: function (text) {
                if (ended) { return; }
                jqXHR.statusText = text || "abort";
                if (opts.error) { opts.error(jqXHR, jqXHR.statusText); }
                finish({kind: "error", textStatus: jqXHR.statusText, at: clock});
            },
            always: function (fn) { always.push(fn); return jqXHR; }
        };
        lastRequest = {
            opts: opts, xhr: xhr, jqXHR: jqXHR,
            byte: function () { xhr.emit("progress"); },
            complete: function () {
                if (ended) { return; }
                if (opts.success) { opts.success({}, "success", jqXHR); }
                finish({kind: "success", at: clock});
            },
            outcome: function () { return ended; }
        };
        return jqXHR;
    }
};

%(util)s

const LIMIT = 65000;
function start(extra) {
    clock = 0; timers = [];
    const seen = [];
    const settings = Object.assign({
        url: "/check_job_status/JOB1", timeout: LIMIT,
        error: function (jqXHR, textStatus) { seen.push({kind: "error", textStatus: textStatus, at: clock}); },
        success: function () { seen.push({kind: "success", at: clock}); }
    }, extra || {});
    ajaxWithStallTimeout(settings);
    return {req: lastRequest, seen: seen};
}

const results = {};

// A server that sends nothing still fails at exactly the old limit.
(function () {
    const run = start();
    advanceTo(LIMIT - 1);
    const before = run.seen.length;
    advanceTo(LIMIT);
    results.silent = {before: before, seen: run.seen, jqueryTimeout: run.req.opts.timeout};
})();

// THE INCIDENT: 1.12 MB at ~15 kB/s is ~75 s of steady bytes. One progress
// event every 5 s for 180 s, then the answer completes.
(function () {
    const run = start();
    for (let t = 5000; t <= 180000; t += 5000) { advanceTo(t); run.req.byte(); }
    run.req.complete();
    advanceTo(1000000);
    results.trickle = {seen: run.seen, timersLeft: timers.length};
})();

// Bytes flow for 30 s, then stop: cut off 65 s after the LAST byte.
(function () {
    const run = start();
    for (let t = 10000; t <= 30000; t += 10000) { advanceTo(t); run.req.byte(); }
    advanceTo(1000000);
    results.stalled = {seen: run.seen};
})();

// A fast answer leaves no timer behind to abort a finished request.
(function () {
    const run = start();
    advanceTo(120); run.req.byte(); run.req.complete();
    const left = timers.length;
    advanceTo(1000000);
    results.fast = {seen: run.seen, timersLeft: left};
})();

// No timeout asked for: the settings go to $.ajax untouched.
(function () {
    clock = 0; timers = [];
    const settings = {url: "/x"};
    ajaxWithStallTimeout(settings);
    results.untimed = {sameObject: lastRequest.opts === settings, hasXhr: !!lastRequest.opts.xhr,
                       timers: timers.length};
})();

// A caller's own xhr factory is still the one that builds the request.
(function () {
    let used = 0;
    const own = function () { used++; return $.ajaxSettings.xhr(); };
    const run = start({xhr: own});
    advanceTo(60000); run.req.byte(); advanceTo(100000);
    run.req.complete();
    results.ownXhr = {used: used, seen: run.seen};
})();

console.log(JSON.stringify(results));
"""


class StatusPollTimeoutCountsFromTheLastByte(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if node is None:
            raise unittest.SkipTest("node is not installed")
        script = HARNESS % {"util": extract_function(read(UTIL), "ajaxWithStallTimeout")}
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            path = handle.name
        try:
            done = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        if done.returncode != 0:
            raise AssertionError("node failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_silent_server_still_times_out_at_the_limit(self):
        silent = self.results["silent"]
        self.assertEqual(silent["before"], 0, "cut off before the limit")
        self.assertEqual(silent["seen"], [{"kind": "error", "textStatus": "timeout", "at": 65000}])

    def test_the_timeout_is_reported_as_a_timeout(self):
        # describeUnansweredRequest and the retry branch key on this string.
        self.assertEqual(self.results["silent"]["seen"][0]["textStatus"], "timeout")

    def test_jquerys_own_wall_clock_timeout_is_off(self):
        self.assertEqual(self.results["silent"]["jqueryTimeout"], 0)

    def test_a_slow_answer_that_keeps_arriving_completes(self):
        trickle = self.results["trickle"]
        self.assertEqual(trickle["seen"], [{"kind": "success", "at": 180000}],
                         "a 180 s download with bytes every 5 s was cut off")
        self.assertEqual(trickle["timersLeft"], 0)

    def test_an_answer_that_stops_arriving_is_cut_off_after_the_last_byte(self):
        self.assertEqual(self.results["stalled"]["seen"],
                         [{"kind": "error", "textStatus": "timeout", "at": 30000 + 65000}])

    def test_a_finished_request_leaves_no_timer(self):
        fast = self.results["fast"]
        self.assertEqual(fast["seen"], [{"kind": "success", "at": 120}])
        self.assertEqual(fast["timersLeft"], 0)

    def test_without_a_timeout_the_settings_pass_through(self):
        untimed = self.results["untimed"]
        self.assertTrue(untimed["sameObject"])
        self.assertFalse(untimed["hasXhr"])
        self.assertEqual(untimed["timers"], 0)

    def test_a_callers_own_xhr_factory_is_kept(self):
        own = self.results["ownXhr"]
        self.assertEqual(own["used"], 1)
        self.assertEqual(own["seen"], [{"kind": "success", "at": 100000}])

    def test_the_job_status_poll_uses_it(self):
        source = read(JOB_CONTROLLER)
        start = source.index("this.checkJobStatus = function")
        body = source[start:source.index("url: SERVER_URL_JOB_STATUS", start)]
        self.assertIn("ajaxWithStallTimeout({", body,
                      "checkJobStatus sends its poll with a wall-clock timeout again")


if __name__ == "__main__":
    unittest.main()
