"""Check 5: the walk is anchored to the perturbation.

The design card names the gene the experiment perturbed and which way. Here
that name becomes a node: resolved through the organism's symbol and alias
table, given its known transcriptional targets as edges for this run when the
organism ships a TF -> target table (``mapping/tf_targets.tsv``, written by
``omnipathInstaller.py --tf-targets``), and made the origin of a distance every
other node is labelled with. The planner is told where the anchor is; every
module of the finished walk carries how far from it the walk went; and two
numbers say whether the walk stayed near the perturbation: the share of walked
nodes within two steps of the anchor against the share of all measured nodes
that are, and, when a list of known targets is given, how many the walk hit.
"""
from __future__ import annotations

import csv
import os
import re
from collections import deque

from scipy.stats import hypergeom

TF_TARGETS_FILE = os.path.join("mapping", "tf_targets.tsv")
ANCHOR_SUBTYPE = "transcriptional regulation"
REACH_RADIUS = 2

# The words that say what was done to the gene, and which way.
UP_RE = re.compile(r"\b(?:induc(?:e|ed|es|ing|tion|ible)|over-?express(?:ed|ion|ing|es)?|activat(?:e|ed|es|ion|ing)|"
                   r"agonist|stimulat(?:e|ed|es|ion|ing)|gain[- ]of[- ]function|transgen(?:e|ic)|constitutive(?:ly)?)\b", re.I)
DOWN_RE = re.compile(r"\b(?:knock-?outs?|KO|knock-?downs?|KD|delet(?:e|ed|es|ion|ing)|null|loss[- ]of[- ]function|"
                     r"loss of|ablat(?:e|ed|ion)|silenc(?:e|ed|ing)|inhibit(?:or|ors|ion|ed|ing)? of|antagonist|"
                     r"siRNA|shRNA|CRISPR|-/-|depletion|depleted)\b", re.I)
_VERB_RE = re.compile("%s|%s" % (UP_RE.pattern, DOWN_RE.pattern), re.I)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,15}")
_WINDOW = 80
# Ordinary words that are also gene aliases somewhere in the table.
_STOP = {"CELL", "CELLS", "MOUSE", "MICE", "HUMAN", "TIME", "COURSE", "CONTROL", "OVER", "WITH", "AND", "THE",
         "FOLD", "LOG", "LOG2", "RATIO", "GENE", "GENES", "TREATED", "TREATMENT", "AFTER", "BEFORE", "HOURS",
         "DAYS", "MIN", "SAMPLE", "SAMPLES", "DATA", "SEQ", "RNA", "DNA", "PROTEIN", "WILD", "TYPE", "LINE"}


def alias_map(org_dir):
    """{ALIAS_UPPER: symbol} from ``mapping/kegg2genesymbol.list``: every name
    before the ``;`` maps to the first, the organism's symbol. Empty when the
    file is missing."""
    out = {}
    path = os.path.join(org_dir, "mapping", "kegg2genesymbol.list")
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or ";" not in parts[3]:
                continue
            names = [n.strip() for n in parts[3].split(";")[0].split(",") if n.strip()]
            if not names or " " in names[0]:
                continue
            for name in names:
                out.setdefault(name.upper(), names[0])
    return out


def find_perturbed_genes(text, aliases):
    """(symbols, direction) read from a design text with no model: the gene
    names that stand within _WINDOW characters of a perturbation word, in
    reading order, and "up" | "down" | "unknown" from the word nearest the
    first gene (the only direction word in the text when no gene is found)."""
    text = str(text or "")
    verbs = [(m.start(), "up" if UP_RE.match(m.group(0)) else "down") for m in _VERB_RE.finditer(text)]
    genes, direction = [], "unknown"
    if not aliases:
        return genes, _lone_direction(verbs)
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        upper = token.upper()
        if upper in _STOP or upper not in aliases:
            continue
        if not any(abs(pos - match.start()) <= _WINDOW for pos, _d in verbs):
            continue
        symbol = aliases[upper]
        if symbol not in genes:
            genes.append(symbol)
        if direction == "unknown" and verbs:
            nearest = min(verbs, key=lambda v: abs(v[0] - match.start()))
            direction = nearest[1]
    if not genes:
        direction = _lone_direction(verbs)
    return genes, direction


def _lone_direction(verbs):
    kinds = {d for _pos, d in verbs}
    return kinds.pop() if len(kinds) == 1 else "unknown"


def load_tf_targets(org_dir):
    """The rows of ``mapping/tf_targets.tsv``: {tf, target, sign, source,
    references}; [] when the organism has no table."""
    path = os.path.join(org_dir, TF_TARGETS_FILE)
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            try:
                sign = int(float(row.get("sign") or 0))
            except (TypeError, ValueError):
                sign = 0
            if row.get("tf") and row.get("target"):
                rows.append({"tf": row["tf"].strip(), "target": row["target"].strip(), "sign": sign,
                             "source": (row.get("source") or "").strip(),
                             "references": (row.get("references") or "").strip()})
    return rows


def _node_for(symbol, network, aliases):
    kegg = network.symbol_to_kegg.get(str(symbol).upper())
    if not kegg and aliases:
        canonical = aliases.get(str(symbol).upper())
        kegg = network.symbol_to_kegg.get(canonical.upper()) if canonical else None
    return ("g:" + kegg) if kegg else None


def resolve_anchor(card, network, org_dir, aliases=None):
    """The anchor of a run, or None: the first perturbed gene of the card that
    the organism's symbol table knows. ``in_graph`` says whether the node has
    edges in ``network`` (add_anchor_edges may give it some)."""
    genes = card.get("perturbed_genes") if isinstance(card, dict) else None
    if isinstance(genes, str):
        genes = [g.strip() for g in re.split(r"[,;]", genes) if g.strip()]
    if not genes:
        return None
    aliases = aliases if aliases is not None else alias_map(org_dir)
    for gene in genes:
        node = _node_for(gene, network, aliases)
        if node is None:
            continue
        symbol = aliases.get(str(gene).upper(), str(gene))
        return {"gene": symbol, "node": node, "direction": str(card.get("perturbation_direction") or "unknown"),
                "in_graph": node in network.nodes and bool(network.neighbours(node)), "source": ""}
    return None


def add_anchor_edges(graph, network, anchor, rows, aliases=None):
    """Give the anchor its known transcriptional targets as tier-1 edges,
    tagged ``anchor:<source>``, for this run only: on the walked ``graph`` for
    the targets it holds, and on the universal ``network`` (for distances) for
    every target it holds. Returns how many edges the walked graph gained and
    records the sources on the anchor."""
    node = anchor["node"]
    added, sources = 0, []
    label = anchor["gene"]
    for row in rows:
        if row["tf"].upper() != anchor["gene"].upper():
            continue
        target = _node_for(row["target"], network, aliases)
        if target is None or target == node:
            continue
        tag = "anchor:" + (row["source"] or "TF")
        for net in dict.fromkeys((network, graph)):
            if target not in net.nodes:
                continue
            if node not in net.nodes:
                net.add_node(node, label, "gene")
            before = len(net.edges)
            net.add_edge(node, target, row["sign"], tag, ANCHOR_SUBTYPE)
            if net is graph and len(net.edges) > before:
                added += 1
        if row["source"] and row["source"] not in sources:
            sources.append(row["source"])
    anchor["source"] = ",".join(sources)
    anchor["in_graph"] = node in network.nodes and bool(network.neighbours(node))
    anchor["edges_added"] = added
    return added


def distances(network, anchor_node):
    """{node: steps from the anchor} over the whole network, hubs and
    currency compounds passable (this is a label, not a walk)."""
    if anchor_node not in network.nodes:
        return {}
    dist = {anchor_node: 0}
    queue = deque([anchor_node])
    while queue:
        u = queue.popleft()
        for w in network.neighbours(u):
            if w not in dist:
                dist[w] = dist[u] + 1
                queue.append(w)
    return dist


def label_segments(walker, dist):
    """Give every recorded module its distance from the anchor: the least
    distance of the nodes its legs touch (None when none is reachable)."""
    chain = walker.chain
    for segment in walker.segments:
        nodes = set()
        for leg in chain:
            if segment["first"] <= leg.n <= segment["last"]:
                nodes.update((leg.src, leg.dst))
        reached = [dist[v] for v in nodes if v in dist]
        segment["distance"] = min(reached) if reached else None
    return walker.segments


def walked_nodes(walker):
    """Every node the walk touched: the ends of its legs (a sealed record's
    chain works too, its legs being dicts with "from" and "to")."""
    nodes = set()
    for leg in walker.chain if hasattr(walker, "chain") else walker:
        if isinstance(leg, dict):
            nodes.update((leg["from"], leg["to"]))
        else:
            nodes.update((leg.src, leg.dst))
    return nodes


def reachability(nodes, dist, radius=REACH_RADIUS):
    """The share of walked ``nodes`` within ``radius`` steps of the anchor."""
    if not nodes:
        return 0.0
    return sum(1 for v in nodes if dist.get(v) is not None and dist[v] <= radius) / float(len(nodes))


def base_rate(overlay, dist, radius=REACH_RADIUS):
    """The share of all measured nodes within ``radius`` steps of the anchor:
    what a walk indifferent to the anchor would score."""
    if not overlay.measured:
        return 0.0
    return sum(1 for v in overlay.measured if dist.get(v) is not None and dist[v] <= radius) / float(len(overlay.measured))


def target_enrichment(nodes, targets, overlay, network, aliases=None):
    """Known targets among the walked, measured genes: {walked, targets, hits,
    p} with the hypergeometric tail over the measured gene nodes."""
    universe = {v for v in overlay.measured if str(v).startswith("g:")}
    target_nodes = set()
    for symbol in targets or []:
        node = _node_for(symbol, network, aliases)
        if node in universe:
            target_nodes.add(node)
    walked = set(nodes) & universe
    hits = len(walked & target_nodes)
    p = 1.0
    if walked and target_nodes:
        p = float(hypergeom.sf(hits - 1, len(universe), len(target_nodes), len(walked)))
    return {"walked": len(walked), "targets": len(target_nodes), "hits": hits, "p": round(p, 5)}


def neighbourhood(network, node, radius=REACH_RADIUS, extra=()):
    """The nodes within ``radius`` steps of ``node`` in the network, plus
    ``extra`` (a regulon the network does not draw yet) and their neighbours."""
    seen = {node} | set(extra)
    frontier = set(seen)
    for _ in range(radius):
        frontier = {w for u in frontier for w in network.neighbours(u)} - seen
        seen |= frontier
    return seen


def decoy_for(anchor, rows, network, rng, aliases=None, tolerance=0.3, pool_size=40):
    """The harness's decoy: a gene of the anchor's kind and size whose
    neighbourhood shares the least with the anchor's. A transcription factor
    anchor gets a factor with a regulon (targets the network holds) within
    ``tolerance`` of its own; any other gene gets a gene of similar degree.
    Among the ``pool_size`` closest in size, the one whose two-step
    neighbourhood overlaps the anchor's least wins; ``rng`` breaks ties. A
    decoy that shares the anchor's neighbourhood is no decoy: the first one
    tried on the example walked 57-64 percent of its nodes within two steps
    of the true anchor."""
    node = anchor["node"]
    regulons = {}
    for row in rows:
        target = _node_for(row["target"], network, aliases)
        if target is not None and target in network.nodes:
            regulons.setdefault(row["tf"].upper(), set()).add(target)
    mine = regulons.get(anchor["gene"].upper())
    if mine:
        pool = {_node_for(tf, network, aliases): (tf, targets) for tf, targets in regulons.items()
                if tf != anchor["gene"].upper() and abs(len(targets) - len(mine)) <= tolerance * len(mine)}
        pool.pop(None, None)
        size_of = {cand: len(pool[cand][1]) for cand in pool}
        extra_of = {cand: pool[cand][1] for cand in pool}
        target_size = len(mine)
    else:
        degree = {v: len(network.neighbours(v)) for v in network.nodes if str(v).startswith("g:")}
        target_size = degree.get(node, 0)
        if not target_size:
            return None
        size_of = {v: d for v, d in degree.items() if v != node and abs(d - target_size) <= tolerance * target_size}
        extra_of = {v: () for v in size_of}
    if not size_of:
        return None
    anchor_hood = neighbourhood(network, node, extra=mine or ())
    candidates = sorted(size_of, key=lambda v: (abs(size_of[v] - target_size), v))[:pool_size]
    scored = []
    for cand in candidates:
        hood = neighbourhood(network, cand, extra=extra_of[cand])
        overlap = len(hood & anchor_hood) / float(len(hood) or 1)
        scored.append((round(overlap, 3), cand))
    least = min(score for score, _cand in scored)
    choice = rng.choice(sorted(cand for score, cand in scored if score == least))
    label = str(network.nodes[choice].get("label") or choice)
    if mine:
        tf = pool[choice][0]
        return aliases.get(tf, tf.capitalize()) if aliases else tf.capitalize()
    return (aliases or {}).get(label.upper(), label)


def anchor_gate(anchor, walker, overlay, dist, targets=None, network=None, aliases=None):
    """The gate dict. Not applicable (and passing) when the design names no
    gene; failing when the anchor is not in the network or the walk stayed no
    closer to it than the measured nodes at large."""
    if anchor is None:
        return {"pass": True, "not_applicable": True, "why": "the design names no perturbed gene"}
    gate = {"pass": False, "not_applicable": False, "gene": anchor["gene"], "direction": anchor.get("direction"),
            "in_graph": bool(anchor.get("in_graph")), "reachability": None, "base_rate": None, "why": ""}
    if not anchor.get("in_graph"):
        gate["why"] = ("%s is not connected in this organism's network, so the walk could not start from the "
                       "perturbation" % anchor["gene"])
        return gate
    nodes = walked_nodes(walker)
    gate["reachability"] = round(reachability(nodes, dist), 3)
    gate["base_rate"] = round(base_rate(overlay, dist), 3)
    if targets and network is not None:
        gate["targets"] = target_enrichment(nodes, targets, overlay, network, aliases)
    gate["pass"] = gate["reachability"] > gate["base_rate"]
    if not gate["pass"]:
        gate["why"] = ("%.0f%% of the walked nodes lie within %d steps of %s, no more than the %.0f%% of all "
                       "measured nodes that do: the walk did not follow the perturbation"
                       % (100 * gate["reachability"], REACH_RADIUS, anchor["gene"], 100 * gate["base_rate"]))
    return gate
