#!/usr/bin/env python3
"""Cover for `MOREServlet.moreBinary` -- which more-rs a job runs on, if any.

There is one MORE engine. Until 2026-09 there were two, and the second was a
*fallback*: with no binary discoverable, `_resolveMOREBackend` returned
`["Rscript", runMORE.R]` and the job went to the MORE R package. That package
has never been installed in the deployed image (deploy/Dockerfile records why),
so the fallback did not produce a slower answer -- it produced an R error from
inside a job the user had already waited for.

So the question this file pins changed shape. It used to be "which of the two",
and it is now "the binary, or a refusal the user can read". The refusal path is
therefore the important half of what is tested here, not an edge case:

* `PAINTOMICS_MORE_RS=off` and its synonyms, the opt-out, which used to mean
  "use R" and now means "refuse".
* A configured path that is not on disk -- a stale setting.
* A configured path without the executable bit, which an unpacked archive
  loses easily and which would otherwise surface as EACCES from Popen.

Discovery order is pinned too, because it is what makes the bundled binary
work with no configuration at all.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_more_backend_selection
"""
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.servlets import MOREServlet


class MoreBinaryTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="more_backend_")
        self.binary = os.path.join(self.tmp, "more-rs")
        with open(self.binary, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.binary, os.stat(self.binary).st_mode | stat.S_IXUSR)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_configured_binary_is_used(self):
        self.assertEqual(MOREServlet.moreBinary(self.binary), self.binary)

    def test_a_binary_is_discovered_when_none_is_configured(self):
        with mock.patch.object(MOREServlet, "_discoverMoreRs",
                               return_value=self.binary):
            self.assertEqual(MOREServlet.moreBinary(""), self.binary)

    def test_nothing_discoverable_means_no_engine(self):
        """The fallback that used to live here is gone, and that is the change.

        This returned ["Rscript", runMORE.R] before, on a host where the MORE R
        package is not installed. Returning "" is what lets every caller refuse
        at submission instead.
        """
        with mock.patch.object(MOREServlet, "_discoverMoreRs", return_value=""):
            self.assertEqual(MOREServlet.moreBinary(""), "")

    def test_the_off_switch_disables_the_only_engine(self):
        """`off` used to mean "use R". With no R, it means "refuse"."""
        for spelling in MOREServlet.MORE_RS_OFF:
            with self.subTest(spelling=spelling):
                self.assertEqual(MOREServlet.moreBinary(spelling), "")

    def test_the_off_switch_is_case_insensitive(self):
        self.assertEqual(MOREServlet.moreBinary("OFF"), "")
        self.assertEqual(MOREServlet.moreBinary("  Off  "), "")

    def test_blank_is_not_an_off_switch(self):
        """Blank means "go and find one" -- the no-configuration default."""
        self.assertNotIn("", MOREServlet.MORE_RS_OFF)
        with mock.patch.object(MOREServlet, "_discoverMoreRs",
                               return_value=self.binary):
            self.assertEqual(MOREServlet.moreBinary("   "), self.binary)

    def test_a_configured_binary_that_is_not_on_disk_is_no_engine(self):
        self.assertEqual(
            MOREServlet.moreBinary(os.path.join(self.tmp, "absent")), "")

    def test_a_configured_binary_that_is_not_executable_is_no_engine(self):
        plain = os.path.join(self.tmp, "not-executable")
        with open(plain, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(plain, 0o644)
        self.assertEqual(MOREServlet.moreBinary(plain), "")

    def test_a_configured_path_is_never_second_guessed_by_discovery(self):
        """A stale setting must refuse, not silently run a different binary.

        Falling through to discovery here would make a typo in
        PAINTOMICS_MORE_RS run whatever happens to be on PATH, which is the
        opposite of what naming a path means.
        """
        with mock.patch.object(MOREServlet, "_discoverMoreRs",
                               return_value=self.binary) as discover:
            self.assertEqual(
                MOREServlet.moreBinary(os.path.join(self.tmp, "absent")), "")
            discover.assert_not_called()


class DiscoveryOrderTest(unittest.TestCase):
    """The bundled binary outranks PATH, and both beat nothing."""

    def test_discovery_prefers_the_bundled_binary_over_path(self):
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("os.access", return_value=True), \
             mock.patch("shutil.which", return_value="/usr/local/bin/more-rs"):
            self.assertEqual(MOREServlet._discoverMoreRs(),
                             MOREServlet.MORE_RS_BUNDLED)

    def test_discovery_falls_through_to_path(self):
        with mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value="/usr/local/bin/more-rs"):
            self.assertEqual(MOREServlet._discoverMoreRs(),
                             "/usr/local/bin/more-rs")

    def test_discovery_returns_empty_when_there_is_nothing(self):
        with mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value=None):
            self.assertEqual(MOREServlet._discoverMoreRs(), "")


class ConfigCompatibilityTest(unittest.TestCase):
    """The setting must be optional in serverconf.py.

    serverconf.py is gitignored and generated from example_serverconf.py by
    deploy/entrypoint.sh -- but only `if [ ! -f "${CONFIG_PATH}" ]`. An upgraded
    container therefore keeps the config it already has, which predates this
    setting. A hard import of it would raise at import time and take the whole
    servlet down, turning a new optional feature into an outage on every
    existing deployment.

    Exercised by reloading the module rather than by grepping its source. The
    source-grep form of this test also reads as
    `from src.conf.serverconf import <name>, source` to the import scanner in
    test_release_hygiene, which then demands a setting called `source`.
    """

    def test_the_servlet_imports_when_serverconf_predates_the_setting(self):
        import importlib
        from src.conf import serverconf

        had = hasattr(serverconf, "MORE_RS_BINARY")
        saved = getattr(serverconf, "MORE_RS_BINARY", None)
        savedEnv = os.environ.get("PAINTOMICS_MORE_RS")
        try:
            if had:
                del serverconf.MORE_RS_BINARY
            os.environ["PAINTOMICS_MORE_RS"] = "/tmp/more-rs-from-env"
            reloaded = importlib.reload(MOREServlet)
            self.assertEqual(reloaded.MORE_RS_BINARY, "/tmp/more-rs-from-env",
                             "the servlet must fall back to the environment")
        finally:
            if had:
                serverconf.MORE_RS_BINARY = saved
            if savedEnv is None:
                os.environ.pop("PAINTOMICS_MORE_RS", None)
            else:
                os.environ["PAINTOMICS_MORE_RS"] = savedEnv
            importlib.reload(MOREServlet)

    def test_the_shipped_config_template_carries_the_setting(self):
        """A fresh container should get the setting without hand-editing."""
        template = os.path.join(
            os.path.dirname(__file__), "..", "resources", "example_serverconf.py")
        with open(template) as fh:
            body = fh.read()
        self.assertIn("MORE_RS_BINARY", body)
        self.assertIn("PAINTOMICS_MORE_RS", body)

    def test_no_runtime_budget_setting_survives_the_cost_model(self):
        """The pre-flight cost model is gone; its setting must not linger.

        A setting the server no longer reads is worse than none: an operator
        who tunes PAINTOMICS_MORE_RUNTIME_BUDGET would be tuning nothing, and
        would have no way to find that out.
        """
        template = os.path.abspath(os.path.join(
            os.path.dirname(MOREServlet.__file__),
            "..", "resources", "example_serverconf.py"))
        for path in (MOREServlet.__file__, template):
            with self.subTest(path=os.path.basename(path)):
                hits = [n for n, line in
                        enumerate(open(path, encoding="utf-8"), 1)
                        if "MORE_RUNTIME_BUDGET_SECONDS" in line]
                self.assertEqual(hits, [], "still referenced at lines %s" % hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
