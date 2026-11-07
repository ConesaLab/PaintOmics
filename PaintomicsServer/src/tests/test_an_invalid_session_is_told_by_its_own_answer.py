#!/usr/bin/env python3
"""Only the server's answer for an invalid session signs the reader out.

Why this exists
---------------
ajaxErrorHandler and extJSErrorHandler showed "Invalid session", with no Close
button, and then cleared the session cookies and reloaded the page, for any error
whose message contained "User not valid" anywhere. Messages quote the user's own
data. A refusal of a file whose identifiers included the words "User not valid"
(explainEmptyMapping quotes up to three unmatched ones) signed its reader out
instead of saying what was wrong with the file, and a crash quoting a cell the
same way did too.

The answer that means it is one sentence, raised by UserSessionManager.isValidUser
as a CredentialException and framed by handleException. Both handlers now ask for
that exception and that opening, through isInvalidSessionAnswer.

Every body below is written by the real server code; the handlers are the real
ones from Util.js, under node.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_an_invalid_session_is_told_by_its_own_answer
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.PathwayAcquisitionJob import explainEmptyMapping
from src.common.ServerErrorManager import CredentialException, InputFileError, handleException
from src.common.UserSessionManager import UserSessionManager
from src.paintomicsserver import Response
from src.tests.test_status_poll_retries_unreadable_answers import UTIL, extract_function, read

WORDS = "User not valid"


def _answerTo(exception, userID="17"):
    """The body handleException really writes for this exception."""
    response = Response()
    try:
        raise exception
    except Exception as ex:
        handleException(response, ex, "PathwayAcquisitionServlet.py", "step", userID=userID)
    return response.content


# The real ajaxErrorHandler and the dialog functions it calls, down to what goes
# into the dialog's elements. `#messageDialog.class` is the type the dialog is
# drawn as: the class showMessage adds to it.
DIALOG_HARNESS = r"""
const nodes = {};
function element(selector) {
    const self = {
        html(value) { nodes[selector + ".html"] = value; return self; },
        text(value) { nodes[selector + ".text"] = value; return self; },
        css(value) { nodes[selector + ".display"] = value.display; return self; },
        removeClass() { return self; },
        addClass(value) { nodes[selector + ".class"] = value; return self; },
        hasClass() { return false; },
        show() { return self; }, hide() { return self; }, focus() { return self; },
        outerHeight() { return 100; }
    };
    return self;
}
const $ = element;
$.wait = function (fn) { fn(); };
Date.logFormat = function () { return ""; };
const dialog = {setWidth() {}, setHeight() {}, getHeight() { return 100; }, center() {}, show() {}, hide() {},
                isVisible() { return true; }, body: null, el: {focus() {}}};
const entities = {"&": "&amp;", ">": "&gt;", "<": "&lt;", '"': "&quot;", "'": "&#39;"};
const Ext = {getCmp() { return dialog; },
             form: {action: {Action: {CLIENT_INVALID: "client"}}},
             String: {htmlEncode(text) { return String(text).replace(/[&<>"']/g, function (c) { return entities[c]; }); }}};
console.error = console.warn = console.info = function () {};
var debugging = false, ignoreOtherErrors = false;
function unreadableAnswerMessage() { return "unreadable"; }
const signedOut = [];
function deleteCredentials() { signedOut.push(true); }
%(client)s
const results = {};
const bodies = %(bodies)s;
const forms = %(forms)s;
for (const name of Object.keys(bodies)) {
    for (const key of Object.keys(nodes)) { delete nodes[key]; }
    ignoreOtherErrors = false;
    signedOut.length = 0;
    const responseText = JSON.stringify(bodies[name]);
    if (forms.indexOf(name) !== -1) {
        /* What a form submit's failure hands its handler (Step 1, the tools). */
        extJSErrorHandler(null, {failureType: "server", response: {responseText: responseText}});
    } else {
        ajaxErrorHandler({responseText: responseText});
    }
    results[name] = {
        kind: nodes["#messageDialog.class"],
        title: nodes["#messageDialogTitle.html"],
        body: nodes["#messageDialogBody.html"],
        report: nodes["#reportErrorButton.display"],
        close: nodes["#messageDialogButton.display"],
        signedOut: signedOut.length > 0
    };
}
process.stdout.write(JSON.stringify(results));
"""

DIALOG_FUNCTIONS = ("showMessage", "showErrorMessage", "showWarningMessage", "ajaxErrorHandler",
                    "extJSErrorHandler", "splitServerError", "isInvalidSessionAnswer")


def runDialogs(bodies, forms=()):
    """Each body through the real ajaxErrorHandler, or extJSErrorHandler for the
    names in `forms`; what each dialog showed."""
    util = read(UTIL)
    client = []
    for name in DIALOG_FUNCTIONS:
        try:
            client.append(extract_function(util, name, "Util.js"))
        except (AssertionError, ValueError):
            pass   # a helper that does not exist on this tree; the handler then says so
    script = DIALOG_HARNESS % {"client": "\n".join(client), "bodies": json.dumps(bodies),
                               "forms": json.dumps(list(forms))}
    directory = tempfile.mkdtemp(prefix="paintomics-gone-")
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




def _invalidSession(userID):
    """What isValidUser really raises for a stale token, as handleException sends it."""
    try:
        UserSessionManager().isValidUser("17", "a-token-nobody-issued")
    except CredentialException as refused:
        return _answerTo(refused, userID=userID)
    raise AssertionError("isValidUser accepted a token nobody issued")


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TheSessionAnswerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        refusal = explainEmptyMapping(
            "hsa", [{"omicName": "Gene expression", "omicSummary": [{"KEGG": 0}, 3]}],
            lambda name, size=None: [WORDS, "ENSX1", "ENSX2"])
        cls.refusal = refusal
        bodies = {
            "invalidSession": _invalidSession("17"),
            "invalidSessionWithNoUser": _invalidSession(""),
            "refusalQuotingTheWords": _answerTo(InputFileError(refusal)),
            "crashQuotingTheWords": _answerTo(ValueError("could not convert string to float: '%s'" % WORDS)),
            "refusalQuotingTheWholeAnswer": _answerTo(InputFileError(
                "CredentialException: AT x.py: f. ERROR MESSAGE: [b]User not valid[/b]. It looks like")),
            "refusalOpeningWithTheSentence": _answerTo(InputFileError(
                "[b]User not valid[/b]. It looks like your session is not valid, please log-in again.")),
            "anotherCredentialRefusal": _answerTo(CredentialException(
                "[b]Administrator privileges required[/b]. Please log in with an administrator account.")),
        }
        names = list(bodies)
        for name in names:
            bodies[name + "ByForm"] = bodies[name]
        cls.results = runDialogs(bodies, forms=[name + "ByForm" for name in names])

    def dialogs(self, name):
        return (self.results[name], self.results[name + "ByForm"])

    def test_the_fixture_quotes_the_words(self):
        self.assertIn(WORDS, self.refusal)

    def test_the_real_answer_still_signs_the_reader_out(self):
        for name in ("invalidSession", "invalidSessionWithNoUser"):
            for dialog in self.dialogs(name):
                with self.subTest(case=name):
                    self.assertIn("Invalid session", dialog["title"])
                    self.assertTrue(dialog["signedOut"])
                    self.assertEqual(dialog["report"], "none")

    def test_a_refusal_quoting_the_words_is_a_refusal(self):
        ajax, form = self.dialogs("refusalQuotingTheWords")
        for dialog in (ajax, form):
            self.assertNotIn("Invalid session", dialog["title"])
            self.assertFalse(dialog["signedOut"], "a data file signed its reader out")
        self.assertIn("Please check your input files", ajax["title"])
        self.assertIn(WORDS, ajax["body"], "the identifier the refusal names was lost")

    def test_a_crash_quoting_the_words_is_a_crash(self):
        for dialog in self.dialogs("crashQuotingTheWords"):
            self.assertIn("Oops..Internal error!", dialog["title"])
            self.assertFalse(dialog["signedOut"])
            self.assertEqual(dialog["report"], "inline-block")

    def test_the_answer_quoted_inside_another_is_not_it(self):
        """The server's own prefix comes first, so the quoted one is the body."""
        for dialog in self.dialogs("refusalQuotingTheWholeAnswer"):
            self.assertNotIn("Invalid session", dialog["title"])
            self.assertFalse(dialog["signedOut"])

    def test_the_sentence_alone_is_not_it(self):
        """The words open the answer only when isValidUser raised them."""
        for dialog in self.dialogs("refusalOpeningWithTheSentence"):
            self.assertNotIn("Invalid session", dialog["title"])
            self.assertFalse(dialog["signedOut"])

    def test_another_credential_refusal_does_not_sign_out(self):
        for dialog in self.dialogs("anotherCredentialRefusal"):
            self.assertNotIn("Invalid session", dialog["title"])
            self.assertFalse(dialog["signedOut"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
