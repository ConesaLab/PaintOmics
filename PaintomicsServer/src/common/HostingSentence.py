"""The sentence the welcome dialog uses to say where this server runs.

The same client is served by paintomics.org, which runs on Supercomputador
Drago (CSIC), and by other servers, among them paintomics.uv.es at the
Universitat de Valencia. The sentence used to be written into MainView.js, so
every server told its visitors it ran on Drago. It is a server setting now,
PAINTOMICS_HOSTED_BY (see example_serverconf.py), and a server without the
setting says nothing about where it runs.

The setting holds the words that follow "PaintOmics AI runs on", for example

    the Universitat de Valencia (I2SysBio)

and may carry links written as [words](https://address):

    [Supercomputador Drago](https://aic.csic.es/supercomputador-drago/) (CSIC)

The setting is the operator's, not a visitor's, but it is still turned into
HTML here rather than passed through: the words are escaped, and only http and
https addresses become links, so a typo cannot break the dialog's markup.
"""
import html
import re

# [words](https://address). The address stops at whitespace, brackets, angle
# brackets and quotes, so it cannot leave the href attribute it is put in.
_LINK = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s()<>\"']+)\)")


def hostingSentenceHtml(setting):
    """"PaintOmics AI runs on <setting>." as HTML, or "" when there is none.

    Anything that is not a non-blank string (None, a hand-kept serverconf.py
    that never defined the setting, an empty environment variable) means the
    server does not say where it runs.
    """
    if not isinstance(setting, str):
        return ""
    words = setting.strip()
    # The sentence adds its own full stop.
    if words.endswith("."):
        words = words[:-1].rstrip()
    if not words:
        return ""

    parts = []
    position = 0
    for link in _LINK.finditer(words):
        parts.append(html.escape(words[position:link.start()]))
        parts.append('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (html.escape(link.group(2)), html.escape(link.group(1))))
        position = link.end()
    parts.append(html.escape(words[position:]))
    return "PaintOmics AI runs on " + "".join(parts) + "."
