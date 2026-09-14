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
