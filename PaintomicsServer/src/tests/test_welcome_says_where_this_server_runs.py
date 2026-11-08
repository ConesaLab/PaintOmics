#!/usr/bin/env python3
"""The welcome dialog says where THIS server runs, or nothing.

Why this exists
---------------
The welcome dialog on every PaintOmics server said

    PaintOmics AI runs on Supercomputador Drago (CSIC). For any inquiries, ...

because the sentence was written into MainView.js. That is true of
paintomics.org and false of paintomics.uv.es (Universitat de Valencia) and of
any other server built from this repository.

The sentence is a server setting now, PAINTOMICS_HOSTED_BY, and reaches the
dialog in the answer to /um_get_message that the dialog already asks for.
Pinned here:

  * paintomics.org, which keeps the template's default, shows exactly the
    sentence it showed before, link included;
  * a server whose setting is empty, or whose hand-kept serverconf.py never
    defined it, answers the welcome request as before and its dialog names no
    host at all;
  * the setting is written into the dialog as text: only [words](https://...)
    becomes a link;
  * deploy/compose.yaml passes the setting into the container with the same
    default the template carries.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_welcome_says_where_this_server_runs
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common.HostingSentence import hostingSentenceHtml
from src.tests.test_boot_failure_names_the_real_cause import _welcomeCheck

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(SRC))
TEMPLATE = os.path.join(SRC, "resources", "example_serverconf.py")
COMPOSE = os.path.join(REPO_ROOT, "deploy", "compose.yaml")

DRAGO_SETTING = "[Supercomputador Drago](https://aic.csic.es/supercomputador-drago/) (CSIC)"
UV_SETTING = "the Universitat de Valencia (I2SysBio)"

CONTACT = ('For any inquiries, please contact us at '
           '<a href="mailto:paintomicsai@gmail.com">paintomicsai@gmail.com</a>')
# What MainView.js wrote on every server until this change. paintomics.org must
# go on showing exactly this.
DRAGO_WELCOME = ('PaintOmics AI runs on <a href="https://aic.csic.es/supercomputador-drago/" '
                 'target="_blank" rel="noopener">Supercomputador Drago</a> (CSIC). ' + CONTACT)


class HostingSentenceTest(unittest.TestCase):

    def test_the_default_reads_as_paintomics_org_always_did(self):
        self.assertEqual(hostingSentenceHtml(DRAGO_SETTING) + " " + CONTACT, DRAGO_WELCOME)

    def test_plain_words(self):
        self.assertEqual(hostingSentenceHtml(UV_SETTING),
                         "PaintOmics AI runs on the Universitat de Valencia (I2SysBio).")

    def test_no_setting_no_sentence(self):
        for setting in (None, "", "   ", ".", 0, ["Drago"]):
            with self.subTest(setting=setting):
                self.assertEqual(hostingSentenceHtml(setting), "")

    def test_a_full_stop_in_the_setting_is_not_doubled(self):
        self.assertEqual(hostingSentenceHtml(UV_SETTING + ". "),
                         "PaintOmics AI runs on the Universitat de Valencia (I2SysBio).")

    def test_the_words_are_text(self):
        sentence = hostingSentenceHtml('<img src=x onerror="alert(1)"> & Co')
        self.assertNotIn("<img", sentence)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; Co", sentence)

    def test_only_web_addresses_become_links(self):
        for setting in ("[here](javascript:alert(1))",
                        "[here](/relative/path)",
                        '[here](https://example.org/"onmouseover="alert(1))',
                        "[<b>here</b>](https://example.org/<script>)"):
            with self.subTest(setting=setting):
                self.assertNotIn("<a ", hostingSentenceHtml(setting))
                self.assertNotIn("<b>", hostingSentenceHtml(setting))

    def test_link_words_are_text_too(self):
        self.assertEqual(
            hostingSentenceHtml("[A & B](https://example.org/?a=1&b=2) and [C](http://c.example)"),
            'PaintOmics AI runs on <a href="https://example.org/?a=1&amp;b=2" target="_blank" '
            'rel="noopener">A &amp; B</a> and <a href="http://c.example" target="_blank" '
            'rel="noopener">C</a>.')


def _loadTemplate(environment):
    """example_serverconf.py imported under the given PAINTOMICS_HOSTED_BY.

    None removes the variable, as on a server whose environment never set it.
    """
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PAINTOMICS_HOSTED_BY", None)
        if environment is not None:
            os.environ["PAINTOMICS_HOSTED_BY"] = environment
        spec = importlib.util.spec_from_file_location("serverconf_template_under_test", TEMPLATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class TemplateSettingTest(unittest.TestCase):

    def test_unset_is_paintomics_org(self):
        self.assertEqual(_loadTemplate(None).PAINTOMICS_HOSTED_BY, DRAGO_SETTING)

    def test_empty_says_nothing(self):
        self.assertEqual(hostingSentenceHtml(_loadTemplate("").PAINTOMICS_HOSTED_BY), "")

    def test_another_server_names_itself(self):
        self.assertEqual(_loadTemplate(UV_SETTING).PAINTOMICS_HOSTED_BY, UV_SETTING)

    def test_compose_passes_it_with_the_same_default(self):
        """compose.yaml's environment block is an allowlist (see
        test_input_converter_survives_deploy): a key missing there never reaches
        the container. `-` and not `:-`, so an empty value stays empty."""
        with open(COMPOSE, encoding="utf-8") as handle:
            compose = handle.read()
        match = re.search(r'^\s{6}PAINTOMICS_HOSTED_BY: "\$\{PAINTOMICS_HOSTED_BY-(.*)\}"$',
                          compose, re.MULTILINE)
        self.assertIsNotNone(match, "compose.yaml does not pass PAINTOMICS_HOSTED_BY to the app")
        self.assertEqual(match.group(1), _loadTemplate(None).PAINTOMICS_HOSTED_BY)


class _Request:
    def __init__(self):
        self.form = {"message_type": "starting_message"}
        self.cookies = {}


class _Response:
    def __init__(self):
        self.content = None
        self.status = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        self.status = status


class _NoMessages:
    def findAll(self, otherParams=None):
        return []

    def closeConnection(self):
        pass


class WelcomeAnswerTest(unittest.TestCase):
    """adminServletGetMessage, the handler behind /um_get_message."""

    def setUp(self):
        from src.servlets import AdminServlet
        self.servlet = AdminServlet
        patcher = mock.patch.object(AdminServlet, "MessageDAO", _NoMessages)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _welcomeAnswer(self, serverconf):
        with mock.patch.object(self.servlet, "_serverconf", serverconf):
            response = self.servlet.adminServletGetMessage(_Request(), _Response())
        return response.content

    def test_the_answer_carries_this_servers_sentence(self):
        answer = self._welcomeAnswer(types.SimpleNamespace(PAINTOMICS_HOSTED_BY=UV_SETTING))
        self.assertIs(answer["success"], True)
        self.assertEqual(answer["hostingSentence"], hostingSentenceHtml(UV_SETTING))

    def test_an_empty_setting_sends_no_sentence(self):
        answer = self._welcomeAnswer(types.SimpleNamespace(PAINTOMICS_HOSTED_BY=""))
        self.assertIs(answer["success"], True)
        self.assertEqual(answer["hostingSentence"], "")

    def test_a_hand_kept_serverconf_without_the_setting_still_answers(self):
        answer = self._welcomeAnswer(types.SimpleNamespace())
        self.assertIs(answer["success"], True, answer)
        self.assertEqual(answer["hostingSentence"], "")
        self.assertEqual(answer["messageList"], [])


# MainView's real Welcome code, answered with `answer`. Returns the dialog it
# opened, or null.
HARNESS = r"""
const vm = require("vm");
const welcome = %(welcome)s;

function show(answer) {
    let dialog = null;
    const sandbox = {
        console: {log() {}},
        SERVER_URL_GET_MESSAGE: "/um_get_message",
        ajaxErrorHandler() {},
        jobIDInAddress() { return null; },
        showInfoMessage(title, data) { dialog = {title: title, message: data.message}; },
        Ext: {util: {Cookies: {get() { return null; }}}},
        $: {ajax(opts) { opts.success(answer); }}
    };
    vm.runInContext(welcome, vm.createContext(sandbox));
    return dialog;
}

process.stdout.write(JSON.stringify(%(answers)s.map(show)));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class WelcomeDialogTest(unittest.TestCase):

    ANSWERS = [
        {"success": True, "messageList": [], "hostingSentence": hostingSentenceHtml(DRAGO_SETTING)},
        {"success": True, "messageList": [], "hostingSentence": hostingSentenceHtml(UV_SETTING)},
        {"success": True, "messageList": [], "hostingSentence": ""},
        {"success": True, "messageList": []},
    ]

    @classmethod
    def setUpClass(cls):
        scratch = tempfile.mkdtemp(prefix="paintomics_welcome_")
        try:
            script = os.path.join(scratch, "harness.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"welcome": json.dumps(_welcomeCheck()),
                                        "answers": json.dumps(cls.ANSWERS)})
            done = subprocess.run(["node", script], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.drago, cls.uv, cls.empty, cls.missing = json.loads(done.stdout)

    def test_paintomics_org_reads_exactly_as_before(self):
        self.assertEqual(self.drago["title"], "Welcome to PaintOmics AI!")
        self.assertEqual(self.drago["message"], DRAGO_WELCOME)

    def test_another_server_names_itself_not_drago(self):
        self.assertEqual(self.uv["message"],
                         "PaintOmics AI runs on the Universitat de Valencia (I2SysBio). " + CONTACT)

    def test_a_server_without_the_setting_names_no_host(self):
        for dialog in (self.empty, self.missing):
            with self.subTest(dialog=dialog):
                self.assertEqual(dialog["title"], "Welcome to PaintOmics AI!")
                self.assertEqual(dialog["message"], CONTACT)
                self.assertNotIn("Drago", dialog["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
