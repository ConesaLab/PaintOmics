"""KGML -> attributed edges.

Every attribute is read by NAME from the element that owns it. The R parser this
replaces read subtypes from a document-global list indexed by relation number
(28.2% of mmu subtypes wrong, 194 of 364 pathways affected) and reaction headers
by token position (14.0% of reaction rows corrupted, 2,388 of them holding the
literal attribute name "type"). Both are unrepresentable here by construction.
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from collections import namedtuple

logger = logging.getLogger(__name__)

# ``via``: the KEGG compound id a ``compound`` relation is drawn through
# (ECrel: two enzymes joined by the metabolite between them), else None.
Edge = namedtuple("Edge", "a b kind subtype pathway reversible via", defaults=(None,))


def _names(entry):
    """Concrete KEGG ids on an entry, prefix stripped: 'tst:100 tst:101' -> ['100','101'].

    A `map` entry is named `path:tst00002`; keep the id so map links stay visible
    to the caller. Filtering them is the graph's job, not the parser's.
    """
    out = []
    for token in (entry.get("name") or "").split():
        if ":" in token:
            out.append(token.split(":", 1)[1])
    return out


def parse_pathway(path):
    """(edges, entry_types) for one KGML file. Never raises on a bad file."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        logger.warning("[keggraph] unreadable KGML %s: %s", path, exc)
        return [], {}

    pathway = (root.get("name") or "").replace("path:", "")
    entries, groups, types = {}, {}, {}

    for entry in root.findall("entry"):
        eid = entry.get("id")
        etype = entry.get("type")
        names = _names(entry)
        if etype == "group":
            groups[eid] = [c.get("id") for c in entry.findall("component")]
        entries[eid] = names
        for name in names:
            types[name] = etype

    def expand(eid):
        """A group resolves to its components' names; anything else to its own.

        No component cap. The R side silently truncated at 50 via
        `seq(2, 100, by = 2)`; the largest real group observed on mmu is 13.
        """
        if eid in groups:
            out = []
            for component in groups[eid]:
                out.extend(entries.get(component, []))
            return out
        return entries.get(eid, [])

    edges = {}

    def add(a, b, kind, subtype, reversible, via=None):
        # An unnamed endpoint is never a real biological entity. The R pipeline
        # let one through as a node called "" that reached degree 1,381.
        if not a or not b or a == b:
            return
        edges.setdefault((a, b), Edge(a, b, kind, subtype, pathway, reversible, via))

    def mediator(relation):
        """The compound a ``compound`` subtype names, by its entry id -> KEGG id."""
        for sub in relation.findall("subtype"):
            if sub.get("name") != "compound":
                continue
            for name in entries.get(sub.get("value") or "", []):
                if types.get(name) == "compound":
                    return name
        return None

    for relation in root.findall("relation"):
        kind = relation.get("type") or "?"
        # D-1: THIS relation's own subtype children, in document order.
        subtype = ",".join(s.get("name") or "" for s in relation.findall("subtype"))
        via = mediator(relation)
        for a in expand(relation.get("entry1")):
            for b in expand(relation.get("entry2")):
                add(a, b, kind, subtype, False, via)

    for reaction in root.findall("reaction"):
        # D-2: attributes by name. `name` may hold several ids.
        reversible = reaction.get("type") == "reversible"
        ids = ",".join((reaction.get("name") or "").split())
        compounds = [c.get("name", "").split(":")[-1]
                     for c in list(reaction.findall("substrate"))
                     + list(reaction.findall("product"))]
        for enzyme in expand(reaction.get("id")):
            for compound in compounds:
                add(enzyme, compound, "reaction", ids, reversible)

    return list(edges.values()), types


def parse_directory(kgml_dir):
    """(edges, entry_types, files_read) over every *.kgml in a directory."""
    edges, types, read = {}, {}, 0
    try:
        listing = sorted(os.listdir(kgml_dir))
    except OSError as exc:
        logger.warning("[keggraph] cannot list %s: %s", kgml_dir, exc)
        return [], {}, 0
    for name in listing:
        if not name.endswith(".kgml"):
            continue
        found, found_types = parse_pathway(os.path.join(kgml_dir, name))
        if not found and not found_types:
            continue
        read += 1
        types.update(found_types)
        for edge in found:
            edges.setdefault((edge.a, edge.b), edge)
    return list(edges.values()), types, read
