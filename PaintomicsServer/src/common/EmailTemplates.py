"""HTML templates for every message PaintOmics AI sends.

Four handlers used to build their own message by concatenating a string:
``userManagementSignUp`` and ``userManagementResetPassword`` in
``servlets/UserManagementServlet.py``, ``adminServletSendReport`` in
``servlets/AdminServlet.py`` and ``remindJobByJobID`` in
``AdminTools/scripts/clean_databases.py``. All four opened with the same logo
anchor, closed with the same ``mailto`` footer, and separated the two with the
same dotted ``<div>``. Four copies meant the rebrand had to be applied four
times, and the copies had already drifted -- two said ``alt='PaintOmics 4
logo'``, two said ``alt='PaintOmics logo'``; one passed ``width='auto'``, which
is not a legal value for the attribute and which Word reads as zero.

So the chrome lives here once and each caller supplies only its own middle.

WHY THIS IS NOT JUST A WEB PAGE IN A STRING
-------------------------------------------
Mail clients are not browsers, and the constraints below are the reason this
file looks the way it does rather than like anything in ``public_html``.

* **No SVG.** Gmail and every Outlook build refuse an ``<img>`` whose source is
  an SVG, so the mark cannot be ``paintomics-mark.svg`` -- the file the
  application itself uses. It ships as ``paintomics-mark-email.png``, rendered
  from that same vector by ``resources/images/build-email-mark.py``.

* **No layout engine.** Outlook on Windows renders with Word, which has no
  flexbox, no grid, no ``opacity``, no ``box-shadow``, no CSS animation, and
  which ignores vertical padding on an inline element. Structure is therefore
  tables with inline styles, and the call to action carries its padding on the
  ``<td>`` rather than on the ``<a>`` inside it.

* **A ``<style>`` block is an enhancement, never structure.** Gmail strips it
  when it is showing a non-Gmail account. Everything the message needs to be
  legible is an inline ``style`` attribute; the block only adds motion, the
  dark palette and the phone breakpoint.

* **Images are blocked by default** in most clients, so the wordmark is live
  text and the one image in the message is decorative with an empty ``alt``.
  Its cell is a fixed 64x52 box, so blocked and loaded occupy the same space.

THE ANIMATION INVARIANT
-----------------------
The user asked for the message to carry the same motion the application uses
for its AI pathway interpretation. Motion in e-mail usually breaks because a
designer hides something at rest and reveals it with a keyframe: Outlook
ignores ``opacity`` outright, and Gmail's webmail keeps a ``<style>`` block
while dropping ``@keyframes`` -- so the hidden thing is revealed permanently in
one client and never in the other.

Every animation here obeys one rule instead, and it is the rule that makes the
motion safe rather than merely decorative:

    Nothing is hidden at rest. Each keyframe runs FROM an unfinished state TO
    the resting state, and the rule that attaches it declares nothing but
    ``animation``.

The consequences are worth spelling out, because they are the whole point.
Strip the ``<style>`` block and the message is pixel-identical to the last
frame. Strip only ``@keyframes`` and it is *still* identical, because the
attaching rule sets no other property. Render it in Word, which honours none of
it, and it is identical again. Motion is purely additive, so there is no client
that sees a broken intermediate -- which is also why no animated GIF is used:
a GIF is a second image, so it dies with images blocked, and Outlook freezes it
on frame one.

The three motions are the application's own, slowed for a medium that is read
once rather than watched:

  ``poBand``   the six omic colours light left to right, from
               ``dotPulse`` (resources/css/ai-interpret.css)
  ``poSettle`` the card lifts in on arrival, from ``aiCardSettle``
               (resources/css/main.css)
  ``poGlow``   the primary action breathes three times, from ``fabGlowPulse``
               (resources/css/ai-interpret.css). The application loops it
               forever because there it means "working"; an inbox is not a
               progress indicator, so here it stops.
"""

import html

from src.conf.serverconf import (
    EMAIL_FROM_ADDRESS,
    PAINTOMICS_BASE_URL,
    PAINTOMICS_LOGIN_URL,
    PAINTOMICS_LOGO_URL,
)

#: The product name, in one place. Every message reads it from here so the next
#: rename is one edit rather than the twelve this one needed.
PRODUCT_NAME = "PaintOmics AI"

#: The six omic hues, in the clockwise order they appear on the brand mark
#: (resources/images/paintomics-mark.svg). They are the application's own
#: type-coding -- the same colours that identify each omic on Step 1, in the
#: legends and in the pathway diagrams -- so the band under the wordmark is the
#: key to the colour language rather than decoration beside it.
OMIC_COLOURS = (
    ("#55C9A6", "Gene expression"),
    ("#79B0EC", "Metabolomics"),
    ("#B4A1DD", "Proteomics"),
    ("#D67F6B", "Other omics"),
    ("#738B9D", "Region based"),
    ("#9A964E", "miRNA"),
)

# Light palette. Every one of these also appears inline somewhere below; the
# names exist so a change lands in one place, not so the template can read them
# at render time from a stylesheet a mail client may have thrown away.
_INK = "#1F2933"
_BODY = "#48535F"
#: Secondary text. #78838F was 3.86:1 on the card and 3.59:1 on the panel --
#: under the 4.5:1 this file darkened _AI_BLUE_DEEP to reach, and it labels
#: the address you sign in with and the temporary password. Darkened until
#: it clears 4.5:1 on all four surfaces the message uses (card #FFFFFF 4.96,
#: panel #F5F7F9 4.62, AI tint #F1F6FC 4.57, page #F4F5F7 4.55).
_MUTED = "#67717C"
_CARD = "#FFFFFF"
_PAGE = "#F4F5F7"
_HAIRLINE = "#E3E6EA"
#: The inset-panel surface. Named because a text colour has to clear 4.5:1
#: against it, so it is one of the four surfaces the contrast test walks.
_PANEL = "#F5F7F9"

#: --pa-ai-blue from resources/css/main.css. The one accent in the message.
_AI_BLUE = "#4A90D9"
#: Darkened until white text on it clears 4.5:1; the token above does not.
_AI_BLUE_DEEP = "#2B6CB0"
_AI_TINT = "#F1F6FC"


def _escape(value):
    """Render an untrusted value safe to drop into the HTML body.

    Names, e-mail addresses and job identifiers all reach these templates from
    the sign-up form or from Mongo, and the old string concatenation put them
    into the markup raw. A user whose affiliation or display name contains an
    ampersand produced an invalid entity, and one containing ``<`` truncated
    the rest of the message at whatever the client decided the tag was.

    ``None`` becomes an empty string rather than the text "None", which is the
    shape ``adaptBSON`` leaves absent fields in.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


#: The retired wordmark, by basename. ``serverconf.py`` is gitignored and
#: written once at install time, so an installation that predates the mark
#: still names this file -- and older templates named it without a suffix, so
#: the URL 404'd and every message showed a broken image. Deploying a new
#: default cannot reach those boxes, and the mark is part of the message
#: design rather than a per-machine setting, so a configured value that still
#: points at the wordmark is treated as unset.
_RETIRED_MARK = "paintomics_white_300x66"

#: The mark itself, as a path under PAINTOMICS_BASE_URL.
_EMAIL_MARK_PATH = "/resources/images/paintomics-mark-email.png"


def _markURL():
    """The absolute URL of the mark every message loads.

    Follows ``PAINTOMICS_LOGO_URL`` -- an operator branding a private
    deployment sets ``PAINTOMICS_LOGO_PATH`` and gets their own mark -- unless
    that value is the wordmark this release retired, in which case the mark is
    served from ``PAINTOMICS_BASE_URL`` instead.

    The base comes from configuration rather than from the retired URL. Slicing
    the configured value on ``/resources/`` happened to work for the one legacy
    default and returned a host-less path for anything else, which no mail
    client can resolve: the message would carry a broken image.
    """
    configured = str(PAINTOMICS_LOGO_URL or "")
    if configured and _RETIRED_MARK not in configured:
        return configured
    return str(PAINTOMICS_BASE_URL or "").rstrip("/") + _EMAIL_MARK_PATH


#: The three configured strings that reach the markup. They are fixed at
#: import and identical in every message, so they are escaped once here rather
#: than on each render -- which is also what stops PAINTOMICS_LOGIN_URL being
#: escaped separately in two different functions.
_LOGIN_HREF = _escape(PAINTOMICS_LOGIN_URL)
_LOGO_SRC = _escape(_markURL())
_CONTACT = _escape(EMAIL_FROM_ADDRESS)


def _enhancementStyles():
    """The ``<style>`` block: motion, dark palette and the phone breakpoint.

    Everything in here is optional by construction -- see the module docstring.
    Note that each ``animation`` rule declares nothing else. That is deliberate
    and load-bearing: a client that keeps this block but drops ``@keyframes``
    (Gmail webmail does exactly that) then applies no property at all, and the
    inline resting styles stand.
    """
    # Built by concatenation rather than by ``%`` interpolation on purpose: CSS
    # is full of literal per-cent signs (``0%``, ``100%``, ``50%``) and every
    # one of them would have to be doubled to survive a format call. Doubling
    # them is the kind of edit that is correct the day it is written and wrong
    # the first time somebody adds a keyframe.
    bandDelays = "".join(
        "      .po-b" + str(i + 1) + " { animation-delay: " + ("%.2f" % (0.18 + i * 0.11)) + "s; }\n"
        for i in range(len(OMIC_COLOURS))
    )
    return """
    @media (prefers-reduced-motion: no-preference) {
      @keyframes poBand   { from { opacity: 0; } to { opacity: 1; } }
      @keyframes poSettle { from { opacity: 0; transform: translateY(8px); }
                            to   { opacity: 1; transform: translateY(0); } }
      @keyframes poGlow   { 0%, 100% { box-shadow: 0 2px 10px rgba(43,108,176,.35); }
                            50%      { box-shadow: 0 2px 28px 6px rgba(43,108,176,.55); } }
      .po-card { animation: poSettle 520ms ease-out both; }
      .po-band { animation: poBand 620ms ease-out both; }
""" + bandDelays + """      .po-cta  { animation: poGlow 1400ms ease-in-out 3 both; animation-delay: 1.1s; }
    }
    @media (max-width: 620px) {
      /* iOS Mail's own auto-shrink is left switched on, but a 600px table on a
         375px screen still needs the card itself to yield, not just its padding. */
      .po-card { width: 100% !important; }
      .po-pad  { padding-left: 22px !important; padding-right: 22px !important; }
      .po-h1   { font-size: 24px !important; }
    }
    @media (prefers-color-scheme: dark) {
      /* Apple Mail and iOS Mail honour this; Outlook.com force-inverts instead,
         which is why no rule here depends on a colour staying light. */
      .po-page    { background-color: #16181C !important; }
      .po-card    { background-color: #1E1F23 !important; }
      .po-ink     { color: #E8EAED !important; }
      .po-text    { color: #C3C8CE !important; }
      .po-muted   { color: #99A0A8 !important; }
      .po-rule    { border-color: #33363C !important; }
      .po-aiband  { background-color: #182430 !important; }
      .po-aitext  { color: #D3DAE2 !important; }
      .po-link    { color: #7FB2E8 !important; }
      .po-panel   { background-color: #23252A !important; }
    }
"""


def _header():
    """Mark, wordmark and the omic band.

    The wordmark is live text and the mark is the only image, so a reader with
    images blocked still gets a branded message rather than a grey rectangle
    where the identity used to be -- which is exactly what the old template
    gave them, because there the wordmark *was* the raster.

    The band is six ``<td>`` elements carrying a ``bgcolor`` attribute, which is
    the one thing Word renders exactly as asked. Nothing is nested inside them:
    a bulletproof cell with a fragile child in it is not bulletproof.

    The mark's CELL is pinned in both dimensions. Width alone was not enough --
    with the image gone the row fell back to the height of the two text lines,
    about 6px shorter, and every row beneath it moved up. Nothing looked broken,
    but "blocked and loaded are the same box" was not true until the height was
    pinned too.

    The IMAGE inside it is sized by width alone. Pinning both axes there does
    something different and worse: it squashes any mark that is not square, and
    an installation whose gitignored serverconf still names the retired 300x66
    wordmark got exactly that -- a 4.55:1 aspect change. The cell holds the
    layout; the image scales inside it.
    """
    cells = ""
    for index, (colour, label) in enumerate(OMIC_COLOURS):
        cells += (
            '<td class="po-band po-b%d" bgcolor="%s" width="16.66%%" '
            'style="background-color:%s;height:6px;line-height:6px;font-size:0;" '
            'title="%s">&nbsp;</td>'
        ) % (index + 1, colour, colour, label)

    return """
      <tr>
        <td class="po-pad" style="padding:32px 36px 20px 36px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td width="64" height="52"
                  style="width:64px;height:52px;padding-right:14px;" valign="middle">
                <!-- The cell above is a fixed 64x52 so blocked and loaded
                     images occupy the same box. The image itself is sized
                     by width alone: pinning both axes squashed any mark
                     that is not square, which is what an installation
                     still configured for the 300x66 wordmark would get. -->
                <a href="%(login)s" target="_blank" style="text-decoration:none;"><img
                  src="%(logo)s" width="52" alt=""
                  style="display:block;width:52px;height:auto;border:0;
                  outline:none;"></a>
              </td>
              <td valign="middle">
                <div class="po-ink" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                  font-size:22px;font-weight:700;letter-spacing:-.2px;color:%(ink)s;
                  mso-line-height-rule:exactly;line-height:26px;">Paint<span
                  style="color:%(blue)s;">Omics</span>&nbsp;AI</div>
                <div class="po-muted" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                  font-size:12px;color:%(muted)s;mso-line-height-rule:exactly;line-height:18px;
                  padding-top:2px;">Multi-omics pathway analysis</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:0;">
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"
                 style="width:100%%;border-collapse:collapse;">
            <tr>%(cells)s</tr>
          </table>
        </td>
      </tr>
""" % {
        "login": _LOGIN_HREF,
        "logo": _LOGO_SRC,
        "ink": _INK,
        "blue": _AI_BLUE,
        "muted": _MUTED,
        "cells": cells,
    }


def _footer(legalNote=""):
    """Support address and, for the reminder mail, its lawful-basis note."""
    contact = _CONTACT
    extra = ""
    if legalNote:
        extra = (
            '<p class="po-muted" style="margin:10px 0 0 0;font-family:\'Helvetica Neue\','
            "Helvetica,Arial,sans-serif;font-size:11px;color:%s;mso-line-height-rule:exactly;"
            'line-height:17px;">%s</p>' % (_MUTED, legalNote)
        )
    return """
      <tr>
        <td class="po-pad" style="padding:8px 36px 34px 36px;">
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
            <tr><td class="po-rule" style="border-top:1px solid %(rule)s;font-size:0;
                line-height:0;height:1px;">&nbsp;</td></tr>
          </table>
          <p class="po-muted" style="margin:16px 0 0 0;font-family:'Helvetica Neue',Helvetica,
            Arial,sans-serif;font-size:12px;color:%(muted)s;mso-line-height-rule:exactly;
            line-height:19px;">Something not working? Write to <a class="po-link"
            href="mailto:%(contact)s" style="color:%(blue)s;">%(contact)s</a> and a human
            will read it.</p>
          %(extra)s
        </td>
      </tr>
""" % {
        "rule": _HAIRLINE,
        "muted": _MUTED,
        "contact": contact,
        "blue": _AI_BLUE_DEEP,
        "extra": extra,
    }


def renderEmail(bodyRows, preheaderText="", legalNote=""):
    """Wrap ``bodyRows`` -- ``<tr>`` markup -- in the shared chrome.

    Callers build only their own middle; the document, the header and the
    footer come from here. ``preheaderText`` is the line a client shows next to
    the subject in the message list. It is hidden in the body itself, and padded
    with zero-width spaces so the client does not pull the first paragraph in
    behind it.

    ``preheaderText`` is PLAIN TEXT and is escaped here -- unlike ``bodyRows``
    and ``legalNote``, and unlike every other helper in this file, all of which
    take markup. Write an em dash, not ``&mdash;``: an entity written here is
    escaped into the six literal characters a reader then sees in the inbox
    preview line.
    """
    hiddenPreheader = ""
    if preheaderText:
        hiddenPreheader = (
            '<div style="display:none;font-size:1px;color:%s;line-height:1px;max-height:0;'
            'max-width:0;opacity:0;overflow:hidden;mso-hide:all;">%s%s</div>'
        ) % (_PAGE, _escape(preheaderText), "&#847;&zwnj;&nbsp;" * 60)

    return """<!DOCTYPE html>
<html lang="en" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch>
</o:OfficeDocumentSettings></xml><![endif]-->
<title>%(product)s</title>
<style>%(styles)s</style>
</head>
<body class="po-page" style="margin:0;padding:0;background-color:%(page)s;
  -webkit-font-smoothing:antialiased;">
%(preheader)s
<table role="presentation" class="po-page" width="100%%" cellpadding="0" cellspacing="0"
       border="0" style="background-color:%(page)s;">
  <tr>
    <td align="center" style="padding:28px 12px;">
      <table role="presentation" class="po-card" width="600" cellpadding="0" cellspacing="0"
             border="0" bgcolor="%(card)s"
             style="width:600px;max-width:600px;background-color:%(card)s;border-radius:10px;">
%(header)s
%(body)s
%(footer)s
      </table>
    </td>
  </tr>
</table>
</body>
</html>
""" % {
        "product": PRODUCT_NAME,
        "styles": _enhancementStyles(),
        "page": _PAGE,
        "card": _CARD,
        "preheader": hiddenPreheader,
        "header": _header(),
        "body": bodyRows,
        "footer": _footer(legalNote),
    }


def _heading(text):
    return (
        '<h1 class="po-ink po-h1" style="margin:0 0 14px 0;font-family:\'Helvetica Neue\','
        "Helvetica,Arial,sans-serif;font-size:27px;font-weight:700;letter-spacing:-.4px;"
        'color:%s;mso-line-height-rule:exactly;line-height:34px;">%s</h1>'
    ) % (_INK, text)


def _paragraph(text):
    return (
        '<p class="po-text" style="margin:0 0 14px 0;font-family:\'Helvetica Neue\',Helvetica,'
        "Arial,sans-serif;font-size:15px;color:%s;mso-line-height-rule:exactly;"
        'line-height:24px;">%s</p>'
    ) % (_BODY, text)


def _button(href, label):
    """A call to action Word can draw.

    The padding is on the ``<td>``. Word does not honour vertical padding on an
    inline element and does not honour ``display:block`` on an ``<a>``, so a
    button built the usual way -- all the padding on the anchor -- arrives in
    Outlook as a thin coloured line with the text overflowing it.

    The resting ``box-shadow`` is inline and is the same value ``poGlow``
    returns to at 100%. That is the animation invariant applied to this
    element, and it was got wrong first: with the shadow living only in the
    keyframe, a client that animates came to rest 46 grey levels away from a
    client that does not -- measured, by rendering both and differencing them.
    Word ignores ``box-shadow`` either way, so Outlook draws a flat button; the
    point is that every client which *can* draw a shadow now draws the same one.
    """
    return """
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                 style="margin:6px 0 10px 0;">
            <tr>
              <td class="po-cta" bgcolor="%(bg)s" align="center"
                  style="background-color:%(bg)s;border-radius:8px;padding:14px 30px;
                  box-shadow:0 2px 10px rgba(43,108,176,.35);">
                <a href="%(href)s" target="_blank" style="font-family:'Helvetica Neue',
                  Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;color:#FFFFFF;
                  text-decoration:none;mso-line-height-rule:exactly;line-height:20px;
                  display:inline-block;">%(label)s</a>
              </td>
            </tr>
          </table>
""" % {"bg": _AI_BLUE_DEEP, "href": href, "label": label}


def _aiCallout(title, text):
    """The AI note, in the application's own idiom.

    ``.po-ai-section-body`` in main.css is a tinted panel with a 3px accent rail
    down its left edge. A left border on a ``<td>`` is one of the few borders
    Word draws, so the shape survives everywhere; only the tint changes between
    the light and dark palettes.
    """
    return """
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"
                 style="margin:6px 0 20px 0;">
            <tr>
              <td class="po-aiband" bgcolor="%(tint)s"
                  style="background-color:%(tint)s;border-left:3px solid %(blue)s;
                  border-radius:0 8px 8px 0;padding:16px 18px;">
                <p class="po-ink" style="margin:0 0 5px 0;font-family:'Helvetica Neue',
                  Helvetica,Arial,sans-serif;font-size:14px;font-weight:700;color:%(ink)s;
                  mso-line-height-rule:exactly;line-height:20px;">%(title)s</p>
                <p class="po-aitext" style="margin:0;font-family:'Helvetica Neue',Helvetica,
                  Arial,sans-serif;font-size:14px;color:%(body)s;mso-line-height-rule:exactly;
                  line-height:22px;">%(text)s</p>
              </td>
            </tr>
          </table>
""" % {
        "tint": _AI_TINT,
        "blue": _AI_BLUE,
        "ink": _INK,
        "body": _BODY,
        "title": title,
        "text": text,
    }


def _panel(rows, accent="", monospace=False):
    """A quiet inset block for facts the reader may need to copy out.

    ``accent`` puts a coloured rule down the left edge, squaring that corner so
    the rule reads as an edge rather than a stripe on a lozenge. The report
    notification uses it to tell an error report from an organism request at a
    glance; nothing else about the block changes, and in particular the text
    stays normal ink, because colouring the body itself put a 3.5:1 run of red
    on the dark card Apple Mail substitutes.

    ``monospace`` is for a block that is quoted rather than read -- a pasted
    traceback or a run of tab-separated data. A fixed-pitch face is only half
    of that: HTML collapses runs of spaces and tabs, so the indentation and the
    columns are gone before the font can line them up, which is why the face
    comes with ``white-space``. ``word-break`` goes with it, because preserved
    text no longer collapses at a space either -- one long unbroken token then
    stretches the whole 600px card past the edge of the window.
    """
    edge = "border-radius:8px;"
    if accent:
        edge = "border-left:4px solid %s;border-radius:0 8px 8px 0;" % accent
    face = ""
    if monospace:
        face = ("font-family:Menlo,Consolas,monospace;font-size:13px;"
                "white-space:pre-wrap;word-break:break-word;"
                "mso-line-height-rule:exactly;line-height:20px;")
    return """
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"
                 style="margin:4px 0 18px 0;">
            <tr>
              <td class="po-panel" bgcolor="%(panel)s" style="background-color:%(panel)s;
                  %(edge)spadding:14px 18px;%(face)s">%(rows)s</td>
            </tr>
          </table>
""" % {"panel": _PANEL, "rows": rows, "edge": edge, "face": face}


def _panelRow(label, value):
    return (
        '<p class="po-text" style="margin:0;font-family:\'Helvetica Neue\',Helvetica,Arial,'
        "sans-serif;font-size:14px;color:%s;mso-line-height-rule:exactly;line-height:24px;\">"
        '<span class="po-muted" style="color:%s;">%s</span> %s</p>'
    ) % (_BODY, _MUTED, label, value)


def _bodyRow(inner):
    return '      <tr><td class="po-pad" style="padding:26px 36px 4px 36px;">%s</td></tr>' % inner


def welcomeEmail(userName, userEmail):
    """The message a new account receives.

    Its job is to get the reader to a first painted pathway, so the one primary
    action is loading an example dataset rather than a bare link to the site.
    """
    name = _escape(userName)
    greeting = ("Thanks for joining, %s." % name) if name else "Thanks for joining."

    inner = (
        _heading("Welcome to %s" % PRODUCT_NAME)
        + _paragraph(
            "%s Your account is ready, and there is nothing else to install or "
            "configure &mdash; %s runs in the browser."
            % (greeting, PRODUCT_NAME)
        )
        + _panel(_panelRow("You sign in with", "<strong>%s</strong>" % _escape(userEmail)))
        + _paragraph(
            "The fastest way to see what it does is to start from an example rather "
            "than your own files. Pick one on Step 1, and you will have painted "
            "pathways in a couple of minutes."
        )
        + _button(_LOGIN_HREF, "Open %s" % PRODUCT_NAME)
        + _aiCallout(
            "Your pathways, interpreted",
            "Once an analysis finishes, the AI assistant reads the enriched pathways "
            "together with your own data and writes up what they mean &mdash; which "
            "pathways moved, in which direction, and which features drove them. It is "
            "off until you ask for it, and it never sees a file you did not submit.",
        )
        + _paragraph(
            "Bring gene expression, metabolomics, proteomics, region-based data, miRNA "
            "or anything else you measured; the six colours above are the omic types "
            "%s paints onto a pathway at once." % PRODUCT_NAME
        )
    )
    return renderEmail(
        _bodyRow(inner),
        preheaderText="Your account is ready \u2014 start from an example dataset.",
    )


def passwordResetEmail(userName, resetLink, temporaryPassword):
    """The message behind "I forgot my password".

    The temporary password is shown in a panel rather than a heading because it
    has to be copied accurately, and a heading font is the wrong tool for a
    string where O and 0 have to be told apart.
    """
    name = _escape(userName)
    opening = ("Hello %s," % name) if name else "Hello,"
    inner = (
        _heading("Reset your password")
        + _paragraph(
            "%s someone asked to reset the password on your %s account. "
            "If that was not you, ignore this message and nothing changes."
            % (opening, PRODUCT_NAME)
        )
        # html.escape, not _escape, and deliberately so at both link sites:
        # _escape turns None into "", which would ship a message whose only
        # action is href="". A missing link must raise here so the caller's
        # "except Exception: logging.error(...)" fires and no mail with a
        # dead button is delivered.
        + _button(html.escape(resetLink, quote=True), "Reset my password")
        + _paragraph("Then sign in with this temporary password and change it:")
        + _panel(
            _panelRow(
                "Temporary password",
                '<strong style="font-family:Menlo,Consolas,monospace;letter-spacing:.5px;">'
                "%s</strong>" % _escape(temporaryPassword),
            )
        )
        # Says only what the code does. The token is cleared on use
        # (userManagementResetPassword), so "works once" is true -- but it is
        # stored with no timestamp and never expires, so the earlier draft's
        # "if it has already expired" promised a protection that does not
        # exist. Copy must not describe a control the server does not have.
        + _paragraph(
            "The link works once: following it is what makes the temporary "
            "password above active. If you need another, ask for one from the "
            "sign-in page."
        )
    )
    return renderEmail(
        _bodyRow(inner),
        preheaderText="A link to reset your %s password." % PRODUCT_NAME,
    )


def jobExpiryEmail(userName, jobID, reminderLink):
    """The week's notice before a stored job is deleted."""
    name = _escape(userName)
    opening = ("Hello %s," % name) if name else "Hello,"
    # Two forms of the same identifier: the body interpolates markup and needs
    # the escaped one, while renderEmail escapes the preheader itself and needs
    # the plain one. Escaping twice is what put "&amp;" in the preview line.
    jobText = "" if jobID is None else str(jobID)
    job = _escape(jobText)
    inner = (
        _heading("A job is about to expire")
        + _paragraph(
            "%s your analysis <strong>%s</strong> will be deleted in one week. "
            "Opening it resets the clock &mdash; you do not have to re-run anything."
            % (opening, job)
        )
        # Fail loud on a missing link, as in passwordResetEmail above.
        + _button(html.escape(reminderLink, quote=True), "Keep this job")
        + _paragraph(
            "If you no longer need it, do nothing and it will be removed on schedule."
        )
    )
    return renderEmail(
        _bodyRow(inner),
        preheaderText="Job %s will be deleted in one week." % jobText,
        legalNote=(
            "You are receiving this because you accepted the %s terms when the job was "
            "created. Your address is stored only to tell you about actions affecting "
            "your own jobs." % PRODUCT_NAME
        ),
    )


def reportNotificationEmail(title, userName, userEmail, reportBody, accent):
    """The notification the maintainers get for an error report or a request.

    ``reportBody`` is whatever the reporter typed, so it is escaped and then
    given back its line breaks; the old template dropped it into the markup raw
    and rendered a paste of tab-separated data as one run-on line.
    """
    name = _escape(userName)
    body = _escape(reportBody).replace("\n", "<br>")
    inner = (
        _heading(title)
        + _paragraph("Thanks for the report%s. We will get back to you." % ((", " + name) if name else ""))
        + _panel(_panelRow("From", "<strong>%s</strong>" % _escape(userEmail)))
        # The same inset block as above, with the accent in its border rather
        # than in the text -- the colour used to run through the whole monospace
        # body, which put 3.5:1 of red on the dark card Apple Mail substitutes.
        + _panel(
            '<span class="po-text" style="color:%s;">%s</span>' % (_BODY, body),
            accent=accent, monospace=True)
        + _paragraph("&mdash; the %s team" % PRODUCT_NAME)
    )
    return renderEmail(_bodyRow(inner), preheaderText=title)
