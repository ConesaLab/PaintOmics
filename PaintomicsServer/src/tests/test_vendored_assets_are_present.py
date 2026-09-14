#!/usr/bin/env python3
"""Every vendored asset the client asks for must exist on disk.

The vendored third-party libraries under `PaintomicsClient/public_html` shipped
with their upstream *build toolchains* attached -- Bootstrap 3.3.7 brought its
Grunt config, its Jekyll docs Gemfile and a `package-lock.json`; Tooltipster
brought a `package-lock.json` and 4494 files of committed `node_modules`. None
of it is installed, executed or served: the app loads four files in total,
all from `dist/`.

Dependabot cannot tell the difference. Those manifests accounted for 68 of the
repository's open alerts -- more than half -- across packages like grunt,
handlebars, lodash and jekyll that no deployed code path can reach. Removing
the manifests removes the alerts, and removing the committed `node_modules`
stops several megabytes of unreviewed third-party JavaScript being publicly
downloadable, since everything under `public_html` is served.

The risk in that deletion is deleting one file too many, and `dist/` looks
incidental next to `less/` and `grunt/`. This test is the guard: it parses the
client's own markup and stylesheets for references into the vendored
directories and asserts each one resolves. It is deliberately derived from the
references rather than from a hand-written list, so a newly added `<script>`
tag is covered without anyone remembering to update it.

Not covered on purpose: whether the assets *work*, only that they are present.
A broken minified bundle is a different failure and a different test.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_vendored_assets_are_present
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "../../../PaintomicsClient/public_html"))

# The directories whose build toolchains were removed. A reference into one of
# these that does not resolve means the deletion went too far.
VENDORED_DIRECTORIES = (
    "js/libs/tooltipster-master",
    "admin/lib/bootstrap",
)

# src="...", href="..." and url(...) are the three ways the client names a file.
REFERENCE = re.compile(
    r"""(?:src|href)\s*=\s*["']([^"'#?]+)["']|url\(\s*["']?([^"')#?]+)""",
    re.IGNORECASE)

SEARCHED_SUFFIXES = (".html", ".js", ".css")


def _referencesIn(path):
    # os.walk reports dangling symlinks as files, and reading one raises
    # FileNotFoundError, which would abort the scan rather than skip a file --
    # and an aborted scan is indistinguishable from a clean one here.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return

    for match in REFERENCE.finditer(content):
        yield match.group(1) or match.group(2)


def _isVendored(reference):
    return any(directory in reference for directory in VENDORED_DIRECTORIES)


class VendoredAssetsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.references = []

        for directory, subdirectories, filenames in os.walk(CLIENT_ROOT):
            # Do not walk into the vendored trees themselves: upstream's own
            # docs and build files reference plenty that was never shipped, and
            # those references are not the application's.
            subdirectories[:] = [
                s for s in subdirectories
                if not _isVendored(os.path.join(directory, s).replace(os.sep, "/"))]

            for filename in filenames:
                if not filename.endswith(SEARCHED_SUFFIXES):
                    continue
                sourcePath = os.path.join(directory, filename)
                for reference in _referencesIn(sourcePath):
                    if _isVendored(reference):
                        cls.references.append((sourcePath, reference))

    def testTheScanFoundSomething(self):
        """An empty scan would make every assertion below vacuous."""
        self.assertGreater(
            len(self.references), 0,
            "no references into the vendored directories were found at all -- "
            "the regex or CLIENT_ROOT (%s) is wrong, not the tree" % CLIENT_ROOT)

    def testEveryReferencedVendoredAssetExists(self):
        missing = []

        for sourcePath, reference in self.references:
            # References are written relative to public_html, occasionally with
            # a leading ./ or /.
            relative = reference.lstrip("/")
            if relative.startswith("./"):
                relative = relative[2:]
            candidate = os.path.join(CLIENT_ROOT, relative)

            if not os.path.exists(candidate):
                missing.append("%s -> %s" % (
                    os.path.relpath(sourcePath, CLIENT_ROOT), reference))

        self.assertEqual(
            [], sorted(set(missing)),
            "the client references vendored files that are not on disk:\n  "
            + "\n  ".join(sorted(set(missing))))

    def testBuildToolchainsAreNotCommitted(self):
        """What was removed must stay removed.

        Re-committing any of these silently restores the alerts, and the
        `node_modules` tree additionally republishes megabytes of third-party
        JavaScript under a directory the server serves wholesale.
        """
        forbidden = []

        for directory in VENDORED_DIRECTORIES:
            base = os.path.join(CLIENT_ROOT, directory)
            for name in ("package.json", "package-lock.json",
                         "Gemfile", "Gemfile.lock", "node_modules"):
                path = os.path.join(base, name)
                if os.path.exists(path):
                    forbidden.append(os.path.join(directory, name))

        self.assertEqual(
            [], forbidden,
            "dependency manifests or vendored node_modules are back:\n  "
            + "\n  ".join(forbidden))


if __name__ == "__main__":
    unittest.main(verbosity=2)
