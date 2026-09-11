"""The AI status poll must survive a request that does not land.

``pollAIStatus`` (PA_Step3Views.js) is a self-rescheduling chain: each answer
schedules the next poll. It rescheduled only from its ``success`` handler and
declared no ``error`` handler at all, so the chain was one dropped request from
being over -- permanently, and silently.

Measured in Chrome against a real job (2026-08-17): five healthy polls, the
server restarted, the sixth answered ``ERR http=0``, and the page never issued
another status request. The interpretation finished and was stored; the widget
sat at "Generating interpretation..." with an empty message area until the user
reloaded the page. That is the whole of the reported bug -- the finished report
only appearing after a refresh -- and an AI run is 5 to 15 minutes of polling
every 3 seconds, so one blip (a deploy, a sleep/wake, a VPN reconnect) is
enough.

A non-answer is not a verdict about the job. Only ``done``, ``error`` and
``cancelled`` end the chain; everything else must try again.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_status_poll_survives_a_failed_request
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
STEP3_VIEWS = os.path.join(CLIENT_ROOT, "app", "view",
                           "PathwayAcquisitionViews", "PA_Step3Views.js")
UTIL = os.path.join(CLIENT_ROOT, "app", "view", "common", "Util.js")
SERVER_CONF = os.path.join(CLIENT_ROOT, "resources", "ServerConfiguration.js")

HEADER = "this.pollAIStatus = function()"
CLEANUP_HEADER = "this.cleanupAIWidget = function()"
# The poll shares its rule for unreadable answers with the job status poll:
# these come from Util.js, and the numbers from ServerConfiguration.js.
UTIL_FUNCTIONS = ("readableAnswer", "describeUnansweredRequest", "statusPollRetryDelay")
CONSTANTS = ("JOB_STATUS_REQUEST_TIMEOUT", "JOB_STATUS_RETRY_FIRST_DELAY",
             "JOB_STATUS_RETRY_MAX_DELAY", "JOB_STATUS_RETRY_BUDGET")

NGINX_504 = ("<html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n"
             "<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n"
             "<hr><center>nginx/1.18.0 (Ubuntu)</center>\r\n</body>\r\n</html>\r\n")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_block(source, header):
    """`header` plus the brace-matched block that follows it."""
    start = source.find(header)
    if start == -1:
        raise AssertionError("%s is missing from PA_Step3Views.js" % header)
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


def extract_function(source, name):
    return extract_block(source, "function %s(" % name)


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


# The real function, lifted out and driven by a stubbed jQuery. `respond`
# decides what the single ajax call does, which is the only variable here.
HARNESS = """
%(constants)s
%(util)s
const console = {info() {}, warn() {}, error() {}, log: globalThis.console.log};
const results = {};
// Installed per drive(); module-scoped so the cleanup case below can build a
// View outside any drive.
let $ = function () { return {hide: function () {}}; };
let View = null;

// `chain` false runs a single tick, which is all most of these need. `chain`
// true keeps firing whatever the poll scheduled, up to a hard ceiling, which is
// how "it eventually stops" can be asserted at all -- a stubbed setTimeout that
// only records can never distinguish "stopped" from "scheduled once more".
// Firing a timer advances a virtual clock by its delay, so the five-minute
// budget for unreadable answers is measured rather than approximated.
function drive(respond, chain) {
    const scheduled = [];
    const calls = [];
    const shown = [];
    const timeouts = [];
    let clock = 0;
    Date.now = function () { return clock; };
    const setTimeout_ = function (fn, delay) { scheduled.push({fn: fn, delay: delay}); return scheduled.length; };
    const AI_POLL_INTERVAL = 3000;
    const AI_POLL_MAX_FAILURES = 5;
    const SERVER_URL_AI_INTERPRET_STATUS = "/ai_interpret_status";

    $ = function () { return {hide: function () {}}; };
    $.ajax = function (opts) {
        calls.push(opts.url);
        timeouts.push(opts.timeout);
        respond(opts, calls.length);
    };

    View = function () {
        this.aiWidget = {updateProgress: function (status, percent, detail) {
            shown.push({status: status, detail: String(detail || "")});
        }, destroy: function () {}};
        this.getModel = function () { return {getJobID: function () { return "JOB"; }}; };
        const setTimeout = setTimeout_;
        const clearTimeout = function () {};
        %(poll)s
        %(cleanup)s
    }

    const view = new View();
    view.pollAIStatus();

    if (chain) {
        // A runaway chain is the failure being tested for, so the ceiling is
        // well above AI_POLL_MAX_FAILURES and reaching it counts as "never
        // stopped".
        let fired = 0;
        const delays = [];
        while (scheduled.length && fired < 40) {
            const next = scheduled.shift();
            fired++;
            delays.push(next.delay);
            clock += next.delay;
            next.fn();
        }
        return {polls: calls.length, stopped: scheduled.length === 0 && fired < 40,
                delays: delays, timeouts: timeouts, clock: clock,
                shown: shown, lastDetail: shown.length ? shown[shown.length - 1].detail : null,
                lastStatus: shown.length ? shown[shown.length - 1].status : null};
    }

    return {polls: calls.length, scheduled: scheduled.length,
            firstDelay: scheduled.length ? scheduled[0].delay : null,
            timeouts: timeouts, shown: shown};
}

// A request that never landed: jQuery calls `error`, or nothing at all when no
// error handler was declared.
results.transportFailure = drive(function (opts) {
    if (opts.error) { opts.error({status: 0}); }
});

// The server answered, but with success:false (the servlet does this for any
// handled exception -- an expired session, a Mongo hiccup).
results.unsuccessfulAnswer = drive(function (opts) {
    opts.success({success: false});
});

// Healthy in-progress answer: the chain must continue.
results.stillRunning = drive(function (opts) {
    opts.success({success: true, status: "interpreting", percent: 45, detail: "..."});
});

// Terminal answers: the chain must stop.
results.done = drive(function (opts) {
    opts.success({success: true, status: "done", percent: 100, detail: "Ready"});
});
results.cancelled = drive(function (opts) {
    opts.success({success: true, status: "cancelled", percent: 0, detail: ""});
});
results.errored = drive(function (opts) {
    opts.success({success: true, status: "error", percent: 0, detail: "boom"});
});

// THE JOB IS GONE. A reopened job is drawn from the browser's own copy, so the
// page shows it without asking the server; only the AI report asks. Once the
// job has been removed the answer is a refusal, and asking again cannot change
// it -- this is the "Starting..." for ever that was reported.
// Captured verbatim from the running server, because the shape is the point:
// handleException renders a UserWarning as HTTP 400 with the message in the
// body, so jQuery routes it to `error`. Asserting against a success:false body
// tests a path this case never takes -- which is how the first attempt at this
// fix passed its tests and changed nothing in the browser.
const JOB_GONE_BODY = JSON.stringify({
    success: false,
    message: "UserWarning: AT AIInterpretServlet.py: aiInterpretStatus. " +
             "ERROR MESSAGE: Job r0l53602VQ was not found."
});
const JOB_REFUSED_BODY = JSON.stringify({
    success: false,
    message: "UserWarning: AT AIInterpretServlet.py: aiInterpretStatus. " +
             "ERROR MESSAGE: Invalid Job ID (r0l53602VQ) for current user."
});

results.jobDeleted = drive(function (opts) {
    opts.error({status: 400, responseText: JOB_GONE_BODY});
}, true);

results.jobRefused = drive(function (opts) {
    opts.error({status: 400, responseText: JOB_REFUSED_BODY});
}, true);

// A 400 it cannot classify: worth retrying, but not for ever.
results.persistentUnknownFailure = drive(function (opts) {
    opts.error({status: 400, responseText: JSON.stringify(
        {success: false, message: "some transient database problem"})});
}, true);

// The same failure, but it clears before the ceiling: the chain must recover
// and go on to deliver the report rather than having burned its budget.
results.recoversAfterTwoFailures = drive(function (opts, n) {
    if (n <= 2) { opts.error({status: 500, responseText: "{}"}); }
    else { opts.success({success: true, status: "done", percent: 100, detail: "Ready"}); }
}, true);

// A session that expired must keep its own message, not be relabelled.
results.sessionExpired = drive(function (opts) {
    opts.error({status: 400, responseText: JSON.stringify(
        {success: false, message: "CredentialException: User not valid. please log-in again."})});
}, true);

// AN UNREADABLE ANSWER. nginx's 504 page is what the job status poll died on
// (2026-09-11, paintomics.uv.es); this poll goes through the same proxy and
// must follow the same rule: retry on a clock, then say so.
const NGINX_504 = %(nginx504)s;
results.one504ThenDone = drive(function (opts, n) {
    if (n === 1) { opts.error({status: 504, statusText: "Gateway Time-out", responseText: NGINX_504}, "error"); }
    else { opts.success({success: true, status: "done", percent: 100, detail: "Ready"}); }
}, true);

results.persistent504 = drive(function (opts) {
    opts.error({status: 504, statusText: "Gateway Time-out", responseText: NGINX_504}, "error");
}, true);

// Five 504s, then a healthy answer, then five more: an outage that cleared
// must not be charged against the next one.
results.twoOutages = drive(function (opts, n) {
    if (n === 6) { opts.success({success: true, status: "interpreting", percent: 45, detail: "..."}); }
    else if (n === 12) { opts.success({success: true, status: "done", percent: 100, detail: "Ready"}); }
    else { opts.error({status: 504, statusText: "Gateway Time-out", responseText: NGINX_504}, "error"); }
}, true);

// The view is reused across jobs: cleanupAIWidget() tears the chain down
// between them and must take the chain's bookkeeping with it, or job B is
// charged for job A's outage (the second review finding on #155).
(function () {
    const view = new View();
    view.pollTimerID = 7;
    view.aiPollOutage = {since: 1, attempts: 4};
    view.aiPollFailures = 4;
    view.cleanupAIWidget();
    results.cleanup = {outage: view.aiPollOutage, failures: view.aiPollFailures,
                       timer: view.pollTimerID, widget: view.aiWidget};
})();

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
class StatusPollChainTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        block = extract_block(read(STEP3_VIEWS), HEADER)
        util = read(UTIL)
        cls.results = run_node(HARNESS % {
            "poll": block,
            "cleanup": extract_block(read(STEP3_VIEWS), CLEANUP_HEADER),
            "constants": constant_lines(read(SERVER_CONF)),
            "util": "\n".join(extract_function(util, name) for name in UTIL_FUNCTIONS),
            "nginx504": json.dumps(NGINX_504),
        })

    def test_a_dropped_request_does_not_end_the_chain(self):
        """The measured bug: one ERR http=0 and the widget never updates again."""
        outcome = self.results["transportFailure"]
        self.assertEqual(
            outcome["scheduled"], 1,
            "a status request that did not land scheduled no retry, so the "
            "poll chain is dead: the report will only appear if the user "
            "reloads the page")

    def test_an_unsuccessful_answer_does_not_end_the_chain(self):
        outcome = self.results["unsuccessfulAnswer"]
        self.assertEqual(
            outcome["scheduled"], 1,
            "success:false ended the poll chain; the servlet returns that for "
            "any handled exception, which says nothing about the job")

    def test_a_running_job_keeps_polling(self):
        self.assertEqual(self.results["stillRunning"]["scheduled"], 1)

    def test_terminal_states_stop_the_chain(self):
        for state in ("done", "cancelled", "errored"):
            with self.subTest(state=state):
                self.assertEqual(
                    self.results[state]["scheduled"], 0,
                    "%s is terminal; polling on after it wastes requests "
                    "forever" % state)

    def test_the_retry_is_not_faster_than_the_normal_cadence(self):
        """A failing server must not be hammered harder than a healthy one."""
        delay = self.results["transportFailure"]["firstDelay"]
        self.assertIsNotNone(delay, "no retry was scheduled at all")
        self.assertGreaterEqual(delay, 3000, "retry is faster than AI_POLL_INTERVAL")

    # ------------------------------------------------------------------
    # A deleted job. The other half of the same bug: the chain surviving a
    # refusal it can never recover from is not resilience, it is a hang.
    # ------------------------------------------------------------------

    def test_a_deleted_job_stops_the_chain(self):
        """The reported bug: reopen an expired job, "Starting..." for ever.

        The page is drawn from the copy the browser keeps in sessionStorage, so
        it never asks the server whether the job still exists; only the AI
        report does. Measured before this: four polls in twenty seconds, still
        going, empty panel, no message.
        """
        outcome = self.results["jobDeleted"]
        self.assertTrue(outcome["stopped"],
                        "the poll kept going against a job that no longer "
                        "exists; it can never succeed")
        self.assertEqual(outcome["polls"], 1,
                         "asked more than once about a job that is gone")

    def test_a_deleted_job_says_so(self):
        """Stopping silently would leave the same empty panel, just quieter."""
        outcome = self.results["jobDeleted"]
        self.assertIn("no longer stored on the server", outcome["lastDetail"])
        self.assertEqual(
            outcome["lastStatus"], "unavailable",
            "reported as a plain error, which renders a Retry button -- and "
            "retrying re-posts to /ai_interpret_initiate, which is refused for "
            "the same reason. A button that cannot work reads as 'we could fix "
            "this if you asked again'")
        self.assertRegex(outcome["lastDetail"], r"7 days.*14 days",
                         "the message should say what the retention actually "
                         "is, since that is why the job is gone")

    def test_a_refused_job_stops_the_chain(self):
        """"Invalid Job ID ... for current user" is equally permanent."""
        outcome = self.results["jobRefused"]
        self.assertTrue(outcome["stopped"])
        self.assertEqual(outcome["polls"], 1)

    def test_an_unclassifiable_failure_gives_up_eventually(self):
        outcome = self.results["persistentUnknownFailure"]
        self.assertTrue(outcome["stopped"],
                        "a server that has stopped answering is polled for "
                        "ever, showing nothing")
        self.assertLessEqual(outcome["polls"], 6,
                             "gave up later than AI_POLL_MAX_FAILURES")
        self.assertEqual(outcome["lastStatus"], "error")
        self.assertIn("stopped answering", outcome["lastDetail"])

    def test_an_expired_session_keeps_its_own_message(self):
        """The two permanent cases must not be confused for one another.

        "your session expired, sign in again" and "this job no longer exists"
        call for different actions from the user, and the second regex must not
        swallow the first.
        """
        outcome = self.results["sessionExpired"]
        self.assertTrue(outcome["stopped"])
        self.assertIn("session expired", outcome["lastDetail"])
        self.assertNotIn("no longer stored", outcome["lastDetail"])

    # ------------------------------------------------------------------
    # An answer the client cannot read: nginx's 502/504 page, an empty body,
    # the request's own timeout. Same rule as the job status poll
    # (test_status_poll_retries_unreadable_answers): retried on a clock, for
    # five minutes since the first one, then reported with the HTTP status.
    # ------------------------------------------------------------------

    def test_a_504_page_does_not_end_the_chain(self):
        outcome = self.results["one504ThenDone"]
        self.assertTrue(outcome["stopped"])
        self.assertEqual(outcome["lastStatus"], "done",
                         "the report never arrived after one 504")
        self.assertEqual(outcome["polls"], 2)

    def test_unreadable_answers_back_off_5_10_20_40_60(self):
        outcome = self.results["persistent504"]
        self.assertEqual(outcome["delays"][:5], [5000, 10000, 20000, 40000, 60000],
                         outcome["delays"])

    def test_a_proxy_that_never_answers_is_given_up_on_by_the_clock(self):
        outcome = self.results["persistent504"]
        self.assertTrue(outcome["stopped"], "the poll never stopped")
        self.assertGreaterEqual(outcome["clock"], 240000,
                                "gave up before four minutes: the budget is a count")
        self.assertLessEqual(outcome["clock"], 300000)
        self.assertEqual(outcome["lastStatus"], "error")
        self.assertIn("HTTP 504", outcome["lastDetail"])
        self.assertIn("may still be running", outcome["lastDetail"])

    def test_an_answer_resets_the_unreadable_budget(self):
        outcome = self.results["twoOutages"]
        self.assertEqual(outcome["lastStatus"], "done",
                         "the second outage was charged for the first")
        self.assertEqual(outcome["polls"], 12)
        # delays[5] is the healthy answer's normal cadence; the second
        # outage that follows starts its backoff over at 5 s.
        self.assertEqual(outcome["delays"][5], 3000, outcome["delays"])
        self.assertEqual(outcome["delays"][6], 5000, outcome["delays"])

    def test_every_status_request_times_out_after_the_proxy_would(self):
        """65 s against nginx's 60 s default, so the proxy's 504 -- which is
        retried -- ends a stalled poll, never the browser abandoning a
        request the server thread is still busy with."""
        for name, outcome in self.results.items():
            if "timeouts" not in outcome:      # the cleanup case makes no request
                continue
            with self.subTest(case=name):
                for timeout in outcome["timeouts"]:
                    self.assertIsNotNone(timeout, "the status request has no timeout")
                    self.assertGreater(timeout, 60000)

    def test_tearing_the_widget_down_takes_the_chains_bookkeeping_with_it(self):
        """The view is reused across jobs. cleanupAIWidget() cancels the
        retry timer -- the one thing that would have let the chain reset its
        own state -- so it must reset that state itself, or job B's first
        unreadable answer is measured against job A's outage and gives up at
        once."""
        outcome = self.results["cleanup"]
        self.assertIsNone(outcome["outage"], "the outage survived the teardown")
        self.assertEqual(outcome["failures"], 0, "the refusal count survived the teardown")
        self.assertIsNone(outcome["timer"])
        self.assertIsNone(outcome["widget"])

    def test_a_failure_that_clears_does_not_consume_the_budget(self):
        """Two blips then success must still deliver the report.

        The counter has to reset on a good answer, or a long run with
        occasional hiccups would exhaust its allowance and give up on a job
        that was working.
        """
        outcome = self.results["recoversAfterTwoFailures"]
        self.assertTrue(outcome["stopped"])
        self.assertEqual(outcome["lastStatus"], "done",
                         "the chain gave up instead of recovering")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
