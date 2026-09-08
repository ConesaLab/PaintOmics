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
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))


def _ensureServerConfig():
    """Make src.conf.serverconf importable on a checkout that has none.

    Bound in memory rather than written to disk. serverconf.py is gitignored
    and per-machine, so an earlier version of this helper that copied the
    template over it turned a "no config" checkout into a "config installed"
    one permanently -- changing what the sibling suites then exercise, and, in
    this repo, a stray serverconf copied between trees has already produced a
    phantom INTRODUCED failure. Same pattern as
    test_report_survives_mail_outage.py.
    """
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

        Scoped to the header's own wordmark element rather than to the whole
        message. Searching the message for "Omics" cannot fail: <title> carries
        the product name, and so does the body copy ("PaintOmics AI runs in the
        browser"). Replacing the live-text wordmark with an <img> -- the exact
        regression named above -- passed that version of this test.
        """
        message = T.welcomeEmail("Ada", "ada@example.org")
        wordmark = re.search(r'<div class="po-ink"[^>]*>(.*?)</div>', message, flags=re.S)
        self.assertIsNotNone(wordmark, "the header wordmark element is gone or was renamed")
        text = re.sub(r"<[^>]*>", "", wordmark.group(1))
        text = text.replace("&nbsp;", " ")
        self.assertIn("Paint", text,
                      "the header wordmark holds no live text, so a client with "
                      "images blocked shows an unbranded message")
        self.assertIn("Omics", text,
                      "the header wordmark holds no live text, so a client with "
                      "images blocked shows an unbranded message")


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

    def _preheaderOf(self, message):
        """The hidden line a client shows beside the subject, as text."""
        div = re.search(r'<div style="display:none;.*?</div>', message, flags=re.S)
        self.assertIsNotNone(div, "the message carries no preheader")
        return div.group(0)

    def test_a_preheader_is_escaped_once_and_only_once(self):
        """`preheaderText` is plain text; every other helper here takes markup.

        Written as an entity -- `&mdash;` -- it is escaped into `&amp;mdash;`
        and the reader sees the six literal characters in the message list.
        That shipped: every welcome mail's preview line read "Your account is
        ready &mdash; start from an example dataset."
        """
        for name, message in _allMessages().items():
            preheader = self._preheaderOf(message)
            self.assertNotIn("&amp;", preheader,
                             "the %s email's preheader is escaped twice, so its "
                             "inbox preview shows a literal entity" % name)

    def test_an_ampersand_in_a_job_id_is_escaped_once(self):
        """The body needs the escaped id and the preheader needs the plain one."""
        message = T.jobExpiryEmail("Ada", "a&b", "https://x/")
        self.assertIn("Job a&amp;b will", self._preheaderOf(message))
        self.assertNotIn("&amp;amp;", message)

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
        """Any `animation-*` longhand attaches motion, not only the shorthand.

        Keyed on the shorthand alone, this skipped every delay rule -- so
        `.po-b3 { opacity: 0; animation-delay: .4s; }` passed, and that is
        precisely the shape the invariant forbids: Gmail webmail keeps the
        stylesheet, drops @keyframes, and the band cell rests invisible.
        """
        for selector, body in self._rules(self._styleBlock()):
            declarations = [d.strip() for d in body.split(";") if d.strip()]
            properties = [d.split(":", 1)[0].strip() for d in declarations]
            if not any(p == "animation" or p.startswith("animation-") for p in properties):
                continue
            extra = [p for p in properties if not p.startswith("animation")]
            self.assertEqual(
                extra, [],
                "the rule for %r sets %s alongside `animation`. A client that keeps "
                "the stylesheet but drops @keyframes (Gmail webmail does) will apply "
                "those with nothing to animate them, and will not match a client that "
                "drops the stylesheet entirely (Outlook)."
                % (selector.strip(), ", ".join(extra)))

    #: Resting values that need no inline declaration, because they already ARE
    #: the CSS initial value: a client that never runs the keyframe renders
    #: them anyway. Anything else in a resting frame must be inline, or the
    #: animation is the only thing carrying it -- which is the invariant broken.
    INITIAL_RESTING = {
        ("opacity", "1"),
        ("transform", "none"),
        ("transform", "translateY(0)"),
    }

    def _keyframes(self, css):
        """``{name: {step: {property: value}}}`` for every @keyframes block."""
        blocks = {}
        for name, body in re.findall(
                r"@keyframes\s+(\w+)\s*\{((?:[^{}]|\{[^{}]*\})*)\}", css):
            steps = {}
            for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
                properties = {}
                for declaration in declarations.split(";"):
                    if ":" in declaration:
                        prop, value = declaration.split(":", 1)
                        properties[prop.strip()] = value.strip()
                steps[selector.strip()] = properties
            blocks[name] = steps
        return blocks

    def _inlineStyleOf(self, message, className):
        element = re.search(
            r'<[a-z]+[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>' % re.escape(className), message)
        self.assertIsNotNone(element, "no element carries class %r" % className)
        style = re.search(r'style="([^"]*)"', element.group(0))
        return re.sub(r"\s+", "", style.group(1)) if style else ""

    def test_a_resting_frame_declares_nothing_the_element_does_not(self):
        """The other half of the invariant, and the one that already broke.

        `poGlow` rests on a box-shadow. While that shadow lived only inside the
        keyframe, a client that ran the animation came to rest with it and a
        client that did not came to rest without it -- 4913 pixels and 46 grey
        levels apart when the two were rendered and differenced. The structural
        rule is that whatever a resting frame sets, the element must already
        set inline, unless the value is the property's CSS initial value.

        This is the assertion the class docstring claimed and did not make:
        deleting that inline box-shadow passed every other test here.
        """
        message = T.welcomeEmail("Ada", "a@b.org")
        css = self._styleBlock()
        keyframes = self._keyframes(css)
        checked = 0
        for selector, body in self._rules(css):
            for declaration in body.split(";"):
                if not declaration.strip().startswith("animation:"):
                    continue
                animation = declaration.split(":", 1)[1].split()[0].strip()
                steps = keyframes.get(animation, {})
                resting = {}
                for step, properties in steps.items():
                    if "100%" in step or step.strip() == "to":
                        resting.update(properties)
                self.assertTrue(resting,
                                "@keyframes %s has no resting (100%%/to) step, so "
                                "there is no state for a client that drops it to "
                                "agree with" % animation)
                className = selector.strip().lstrip(".").split()[0]
                inline = self._inlineStyleOf(message, className)
                for prop, value in sorted(resting.items()):
                    if (prop, value.replace(" ", "")) in {
                            (p, v.replace(" ", "")) for p, v in self.INITIAL_RESTING}:
                        continue
                    checked += 1
                    self.assertIn("%s:%s" % (prop, value.replace(" ", "")), inline,
                                  "@keyframes %s rests on %s:%s, but .%s does not "
                                  "declare it inline. A client that drops @keyframes "
                                  "(Gmail webmail) or the whole stylesheet (Outlook) "
                                  "then rests somewhere the others do not."
                                  % (animation, prop, value, className))
        self.assertTrue(checked, "no resting declaration was examined")

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
        checked = 0
        for _selector, body in self._rules(css):
            for declaration in body.split(";"):
                if ":" not in declaration:
                    continue
                name, value = declaration.split(":", 1)
                # The longhand counts too: `animation-iteration-count: infinite`
                # loops just as forever as the shorthand, and reading only the
                # shorthand let that spelling through.
                if not name.strip().startswith("animation"):
                    continue
                checked += 1
                self.assertNotIn("infinite", value,
                                 "%r loops forever" % declaration.strip())
        self.assertTrue(checked, "no animation declaration was examined")


class ClientCompatibilityTest(unittest.TestCase):

    def test_the_report_body_reuses_the_panel_rather_than_a_copy_of_it(self):
        """One inset block, two dressings -- not two blocks that look alike.

        The report body used to hand-roll a table with the panel's own margin,
        background, class and padding, differing only in a border and a font.
        In a module whose reason for existing is that the chrome lives once,
        that is the duplication coming back.
        """
        plain = T._panel("ROWS")
        accented = T._panel("ROWS", accent="#0069C0", monospace=True)
        for shared in ('<table role="presentation"', "margin:4px 0 18px 0;",
                       'class="po-panel"', T._PANEL, "padding:14px 18px;"):
            self.assertIn(shared, plain)
            self.assertIn(shared, accented,
                          "the accented panel no longer shares %r with the plain "
                          "one, so they have drifted into two components" % shared)

    def test_the_panel_accent_is_a_border_never_the_text(self):
        """Colouring the body put 3.5:1 of red on Apple Mail's dark card."""
        accented = T._panel("ROWS", accent="#C0392B")
        self.assertIn("border-left:4px solid #C0392B;", accented)
        self.assertNotIn("color:#C0392B", accented,
                         "the accent colours the text again; it belongs in the border")
        self.assertIn("border-radius:0 8px 8px 0;", accented,
                      "the accented corner is still rounded, so the rule reads as "
                      "a stripe on a lozenge rather than an edge")
        self.assertNotIn("border-left", T._panel("ROWS"),
                         "an unaccented panel grew a border")

    def test_the_panel_can_be_fixed_pitch_for_quoted_text(self):
        self.assertIn("font-family:Menlo,Consolas,monospace;",
                      T._panel("ROWS", monospace=True))
        self.assertNotIn("monospace", T._panel("ROWS"))

    def test_a_quoted_block_keeps_its_indentation_and_its_columns(self):
        """A fixed-pitch face cannot align what HTML has already collapsed.

        Runs of spaces and tabs go before the font can line them up, so a
        pasted traceback arrives flush left and tab-separated columns become
        single spaces -- exactly what this panel exists to avoid.
        """
        self.assertIn("white-space:pre-wrap;", T._panel("ROWS", monospace=True))

    def test_a_quoted_block_cannot_stretch_the_card(self):
        """Preserved text no longer collapses at a space either.

        Rendered and measured: with white-space alone, one long unbroken token
        pushes the 600px card past the edge of the window. word-break is what
        keeps it inside.
        """
        self.assertIn("word-break:break-word;", T._panel("ROWS", monospace=True))

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

    #: Every surface a body colour is set on in this file.
    SURFACES = (T._CARD, T._PANEL, T._AI_TINT, T._PAGE)

    @staticmethod
    def _contrast(foreground, background):
        def channel(value):
            value = value / 255.0
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        def luminance(colour):
            colour = colour.lstrip("#")
            red, green, blue = (int(colour[i:i + 2], 16) for i in (0, 2, 4))
            return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

        first, second = luminance(foreground), luminance(background)
        return (max(first, second) + 0.05) / (min(first, second) + 0.05)

    def test_every_text_colour_is_legible_on_every_surface(self):
        """The file darkens _AI_BLUE for 4.5:1 and then set _MUTED at 3.6:1.

        _MUTED is not decoration: it labels the address you sign in with and
        the temporary password. A standard the file applies to one token and
        not the others is not a standard, so it is checked here rather than
        argued in a comment.
        """
        for name in ("_INK", "_BODY", "_MUTED"):
            colour = getattr(T, name)
            for surface in self.SURFACES:
                ratio = self._contrast(colour, surface)
                self.assertGreaterEqual(
                    round(ratio, 2), 4.5,
                    "%s (%s) is %.2f:1 on %s, under the 4.5:1 body text needs"
                    % (name, colour, ratio, surface))

    def test_white_text_is_legible_on_the_call_to_action(self):
        ratio = self._contrast("#FFFFFF", T._AI_BLUE_DEEP)
        self.assertGreaterEqual(round(ratio, 2), 4.5,
                                "white on the button (%s) is %.2f:1"
                                % (T._AI_BLUE_DEEP, ratio))

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

        Detected by the outcome rather than by one spelling. Keyed on the
        literal `'<html><body>'`, this only ever matched the four handlers it
        was written against: a fifth message opening with `'<table ...>'` would
        restart the duplication with every test still green. What actually
        defines the offence is sending HTML this module did not render.
        """
        roots = [os.path.join(REPO, "PaintomicsServer", "src", "servlets"),
                 os.path.join(REPO, "PaintomicsServer", "src", "AdminTools", "scripts")]
        offenders = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for directory, _subdirs, files in os.walk(root):
                for filename in sorted(files):
                    if not filename.endswith(".py"):
                        continue
                    path = os.path.join(directory, filename)
                    with open(path, encoding="utf-8") as handle:
                        source = handle.read()
                    if "isHTML=True" not in source:
                        continue
                    if "src.common.EmailTemplates" in source:
                        continue
                    offenders.append(os.path.relpath(path, REPO))
        self.assertEqual(offenders, [],
                         "these send HTML mail without rendering it through "
                         "src.common.EmailTemplates, so the chrome is being built "
                         "by hand again: %s" % ", ".join(offenders))

    def test_only_the_unsolicited_message_carries_the_lawful_basis_note(self):
        """The expiry reminder is the one message nobody asked for.

        It is sent because a job is about to be deleted, not because the reader
        did anything, so it states why it is allowed to reach them. The branch
        that renders it had no assertion at all: turning `if legalNote:` into
        `if False:` left every test green and the note gone.
        """
        messages = _allMessages()
        marker = "You are receiving this because you accepted"
        self.assertIn(marker, messages["expiry"],
                      "the job-expiry reminder is unsolicited and carries no "
                      "lawful-basis note")
        for name in ("welcome", "reset", "report"):
            self.assertNotIn(marker, messages[name],
                             "the %s email is a reply to something the reader did "
                             "and should not carry the reminder's note" % name)

    def test_a_deployment_still_set_to_the_retired_wordmark_gets_the_mark(self):
        """serverconf.py is gitignored, so a new default cannot reach a box.

        paintomics.uv.es was installed before the mark existed: its
        PAINTOMICS_LOGO_PATH still names the 300x66 wordmark, and an older
        template named it with no suffix at all, so the URL 404'd. Changing the
        template default does nothing there. The mark is part of the message
        design rather than a per-machine setting, so a configured value that
        still points at the retired file is treated as unset and the mark is
        served from PAINTOMICS_BASE_URL, the deployment's own externally
        reachable address.
        """
        original = (T.PAINTOMICS_LOGO_URL, T.PAINTOMICS_BASE_URL)
        try:
            T.PAINTOMICS_BASE_URL = "https://paintomics.uv.es"
            for configured, expected in (
                    # The install-time defaults, with and without the suffix the
                    # older template omitted -- which made the URL 404 outright.
                    ("https://paintomics.uv.es/resources/images/paintomics_white_300x66",
                     "https://paintomics.uv.es" + T._EMAIL_MARK_PATH),
                    ("https://old-host.example/img/paintomics_white_300x66.png",
                     "https://paintomics.uv.es" + T._EMAIL_MARK_PATH),
                    # A deliberate override is left alone: this is still a knob.
                    ("https://example.org/resources/images/house-brand.png",
                     "https://example.org/resources/images/house-brand.png"),
            ):
                T.PAINTOMICS_LOGO_URL = configured
                self.assertEqual(expected, T._markURL(),
                                 "configured %s" % configured)
        finally:
            T.PAINTOMICS_LOGO_URL, T.PAINTOMICS_BASE_URL = original

    def test_the_mark_url_is_always_absolute(self):
        """A host-less src is a broken image: mail has no page to resolve against.

        The base comes from PAINTOMICS_BASE_URL. Slicing it out of the
        configured logo URL on "/resources/" worked for the one legacy default
        and returned a bare path for every other layout.
        """
        original = T.PAINTOMICS_LOGO_URL
        try:
            for configured in (
                    "https://host/img/paintomics_white_300x66.png",   # not under /resources/
                    "https://host/resources/images/paintomics_white_300x66",
                    "paintomics_white_300x66",
                    "",
                    None):
                T.PAINTOMICS_LOGO_URL = configured
                url = T._markURL()
                self.assertRegex(url, r"^https?://",
                                 "configured %r gives %r, which no mail client can "
                                 "resolve" % (configured, url))
        finally:
            T.PAINTOMICS_LOGO_URL = original

    def test_no_message_loads_the_retired_wordmark(self):
        for name, message in _allMessages().items():
            self.assertNotIn("paintomics_white_300x66", message,
                             "the %s email still loads the pre-AI wordmark" % name)

    def test_the_mark_is_sized_by_width_so_it_cannot_be_squashed(self):
        """Pinning both axes squashed anything that is not square.

        A deployment configured for the 300x66 wordmark rendered it into a
        52x52 box -- a 4.55:1 aspect change. The cell stays a fixed 64x52 so a
        blocked image occupies the same space; the image itself scales.
        """
        message = T.welcomeEmail("Ada", "a@b.org")
        tag = re.search(r"<img[^>]*>", message)
        self.assertIsNotNone(tag, "the header image is gone")
        markup = re.sub(r"\s+", " ", tag.group(0))
        self.assertNotRegex(markup, r'height="\d',
                            "the mark pins a height attribute, so a non-square "
                            "logo is squashed rather than scaled")
        self.assertNotRegex(markup, r"height:\s*\d+px",
                            "the mark pins a height, so a non-square logo is "
                            "squashed rather than scaled")

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
