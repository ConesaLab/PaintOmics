"""The Step 1 form's own placeholder text, which is never the user's answer.

Ext JS 4's form submit sends an empty field's emptyText as its value
(`Ext.form.action.Submit.submitEmptyText` defaults to true), and Ext renders
emptyText as a placeholder="..." attribute, so the field looks empty on screen
while the request carries the hint. On paintomics.org on 2026-10-28, 32 of 64
jobs stored the Experiment design field's placeholder as `experimentDesign` --
zebrafish, E. coli, potato, N. gonorrhoeae, S. aureus, human, pig, mouse and
Leishmania jobs alike -- and the AI walk read it as the design: perturbation
"knockout of gene in mouse liver", system "mouse liver", and titles such as
"... in a knockout mouse liver transcriptome" on zebrafish job JK5047lsnI.

The client now submits with submitEmptyText: false. This list is the server's
half: a value that IS one of the form's placeholders is read as "", both when a
job is created and when a stored job is read, so the jobs already saved with it
need no database rewrite. Only an exact match counts (after whitespace, quote
and case folding); a real design that merely mentions "mouse liver" is kept.
"""
import re

# Every placeholder the Experiment design textarea has shipped with
# (PA_Step1Views.js). The second is the one 24dc08f1 introduced on 2026-03-04
# and a841d36c replaced on 2026-08-11; jobs submitted in between carry it.
EXPERIMENT_DESIGN_PLACEHOLDERS = (
    "e.g. RNA-seq of wildtype vs knockout mouse liver, 3 replicates per group, sampled at 24 h",
    'e.g., "RNA-seq wildtype vs knockout mouse liver, n=3 per group"',
)

_WHITESPACE = re.compile(r"\s+")
# The comment above the field suggests curly quotes for the old placeholder's
# straight ones; fold both, so either spelling matches.
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _normalised(text):
    return _WHITESPACE.sub(" ", str(text).translate(_QUOTES)).strip().casefold()


_DESIGN_PLACEHOLDER_KEYS = frozenset(_normalised(p) for p in EXPERIMENT_DESIGN_PLACEHOLDERS)


def isDesignPlaceholder(text):
    """True when `text` is one of the design field's placeholders, ignoring
    whitespace runs, curly versus straight quotes and case."""
    if not text:
        return False
    return _normalised(text) in _DESIGN_PLACEHOLDER_KEYS


def designWithoutPlaceholder(text):
    """The design text as given, or "" when it is the form's placeholder."""
    if not text:
        return ""
    text = str(text)
    return "" if isDesignPlaceholder(text) else text
