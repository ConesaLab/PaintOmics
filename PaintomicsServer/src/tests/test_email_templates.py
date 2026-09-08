"""Tests for the shared outgoing-mail templates.

``src/common/EmailTemplates.py`` replaced four hand-concatenated HTML strings.
The tests here pin the three properties that are easy to lose and expensive to
notice, because nobody reads their own welcome mail in Outlook:

  1. No message names the product by an old version.
  2. Nothing a user typed reaches the markup unescaped.
  3. The animation invariant holds -- every rule that attaches an animation
     declares nothing else, so a client that keeps the <style> block but drops
     @keyframes (Gmail webmail) renders exactly what a client with no <style>
     block at all (Outlook) renders.

Property 3 is the one worth a machine check. It was verified by rendering both
variants in Chrome and differencing them -- 0 differing pixels -- but a pixel
diff needs a browser and a display, so what is asserted here is the structural
rule that produced it. It has already caught one real regression: ``poGlow``'s
resting shadow lived only in the keyframe, which left Apple Mail 46 grey levels
away from Outlook.

Run:  PYTHONPATH=PaintomicsServer python3 PaintomicsServer/src/tests/test_email_templates.py
"""
import os
import re
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))


def _ensureServerConfig():
    """serverconf.py is gitignored, so a clean checkout has none."""
    conf = os.path.join(HERE, "..", "conf")
    target = os.path.join(conf, "serverconf.py")
    if not os.path.isfile(target):
        shutil.copyfile(os.path.join(HERE, "..", "resources", "example_serverconf.py"), target)
        init = os.path.join(conf, "__init__.py")
        if not os.path.isfile(init):
            open(init, "a").close()


_ensureServerConfig()

from src.common import EmailTemplates as T  # noqa: E402 -- needs the config above


def _allMessages():
    """One of every message the system sends, with awkward input in each."""
    return {
        "welcome": T.welcomeEmail("Ada Lovelace", "ada@example.org"),
        "reset": T.passwordResetEmail("Ada", "https://example.org/reset?t=abc", "K7Q2M9XZ"),
        "expiry": T.jobExpiryEmail("Ada", "JOB-2026-0412", "https://example.org/?jobID=x"),
        "report": T.reportNotificationEmail("New error notification", "Ada",
                                            "ada@example.org", "boom", "#C0392B"),
    }


class BrandTest(unittest.TestCase):

    def test_no_message_names_an_old_version(self):
        """The rename this file was written for.

        The welcome mail said "Welcome to Paintomics 4!" for the whole life of
        the AI release, because the copy was buried in four separate string
        concatenations and nobody re-reads a mail they have already received.
        """
        stale = re.compile(r"Paint[Oo]mics\s*[0-9]", re.IGNORECASE)
        for name, message in _allMessages().items():
            found = stale.search(message)
            self.assertIsNone(found, "the %s email still says %r; the product is %r"
                              % (name, found.group(0) if found else "", T.PRODUCT_NAME))

    def test_every_message_names_the_product(self):
        for name, message in _allMessages().items():
            self.assertIn("PaintOmics", message, "the %s email never names the product" % name)

    def test_the_wordmark_is_live_text_not_only_an_image(self):
        """With images blocked the reader must still see who this is from.

        The old template's wordmark WAS the raster, so a blocked image left an
        unbranded message. The mark is now decorative and the name is text.
        """
        message = T.welcomeEmail("Ada", "ada@example.org")
        withoutImages = re.sub(r"<img[^>]*>", "", message)
        self.assertIn("Omics", withoutImages,
                      "the product name survives only inside an <img>, so a client "
                      "with images blocked shows an unbranded message")


class EscapingTest(unittest.TestCase):

    HOSTILE = '<script>alert("x")</script> & <b>'

    def _bodyOf(self, message):
        """Everything outside <style>, which legitimately contains braces."""
        return re.sub(r"<style>.*?</style>", "", message, flags=re.S)

    def test_a_user_name_cannot_inject_markup(self):
        for name, message in {
            "welcome": T.welcomeEmail(self.HOSTILE, "a@b.org"),
            "reset": T.passwordResetEmail(self.HOSTILE, "https://x/", "PW"),
            "expiry": T.jobExpiryEmail(self.HOSTILE, "JOB1", "https://x/"),
            "report": T.reportNotificationEmail("t", self.HOSTILE, "a@b.org", "m", "#333333"),
        }.items():
            self.assertNotIn("<script>", self._bodyOf(message),
                             "the %s email interpolates the user name unescaped" % name)

    def test_an_ampersand_in_an_address_becomes_an_entity(self):
        message = T.welcomeEmail("Ada", "a&b@example.org")
        self.assertIn("a&amp;b@example.org", message)
        self.assertNotIn("a&b@example.org", message)

    def test_a_report_body_cannot_inject_markup(self):
        message = T.reportNotificationEmail("t", "Ada", "a@b.org", self.HOSTILE, "#333333")
        self.assertNotIn("<script>", self._bodyOf(message))

    def test_a_missing_name_does_not_become_the_string_none(self):
        """adaptBSON turns an absent field into the *text* "None"."""
        message = T.welcomeEmail(None, "a@b.org")
        self.assertNotIn("None", message)


class AnimationInvariantTest(unittest.TestCase):
    """The rule that makes the motion safe in every client.

    See the module docstring of EmailTemplates for why this matters. In short:
    if an animation rule declares any property besides ``animation``, then a
    client that keeps the stylesheet but drops ``@keyframes`` applies that
    property with no keyframe to complete it, and comes to rest somewhere no
    other client does.
    """

    def _styleBlock(self):
        message = T.welcomeEmail("Ada", "a@b.org")
        match = re.search(r"<style>(.*?)</style>", message, flags=re.S)
        self.assertIsNotNone(match, "the message has no <style> block")
        return match.group(1)

    def _rules(self, css):
        """Every ``selector { body }`` outside a @keyframes block."""
        withoutKeyframes = re.sub(r"@keyframes\s+\w+\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", css)
        return re.findall(r"([^{}@]+)\{([^{}]*)\}", withoutKeyframes)

    def test_an_animation_rule_declares_nothing_else(self):
        for selector, body in self._rules(self._styleBlock()):
            declarations = [d.strip() for d in body.split(";") if d.strip()]
            properties = [d.split(":", 1)[0].strip() for d in declarations]
            if not any(p == "animation" for p in properties):
                continue
            extra = [p for p in properties if not p.startswith("animation")]
            self.assertEqual(
                extra, [],
                "the rule for %r sets %s alongside `animation`. A client that keeps "
                "the stylesheet but drops @keyframes (Gmail webmail does) will apply "
                "those with nothing to animate them, and will not match a client that "
                "drops the stylesheet entirely (Outlook)."
                % (selector.strip(), ", ".join(extra)))

    def test_every_animation_names_a_keyframe_that_exists(self):
        css = self._styleBlock()
        defined = set(re.findall(r"@keyframes\s+(\w+)", css))
        used = set()
        for _selector, body in self._rules(css):
            for declaration in body.split(";"):
                if declaration.strip().startswith("animation:"):
                    used.add(declaration.split(":", 1)[1].split()[0].strip())
        self.assertTrue(used, "no animation is attached to anything")
        self.assertEqual(used - defined, set(),
                         "these animations name a @keyframes that does not exist: %s"
                         % ", ".join(sorted(used - defined)))

    def test_no_animation_loops_forever(self):
        """A message is read once; a looping inbox preview is an irritant.

        The application's own fab pulses infinitely because there it means
        "working". Nothing is working when a welcome mail is open.
        """
        css = self._styleBlock()
        for declaration in re.findall(r"animation:\s*([^;]+);", css):
            self.assertNotIn("infinite", declaration,
                             "%r loops forever" % declaration.strip())


class ClientCompatibilityTest(unittest.TestCase):

    def test_no_message_uses_an_svg_image(self):
        """Gmail and every Outlook build refuse an <img> whose source is SVG.

        The application's own mark is an SVG, so pointing the mail at it is the
        obvious wrong move; the raster from build-email-mark.py exists for this.
        """
        for name, message in _allMessages().items():
            for source in re.findall(r'<img[^>]*src=[\'"]([^\'"]+)', message):
                self.assertFalse(source.lower().split("?")[0].endswith(".svg"),
                                 "the %s email loads %s; SVG does not render in "
                                 "Gmail or Outlook" % (name, source))

    def test_the_message_stays_under_the_gmail_clip_threshold(self):
        """Gmail truncates around 102 kB and hides the rest behind a link."""
        for name, message in _allMessages().items():
            size = len(message.encode("utf-8"))
            self.assertLess(size, 102 * 1024,
                            "the %s email is %d bytes; Gmail clips near 102 kB" % (name, size))

    def test_the_layout_is_tables_not_flexbox(self):
        """Outlook renders with Word, which has neither flexbox nor grid."""
        message = T.welcomeEmail("Ada", "a@b.org")
        body = re.sub(r"<style>.*?</style>", "", message, flags=re.S)
        for banned in ("display:flex", "display: flex", "display:grid", "display: grid"):
            self.assertNotIn(banned, body,
                             "%s appears in the markup; Word ignores it and the "
                             "layout collapses in Outlook" % banned)

    def test_the_call_to_action_pads_its_cell_not_its_anchor(self):
        """Word drops vertical padding on an inline element.

        With the padding on the <a>, the button arrives in Outlook as a thin
        coloured line with its label spilling out.
        """
        message = T.welcomeEmail("Ada", "a@b.org")
        cta = re.search(r'<td class="po-cta"[^>]*style="([^"]*)"', message, flags=re.S)
        self.assertIsNotNone(cta, "the call-to-action cell is gone or was renamed")
        self.assertIn("padding:", cta.group(1),
                      "the call-to-action cell carries no padding, so the padding "
                      "must be on the <a> inside it, where Word ignores it")


class SharedChromeTest(unittest.TestCase):

    def test_no_servlet_builds_its_own_message_html(self):
        """The duplication this module exists to remove must not come back.

        Four handlers each concatenated their own <html><body> chrome, and the
        copies had already drifted apart in the logo alt text and the <img>
        geometry before anyone noticed.
        """
        roots = [os.path.join(REPO, "PaintomicsServer", "src", "servlets"),
                 os.path.join(REPO, "PaintomicsServer", "src", "AdminTools", "scripts")]
        offenders = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for directory, _subdirs, files in os.walk(root):
                for filename in files:
                    if not filename.endswith(".py"):
                        continue
                    path = os.path.join(directory, filename)
                    with open(path, encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            if "'<html><body>'" in line or '"<html><body>"' in line:
                                offenders.append("%s:%d" % (os.path.relpath(path, REPO), number))
        self.assertEqual(offenders, [],
                         "these build email HTML by hand instead of calling "
                         "src.common.EmailTemplates: %s" % ", ".join(offenders))

    def test_the_email_logo_is_a_raster_that_exists(self):
        """The template default, which is what a fresh deploy installs."""
        path = os.path.join(HERE, "..", "resources", "example_serverconf.py")
        logo = None
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("PAINTOMICS_LOGO_PATH"):
                    logo = line.split('"')[-2]
        self.assertIsNotNone(logo, "example_serverconf.py defines no PAINTOMICS_LOGO_PATH")
        # Naming the retired file explicitly, because "a raster that exists" is
        # true of the old wordmark too: a revert of this default passed every
        # other assertion here while quietly putting the pre-AI logo back into
        # every message. That happened once already, during development.
        self.assertNotIn("paintomics_white_300x66", logo,
                         "PAINTOMICS_LOGO_PATH is back on the retired wordmark, so "
                         "every outgoing message shows the pre-AI logo")
        self.assertFalse(logo.lower().endswith(".svg"),
                         "PAINTOMICS_LOGO_PATH is %s. Every outgoing message loads it "
                         "in an <img>, and Gmail and Outlook will not render an SVG "
                         "there." % logo)
        served = os.path.join(REPO, "PaintomicsClient", "public_html", logo.lstrip("/"))
        if os.path.isdir(os.path.join(REPO, "PaintomicsClient", "public_html")):
            self.assertTrue(os.path.isfile(served),
                            "PAINTOMICS_LOGO_PATH points at %s, which is not in "
                            "public_html, so the URL 404s in every message" % logo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
