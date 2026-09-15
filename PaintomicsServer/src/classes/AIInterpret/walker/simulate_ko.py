"""Simulate a gene knockout on the universal network and write it as an
example dataset the walker can be evaluated against.

The model is deliberately small enough to state in one paragraph. The
knocked-out gene is set to EFFECT log2 (KO over WT) at every time point. The
effect then propagates along SIGNED edges, in the stored direction only: a gene
reached over an activation/expression edge takes the sign of its source, one
reached over an inhibition/repression/dephosphorylation edge takes the opposite
sign, and unsigned edges (binding, phosphorylation, compound links, ...) are not
followed at all. Every step keeps DECAY of the value, for STEPS steps; a node
reached by several paths keeps the largest-magnitude value. Compound nodes
relay the sign but are never assigned. Every other gene is background.

Why downstream only: a knockout removes a protein, so the genes it regulates
change and the genes regulating IT do not. Following edges against their arrow
would also plant the knocked-out gene's upstream regulators (696 genes from
Pten instead of 160 on the 2026-09 mmu snapshot), which is not what a knockout
does, and the walker's known-target check walks WITH the arrows.

The planted effect develops over the six columns (PROFILE: a quarter at 0 h,
the full effect from 12 h) so the time course shows a gradient, and every gene
adds N(0, NOISE) noise from one seeded ``random.Random``. A gene is relevant
when |value| at the last column exceeds RELEVANT_ABS. Same network, same seed,
byte-identical files.

    cd PaintomicsServer
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.simulate_ko \\
        --data-dir /path/to/KEGG_DATA --organism mmu \\
        --out src/examplefiles/datasets/13-simulated-pten-knockout

Writes ``data/gene_expression_values.tab``, ``data/gene_expression_relevant.tab``
and, under ``expected/``, the planted gene list and the KEGG pathways the
knocked-out gene has edges in with the planted share of each.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

from src.classes.AIInterpret.walker import network as net_mod

EFFECT = -2.5          # log2 KO over WT for the knocked-out gene at every column
DECAY = 0.6            # effect kept per step of propagation
STEPS = 2              # how far the sign propagates along signed edges
NOISE = 0.35           # background s.d., log2
COLUMNS = ["KO/WT_0h", "KO/WT_2h", "KO/WT_6h", "KO/WT_12h", "KO/WT_18h", "KO/WT_24h"]
RELEVANT_ABS = 1.0     # |value at the last column| above this is relevant
# Share of a node's planted effect present at each column: the change develops
# over the first twelve hours and then holds.
PROFILE = (0.25, 0.5, 0.75, 1.0, 1.0, 1.0)

GENE_PREFIX = "g:"
VALUES_NAME = "gene_expression_values.tab"
RELEVANT_NAME = "gene_expression_relevant.tab"
SIGNAL_NAME = "signal_features.txt"
PATHWAYS_NAME = "expected_pathways.txt"


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def read_ensembl_map(path):
    """``<ensembl gene>\\t<entrez>\\t<protein>\\t<transcript>`` -> {entrez: ensembl}.

    The mapping lists one row per transcript, so a gene appears several times;
    the first Ensembl gene id seen for an Entrez id wins. Rows that are short,
    blank in either column, or carry a non-numeric Entrez id are skipped.
    """
    out = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            ensembl, entrez = parts[0].strip(), parts[1].strip()
            if ensembl and entrez.isdigit():
                out.setdefault(entrez, ensembl)
    return out


def resolve_gene(network, gene_symbol):
    """The network node for a gene symbol, through the install's symbol map."""
    entrez = network.symbol_to_kegg.get(str(gene_symbol).upper())
    if not entrez:
        raise ValueError("no KEGG gene for symbol %r in organism %s"
                         % (gene_symbol, network.organism))
    node = GENE_PREFIX + entrez
    if node not in network.nodes:
        raise ValueError("%s (%s) has no edge in any annotation of %s, nothing to propagate"
                         % (gene_symbol, node, network.organism))
    return node


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------
def propagate(network, source_node, effect=EFFECT, decay=DECAY, steps=STEPS,
              downstream=True):
    """{node_id: effect} for the knocked-out node and every gene within
    ``steps`` signed edges of it.

    An activation edge (sign +1) copies the sign, an inhibition edge (-1) flips
    it, an unsigned edge (0) is not followed. With ``downstream`` (the default)
    only edges walked WITH their stored arrow are followed. The effect shrinks
    by ``decay`` per step; a node reached by several paths keeps the
    largest-magnitude value (ties keep the first in sorted order). Compound
    nodes relay the value to the next step but are never assigned. Frontier and
    neighbours are iterated sorted, so the result is deterministic.
    """
    if source_node not in network.nodes:
        raise KeyError("%r is not a node of the %s network" % (source_node, network.organism))
    carried = {source_node: float(effect)}        # every reached node, compounds included
    effects = {source_node: float(effect)}        # gene nodes only
    frontier = [source_node]
    for _ in range(int(steps)):
        reached = {}
        for node in sorted(frontier):
            value = carried[node]
            for neighbour in sorted(network.neighbours(node)):
                if neighbour == source_node:
                    continue
                edge, direction = network.edge_between(node, neighbour)
                if edge is None or not edge["sign"]:
                    continue
                if downstream and direction != "with":
                    continue
                candidate = value * edge["sign"] * decay
                best = reached.get(neighbour)
                if best is None or abs(candidate) > abs(best):
                    reached[neighbour] = candidate
        frontier = []
        for neighbour, candidate in reached.items():
            # Magnitudes only shrink with depth, so an earlier value is kept
            # unless this path is genuinely stronger.
            if neighbour in carried and abs(carried[neighbour]) >= abs(candidate):
                continue
            carried[neighbour] = candidate
            frontier.append(neighbour)
            if neighbour.startswith(GENE_PREFIX):
                effects[neighbour] = candidate
    return effects


def _clean(value):
    """Six decimals, and never ``-0.0`` (which prints as ``-0.000000``)."""
    return round(value, 6) or 0.0


def simulate(network, ensembl_by_entrez, gene_symbol="Pten", seed=0):
    """(rows, relevant) for every network gene node with an Ensembl id.

    ``rows`` = ``[[ensembl_id, v0..v5], ...]`` sorted by Ensembl id: the node's
    planted effect (0 for background) scaled by PROFILE at each column, plus
    N(0, NOISE) noise from ``random.Random(seed)``, rounded to six decimals.
    ``relevant`` = the Ensembl ids with |value| at the last column above
    RELEVANT_ABS. Two Entrez ids mapping to one Ensembl gene give one row,
    carrying the larger-magnitude planted effect.
    """
    node = resolve_gene(network, gene_symbol)
    effects = propagate(network, node)
    by_ensembl = {}
    for node_id, info in network.nodes.items():
        if info.get("kind") != "gene":
            continue
        ensembl = ensembl_by_entrez.get(node_id[len(GENE_PREFIX):])
        if not ensembl:
            continue
        value = effects.get(node_id, 0.0)
        if ensembl not in by_ensembl or abs(value) > abs(by_ensembl[ensembl]):
            by_ensembl[ensembl] = value
    rng = random.Random(seed)
    rows, relevant = [], []
    for ensembl in sorted(by_ensembl):            # sorted BEFORE drawing: same seed, same file
        planted = by_ensembl[ensembl]
        values = [_clean(planted * share + rng.gauss(0.0, NOISE)) for share in PROFILE]
        rows.append([ensembl] + values)
        if abs(values[-1]) > RELEVANT_ABS:
            relevant.append(ensembl)
    return rows, relevant


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------
def write_dataset(out_dir, rows, relevant):
    """``data/gene_expression_values.tab`` (header ``#geneID`` + COLUMNS) and
    ``data/gene_expression_relevant.tab`` (one id per line), LF line ends."""
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, VALUES_NAME), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#geneID\t" + "\t".join(COLUMNS) + "\n")
        for row in rows:
            handle.write(row[0] + "\t" + "\t".join("%.6f" % value for value in row[1:]) + "\n")
    with open(os.path.join(data_dir, RELEVANT_NAME), "w", encoding="utf-8", newline="\n") as handle:
        for ensembl in relevant:
            handle.write(ensembl + "\n")


def write_expected(out_dir, network, source_node, effects, ensembl_by_entrez):
    """The ground truth beside the data, in the layout the other simulated
    scenarios use: ``expected/signal_features.txt`` (the planted genes that are
    in the values file) and ``expected/expected_pathways.txt`` (the KEGG pathways
    the knocked-out gene has edges in, with the planted share of each pathway's
    network genes). Returns ``{"signal": n, "pathways": [[id, planted, members], ...]}``.

    The pathways are context, not enrichment targets: a knockout propagated two
    steps marks a neighbourhood, and on mmu that is 4-29% of any Pten pathway.
    """
    expected_dir = os.path.join(out_dir, "expected")
    os.makedirs(expected_dir, exist_ok=True)
    label = network.nodes[source_node]["label"]

    def ensembl_of(node_id):
        return ensembl_by_entrez.get(node_id[len(GENE_PREFIX):])

    signal = sorted({ensembl_of(node_id) for node_id in effects if ensembl_of(node_id)})
    with open(os.path.join(expected_dir, SIGNAL_NAME), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("# Genes given a planted effect: %s (knocked out, %s log2) and every gene\n"
                     "# within %d signed downstream steps of it (x%s per step).\n"
                     "# Generated by src/classes/AIInterpret/walker/simulate_ko.py; do not edit by hand.\n"
                     % (label, EFFECT, STEPS, DECAY))
        for ensembl in signal:
            handle.write(ensembl + "\n")

    members_by_tag = {}
    tags = {tag for tag in network.nodes[source_node]["pathways"] if tag.startswith("KEGG:")}
    for node_id, info in network.nodes.items():
        if info.get("kind") != "gene" or not ensembl_of(node_id):
            continue
        for tag in info.get("pathways", []):
            if tag in tags:
                members_by_tag.setdefault(tag, set()).add(node_id)
    pathways = []
    for tag in sorted(tags):
        members = members_by_tag.get(tag, set())
        planted = sum(1 for node_id in members if node_id in effects)
        pathways.append([tag.split(":", 1)[1], planted, len(members)])
    with open(os.path.join(expected_dir, PATHWAYS_NAME), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("# KEGG pathways %s has an edge in, with the planted share of each pathway's\n"
                     "# network genes. Context for the walk, NOT enrichment targets: the planted\n"
                     "# signal is %s's downstream neighbourhood, not any one pathway.\n"
                     "# Generated by src/classes/AIInterpret/walker/simulate_ko.py; do not edit by hand.\n"
                     % (label, label))
        for pathway, planted, members in pathways:
            handle.write("%s\t%s\t%d/%d planted\n"
                         % (pathway, network.pathway_name("KEGG:" + pathway), planted, members))
    return {"signal": len(signal), "pathways": pathways}


def build(data_dir, organism, out_dir, gene_symbol="Pten", seed=0):
    """Load the network (no Mongo), read the Ensembl map, simulate the knockout
    and write the dataset. Returns the counts a caller reports."""
    network = net_mod.load_or_build(organism, data_dir, mongo_db=None)
    mapping = os.path.join(data_dir, "current", organism, "mapping", "ensembl_mapping.list")
    ensembl_by_entrez = read_ensembl_map(mapping)
    if not ensembl_by_entrez:
        raise ValueError("no usable rows in %s" % mapping)
    node = resolve_gene(network, gene_symbol)
    rows, relevant = simulate(network, ensembl_by_entrez, gene_symbol, seed)
    write_dataset(out_dir, rows, relevant)
    effects = propagate(network, node)
    expected = write_expected(out_dir, network, node, effects, ensembl_by_entrez)
    return {"gene": network.nodes[node]["label"], "node": node, "genes": len(rows),
            "relevant": len(relevant), "planted": len(effects),
            "signal": expected["signal"], "pathways": expected["pathways"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="KEGG_DATA directory")
    parser.add_argument("--organism", default="mmu")
    parser.add_argument("--out", required=True, help="dataset directory to write")
    parser.add_argument("--gene", default="Pten", help="symbol of the gene to knock out")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    result = build(args.data_dir, args.organism, args.out, args.gene, args.seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
