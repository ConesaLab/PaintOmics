#!/usr/bin/env python3
"""Every control the sign-in panel binds is one it draws.

Why this exists
---------------
SignInPanel's afterrender bound a click handler to #guestUserButton, which
started a guest session. The panel stopped drawing that button when "Continue
without an account" (#noLoginButton) took its place, and the binding stayed, bound
to nothing. A check of guest sessions in the browser for PR #218 found no way to
start one from the page for that reason. The binding is gone; nothing else in the
client starts a guest session (UserController.startGuestSessionButtonClickHandler
and the /um_guestsession route are left for whoever decides what guests are for).

The real panel is built under node with a stand-in for Ext.widget, and its real
afterrender runs against a $ that records what it binds.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_sign_in_panel_binds_what_it_draws
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

USER_VIEWS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "PaintomicsClient", "public_html", "app", "view", "UserManagementViews", "UserViews.js"))

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const bound = [];
const sandbox = {
    console: {log() {}, info() {}, warn() {}, error() {}},
    View: function View() {},
    Ext: {widget(config) { return config; }, util: {Cookies: {get() { return null; }}}},
    $: function (selector) { return {click() { bound.push(selector); return this; }, on() { return this; }}; }
};
const context = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(%(views)s, "utf8"), context, {filename: "UserViews.js"});
context.poAttachPasswordReveal = function () {};
const panel = vm.runInContext("new SignInPanel()", context);
const config = panel.initComponent();
const markup = [];
(function walk(item) {
    if (!item || typeof item !== "object") { return; }
    if (typeof item.html === "string") { markup.push(item.html); }
    (item.items || []).forEach(walk);
})(config);
config.listeners.afterrender({});
const drawn = [];
markup.join("\n").replace(/\sid=['"]([^'"]+)['"]/g, function (all, id) { drawn.push(id); });
const classes = markup.join("\n");
process.stdout.write(JSON.stringify({bound: bound, drawn: drawn, markup: classes}));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class SignInPanelTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        directory = tempfile.mkdtemp(prefix="paintomics-signin-")
        try:
            path = os.path.join(directory, "check.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(HARNESS % {"views": json.dumps(USER_VIEWS)})
            done = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if done.returncode != 0:
            raise AssertionError("the harness failed:\n" + done.stderr)
        cls.result = json.loads(done.stdout)

    def test_every_id_it_binds_is_drawn(self):
        ids = [selector[1:] for selector in self.result["bound"] if selector.startswith("#")]
        self.assertTrue(ids, "the harness saw no binding at all")
        self.assertEqual([i for i in ids if i not in self.result["drawn"]], [],
                         "handlers bound to elements the panel does not draw")

    def test_every_class_it_binds_is_drawn(self):
        for selector in self.result["bound"]:
            if selector.startswith("."):
                with self.subTest(selector=selector):
                    self.assertIn(selector[1:], self.result["markup"])

    def test_the_controls_people_use_are_still_bound(self):
        for selector in ("#signInLink", "#forgotPassLink", ".signUpLink", "#noLoginButton"):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.result["bound"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
