"""The omic card's file-check tint has to follow the theme.

Reported 2026-09-08 with a screenshot: in the dark theme the "Gene expression"
card's title bar was a near-white band carrying near-white text, so the omic's
name and its delete control were invisible. Measured in Chrome on the rendered
card: #EDEEF0 on #F7E9ED is **1.01:1**, against the 4.5:1 AA needs.

The cause was one stylesheet painting a state and another painting the ink,
neither knowing about the other:

* `inputformat.css` tints a card that failed (or passed) its file check with
  hard-coded light literals -- `#F4F9F3` for ok, `#F7E9ED` for warn/err -- and
  paints the title bar with `!important` so the bar does not sit as a clean
  white strip on a tinted body.
* `dark.css` had NO rule for any of them. It did repaint the bar's `h4` to
  `--pa-ink-title`, because on this theme the header is a dark surface.

So the two literals survived into the dark theme and the ink was inverted out
from under them. The card BODY meanwhile lost its tint entirely: it is the same
specificity as `[data-theme="dark"] .omicbox` (0,2,0) and dark.css loads later,
so the tie went to the theme. Dark users got no body signal at all and an
unreadable header -- the exact opposite of what the tint exists to do.

The fix is the pattern the rest of `inputformat.css` and `--pa-cell-empty`
already use: the literal becomes the light fallback of a token, and dark.css
substitutes a dark value. The body rules are restated in dark.css because a
fallback cannot win a specificity tie.

What is tested is the source the browser loads, in the style of the other Step 1
tests; the rendered contrast was measured in Chrome and is described in the
commit.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_dark_theme_paints_the_omic_card_state_tints
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_ROOT = os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "PaintomicsClient", "public_html"))
INPUTFORMAT_CSS = os.path.join(CLIENT_ROOT, "resources", "css", "inputformat.css")
DARK_CSS = os.path.join(CLIENT_ROOT, "resources", "css", "dark.css")
INDEX_HTML = os.path.join(CLIENT_ROOT, "index.html")

# The two tints, and the token each one now travels through.
OK_TOKEN, OK_LIGHT = "--pa-state-ok-bg", "#F4F9F3"
BAD_TOKEN, BAD_LIGHT = "--pa-state-bad-bg", "#F7E9ED"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def declarations(source):
    """Every `property: value` outside a comment, lowercased.

    Comments are stripped first because this file explains its colours in prose
    -- the note above `.omicbox.pa-state-warn` quotes `#F7E9ED` while recording
    where the convention comes from, and a bare substring search would read that
    sentence as a rule.
    """
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


class DarkThemePaintsTheOmicCardStateTints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputformat = read(INPUTFORMAT_CSS)
        cls.dark = read(DARK_CSS)
        cls.index = read(INDEX_HTML)
        cls.rules = declarations(cls.inputformat)

    # -- the tints leave inputformat.css as tokens ------------------------

    def test_no_state_rule_hard_codes_a_light_tint(self):
        """The regression itself: a literal here cannot follow the theme.

        Both are `background-color`, and both are what the dark theme inherited.
        """
        for literal in (OK_LIGHT, BAD_LIGHT):
            self.assertNotRegex(
                self.rules, r"background-color:\s*%s" % re.escape(literal),
                "inputformat.css still paints a state with the literal %s, so "
                "the dark theme inherits a near-white card" % literal)

    def test_each_state_tint_travels_through_a_token(self):
        """...and keeps its light value as the fallback, so light is untouched
        by this change: main.css defines neither token."""
        for token, light in ((OK_TOKEN, OK_LIGHT), (BAD_TOKEN, BAD_LIGHT)):
            self.assertRegex(
                self.rules,
                r"background-color:\s*var\(\s*%s\s*,\s*%s\s*\)" % (
                    re.escape(token), re.escape(light)),
                "%s must be read with %s as its light fallback" % (token, light))

    def test_the_title_bar_is_tinted_through_the_token_too(self):
        """The bar is the surface the omic's name sits on -- it is the half the
        user photographed, and it carries `!important`, so a token that reached
        only the body would have fixed nothing visible."""
        bar = re.search(
            r"\.omicbox\.pa-state-ok\s+\.omicboxTitle\s*\{(.*?)\}", self.rules, re.S)
        self.assertIsNotNone(bar, "the ok title-bar rule went missing")
        self.assertIn("var(%s" % OK_TOKEN, bar.group(1))

        bad_bar = re.search(
            r"\.omicbox\.pa-state-warn\s+\.omicboxTitle,\s*"
            r"\.omicbox\.pa-state-err\s+\.omicboxTitle\s*\{(.*?)\}",
            self.rules, re.S)
        self.assertIsNotNone(bad_bar, "the warn/err title-bar rule went missing")
        self.assertIn("var(%s" % BAD_TOKEN, bad_bar.group(1))

    # -- and dark.css answers with a dark value ---------------------------

    def test_dark_defines_both_tokens(self):
        for token in (OK_TOKEN, BAD_TOKEN):
            self.assertRegex(
                self.dark, r"%s:\s*#[0-9A-Fa-f]{6}\s*;" % re.escape(token),
                "dark.css must give %s a dark value" % token)

    def test_the_dark_tints_are_dark(self):
        """A token is only a fix if the value behind it is actually dark. Both
        must sit near --pa-surface (#1E1F23), not near white -- the failure being
        guarded is a near-white band, so a wrong value here reproduces it."""
        for token in (OK_TOKEN, BAD_TOKEN):
            value = re.search(r"%s:\s*(#[0-9A-Fa-f]{6})" % re.escape(token), self.dark)
            self.assertIsNotNone(value, "%s has no value" % token)
            red, green, blue = (int(value.group(1)[i:i + 2], 16) for i in (1, 3, 5))
            self.assertLess(
                max(red, green, blue), 0x60,
                "%s is %s, which is not a dark surface" % (token, value.group(1)))

    def test_dark_restates_the_card_body_so_the_tint_is_not_lost(self):
        """`[data-theme="dark"] .omicbox` is (0,2,0) and loads after
        inputformat.css, so it beats a bare `.omicbox.pa-state-ok` on source
        order. Without a restatement at (0,3,0) the dark card keeps a plain
        surface and the state never reaches the body at all."""
        for state, token in (("ok", OK_TOKEN), ("warn", BAD_TOKEN), ("err", BAD_TOKEN)):
            pattern = r'\[data-theme="dark"\]\s+\.omicbox\.pa-state-%s\b' % state
            self.assertRegex(
                self.dark, pattern,
                "dark.css must restate .pa-state-%s on the card body" % state)

    def test_the_dark_body_rules_use_the_same_tokens(self):
        """The body and its title bar must not drift apart into two colours;
        that is what made the header a separate surface in the first place."""
        for token in (OK_TOKEN, BAD_TOKEN):
            block = re.search(
                r'\[data-theme="dark"\]\s+\.omicbox\.pa-state-[a-z]+[^{]*\{[^}]*'
                r'var\(%s\)' % re.escape(token), self.dark)
            self.assertIsNotNone(
                block, "the dark body rule must read %s, not a second literal" % token)

    # -- the flagged field, the same bug one layer down -------------------

    def test_the_flagged_field_border_travels_through_a_token(self):
        """Found while verifying the card in Chrome: the same rule block flags
        the file field with `border-color: #D22 !important`, and on a dark page
        the computed border was #3B3E45. Not a tint that survived -- a rule that
        never applied."""
        self.assertNotRegex(
            self.rules, r"border-color:\s*#D22",
            "the flagged field still hard-codes #D22")
        self.assertRegex(
            self.rules,
            r"border-color:\s*var\(\s*--pa-field-bad-border\s*,\s*#D22\s*\)\s*!important",
            "the flagged field's border must read --pa-field-bad-border")
        self.assertRegex(
            self.rules,
            r"box-shadow:[^;]*var\(\s*--pa-field-bad-ring\s*,",
            "the ring must be a token too -- 14% red is invisible on a dark field")

    def test_dark_outranks_the_blanket_field_rule(self):
        """`!important` alone loses here. The blanket is
        `[data-theme="dark"] .x-form-field:not(...):not(...)` at (0,4,0), so the
        restatement has to reach (0,4,1) -- which is why .x-form-text is in the
        selector. A future edit that drops it puts the border back to grey with
        every test still passing unless this one checks the shape."""
        for state in ("warn", "err"):
            self.assertRegex(
                self.dark,
                r'\[data-theme="dark"\]\s+input\[type="text"\]\.x-form-text\.pa-field-%s' % state,
                "dark.css must outrank the blanket for .pa-field-%s" % state)
        self.assertRegex(
            self.dark,
            r"border-color:\s*var\(--pa-field-bad-border\)\s*!important",
            "the restatement needs !important to meet the blanket's own")

    def test_dark_defines_the_field_tokens(self):
        self.assertRegex(self.dark, r"--pa-field-bad-border:\s*#[0-9A-Fa-f]{6}\s*;")
        self.assertRegex(self.dark, r"--pa-field-bad-ring:\s*rgba\([^)]*\)\s*;")

    # -- and the browser is told to fetch them ----------------------------

    def test_both_stylesheets_are_cache_busted(self):
        """A dark-theme fix that arrives on a cached stylesheet has not
        arrived. Both files changed, so both markers must move together."""
        for sheet in ("inputformat.css", "dark.css"):
            self.assertRegex(
                self.index, r"%s\?v=[0-9]+\.[0-9]+" % re.escape(sheet),
                "%s must be served with a ?v= marker" % sheet)


if __name__ == "__main__":
    unittest.main()
