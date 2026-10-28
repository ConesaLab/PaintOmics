"""The job laid over the network: r, layer text, regulator nodes, heat.

The user's values are never computed on. They are copied into text with the
user's own column labels; the only thing the arithmetic reads is ``r``, the OR
over a node's own layers of the job's ``relevant`` flag (itself OR over
conditions). A regulatory omic becomes nodes only when its rows carry their
own identity: miRNA rows name the miRNA and become ``mir:<name>`` nodes with an
edge to each gene they are attached to; DNase rows are keyed by the gene and
stay a layer on it.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from src.classes.AIInterpret.walker import heat as heat_mod
from src.classes.AIInterpret.walker.card import job_value_kinds, labels_by_omic, shorten_labels

REGULATOR_OMICS = ("miRNA-seq", "miRNA", "microRNA")


class Overlay(object):
    """Everything the walk reads about the job, keyed by network node id."""

    def __init__(self):
        self.measured = set()
        self.r = {}                          # node -> bool
        self.layers = OrderedDict()          # node -> [layer dict, ...]
        self.labels = {}                     # omic -> [column label, ...] or None
        self.value_kind = []                 # card.value_kinds: omics with p/q-value columns
        self.significance = {}               # omic -> [column index, ...] of p/q-values
        self.heat = {}
        self.cap = None
        self.capped = set()
        self.N = 0
        self.K = 0
        self.regulators = 0

    def layer_text(self, node_id, flip=False):
        """The block the model reads for one node: every layer, every member,
        the flag as recorded, the values as text with the user's labels.

        ``flip``: every value's sign reversed, for the direction check's
        counter-hypothesis -- except a p-value or q-value, which has no sign
        to reverse and would read as a negative p-value."""
        lines = []
        for layer in self.layers.get(node_id, []):
            lines.append("%-16s %-20s %-12s %s" % (
                layer["omic"], layer["member"][:20],
                "relevant" if layer["relevant"] else "not relevant",
                _flipped(layer, self.labels.get(layer["omic"])) if flip else layer["text"]))
        return "\n".join(lines) if lines else "not measured"


def _any_relevant(value):
    if isinstance(value, (list, tuple)):
        return any(bool(v) for v in value)
    return bool(value)


_shorten_labels = shorten_labels          # the tests reach it under this name


# Below this magnitude a value is printed in scientific form. "%.2f" made
# every p-value and q-value under 0.005 read "+0.00": on job u36I21KMxV
# (value column adj.p.Value, 2026-10-27) the walk wrote that GSTA4, EPHX1,
# AKR1C1, AKR1C2 and NQO1 "all read adj.p.Value +0.00", so "the whole
# xenobiotic/antioxidant arm is unchanged" -- the most significant genes of the
# job, read as zero.
SMALL_VALUE = 0.01


def value_text(number):
    """One user value as the model reads it: ``+1.90``, ``−0.35`` (a unicode
    minus) and ``+0.00`` for an exact zero, as always; a magnitude under
    SMALL_VALUE in two significant digits, ``+3.2e-05``, ``−4.1e-03``, so a
    small p-value is never printed as zero. verify.NUMBER_RE reads both forms
    back."""
    sign = "+" if number >= 0 else "−"
    size = abs(number)
    if size == 0 or size >= SMALL_VALUE:
        return "%s%.2f" % (sign, size)
    return "%s%.1e" % (sign, size)


def values_text(values, labels):
    """``0h +1.90 · 2h +0.35 ...``; unlabeled columns are numbered ``c1, c2``."""
    out = []
    for i, v in enumerate(values or []):
        try:
            number = float(v)
        except (TypeError, ValueError):
            continue
        if number != number:            # NaN marks an unmeasured cell, not a reading
            continue
        label = labels[i] if labels and i < len(labels) else "c%d" % (i + 1)
        out.append("%s %s" % (label, value_text(number)))
    return " · ".join(out)


def signed_values(layer):
    """The numbers of a layer whose sign means something: every readable,
    non-NaN value except those in the layer's significance columns (p-values,
    q-values; card.significance_columns). A p-value is never negative, so
    read as a sign every one of them said "up"."""
    skip = set(layer.get("significance") or ())
    for i, value in enumerate(layer.get("values") or []):
        if i in skip:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            yield number


_SIGN_RE = re.compile(r"(?<=\s)([+−-])(?=\d)")


def flipped_text(values_line):
    """A values line with every value's sign reversed (``0h +0.10`` ->
    ``0h −0.10``); an exponent's sign is not a value's and stays."""
    def flip(match):
        return "−" if match.group(1) == "+" else "+"
    return _SIGN_RE.sub(flip, str(values_line or ""))


def _flipped(layer, labels):
    """A layer's values line with its signed values reversed; its p-value and
    q-value columns, re-rendered from the values, as they are."""
    skip = set(layer.get("significance") or ())
    if not skip:
        return flipped_text(layer["text"])
    values = []
    for i, value in enumerate(layer.get("values") or []):
        try:
            values.append(value if i in skip else -float(value))
        except (TypeError, ValueError):
            values.append(value)
    return values_text(values, labels)


def node_for_feature(network, feature_id, kind):
    """Map a job feature id onto a network node id, whatever id space it uses:
    KEGG gene ids, UniProt (Reactome rows), symbols (OmniPath rows), compounds."""
    fid = str(feature_id)
    if kind == "compound":
        return "c:" + fid if ("c:" + fid) in network.nodes else None
    # Not only digits: a bacterium's or a plant's KEGG gene id is its locus tag
    # (SAUSA300_0001, NGO_1747, b2029, AT1G01010), and the graph keys it as is.
    if fid.isdigit() or ("g:" + fid) in network.nodes:
        return "g:" + fid
    kegg = network.uniprot_to_kegg.get(fid) or network.symbol_to_kegg.get(fid.upper())
    return "g:" + kegg if kegg else None


def overlay_job(network, job_instance):
    """Add the job's regulator nodes to ``network`` (in place) and return the
    Overlay: measured set, r, layer text, labels, heat, degree cap."""
    ov = Overlay()
    ov.labels = labels_by_omic(job_instance)
    # The omics with p-value or q-value columns, and those columns' indices:
    # no sign is ever read from them (signed_values).
    ov.value_kind = job_value_kinds(job_instance)
    ov.significance = {entry["omic"]: entry["index"] for entry in ov.value_kind}
    regulators = OrderedDict()                # mir:<name> -> {targets, relevant, values}
    seen_layers = set()

    def take(feature, kind):
        node_id = node_for_feature(network, feature.getID(), kind)
        if node_id is None or node_id not in network.nodes:
            return
        # KEGG's symbol list skips some genes, which then read as their id
        # ("g:11951"); the user's own name for the feature is the better label.
        node = network.nodes[node_id]
        if str(node.get("label")) in (node_id, node_id.split(":", 1)[-1]):
            try:
                name = feature.getName()
            except AttributeError:
                name = None
            if name and str(name) != str(feature.getID()):
                node["label"] = str(name)
        for omic in feature.getOmicsValues() or []:
            omic_name = str(omic.getOmicName() or "")
            member = str(omic.getOriginalName() or omic.getInputName() or feature.getID())
            relevant = _any_relevant(omic.isRelevant())
            values = list(omic.getValues() or [])
            if omic_name in REGULATOR_OMICS:
                reg = regulators.setdefault("mir:" + member, {
                    "targets": set(), "relevant": False, "values": values, "omic": omic_name})
                reg["targets"].add(node_id)
                reg["relevant"] = reg["relevant"] or relevant
                continue
            key = (node_id, omic_name, member)
            if key in seen_layers:
                continue
            seen_layers.add(key)
            ov.measured.add(node_id)
            ov.r[node_id] = ov.r.get(node_id, False) or relevant
            ov.layers.setdefault(node_id, []).append({
                "omic": omic_name, "member": member, "relevant": relevant,
                "values": values, "text": values_text(values, ov.labels.get(omic_name)),
                "significance": ov.significance.get(omic_name, [])})

    for feature in (job_instance.getInputGenesData() or {}).values():
        take(feature, "gene")
    for feature in (job_instance.getInputCompoundsData() or {}).values():
        take(feature, "compound")

    for node_id, reg in regulators.items():
        label = node_id[4:]
        network.add_node(node_id, label, "miRNA")
        ov.measured.add(node_id)
        ov.r[node_id] = reg["relevant"]
        ov.layers[node_id] = [{"omic": reg["omic"], "member": label, "relevant": reg["relevant"],
                               "values": reg["values"],
                               "text": values_text(reg["values"], ov.labels.get(reg["omic"])),
                               "significance": ov.significance.get(reg["omic"], [])}]
        for target in reg["targets"]:
            network.add_edge(node_id, target, -1, "job:" + reg["omic"], "miRNA target")
    ov.regulators = len(regulators)
    ov.heat = heat_mod.compute_heat(network, ov.measured, ov.r)
    ov.N = len(ov.measured)
    ov.K = sum(1 for v in ov.measured if ov.r.get(v))
    return ov


def apply_degree_cap(overlay, percentile=99):
    overlay.cap, overlay.capped = heat_mod.degree_cap(overlay.heat, percentile)
    return overlay

