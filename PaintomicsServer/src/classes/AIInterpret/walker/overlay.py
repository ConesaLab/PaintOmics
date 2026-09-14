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

from collections import OrderedDict

from src.classes.AIInterpret.walker import heat as heat_mod

REGULATOR_OMICS = ("miRNA-seq", "miRNA", "microRNA")


class Overlay(object):
    """Everything the walk reads about the job, keyed by network node id."""

    def __init__(self):
        self.measured = set()
        self.r = {}                          # node -> bool
        self.layers = OrderedDict()          # node -> [layer dict, ...]
        self.labels = {}                     # omic -> [column label, ...] or None
        self.heat = {}
        self.cap = None
        self.capped = set()
        self.N = 0
        self.K = 0
        self.regulators = 0

    def layer_text(self, node_id):
        """The block the model reads for one node: every layer, every member,
        the flag as recorded, the values as text with the user's labels."""
        lines = []
        for layer in self.layers.get(node_id, []):
            lines.append("%-16s %-20s %-12s %s" % (
                layer["omic"], layer["member"][:20],
                "relevant" if layer["relevant"] else "not relevant", layer["text"]))
        return "\n".join(lines) if lines else "not measured"


def _any_relevant(value):
    if isinstance(value, (list, tuple)):
        return any(bool(v) for v in value)
    return bool(value)


def _shorten_labels(header):
    """The user's column labels, minus a prefix every label shares
    (``Ikaros/Control_0h`` -> ``0h``). None when the file had no header."""
    # The job store turns a missing header into the STRING "None" (adaptBSON);
    # anything that is not a list of column names is no header.
    if not isinstance(header, (list, tuple)) or len(header) < 2:
        return None
    labels = [str(h) for h in header[1:]]
    prefix = labels[0]
    for label in labels[1:]:
        while prefix and not label.startswith(prefix):
            prefix = prefix[:-1]
    cut = max(prefix.rfind("_"), prefix.rfind("/"), prefix.rfind(" "))
    if cut > 0 and all(len(label) > cut + 1 for label in labels):
        labels = [label[cut + 1:] for label in labels]
    return labels


def values_text(values, labels):
    """``0h +1.90 · 2h +0.35 ...``; unlabeled columns are numbered ``c1, c2``."""
    out = []
    for i, v in enumerate(values or []):
        try:
            number = float(v)
        except (TypeError, ValueError):
            continue
        label = labels[i] if labels and i < len(labels) else "c%d" % (i + 1)
        out.append("%s %s%.2f" % (label, "+" if number >= 0 else "−", abs(number)))
    return " · ".join(out)


def _column_labels(job_instance):
    """omic name -> labels (None when the input file carried no header)."""
    labels = {}
    for getter in ("getGeneBasedInputOmics", "getCompoundBasedInputOmics"):
        try:
            omics = getattr(job_instance, getter)() or []
        except Exception:                                      # noqa: BLE001
            omics = []
        for omic in omics:
            name = omic.get("omicName") if isinstance(omic, dict) else None
            if name:
                labels[name] = _shorten_labels(omic.get("omicHeader"))
    return labels


def node_for_feature(network, feature_id, kind):
    """Map a job feature id onto a network node id, whatever id space it uses:
    KEGG gene ids, UniProt (Reactome rows), symbols (OmniPath rows), compounds."""
    fid = str(feature_id)
    if kind == "compound":
        return "c:" + fid if ("c:" + fid) in network.nodes else None
    if fid.isdigit():
        return "g:" + fid
    kegg = network.uniprot_to_kegg.get(fid) or network.symbol_to_kegg.get(fid.upper())
    return "g:" + kegg if kegg else None


def overlay_job(network, job_instance):
    """Add the job's regulator nodes to ``network`` (in place) and return the
    Overlay: measured set, r, layer text, labels, heat, degree cap."""
    ov = Overlay()
    ov.labels = _column_labels(job_instance)
    regulators = OrderedDict()                # mir:<name> -> {targets, relevant, values}
    seen_layers = set()

    def take(feature, kind):
        node_id = node_for_feature(network, feature.getID(), kind)
        if node_id is None or node_id not in network.nodes:
            return
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
                "values": values, "text": values_text(values, ov.labels.get(omic_name))})

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
                               "text": values_text(reg["values"], ov.labels.get(reg["omic"]))}]
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


def relabel_unlabeled(overlay):
    """Which omics carry no column labels: the design card marks them and the
    walker may make no timing claim on them until the user confirms."""
    return sorted(name for name, labels in overlay.labels.items() if labels is None)
