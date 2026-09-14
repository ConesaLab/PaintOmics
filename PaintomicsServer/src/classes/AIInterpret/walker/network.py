"""The universal network: every annotation file the install ships, read once
into one node-edge table.

Sources (all optional; whatever is present is read):
  * KEGG   -- ``<data_dir>/current/<org>/kgml/*.kgml`` through the KGML parser
              the metabolite hub already uses.
  * Reactome -- ``<data_dir>/current/<org>/reactome/*.graph.json`` reactions:
              input or catalyst -> output.
  * OmniPath -- the ``omnipath_network`` collection of the organism's Mongo
              database: ``{ID, edges: [[uniprot, uniprot, type], ...]}``.

Nodes are keyed by KEGG gene id (``g:<entrez>``) or compound id (``c:<id>``).
UniProt ids map to gene ids through ``mapping/uniprot2kegg.list``; symbols
through ``mapping/kegg2genesymbol.list``. Every edge carries the databases and
pathways it came from and a sign (+1 activation/expression/stimulation,
-1 inhibition/repression/dephosphorylation, 0 otherwise).

Sizes measured on mmu (2026-09-14): 18,931 nodes, 113,327 edges, read in
under a second; the id maps are the only real work. The result is cached as
JSON beside the KGML directory with a signature of the source listing, the
way the KEGG graph store caches its CSR graph.
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import json
import logging
import os
import re
from collections import defaultdict

from src.common.KeggGraph.parser import parse_directory

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
# KGML relation subtypes -> sign. Anything else (binding, indirect, ...) is 0.
SIGN_BY_SUBTYPE = {
    "activation": 1, "expression": 1,
    "inhibition": -1, "repression": -1, "dephosphorylation": -1,
}
_UNIPROT_RE = re.compile(r"^[A-NR-Z][0-9][A-Z0-9]{3}[0-9]$|^[OPQ][0-9][A-Z0-9]{3}[0-9]$"
                         r"|^[A-Z][0-9][A-Z0-9]{3}[0-9][A-Z][A-Z0-9]{2}[0-9]$")


class Network(object):
    """Nodes, signed tagged edges, and an adjacency built on first use.

    ``nodes``: id -> {"label", "kind", "pathways": [tag, ...]}
    ``edges``: (a, b) -> {"sign", "subtype", "tags": ["KEGG:mmu04068", ...]}
    """

    def __init__(self, organism):
        self.organism = organism
        self.nodes = {}
        self.edges = {}
        self.pathway_names = {}
        self.uniprot_to_kegg = {}
        self.symbol_to_kegg = {}
        self.sources = {}                  # pathways read per source, set by build_network
        self._adjacency = None

    # ---- construction -------------------------------------------------
    def add_node(self, node_id, label, kind):
        node = self.nodes.get(node_id)
        if node is None:
            self.nodes[node_id] = {"label": label or node_id, "kind": kind,
                                   "pathways": []}
        self._adjacency = None

    def add_edge(self, a, b, sign, tag, subtype=""):
        """Add or enrich the directed edge a -> b. A second source for the same
        pair adds its tag; a non-zero sign never downgrades to zero."""
        if a == b or a not in self.nodes or b not in self.nodes:
            return
        edge = self.edges.get((a, b))
        if edge is None:
            edge = self.edges[(a, b)] = {"sign": int(sign), "subtype": subtype or "",
                                         "tags": []}
        if tag and tag not in edge["tags"]:
            edge["tags"].append(tag)
            for node_id in (a, b):
                pws = self.nodes[node_id]["pathways"]
                if tag not in pws:
                    pws.append(tag)
        if sign and not edge["sign"]:
            edge["sign"] = int(sign)
        if subtype and not edge["subtype"]:
            edge["subtype"] = subtype
        self._adjacency = None

    # ---- queries ------------------------------------------------------
    @property
    def adjacency(self):
        if self._adjacency is None:
            adj = defaultdict(set)
            for (a, b) in self.edges:
                adj[a].add(b)
                adj[b].add(a)
            self._adjacency = adj
        return self._adjacency

    def neighbours(self, node_id):
        return self.adjacency.get(node_id, set())

    def edge_between(self, a, b):
        """(edge, direction): the stored edge for the pair and whether a -> b
        walks WITH the stored arrow. None when no edge joins them."""
        edge = self.edges.get((a, b))
        if edge is not None:
            return edge, "with"
        edge = self.edges.get((b, a))
        if edge is not None:
            return edge, "against"
        return None, None

    def filter_pathway(self, tag):
        """The subgraph of one pathway tag: its nodes and the edges tagged with it.
        Nothing is rebuilt; the annotation file was read at install."""
        sub = Network(self.organism)
        sub.pathway_names = self.pathway_names
        sub.uniprot_to_kegg = self.uniprot_to_kegg
        sub.symbol_to_kegg = self.symbol_to_kegg
        for (a, b), edge in self.edges.items():
            if tag in edge["tags"]:
                for node_id in (a, b):
                    if node_id not in sub.nodes:
                        node = self.nodes[node_id]
                        sub.add_node(node_id, node["label"], node["kind"])
                sub.add_edge(a, b, edge["sign"], tag, edge["subtype"])
        return sub

    def pathway_name(self, tag):
        return self.pathway_names.get(tag.split(":", 1)[-1], tag)

    # ---- serialisation ------------------------------------------------
    def to_dict(self):
        return {
            "version": CACHE_VERSION, "organism": self.organism,
            "pathway_names": self.pathway_names,
            "uniprot_to_kegg": self.uniprot_to_kegg,
            "symbol_to_kegg": self.symbol_to_kegg,
            "sources": self.sources,
            "nodes": self.nodes,
            "edges": [[a, b, e["sign"], e["subtype"], e["tags"]]
                      for (a, b), e in self.edges.items()],
        }

    @classmethod
    def from_dict(cls, data):
        net = cls(data["organism"])
        net.pathway_names = data.get("pathway_names", {})
        net.uniprot_to_kegg = data.get("uniprot_to_kegg", {})
        net.symbol_to_kegg = data.get("symbol_to_kegg", {})
        net.sources = data.get("sources", {})
        net.nodes = data["nodes"]
        for a, b, sign, subtype, tags in data["edges"]:
            net.edges[(a, b)] = {"sign": sign, "subtype": subtype, "tags": list(tags)}
        return net


# ---------------------------------------------------------------------------
# id maps
# ---------------------------------------------------------------------------
def _read_uniprot_map(path):
    """``up:Q1WG82<TAB>mmu:100009600`` -> {"Q1WG82": "100009600"}."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            uniprot = parts[0].split(":", 1)[-1]
            kegg = parts[1].split(":", 1)[-1]
            if kegg.isdigit():
                out.setdefault(uniprot, kegg)
    return out


def _read_symbol_map(kegg_symbol_path, ncbi_reactome_path):
    """gene id -> symbol.

    ``kegg2genesymbol.list`` rows read ``mmu:212427<TAB>CDS<TAB>locus<TAB>Sym1,
    Sym2; description``: the symbol is the first token before ``;``. Rows without
    a ``;`` carry only a description and are skipped. ``NCBI2Reactome.txt``'s
    third column is a Reactome ENTITY name (``phospho-Cd247``), used only as a
    fallback for ids KEGG has no symbol for.
    """
    k2sym = {}
    if os.path.isfile(kegg_symbol_path):
        with open(kegg_symbol_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4 or ";" not in parts[3]:
                    continue
                kegg = parts[0].split(":", 1)[-1]
                symbol = parts[3].split(";")[0].split(",")[0].strip()
                if kegg.isdigit() and symbol and " " not in symbol:
                    k2sym.setdefault(kegg, symbol)
    if os.path.isfile(ncbi_reactome_path):
        with open(ncbi_reactome_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[0].isdigit():
                    k2sym.setdefault(parts[0], parts[2])
    return k2sym


def _read_pathway_names(org_dir):
    names = {}
    path = os.path.join(org_dir, "pathways.list")
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    names[parts[0].split(":", 1)[-1]] = re.sub(
                        r"\s*-\s*[A-Z][a-z]+ [a-z]+ \(.*\)$", "", parts[1])
    path = os.path.join(org_dir, "ReactomePathway.txt")
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2 and parts[0].startswith("R-"):
                    names[parts[0]] = parts[1]
    return names


def sign_of_subtype(subtype):
    """KGML subtypes are comma-joined in document order; the first signed one wins."""
    for name in (subtype or "").split(","):
        if name in SIGN_BY_SUBTYPE:
            return SIGN_BY_SUBTYPE[name]
    return 0


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
def _add_kegg(net, org_dir, k2sym):
    edges, types, read = parse_directory(os.path.join(org_dir, "kgml"))
    if not read:
        return 0
    for edge in edges:
        ends = []
        for raw in (edge.a, edge.b):
            kind = types.get(raw)
            if kind == "gene":
                node_id = "g:" + raw
                net.add_node(node_id, k2sym.get(raw, raw), "gene")
            elif kind == "compound":
                node_id = "c:" + raw
                net.add_node(node_id, raw, "compound")
            else:                      # map links and unknown entries
                node_id = None
            ends.append(node_id)
        if ends[0] and ends[1]:
            net.add_edge(ends[0], ends[1], sign_of_subtype(edge.subtype),
                         "KEGG:" + edge.pathway, edge.subtype)
    return read


def _add_reactome(net, org_dir, u2k, k2sym):
    files = sorted(glob.glob(os.path.join(org_dir, "reactome", "*.graph.json")))
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                graph = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("[walker] unreadable Reactome graph %s: %s", path, exc)
            continue
        pathway = graph.get("stId") or os.path.basename(path).split(".")[0]
        by_db_id = {}
        for node in graph.get("nodes") or []:
            kegg = u2k.get(str(node.get("identifier") or ""))
            if kegg:
                by_db_id[node.get("dbId")] = "g:" + kegg
                net.add_node("g:" + kegg, k2sym.get(kegg, kegg), "gene")
        for reaction in graph.get("edges") or []:
            sources = [by_db_id.get(x) for x in
                       (reaction.get("inputs") or []) + (reaction.get("catalysts") or [])]
            targets = [by_db_id.get(x) for x in reaction.get("outputs") or []]
            for a in filter(None, sources):
                for b in filter(None, targets):
                    net.add_edge(a, b, 1, "Reactome:" + pathway, "reaction")
    return len(files)


def _add_omnipath(net, mongo_db, u2k, k2sym):
    if mongo_db is None:
        return 0
    try:
        docs = list(mongo_db["omnipath_network"].find({}, {"_id": 0, "ID": 1, "edges": 1}))
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("[walker] OmniPath collection unavailable: %s", exc)
        return 0
    for doc in docs:
        pathway = str(doc.get("ID") or "")
        net.pathway_names.setdefault(pathway, pathway[4:] if pathway.startswith("opne") else pathway)
        for row in doc.get("edges") or []:
            if len(row) < 3:
                continue
            ka, kb, kind = u2k.get(row[0]), u2k.get(row[1]), str(row[2])
            if not ka or not kb:
                continue
            for kegg in (ka, kb):
                net.add_node("g:" + kegg, k2sym.get(kegg, kegg), "gene")
            sign = 1 if kind == "stimulation" else (-1 if kind == "inhibition" else 0)
            net.add_edge("g:" + ka, "g:" + kb, sign, "OmniPath:" + pathway, kind)
    return len(docs)


# ---------------------------------------------------------------------------
# build + cache
# ---------------------------------------------------------------------------
def build_network(organism, data_dir, mongo_db=None):
    """Read every source present for the organism into one Network."""
    org_dir = os.path.join(data_dir, "current", organism)
    net = Network(organism)
    net.pathway_names = _read_pathway_names(org_dir)
    net.uniprot_to_kegg = _read_uniprot_map(os.path.join(org_dir, "mapping", "uniprot2kegg.list"))
    k2sym = _read_symbol_map(os.path.join(org_dir, "mapping", "kegg2genesymbol.list"),
                             os.path.join(org_dir, "mapping", "reactome", "NCBI2Reactome.txt"))
    net.symbol_to_kegg = {sym.upper(): kegg for kegg, sym in k2sym.items()}
    net.sources = {
        "kegg": _add_kegg(net, org_dir, k2sym),
        "reactome": _add_reactome(net, org_dir, net.uniprot_to_kegg, k2sym),
        "omnipath": _add_omnipath(net, mongo_db, net.uniprot_to_kegg, k2sym),
    }
    logger.info("[walker] network %s: %d nodes, %d edges from %s", organism,
                len(net.nodes), len(net.edges), net.sources)
    return net


def _signature(org_dir):
    """Names and sizes of the source files, hashed: a changed install rebuilds."""
    digest = hashlib.sha1()
    for pattern in ("kgml/*.kgml", "reactome/*.graph.json", "mapping/uniprot2kegg.list"):
        for path in sorted(glob.glob(os.path.join(org_dir, pattern))):
            try:
                digest.update(("%s:%d" % (os.path.basename(path), os.path.getsize(path))).encode())
            except OSError:
                continue
    return digest.hexdigest()[:16]


def load_or_build(organism, data_dir, mongo_db=None, cache_dir=None):
    """The cached network when its signature matches the install, else a rebuild.

    The file signature cannot see Mongo, so a graph built without the OmniPath
    collection is never served to a caller that has it: the cache records how
    many OmniPath pathways it holds, a caller with Mongo rejects a cache with
    none, and a build that was asked for OmniPath and got nothing is not cached.
    """
    org_dir = os.path.join(data_dir, "current", organism)
    cache_dir = cache_dir or org_dir
    signature = _signature(org_dir)
    cache_path = os.path.join(cache_dir, "universal_network.v%d.json.gz" % CACHE_VERSION)
    try:
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        cached_omnipath = (data.get("sources") or {}).get("omnipath", 0)
        if data.get("signature") == signature and (mongo_db is None or cached_omnipath > 0):
            return Network.from_dict(data)
    except (OSError, ValueError, KeyError):
        pass
    net = build_network(organism, data_dir, mongo_db)
    if mongo_db is not None and not net.sources.get("omnipath"):
        logger.warning("[walker] OmniPath was requested but read nothing; not caching this build")
        return net
    try:
        os.makedirs(cache_dir, exist_ok=True)
        data = net.to_dict()
        data["signature"] = signature
        with gzip.open(cache_path, "wt", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError as exc:
        logger.warning("[walker] could not cache the network at %s: %s", cache_path, exc)
    return net
