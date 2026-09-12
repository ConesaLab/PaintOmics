#!/usr/bin/env python3
"""The data-management tools must normalise an upload's encoding before reading.

Why this exists
---------------
`cab1dd57` gave Bed2GeneJob and MiRNA2GeneJob the `ensure_utf8` call that
PathwayAcquisitionJob already had, because both read their uploads with a bare

    open(relevantFileName, 'r')

which decodes using the locale. A spreadsheet exported as cp1252/latin-1 --
what Excel writes by default outside a UTF-8 locale -- therefore raised

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd1 in position 4

out of a validation routine, reaching the user as an internal error rather than
a message about their file. One accented gene name is enough.

That fix shipped with no test. It was verified by hand at the time and then had
nothing holding it in place: replacing the guard with `encodingError = None`
left all 87 tests passing. This file closes that hole, and it exists because a
mutation run over this session's fixes caught six of seven and missed exactly
this one.

What is pinned is the behaviour rather than the call: a latin-1 file must be
readable after validateFile has seen it, and a UTF-8 or ASCII file must come
back byte-identical, since rewriting files that were already fine would be its
own bug.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_dm_upload_encoding
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.Bed2GeneJob import Bed2GeneJob
from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob

LATIN1_BODY = "MMU-MIR-1983\nGeneÑ\ncafé\n".encode("latin-1")
UTF8_BODY = "MMU-MIR-1983\nGeneÑ\ncafé\n".encode("utf-8")
ASCII_BODY = b"MMU-MIR-1983\nGeneA\n"
VALUES_BODY = b"#name\tC1\ngene\t1\n"


class DataManagementUploadEncodingTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _job(self, jobClass):
        job = jobClass("ENCTEST", None, self.directory)
        job.setDirectories(self.directory)
        job.inputDir = self.directory + os.sep
        return job

    def _validate(self, jobClass, relevantBody):
        relevant = os.path.join(self.directory, "relevant.tab")
        values = os.path.join(self.directory, "values.tab")
        with open(relevant, "wb") as handle:
            handle.write(relevantBody)
        with open(values, "wb") as handle:
            handle.write(VALUES_BODY)

        self._job(jobClass).validateFile(
            {"omicName": "miRNA", "inputDataFile": "values.tab",
             "relevantFeaturesFile": "relevant.tab"}, 0, "")
        return relevant

    def _classes(self):
        return (("Bed2GeneJob", Bed2GeneJob), ("MiRNA2GeneJob", MiRNA2GeneJob))

    def test_a_latin1_upload_becomes_readable(self):
        """The whole point: it could not be read as UTF-8 before, and can after."""
        for label, jobClass in self._classes():
            with self.subTest(job=label):
                with self.assertRaises(UnicodeDecodeError,
                                       msg="the fixture is not actually latin-1"):
                    LATIN1_BODY.decode("utf-8")

                path = self._validate(jobClass, LATIN1_BODY)

                with open(path, "rb") as handle:
                    content = handle.read()
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError:
                    self.fail("%s left the upload undecodable, so the next bare "
                              "open() of it raises UnicodeDecodeError at the "
                              "user" % label)

    def test_validating_a_latin1_upload_does_not_raise(self):
        """The user-visible symptom was an internal error out of validation."""
        for label, jobClass in self._classes():
            with self.subTest(job=label):
                try:
                    self._validate(jobClass, LATIN1_BODY)
                except UnicodeDecodeError as exc:
                    self.fail("%s raised UnicodeDecodeError from validateFile, "
                              "which reaches the browser as 'Oops..Internal "
                              "error!': %s" % (label, exc))

    def test_a_utf8_upload_is_left_alone(self):
        """Rewriting a file that was already fine would be its own bug."""
        for label, jobClass in self._classes():
            with self.subTest(job=label):
                before = hashlib.sha256(UTF8_BODY).hexdigest()

                path = self._validate(jobClass, UTF8_BODY)

                with open(path, "rb") as handle:
                    after = hashlib.sha256(handle.read()).hexdigest()
                self.assertEqual(after, before,
                                 "%s rewrote a UTF-8 upload" % label)

    def test_an_ascii_upload_is_left_alone(self):
        for label, jobClass in self._classes():
            with self.subTest(job=label):
                before = hashlib.sha256(ASCII_BODY).hexdigest()

                path = self._validate(jobClass, ASCII_BODY)

                with open(path, "rb") as handle:
                    after = hashlib.sha256(handle.read()).hexdigest()
                self.assertEqual(after, before,
                                 "%s rewrote an ASCII upload" % label)


class MOREUploadEncodingTest(unittest.TestCase):
    """MORE hands its files to R without ever reading them in Python.

    That made it the last upload route without encoding normalisation, and the
    consequence is quieter than elsewhere: R does not fail on a mis-encoded
    file. Measured with read.delim on the same bytes --

        utf8    names: GeneN, cafe (accents intact)
        latin1  names: Gene?, caf?  (replacement characters)

    -- so a cp1252 spreadsheet yields garbled regulator and gene identifiers,
    which then fail to match the target expression file that *is* normalised.
    MORE reports fewer associations, or none, and nothing errors.
    """

    def test_the_servlet_normalises_before_calling_r(self):
        import io, tokenize

        path = os.path.join(os.path.dirname(__file__), "../servlets/MOREServlet.py")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()

        try:
            tokens = [t for t in tokenize.generate_tokens(io.StringIO(source).readline)
                      if t.type != tokenize.COMMENT]
            stripped = tokenize.untokenize(tokens)
        except Exception:
            stripped = source

        # The call, not the import. Deleting the loop that normalises the
        # files leaves "from src.common.Util import ensure_utf8" behind, so
        # searching for the bare name passes while nothing is normalised --
        # which is exactly what the first version of this test did, and a
        # mutation run caught it.
        callAt = stripped.find("ensure_utf8(")
        self.assertNotEqual(callAt, -1,
                            "MORE imports ensure_utf8 but never calls it, so "
                            "uploaded files still reach runMORE.R in whatever "
                            "encoding they arrived in")

        # Anchor on the invocation itself. This is the third anchor this test
        # has used, and the first two failed the same way -- both were proxies
        # for "where R gets called", and both stopped tracking it:
        #
        #   "bioscripts"  -- picked because only the path construction had it.
        #                    Untrue once PLS1 defaulted to the Rust backend and
        #                    MORE_RS_BUNDLED built a second bioscripts path at
        #                    the top of the module.
        #   '"runMORE.R"' -- picked because only the path construction quoted
        #                    it, prose being unquoted. Untrue once the path
        #                    construction moved into the module-level helper
        #                    _moreRScript(), ~400 lines above the normalisation,
        #                    and again once the backend-selection logging began
        #                    quoting the filename in log strings, which are
        #                    literals and survive comment-stripping. Both that
        #                    helper and runMORE.R itself are gone now, with the
        #                    R engine; the point of the list is the pattern.
        #
        # So stop proxying. `subprocess.Popen(` IS the call that must not
        # receive un-normalised files, it occurs exactly once, and it cannot be
        # hoisted into a helper without the ordering genuinely changing -- which
        # is the property this test exists to defend. Note it must be Popen
        # specifically and not `subprocess.`: the R-availability probe uses
        # subprocess.run() and legitimately runs before normalisation.
        callsRAt = stripped.find("subprocess.Popen(")
        self.assertNotEqual(callsRAt, -1,
                            "MOREServlet no longer calls subprocess.Popen; if "
                            "the backend invocation moved, re-anchor this test "
                            "on whatever now runs the MORE binary")
        self.assertLess(callAt, callsRAt,
                        "the normalisation happens after the MORE backend is "
                        "invoked; it must precede the call, or R reads the "
                        "files in whatever encoding they arrived in")

    def test_a_latin1_file_becomes_readable(self):
        from src.common.Util import ensure_utf8

        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "regulator.tab")
            with open(path, "wb") as handle:
                handle.write("gene\tC1\nGeneÑ\t1.5\n".encode("latin-1"))

            self.assertIsNone(ensure_utf8(path))

            with open(path, "rb") as handle:
                handle.read().decode("utf-8")   # raises if still latin-1
        finally:
            shutil.rmtree(directory, ignore_errors=True)



def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
