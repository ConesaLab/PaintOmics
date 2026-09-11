#!/usr/bin/env python3
"""A status poll that gets no readable answer is retried, not reported.

Why this exists
---------------
`checkJobStatus` (JobController.js) is a self-rescheduling chain: each
still-running answer schedules the next poll. Its `error` branch used to hand
whatever arrived straight to the error handler, and the error handler showed

    Oops..Internal error! Unable to parse the error message.

for anything that was not JSON. nginx's 504 page is not JSON.

2026-09-11 13:07:47 UTC, paintomics.uv.es: the poll for job ia5b7334Y0 took
61,035 ms (the VM was swapping), nginx answered 504 at its default 60 s
`uwsgi_read_timeout`, the dialog above appeared and the chain ended. The job
finished and was stored at 13:09:42 with nobody polling. The user came back
five hours later and redid every step. The report they sent carried the
dialog's text and nothing else, because nothing else had happened.

The rule pinned here: an answer the client can READ -- JSON with a message --
is a verdict about the job and is final. An answer it cannot read (nginx's
502/504 HTML, the empty body of a dropped connection, the request's own
timeout) is a verdict about the wire, and is retried: 5, 10, 20, 40, 60 s, for
five minutes of wall-clock time since the first one, then reported with the
HTTP status and the job's link instead of "Unable to parse". The request
itself times out at 65 s, which is LONGER than the proxy's 60 s on purpose,
so it is the proxy's 504 that ends a stalled poll and never the browser
abandoning a request the server thread is still working on.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_status_poll_retries_unreadable_answers
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
JOB_CONTROLLER = os.path.join(CLIENT_ROOT, "app", "controller", "JobController.js")
UTIL = os.path.join(CLIENT_ROOT, "app", "view", "common", "Util.js")
SERVER_CONF = os.path.join(CLIENT_ROOT, "resources", "ServerConfiguration.js")

POLL_HEADER = "this.checkJobStatus = function (jobID, jobView, callback, other, showURL = false)"
UTIL_FUNCTIONS = ("readableAnswer", "describeUnansweredRequest",
                  "unreadableAnswerMessage", "statusPollRetryDelay")
CONTROLLER_FUNCTIONS = ("acknowledgeJobResult", "unansweredStatusResponse")
CONSTANTS = ("JOB_STATUS_REQUEST_TIMEOUT", "JOB_STATUS_RETRY_FIRST_DELAY",
             "JOB_STATUS_RETRY_MAX_DELAY", "JOB_STATUS_RETRY_BUDGET")

NGINX_504 = ("<html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n"
             "<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n"
             "<hr><center>nginx/1.18.0 (Ubuntu)</center>\r\n</body>\r\n</html>\r\n")
NGINX_502 = NGINX_504.replace("504 Gateway Time-out", "502 Bad Gateway")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_block(source, header, what):
    """`header` plus the brace-matched block that follows it."""
    start = source.find(header)
    if start == -1:
        raise AssertionError("%s is missing from %s" % (header, what))
    opening = source.index("{", start + len(header) - 1)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces after %s" % header)


def extract_function(source, name, what):
    return extract_block(source, "function %s(" % name, what)


def constant_lines(source):
    lines = []
    for name in CONSTANTS:
        for line in source.splitlines():
            if line.startswith(name + " ="):
                lines.append(line.split("/*")[0].strip())
                break
        else:
            raise AssertionError("%s is not defined in ServerConfiguration.js" % name)
    return "\n".join(lines)


# The real function, lifted out and driven by a stubbed jQuery on a virtual
# clock. `respond(opts, n)` decides what the n-th status request does.
# Timers fire in order and each one advances the clock by its delay, so the
# five-minute budget is measured, not approximated.
HARNESS = """
%(constants)s
const SERVER_URL_JOB_STATUS = "/check_job_status";
const SERVER_URL_JOB_RESULT_ACK = "/ack_job_result";
const CHECK_STATUS_TIMEOUT = 5000;
const window = {location: {host: "paintomics.org", pathname: "/"}};
const console = {info() {}, warn() {}, error() {}, log: globalThis.console.log};
// The module-level helpers lifted from JobController.js resolve `$` at module
// scope, as they do in the browser; drive() installs a fresh stub per case.
let $ = null;

%(util)s
%(controller_helpers)s

function drive(respond, options) {
    options = options || {};
    let clock = 0;
    Date.now = function () { return clock; };
    const scheduled = [];
    const statusRequests = [];
    const acks = [];
    const dialogs = [];
    const callbacks = [];
    const handled = [];
    const setTimeout_ = function (fn, delay) { scheduled.push({fn: fn, delay: delay}); return scheduled.length; };

    $ = function () { return {}; };
    $.ajax = function (opts) {
        if (opts.url.indexOf(SERVER_URL_JOB_RESULT_ACK) === 0) {
            acks.push({url: opts.url, delivery: opts.data && opts.data.delivery});
            return;
        }
        statusRequests.push({url: opts.url, timeout: opts.timeout, at: clock});
        respond(opts, statusRequests.length);
    };
    function showInfoMessage(title, data) { dialogs.push({title: title, message: data.message, at: clock}); }
    function ajaxErrorHandler(response) { handled.push({kind: "default", response: response, at: clock}); }

    function Controller() {
        const setTimeout = setTimeout_;
        %(poll)s
    }

    const controller = new Controller();
    const other = options.customHandler
        ? {errorHandler: function (response, jobID, jobView, other) { handled.push({kind: "custom", response: response, at: clock}); }}
        : (options.other || undefined);
    controller.checkJobStatus("JOB1", {}, function (response) { callbacks.push({response: response, at: clock}); },
                              other, options.showURL !== false);

    let fired = 0;
    while (scheduled.length && fired < 60) {
        const next = scheduled.shift();
        fired++;
        clock += next.delay;
        next.fn();
    }
    const parsed = handled.map(function (h) {
        let body = null;
        try { body = JSON.parse(h.response.responseText); } catch (e) { body = null; }
        return {kind: h.kind, at: h.at, status: h.response.status, unanswered: !!h.response.unanswered, body: body};
    });
    return {
        requests: statusRequests.length,
        timeouts: statusRequests.map(function (r) { return r.timeout; }),
        requestTimes: statusRequests.map(function (r) { return r.at; }),
        fired: fired,
        runaway: fired >= 60,
        acks: acks,
        callbacks: callbacks.length,
        dialogs: dialogs,
        handled: parsed,
        clock: clock
    };
}

const results = {};

// What jQuery hands the handlers: the parsed body to `success`, the jqXHR to
// both. A finished or failed answer carries the server's delivery token in
// X-Paintomics-Delivery; nginx's pages and a job that is gone carry none.
function answer(status, statusText, responseText, delivery) {
    return {status: status, statusText: statusText, responseText: responseText,
            getResponseHeader: function (name) {
                return (name === "X-Paintomics-Delivery") ? (delivery || null) : null;
            }};
}
function finished(opts, delivery) {
    const body = {success: true, jobID: "JOB1"};
    opts.success(body, "success", answer(200, "OK", JSON.stringify(body), delivery));
}
function running(opts) {
    const body = {success: false, status: "JobStatus.STARTED", timeSpent: 10, estimatedFinishTime: 0};
    opts.success(body, "success", answer(200, "OK", JSON.stringify(body), null));
}

// THE INCIDENT: one poll answered by nginx's 504 page, the next by the job.
results.one504ThenDone = drive(function (opts, n) {
    if (n === 1) { opts.error(answer(504, "Gateway Time-out", %(nginx504)s), "error"); }
    else { finished(opts, "deliv-1"); }
});

// A 502 from a proxy that reused a closed upstream connection (Drago, 09-08).
results.one502ThenRunning = drive(function (opts, n) {
    if (n === 1) { opts.error(answer(502, "Bad Gateway", %(nginx502)s), "error"); }
    else if (n === 2) { running(opts); }
    else { finished(opts, "deliv-1"); }
});

// A dropped connection: status 0, no body.
results.droppedThenDone = drive(function (opts, n) {
    if (n === 1) { opts.error(answer(0, "error", ""), "error"); }
    else { finished(opts, "deliv-1"); }
});

// The request's own timeout.
results.timeoutThenDone = drive(function (opts, n) {
    if (n === 1) { opts.error(answer(0, "timeout", undefined), "timeout"); }
    else { finished(opts, "deliv-1"); }
});

// A readable refusal: the job is gone. Final, whatever the status code.
const JOB_GONE = JSON.stringify({success: false, status: "failed",
    message: "Your job is not on the queue anymore. Check your job list, if it's not there the process stopped and you must resend the data again."});
results.jobGone = drive(function (opts) {
    opts.error(answer(400, "BAD REQUEST", JOB_GONE), "error");
});
results.jobGoneCustomHandler = drive(function (opts) {
    opts.error(answer(400, "BAD REQUEST", JOB_GONE), "error");
}, {customHandler: true});

// A failed job's own message: also final, and acknowledged.
results.jobFailed = drive(function (opts) {
    opts.error(answer(400, "BAD REQUEST", JSON.stringify(
        {success: false, status: "JobStatus.FAILED", message: "Exception: AT PathwayAcquisitionServlet.py: pathwayAcquisitionStep1_PART2. ERROR MESSAGE: Errors detected in input files"}),
        "deliv-failed"), "error");
});

// A server that never comes back: 504 on every attempt.
results.persistent504 = drive(function (opts) {
    opts.error(answer(504, "Gateway Time-out", %(nginx504)s), "error");
});
results.persistent504CustomHandler = drive(function (opts) {
    opts.error(answer(504, "Gateway Time-out", %(nginx504)s), "error");
}, {customHandler: true});
results.persistent504NoURL = drive(function (opts) {
    opts.error(answer(504, "Gateway Time-out", %(nginx504)s), "error");
}, {showURL: false});

// Every attempt takes the full request timeout to fail: the budget is time,
// so this must give up after far fewer attempts than instant failures do.
results.persistentTimeouts = drive(function (opts) {
    Date.now = (function (previous) { const t = previous() + JOB_STATUS_REQUEST_TIMEOUT; return function () { return t; }; })(Date.now);
    opts.error(answer(0, "timeout", ""), "timeout");
});

// An outage that clears must not be charged against the next one: 504 on
// polls 1-3, running on 4, 504 on 5-7, done on 8. Counted together the seven
// unanswered polls would spend the budget; counted per outage they do not.
results.twoOutages = drive(function (opts, n) {
    if (n === 4) { running(opts); }
    else if (n === 8) { finished(opts, "deliv-1"); }
    else { opts.error(answer(504, "Gateway Time-out", %(nginx504)s), "error"); }
});

// A finished answer that carries no delivery token (an older server): the
// result is used, and nothing is acknowledged, because an acknowledgement
// without a token names nothing.
results.doneWithoutToken = drive(function (opts) {
    finished(opts, null);
});

console.log(JSON.stringify(results));
"""


def run_node(script):
    directory = tempfile.mkdtemp(prefix="paintomics-poll-")
    try:
        path = os.path.join(directory, "check.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        completed = subprocess.run(["node", path], capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode != 0:
            raise AssertionError("node failed:\n%s" % completed.stderr)
        return json.loads(completed.stdout)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class StatusPollRetryTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        util = read(UTIL)
        controller = read(JOB_CONTROLLER)
        cls.results = run_node(HARNESS % {
            "constants": constant_lines(read(SERVER_CONF)),
            "util": "\n".join(extract_function(util, name, "Util.js") for name in UTIL_FUNCTIONS),
            "controller_helpers": "\n".join(
                extract_function(controller, name, "JobController.js") for name in CONTROLLER_FUNCTIONS),
            "poll": extract_block(controller, POLL_HEADER, "JobController.js"),
            "nginx504": json.dumps(NGINX_504),
            "nginx502": json.dumps(NGINX_502),
        })

    # ------------------------------------------------------------------
    # Unreadable answers are retried.
    # ------------------------------------------------------------------

    def test_the_incident_a_504_page_does_not_end_the_chain(self):
        """One nginx 504, then the job's result: the result must arrive."""
        outcome = self.results["one504ThenDone"]
        self.assertEqual(outcome["requests"], 2, "the poll was not retried after the 504")
        self.assertEqual(outcome["callbacks"], 1, "the job's result never reached its callback")
        self.assertEqual(outcome["handled"], [], "the 504 page was reported as an error")

    def test_a_502_page_is_retried_too(self):
        outcome = self.results["one502ThenRunning"]
        self.assertEqual(outcome["callbacks"], 1)
        self.assertEqual(outcome["handled"], [])

    def test_a_dropped_connection_is_retried(self):
        outcome = self.results["droppedThenDone"]
        self.assertEqual(outcome["callbacks"], 1)
        self.assertEqual(outcome["handled"], [])

    def test_the_requests_own_timeout_is_retried(self):
        outcome = self.results["timeoutThenDone"]
        self.assertEqual(outcome["callbacks"], 1)
        self.assertEqual(outcome["handled"], [])

    def test_the_first_retry_is_five_seconds_out(self):
        outcome = self.results["one504ThenDone"]
        self.assertEqual(outcome["requestTimes"], [0, 5000])

    # ------------------------------------------------------------------
    # Readable answers are final.
    # ------------------------------------------------------------------

    def test_a_job_that_is_gone_is_reported_once_and_not_retried(self):
        outcome = self.results["jobGone"]
        self.assertEqual(outcome["requests"], 1, "a readable refusal was retried")
        self.assertEqual(len(outcome["handled"]), 1)
        self.assertEqual(outcome["handled"][0]["body"]["message"][:36], "Your job is not on the queue anymore")
        self.assertFalse(outcome["handled"][0]["unanswered"])

    def test_a_readable_refusal_reaches_the_custom_handler_untouched(self):
        outcome = self.results["jobGoneCustomHandler"]
        self.assertEqual([h["kind"] for h in outcome["handled"]], ["custom"])
        self.assertEqual(outcome["handled"][0]["status"], 400)

    def test_a_failed_jobs_message_is_final_and_acknowledged(self):
        outcome = self.results["jobFailed"]
        self.assertEqual(outcome["requests"], 1)
        self.assertEqual(len(outcome["handled"]), 1)
        self.assertIn("Errors detected in input files", outcome["handled"][0]["body"]["message"])
        self.assertEqual(outcome["acks"], [{"url": "/ack_job_result/JOB1", "delivery": "deliv-failed"}],
                         "the server keeps a failed job's message until the "
                         "client says it has it; nothing said so")

    # ------------------------------------------------------------------
    # The result is acknowledged, so the server can drop its copy.
    # ------------------------------------------------------------------

    def test_a_received_result_is_acknowledged_by_its_delivery_token(self):
        """The token from the answer's X-Paintomics-Delivery header travels
        back. Keyed on the job id alone, a slow acknowledgement for step 1
        could remove step 2's result under the same id (the review finding
        on #155); the server removes only the delivery the token names."""
        outcome = self.results["one504ThenDone"]
        self.assertEqual(outcome["acks"], [{"url": "/ack_job_result/JOB1", "delivery": "deliv-1"}])

    def test_an_answer_without_a_token_is_used_but_not_acknowledged(self):
        outcome = self.results["doneWithoutToken"]
        self.assertEqual(outcome["callbacks"], 1)
        self.assertEqual(outcome["acks"], [])

    def test_a_job_that_is_gone_is_not_acknowledged(self):
        self.assertEqual(self.results["jobGone"]["acks"], [])

    def test_an_unanswered_poll_acknowledges_nothing(self):
        """Acknowledging before the result is in hand would recreate the bug."""
        outcome = self.results["persistent504"]
        self.assertEqual(outcome["acks"], [])

    # ------------------------------------------------------------------
    # The request timeout outlasts the proxy's.
    # ------------------------------------------------------------------

    def test_every_status_request_times_out_after_the_proxy_would(self):
        """65 s against nginx's 60 s default uwsgi_read_timeout on uv.es.

        Shorter, and the browser gives up while the uWSGI thread is still busy
        with that poll, then retries on top of it: two of four threads on one
        client at the moment the server is weakest.
        """
        for name, outcome in self.results.items():
            with self.subTest(case=name):
                self.assertTrue(outcome["timeouts"], "no request was made")
                for timeout in outcome["timeouts"]:
                    self.assertIsNotNone(timeout, "the status request has no timeout")
                    self.assertGreater(timeout, 60000)
                    self.assertLessEqual(timeout, 90000)

    # ------------------------------------------------------------------
    # The budget is five minutes of wall-clock time, then a real report.
    # ------------------------------------------------------------------

    def test_a_server_that_never_answers_is_given_up_on(self):
        outcome = self.results["persistent504"]
        self.assertFalse(outcome["runaway"], "the chain never stopped")
        self.assertEqual(len(outcome["handled"]), 1, "gave up more than once, or never")
        self.assertEqual(outcome["callbacks"], 0)

    def test_the_retries_back_off_5_10_20_40_60(self):
        outcome = self.results["persistent504"]
        times = outcome["requestTimes"]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[:5], [5000, 10000, 20000, 40000, 60000], gaps)
        self.assertTrue(all(gap == 60000 for gap in gaps[5:]), gaps)

    def test_the_budget_is_time_not_attempts(self):
        instant = self.results["persistent504"]
        slow = self.results["persistentTimeouts"]
        self.assertGreaterEqual(instant["handled"][0]["at"], 240000,
                                "gave up before four minutes of instant 504s")
        self.assertLessEqual(instant["handled"][0]["at"], 300000,
                             "kept going past the five-minute budget")
        self.assertLess(slow["requests"], instant["requests"],
                        "attempts that each took 65 s to fail were allowed as "
                        "many tries as instant failures: the budget is a count")
        self.assertLessEqual(slow["handled"][0]["at"], 300000 + 2 * 65000)

    def test_giving_up_says_what_happened_and_where_the_job_is(self):
        """Not "Unable to parse the error message"."""
        outcome = self.results["persistent504"]
        report = outcome["handled"][0]
        self.assertTrue(report["unanswered"])
        message = report["body"]["message"]
        self.assertIn("HTTP 504", message)
        self.assertIn("may still be running", message)
        self.assertIn("?jobID=JOB1", message, "the link back to the job is missing")
        self.assertNotIn("Unable to parse", message)
        self.assertNotIn("Unable to parse", json.dumps(outcome["dialogs"]))

    def test_giving_up_without_a_link_points_at_my_jobs(self):
        outcome = self.results["persistent504NoURL"]
        message = outcome["handled"][0]["body"]["message"]
        self.assertIn("My jobs", message)
        self.assertNotIn("?jobID=", message)

    def test_the_custom_handler_runs_after_the_retries_not_during(self):
        """Callers pass their own handler to unlock the form or count a failed
        conversion. Reached during a retry it would do that while the job was
        still being polled."""
        outcome = self.results["persistent504CustomHandler"]
        self.assertEqual([h["kind"] for h in outcome["handled"]], ["custom"])
        self.assertGreaterEqual(outcome["handled"][0]["at"], 240000)
        self.assertGreater(outcome["requests"], 5)

    def test_the_waiting_dialog_says_why_it_is_still_waiting(self):
        outcome = self.results["persistent504"]
        self.assertTrue(outcome["dialogs"], "the dialog was never updated during the outage")
        self.assertIn("HTTP 504", outcome["dialogs"][0]["message"])
        self.assertIn("Trying again", outcome["dialogs"][0]["message"])

    def test_an_answer_resets_the_budget(self):
        """Seven unanswered polls across two outages, with a running answer
        between them: neither outage alone spends the budget, and the result
        must still arrive."""
        outcome = self.results["twoOutages"]
        self.assertEqual(outcome["callbacks"], 1, "the second outage was charged for the first")
        self.assertEqual(outcome["handled"], [])
        self.assertEqual(outcome["requests"], 8)
        times = outcome["requestTimes"]
        self.assertEqual(times[5] - times[4], 5000,
                         "the second outage did not restart the backoff at 5 s")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
