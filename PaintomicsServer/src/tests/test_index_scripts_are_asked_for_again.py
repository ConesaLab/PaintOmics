#!/usr/bin/env python3
"""A script index.html lists that does not arrive is asked for again, and then named.

Why this exists
---------------
PR #218 gave app.js's loadModule three attempts at each view, model and
controller, after a connection that dropped during boot (2026-10-27) was reported
as a stored analysis that could not be restored. The scripts index.html lists
itself were left as they were: the browser fetched each once. In headless
Chromium against a staging copy, a connection reset on app/view/common/Util.js,
once, left a black page (body background rgb(0, 0, 0)) with "View is not defined"
in the console and nothing on screen, and so did one on ext-all.js or app.js.
Without Util.js the boot failure had no dialog to be shown in.

index.html now opens with a loader. The browser fires `error` on a script it could
not fetch before it runs the next one, so a capturing listener on window asks
again, synchronously, with the same three attempts and pause loadModule uses, and
runs the text in the failed script's place. A script that still has not arrived
is named on the page with a reload button, built from the DOM alone, and app.js
does not boot.

A body cut off on the wire with no declared length looks whole to the browser. It
fires no error at the script: it runs half a file, which raises a SyntaxError on
window, and Util.js lost that way left the same black page. A SyntaxError from one
of these files is treated as a lost file, and asked for again past the HTTP cache,
because these addresses are cached for a long time and the cut copy may be there.

Pinned here: the loader as it ships in index.html, run under node against a
stand-in for the page, and app.js's guard. The same cases ran in headless
Chromium (not Chrome) on staging: Util.js reset once booted to Step 1, Util.js
reset every time or answering 502 showed the message, ext-all.js reset once booted,
and Util.js really cut on the wire, once and every time, each of the three ways a
body can end early.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_index_scripts_are_asked_for_again
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "PaintomicsClient", "public_html"))
INDEX_HTML = os.path.join(CLIENT_ROOT, "index.html")
APP_JS = os.path.join(CLIENT_ROOT, "app.js")

_INLINE = re.compile(r"<script type=\"text/javascript\">(.*?)</script>", re.DOTALL)
_EXTERNAL = re.compile(r"<script[^>]*\ssrc=\"([^\"]+)\"")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def loaderSource():
    """The inline script in index.html that keeps the list of failures, or None."""
    for match in _INLINE.finditer(read(INDEX_HTML)):
        if "PA_BOOT_SCRIPT_FAILURES" in match.group(1):
            return match
    return None


HARNESS = r"""
const vm = require("vm");
const loader = %(loader)s;

function node(tag) {
    const self = {
        tagName: tag.toUpperCase(), children: [], attributes: {}, style: {}, parentNode: null,
        appendChild(child) { child.parentNode = self; self.children.push(child); ran(child); return child; },
        insertBefore(child, before) {
            child.parentNode = self;
            const at = before ? self.children.indexOf(before) : -1;
            if (at === -1) { self.children.push(child); } else { self.children.splice(at, 0, child); }
            ran(child);
            return child;
        },
        setAttribute(name, value) { self.attributes[name] = String(value); },
        getAttribute(name) { return name in self.attributes ? self.attributes[name] : null; },
        focus() { page.focused = self; },
        get textContent() { return (self.text || "") + self.children.map((c) => c.textContent).join(""); },
        get nextSibling() {
            const siblings = self.parentNode ? self.parentNode.children : [];
            return siblings[siblings.indexOf(self) + 1] || null;
        }
    };
    return self;
}
let page = null;
function ran(child) {
    if (child.tagName === "SCRIPT" && child.text) { page.ran.push(child.text); }
}
/* Inside the page, `window` is the context's global and not the sandbox object. */
function pageWindow() { return vm.runInContext("window", page.context); }
function raise(event) {
    (page.listeners.window.error || []).slice().forEach((l) => l.fn(event));
    return page;
}
function syntaxError(filename) {
    return raise({target: pageWindow(), filename: filename, message: "Uncaught SyntaxError: Unexpected end of input"});
}

/* One page load. `answer(n)` is what the n-th request the loader sends gets:
   {status, type, text}, or {drop: true} for a request that throws. */
function load(answer) {
    page = {ran: [], requests: [], reloads: 0, focused: null, clock: 0, listeners: {window: {}, document: {}}};
    const on = (where) => ({
        addEventListener(type, fn, capture) { (page.listeners[where][type] = page.listeners[where][type] || []).push({fn, capture}); },
        removeEventListener(type, fn) { page.listeners[where][type] = (page.listeners[where][type] || []).filter((l) => l.fn !== fn); }
    });
    const head = node("head"), body = node("body");
    const document = Object.assign(on("document"), {
        head: head, body: body,
        createElement: node,
        getElementsByTagName(tag) { return head.children.filter((c) => c.tagName === tag.toUpperCase()); },
        createTextNode(text) { return {textContent: String(text), children: []}; }
    });
    function XMLHttpRequest() {}
    XMLHttpRequest.prototype.open = function (method, url, async) { this.url = url; this.async = async; };
    XMLHttpRequest.prototype.send = function () {
        page.requests.push({url: this.url, async: this.async, at: page.clock});
        const reply = answer(page.requests.length);
        if (reply.drop) { throw new Error("NetworkError: A network error occurred."); }
        this.status = reply.status; this.responseText = reply.text || ""; this.type = reply.type || "";
    };
    XMLHttpRequest.prototype.getResponseHeader = function (name) { return name === "Content-Type" ? this.type : null; };
    const sandbox = Object.assign(on("window"), {
        document: document, XMLHttpRequest: XMLHttpRequest,
        location: {protocol: "https:", host: "paintomics.org", reload() { page.reloads += 1; }}
    });
    sandbox.window = sandbox;
    const context = vm.createContext(sandbox);
    vm.runInContext("Date.now = function () { return (globalThis.__tick = (globalThis.__tick || 0) + 50); };", context);
    page.context = context;
    vm.runInContext(loader, context);
    page.head = head;
    page.body = body;
    page.window = sandbox;
    return page;
}

function scriptTag(src, absolute) {
    const tag = node("script");
    tag.setAttribute("src", src);
    tag.src = absolute;
    return tag;
}

/* The browser could not fetch `tag`: it fires error at it, which reaches the
   capturing listeners on window. */
function fail(tag) {
    page.head.appendChild(tag);
    (page.listeners.window.error || []).forEach((l) => l.fn({target: tag, type: "error"}));
    return page;
}

function domReady() {
    (page.listeners.document.DOMContentLoaded || []).forEach((l) => l.fn({}));
    return page;
}

function seen(p) {
    const box = p.body.children.find((c) => c.attributes && c.id === "bootScriptFailure") || null;
    const find = (n, tag) => n && n.children ? (n.tagName === tag ? n : n.children.map((c) => find(c, tag)).find(Boolean)) : null;
    const button = box ? find(box, "BUTTON") : null;
    return {
        ran: p.ran, requests: p.requests.map((r) => ({url: r.url.replace(/&_=[0-9]+$/, ""), async: r.async})),
        pastTheCache: p.requests.every((r) => /&_=[0-9]+$/.test(r.url)),
        waited: p.requests.length ? vm.runInContext("globalThis.__tick", p.context) : 0,
        failures: vm.runInContext("JSON.parse(JSON.stringify(window.PA_BOOT_SCRIPT_FAILURES))", p.context),
        box: box ? box.textContent : null, role: box ? box.getAttribute("role") : null,
        button: button ? button.textContent : null, focused: !!(button && p.focused === button),
        reloadsOnClick: button ? (button.onclick(), p.reloads) : null,
        copyFollowsTheFailedTag: p.head.children.length > 1 && p.head.children[1].tagName === "SCRIPT" && !!p.head.children[1].text
    };
}

const UTIL = ["app/view/common/Util.js?v=3.6", "https://paintomics.org/app/view/common/Util.js?v=3.6"];
const JS = {status: 200, type: "application/javascript; charset=utf-8", text: "var utilLoaded = true;"};
const DROPPED = {drop: true};
const results = {};

results.droppedOnce = seen(domReady(fail(load(() => JS) && scriptTag(...UTIL))));
results.droppedTwice = seen(domReady(fail(load((n) => n === 1 ? DROPPED : JS) && scriptTag(...UTIL))));
results.alwaysDropped = seen(domReady(fail(load(() => DROPPED) && scriptTag(...UTIL))));
results.always502 = seen(domReady(fail(load(() => ({status: 502, type: "text/html", text: "<html>Bad Gateway</html>"})) && scriptTag(...UTIL))));
results.htmlWithA200 = seen(domReady(fail(load(() => ({status: 200, type: "text/html", text: "<html>portal</html>"})) && scriptTag(...UTIL))));
results.twoLost = (function () {
    load(() => DROPPED);
    fail(scriptTag(...UTIL));
    fail(scriptTag("app.js?v=0.12", "https://paintomics.org/app.js?v=0.12"));
    return seen(domReady());
})();
results.anotherSite = seen(domReady(fail(load(() => JS) && scriptTag("https://cdnjs.cloudflare.com/ajax/libs/mathjs/10.4.2/math.js", "https://cdnjs.cloudflare.com/ajax/libs/mathjs/10.4.2/math.js"))));
results.notAScript = (function () {
    load(() => JS);
    const link = node("link");
    link.src = "https://paintomics.org/resources/css/main.css";
    page.head.appendChild(link);
    (page.listeners.window.error || []).forEach((l) => l.fn({target: link}));
    return seen(domReady());
})();
results.afterTheParse = (function () {
    load(() => DROPPED);
    domReady();
    return seen(fail(scriptTag(...UTIL)));
})();
results.nothingLost = seen(domReady(load(() => JS)));

/* A body cut off with no declared length: nothing fires at the script. */
const CUT = {status: 200, type: "application/javascript", text: "function half( /* CUT OFF */"};
const OWN = "https://paintomics.org/app/view/common/Util.js?v=3.6";
/* The parser has just run `last`, and an error reports on window. By default a
   SyntaxError under the name Util.js gives itself on its first line. */
function cutOff(answer, options) {
    options = options || {};
    load(answer);
    const last = options.last === undefined ? scriptTag("app/view/common/Util.js?v=3.6", OWN) : options.last;
    if (last) { page.head.appendChild(last); }
    if (options.domReadyFirst) { domReady(); }
    raise({target: pageWindow(), filename: options.filename || "Util.js",
           message: options.message || "Uncaught SyntaxError: Unexpected end of input"});
    return options.domReadyFirst ? seen(page) : seen(domReady());
}
results.cutOffOnce = cutOff(() => JS);
results.cutOffUnderItsAddress = cutOff(() => JS, {filename: OWN});
results.cutOffAgainThenWhole = cutOff((n) => n === 1 ? CUT : JS);
results.cutOffEveryTime = cutOff(() => CUT);
results.cutOffThenDropped = cutOff(() => DROPPED);
const inline = node("script");
results.syntaxErrorInAnotherSite = cutOff(() => JS, {last: scriptTag("https://cdn.example/x.js", "https://cdn.example/x.js"), filename: "https://cdn.example/x.js"});
results.syntaxErrorInline = cutOff(() => JS, {last: inline, filename: "https://paintomics.org/"});
results.asyncScriptOfAnotherSite = cutOff(() => JS, {filename: "https://www.googletagmanager.com/gtag/js"});
results.anotherOfOurFilesNamed = cutOff(() => JS, {filename: "app.js"});
results.notASyntaxError = cutOff(() => JS, {message: "Uncaught ReferenceError: View is not defined"});
results.syntaxErrorAfterTheParse = cutOff(() => JS, {domReadyFirst: true});
results.listener = (function () {
    const p = load(() => JS);
    return {capture: (p.listeners.window.error || []).map((l) => l.capture === true)};
})();

/* app.js's ready handler with and without a failure on record. */
const fs = require("fs");
function boot(failures) {
    let onReady = null, launched = false;
    const sandbox = {debugging: false, console: {info() {}, warn() {}, error() {}, log() {}}, document: {}};
    sandbox.window = sandbox;
    if (failures !== undefined) { sandbox.PA_BOOT_SCRIPT_FAILURES = failures; }
    sandbox.$ = function () { return {ready(fn) { onReady = fn; }}; };
    sandbox.Ext = {application() { launched = true; }, form: {field: {File: {override() {}}}}};
    vm.runInContext(fs.readFileSync(%(app)s, "utf8"), vm.createContext(sandbox));
    onReady();
    return launched;
}
results.app = {afterAFailure: boot([{file: "app/view/common/Util.js", status: 0}]), withNone: boot([]), withNoLoader: boot(undefined)};
process.stdout.write(JSON.stringify(results));
"""


class TheLoaderIsWhereItMustBeTest(unittest.TestCase):

    def test_it_comes_before_every_script_it_has_to_cover(self):
        """A script listed above the loader is fetched once, as before."""
        loader = loaderSource()
        self.assertIsNotNone(loader, "index.html has no loader that records failed scripts")
        html = read(INDEX_HTML)
        own = [m for m in _EXTERNAL.finditer(html) if not m.group(1).startswith(("http:", "https:", "//"))]
        self.assertTrue(own)
        self.assertLess(loader.start(), own[0].start(),
                        "%s is listed above the loader" % own[0].group(1))


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheLoaderTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        loader = loaderSource()
        if loader is None:
            raise AssertionError("index.html has no loader that records failed scripts")
        directory = tempfile.mkdtemp(prefix="paintomics-loader-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"loader": json.dumps(loader.group(1)), "app": json.dumps(APP_JS)})
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.results = json.loads(done.stdout)

    def test_a_script_lost_once_is_asked_for_again_and_runs_in_its_place(self):
        result = self.results["droppedOnce"]
        self.assertEqual(result["requests"], [{"url": "https://paintomics.org/app/view/common/Util.js?v=3.6", "async": False}],
                         "asked again, synchronously, so it runs before the next script")
        self.assertEqual(len(result["ran"]), 1)
        self.assertIn("var utilLoaded = true;", result["ran"][0])
        self.assertTrue(result["copyFollowsTheFailedTag"])
        self.assertEqual(result["failures"], [])
        self.assertIsNone(result["box"])

    def test_it_is_asked_up_to_twice_more_like_a_module(self):
        self.assertEqual(len(self.results["droppedTwice"]["requests"]), 2)
        self.assertEqual(len(self.results["droppedTwice"]["ran"]), 1)
        self.assertEqual(len(self.results["alwaysDropped"]["requests"]), 2)

    def test_the_attempts_are_spaced_but_not_for_long(self):
        """A dead connection fails at once; the wait is what gives it time to come back."""
        waited = self.results["alwaysDropped"]["waited"]
        self.assertGreaterEqual(waited, 1500)
        self.assertLess(waited, 5000, "the page is frozen while it waits")

    def test_a_script_that_never_arrives_is_named_with_a_reload_button(self):
        result = self.results["alwaysDropped"]
        self.assertEqual(result["ran"], [])
        self.assertEqual(result["failures"], [{"file": "app/view/common/Util.js", "status": 0, "cutOff": False}])
        self.assertIn("PaintOmics AI could not start", result["box"])
        self.assertIn("because the connection dropped while fetching app/view/common/Util.js.", result["box"])
        self.assertIn("Your analysis and your jobs are not affected.", result["box"])
        self.assertIn("paintomicsai@gmail.com", result["box"])
        self.assertEqual(result["role"], "alert")
        self.assertEqual(result["button"], "Reload the page")
        self.assertTrue(result["focused"])
        self.assertEqual(result["reloadsOnClick"], 1)

    def test_an_http_error_is_told_as_one(self):
        result = self.results["always502"]
        self.assertEqual(result["ran"], [], "nginx's error page ran as the script")
        self.assertIn("the server answered HTTP 502 while fetching app/view/common/Util.js", result["box"])

    def test_a_body_that_is_not_javascript_is_not_run(self):
        """nosniff has the browser refuse it, so the retry does too."""
        self.assertEqual(self.results["htmlWithA200"]["ran"], [])
        self.assertTrue(self.results["htmlWithA200"]["box"])

    def test_every_lost_script_is_named(self):
        self.assertIn("app/view/common/Util.js, app.js", self.results["twoLost"]["box"])

    def test_only_this_sites_scripts_during_the_parse(self):
        for name in ("anotherSite", "notAScript", "afterTheParse"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["requests"], [])
                self.assertEqual(self.results[name]["failures"], [])
                self.assertIsNone(self.results[name]["box"])

    def test_it_asks_past_the_http_cache(self):
        """A copy cut off with no declared length may be in the cache, under an
        address the server says to keep for a long time."""
        self.assertTrue(self.results["droppedTwice"]["pastTheCache"])
        self.assertTrue(self.results["cutOffOnce"]["pastTheCache"])

    def test_a_script_cut_off_on_the_wire_is_asked_for_again(self):
        result = self.results["cutOffOnce"]
        self.assertEqual(result["requests"], [{"url": "https://paintomics.org/app/view/common/Util.js?v=3.6", "async": False}])
        self.assertEqual(len(result["ran"]), 1)
        self.assertIn("var utilLoaded = true;", result["ran"][0])
        self.assertEqual(result["failures"], [])
        self.assertIsNone(result["box"])
        self.assertEqual(len(self.results["cutOffUnderItsAddress"]["requests"]), 1,
                         "a file cut off before its //# sourceURL line reports under its address")

    def test_a_second_cut_is_asked_for_again_too(self):
        result = self.results["cutOffAgainThenWhole"]
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["failures"], [])
        self.assertIsNone(result["box"])

    def test_a_script_cut_off_every_time_is_named(self):
        result = self.results["cutOffEveryTime"]
        self.assertEqual(result["failures"], [{"file": "app/view/common/Util.js", "status": 0, "cutOff": True}])
        self.assertEqual(result["ran"], [], "half a file was run; in a browser that throws out of the listener")
        self.assertEqual(len(result["requests"]), 2, "the same three attempts, the browser's own being the first")
        self.assertIn("because the answer arrived cut off or unreadable while fetching app/view/common/Util.js.", result["box"])
        self.assertEqual(result["button"], "Reload the page")

    def test_a_script_cut_off_and_then_not_answered_is_a_dropped_connection(self):
        result = self.results["cutOffThenDropped"]
        self.assertEqual(result["failures"], [{"file": "app/view/common/Util.js", "status": 0, "cutOff": False}])
        self.assertIn("because the connection dropped", result["box"])

    def test_only_a_syntax_error_in_these_files_during_the_parse_counts(self):
        """A top-level exception in a script that arrived whole is not a lost file:
        pages have run with those. Nor is a SyntaxError in another site's script
        (one that was async can report while ours is the last parsed), inline, under
        another name, or after the parse."""
        for name in ("syntaxErrorInAnotherSite", "syntaxErrorInline", "asyncScriptOfAnotherSite",
                     "anotherOfOurFilesNamed", "notASyntaxError", "syntaxErrorAfterTheParse"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["requests"], [])
                self.assertEqual(self.results[name]["failures"], [])
                self.assertIsNone(self.results[name]["box"])

    def test_a_page_with_nothing_lost_shows_nothing(self):
        self.assertIsNone(self.results["nothingLost"]["box"])
        self.assertEqual(self.results["nothingLost"]["requests"], [])

    def test_the_listener_captures(self):
        """A resource's error does not bubble; only a capturing listener on window sees it."""
        self.assertEqual(self.results["listener"]["capture"], [True])

    def test_app_js_does_not_boot_over_a_lost_script(self):
        self.assertFalse(self.results["app"]["afterAFailure"], "app.js booted over a missing script")
        self.assertTrue(self.results["app"]["withNone"])
        self.assertTrue(self.results["app"]["withNoLoader"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
