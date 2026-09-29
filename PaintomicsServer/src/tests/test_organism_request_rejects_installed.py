#!/usr/bin/env python3
"""An organism that is already installed cannot be requested.

What happened
-------------
2026-09-03 08:36, paintomics.uv.es: an anonymous visitor submitted the
"Request an organism" dialog for Homo sapiens (human). hsa has been installed
on that server for years and sits in the Organism combo on the same page. The
request was stored, mailed to the developers, and answered nobody's question:
the visitor had missed the combo, and the dialog let them ask for what they
already had.

The rule
--------
"Installed" means listed in KEGG_DATA_DIR/current/species.json -- exactly the
list the step 1 Organism combo is drawn from, so an organism this rejects is
one the visitor can pick right now. The dialog checks first, so the usual
outcome is a hint next to the field and no round trip; the servlet checks
again, because the dialog is a cached JavaScript file and the request is a
POST anyone can make. The servlet answers success=false with installed=true
and the organism's display name, stores nothing and mails nobody.

A client older than this change sends no `specie`/`specieCode` fields, only
the HTML message it always sent, so the servlet also reads the organism out of
that message. An unreadable species.json blocks nothing: a request that gets
through is a nuisance, a request that cannot be made is a lost organism.

Run:  PYTHONPATH=PaintomicsServer python3 PaintomicsServer/src/tests/test_organism_request_rejects_installed.py
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))


def _ensureServerConfig():
    """serverconf.py is gitignored; bind the tracked template when it is absent."""
    try:
        import src.conf.serverconf                       # noqa: F401 -- availability probe
        return
    except ImportError:
        pass
    template = os.path.join(HERE, "..", "resources", "example_serverconf.py")
    spec = importlib.util.spec_from_file_location("src.conf.serverconf", template)
    module = importlib.util.module_from_spec(spec)
    sys.modules["src.conf.serverconf"] = module
    spec.loader.exec_module(module)


_ensureServerConfig()

import src.servlets.AdminServlet as AdminServlet   # noqa: E402

INSTALLED = [{"name": "Homo sapiens (human)", "value": "hsa"},
             {"name": "Mus musculus (house mouse)", "value": "mmu"}]


class _Cookies(object):
    def get(self, _name):
        return None


class _Request(object):
    def __init__(self, form):
        self.cookies = _Cookies()
        self.form = form


class _Response(object):
    def __init__(self):
        self.content = None

    def setContent(self, content):
        self.content = content

    def setStatus(self, status):
        pass


class OrganismRequestTest(unittest.TestCase):
    def setUp(self):
        self.keggDataDir = tempfile.mkdtemp(prefix="paintomics-species-")
        os.makedirs(os.path.join(self.keggDataDir, "current"))
        with io.open(os.path.join(self.keggDataDir, "current", "species.json"),
                     "w", encoding="utf-8") as handle:
            json.dump({"success": True, "species": INSTALLED}, handle)

        self.calls = []
        calls = self.calls

        class _StubDAO(object):
            def insert(self, instance):
                calls.append(("insert", instance.report_type, instance.message))
                return "stub-report-id"

            def markDelivered(self, reportID, delivered, deliveryError=""):
                calls.append(("markDelivered", reportID, delivered))
                return True

        def _stubSendEmail(*args, **kwargs):
            calls.append(("sendEmail",))

        self._saved = (AdminServlet.ReportDAO, AdminServlet.sendEmail,
                       AdminServlet.KEGG_DATA_DIR)
        AdminServlet.ReportDAO = _StubDAO
        AdminServlet.sendEmail = _stubSendEmail
        AdminServlet.KEGG_DATA_DIR = self.keggDataDir + "/"

    def tearDown(self):
        (AdminServlet.ReportDAO, AdminServlet.sendEmail,
         AdminServlet.KEGG_DATA_DIR) = self._saved
        shutil.rmtree(self.keggDataDir, ignore_errors=True)

    def _request(self, **fields):
        form = {"type": "specie_request",
                "fromEmail": "requester@example.org",
                "fromName": "A Researcher",
                "message": "<p><b>Specie:</b> %s</p><p><b>Comments:</b></p>"
                           % fields.get("specie", "")}
        form.update(fields)
        response = _Response()
        AdminServlet.adminServletSendReport(_Request(form), response, self.keggDataDir)
        return response.content

    def _stored(self):
        return [call for call in self.calls if call[0] == "insert"]

    def _mailed(self):
        return [call for call in self.calls if call[0] == "sendEmail"]

    # -- rejected ------------------------------------------------------------

    def test_an_installed_code_is_rejected_stored_nowhere_and_mailed_to_nobody(self):
        content = self._request(specie="Homo sapiens (human)", specieCode="hsa")

        self.assertIs(False, content["success"])
        self.assertIs(True, content["installed"])
        self.assertEqual("Homo sapiens (human)", content["organism"])
        self.assertEqual("hsa", content["code"])
        self.assertIn("already installed", content["errorMessage"])
        self.assertEqual([], self._stored())
        self.assertEqual([], self._mailed())

    def test_an_installed_name_is_rejected_whatever_its_case_or_spacing(self):
        """The combo lets the visitor type; the typed name still counts."""
        content = self._request(specie="  homo SAPIENS (Human) ", specieCode="")
        self.assertIs(False, content["success"])
        self.assertEqual("Homo sapiens (human)", content["organism"])

    def test_a_code_typed_as_the_name_is_rejected(self):
        content = self._request(specie="hsa", specieCode="")
        self.assertIs(False, content["success"])
        self.assertEqual("hsa", content["code"])

    def test_a_client_that_predates_the_fields_is_read_from_its_message(self):
        """Cached JavaScript sends only the HTML it always sent."""
        form = {"type": "specie_request", "fromEmail": "r@example.org",
                "fromName": "R",
                "message": "<p><b>Specie:</b> Mus musculus (house mouse)</p>"
                           "<p><b>Comments:</b>Entrez IDs please</p>"}
        response = _Response()
        AdminServlet.adminServletSendReport(_Request(form), response, self.keggDataDir)

        self.assertIs(False, response.content["success"])
        self.assertEqual("mmu", response.content["code"])
        self.assertEqual([], self._stored())

    def test_an_old_client_message_is_read_in_bounded_time(self):
        """The message is untrusted text of up to MAX_FORM_MEMORY_SIZE, posted
        without a session. Reading the organism out of it was a regex with
        three quantifiers overlapping on whitespace: a label followed by a run
        of spaces and no </p> cost cubic time, so one request pinned a worker
        for hours. Dropping the inner \\s* leaves a quadratic scan when the
        label repeats. Run in a subprocess so a regression fails on a timeout
        instead of hanging the suite."""
        probe = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import test_organism_request_rejects_installed as suite\n"
            "servlet = suite.AdminServlet\n"
            "assert servlet._installedOrganism(None, None, '<p><b>Specie:</b>' + ' ' * 20000) is None\n"
            "assert servlet._installedOrganism(None, None, '<b>Specie:</b>' * 200000) is None\n"
            "print('bounded')\n"
        ) % HERE
        try:
            result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                                    text=True, timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("reading the organism out of a 20 kB whitespace run and a "
                      "2.8 MB run of repeated labels did not finish in 20 s")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("bounded", result.stdout)

    def test_a_request_that_names_no_organism_is_refused_stored_nowhere(self):
        """2026-09-26: two requests reached the maintainer as "Specie: null"."""
        for specie, code, message in (
                ("", "", "Specie: null\n\nComments: My gene IDs are Phytozome"),   # the reported shape
                (None, None, None),
                ("null", "", "Specie: null"),
                ("  undefined ", " ", ""),
                ("", "", "<p><b>Specie:</b> null</p><p><b>Comments:</b>x</p>")):  # a cached old client
            form = {"type": "specie_request", "fromEmail": "r@example.org", "fromName": "R",
                    "specie": specie, "specieCode": code, "message": message}
            response = _Response()
            AdminServlet.adminServletSendReport(_Request(form), response, self.keggDataDir)
            self.assertIs(False, response.content["success"], repr((specie, code, message)))
            self.assertIs(True, response.content["missingOrganism"])
            self.assertIn("Organism", response.content["errorMessage"])
        self.assertEqual([], self._stored())
        self.assertEqual([], self._mailed())

    # -- allowed -------------------------------------------------------------

    def test_an_organism_named_only_by_its_code_or_an_old_message_goes_through(self):
        for fields in ({"specie": "", "specieCode": "fox"},
                       {"message": "<p><b>Specie:</b> Fusarium oxysporum</p><p><b>Comments:</b></p>"}):
            form = {"type": "specie_request", "fromEmail": "r@example.org", "fromName": "R"}
            form.update(fields)
            response = _Response()
            AdminServlet.adminServletSendReport(_Request(form), response, self.keggDataDir)
            self.assertIs(True, response.content["success"], fields)
        self.assertEqual(2, len(self._stored()))

    def test_an_organism_that_is_not_installed_goes_through(self):
        content = self._request(specie="Fusarium oxysporum", specieCode="fox")

        self.assertIs(True, content["success"])
        self.assertEqual(1, len(self._stored()))
        self.assertEqual(1, len(self._mailed()))

    def test_an_unreadable_species_list_blocks_nothing(self):
        shutil.rmtree(self.keggDataDir)
        content = self._request(specie="Homo sapiens (human)", specieCode="hsa")

        self.assertIs(True, content["success"])
        self.assertEqual(1, len(self._stored()))

    def test_other_report_types_are_not_screened(self):
        """An error report that mentions hsa is still an error report."""
        form = {"type": "error", "fromEmail": "r@example.org", "fromName": "R",
                "message": "hsa job crashed", "specie": "Homo sapiens (human)",
                "specieCode": "hsa"}
        response = _Response()
        AdminServlet.adminServletSendReport(_Request(form), response, self.keggDataDir)

        self.assertIs(True, response.content["success"])
        self.assertEqual(1, len(self._stored()))


CLIENT = os.path.join(REPO, "PaintomicsClient", "public_html")


def _read(*parts):
    with io.open(os.path.join(CLIENT, *parts), encoding="utf-8") as handle:
        return handle.read()


class OldClientMessageTest(unittest.TestCase):
    """_specieFromMessage reads <p><b>Specie:</b> name</p> and nothing else."""

    def test_reads_the_name_between_the_label_and_the_end_of_its_paragraph(self):
        self.assertEqual("Homo sapiens (human)", AdminServlet._specieFromMessage(
            "<p><b>Specie:</b> Homo sapiens (human)</p><p><b>Comments:</b>hi</p>"))

    def test_the_label_is_read_whatever_its_case_or_spacing(self):
        self.assertEqual("Mus musculus", AdminServlet._specieFromMessage(
            "<p><b> SPECIE: </b>\n Mus musculus \n</p>"))

    def test_a_message_that_does_not_carry_the_label_names_nothing(self):
        for message in (None, "", "<p>hello</p>",
                        "<b>Specie:</b> hsa",             # no paragraph end
                        "<b>Specie: hsa</b> x</p>",       # text inside the label
                        "Specie: hsa</p>"):               # no bold end
            self.assertIsNone(AdminServlet._specieFromMessage(message), repr(message))


class DialogWiringTest(unittest.TestCase):
    """The dialog checks before it sends, and tells the servlet what it chose.

    Source assertions, as in test_database_checkboxes_follow_the_server: the
    client has no test harness. Verified in Chrome -- picking Homo sapiens
    shows the hint and sends nothing; Fusarium oxysporum sends.
    """

    def setUp(self):
        source = _read("app", "controller", "DataManagementController.js")
        match = re.search(r"this\.requestNewSpecieHandler = function\(\)\{.*?\n\};",
                          source, re.S)
        self.assertTrue(match, "requestNewSpecieHandler is gone")
        self.dialog = match.group(0)

    def test_the_dialog_reads_the_installed_list_the_form_uses(self):
        self.assertIn("SERVER_URL_GET_AVAILABLE_SPECIES", self.dialog)

    def test_installed_organisms_are_kept_out_of_the_request_list(self):
        self.assertRegex(self.dialog, r"addFilter\(")

    def test_the_request_names_the_organism_as_fields_not_only_as_html(self):
        self.assertRegex(self.dialog, r"specie:\s*\w+")
        self.assertRegex(self.dialog, r"specieCode:\s*\w+")

    def test_an_installed_choice_is_stopped_before_the_round_trip(self):
        self.assertIn("already installed", self.dialog)
        self.assertNotIn("sendReportMessage(type, message);", self.dialog,
                         "the request is sent without the organism fields")

    def test_an_empty_organism_field_stops_the_send(self):
        """allowBlank never stopped the button; the handler has to check."""
        send = self.dialog[self.dialog.index("text: 'Send request'"):]
        check = send.index("markInvalid(")
        self.assertLess(check, send.index("sendReportMessage("))
        self.assertRegex(send[:check], r"getRawValue\(\)")

    def test_an_installed_organism_points_unmapped_identifiers_to_contact(self):
        self.assertIn("Contact by email", self.dialog)
        mainView = _read("app", "view", "MainView.js")
        self.assertIn("</i>Contact by email</li>", mainView, "the menu entry the hint names is gone")

    def test_the_sender_shows_the_servlet_refusal_as_a_hint_not_a_crash(self):
        """success=false used to mean showErrorMessage, whose dialog carries a
        'report this error' button -- a refusal that invited an error report."""
        util = _read("app", "view", "common", "Util.js")
        match = re.search(r"function sendReportMessage\(.*?\n\}", util, re.S)
        self.assertTrue(match)
        self.assertIn("response.installed", match.group(0))
        self.assertRegex(match.group(0), r"extra")

    def test_the_edited_scripts_are_cache_busted(self):
        index = _read("index.html")
        self.assertRegex(index, r'Util\.js\?v=(2\.9|[3-9]\.\d+)')


if __name__ == "__main__":
    unittest.main(verbosity=2)
