"""The design card: the experiment design as fixed fields the walker and the
Writer read at every turn.

One model call with a fixed schema from the job's stored design text, its
condition names and each omic's column header; without a model, a
deterministic card from the same inputs. A layer whose file carried no header
is marked unlabeled either way, and the walk may make no timing claim on it
until the user confirms the columns.
"""
from __future__ import annotations

import json
import re

# A column is a time point when its label carries a time token: a number with a
# unit ("24h", "0.5 min", "Ikaros_24h_rep1") or a unit with a number ("T0",
# "day7", "W2"). Letters on either side break the token, so "H2O2" is not one.
TIME_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|mins|minute|minutes|d|day|days|"
    r"w|wk|wks|week|weeks|sec|secs|mo|month|months)|(?:t|tp|time|timepoint|day|d|week|wk|w|hour|hr|h|month)"
    r"[\s_-]?\d+(?:\.\d+)?)(?![A-Za-z])", re.I)
NUMBER_LABEL_RE = re.compile(r"^-?\d+(\.\d+)?\s*[a-zA-Zµ%/]*$")
TIME_WORDS = ("time course", "time-course", "timecourse", "time point", "timepoint", "time series", "kinetic")


def shorten_labels(header):
    """The user's column labels from an input file's header, minus a prefix
    every label shares (``Ikaros/Control_0h`` -> ``0h``); None when the file
    carried no header. The job store turns a missing header into the STRING
    "None", and anything that is not a list of column names is no header."""
    if not isinstance(header, (list, tuple)) or len(header) < 2:
        return None
    labels = [str(h).strip() for h in header[1:]]
    prefix = labels[0]
    for label in labels[1:]:
        while prefix and not label.startswith(prefix):
            prefix = prefix[:-1]
    cut = max(prefix.rfind("_"), prefix.rfind("/"), prefix.rfind(" "))
    if cut > 0 and all(len(label) > cut + 1 for label in labels):
        labels = [label[cut + 1:] for label in labels]
    return labels


def labels_by_omic(job_instance):
    """omic name -> the user's labels, or None for an omic whose file had no
    header. Every reader of the job's values goes through this, so the chat,
    the report and the walk print the same labels and never borrow another
    omic's."""
    labels = {}
    for getter in ("getGeneBasedInputOmics", "getCompoundBasedInputOmics"):
        try:
            omics = getattr(job_instance, getter)() or []
        except Exception:                                      # noqa: BLE001
            omics = []
        for omic in omics:
            name = omic.get("omicName") if isinstance(omic, dict) else None
            if name:
                labels[name] = shorten_labels(omic.get("omicHeader"))
    return labels


def axis_kind(labels, design_text=""):
    """What the columns are, read from the labels and the design text:
    "time" (every label of a labeled omic is a time point, or the design says
    time course and the labels are numbers), "ordered" (numbers: doses,
    concentrations), "groups" (anything else: conditions by name),
    "unlabeled" (no omic carries a header). Only "time" and "ordered" allow
    talk of trajectories, peaks and early or late responses."""
    labeled = [l for l in (labels or {}).values() if l]
    if not labeled:
        return "unlabeled"
    text = str(design_text or "").lower()
    says_time = any(word in text for word in TIME_WORDS)
    all_time = all(all(TIME_TOKEN_RE.search(x) for x in l) for l in labeled)
    all_numbers = all(all(NUMBER_LABEL_RE.match(x) for x in l) for l in labeled)
    if all_time or (says_time and all_numbers):
        return "time"
    if all_numbers:
        return "ordered"
    return "groups"


def job_card(job_instance):
    """The deterministic card for a stored job: the design text's first
    sentence, the value type if the text names it, the axis, the labels, and
    which omics are unlabeled. No model call; cheap enough for every request."""
    labels = labels_by_omic(job_instance)
    try:
        design_text = job_instance.getExperimentDesign() or ""
    except Exception:                                          # noqa: BLE001
        design_text = ""
    conditions = []
    try:
        conditions = list(getattr(job_instance, "conditionNames", None) or [])
    except TypeError:
        conditions = []
    card = deterministic_card(design_text, conditions, labels)
    card["axis_kind"] = axis_kind(labels, design_text)
    card["labels"] = labels
    card["unlabeled"] = sorted(name for name, l in labels.items() if not l)
    return card


def card_lines(card):
    """The card as the lines every prompt carries."""
    kind = card.get("axis_kind", "groups")
    meaning = {"time": "the columns are time points in order",
               "ordered": "the columns are ordered values (doses or concentrations)",
               "groups": "the columns are conditions or groups; they have no order",
               "unlabeled": "no omic carries column labels"}[kind]
    lines = ["Design card:",
             "- perturbation: %s" % card.get("perturbation", "not stated"),
             "- a value is: %s" % card.get("value", "as given"),
             "- axis: %s (%s)" % (card.get("axis", "not stated"), meaning),
             "- baseline: %s" % card.get("baseline", "not stated"),
             "- relevant means: %s" % card.get("relevant", "not stated"),
             "- columns: %s" % card.get("columns", "")]
    if card.get("unlabeled"):
        lines.append("- no timing or order claims on: %s (their columns are unlabeled)"
                     % ", ".join(card["unlabeled"]))
    return lines


CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "perturbation": {"type": "string"},
        "value": {"type": "string"},
        "axis": {"type": "string"},
        "baseline": {"type": "string"},
        "relevant": {"type": "string"},
    },
    "required": ["perturbation", "value", "axis", "baseline", "relevant"],
    "additionalProperties": False,
}


def deterministic_card(design_text, condition_names, labels_by_omic):
    """A card with no model: the design text's first sentence, the value type
    if the text names it, the axis from the first labeled omic."""
    text = str(design_text or "").strip()
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    value = "as given by the user"
    low = text.lower()
    if "log2" in low and "fold" in low:
        value = "log2 fold change against the control"
    elif "fold" in low:
        value = "fold change against the control"
    elif "z-score" in low or "zscore" in low:
        value = "z-score"
    axis = [str(c) for c in (condition_names or [])]
    labeled = [labels for labels in labels_by_omic.values() if labels]
    if labeled:
        axis = labeled[0]
    return {
        "perturbation": first[:200] or "not stated",
        "value": value,
        "axis": " · ".join(axis) if axis else "not stated",
        "baseline": (axis[0] + "; whether it precedes the perturbation is not stated") if axis
                    else "not stated",
        "relevant": "not stated; the flags are used as given",
        "columns": columns_line(labels_by_omic),
        "source": "deterministic",
    }


def columns_line(labels_by_omic):
    labeled = sorted(name for name, labels in labels_by_omic.items() if labels)
    unlabeled = sorted(name for name, labels in labels_by_omic.items() if not labels)
    parts = []
    if labeled:
        parts.append("labeled: " + ", ".join(labeled))
    if unlabeled:
        parts.append("UNLABELED (no timing claims until confirmed): " + ", ".join(unlabeled))
    return " · ".join(parts) or "no omics"


def model_card(client, design_text, condition_names, labels_by_omic, temperature=0.1):
    """The card by one schema-constrained model call; the deterministic card on
    any failure. The columns field is never the model's: it comes from the data."""
    fallback = deterministic_card(design_text, condition_names, labels_by_omic)
    prompt = (
        "Read an experiment design and answer with five short fields.\n"
        "perturbation: what was done, against what, in which system.\n"
        "value: what one number in the tables is (e.g. log2 fold change treated over control at one time point).\n"
        "axis: the conditions in order, as the user labels them.\n"
        "baseline: which condition is the baseline and whether the text says it precedes the perturbation; "
        "if not stated, say so and that a difference at the baseline is read as baseline, not effect.\n"
        "relevant: what the user's relevance flag means if the text says; else 'not stated'.\n"
        "Never invent what the text does not say.\n\n"
        "Design text:\n%s\n\nCondition names: %s\n\nColumn labels per omic: %s" % (
            design_text, ", ".join(map(str, condition_names or [])) or "none",
            json.dumps(labels_by_omic)))
    try:
        card = client.complete_json(
            [{"role": "system", "content": "You extract experiment designs into fixed fields."},
             {"role": "user", "content": prompt}],
            "design_card", CARD_SCHEMA, lambda text: None, max_tokens=600, temperature=temperature)
    except Exception:                                                 # noqa: BLE001
        card = None
    if not isinstance(card, dict) or not all(k in card for k in CARD_SCHEMA["required"]):
        return fallback
    card = {k: str(card.get(k, "")) for k in CARD_SCHEMA["required"]}
    card["columns"] = fallback["columns"]
    card["source"] = "model"
    return card


def card_text(card):
    return "\n".join("%-13s %s" % (key, card.get(key, "")) for key in
                     ("perturbation", "value", "axis", "baseline", "relevant", "columns"))
