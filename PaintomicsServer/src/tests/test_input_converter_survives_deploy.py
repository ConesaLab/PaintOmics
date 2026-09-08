#!/usr/bin/env python3
"""A deployment cannot lose the converter switch, and a browser learns it first.

Why this exists
---------------
On 2026-09-08 a user on paintomics.org tried thirteen times to convert a
proteomics spreadsheet and every attempt ended with

    AI file conversion is not enabled on this server.

after the sandbox had booted and the file had been profiled. The operator
believed the converter was on -- it had been "fixed before". It had not: the
setting was never carried. `deploy/compose.yaml` passes the environment to the
container through an explicit list, `AI_INPUT_CONVERTER` was not in it, and
`example_serverconf.py`'s os.getenv() read the default on every rebuild. The
smoke test passed. Nothing on the page said the feature was off until the last
step, and that step offered a text box that was disabled.

Three things are pinned here, each the mechanical half of one of those facts:

  * every setting `deploy/env.example` documents is passed through
    `deploy/compose.yaml`, so a key an operator sets in `.env` reaches the
    container. The bug class is "set in .env, silently dropped"; the whole
    class is closed, not the one key.
  * `/ai_provider` reports `inputConverter` from the same gate the turn route
    uses, so the browser can decide before it starts whether starting is
    worth anything, and the two answers can never disagree.
  * the refusal sentence is one sentence: the servlet's constant, the phrase
    the drawer recognises, and the strip's wording all contain it.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_input_converter_survives_deploy
"""
import os
import re
import sys
import unittest

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(SERVER_ROOT)
sys.path.insert(0, SERVER_ROOT)

COMPOSE = os.path.join(REPO_ROOT, "deploy", "compose.yaml")
ENV_EXAMPLE = os.path.join(REPO_ROOT, "deploy", "env.example")
TEMPLATE = os.path.join(SERVER_ROOT, "src", "resources", "example_serverconf.py")
DRAWER = os.path.join(REPO_ROOT, "PaintomicsClient", "public_html", "app", "view",
                      "PathwayAcquisitionViews", "InputFormat", "convert-drawer.js")
PANEL = os.path.join(REPO_ROOT, "PaintomicsClient", "public_html", "app", "view",
                     "PathwayAcquisitionViews", "InputFormat", "format-panel.js")
SMOKE = os.path.join(REPO_ROOT, "deploy", "smoke-test.sh")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _env_example_keys():
    """Every variable env.example documents, set or commented out."""
    keys = set()
    for line in _read(ENV_EXAMPLE).splitlines():
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def _compose_environment_keys():
    """Every `KEY:` line inside the app service's environment block."""
    text = _read(COMPOSE)
    start = text.index("  app:")
    block = text[start:]
    env_at = block.index("    environment:")
    body = block[env_at + len("    environment:"):]
    keys = set()
    for line in body.splitlines():
        if line.strip() == "":
            continue
        if not line.startswith("      "):        # back out to the service level
            break
        match = re.match(r"^\s{6}([A-Z][A-Z0-9_]*):", line)
        if match:
            keys.add(match.group(1))
    return keys


class EverySettingReachesTheContainer(unittest.TestCase):

    def test_env_example_keys_are_passed_through_compose(self):
        documented = _env_example_keys()
        passed = _compose_environment_keys()
        self.assertTrue(documented, "env.example lists no settings?")
        dropped = sorted(documented - passed)
        self.assertEqual(dropped, [],
                         "set in deploy/.env these would never reach the app "
                         "container -- compose.yaml's environment block is an "
                         "allowlist: %s" % dropped)

    def test_the_converter_switch_is_wired_end_to_end(self):
        self.assertIn("AI_INPUT_CONVERTER", _env_example_keys())
        self.assertIn("AI_INPUT_CONVERTER", _compose_environment_keys())
        self.assertRegex(_read(TEMPLATE),
                         r'AI_INPUT_CONVERTER\s*=\s*os\.getenv\("AI_INPUT_CONVERTER"',
                         "the template must read the switch from the environment")

    def test_the_smoke_test_checks_the_pass_through(self):
        """A deployment finds out at deploy time, not from a user."""
        smoke = _read(SMOKE)
        self.assertIn("AI_INPUT_CONVERTER", smoke)
        self.assertIn("check_llm_gateway.py", smoke)


class TheBrowserIsToldBeforeItStarts(unittest.TestCase):

    def setUp(self):
        from src.servlets import InputConvertServlet as servlet
        from src.servlets import AIInterpretServlet as status
        self.servlet = servlet
        self.status = status
        self._gate = servlet._converter_enabled

    def tearDown(self):
        self.servlet._converter_enabled = self._gate

    def test_ai_provider_reports_the_same_gate_the_turn_uses(self):
        self.servlet._converter_enabled = lambda: False
        self.assertIs(self.status.getAIProviderInfo()["inputConverter"], False)
        self.servlet._converter_enabled = lambda: True
        self.assertIs(self.status.getAIProviderInfo()["inputConverter"], True)

    def test_a_switched_off_server_refuses_with_the_one_sentence(self):
        from src.tests.test_input_convert_gateway_timeout import _Request, _Response
        self.servlet._converter_enabled = lambda: False
        real = self.servlet.UserSessionManager
        self.servlet.UserSessionManager = lambda: type(
            "S", (), {"isValidUser": staticmethod(lambda u, t: True)})()
        try:
            response = self.servlet.inputConvertTurn(
                _Request({}, {"userID": "1", "sessionToken": "t"}),
                _Response(), None, "convert_test")
        finally:
            self.servlet.UserSessionManager = real
        message = str(response.content.get("message") or response.content.get("errorMessage") or "")
        self.assertIn(self.servlet.NOT_ENABLED_MESSAGE, message)

    def test_the_sentence_is_shared_with_the_client(self):
        phrase = "not enabled on this server"
        self.assertIn(phrase, self.servlet.NOT_ENABLED_MESSAGE)
        drawer = _read(DRAWER)
        panel = _read(PANEL)
        self.assertIn(phrase, drawer, "the drawer must recognise the refusal")
        self.assertIn(phrase, panel, "the upload strip must say the same thing")
        self.assertIn("inputConverter", drawer,
                      "the drawer must ask /ai_provider before booting the sandbox")
        self.assertIn("inputConverter", panel)


if __name__ == "__main__":
    unittest.main()
