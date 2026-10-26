#!/usr/bin/env python3
"""A pathway image export must survive Werkzeug's form-memory limit.

WHAT BROKE
----------
`d6bcf643` ("Upgrade the Flask stack") pinned `Werkzeug==3.1.8`. Werkzeug 3.1
changed `max_form_memory_size` in two ways at once:

  * its default went from unlimited to 500_000 bytes, and
  * it is now applied to `application/x-www-form-urlencoded` bodies, where
    before it only bounded non-file fields of a *multipart* body.

    # werkzeug/formparser.py -- FormDataParser._parse_urlencoded
    if (self.max_form_memory_size is not None
            and content_length is not None
            and content_length > self.max_form_memory_size):
        raise RequestEntityTooLarge()

`paintomicsserver.py` set only `MAX_CONTENT_LENGTH` (100 MB), so the 100 MB
never got a say: the urlencoded branch compares Content-Length against
`max_form_memory_size` alone.

`PathwayController.js` posts the whole pathway SVG as one urlencoded form
field, with the pathway background inlined as a base64 `data:` URI, so the
bodies are large by construction. Measured in Chrome against a live job
(mmu01100, STATegra):

    background raster   4961 x 3199 px
    data: URI           3,650,422 chars
    serialised SVG      4,130,099 chars
    urlencoded body     4,408,629 bytes      <- 8.8x over the 500 kB default

Every such export came back as

    RequestEntityTooLarge: AT PathwayAcquisitionServlet.py:
    pathwayAcquisitionSaveImage. ERROR MESSAGE: 413 Request Entity Too Large

raised by the *first* `request.form` access in the handler
(`request.form.get("jobID")`) and reformatted by `handleException`, which is
why it reached users as an in-app message rather than a browser-level failure.
22 of the 1172 KEGG pathway images bust 500 kB on the background alone, so it
failed per-pathway -- big overview maps only -- and looked intermittent.

WERKZEUG 3.1.9
--------------
3.1.9 (pallets/werkzeug#3251) stopped applying `max_form_memory_size` to
urlencoded bodies: "only max_content_length" bounds them again. The export
no longer needs MAX_FORM_MEMORY_SIZE on this Werkzeug, but the setting stays:
it still bounds multipart non-file fields, and any Werkzeug from 3.1.0 to
3.1.8 refuses the export without it.

WHAT THESE TESTS ASSERT
-----------------------
`testALimitBelowTheBodyRejectsIt` is the control: the same request against an
app whose request limit is below the body fails with 413 -- without it the
positive test would pass on any Werkzeug that stopped enforcing a limit, and
stop meaning anything. `testAnUrlencodedBodyIsBoundByTheContentLengthOnly`
records the 3.1.9 behaviour; if it fails, Werkzeug applies the form-memory
limit to urlencoded bodies again and MAX_FORM_MEMORY_SIZE is what keeps the
export working.

File uploads are deliberately not covered: the multipart parser sets
`field_size = None` for `File` parts and skips the check entirely, so Step 1
was never affected by this and does not need pinning here.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_form_memory_limit
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from flask import Flask, request

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
_SERVER_SOURCE = os.path.join(
    _REPO_ROOT, "PaintomicsServer", "src", "paintomicsserver.py")
_CONFIG_TEMPLATE = os.path.join(
    _REPO_ROOT, "PaintomicsServer", "src", "resources", "example_serverconf.py")

# The body measured in Chrome for mmu01100, rounded down. Any limit at or below
# this reintroduces the bug for the maps that motivated the fix.
MEASURED_EXPORT_BODY_BYTES = 4408629


def _buildApp(formMemoryLimit, contentLimit=100 * pow(1024, 2)):
    """A Flask app configured the way Application.__init__ configures its own."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = contentLimit
    if formMemoryLimit is not None:
        app.config["MAX_FORM_MEMORY_SIZE"] = formMemoryLimit

    @app.route("/pa_save_image", methods=["POST"])
    def saveImage():
        # The handler's first form access is what raises, so touch .form here.
        return {"svgCodeLength": len(request.form.get("svgCode") or "")}

    return app.test_client()


def _exportShapedBody(payloadBytes):
    """The four fields PathwayController.js posts, with a payload of a given size."""
    return {"jobID": "TESTJOB000",
            "fileName": "Metabolic pathways",
            "format": "png",
            "svgCode": "<svg>" + ("a" * payloadBytes)}


def _readTemplate():
    with open(_CONFIG_TEMPLATE) as handle:
        return handle.read()


class FormMemoryLimitTest(unittest.TestCase):

    def testALimitBelowTheBodyRejectsIt(self):
        """Control: a request limit below the export body refuses it with 413.

        If this ever stops failing, the positive tests below are vacuous.
        """
        client = _buildApp(formMemoryLimit=512 * 1024, contentLimit=512 * 1024)

        response = client.post("/pa_save_image", data=_exportShapedBody(1 << 20))

        self.assertEqual(
            413, response.status_code,
            "expected a 512 kB request limit to reject a 1 MB urlencoded body; "
            "got %s, so this Werkzeug enforces no limit on it and the positive "
            "tests mean nothing" % response.status_code)

    def testAnUrlencodedBodyIsBoundByTheContentLengthOnly(self):
        """Werkzeug 3.1.9: max_form_memory_size no longer applies to urlencoded
        bodies, so an app that sets only MAX_CONTENT_LENGTH takes the export."""
        client = _buildApp(formMemoryLimit=None)

        response = client.post("/pa_save_image", data=_exportShapedBody(1 << 20))

        self.assertEqual(
            200, response.status_code,
            "Werkzeug's 500 kB max_form_memory_size default rejected a 1 MB "
            "urlencoded body (%s): it applies to urlencoded data again, as in "
            "3.1.0-3.1.8, and the configured MAX_FORM_MEMORY_SIZE is what keeps "
            "pathway exports working. Update the docstring." % response.status_code)

    def testConfiguredAppAcceptsAMeasuredExport(self):
        """The payload that produced the user-visible 413 must now parse."""
        client = _buildApp(formMemoryLimit=100 * pow(1024, 2))

        payload = MEASURED_EXPORT_BODY_BYTES
        response = client.post("/pa_save_image", data=_exportShapedBody(payload))

        self.assertEqual(200, response.status_code,
                         "a %d-byte export body was rejected with %s"
                         % (payload, response.status_code))
        self.assertEqual(payload + len("<svg>"),
                         response.get_json()["svgCodeLength"],
                         "the field arrived truncated")

    def testServerWiresTheFormLimit(self):
        """The app must set MAX_FORM_MEMORY_SIZE, not just MAX_CONTENT_LENGTH."""
        with open(_SERVER_SOURCE) as handle:
            source = handle.read()

        self.assertIn("MAX_FORM_MEMORY_SIZE", source,
                      "paintomicsserver.py no longer configures "
                      "MAX_FORM_MEMORY_SIZE, so Werkzeug's 500 kB default "
                      "applies again and large pathway exports return 413")
        self.assertRegex(
            source,
            r"config\[.MAX_FORM_MEMORY_SIZE.\]\s*=\s*SERVER_MAX_FORM_MEMORY_SIZE",
            "MAX_FORM_MEMORY_SIZE must be driven by the serverconf setting so "
            "deployments can change it without editing code")

    def testServerFallsBackWhenTheSettingIsAbsent(self):
        """serverconf.py is gitignored, so upgraded deployments predate the key.

        `from src.conf.serverconf import *` leaves the name undefined rather
        than raising, which would surface as a NameError at app construction.
        """
        with open(_SERVER_SOURCE) as handle:
            source = handle.read()

        self.assertRegex(
            source,
            r"except\s+ImportError:\s*\n\s*SERVER_MAX_FORM_MEMORY_SIZE\s*=\s*"
            r"SERVER_MAX_CONTENT_LENGTH",
            "a config written before this setting existed must fall back "
            "rather than break app construction")

    def testTemplateDeclaresTheSetting(self):
        """What a fresh deployment installs has to carry the key."""
        template = _readTemplate()

        self.assertTrue(
            re.search(r"^SERVER_MAX_FORM_MEMORY_SIZE\s*=", template, re.M),
            "example_serverconf.py must define SERVER_MAX_FORM_MEMORY_SIZE")

    def testTemplateLimitCoversAMeasuredExport(self):
        """The shipped default must be above the payloads that motivated it."""
        namespace = {"__file__": _CONFIG_TEMPLATE, "__name__": "example_serverconf"}
        exec(compile(_readTemplate(), _CONFIG_TEMPLATE, "exec"), namespace)

        configured = namespace["SERVER_MAX_FORM_MEMORY_SIZE"]

        self.assertGreater(
            configured, MEASURED_EXPORT_BODY_BYTES,
            "SERVER_MAX_FORM_MEMORY_SIZE (%d) is below the %d-byte body measured "
            "for mmu01100, so that export would still fail"
            % (configured, MEASURED_EXPORT_BODY_BYTES))
        self.assertEqual(
            namespace["SERVER_MAX_CONTENT_LENGTH"], configured,
            "the two limits are meant to stay equal so one number governs the "
            "request size; nginx's client_max_body_size tracks the same value")

    def testNginxBodyLimitStillCoversIt(self):
        """The proxy caps the request before Flask sees it, so it must agree."""
        nginxConf = os.path.join(_REPO_ROOT, "deploy", "nginx", "paintomics.conf")
        if not os.path.isfile(nginxConf):
            self.skipTest("nginx config not present in this checkout")

        with open(nginxConf) as handle:
            content = handle.read()

        match = re.search(r"client_max_body_size\s+(\d+)m\s*;", content)
        self.assertIsNotNone(
            match, "client_max_body_size is not declared in megabytes")
        self.assertGreater(
            int(match.group(1)) * pow(1024, 2), MEASURED_EXPORT_BODY_BYTES,
            "nginx would reject the export before Flask's limit mattered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
