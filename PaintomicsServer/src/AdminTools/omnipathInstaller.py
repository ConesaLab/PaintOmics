#!/usr/bin/env python3
# ***************************************************************
#  This file is part of Paintomics v4
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
# **************************************************************
"""Install OmniPath as a pathway database source for one organism.

Why this installer looks different from the KEGG/Reactome/MapMan ones
--------------------------------------------------------------------
Every other source Paintomics paints ships a *diagram*: an image plus a
coordinate for each feature drawn on it. OmniPath ships none. It is a
prior-knowledge network -- signed, directed molecular interactions -- with no
canvas and no node positions anywhere in the resource.

So an OmniPath pathway is stored here with its genes carrying **no
coordinates**, exactly the shape KEGG already produces for the ~29% of its
genes that belong to a pathway without being drawn on its map. The pathway
renders as an interactive network in the client instead of as a painted raster,
which is also the only honest rendering: a force-directed layout is an artifact
of the solver, not a statement about biology, so it is presented as a graph the
user can move rather than as a map whose geometry means something.

What a "pathway" is here
------------------------
OmniPath keeps pathway *membership* and the interaction *network* in two
separate datasets. This installer combines them:

  * membership comes from the ``annotations`` endpoint, restricted to the
    curated causal resources named in ``PARTITIONS``;
  * edges come from the ``interactions`` endpoint for the target organism;
  * a pathway is the subnetwork the members induce on that graph.

Organisms
---------
The web service serves exactly three taxa -- human, mouse and rat -- and
rejects every other with ``Unknown values for argument organisms``. Its
``annotations`` endpoint takes no organism parameter at all: membership is
human. Mouse and rat therefore need the human members carried across, and this
installer does **not** guess that mapping from gene-symbol casing. It recovers
the real one from OmniPath itself: the mouse and rat interaction tables are
orthology translations of the human table, so a translated row keeps its
source literature, and aligning the two tables on that provenance recovers the
ortholog pairs OmniPath actually used. See ``derive_ortholog_map``.

Transcription-factor targets
----------------------------
A second, independent product of this installer is a flat file of signed
TF -> target edges, ``current/<code>/mapping/tf_targets.tsv``. It is *not* a
pathway and is not written to MongoDB: it is a regulatory prior keyed on the
organism's own gene symbols, so a downstream reader can ask "which
transcription factors are reported to regulate this gene" without a network
layout or an identifier conversion. Three sources are merged, each row keeping
the name of the one it came from:

  * ``CollecTRI`` -- OmniPath's ``collectri`` dataset for the organism itself;
  * ``CollecTRI-human`` -- the human CollecTRI table carried across to a
    non-human organism by gene-symbol identity, resolved through the KEGG
    ``kegg2genesymbol.list`` alias table (so ``IKAROS`` finds ``Ikzf1``). A
    symbol that the organism's table does not know is dropped, not guessed;
  * ``TRRUST`` -- the raw TRRUST v2 table, published for human and mouse only.

The sources deliberately stay separate rather than being collapsed into one
consensus edge: a row is deduplicated on ``(tf, target, source)`` and a reader
that wants "confirmed by two resources" can count sources per pair itself.
``--tf-targets`` writes only this file; a normal install writes it last and
treats a failure there as a warning, because the pathway install is complete
without it.

Usage
-----
    python src/AdminTools/omnipathInstaller.py --organism mmu
    python src/AdminTools/omnipathInstaller.py --organism hsa --dry-run
    python src/AdminTools/omnipathInstaller.py --organism mmu --tf-targets
"""

import argparse
import collections
import csv
import hashlib
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The only organisms omnipathdb.org serves. Anything else is rejected by the
#: service itself, so it is rejected here rather than downloaded and found
#: empty. Verified against the live service: 7955/7227/6239/4932/3702 all
#: return "Unknown values for argument `organisms`".
SUPPORTED_ORGANISMS = {"hsa": 9606, "mmu": 10090, "rno": 10116}

#: Human is OmniPath's native organism; membership needs no translation.
HUMAN_TAXID = 9606

#: Curated pathway partitions used as the pathway definition. SIGNOR supplies
#: focused causal modules (median ~18 genes), NetPath broader receptor cascades
#: (median ~92). Their pathway names do not collide.
PARTITIONS = ("SIGNOR", "NetPath")

#: Interaction datasets pulled for the network. ``omnipath`` is the core
#: literature-curated set; the two "extra" sets add directed and kinase-substrate
#: edges without pulling in the transcriptional and miRNA layers, which are a
#: different kind of statement and would swamp the graph.
DATASETS = "omnipath,pathwayextra,kinaseextra"

#: A pathway with fewer members than this induces a subnetwork too small to
#: read and too small to enrich against.
MIN_PATHWAY_GENES = 5

BASE_URL = "https://omnipathdb.org"
SOURCE_NAME = "OmniPath"

#: Collection holding one document per pathway, shared with every other source.
PATHWAY_COLLECTION = "kegg"
#: Edges live apart from the pathway documents on purpose. The whole edge set is
#: ~1.3 MB per organism and KeggInformationManager caches pathway documents for
#: up to 25 organisms at once; folding the network into them would multiply that
#: cache by an order of magnitude to serve data only needed when a single
#: pathway is actually opened.
NETWORK_COLLECTION = "omnipath_network"

#: The identifier OmniPath speaks. Must match the xref database named for
#: OmniPath in conf/organismDB.py, or matched features resolve to nothing.
FEATURE_DB = "uniprot_acc"

#: Gene box geometry, in the same pixel space Reactome's diagrams use, so the
#: Step 4 viewer scales OmniPath pathways with the arithmetic it already has.
NODE_WIDTH = 92
NODE_HEIGHT = 22
CANVAS_MARGIN = 40
MIN_CANVAS = 640

_HTTP_TIMEOUT = 300
_HTTP_RETRIES = 3

#: OmniPath dataset holding the CollecTRI transcriptional regulon collection.
TF_DATASET = "collectri"

#: TRRUST v2 raw tables, by organism code. The resource is published for exactly
#: these two organisms; every other code gets no TRRUST rows rather than a 404.
TRRUST_URLS = {
    "hsa": "https://www.grnpedia.org/trrust/data/trrust_rawdata.human.tsv",
    "mmu": "https://www.grnpedia.org/trrust/data/trrust_rawdata.mouse.tsv",
}
#: grnpedia.org is a small academic host: a 220 kB table can take tens of
#: seconds, but a five-minute wait would only hide an outage.
_TRRUST_TIMEOUT = 120

#: Column names of the TF-target file, in order.
TF_TARGET_COLUMNS = ("tf", "target", "sign", "source", "references")
TF_TARGET_FILENAME = "tf_targets.tsv"

#: The three-way causal sign as the TF-target file stores it: +1 activation,
#: -1 repression, 0 unknown or contradictory.
_SIGN_CODES = {"stimulation": 1, "inhibition": -1, "unsigned": 0}
_TRRUST_SIGNS = {"Activation": 1, "Repression": -1}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("omnipath")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _fetch(path, params):
    """GET one omnipathdb.org endpoint and return its decoded body.

    Validates that the body is actually tab-separated data. A 200 response
    carrying an HTML error page is the failure mode that has previously put a
    web page into this project's MongoDB, so status alone is not trusted.
    """
    url = "%s/%s?%s" % (BASE_URL, path, urllib.parse.urlencode(params))
    body = _get(url)

    stripped = body.lstrip()
    if stripped[:1] == "<":
        raise RuntimeError(
            "%s returned markup, not data (first 120 chars: %r)"
            % (url, stripped[:120]))
    if stripped.startswith("==>"):
        # The service reports bad arguments in the body with a 200 status.
        raise RuntimeError("%s rejected the request: %s" % (url, stripped[:200]))
    if "\t" not in body.split("\n", 1)[0]:
        raise RuntimeError(
            "%s did not return a TSV header (got %r)" % (url, body[:120]))
    return body


def _get(url, timeout=_HTTP_TIMEOUT):
    """GET ``url`` with retries and return its body decoded as UTF-8.

    Transport errors (DNS, refused connection, timeout) are retried with a
    growing pause; anything else propagates. Callers validate the body -- what
    counts as a good answer differs between omnipathdb.org and grnpedia.org.
    """
    last_error = None
    for attempt in range(1, _HTTP_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as error:      # network/DNS/timeout
            last_error = error
            logger.warning("attempt %d/%d failed for %s: %s",
                           attempt, _HTTP_RETRIES, url, error)
            time.sleep(2 * attempt)

    raise RuntimeError("%s unreachable after %d attempts: %s"
                       % (url, _HTTP_RETRIES, last_error))


def _rows(body):
    """Parse a TSV body into dict rows without holding the text twice."""
    return csv.DictReader(io.StringIO(body), delimiter="\t")


def fetch_interactions(taxid):
    """Return interaction rows for one taxid, reduced to the fields used.

    Keeping four strings per row rather than the full 20-column record holds
    the human table (~138k rows) to a few MB instead of tens.
    """
    body = _fetch("interactions", {
        "genesymbols": "1", "format": "tsv", "organisms": str(taxid),
        # `sources` and `curation_effort` ride along on the same request.
        # references was already fetched but only ever used to vote on
        # orthologs; it is now stored, so an interaction shown to a user can
        # cite the papers it came from. curation_effort is the only per-edge
        # ranking key OmniPath offers, and a literature layer needs one --
        # the raw graph puts a median of 211 edges on a KEGG map.
        "fields": "references,sources,curation_effort", "datasets": DATASETS,
    })
    interactions = []
    for row in _rows(body):
        source, target = row.get("source"), row.get("target")
        if not source or not target or source == target:
            continue                                   # self-loops draw nothing
        interactions.append({
            "source": source,
            "target": target,
            "source_symbol": row.get("source_genesymbol") or source,
            "target_symbol": row.get("target_genesymbol") or target,
            "sign": _sign(row),
            "references": row.get("references") or "",
            "sources": row.get("sources") or "",
            "curation_effort": _int_or_zero(row.get("curation_effort")),
        })
    logger.info("taxid %s: %d interactions", taxid, len(interactions))
    return interactions


def _int_or_zero(value):
    """OmniPath renders a missing numeric cell as an empty string or 'nan'."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _sign(row):
    """Collapse OmniPath's two boolean columns into one causal sign.

    The service renders booleans as the strings ``True``/``False``, not 1/0;
    comparing against "1" silently classifies every edge as unsigned.
    """
    stimulation = row.get("is_stimulation") == "True"
    inhibition = row.get("is_inhibition") == "True"
    if stimulation and not inhibition:
        return "stimulation"
    if inhibition and not stimulation:
        return "inhibition"
    return "unsigned"                        # unknown, or curated both ways


def fetch_annotations(resource):
    """Return ``{pathway name: {human uniprot accession, ...}}`` for a resource.

    Over half of SIGNOR's annotation rows describe protein *complexes* rather
    than proteins. Complexes carry no gene-level interaction and no xref, so
    including them makes roughly half of every pathway look unconnected and
    unmatchable. ``entity_type`` is the only thing separating the two.
    """
    body = _fetch("annotations", {"resources": resource, "format": "tsv"})
    pathways = collections.defaultdict(set)
    skipped_non_protein = 0

    for row in _rows(body):
        if row.get("label") != "pathway":
            continue
        if row.get("entity_type") != "protein":
            skipped_non_protein += 1
            continue
        accession, name = row.get("uniprot"), row.get("value")
        if accession and name:
            pathways[name].add(accession)

    logger.info("%s: %d pathways (%d non-protein rows skipped)",
                resource, len(pathways), skipped_non_protein)
    return pathways


# ---------------------------------------------------------------------------
# Orthology
# ---------------------------------------------------------------------------

def derive_ortholog_map(human_interactions, target_interactions):
    """Recover OmniPath's own human -> target ortholog assignment.

    The mouse and rat interaction tables are produced by translating the human
    table, so a translated row carries the same literature references and the
    same direction/sign as the human row it came from. Aligning on that
    provenance yields real ortholog pairs instead of a guess from symbol
    casing, which agrees for only ~65% of symbols and silently mismaps
    paralogs where it does not.

    Rows whose provenance matches more than one human row are dropped rather
    than resolved arbitrarily, as are proteins that end up with more than one
    candidate ortholog.
    """
    by_provenance = collections.defaultdict(list)
    for interaction in human_interactions:
        if interaction["references"]:
            by_provenance[_provenance(interaction)].append(interaction)

    votes = collections.defaultdict(collections.Counter)
    aligned = ambiguous = 0

    for interaction in target_interactions:
        if not interaction["references"]:
            continue
        candidates = by_provenance.get(_provenance(interaction), ())
        if len(candidates) != 1:
            ambiguous += 1 if candidates else 0
            continue
        aligned += 1
        human = candidates[0]
        votes[human["source"]][interaction["source"]] += 1
        votes[human["target"]][interaction["target"]] += 1

    ortholog_map = {
        human: counter.most_common(1)[0][0]
        for human, counter in votes.items()
        if len(counter) == 1                       # exactly one candidate seen
    }
    logger.info("orthology: %d rows aligned (%d ambiguous) -> %d unambiguous "
                "pairs from %d proteins seen",
                aligned, ambiguous, len(ortholog_map), len(votes))
    return ortholog_map


def _provenance(interaction):
    return (interaction["references"], interaction["sign"])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_pathways(interactions, annotations_by_resource, ortholog_map=None):
    """Assemble pathway documents and their induced subnetworks.

    Returns ``(pathway_documents, networks)`` where ``networks`` maps a pathway
    ID to its edge list. Genes carry no coordinates -- OmniPath has none to
    give -- so ``x``/``y``/``width``/``height`` are absent rather than null:
    the job layer restores optional Mongo fields through a serialiser that
    turns a ``None`` leaf into the four-character string ``"None"``, and an
    absent key is unambiguous where a null one is not.
    """
    adjacency = collections.defaultdict(dict)
    symbols = {}
    for interaction in interactions:
        # The stored value is the whole provenance triple, not just the sign.
        # Storing [source, target, sign] alone meant an edge presented to a
        # user as "reported in the literature" could cite nothing and could
        # not be ranked.
        adjacency[interaction["source"]][interaction["target"]] = (
            interaction["sign"],
            interaction.get("references", ""),
            interaction.get("curation_effort", 0),
        )
        symbols[interaction["source"]] = interaction["source_symbol"]
        symbols[interaction["target"]] = interaction["target_symbol"]

    in_network = set(adjacency) | {t for edges in adjacency.values() for t in edges}

    documents, networks = [], {}
    dropped_small = 0

    for resource in sorted(annotations_by_resource):
        for name in sorted(annotations_by_resource[resource]):
            human_members = annotations_by_resource[resource][name]

            if ortholog_map is None:
                members = set(human_members)
            else:
                members = {ortholog_map[a] for a in human_members if a in ortholog_map}
            members &= in_network

            if len(members) < MIN_PATHWAY_GENES:
                dropped_small += 1
                continue

            pathway_id = _pathway_id(resource, name)
            # [source, target, sign, references, curation_effort]. Consumers
            # written against the original three-element form keep working --
            # PA_Step4OmniPathNetworkView reads only indices 0..2.
            edges = [
                [source, target, provenance[0], provenance[1], provenance[2]]
                for source in sorted(members)
                for target, provenance in sorted(adjacency.get(source, {}).items())
                if target in members
            ]

            ordered = sorted(members)
            positions, (canvas_width, canvas_height) = layout(ordered, edges)

            documents.append({
                "ID": pathway_id,
                "name": name,
                "source": SOURCE_NAME,
                "classification": "%s;%s" % (classify(name), resource),
                "featureDB": FEATURE_DB,
                "relatedPathways": [],
                "compounds": [],
                # Coordinates are the box CENTRE, matching what Reactome stores
                # and what the Step 4 viewer expects.
                "genes": [
                    {"id": accession,
                     "name": symbols.get(accession, accession),
                     "x": positions[accession][0] + NODE_WIDTH // 2,
                     "y": positions[accession][1] + NODE_HEIGHT // 2,
                     "width": NODE_WIDTH,
                     "height": NODE_HEIGHT}
                    for accession in ordered
                ],
                # There is no diagram to measure, so the canvas the layout was
                # computed on is stored instead and read back in place of a PNG.
                "imageWidth": canvas_width,
                "imageHeight": canvas_height,
            })
            networks[pathway_id] = {
                "ID": pathway_id,
                "edges": edges,
                "symbols": {a: symbols.get(a, a) for a in ordered},
            }

    logger.info("built %d pathways (%d dropped below %d genes), %d edges total",
                len(documents), dropped_small, MIN_PATHWAY_GENES,
                sum(len(n["edges"]) for n in networks.values()))
    return documents, networks


#: OmniPath publishes no pathway hierarchy: SIGNOR and NetPath are flat lists
#: of names, so a Paintomics install would show 120 pathways under one
#: undifferentiated heading and the Step 3 category filter would do nothing.
#: This is the taxonomy Paintomics assigns them -- derived here, not carried
#: from upstream, which is why it is stated in one readable table rather than
#: hidden in the pathway documents.
#:
#: Ordered, first match wins: a pathway named for a tumour is filed under
#: cancer even though it is also a signalling cascade, and a SARS-CoV module is
#: filed under infection even though it is named for apoptosis or MAPK.
PATHWAY_CATEGORIES = (
    ("Cancer", (
        "leukemia", "carcinoma", "tumor", "tumour", "melanoma", "cancer",
        "glioblastoma", "sarcoma", "nsclc", "pda", "pdac", "_crc", "_hcc",
        "neoplas", "oncogen", "caf signaling")),
    ("Infection and inflammation", (
        "sars-cov", "sars-co", "covid", "ebv", "infection", "viral",
        "inflammos", "inflammatory", "complement", "toll like", "innate immune",
        "interferon")),
    ("Immune signalling", (
        "b-cell", "b cell", "t cell", "t-cell", "macrophage", "interleukin",
        "il1 ", "il6 ", "chemokine", "tnf", "rankl", "oncostatin", "tslp",
        "multiple sclerosis", "lymphopoietin")),
    ("Nervous system", (
        "alzheimer", "parkinson", "axon", "synapse", "neurotransmitter",
        "dopaminergic", "gabaergic", "glutamatergic", "neurotrophic")),
    ("Cell cycle, death and autophagy", (
        "cell cycle", "apoptosis", "autophagy", "death receptor",
        "differentiation")),
    ("Metabolism", (
        "metabolism", "biosynthesis", "pentose", "adipogenesis", "vitamin",
        "circadian", "glycation", "insulin", "leptin", "ghrelin", "ampk")),
    ("Development and tissue remodelling", (
        "fibrosis", "glomerulosclerosis", "noonan", "hedgehog", "wnt",
        "notch", "integrin", "focal adhesion", "macropinocytosis",
        "mitochondrial dynamics")),
)

#: Everything the table above does not name. Deliberately a real category
#: rather than "Other": these are all receptor-to-nucleus cascades.
DEFAULT_CATEGORY = "Signal transduction"


def classify(name):
    """The category Paintomics files an OmniPath pathway under."""
    lowered = name.lower()
    for category, keywords in PATHWAY_CATEGORIES:
        for keyword in keywords:
            if keyword in lowered:
                return category
    return DEFAULT_CATEGORY


def layout(node_ids, edges, iterations=220, seed=20260818):
    """Lay a pathway's subnetwork out on a canvas and return pixel boxes.

    Every other source stores a coordinate per gene because it has a drawn
    diagram to take one from. OmniPath has none, and the coordinate is not
    optional downstream: Step 4 groups features into boxes by ``x + "#" + y``,
    so genes without one collapse into a single overlapping box instead of
    appearing as a pathway. Laying the graph out here rather than in the
    browser also keeps the geometry stable between sessions and lets the
    existing pan/zoom viewer place the omics-painted boxes unchanged.

    A plain Fruchterman-Reingold, vectorised over NumPy: the repulsion term is
    the only O(n^2) step and n is at most a few hundred, so a pathway lays out
    in single-digit milliseconds. Seeded, because an installer that produced a
    different diagram on every run would make its own output impossible to
    diff.
    """
    import numpy

    count = len(node_ids)
    if count == 0:
        return {}, (0, 0)
    if count == 1:
        return ({node_ids[0]: (CANVAS_MARGIN, CANVAS_MARGIN)},
                (2 * CANVAS_MARGIN + NODE_WIDTH, 2 * CANVAS_MARGIN + NODE_HEIGHT))

    index = {identifier: position for position, identifier in enumerate(node_ids)}
    generator = numpy.random.RandomState(seed)
    positions = generator.rand(count, 2).astype(numpy.float64)

    adjacency = numpy.zeros((count, count), dtype=numpy.float64)
    for source, target, _sign, *_provenance in edges:
        i, j = index[source], index[target]
        adjacency[i, j] = adjacency[j, i] = 1.0

    optimal = numpy.sqrt(1.0 / count)          # ideal edge length on a unit square
    temperature = 0.1
    cooling = temperature / (iterations + 1)

    for _step in range(iterations):
        delta = positions[:, numpy.newaxis, :] - positions[numpy.newaxis, :, :]
        distance = numpy.linalg.norm(delta, axis=-1)
        numpy.clip(distance, 1e-4, None, out=distance)

        # repulsion k^2/d everywhere, attraction d^2/k along edges
        force = (optimal ** 2) / distance - adjacency * (distance ** 2) / optimal
        displacement = numpy.einsum("ijk,ij->ik", delta / distance[..., numpy.newaxis], force)

        length = numpy.linalg.norm(displacement, axis=-1)
        numpy.clip(length, 1e-4, None, out=length)
        positions += displacement / length[:, numpy.newaxis] * numpy.minimum(length, temperature)[:, numpy.newaxis]
        temperature -= cooling

    # A spring layout alone is not usable here. Its output is scale-free: a
    # dense core converges to almost a single point while a few weakly
    # connected nodes drift far out, and normalising by min/max then squeezes
    # every core node into a handful of pixels behind one another. Boxes 92px
    # wide cannot overlap and still be read or clicked.
    #
    # So the spring positions are used only for *relative* placement, and each
    # node is then assigned to its own cell on a grid sized for the boxes.
    # Structure survives -- neighbours stay neighbours -- and overlap becomes
    # impossible by construction.
    columns = int(numpy.ceil(numpy.sqrt(count * (NODE_HEIGHT * 3.0) / NODE_WIDTH)))
    columns = max(1, min(columns, count))
    rows = int(numpy.ceil(count / float(columns)))

    cell_width = NODE_WIDTH + 26
    cell_height = NODE_HEIGHT + 30
    width = max(MIN_CANVAS, columns * cell_width + 2 * CANVAS_MARGIN)
    height = max(MIN_CANVAS, rows * cell_height + 2 * CANVAS_MARGIN)

    # Rank-normalise instead of min/max-normalise: an outlier then costs one
    # rank rather than the whole range.
    order_x = numpy.argsort(numpy.argsort(positions[:, 0]))
    order_y = numpy.argsort(numpy.argsort(positions[:, 1]))
    target = numpy.column_stack([
        order_x / max(1.0, count - 1.0) * (columns - 1),
        order_y / max(1.0, count - 1.0) * (rows - 1),
    ])

    # Claim the nearest free cell, most-central node first, so the crowded
    # middle settles before the periphery is pushed outward.
    centre = target.mean(axis=0)
    by_centrality = numpy.argsort(numpy.linalg.norm(target - centre, axis=-1))

    taken = set()
    placement = {}
    for node in by_centrality:
        wanted = (int(round(target[node][0])), int(round(target[node][1])))
        best, best_distance = None, None
        for column in range(columns):
            for row in range(rows):
                if (column, row) in taken:
                    continue
                distance = (column - wanted[0]) ** 2 + (row - wanted[1]) ** 2
                if best_distance is None or distance < best_distance:
                    best, best_distance = (column, row), distance
        taken.add(best)
        placement[int(node)] = best

    scaled = {
        identifier: (CANVAS_MARGIN + placement[position][0] * cell_width,
                     CANVAS_MARGIN + placement[position][1] * cell_height)
        for identifier, position in index.items()
    }
    return scaled, (int(width), int(height))


def build_pathway_overview(documents):
    """Build the species-level pathway-to-pathway network for the Step 3 view.

    The three diagram-bearing sources ship one of these per species, describing
    how whole pathways relate; without it the network tab renders nothing at
    all for this source. Two OmniPath pathways are linked when they share
    genes, weighted by how many, and grouped under their curating resource so
    the view has a classification layer to collapse.

    Shape is Cytoscape.js elements, matching pathways_network_Reactome.json.
    """
    members = {document["ID"]: {gene["id"] for gene in document["genes"]}
               for document in documents}

    nodes, resources = [], set()
    for document in documents:
        resource = document["classification"].split(";")[-1].strip()
        resources.add(resource)
        nodes.append({"data": {"id": document["ID"],
                               "parent": _resource_node_id(resource),
                               "label": document["name"]},
                      "group": "nodes"})

    # Parents first: Cytoscape.js drops a child whose compound parent is
    # declared after it.
    nodes = [{"data": {"id": _resource_node_id(resource), "label": resource,
                       "is_classification": "A"}, "group": "nodes"}
             for resource in sorted(resources)] + nodes

    edges = []
    identifiers = sorted(members)
    for index, left in enumerate(identifiers):
        for right in identifiers[index + 1:]:
            shared = len(members[left] & members[right])
            if shared:
                edges.append({"data": {"id": "%s-%s" % (left, right),
                                       "source": left, "target": right,
                                       "weight": shared, "class": "l"},
                              "group": "edges"})

    logger.info("overview network: %d nodes (%d pathways), %d edges",
                len(nodes), len(documents), len(edges))
    return {"nodes": nodes, "edges": edges}


def _resource_node_id(resource):
    return "omnipath_%s" % resource.lower()


def _pathway_id(resource, name):
    """A stable, filesystem- and URL-safe pathway ID.

    Kept alphanumeric because the ``kegg_data`` route strips non-digits from
    the first underscore-separated segment of any non-KEGG, non-MapMan
    identifier; an ID that survives that transformation unchanged cannot be
    silently rewritten into another pathway's.
    """
    slug = "".join(character if character.isalnum() else "" for character in name)
    # md5, not hash(): CPython salts str hashing per process (PYTHONHASHSEED),
    # so the built-in fallback produced a DIFFERENT id on every reinstall and
    # orphaned every saved job that referenced the pathway. Only names with no
    # alphanumeric characters at all reach this branch, but a silently moving
    # identifier is not an acceptable failure mode for one.
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return "op%s%s" % (resource[:2].lower(), slug[:40] or digest)


# ---------------------------------------------------------------------------
# Transcription-factor targets
# ---------------------------------------------------------------------------

def fetch_tf_targets(taxid):
    """Return CollecTRI TF -> target rows for one taxid.

    Each row is ``{"tf", "target", "sign", "source", "references"}`` with the
    gene symbols OmniPath reports for that organism, ``sign`` an int in
    {+1, -1, 0} and ``source`` the literal ``"CollecTRI"``. Self-loops and rows
    missing either symbol are dropped: a TF file keyed on symbols cannot
    represent them.
    """
    body = _fetch("interactions", {
        "datasets": TF_DATASET, "organisms": str(taxid), "genesymbols": "1",
        "format": "tsv", "fields": "references,sources",
    })
    rows = []
    for row in _rows(body):
        tf = (row.get("source_genesymbol") or "").strip()
        target = (row.get("target_genesymbol") or "").strip()
        if not tf or not target or tf == target:
            continue
        rows.append({
            "tf": tf,
            "target": target,
            "sign": _SIGN_CODES[_sign(row)],
            "source": "CollecTRI",
            "references": row.get("references") or "",
        })
    logger.info("taxid %s: %d CollecTRI TF-target rows", taxid, len(rows))
    return rows


def read_symbol_table(code, kegg_data_dir=None):
    """Return ``{ALIAS_UPPER: organism symbol}`` from ``kegg2genesymbol.list``.

    The fourth column of that file reads ``Ikzf1, Ikaros, LyF-1; DNA-binding
    protein Ikaros``: a comma list of the primary symbol followed by its
    aliases, then ``;`` and a description. Every name in the list, upper-cased,
    maps to the *first* one -- the symbol KEGG itself uses for the gene -- and
    the first gene to claim an alias keeps it, so a name shared by two genes is
    never silently reassigned. Rows without a ``;`` carry a description only
    and name no symbol at all.

    Returns an empty table (with a warning) when the file is absent, so a
    caller can still write the sources that need no translation.
    """
    directory = _speciesDirectory(code, kegg_data_dir)
    if directory is None:
        return {}
    path = os.path.join(directory, "mapping", "kegg2genesymbol.list")
    if not os.path.isfile(path):
        logger.warning("%s missing; symbols cannot be translated for %s", path, code)
        return {}

    table = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 4:
                continue
            names, separator, _description = columns[3].partition(";")
            if not separator:
                continue
            symbols = [name.strip() for name in names.split(",")]
            symbols = [name for name in symbols if name]
            if not symbols:
                continue
            primary = symbols[0]
            for alias in symbols:
                table.setdefault(alias.upper(), primary)
    logger.info("%s: %d symbol aliases read from %s", code, len(table), path)
    return table


def _translate_rows(rows, symbol_table, source):
    """Re-express ``rows`` in the symbols of ``symbol_table``.

    A row survives only when both its TF and its target resolve; a pair whose
    two ends collapse onto the same organism gene (two human paralogs with one
    mouse ortholog) is dropped as the self-loop it has become. ``source`` is
    stamped on every surviving row. Linear in the number of rows.
    """
    translated = []
    for row in rows:
        tf = symbol_table.get(row["tf"].upper())
        target = symbol_table.get(row["target"].upper())
        if not tf or not target or tf == target:
            continue
        translated.append({
            "tf": tf,
            "target": target,
            "sign": row["sign"],
            "source": source,
            "references": row.get("references", ""),
        })
    return translated


def map_human_by_symbol(rows, symbol_table):
    """Carry human CollecTRI rows across to an organism by symbol identity.

    ``symbol_table`` is ``read_symbol_table``'s output for the target
    organism. Human ``IKZF1`` finds mouse ``Ikzf1`` because the mouse alias
    list contains ``Ikaros``/``IKZF1``-style names in every casing; a human
    symbol the organism's table does not know is dropped rather than guessed.
    Surviving rows are stamped ``source = "CollecTRI-human"`` so a reader can
    tell an orthology-by-name edge from one curated in the organism itself.
    """
    mapped = _translate_rows(rows, symbol_table, "CollecTRI-human")
    logger.info("human CollecTRI: %d of %d rows mapped by symbol", len(mapped), len(rows))
    return mapped


def fetch_trrust(code):
    """Return TRRUST v2 rows for ``code``, or ``[]`` where TRRUST has no table.

    The raw table has no header: ``TF<TAB>target<TAB>Activation|Repression|
    Unknown<TAB>PMID;PMID``. Symbols are as TRRUST prints them (upper-case
    human, mixed-case mouse); the caller normalises them through the organism's
    alias table.
    """
    url = TRRUST_URLS.get(code)
    if url is None:
        logger.info("TRRUST publishes no table for %s", code)
        return []

    body = _get(url, timeout=_TRRUST_TIMEOUT)
    if body.lstrip()[:1] == "<":
        raise RuntimeError("%s returned markup, not data (first 120 chars: %r)"
                           % (url, body.lstrip()[:120]))

    rows = []
    for columns in csv.reader(io.StringIO(body), delimiter="\t"):
        if len(columns) < 3:
            continue                                  # blank or truncated line
        tf, target, mode = (column.strip() for column in columns[:3])
        if not tf or not target or tf == target:
            continue
        rows.append({
            "tf": tf,
            "target": target,
            "sign": _TRRUST_SIGNS.get(mode, 0),
            "source": "TRRUST",
            "references": columns[3].strip() if len(columns) > 3 else "",
        })
    logger.info("%s: %d TRRUST rows", code, len(rows))
    return rows


def write_tf_targets(code, rows, kegg_data_dir=None):
    """Write the TF-target file and return ``(path, row count)``.

    Rows are deduplicated on ``(tf, target, source)`` keeping the first seen,
    then sorted by TF, target and source so the file diffs cleanly between
    installs. Written through a temporary file in the same directory and
    ``os.replace``d into place, so a reader never sees a half-written table.
    """
    directory = _speciesDirectory(code, kegg_data_dir)
    if directory is None:
        raise RuntimeError("no species directory for %s; cannot write %s"
                           % (code, TF_TARGET_FILENAME))
    mapping = os.path.join(directory, "mapping")
    os.makedirs(mapping, exist_ok=True)
    target = os.path.join(mapping, TF_TARGET_FILENAME)

    unique = {}
    for row in rows:
        unique.setdefault((row["tf"], row["target"], row["source"]), row)
    ordered = sorted(unique.values(),
                     key=lambda row: (row["tf"], row["target"], row["source"]))

    temporary = "%s.%d.tmp" % (target, os.getpid())
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write("\t".join(TF_TARGET_COLUMNS) + "\n")
            for row in ordered:
                handle.write("%s\t%s\t%d\t%s\t%s\n" % (
                    _cell(row["tf"]), _cell(row["target"]), int(row["sign"]),
                    _cell(row["source"]), _cell(row.get("references", ""))))
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise
    logger.info("wrote %s (%d rows)", target, len(ordered))
    return target, len(ordered)


def _cell(value):
    """A value made safe for one TSV cell: no tabs or line breaks inside it."""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def install_tf_targets(code, kegg_data_dir=None):
    """Merge every TF-target source available for ``code`` into one file.

    Sources: the organism's own CollecTRI table (organisms OmniPath serves),
    the human CollecTRI table mapped by symbol (non-human organisms OmniPath
    serves) and TRRUST (human and mouse). A source that fails is logged and
    skipped so one slow host cannot take the others down with it; the install
    fails only when no source produced anything. Returns the path written.
    """
    symbol_table = read_symbol_table(code, kegg_data_dir)
    rows, counts, failures = [], collections.OrderedDict(), []

    def attempt(name, produce):
        try:
            produced = produce()
        except Exception as error:                  # one source down, not all
            logger.warning("%s: %s failed and is skipped: %s", code, name, error)
            failures.append(name)
            return
        counts[name] = len(produced)
        rows.extend(produced)

    if code in SUPPORTED_ORGANISMS:
        taxid = SUPPORTED_ORGANISMS[code]
        attempt("CollecTRI", lambda: fetch_tf_targets(taxid))
        if taxid != HUMAN_TAXID:
            if symbol_table:
                attempt("CollecTRI-human",
                        lambda: map_human_by_symbol(fetch_tf_targets(HUMAN_TAXID), symbol_table))
            else:
                logger.warning("%s: no symbol table; human CollecTRI cannot be mapped", code)
    if code in TRRUST_URLS:
        def trrust():
            # TRRUST prints its own casing and the odd alias; the organism's
            # table settles both onto the symbol KEGG uses. Without a table
            # the rows are kept as printed rather than thrown away.
            fetched = fetch_trrust(code)
            return _translate_rows(fetched, symbol_table, "TRRUST") if symbol_table else fetched
        attempt("TRRUST", trrust)

    if not counts:
        raise RuntimeError(
            "no TF-target source produced rows for %s (failed: %s)"
            % (code, ", ".join(failures) or "none applicable"))

    path, written = write_tf_targets(code, rows, kegg_data_dir)
    logger.info("%s TF targets: %s -> %d unique rows in %s", code,
                ", ".join("%s=%d" % item for item in counts.items()), written, path)
    return path


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install(code, mongo_host="localhost", mongo_port=27017, dry_run=False,
            kegg_data_dir=None):
    """Download, assemble and write OmniPath data for one organism."""
    if code not in SUPPORTED_ORGANISMS:
        raise SystemExit(
            "OmniPath serves only %s (taxids %s); %r is not available from the "
            "web service and cannot be installed."
            % (", ".join(sorted(SUPPORTED_ORGANISMS)),
               ", ".join(str(t) for t in sorted(SUPPORTED_ORGANISMS.values())),
               code))

    taxid = SUPPORTED_ORGANISMS[code]
    logger.info("installing %s (taxid %d)", code, taxid)

    interactions = fetch_interactions(taxid)
    if not interactions:
        raise RuntimeError("no interactions returned for taxid %d" % taxid)

    ortholog_map = None
    if taxid != HUMAN_TAXID:
        logger.info("non-human organism: recovering ortholog map from OmniPath")
        ortholog_map = derive_ortholog_map(fetch_interactions(HUMAN_TAXID), interactions)
        if not ortholog_map:
            raise RuntimeError(
                "could not recover any ortholog pair for taxid %d; refusing to "
                "install a pathway partition built on a guess" % taxid)

    annotations = {resource: fetch_annotations(resource) for resource in PARTITIONS}

    # A partition that returns nothing must fail the install, not shrink it
    # silently. omnipathdb.org answers a request for a resource it does not
    # serve with HTTP 200 and a header-only TSV -- which passes every check in
    # _fetch, because it *is* well-formed TSV. `KEGG` is exactly that case: it
    # is advertised in annotations_summary with 192 pathway names, is absent
    # from /resources, and returns 0 rows even with license=academic (KEGG is
    # not redistributable; only the Pathway Commons mirror, `KEGG-PC`, is
    # served). Without this, adding such a resource to PARTITIONS installs zero
    # pathways from it and still reports success, because the aggregate guard
    # below is satisfied by the partitions that did work. This repository has
    # been bitten before by a well-formed but empty/wrong 200 reaching MongoDB.
    empty = sorted(resource for resource, pathways in annotations.items() if not pathways)
    if empty:
        raise RuntimeError(
            "OmniPath returned no pathway annotations for: %s. The service "
            "advertises some resources it does not actually serve; check "
            "https://omnipathdb.org/annotations?resources=<name>&format=tsv "
            "returns rows before adding one to PARTITIONS." % ", ".join(empty))

    documents, networks = build_pathways(interactions, annotations, ortholog_map)

    if not documents:
        raise RuntimeError("no pathway survived assembly for %s" % code)

    overview = build_pathway_overview(documents)

    if dry_run:
        logger.info("dry run: %d pathways, %d networks -- nothing written",
                    len(documents), len(networks))
        return documents, networks

    _write(code, documents, networks, mongo_host, mongo_port)
    _write_overview(code, overview, kegg_data_dir)
    _write_gene2pathway(code, documents, kegg_data_dir)
    try:
        install_tf_targets(code, kegg_data_dir)
    except Exception as error:                      # optional: pathways are in
        logger.warning("%s: TF-target file not written: %s", code, error)
    return documents, networks


def _speciesDirectory(code, kegg_data_dir):
    """The organism's directory under KEGG_DATA, or None if it is unusable."""
    if not kegg_data_dir:
        try:
            from src.conf.serverconf import KEGG_DATA_DIR as kegg_data_dir
        except ImportError:
            logger.warning("no KEGG_DATA_DIR configured; skipping file output")
            return None

    directory = os.path.join(kegg_data_dir, "current", code)
    if not os.path.isdir(directory):
        logger.warning("%s does not exist; skipping file output", directory)
        return None
    return directory


def _write_overview(code, overview, kegg_data_dir):
    """Write the species pathway-to-pathway network beside the other sources'."""
    directory = _speciesDirectory(code, kegg_data_dir)
    if directory is None:
        return

    target = os.path.join(directory, "pathways_network_%s.json" % SOURCE_NAME)
    with open(target, "w") as handle:
        json.dump(overview, handle, separators=(",", ":"))
    logger.info("wrote %s", target)


def _write_gene2pathway(code, documents, kegg_data_dir):
    """Write the feature-to-pathway list the metagene R script reads.

    ``generateMetaGenes.R`` runs once per (omic, database) and opens
    ``current/<specie>/gene2pathway_<database>.list`` before anything else; a
    database without one aborts the whole of step 2 for every omic, not just
    its own metagenes. Two tab-separated columns, no header, joined
    case-insensitively against column 3 of the matched-features file -- which
    for OmniPath is the UniProt accession, so that is what is written.
    """
    directory = _speciesDirectory(code, kegg_data_dir)
    if directory is None:
        return

    target = os.path.join(directory,
                          "gene2pathway_%s.list" % SOURCE_NAME.lower())
    written = 0
    with open(target, "w") as handle:
        for document in documents:
            for gene in document["genes"]:
                handle.write("%s\t%s\n" % (gene["id"], document["ID"]))
                written += 1
    logger.info("wrote %s (%d rows)", target, written)


def _write(code, documents, networks, mongo_host, mongo_port):
    """Replace this source's documents in the organism database.

    Scoped to ``source: OmniPath`` so a reinstall never touches the KEGG,
    Reactome or MapMan pathways sharing the collection.
    """
    from pymongo import MongoClient

    client = MongoClient(mongo_host, mongo_port, serverSelectionTimeoutMS=10000)
    database = client["%s-paintomics" % code]

    removed = database[PATHWAY_COLLECTION].delete_many({"source": SOURCE_NAME})
    database[PATHWAY_COLLECTION].insert_many(documents)
    # The KEGG build creates this index; an organism restored from a dump or
    # built before it existed may not have it, and this is the write that
    # adds a second `source` value for DatabaseAvailability to find.
    database[PATHWAY_COLLECTION].create_index("source")
    logger.info("%s: replaced %d OmniPath pathways with %d",
                code, removed.deleted_count, len(documents))

    database[NETWORK_COLLECTION].delete_many({})
    database[NETWORK_COLLECTION].insert_many(list(networks.values()))
    database[NETWORK_COLLECTION].create_index("ID")
    logger.info("%s: wrote %d pathway networks", code, len(networks))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--organism", required=True,
                        help="organism code (%s)" % ", ".join(sorted(SUPPORTED_ORGANISMS)))
    parser.add_argument("--mongo-host", default="localhost")
    parser.add_argument("--mongo-port", type=int, default=27017)
    parser.add_argument("--kegg-data-dir", default=None,
                        help="override KEGG_DATA_DIR for the overview network, gene2pathway and TF-target files")
    parser.add_argument("--dry-run", action="store_true",
                        help="assemble and report without writing to MongoDB")
    parser.add_argument("--tf-targets", action="store_true",
                        help="write only current/<organism>/mapping/%s (CollecTRI + TRRUST); "
                             "touches neither MongoDB nor the pathway files" % TF_TARGET_FILENAME)
    arguments = parser.parse_args(argv)

    if arguments.tf_targets:
        install_tf_targets(arguments.organism, arguments.kegg_data_dir)
        return 0

    install(arguments.organism, arguments.mongo_host, arguments.mongo_port,
            arguments.dry_run, arguments.kegg_data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
