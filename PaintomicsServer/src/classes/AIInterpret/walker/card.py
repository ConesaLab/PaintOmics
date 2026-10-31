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

from src.classes.AIInterpret.llm_client import opens_like_reasoning, strip_reasoning
from src.classes.AIInterpret.walker.anchor import find_perturbed_genes
from src.common.FormPlaceholders import designWithoutPlaceholder

# A column is a time point when its label carries a time token: a number with a
# unit ("24h", "0.5 min", "Ikaros_24h_rep1") or a unit with a number ("T0",
# "day7", "W2"). Letters on either side break the token, so "H2O2" is not one.
TIME_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|mins|minute|minutes|d|day|days|"
    r"w|wk|wks|week|weeks|sec|secs|mo|month|months)|(?:t|tp|time|timepoint|day|d|week|wk|w|hour|hr|h|month)"
    r"[\s_-]?\d+(?:\.\d+)?)(?![A-Za-z])", re.I)
NUMBER_LABEL_RE = re.compile(r"^-?\d+(\.\d+)?\s*[a-zA-Zµ%/]*$")
TIME_WORDS = ("time course", "time-course", "timecourse", "time point", "timepoint", "time series", "kinetic")
# The axis kinds (axis_kind) whose columns carry an order.
ORDERED_AXES = ("time", "ordered")
# A perturbation field that says there is none, as a whole phrase on the
# stripped, lower-cased text: a prefix match read "Na+ stress", "None-treated
# vs LPS" and "Unknown compound X exposure" as "not stated" (review of #212,
# 31 Oct 2026). The deterministic card writes "not stated"; a model card or a
# user may word it otherwise.
NOT_STATED_RE = re.compile(
    r"(?:it is |is )?not (?:explicitly |clearly )?(?:stated|given|specified|described|known|provided|mentioned)"
    r"(?: in the (?:design|text|design text))?"
    r"|none(?: stated| given| described| specified)?"
    r"|unknown|n/?a"
    r"|no perturbation(?: is)?(?: stated| described| given| specified| named| provided)?"
    r"|no design(?: text)?(?: provided| given| stated)?"
    r"|the design(?: text)? does not (?:describe|state|name|specify|mention) (?:a|any) perturbation")
# A column of p-values or q-values, by its header: p.value, pvalue, p_val,
# P-value, PValue, adj.p.Value, adj.P.Val, padj, p.adjusted, FDR, qvalue,
# q.value, q_val. Whole tokens only -- letters or digits on either side break
# the match -- so Ip, pH, Ctr_0H, PDX and dpi are not one.
SIGNIFICANCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:adj[._ -]?p(?:[._ -]?val(?:ue)?s?)?|p[._ -]?adj(?:usted)?|p[._ -]?val(?:ue)?s?"
    r"|q[._ -]?val(?:ue)?s?|fdr)(?![A-Za-z0-9])", re.I)
# A log transform in a header: -log10(padj), neg_log10_FDR, log10_pvalue,
# signed_-log10_pval, -lg(p), ln_qvalue, neglog_p. Such a column names a
# p-value but holds its transform, where larger is the stronger difference and
# a signed one carries the direction: everything SIGNIFICANCE_NOTE says about a
# p-value ("smaller means stronger", relevance_hint's "below 0.05", no sign) is
# exactly wrong for it, so it is no significance column and stays an ordinary
# signed value. log2/log10 bind anywhere ("mlog10p"); the bare log, ln and lg
# only as whole tokens, so logFC and the "lg" inside a word are not one.
LOG_TRANSFORM_RE = re.compile(r"log(?:10|2)|(?<![A-Za-z])(?:neg|minus|m)?(?:log|ln|lg)(?![A-Za-z])", re.I)
# A column of effect sizes, by its header: logFC, log2FC, log2FoldChange, FC,
# fold change, Log2 fold change; a log ratio (log2 Ratio, logratio) and
# limma's M; an estimate; a diff or difference. Whole tokens, case-insensitive
# but for M, so a lowercase "m" is no column of it. In a table that also
# holds p-values (a limma or DESeq2 export) only these carry the direction:
# AveExpr or baseMean is always positive and the largest number on the row, B
# is a log-odds, and read as a sign they made every gene "up". A plain ratio
# is never negative and is not one.
EFFECT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:log(?:2|10)?[._ -]?)?(?:fc|fold[._ -]?change)|log(?:2|10)?[._ -]?ratios?|(?-i:M)"
    r"|estimates?|diff(?:erence)?s?)(?![A-Za-z0-9])", re.I)
# A signed test statistic: t, t-statistic, DESeq2's stat, z, z-score. Its sign
# is the effect's, so a table with no effect-size column reads its direction
# here; beside a logFC it is not read, as before. "stat" only as the whole
# label: limma's B-statistic is the log-odds that a gene changed, whose sign
# says nothing of which way.
STATISTIC_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:t(?:[._ -]?stat(?:istic)?s?)?|z(?:[._ -]?scores?)?)(?![A-Za-z0-9])|^\s*stat\s*$", re.I)
# Of those, the corrected ones, which a relevance cut-off should be made from.
ADJUSTED_RE = re.compile(r"adj|fdr|(?<![A-Za-z0-9])q[._ -]?val", re.I)
# What every agent is told about such a column (service.walk_card_text, the
# chat's card_lines, the direction check's fit prompt). Nothing said it on job
# u36I21KMxV (2026-10-27), whose design text even read "adjusted p-value for
# differential expression": the walker, the Writer, the checks and the
# Narrator wrote "flat", "unchanged" and "the only non-zero adjusted p-value".
SIGNIFICANCE_NOTE = (
    "p-values or q-values (0 to 1); smaller means a stronger difference between conditions. They carry no "
    "direction: never call a gene up, down, flat or unchanged from them, and a value near 0 is the strongest "
    "difference, not the absence of one.")
# The organism as a paper's MeSH heading names it, by KEGG code.
ORGANISM_WORDS = {"mmu": "mouse", "hsa": "human", "rno": "rat", "dre": "zebrafish", "dme": "fly", "cel": "worm",
                  "sce": "yeast", "ath": "arabidopsis", "bta": "cow", "ssc": "pig", "gga": "chicken",
                  "cfa": "dog", "xla": "frog", "xtr": "frog", "osa": "rice", "zma": "maize"}


def organism_word(code):
    """The organism word the context match compares with a paper's: "mouse"
    for mmu; the code itself for a species the table does not name."""
    return ORGANISM_WORDS.get(str(code or "").lower(), str(code or ""))


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


def _omic_headers(job_instance):
    """(omic name, its file's header as stored) for every gene- and
    compound-based omic of the job."""
    for getter in ("getGeneBasedInputOmics", "getCompoundBasedInputOmics"):
        try:
            omics = getattr(job_instance, getter)() or []
        except Exception:                                      # noqa: BLE001
            omics = []
        for omic in omics:
            name = omic.get("omicName") if isinstance(omic, dict) else None
            if name:
                yield name, omic.get("omicHeader")


def labels_by_omic(job_instance):
    """omic name -> the user's labels, or None for an omic whose file had no
    header. Every reader of the job's values goes through this, so the chat,
    the report and the walk print the same labels and never borrow another
    omic's."""
    return {name: shorten_labels(header) for name, header in _omic_headers(job_instance)}


def significance_columns(labels):
    """The indices of the labels that name a p-value or q-value column, and
    not its log transform (LOG_TRANSFORM_RE)."""
    return [i for i, label in enumerate(labels or [])
            if SIGNIFICANCE_RE.search(str(label)) and not LOG_TRANSFORM_RE.search(str(label))]


def effect_columns(labels):
    """The indices of the labels that name an effect size (EFFECT_RE)."""
    return [i for i, label in enumerate(labels or []) if EFFECT_RE.search(str(label))]


def statistic_columns(labels):
    """The indices of the labels that name a signed test statistic (STATISTIC_RE)."""
    return [i for i, label in enumerate(labels or []) if STATISTIC_RE.search(str(label))]


def _columns_of(find, cells, shown):
    """``find`` over the whole header cells and over the shortened labels: a
    prefix every label shares ("padj_KO", "padj_WT" -> "KO", "WT") can be the
    very word."""
    return sorted(set(find(cells)) | set(find(shown)))


def value_kinds(headers_by_omic, condition_names=None):
    """Which omics' values are significance values, from the data alone (like
    axis_kind, never from a model): a list of {omic, kind, columns, index,
    signed_index}, one per omic with at least one p-value or q-value column.
    ``kind`` is "significance" when every column is one, "mixed" otherwise (a
    limma table: logFC, AveExpr, t, P.Value, adj.P.Val, B); ``columns`` are
    those columns' labels as the layer text prints them, ``index`` their
    positions. ``signed_index`` are the columns a direction is read from
    (overlay.signed_values): the effect-size columns (EFFECT_RE), else the
    signed statistics (STATISTIC_RE), else none. Every other column of a
    table with p-values in it is no direction to read: falling back to them
    read AveExpr or baseMean, the largest and always positive number on the
    row, as every gene "up".

    ``headers_by_omic`` maps an omic to its file's header as stored (ID column
    first); the whole cells are read as well as the shortened labels
    (_columns_of). An omic with no header borrows the job's condition names
    only when it is the job's sole omic; with two, they belong to some other
    file."""
    out = []
    for omic, header in headers_by_omic.items():
        shown = shorten_labels(header)
        cells = [str(h).strip() for h in header[1:]] if shown else None
        if cells is None and len(headers_by_omic) == 1 and condition_names:
            cells = shown = [str(c).strip() for c in condition_names]
        if not cells:
            continue
        # A log transform is read off the whole cell too: a prefix that hid it
        # ("-log10_KO_padj", "-log10_WT_padj" -> "KO_padj") makes no p-value of it.
        index = [i for i in _columns_of(significance_columns, cells, shown) if not LOG_TRANSFORM_RE.search(cells[i])]
        if index:
            effects = ([i for i in _columns_of(effect_columns, cells, shown) if i not in index]
                       or [i for i in _columns_of(statistic_columns, cells, shown) if i not in index])
            out.append({"omic": omic, "kind": "significance" if len(index) == len(cells) else "mixed",
                        "columns": [shown[i] for i in index], "index": index,
                        "signed_index": effects})
    return out


def job_value_kinds(job_instance):
    """value_kinds for a stored job."""
    try:
        conditions = list(getattr(job_instance, "conditionNames", None) or [])
    except TypeError:
        conditions = []
    return value_kinds(dict(_omic_headers(job_instance)), conditions)


def value_kind_text(value_kind):
    """The line every agent reads about significance columns; "" when there
    are none. A mixed omic names its columns, the others are all of it."""
    parts = [entry["omic"] if entry["kind"] == "significance"
             else "%s (columns %s)" % (entry["omic"], ", ".join(entry["columns"]))
             for entry in value_kind or []]
    return "%s: %s" % ("; ".join(parts), SIGNIFICANCE_NOTE) if parts else ""


def relevance_hint(value_kind):
    """For a job with no relevant feature: one sentence naming a significance
    column and the Relevant features file it could make; "" without one. An
    adjusted column is named before a raw one. Job laW30Q7bYU (2026-10-27)
    uploaded a whole limma table with adj.p.Value in it, no relevant file, and
    got only the generic refusal."""
    columns = [c for entry in value_kind or [] for c in entry["columns"]]
    if not columns:
        return ""
    column = next((c for c in columns if ADJUSTED_RE.search(c)), columns[0])
    return (" Your values include %s: a Relevant features file listing the features with %s below 0.05 "
            "would give the walk its seeds." % (column, column))


# What a model's reasoning about the design draft says of its own task, and a
# researcher's design never does: the column headers it was given, the
# instruction to describe the experiment in 2-3 sentences in the first person,
# what "the user" asks. An opening like "We need to" is not enough alone: "We
# need to compare KO vs WT at 0h, 2h and 6h after LPS" is a design a user typed.
DRAFTING_TASK_RE = re.compile(
    r"\bcolumn headers?\b|\bthe headers\b|\bdescri(?:be|ption of) the experiment(?: design)?\b|"
    r"\b(?:2|two)\s*(?:-|–|to)\s*(?:3|three) sentences\b|\bfirst person\b|\bthe user (?:asks|wants)\b|"
    r"\bi need to write\b", re.I)


def is_leaked_draft_reasoning(text):
    """True when a stored design is a model's reasoning about drafting it:
    it opens the way thinking aloud does (llm_client.opens_like_reasoning)
    and talks about the drafting task itself (DRAFTING_TASK_RE)."""
    return opens_like_reasoning(text) and bool(DRAFTING_TASK_RE.search(str(text or "")))


def design_text_of(job_instance):
    """The job's stored design text without the reasoning a model leaked into
    it. Designs written before every completion went through
    llm_client.strip_reasoning (#202) can begin "The user asks for a
    description of the experiment design ... Let's craft.</think>I performed
    ..." (jobs laW30Q7bYU, u36I21KMxV, 2026-10-27), and the card took that
    first sentence as the perturbation.

    A design saved under the old 300-token cap can be reasoning and nothing
    else, cut off before its "</think>" ("The user asks for a description of
    the experiment design ... I need to write 2-3 sentences"): there is no
    answer in it to keep, so the job reads as one with no design text. Only
    when it also speaks of the drafting task (is_leaked_draft_reasoning): a
    design the user typed may open "We need to compare ..." and is the design.
    The draft itself refuses any reasoning opener (the servlet's
    _usableDescription), since nothing a user wrote is at stake there.

    The Step 1 form's placeholder is no design either: 32 of 64 jobs on
    paintomics.org (2026-10-28) stored "e.g. RNA-seq of wildtype vs knockout
    mouse liver ..." because the form submitted it for an empty field, and the
    card made "mouse liver" the system of zebrafish and potato jobs. The job
    class drops it already; the guard is repeated here for any object that
    reaches the card without going through that getter."""
    try:
        text = job_instance.getExperimentDesign() if hasattr(job_instance, "getExperimentDesign") else ""
    except Exception:                                          # noqa: BLE001
        text = ""
    text = strip_reasoning(designWithoutPlaceholder(text))
    return "" if is_leaked_draft_reasoning(text) else text


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


def axis_is_ordered(kind):
    """True for the axis kinds whose columns carry an order (time points,
    doses): the only ones that allow before and after, trajectories, peaks."""
    return kind in ORDERED_AXES


def _says_no_perturbation(text):
    text = str(text or "").strip().lower().rstrip(".!;: ")
    return not text or bool(NOT_STATED_RE.fullmatch(text))


def perturbation_stated(card, design_text=None):
    """Whether the card names a perturbation. Not when the job has no design
    text at all (the #209 case; a model card may still word something there),
    unless the user's own edits name one; not when the perturbation field is
    wholly a "not stated" phrase; always when a perturbed gene is named."""
    if design_text is not None and not str(design_text).strip() and card.get("source") != "user":
        return False
    if card.get("perturbed_genes"):
        return True
    return not _says_no_perturbation(card.get("perturbation"))


def job_card(job_instance):
    """The deterministic card for a stored job: the design text's first
    sentence, the value type if the text names it, the axis, the labels, and
    which omics are unlabeled. No model call; cheap enough for every request."""
    labels = labels_by_omic(job_instance)
    design_text = design_text_of(job_instance)
    conditions = []
    try:
        conditions = list(getattr(job_instance, "conditionNames", None) or [])
    except TypeError:
        conditions = []
    card = deterministic_card(design_text, conditions, labels)
    card["axis_kind"] = axis_kind(labels, design_text)
    card["labels"] = labels
    card["unlabeled"] = sorted(name for name, l in labels.items() if not l)
    card["value_kind"] = value_kinds(dict(_omic_headers(job_instance)), conditions)
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
             "- perturbed genes: %s (%s)" % (", ".join(card.get("perturbed_genes") or []) or "none named",
                                              card.get("perturbation_direction", "unknown")),
             "- a value is: %s" % card.get("value", "as given"),
             "- axis: %s (%s)" % (card.get("axis", "not stated"), meaning),
             "- baseline: %s" % card.get("baseline", "not stated"),
             "- relevant means: %s" % card.get("relevant", "not stated"),
             "- columns: %s" % card.get("columns", "")]
    if card.get("unlabeled"):
        lines.append("- no timing or order claims on: %s (their columns are unlabeled)"
                     % ", ".join(card["unlabeled"]))
    if value_kind_text(card.get("value_kind")):
        lines.append("- value kind: %s" % value_kind_text(card.get("value_kind")))
    return lines


CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "perturbation": {"type": "string"},
        "value": {"type": "string"},
        "axis": {"type": "string"},
        "baseline": {"type": "string"},
        "relevant": {"type": "string"},
        "perturbed_genes": {"type": "array", "items": {"type": "string"}},
        "perturbation_direction": {"type": "string", "enum": ["up", "down", "unknown"]},
        "system": {"type": "string"},
    },
    "required": ["perturbation", "value", "axis", "baseline", "relevant", "perturbed_genes",
                 "perturbation_direction", "system"],
    "additionalProperties": False,
}


def deterministic_card(design_text, condition_names, labels_by_omic, aliases=None):
    """A card with no model: the design text's first sentence, the value type
    if the text names it, the axis from the first labeled omic, and the
    perturbed genes the text names next to a perturbation word (resolved
    through the organism's alias table when given)."""
    text = str(design_text or "").strip()
    genes, direction = find_perturbed_genes(text, aliases or {})
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
        "perturbed_genes": genes,
        "perturbation_direction": direction,
        "system": "not stated",
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


def model_card(client, design_text, condition_names, labels_by_omic, temperature=0.1, aliases=None):
    """The card by one schema-constrained model call; the deterministic card on
    any failure. The columns field is never the model's: it comes from the data.
    The perturbed genes are kept only when the organism's table knows them."""
    fallback = deterministic_card(design_text, condition_names, labels_by_omic, aliases)
    prompt = (
        "Read an experiment design and answer with five short fields.\n"
        "perturbation: what was done, against what, in which system; 'not stated' if the text names none.\n"
        "value: what one number in the tables is (e.g. log2 fold change treated over control at one time point).\n"
        "axis: the conditions in order, as the user labels them.\n"
        "baseline: which condition is the baseline and whether the text says it precedes the perturbation; "
        "if not stated, say so and that a difference at the baseline is read as baseline, not effect.\n"
        "relevant: what the user's relevance flag means if the text says; else 'not stated'.\n"
        "perturbed_genes: the gene symbols the experiment perturbed (induced, overexpressed, knocked out, "
        "knocked down, inhibited, deleted), written as the organism's gene symbols (Ikaros is Ikzf1); [] when none.\n"
        "perturbation_direction: up when the gene was induced, overexpressed or activated; down when it was "
        "knocked out, knocked down, deleted or inhibited; unknown otherwise.\n"
        "system: the cell type or tissue and the organism, e.g. 'mouse B3 pre-B cell line'; 'not stated' if absent.\n"
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
    genes = card.get("perturbed_genes")
    card = {k: str(card.get(k, "")) for k in CARD_SCHEMA["required"] if k != "perturbed_genes"}
    # The model's answer stands, an explicit empty list included; the regex
    # reading fills in only when the model gave none.
    card["perturbed_genes"] = list(fallback["perturbed_genes"]) if genes is None else normalise_genes(genes, aliases)
    if card["perturbation_direction"] not in ("up", "down", "unknown"):
        card["perturbation_direction"] = fallback["perturbation_direction"]
    if card["perturbation_direction"] == "unknown" and card["perturbed_genes"]:
        card["perturbation_direction"] = fallback["perturbation_direction"]
    card["columns"] = fallback["columns"]
    card["source"] = "model"
    return card


def normalise_genes(genes, aliases=None):
    """A model's or a user's perturbed-gene list as the organism's symbols:
    a list or a comma-separated string in, symbols the alias table knows out
    (every name kept when no table is given), duplicates dropped."""
    if isinstance(genes, str):
        genes = re.split(r"[,;]", genes)
    out = []
    for gene in genes or []:
        name = str(gene or "").strip()
        if not name:
            continue
        if aliases:
            name = aliases.get(name.upper())
            if not name:
                continue
        if name not in out:
            out.append(name)
    return out


def card_text(card):
    lines = ["%-13s %s" % (key, card.get(key, "")) for key in
             ("perturbation", "value", "axis", "baseline", "relevant", "columns")]
    genes = card.get("perturbed_genes") or []
    lines.append("%-13s %s" % ("perturbed", ("%s, %s" % (", ".join(genes), card.get("perturbation_direction", "unknown")))
                               if genes else "no gene named"))
    lines.append("%-13s %s" % ("system", card.get("system") or "not stated"))
    return "\n".join(lines)
