#!/usr/bin/env python3
"""The public instance is paintomics.org; nothing that points a person at it
may still say paintomics.uv.es.

Why this exists
---------------
paintomics.org has been the public instance since the 2026-09-07 cutover
(deploy/CUTOVER.md). Two weeks later the README badge, the security policy,
the citation file, both issue templates, the terms page, the maintainer docs
and the Help menu still sent people to paintomics.uv.es -- and the client
pinned every guest's displayed address to that host through a constant in
ServerConfiguration.js, which is why the deployment had to keep
`PAINTOMICS_EMAIL_DOMAIN=paintomics.uv.es` in its `.env`: the server stores
"guest<n>@" + its own domain, sign-in is a lookup by that address, and a client
that assembles the address from its own constant only agrees with the server
by coincidence.

Historical notes ("on paintomics.uv.es on 2026-08-17 ...") are records of
where something was measured and stay as they are. This guards the pointers:

1. Every file that sends a person to the public instance names paintomics.org
   and carries no paintomics.uv.es URL.
2. The guest-session contract: the server returns the stored address with the
   credentials (`email`), and the client shows that rather than assembling one
   from PAINTOMICS_EMAIL_DOMAIN, which is now only the fallback for a server
   that predates the field.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_public_instance_is_paintomics_org
"""
import ast
import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "../../.."))
CLIENT = os.path.join(REPO, "PaintomicsClient", "public_html")
SERVLET = os.path.join(REPO, "PaintomicsServer", "src", "servlets",
                       "UserManagementServlet.py")

OLD_URL = "https://paintomics.uv.es"
NEW_URL = "https://paintomics.org"

#: Files whose job includes telling a person where the public instance is.
#: Each must name paintomics.org and must not link paintomics.uv.es.
POINTERS = [
    "README.md",
    "SECURITY.md",
    "CITATION.cff",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/organism_request.yml",
    "docs/dev/README.md",
    "PaintomicsClient/public_html/conditions.html",
]


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class PublicInstancePointers(unittest.TestCase):

    def test_every_pointer_names_paintomics_org(self):
        for rel in POINTERS:
            text = read(os.path.join(REPO, rel))
            self.assertIn(NEW_URL, text,
                          "%s does not name the public instance %s" % (rel, NEW_URL))
            self.assertNotIn(OLD_URL, text,
                             "%s still sends people to %s" % (rel, OLD_URL))

    def test_issue_templates_offer_the_public_instance(self):
        """The dropdown option is what a reporter picks; it must be the live host."""
        for rel in (".github/ISSUE_TEMPLATE/bug_report.yml",
                    ".github/ISSUE_TEMPLATE/organism_request.yml"):
            text = read(os.path.join(REPO, rel))
            self.assertIn("- %s/" % NEW_URL, text,
                          "%s does not offer %s/ as an instance" % (rel, NEW_URL))

    def test_help_menu_links_no_download_on_the_old_host(self):
        """The Help menu is built from string literals; a comment may record the
        old host, an href may not."""
        text = read(os.path.join(CLIENT, "app", "view", "MainView.js"))
        hrefs = re.findall(r"href='([^']*)'", text)
        offenders = [h for h in hrefs if "paintomics.uv.es" in h]
        self.assertEqual(offenders, [],
                         "MainView.js still links the old host: %s" % offenders)


class GuestAddressContract(unittest.TestCase):

    def test_server_returns_the_stored_address_with_the_credentials(self):
        """userManagementNewGuestSession's response carries `email`, taken from
        the user it just stored, not rebuilt from the name."""
        tree = ast.parse(read(SERVLET), SERVLET)
        function = next((node for node in ast.walk(tree)
                         if isinstance(node, ast.FunctionDef)
                         and node.name == "userManagementNewGuestSession"), None)
        self.assertIsNotNone(function, "userManagementNewGuestSession is gone")

        payloads = [call.args[0] for call in ast.walk(function)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setContent"
                    and call.args and isinstance(call.args[0], ast.Dict)]
        self.assertEqual(len(payloads), 1,
                         "expected one setContent({...}) in the guest handler, "
                         "found %d" % len(payloads))
        payload = payloads[0]
        keys = [key.value for key in payload.keys if isinstance(key, ast.Constant)]
        self.assertIn("email", keys, "the guest-session response carries no email")

        value = payload.values[keys.index("email")]
        self.assertEqual(ast.unparse(value), "userInstance.getEmail()",
                         "email must be the stored address, got %s" % ast.unparse(value))

    def test_client_shows_the_address_the_server_returned(self):
        controller = read(os.path.join(CLIENT, "app", "controller", "UserController.js"))
        handler_start = controller.index("this.startGuestSessionButtonClickHandler")
        handler_end = controller.index("this.showGuestSessionDialog = function")
        handler = controller[handler_start:handler_end]

        self.assertIn("response.email", handler,
                      "the guest-session handler ignores the address the server returned")
        self.assertNotIn('showGuestSessionDialog(response.userName + "@"', handler,
                         "the dialog is still shown an address the client assembled")
        self.assertNotIn('Cookies.set("lastEmail", response.userName + "@"', handler,
                         "the lastEmail cookie is still an address the client assembled")

    def test_the_fallback_domain_is_the_public_instance(self):
        config = read(os.path.join(CLIENT, "resources", "ServerConfiguration.js"))
        match = re.search(r'^PAINTOMICS_EMAIL_DOMAIN\s*=\s*"([^"]*)";', config, re.M)
        self.assertIsNotNone(match, "PAINTOMICS_EMAIL_DOMAIN is not defined in ServerConfiguration.js")
        self.assertEqual(match.group(1), "paintomics.org",
                         "the fallback guest domain is %r, not the public instance" % match.group(1))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
