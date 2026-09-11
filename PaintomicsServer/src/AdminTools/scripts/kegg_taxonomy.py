#!/usr/bin/env python
"""KEGG's organism taxonomy, from the br08610 BRITE hierarchy.

KEGG retired `/list/organism`, and with it the fourth column every consumer of
`organisms_all.list` used to read: the lineage string
(`Eukaryotes;Animals;Vertebrates;Mammals`). `/list/genome`, its replacement,
carries no taxonomy at all, so the converted list was written with that column
empty -- and `ensembl_census.py registry`, which selects the eukaryotes by
`startswith("Eukaryotes")`, silently resolved zero species.

The lineage still exists in KEGG, as the BRITE hierarchy `br08610`
("Organisms in the taxonomic classification"), one request for all ~12,000
organisms:

    AEukaryota
    B  Metazoa
    C    Chordata
    ...
    O                            hsa  Homo sapiens (human)
    ABacteria
    B  Pseudomonadati
    ...

This module parses it and renders the historic column so nothing downstream
has to change: the kingdom is spelled the way the old list spelled it
(`Eukaryotes`, `Prokaryotes;Bacteria`, `Prokaryotes;Archaea`) and the next
levels of the BRITE tree follow. No project imports here on purpose: both
DBManager and the census tool load it, and it must work in a bare interpreter.
"""
import re
import time

try:
    from urllib.request import urlopen
except ImportError:  # pragma: no cover - Python 2 is not supported, but be explicit
    from urllib2 import urlopen  # type: ignore

KEGG_TAXONOMY_URL = "https://rest.kegg.jp/get/br:br08610"

#: How the retired /list/organism spelled the top level. Its prokaryote rows
#: read "Prokaryotes;Bacteria;..." and "Prokaryotes;Archaea;..."; eukaryotes
#: read "Eukaryotes;...". Consumers test the prefix, so the spelling matters.
LEGACY_KINGDOM = {
    "Eukaryota": ("Eukaryotes",),
    "Bacteria": ("Prokaryotes", "Bacteria"),
    "Archaea": ("Prokaryotes", "Archaea"),
}

#: A leaf row: level letter, indentation, a code of 2-5 lowercase letters, two
#: or more spaces, the name. Inner rows (`B  Metazoa`) have no such code.
_LEAF = re.compile(r"^([A-Z])\s+([a-z]{2,5})\s{2,}(.+?)\s*$")
_NODE = re.compile(r"^([A-Z])\s*(.*?)\s*$")


def parseOrganismTaxonomy(text):
    """{code: {"kingdom": ..., "lineage": [...], "name": ...}} from br08610 text.

    `lineage` is the list of BRITE node names above the organism, kingdom first
    (`["Eukaryota", "Metazoa", "Chordata", ...]`). Lines that are not part of
    the hierarchy (`+X`, `!`, `#`) are ignored.
    """
    organisms = {}
    stack = []  # (level letter, name)
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line or line[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            continue
        leaf = _LEAF.match(line)
        if leaf:
            level, code, name = leaf.groups()
            lineage = [name for lvl, name in stack if lvl < level]
            if not lineage:
                continue
            organisms[code] = {"kingdom": lineage[0], "lineage": lineage, "name": name}
            continue
        node = _NODE.match(line)
        if not node:
            continue
        level, name = node.groups()
        if not name:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, name))
    return organisms


def legacyLineage(entry, depth=3):
    """The old organisms_all.list column 4 for one parsed entry.

    `Eukaryotes;Metazoa;Chordata` / `Prokaryotes;Bacteria;Bacillati;Bacillota`
    -- the historic kingdom spelling followed by up to `depth` BRITE levels.
    Unknown kingdoms are rendered as they come, so a KEGG rename cannot make
    a whole kingdom disappear from the file.
    """
    lineage = entry.get("lineage") or []
    if not lineage:
        return ""
    head = LEGACY_KINGDOM.get(lineage[0], (lineage[0],))
    return ";".join(list(head) + lineage[1:1 + depth])


def fetchOrganismTaxonomy(url=KEGG_TAXONOMY_URL, timeout=120, retries=3, delay=5):
    """Download and parse br08610. Raises after `retries` failed attempts."""
    lastError = None
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
            organisms = parseOrganismTaxonomy(text)
            if not organisms:
                raise ValueError("no organisms parsed from " + url)
            return organisms
        except Exception as exc:  # network or parse: retry, then give up loudly
            lastError = exc
            if attempt + 1 < retries:
                time.sleep(delay)
    raise Exception("Unable to retrieve the KEGG organism taxonomy from " + url + ": " + str(lastError))


def isEukaryote(entry):
    return bool(entry) and entry.get("kingdom") == "Eukaryota"
