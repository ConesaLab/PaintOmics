"""Regression tests for the report handler that died on a mail outage.

Production symptom (2026-08-19), reported by a user submitting the organism
request form:

    Oops..Internal error!
    Exception: AT AdminServlet.py: adminServletSendReport. ERROR MESSAGE:
    SMTP_PASSWORD is not configured. Please set the environment variable.

Two independent defects met there:

  1. paintomics4.ini declared `env-file = /etc/paintomics/paintomics.env`.
     uWSGI has no such option (--env, --envdir and --unenv are the real ones)
     and ignores unknown ini keys silently, so SMTP_PASSWORD was empty in every
     worker. Delivery to systemd's EnvironmentFile= is the fix; this file pins
     the ini so the dead option cannot come back.

  2. adminServletSendReport called sendEmail with no guard, so ANY delivery
     failure -- unset credential, exhausted provider quota, blocked egress --
     became an internal error AND discarded the user's report. Organism
     requests arrive through this handler, so those were being lost outright.

Run:  PYTHONPATH=PaintomicsServer python3 PaintomicsServer/src/tests/test_report_survives_mail_outage.py
"""
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))


def _ensureServerConfig():
    """Make src.conf.serverconf importable on a checkout that has no config.

    serverconf.py is gitignored and installed from the tracked template at
    deploy time, so a clean checkout (CI, a fresh worktree) has none and
    importing any servlet dies at module level. test_release_hygiene already
    pins that the template imports with no environment set, so binding it under
    the real module name gives this test the same names the app would see.
    """
    try:
        import src.conf.serverconf                       # noqa: F401 -- availability probe: importable conf means nothing to stub
        return
    except ImportError:
        pass

    template = os.path.join(HERE, "..", "resources", "example_serverconf.py")
    spec = importlib.util.spec_from_file_location("src.conf.serverconf", template)
    module = importlib.util.module_from_spec(spec)
    sys.modules["src.conf.serverconf"] = module
    spec.loader.exec_module(module)


_ensureServerConfig()

import src.servlets.AdminServlet as AdminServlet
from src.classes.Report import Report


class _Cookies(object):
    def get(self, _name):
        return None            # anonymous reporter: the form supplies identity


class _Request(object):
    def __init__(self, form):
        self.cookies = _Cookies()
        self.form = form


class _Response(object):
    """Minimal stand-in recording what the handler decided."""
    def __init__(self):
        self.content = None
        self.status = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        self.status = status


def _runHandler(mailBehaviour, calls, form=None):
    """Drive the real handler with a stubbed DAO and a stubbed mailer."""
    form = form or {"type": "specie_request",
                    "message": "Please add Fusarium oxysporum",
                    "fromEmail": "requester@example.org",
                    "fromName": "A Researcher"}

    class _StubDAO(object):
        def insert(self, instance):
            calls.append(("insert", instance.report_type, instance.message))
            return "stub-report-id"

        def markDelivered(self, reportID, delivered, deliveryError=""):
            calls.append(("markDelivered", reportID, delivered))
            return True

    def _stubSendEmail(*args, **kwargs):
        calls.append(("sendEmail",))
        mailBehaviour()

    originalDAO, originalMail = AdminServlet.ReportDAO, AdminServlet.sendEmail
    AdminServlet.ReportDAO, AdminServlet.sendEmail = _StubDAO, _stubSendEmail
    try:
        response = _Response()
        AdminServlet.adminServletSendReport(_Request(form), response, "/tmp")
        return response
    finally:
        AdminServlet.ReportDAO, AdminServlet.sendEmail = originalDAO, originalMail


def _fail():
    raise Exception("SMTP_PASSWORD is not configured. Please set the environment variable.")


def _succeed():
    return None


class MailOutageTest(unittest.TestCase):
    def test_unset_credential_no_longer_produces_an_internal_error(self):
        calls = []
        response = _runHandler(_fail, calls)
        # The exact production failure must not reach the reporter as an error.
        self.assertIsNotNone(response.content, "handler produced no response")
        self.assertTrue(response.content.get("success"),
                        "a mail outage still fails the submission: %r" % (response.content,))
        self.assertFalse(response.content.get("delivered"),
                         "delivery must be reported honestly as not delivered")

    def test_the_report_is_stored_before_delivery_is_attempted(self):
        calls = []
        _runHandler(_fail, calls)
        kinds = [c[0] for c in calls]
        self.assertIn("insert", kinds, "the report was never stored")
        self.assertIn("sendEmail", kinds, "delivery was never attempted")
        self.assertLess(kinds.index("insert"), kinds.index("sendEmail"),
                        "the report must be stored BEFORE the fallible send")

    def test_the_stored_report_carries_the_users_words(self):
        calls = []
        _runHandler(_fail, calls)
        inserted = [c for c in calls if c[0] == "insert"][0]
        self.assertEqual(inserted[1], "specie_request")
        self.assertIn("Fusarium oxysporum", inserted[2])

    def test_a_failed_delivery_is_recorded_against_the_report(self):
        calls = []
        _runHandler(_fail, calls)
        marks = [c for c in calls if c[0] == "markDelivered"]
        self.assertTrue(marks, "delivery outcome was never recorded")
        self.assertFalse(marks[0][2])

    def test_the_happy_path_still_reports_delivered(self):
        calls = []
        response = _runHandler(_succeed, calls)
        self.assertTrue(response.content.get("success"))
        self.assertTrue(response.content.get("delivered"),
                        "a successful send must still be reported as delivered")
        marks = [c for c in calls if c[0] == "markDelivered"]
        self.assertTrue(marks and marks[0][2])

    def test_storage_failure_alone_does_not_fail_the_submission(self):
        """Mongo down AND mail down is still not an internal error."""
        class _BrokenDAO(object):
            def insert(self, instance):
                raise Exception("mongo unreachable")

            def markDelivered(self, *a, **k):
                raise Exception("mongo unreachable")

        originalDAO, originalMail = AdminServlet.ReportDAO, AdminServlet.sendEmail
        AdminServlet.ReportDAO = _BrokenDAO
        AdminServlet.sendEmail = lambda *a, **k: _fail()
        try:
            response = _Response()
            AdminServlet.adminServletSendReport(
                _Request({"type": "error", "message": "boom"}), response, "/tmp")
            self.assertTrue(response.content.get("success"))
        finally:
            AdminServlet.ReportDAO, AdminServlet.sendEmail = originalDAO, originalMail


class AdminReportsViewTest(unittest.TestCase):
    """The panel that makes stored reports visible without a Mongo shell.

    With outbound mail unavailable, this view is the ONLY place an organism
    request appears, so 'it is stored' is only half the fix.
    """

    def _run(self, handler, calls, admin=True, extra=None):
        class _StubSessionManager(object):
            def isValidAdminUser(self, userID, userName, sessionToken):
                calls.append(("adminCheck", userID))
                if not admin:
                    raise Exception("Invalid admin user")
                return True

        class _StubDAO(object):
            def findAll(self, otherParams=None):
                calls.append(("findAll",))
                stored, undelivered = Report("specie_request"), Report("error")
                stored.setMessage("Please add Fusarium oxysporum")
                stored.setDelivered(True)
                stored.setReportID("id-delivered")
                undelivered.setMessage("boom")
                undelivered.setDelivered(False)
                undelivered.setReportID("id-undelivered")
                return [stored, undelivered]

            def remove(self, reportID, otherParams=None):
                calls.append(("remove", reportID))
                return True

            def closeConnection(self):
                calls.append(("close",))
                return True

        originalDAO = AdminServlet.ReportDAO
        originalSession = AdminServlet.UserSessionManager
        AdminServlet.ReportDAO = _StubDAO
        AdminServlet.UserSessionManager = _StubSessionManager
        try:
            response = _Response()
            if extra is None:
                handler(_Request({}), response)
            else:
                handler(_Request({}), response, extra)
            return response
        finally:
            AdminServlet.ReportDAO = originalDAO
            AdminServlet.UserSessionManager = originalSession

    def test_reports_are_listed_for_an_admin(self):
        calls = []
        response = self._run(AdminServlet.adminServletGetReports, calls)
        self.assertTrue(response.content.get("success"), response.content)
        reports = response.content.get("reportList")
        self.assertEqual(len(reports), 2)
        self.assertIn("Fusarium oxysporum", reports[0]["message"])

    def test_the_undelivered_count_is_reported(self):
        """So an operator is told these never reached an inbox."""
        calls = []
        response = self._run(AdminServlet.adminServletGetReports, calls)
        self.assertEqual(response.content.get("undelivered"), 1)

    def test_listing_requires_an_admin(self):
        calls = []
        response = self._run(AdminServlet.adminServletGetReports, calls, admin=False)
        self.assertIn(("adminCheck", None), calls)
        self.assertFalse((response.content or {}).get("success"),
                         "a non-admin was served the report list: %r" % (response.content,))

    def test_dismissing_requires_an_admin(self):
        calls = []
        self._run(AdminServlet.adminServletDeleteReport, calls, admin=False, extra="id-1")
        self.assertNotIn(("remove", "id-1"), calls,
                         "a non-admin deleted a report")

    def test_an_admin_can_dismiss_a_report(self):
        calls = []
        response = self._run(AdminServlet.adminServletDeleteReport, calls, extra="id-1")
        self.assertTrue(response.content.get("success"))
        self.assertIn(("remove", "id-1"), calls)

    def test_the_mongo_connection_is_closed(self):
        """DBmanager builds a client per DAO; the panel polls this route."""
        calls = []
        self._run(AdminServlet.adminServletGetReports, calls)
        self.assertIn(("close",), calls)


class ReportRoutesTest(unittest.TestCase):
    def test_the_admin_report_routes_are_registered(self):
        path = os.path.join(REPO, "PaintomicsServer", "src", "paintomicsserver.py")
        source = open(path, encoding="utf-8").read()
        self.assertIn("'/api/admin/reports/'", source,
                      "the reports listing route is not registered")
        self.assertIn("adminServletGetReports", source)
        self.assertIn("adminServletDeleteReport", source)


class LegacyReportBodyTest(unittest.TestCase):
    """The request dialogs used to post an HTML fragment as `message`.

    The maintainer notification escapes the body now, because /dm_sendReport
    takes no session and `message` is free-form input. The dialogs post plain
    text since this release, but the endpoint cannot assume that: a tab open
    across a deploy keeps posting the fragment, and no session expires it.
    """

    def test_the_legacy_fragment_becomes_labelled_lines(self):
        body = ("<p><b>Specie:</b> Bos taurus (cow)</p>"
                "<p><b>Comments:</b>We use Ensembl IDs &amp; &lt;2000 genes</p>")
        self.assertEqual(
            "Specie: Bos taurus (cow)\nComments: We use Ensembl IDs & <2000 genes",
            AdminServlet._plainTextReportBody(body))

    def test_an_address_in_angle_brackets_survives(self):
        """The contact form wrote it bare, so HTML ate it as an unknown tag.

        Only the five tags that fragment was built from are treated as markup;
        anything else between angle brackets is text. That makes the converted
        mail better than the one a client used to render, where the maintainers
        never saw who had written in.
        """
        body = ("<p><b>From:</b> Ada Lovelace<ada@example.org></p>"
                "<p><b>Message:</b>Hi there</p>")
        self.assertEqual("From: Ada Lovelace<ada@example.org>\nMessage: Hi there",
                         AdminServlet._plainTextReportBody(body))

    def test_a_typed_comparison_does_not_swallow_the_tag_after_it(self):
        """The old client concatenated the textarea raw, so a bare "<" is easy.

        "<2000 genes" and "padj<0.05" are ordinary things to write in this
        domain. Pairing that "<" with the next ">" anywhere later in the string
        matched the ">" of the real closing tag, so the tag was emitted as text
        -- the maintainer read a literal "</p>" and lost the line break, and
        with another field after it the two ran together.

        The first version of this test used "&lt;2000", which is what an
        escaping client would send. This one does not escape, because the
        client that still posts this shape does not either.
        """
        self.assertEqual(
            "Comments: We use <2000 genes",
            AdminServlet._plainTextReportBody(
                "<p><b>Comments:</b>We use <2000 genes</p>"))

        self.assertEqual(
            "Specie: Bos taurus\nComments: only <2000 genes, padj<0.05",
            AdminServlet._plainTextReportBody(
                "<p><b>Specie:</b> Bos taurus</p>"
                "<p><b>Comments:</b>only <2000 genes, padj<0.05</p>"))

    def test_both_comparison_directions_survive(self):
        self.assertEqual(
            "a: 3 < 4 and 5 > 2",
            AdminServlet._plainTextReportBody("<p><b>a:</b>3 < 4 and 5 > 2</p>"))

    def test_a_plain_text_report_is_returned_untouched(self):
        """Error reports are plain text and carry <module> in a traceback.

        Converting unconditionally would eat it, which is why this is keyed on
        the fragment's own opening rather than applied to every body.
        """
        for body in ('Oops..Internal error!\nTraceback (most recent call last):\n'
                     '  File "<module>", line 3\nKeyError',
                     "Specie: Bos taurus\n\nComments: none",
                     "", None):
            expected = "" if body is None else body
            self.assertEqual(expected, AdminServlet._plainTextReportBody(body))

    def test_markup_inside_the_fragment_is_kept_as_text_not_dropped(self):
        """Whatever survives is escaped downstream, so keeping it is safe.

        Dropping it would be the dangerous direction: it hides from the
        maintainer what the reporter actually sent.
        """
        converted = AdminServlet._plainTextReportBody(
            "<p><b>Specie:</b> x</p><p><b>C:</b><script>alert(1)</script></p>")
        self.assertIn("<script>alert(1)</script>", converted)


class ReportBodySurvivesTheAdminPanelTest(unittest.TestCase):
    """A report body is rendered twice, and the second renderer strips tags.

    The mail escapes the body, so anything angle-bracketed shows up fine there.
    The admin panel does not: report-list-service.js runs the stored message
    through ``/<[^>]*>/g`` to display it as text, because reports stored before
    this release are HTML. So a bare ``<ada@example.org>`` in a plain-text body
    survives the mail and is eaten by the panel.
    """

    #: What admin/controllers/report-list-service.js does to a stored message.
    _TAG = re.compile(r"<[^>]*>")

    def _clientSource(self):
        path = os.path.join(REPO, "PaintomicsClient", "public_html", "app",
                            "controller", "DataManagementController.js")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_no_report_dialog_wraps_a_value_in_angle_brackets(self):
        """Asserts the property, not one spelling of the fix."""
        source = self._clientSource()
        for handler in ("requestNewSpecieHandler", "sendReportHandler"):
            match = re.search(r"this\.%s = function\(\)\{.*?\n\};" % handler,
                              source, re.S)
            self.assertIsNotNone(match, "%s is gone" % handler)
            built = re.search(r'var message\s*=\s*(.*?);', match.group(0), re.S)
            self.assertIsNotNone(built, "%s builds no message" % handler)
            body = built.group(1)
            self.assertNotIn('"<"', body,
                             "%s wraps a value in angle brackets. The mail escapes "
                             "it, but the admin panel strips tags out of the stored "
                             "message and the value disappears there." % handler)
            self.assertNotIn('" <"', body,
                             "%s wraps a value in angle brackets; see above." % handler)

    def test_a_contact_body_keeps_its_address_through_the_panel_stripper(self):
        source = self._clientSource()
        match = re.search(r"this\.sendReportHandler = function\(\)\{.*?\n\};",
                          source, re.S)
        self.assertIsNotNone(match)
        # The body the handler builds, with the two field reads substituted.
        built = re.search(r'var message\s*=\s*(.*?);', match.group(0), re.S).group(1)
        rendered = (built.replace("userName", '"Ada Lovelace"')
                         .replace("userEmail", '"ada@example.org"'))
        self.assertIn("ada@example.org", rendered)
        # Everything the panel would strip, stripped.
        self.assertIn("ada@example.org", self._TAG.sub(" ", rendered),
                      "the address does not survive the admin panel's tag "
                      "stripper, so the report body shows a sender with no address")


class EmailTemplateTest(unittest.TestCase):
    """The report email rendered a broken logo and named the wrong mailbox.

    Both were config-shaped bugs that no test could see:
      * PAINTOMICS_LOGO_PATH had no file extension, so the <img> URL 404'd and
        every recipient saw a broken-image icon.
      * The "Problems? E-mail ..." footer hardcoded one address in two places,
        so it kept naming a mailbox the deployment had moved off.
    """

    def _templateLogoPath(self):
        """The default in the tracked template, which is what fresh deploys get.

        serverconf.py itself is gitignored and per-machine, so asserting on it
        would pass or fail by accident of the local box.
        """
        path = os.path.join(HERE, "..", "resources", "example_serverconf.py")
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("PAINTOMICS_LOGO_PATH"):
                    return line.split('"')[-2]
        self.fail("example_serverconf.py defines no PAINTOMICS_LOGO_PATH")

    def test_the_logo_path_names_a_file_that_exists(self):
        PAINTOMICS_LOGO_PATH = self._templateLogoPath()

        extension = os.path.splitext(PAINTOMICS_LOGO_PATH)[1].lower()
        self.assertIn(extension, (".png", ".jpg", ".jpeg", ".gif", ".svg"),
                      "PAINTOMICS_LOGO_PATH (%s) has no image file extension, so "
                      "the email <img> URL 404s and renders as a broken image"
                      % PAINTOMICS_LOGO_PATH)

        served = os.path.join(REPO, "PaintomicsClient", "public_html",
                              PAINTOMICS_LOGO_PATH.lstrip("/"))
        if os.path.isdir(os.path.join(REPO, "PaintomicsClient", "public_html")):
            self.assertTrue(os.path.isfile(served),
                            "PAINTOMICS_LOGO_PATH points at %s, which is not in "
                            "public_html" % PAINTOMICS_LOGO_PATH)

    def test_no_email_template_hardcodes_a_contact_address(self):
        """Every mail footer must follow config, or it drifts from the mailbox.

        Four templates hardcoded the same literal address -- the report mail,
        both account mails, and the quota warning -- so moving the project
        mailbox left all four naming the old one.
        """
        roots = [os.path.join(REPO, "PaintomicsServer", "src", "servlets"),
                 os.path.join(REPO, "PaintomicsServer", "src", "AdminTools", "scripts")]

        offenders = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    lines = handle.readlines()
                for number, line in enumerate(lines, 1):
                    if line.strip().startswith("#"):
                        continue              # licence header names a contact
                    if "mailto:" in line and "@" in line and '"' in line:
                        # a quoted literal address next to mailto: is the bug
                        if re.search(r'"[^"]*@[^"]*\.[a-z]{2,}[^"]*"', line):
                            offenders.append("%s:%d" % (name, number))
        self.assertEqual(
            offenders, [],
            "these email templates hardcode a contact address instead of using "
            "the configured sender: " + "; ".join(offenders))


class UwsgiIniTest(unittest.TestCase):
    def test_ini_does_not_use_the_nonexistent_env_file_option(self):
        path = os.path.join(REPO, "paintomics4.ini")
        if not os.path.isfile(path):
            self.skipTest("paintomics4.ini not present in this checkout")
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            self.assertNotEqual(
                key, "env-file",
                "paintomics4.ini:%d sets 'env-file', which uWSGI does not "
                "implement and silently ignores. Secrets must come from the "
                "systemd EnvironmentFile= drop-in instead." % number)


if __name__ == "__main__":
    unittest.main(verbosity=2)
