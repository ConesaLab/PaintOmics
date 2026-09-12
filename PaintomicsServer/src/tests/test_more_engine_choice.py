#!/usr/bin/env python3
"""The regulatory engine catalogue, its availability report, and the refusal.

One engine, two methods. This file used to pin a four-entry catalogue -- PLS1
and MLR on `more-rs` and on the MORE R package -- and most of what it covered
was the machinery for choosing between them: an `auto` pseudo-engine, a router
that sent PLS1 to the port and MLR to R, an R *package* probe (not an
interpreter probe, which was the subtle part), and a refusal that had to let
`auto` bend from one engine to the other without contradicting the router.

All of that is gone with the R engine, and the tests for it are gone with it.
What remains is narrower and, in one respect, stricter: with no second backend
to fall back to, "this host has no more-rs" is a refusal rather than a slower
answer, and the refusal has to reach the user at submission. That is what the
RefusalTest cases below exist for.

The compatibility surface is kept deliberately: `engineIdFor` and
`engineRefusal` still accept an `engine` argument, and still accept the literal
string "r", because stored jobs in Mongo and clients that predate the removal
send exactly that. They resolve by method now.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_more_engine_choice
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.JobInstances.MOREJob import MOREJob
from src.servlets import MOREServlet


class CatalogueTest(unittest.TestCase):

    def test_the_catalogue_is_rust_only(self):
        self.assertEqual([e["engine"] for e in MOREServlet.MORE_ENGINES],
                         ["rust", "rust"])

    def test_every_method_is_offered_exactly_once(self):
        """Two entries for one method would make engineIdFor's answer depend on
        list order, which is how the old catalogue hid a choice inside a lookup.
        """
        methods = [e["method"] for e in MOREServlet.MORE_ENGINES]
        self.assertEqual(sorted(methods), ["MLR", "PLS1"])

    def test_the_ids_are_unique_and_the_default_is_one_of_them(self):
        ids = [e["id"] for e in MOREServlet.MORE_ENGINES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(MOREServlet.DEFAULT_MORE_ENGINE, ids)

    def test_every_entry_resolves_back_to_itself(self):
        for entry in MOREServlet.MORE_ENGINES:
            with self.subTest(entry=entry["id"]):
                self.assertEqual(MOREServlet.engineIdFor(entry["method"]),
                                 entry["id"])

    def test_no_entry_still_advertises_the_r_engine(self):
        """The wording had one home in this catalogue, and the R half of it is
        the thing being removed. A stale sentence here reaches the user
        directly: /more_backends serves `detail` verbatim into the picker."""
        for entry in MOREServlet.MORE_ENGINES:
            text = (entry["label"] + " " + entry["detail"]).lower()
            with self.subTest(entry=entry["id"]):
                for phrase in ("r engine", "the r package", "runmore.r",
                               "reference implementation"):
                    self.assertNotIn(phrase, text)


class EngineIdTest(unittest.TestCase):

    def test_the_method_alone_decides(self):
        for engine in (None, "", "auto", "rust", "r", "R", "  AUTO  ", "junk"):
            with self.subTest(engine=engine):
                self.assertEqual(MOREServlet.engineIdFor("PLS1", engine),
                                 "rust-pls1")
                self.assertEqual(MOREServlet.engineIdFor("MLR", engine),
                                 "rust-mlr")

    def test_a_stored_job_that_names_r_still_resolves(self):
        """Jobs submitted before the removal carry engine="r" in Mongo. They
        have to keep opening, and they now run on the port."""
        self.assertEqual(MOREServlet.engineIdFor("PLS1", "r"), "rust-pls1")

    def test_an_unknown_method_resolves_to_nothing_rather_than_something(self):
        self.assertIsNone(MOREServlet.engineIdFor("PLS2"))
        self.assertIsNone(MOREServlet.engineIdFor(""))
        self.assertIsNone(MOREServlet.engineIdFor(None))


class AvailabilityTest(unittest.TestCase):
    """What /more_backends tells the browser."""

    def _report(self, binary):
        with mock.patch.object(MOREServlet, "moreBinary", return_value=binary):
            return MOREServlet.describeMOREBackends()

    def test_a_binary_makes_both_methods_available(self):
        report = self._report("/x/more-rs")
        self.assertTrue(report["anyAvailable"])
        self.assertEqual([e["available"] for e in report["engines"]],
                         [True, True])
        self.assertEqual(report["default"], MOREServlet.DEFAULT_MORE_ENGINE)

    def test_no_binary_disables_everything_and_says_why(self):
        """Both entries stand or fall together now -- one binary runs both
        methods -- so there is no "available instead" to offer."""
        report = self._report("")
        self.assertFalse(report["anyAvailable"])
        self.assertEqual([e["available"] for e in report["engines"]],
                         [False, False])
        self.assertIsNone(report["default"])
        for entry in report["engines"]:
            self.assertIn("no more-rs binary", entry["unavailableReason"])

    def test_the_reason_is_empty_when_there_is_nothing_wrong(self):
        for entry in self._report("/x/more-rs")["engines"]:
            self.assertEqual(entry["unavailableReason"], "")

    def test_the_report_does_not_mutate_the_catalogue(self):
        """`available` is per-host and must not be written back onto the
        module-level catalogue, which is shared by every request."""
        self._report("")
        for entry in MOREServlet.MORE_ENGINES:
            self.assertNotIn("available", entry)

    def test_refresh_is_accepted_and_harmless(self):
        """It used to force an R re-probe. Callers still pass it."""
        with mock.patch.object(MOREServlet, "moreBinary", return_value="/x/m"):
            self.assertEqual(MOREServlet.describeMOREBackends(refresh=True),
                             MOREServlet.describeMOREBackends())


class RefusalTest(unittest.TestCase):
    """Submission-time refusal: the half that used to be a fallback.

    Hiding an option in the dropdown is necessary and not sufficient -- a stale
    client, a resubmitted job or a scripted POST all reach the servlet.
    """

    def _refuse(self, method, engine=None, binary="/x/more-rs"):
        with mock.patch.object(MOREServlet, "moreBinary", return_value=binary):
            return MOREServlet.engineRefusal(method, engine)

    def test_a_runnable_job_is_not_refused(self):
        for method in ("PLS1", "MLR"):
            for engine in (None, "auto", "rust", "r"):
                with self.subTest(method=method, engine=engine):
                    self.assertIsNone(self._refuse(method, engine))

    def test_a_host_with_no_binary_refuses_every_method(self):
        for method in ("PLS1", "MLR"):
            with self.subTest(method=method):
                message = self._refuse(method, binary="")
                self.assertIsNotNone(message)
                self.assertIn("no more-rs binary", message)
                self.assertIn("administrator", message)

    def test_the_refusal_names_the_server_and_not_the_dataset(self):
        """Blaming the upload for a missing binary sends the user to edit files
        that are fine. This is the failure the R fallback used to produce, one
        layer down and half an hour later."""
        message = self._refuse("PLS1", binary="")
        self.assertNotIn("your data", message.lower())
        self.assertIn("this server", message.lower())

    def test_a_method_the_catalogue_does_not_cover_is_refused_by_name(self):
        self.assertIn("PLS2", self._refuse("PLS2"))


class ApplyEngineChoiceTest(unittest.TestCase):
    """What STEP1 does with the `more_engine` form field."""

    def setUp(self):
        self.job = MOREJob("job1", "user1", "/tmp")

    def _apply(self, formFields, binary="/x/more-rs"):
        with mock.patch.object(MOREServlet, "moreBinary", return_value=binary):
            return MOREServlet._applyEngineChoice(self.job, formFields)

    def test_a_new_job_defaults_to_auto(self):
        """MOREJob's own default, so a job built and never submitted through
        the form still resolves rather than carrying None."""
        self.assertEqual(MOREJob("j", "u", "/tmp").engine, "auto")

    def test_the_picked_id_sets_both_method_and_engine(self):
        self.assertIsNone(self._apply({"more_engine": "rust-mlr"}))
        self.assertEqual(self.job.method, "MLR")
        self.assertEqual(self.job.engine, "rust")

    def test_no_engine_field_leaves_the_method_the_form_already_set(self):
        self.job.method = "MLR"
        self.assertIsNone(self._apply({}))
        self.assertEqual(self.job.method, "MLR")
        self.assertEqual(self.job.engine, MOREServlet.AUTO_ENGINE)

    def test_a_retired_id_is_ignored_rather_than_obeyed(self):
        """`r-pls1` and `r-mlr` are still posted by a cached client. An id the
        catalogue does not know must not silently set a method: falling back to
        `auto` keeps whatever `more_method` said, which is what that client
        also posted."""
        self.job.method = "PLS1"
        for retired in ("r-pls1", "r-mlr", "nonsense"):
            with self.subTest(retired=retired):
                self.assertIsNone(self._apply({"more_engine": retired}))
                self.assertEqual(self.job.method, "PLS1")
                self.assertEqual(self.job.engine, MOREServlet.AUTO_ENGINE)

    def test_a_host_with_no_binary_comes_back_as_a_refusal(self):
        message = self._apply({"more_engine": "rust-pls1"}, binary="")
        self.assertIsNotNone(message)
        self.assertIn("no more-rs binary", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
