import re
import difflib
import unicodedata
from src.conf.serverconf import AI_VERIFICATION_FUZZY_THRESHOLD

# ---------------------------------------------------------------------------
# Legacy verification (kept for backward compat)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# V2 verification — [N] citation format with fuzzy text matching
# ---------------------------------------------------------------------------

# Heading level, trailing colon, surrounding bold and case all vary between
# generations, so every reader of the References section matches the section
# rather than one exact spelling. Shared so the readers cannot drift apart:
# render_references_section cuts the body at this heading and
# parse_references_section reads the entries after it, and a section only one of
# them recognises is worse than one neither does.
_REFERENCES_HEADING_RE = re.compile(
    r'^\s*(?:#{1,6}\s*)?\**\s*references\s*\**\s*:?\s*$',
    re.MULTILINE | re.IGNORECASE)

# A reference entry starts at the left margin with its own [N] label.
_REFERENCE_ENTRY_RE = re.compile(r'^\[(\d+)\]', re.MULTILINE)

# redact_unverified_v2 appends its note after the section; it belongs to the
# report, not to whichever entry happens to be printed last.
_TRAILING_NOTE_RE = re.compile(r'^>\s*\*\*Note:\*\*', re.MULTILINE)

# Multi-citation markers, the academic style the model falls back into no
# matter what the prompt says: "[17, 18]", "[17;18]", "[17-19]". Every reader
# in this module matches single "[N]" markers, so an unnormalised multi-marker
# is invisible to all of them at once: render_references_section drops refs 17
# and 18 from the section ("not cited"), redaction cannot remove the sentence,
# renumbering skips the marker, and the shipped report ends with a body
# citation pointing at entries that are not there -- which the reader sees and
# no check does. Numbers are capped at two digits so a span of years
# ("[2010-2015]") or any other bracketed figure is left alone, and a marker
# directly followed by "(" is a Markdown link, not a citation.
_MULTI_CITATION_RE = re.compile(r'\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})+)\](?!\()')
_RANGE_CITATION_RE = re.compile(r'\[(\d{1,2})\s*[-–—]\s*(\d{1,2})\](?!\()')


def normalize_citation_markers(text):
    """Split "[17, 18]" and "[17-19]" into "[17], [18](, [19])".

    Idempotent -- single markers are untouched -- so it is applied at the
    entry of every function that reads citation markers rather than trusting
    each call site to remember it.
    """
    if not text:
        return text

    def _split_range(match):
        start, stop = int(match.group(1)), int(match.group(2))
        # An inverted or implausibly wide "range" is a figure, not a citation.
        if stop <= start or stop - start > 20:
            return match.group(0)
        return ", ".join("[%d]" % n for n in range(start, stop + 1))

    def _split_list(match):
        numbers = re.split(r'\s*[,;]\s*', match.group(1))
        return ", ".join("[%s]" % n for n in numbers)

    text = _RANGE_CITATION_RE.sub(_split_range, text)
    return _MULTI_CITATION_RE.sub(_split_list, text)

# An inline PMID mention, in the forms the model actually writes when it
# cites by identifier instead of by marker: "(PMID 42565800)", "PMID: 42565800",
# "PMIDs 42505068 and 42371798", "(PMID: 1; PMID: 2)". One match covers a whole
# group so the connective words go with the numbers it joins.
# The parentheses are consumed only as a pair, so "(see PMID 1)" keeps its
# closing bracket while "(PMID 1)" loses both.
_PMID_LIST = r'\d{5,9}(?:\s*(?:,|;|/|&|and)\s*(?:PMIDs?\s*:?\s*#?\s*)?\d{5,9})*'
_PMID_MENTION_RE = re.compile(
    r'\(\s*PMIDs?\s*:?\s*#?\s*(?P<a>' + _PMID_LIST + r')\s*\)'   # "(PMID 1)", "(PMIDs 1 and 2)"
    r'|\bPMIDs?\s*:?\s*#?\s*(?P<b>' + _PMID_LIST + r')',            # bare "PMID 1", "PMIDs 1, 2"
    re.IGNORECASE)


def resolve_pmid_mentions(text, paper_index):
    """Rewrite inline PMID mentions of retrieved papers as [N] markers.

    Every reader of citations here matches "[N]" in the body of the report:
    quote collection, rendering, verification, renumbering. When the model
    cites a paper by its PMID instead -- which it did 90 times in one live
    synthesis, keeping "[N]" for a bibliography of its own -- all of that
    support is invisible and the report ships with no references. The mapping
    PMID -> ref_index is exact and known, so the mention is rewritten rather
    than the model re-asked.

    Only PMIDs that were retrieved are converted; an unknown PMID stays as
    text, where verification will treat the sentence as uncited rather than
    manufacture a reference to a paper nobody fetched. The model's own
    References section (if any) is left as written: render_references_section
    replaces it wholesale, and rewriting it would only create body-looking
    markers below the heading. Idempotent, and a no-op without a paper index.
    """
    if not text or not paper_index:
        return text
    by_pmid = {}
    for idx, paper in paper_index.items():
        pmid = str((paper or {}).get("pmid") or "").strip()
        if pmid:
            by_pmid[pmid] = idx
    if not by_pmid:
        return text

    ref_match = _REFERENCES_HEADING_RE.search(text)
    body, tail = ((text[:ref_match.start()], text[ref_match.start():])
                  if ref_match else (text, ""))

    def _swap(match):
        numbers = re.findall(r'\d{5,9}', match.group("a") or match.group("b") or "")
        known = [by_pmid[n] for n in numbers if n in by_pmid]
        if not known:
            return match.group(0)
        if len(known) == len(numbers):
            return ", ".join("[%d]" % i for i in known)
        # A mixed group: convert what we have, keep the rest as text.
        parts = ["[%d]" % by_pmid[n] if n in by_pmid else "PMID %s" % n
                 for n in numbers]
        return ", ".join(parts)

    body = _PMID_MENTION_RE.sub(_swap, body)
    # "axis (PMID 1) provides" became "axis [3] provides" -- the surrounding
    # spacing was the parenthesis's; the marker wants a single space each side.
    body = re.sub(r'[ \t]+(\[\d+\])', r' \1', body)
    body = re.sub(r'(\[\d+\])[ \t]+([.,;:)])', r'\1\2', body)
    return body + tail


def count_body_citations(report_text, valid_indices):
    """The set of retrieved-paper markers cited in the BODY of the report.

    A model that lists its papers under a References heading and never cites
    them in the text has cited nothing: the section is discarded by
    render_references_section, and only body markers survive to be verified.
    Both the citation top-up's gate and its acceptance test read this, so
    they cannot disagree with each other -- they did once (PR #28 fixed the
    gate alone), and a top-up whose whole contribution was a bibliography was
    accepted as having "added 56 citations".
    """
    text = normalize_citation_markers(report_text or "")
    ref_match = _REFERENCES_HEADING_RE.search(text)
    body = text[:ref_match.start()] if ref_match else text
    return {int(n) for n in re.findall(r'\[(\d+)\]', body)} & set(valid_indices)


def verify_report_v2(report_text, gene_whitelist, unique_papers, job_instance):
    """Verify a report using [N] citation format.

    Returns {
        "score": float,
        "failed_citations": [{"ref_index", "reason", "cited_text", "claim_sentence"}],
        "gene_accuracy": float,
        "ref_accuracy": float,
    }
    """
    report_text = normalize_citation_markers(report_text)
    paper_index = {p["ref_index"]: p for p in unique_papers}
    valid_ref_indices = set(paper_index.keys())

    # 1. Parse references section
    parsed_refs = parse_references_section(report_text)

    # 2. Check [N] indices in body text
    body_indices = set(int(m) for m in re.findall(r'\[(\d+)\]', report_text))
    invalid_indices = body_indices - valid_ref_indices

    failed_citations = []

    # Flag invalid reference indices
    for idx in invalid_indices:
        failed_citations.append({
            "ref_index": idx,
            "reason": f"Reference [{idx}] not in available papers",
            "cited_text": "",
            "claim_sentence": "",
            "actual_text": "",
            "suggested_fix": "",
        })

    # 3. Fuzzy-match cited text against paper sections
    for ref_entry in parsed_refs:
        ref_idx = ref_entry["ref_index"]
        cited_text = ref_entry.get("cited_text", "").strip()

        if ref_idx not in valid_ref_indices:
            continue  # already flagged above

        if not cited_text:
            failed_citations.append({
                "ref_index": ref_idx,
                "reason": f"Reference [{ref_idx}] has no Cited Text",
                "cited_text": "",
                "claim_sentence": ref_entry.get("claim_sentence", ""),
                "actual_text": "",
                "suggested_fix": "",
            })
            continue

        paper = paper_index[ref_idx]
        # Concatenate all sections for searching
        all_text = " ".join(
            text for text in paper.get("sections", {}).values() if text
        )

        if not all_text:
            # Only abstract available — use it
            all_text = paper.get("abstract", "")

        if not _fuzzy_contains(all_text, cited_text, AI_VERIFICATION_FUZZY_THRESHOLD):
            failed_citations.append({
                "ref_index": ref_idx,
                "reason": f"Cited text for [{ref_idx}] not found in paper (fuzzy match failed)",
                "cited_text": cited_text,
                "claim_sentence": ref_entry.get("claim_sentence", ""),
                "actual_text": "",
                "suggested_fix": "",
            })

    # 4. Gene whitelist check (informational)
    mentioned_genes = _extract_gene_mentions(report_text)
    valid_genes = sum(1 for g in mentioned_genes if g.upper() in gene_whitelist)
    gene_accuracy = valid_genes / max(len(mentioned_genes), 1)

    # 5. Compute ref accuracy
    total_refs = len(parsed_refs) + len(invalid_indices)
    failed_count = len(failed_citations)
    ref_accuracy = (total_refs - failed_count) / max(total_refs, 1)

    # 6. P-value check (informational)
    pval_issues = _check_pvalues(report_text, job_instance)

    score = 0.5 * ref_accuracy + 0.2 * gene_accuracy + 0.3 * (1 - min(len(pval_issues) / 5, 1))

    # Whether the quotations could be checked at all, kept separate from
    # whether they passed. Roughly two reports in five carry inline [N] markers
    # but no References section with Cited Text blocks -- there is then nothing
    # to ground the claims against, so no citation is examined and
    # failed_citations comes back empty. That is indistinguishable from "all
    # citations verified" unless it is stated, which is exactly the confusion
    # the "### References" parsing bug used to cause silently.
    body_cites_something = bool(body_indices)
    return {
        "score": round(score, 2),
        "failed_citations": failed_citations,
        "gene_accuracy": round(gene_accuracy, 2),
        "ref_accuracy": round(ref_accuracy, 2),
        "references_section_found": bool(parsed_refs),
        "citations_checked": len([r for r in parsed_refs if r.get("cited_text")]),
        "quotations_unverifiable": body_cites_something and not parsed_refs,
    }


# -- structure-preserving redaction ---------------------------------------
#
# Redaction used to split the body on sentence boundaries and rejoin with " ".
# The split pattern consumes the whitespace after a full stop, and in markdown
# that whitespace is usually the newline before a heading or a bullet, so every
# paragraph break that followed a sentence collapsed and any heading glued to a
# removed sentence disappeared with it. A report with one failed citation came
# back as a wall of text with its sections missing -- and since the frontend
# renders this as markdown, the damage was visible to every reader.
#
# The rule now: only whole sentences that cite a failed index are removed, and
# nothing else in the document moves.

_HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s')
_FENCE_RE = re.compile(r'^\s*(```|~~~)')
_TABLE_RE = re.compile(r'^\s*\|')


def _is_structural(line):
    """A line that carries layout rather than prose, and must survive verbatim."""
    return (not line.strip()
            or _HEADING_RE.match(line)
            or _TABLE_RE.match(line)
            or line.strip() in ("---", "***", "___"))


# A run of citations written as one list: "[2]", "[2], [4]", "[2], [9] and [4]".
_CITATION_RUN = re.compile(r'\[\d+\](?:\s*(?:,\s*and|,|and|&)\s*\[\d+\])*')


def _prune_citation_run(run, bad_indices):
    """Re-render a citation list with the failed indices dropped.

    Editing the markers in place and patching the punctuation afterwards does
    not work: "from [1] and [9] agrees" becomes "from [1] and agrees", and
    "rose [9], [2]" becomes "rose, [2]". Treating the whole list as one token
    and writing it out again from the survivors avoids inventing those.
    """
    indices = [int(n) for n in re.findall(r'\[(\d+)\]', run)]
    seen, kept = set(), []
    for i in indices:
        if i not in bad_indices and i not in seen:
            seen.add(i)
            kept.append(i)
    if not kept:
        return ""
    if len(kept) == 1:
        return "[%d]" % kept[0]
    return "%s and [%d]" % (", ".join("[%d]" % i for i in kept[:-1]), kept[-1])


def _tidy_after_marker_removal(sentence, bad_indices):
    """Drop the failed citations from every list in the sentence, then close up."""
    sentence = _CITATION_RUN.sub(
        lambda m: _prune_citation_run(m.group(0), bad_indices), sentence)
    sentence = re.sub(r'\s+(?=[.!?,;:])', '', sentence)   # " ." -> "."
    sentence = re.sub(r'\(\s*\)', '', sentence)            # "()" left behind
    sentence = re.sub(r'\s{2,}', ' ', sentence)
    return sentence


def strip_markers(report_text, refs):
    """Remove [N] markers for `refs` WITHOUT touching any sentence.

    The difference from redact_unverified_v2 matters. Redaction removes the
    sentence when its last citation goes, because a claim with no support left
    should not ship. This does not: it is for markers that were ATTACHED to
    prose which already stood on its own, where taking the marker back restores
    the sentence exactly as it was.

    That is the top-up's situation precisely. It bolts [N] onto finished
    sentences, and 40-50% of those citations then fail -- so pulling an
    unsupportable one back before the gate sees it costs nothing at all, while
    letting it through costs the sentence.
    """
    refs = {int(r) for r in (refs or [])}
    if not refs:
        return report_text, 0
    before = len(re.findall(r"\[(\d+)\]", report_text))
    parts = re.split(r"((?<=[.!?])\s+)", report_text)
    out = []
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if any("[%d]" % r in sentence for r in refs):
            sentence = _tidy_after_marker_removal(sentence, refs)
        out.append(sentence)
        if i + 1 < len(parts):
            out.append(parts[i + 1])
    cleaned = "".join(out)
    return cleaned, before - len(re.findall(r"\[(\d+)\]", cleaned))


def _redact_sentences(text, bad_patterns, bad_indices=None):
    """Remove failed citations, and the sentence only when nothing is left.

    Splitting with a capturing group keeps every separator, so the surviving
    sentences are rejoined with exactly the whitespace that was between them --
    newlines included. When a removed sentence was followed by a paragraph
    break, that break is kept, otherwise the paragraphs either side would merge.

    A sentence that ALSO cites something verified keeps its place, with only the
    failed marker taken out. Deleting it outright destroys evidence that passed:
    39% of citation-bearing sentences in the stored reports carry two or more
    citations, and one carried ten -- so one bad index could take nine good ones
    with it. Measured on a live run, an agent submitted 11 citations it had
    checked and grounded and the gate returned 6.
    """
    parts = re.split(r'((?<=[.!?])\s+)', text)
    kept, removed, dropped = [], 0, 0
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        separator = parts[i + 1] if i + 1 < len(parts) else ""
        hits = [bp for bp in bad_patterns if bp in sentence]
        if sentence.strip() and hits:
            here = {int(n) for n in re.findall(r'\[(\d+)\]', sentence)}
            survivors = here - set(bad_indices or [])
            removed += len(hits)
            if survivors:
                # keep the claim; it still stands on a citation that verified
                kept.append(_tidy_after_marker_removal(sentence,
                                                       set(bad_indices or [])))
                if separator:
                    kept.append(separator)
                continue
            dropped += 1                     # the CLAIM went, not just a marker
            if "\n" in separator and kept:
                kept.append(separator)          # keep the block boundary
        else:
            kept.append(sentence)
            if separator:
                kept.append(separator)
    return "".join(kept), removed, dropped


def _drop_orphan_headings(lines):
    """Remove a heading whose section lost all of its prose to redaction.

    A heading left standing over nothing reads as a rendering failure. A heading
    is orphaned only when everything down to the next heading of the same or a
    higher level is blank -- a parent heading whose subsections still have text
    is kept.
    """
    levels = [len(_HEADING_RE.match(l).group(1)) if _HEADING_RE.match(l) else None
              for l in lines]
    drop = set()
    for i, level in enumerate(levels):
        if level is None:
            continue
        has_content = False
        for j in range(i + 1, len(lines)):
            other = levels[j]
            if other is not None and other <= level:
                break
            if other is None and lines[j].strip():
                has_content = True
                break
        if not has_content:
            drop.add(i)
    return [l for i, l in enumerate(lines) if i not in drop]


def _redact_body(body, bad_patterns, bad_indices=None):
    """Sentence-level redaction that leaves headings, lists and tables in place."""
    segments, prose, removed, dropped = [], [], 0, 0
    in_fence = False

    def flush():
        nonlocal removed, dropped
        if not prose:
            return
        cleaned, count, gone = _redact_sentences("\n".join(prose), bad_patterns,
                                                 bad_indices)
        removed += count
        dropped += gone
        if cleaned.strip():
            segments.append(cleaned.rstrip())
        prose.clear()

    for line in body.split("\n"):
        if _FENCE_RE.match(line):               # code fences pass through whole
            flush()
            in_fence = not in_fence
            segments.append(line)
        elif in_fence or _is_structural(line):
            flush()
            segments.append(line)
        else:
            prose.append(line)
    flush()

    lines = "\n".join(segments).split("\n")
    return "\n".join(_drop_orphan_headings(lines)), removed, dropped


def theme_conversion(retrieved_papers, cited_papers):
    """Themes that produced a retrieved paper vs themes that produced a CITED one.

    Both arms tag every paper with the pathway/theme its search was run for, so
    this is the one retrieval measure that is directly comparable between them.
    The agent arm's own `tags_searched` counts every search including the barren
    ones, which is a different and arm-specific question -- for a comparison the
    denominator has to be themes that actually brought literature back.

    Why it matters: the agent arm retrieves ~3x more papers than base and cites
    fewer, and until both arms report on the same denominator there is no way to
    say whether that is a retrieval problem or a writing one.

    Returns (themes_with_a_paper, themes_with_a_cited_paper).
    """
    def tags(papers):
        return {str(t).strip().lower()
                for paper in (papers or [])
                for t in (paper.get("pathways") or []) if str(t).strip()}
    retrieved, cited = tags(retrieved_papers), tags(cited_papers)
    return len(retrieved), len(cited & retrieved)


def quote_provenance(quotes, paper_index):
    """Where the SURVIVING quotes actually came from: abstract, or full text.

    Adoption and cost are answered for every tool now -- all nine at 100%, and
    the character bill says what each spends. Neither says which tool's output
    ends up cited, and that is the question that decides whether a tool earns
    its place.

    This answers it for the most expensive retrieval machinery in the pipeline.
    A quote found in the abstract was free: search_literature already fetched it.
    A quote found only deeper cost a full-text upgrade -- an NCBI/Europe PMC
    round trip, and read_paper's ~11 kB a run of context. If the surviving quotes
    are overwhelmingly abstract quotes, that machinery is being paid for and not
    used; if they are not, it is load-bearing and the cost is the price of
    grounding.

    Returns {"quotes_from_abstract": n, "quotes_from_full_text": n,
             "quotes_unlocatable": n}. Unlocatable is its own bucket rather than
    being folded into either: a quote the deterministic matcher cannot find in
    the paper at all is a different fact about the pipeline, and hiding it inside
    "full text" would flatter the machinery this measures.
    """
    from_abstract = from_full = unlocatable = 0
    for ref, quote in (quotes or {}).items():
        text = (quote or "").strip()
        if not text:
            continue
        paper = (paper_index or {}).get(ref) or {}
        abstract = paper.get("abstract") or ""
        sections = paper.get("sections") or {}
        deeper = "\n".join(str(v) for k, v in sections.items()
                            if k != "abstract" and v)
        if abstract and _fuzzy_contains(abstract, text):
            from_abstract += 1
        elif deeper and _fuzzy_contains(deeper, text):
            from_full += 1
        else:
            unlocatable += 1
    return {"quotes_from_abstract": from_abstract,
            "quotes_from_full_text": from_full,
            "quotes_unlocatable_here": unlocatable}


def score_topup_survival(stats, verification):
    """Price the top-up's bet: did the citations it added survive the gate?

    The top-up adds [N] markers to sentences that were already written and
    already stood on their own. That is a wager with asymmetric stakes: a
    marker that verifies buys one citation, and a marker that fails costs the
    ENTIRE sentence, because redact_unverified_v2 removes each claim along with
    its bad citation.

    stats["topup_added"] has only ever recorded the winning half. A stage that
    destroys more prose than it grounds is indistinguishable, in the archive,
    from one that grounds prose for free -- which is how 40 s of every run went
    unpriced. Records nothing when the top-up did not run.
    """
    added = stats.get("topup_added_refs")
    if not added:
        return
    failed = {fc.get("ref_index")
              for fc in (verification.get("failed_citations") or [])}
    stats["topup_added_failed"] = len(set(added) & failed)


# The claims a redaction actually destroyed, as opposed to the markers it took
# out. Kept beside the function rather than added to its return type, because
# every caller unpacks a 2-tuple and the count is a diagnostic, not a result.
_LAST_REDACTION = {"sentences_dropped": 0}


def last_sentences_dropped():
    """Claims destroyed by the most recent redaction in this process."""
    return _LAST_REDACTION.get("sentences_dropped", 0)


def redact_unverified_v2(report_text, failed_citations):
    """Remove sentences citing failed [N] indices and their References entries.

    Returns (cleaned_report, removed_count) where removed_count is the number of
    bad MARKERS taken out of the body plus the reference entries dropped -- not
    the number of claims lost. A sentence citing a failed paper twice counts
    twice; a sentence that keeps another verified citation counts too, although
    the claim survives intact.

    That distinction was blurred for a long time -- including in this project's
    own notes, which described the count as "sentences lost" and once reported
    "1 failed citation = 15 lost sentences" when it meant 15 markers. The number
    of claims actually destroyed is recorded separately as `sentences_dropped`.
    """
    report_text = normalize_citation_markers(report_text)
    if not failed_citations:
        return report_text, 0

    bad_indices = {fc["ref_index"] for fc in failed_citations}
    bad_patterns = {f"[{idx}]" for idx in bad_indices}

    # Split report into body and references
    ref_header_match = re.search(r'^### References\s*$', report_text, re.MULTILINE)

    if ref_header_match:
        body = report_text[:ref_header_match.start()]
        refs_section = report_text[ref_header_match.start():]
    else:
        body = report_text
        refs_section = ""

    # Remove body sentences that cite bad indices, leaving structure intact
    clean_body, removed, sentences_dropped = _redact_body(body, bad_patterns,
                                                          bad_indices)

    # Remove bad reference entries from References section
    _LAST_REDACTION["sentences_dropped"] = sentences_dropped
    if refs_section:
        ref_lines = refs_section.split("\n")
        clean_ref_lines = []
        skip_entry = False
        for line in ref_lines:
            # Check if this line starts a new reference entry
            ref_match = re.match(r'\[(\d+)\]', line.strip())
            if ref_match:
                idx = int(ref_match.group(1))
                skip_entry = idx in bad_indices
                if skip_entry:
                    removed += 1
                    continue
            elif skip_entry:
                # Skip continuation lines of a bad entry (indented lines)
                if line.startswith("    ") or line.strip().startswith("**Cited Text:**"):
                    continue
                else:
                    skip_entry = False
            clean_ref_lines.append(line)
        refs_section = "\n".join(clean_ref_lines)

    result = clean_body.rstrip()
    if refs_section.strip():
        result += "\n\n" + refs_section.rstrip()

    if removed:
        result += f"\n\n> **Note:** {removed} citation(s) with unverified references were removed."

    return result, removed


def _drop_uncited_references(report_text):
    """Remove reference entries the body never cites.

    Indices are collected from the whole document, so an entry whose citations
    all disappeared -- redacted, or dropped when the report was rewritten --
    kept its place in the list and was renumbered along with the rest. Measured
    over the stored reports, 11 of 43 carried at least one: one report listed 21
    references for 18 citations, the extra three being papers on unrelated
    cancers.

    That is not a cosmetic problem. The reference list is the reader's measure of
    how much evidence stands behind the report, and three of those twenty-one
    stood behind nothing.

    Conservative by design: it prunes only when at least one citation survives in
    the body, so a report whose citations were all removed keeps its section
    rather than being left with an empty heading.
    """
    header = re.search(r'^### References\s*$', report_text, re.MULTILINE)
    if not header:
        return report_text
    body, refs = report_text[:header.start()], report_text[header.start():]
    cited = {int(n) for n in re.findall(r'\[(\d+)\]', body)}
    if not cited:
        return report_text

    kept, dropping = [], False
    for line in refs.split("\n"):
        entry = re.match(r'\s*\[(\d+)\]', line)
        if entry:
            dropping = int(entry.group(1)) not in cited
            if dropping:
                continue
        elif dropping:
            # continuation lines of a dropped entry (indented, or its quote)
            if line.startswith("    ") or line.strip().startswith("**Cited Text:**"):
                continue
            dropping = False
        kept.append(line)
    return body + "\n".join(kept)


def renumber_citations(report_text):
    """Renumber [N] citations sequentially starting from [1].

    After redaction or synthesis, citations may have gaps (e.g., [7], [11], [18]).
    This function remaps them to [1], [2], [3]... consistently across body and References.

    Returns (renumbered_report, old_to_new_mapping).
    """
    report_text = normalize_citation_markers(report_text)
    report_text = _drop_uncited_references(report_text)
    # 1. Collect all [N] indices used in the report (body + references), in order of first appearance
    all_indices = []
    seen = set()
    for m in re.finditer(r'\[(\d+)\]', report_text):
        idx = int(m.group(1))
        if idx not in seen:
            all_indices.append(idx)
            seen.add(idx)

    if not all_indices:
        return report_text, {}

    # Check if already sequential starting from 1
    if all_indices == list(range(1, len(all_indices) + 1)):
        return report_text, {i: i for i in all_indices}

    # 2. Build old -> new mapping (preserve order of first appearance)
    old_to_new = {}
    for new_idx, old_idx in enumerate(all_indices, 1):
        old_to_new[old_idx] = new_idx

    # 3. Replace all [N] occurrences — use placeholder to avoid collision
    # e.g., if [2] -> [1] and [1] -> [3], direct replacement would corrupt
    result = report_text

    # First pass: replace [old] with unique placeholders
    for old_idx in sorted(old_to_new.keys(), reverse=True):
        # Use a placeholder that can't appear naturally in text
        placeholder = f"__CITE_PLACEHOLDER_{old_to_new[old_idx]}__"
        result = result.replace(f"[{old_idx}]", placeholder)

    # Second pass: replace placeholders with final [new] indices
    for new_idx in range(1, len(all_indices) + 1):
        result = result.replace(f"__CITE_PLACEHOLDER_{new_idx}__", f"[{new_idx}]")

    return result, old_to_new


def sort_references_section(report_text):
    """Print the References entries in the order of the labels they now carry.

    ``render_references_section`` emits entries in ascending index order and
    ``renumber_citations`` then rewrites every ``[N]`` marker in place, in order
    of first mention in the body. Neither step moves an entry, so the two
    compose into a section that is individually correct and collectively
    unreadable: rendered 2, 9, 10, 15, 22 and renumbered in body order, it
    prints as [1], [5], [3], [2], [4].

    Nothing downstream notices, because every marker still resolves to the right
    paper -- verification passes and the job reports done. Only the reader sees
    it, which is why this runs last, after redaction and renumbering have both
    had their say.

    Whole entries are moved and never relabelled, so each [N] keeps its own
    title, PMID and Cited Text. Anything printed after the entries -- the
    redaction note -- stays at the end where it belongs.
    """
    heading = _REFERENCES_HEADING_RE.search(report_text)
    if not heading:
        return report_text

    head, tail = report_text[:heading.end()], report_text[heading.end():]

    entries = list(_REFERENCE_ENTRY_RE.finditer(tail))
    if len(entries) < 2:
        return report_text

    note = _TRAILING_NOTE_RE.search(tail, entries[-1].end())
    entries_end = note.start() if note else len(tail)

    blocks = []
    for i, match in enumerate(entries):
        stop = entries[i + 1].start() if i + 1 < len(entries) else entries_end
        blocks.append((int(match.group(1)), tail[match.start():stop].strip("\n")))

    labels = [index for index, _ in blocks]
    if labels == sorted(labels):
        return report_text

    blocks.sort(key=lambda block: block[0])

    rebuilt = tail[:entries[0].start()] + "\n\n".join(text for _, text in blocks)
    trailer = tail[entries_end:].lstrip("\n")
    return head + rebuilt + ("\n\n" + trailer if trailer else "\n")


def parse_references_section(report_text):
    """Parse ### References section from a report.

    Returns list of {
        "ref_index": int,
        "cited_text": str,
        "claim_sentence": str,  # first body sentence citing this ref
        "author": str,
        "title": str,
    }
    """
    report_text = normalize_citation_markers(report_text)
    # Find References section.
    #
    # This used to require exactly "### References". The model writes
    # "## References", so the match failed and this returned [] on every real
    # report -- which silently disabled the whole citation check: the
    # verification loop in pipeline.py breaks immediately when there are no
    # citations, and verify_report_v2's fuzzy grounding pass iterates over
    # nothing. The stored result was failed_citations = 0, which reads as "all
    # citations verified" when in fact none were ever examined.
    ref_match = _REFERENCES_HEADING_RE.search(report_text)
    if not ref_match:
        return []

    body = report_text[:ref_match.start()]
    refs_text = report_text[ref_match.end():]

    # Parse each reference entry
    # Pattern: [N] Author "Title" Journal, Year.
    entries = []
    # Split by reference entries starting with [N]
    ref_pattern = re.compile(r'^\[(\d+)\]\s*(.+)', re.MULTILINE)
    matches = list(ref_pattern.finditer(refs_text))

    for i, m in enumerate(matches):
        ref_idx = int(m.group(1))
        header_line = m.group(2).strip()

        # Extract the block of text until the next reference entry
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(refs_text)
        block = refs_text[m.start():block_end]

        # Extract Cited Text
        cited_match = re.search(r'\*\*Cited Text:\*\*\s*"([^"]*)"', block)
        cited_text = cited_match.group(1) if cited_match else ""

        # Extract author and title from header line
        title_match = re.search(r'"([^"]+)"', header_line)
        title = title_match.group(1) if title_match else ""
        author = header_line.split('"')[0].strip().rstrip('.')

        # Find the claim sentence in the body that cites this ref.
        #
        # Least numeric first, matching how the quote was chosen upstream. A
        # reference is typically cited several times; taking the first
        # occurrence meant the quote could be gathered to support one sentence
        # and then verified against a different one -- usually a sentence full
        # of this dataset's own measurements, which no paper can corroborate.
        # The citation then failed for a mismatch we created ourselves.
        ref_tag = f"[{ref_idx}]"
        citing = [s.strip() for s in re.split(r'(?<=[.!?])\s+', body)
                  if ref_tag in s]
        claim_sentence = min(
            citing,
            key=lambda s: sum(c.isdigit() for c in s) / max(len(s), 1),
            default="")

        entries.append({
            "ref_index": ref_idx,
            "cited_text": cited_text,
            "claim_sentence": claim_sentence,
            "author": author,
            "title": title,
        })

    return entries


def render_references_section(report_text, paper_index, quotes):
    """Replace whatever the model wrote with a canonical References section.

    ``parse_references_section`` is the inverse of this function, and asking a
    model to hit its format by prose instruction does not work: across six
    measured runs the synthesis produced a parseable section exactly once. It
    omitted the heading entirely, or wrote ``**Cited Text:** foo`` without the
    double quotes the parser requires, or renumbered as it went. Each failure is
    silent -- verification then checks zero citations and the run still reports
    ``done``.

    So the model is no longer asked to format anything. Bibliographic metadata
    comes from ``paper_index``, which is ground truth; the model supplies only
    the one thing it alone knows -- which sentence it relied on -- via
    ``quotes`` (``{ref_index: cited_text}``), and that is what verification then
    checks against the real paper. Rendering is deterministic.

    Citation markers with no corresponding paper are dropped from the section,
    not invented: a reference to a paper we never retrieved cannot be verified,
    and emitting it would manufacture the appearance of support.

    Each entry also names what its quote was found in -- "abstract" or a
    full-text section -- so a reader can tell a citation grounded in a
    paper's Results from one grounded in the two sentences of its abstract.
    The label is computed here, where the quote and the paper's sections are
    both at hand, rather than asked of the model.

    Returns (new_report_text, rendered_ref_indices).
    """
    report_text = normalize_citation_markers(report_text)
    if not paper_index:
        return report_text, []

    # Drop any section the model wrote, keeping only the body before it.
    ref_match = _REFERENCES_HEADING_RE.search(report_text)
    body = report_text[:ref_match.start()] if ref_match else report_text
    body = body.rstrip()

    cited = sorted({int(n) for n in re.findall(r'\[(\d+)\]', body)}
                   & set(paper_index.keys()))
    if not cited:
        return body, []

    lines = ["", "### References", ""]
    for idx in cited:
        paper = paper_index[idx] or {}
        author = (paper.get("first_author") or paper.get("author") or "").strip()
        if author and not author.endswith("et al."):
            author = "%s et al." % author
        title = (paper.get("title") or "").strip().rstrip(".")
        journal = (paper.get("journal") or "").strip()
        year = str(paper.get("year") or "").strip()
        pmid = str(paper.get("pmid") or "").strip()

        header = "[%d] " % idx
        if author:
            header += "%s " % author
        header += '"%s."' % title if title else '"Untitled."'
        tail = ", ".join(x for x in (journal, year) if x)
        if tail:
            header += " %s." % tail
        if pmid:
            header += " PMID: %s" % pmid
        lines.append(header)

        quote = (quotes or {}).get(idx) or (quotes or {}).get(str(idx)) or ""
        # Collapse whitespace and strip embedded double quotes: the parser reads
        # the quote as a "..."-delimited field, so an inner quote truncates it.
        quote = re.sub(r'\s+', ' ', str(quote)).replace('"', "'").strip()
        if quote:
            lines.append('    **Cited Text:** "%s"' % quote)
            lines.append('    *Cited from: %s*' % _quote_source(paper, quote))
        lines.append("")

    return body + "\n" + "\n".join(lines), cited


def _quote_source(paper, quote):
    """Where a quote was found: "abstract" or "full text (results)".

    Located by matching rather than recorded at extraction time, because the
    quote may have been carried forward across a correction rewrite and the
    paper's sections are the one ground truth both stages share. When the
    quote matches nowhere -- verification will flag it -- the label falls back
    to what was available to search, which is still true.
    """
    sections = paper.get("sections") or {}
    abstract = sections.get("abstract") or paper.get("abstract") or ""
    if quote and abstract and _fuzzy_contains(abstract, quote):
        return "abstract"
    for name, text in sections.items():
        if name == "abstract" or not text:
            continue
        if quote and _fuzzy_contains(text, quote):
            return "full text (%s)" % name
    return "full text" if paper.get("full_text_available") else "abstract"


# ---------------------------------------------------------------------------
# Fuzzy text matching
# ---------------------------------------------------------------------------

def _fuzzy_contains(haystack, needle, threshold=None):
    """3-tier matching: exact substring → normalized substring → sliding-window fuzzy.

    Args:
        haystack: The full paper text to search in.
        needle: The cited text to find.
        threshold: Minimum SequenceMatcher ratio for fuzzy match.

    Returns:
        True if needle is found in haystack at the given threshold.
    """
    if threshold is None:
        threshold = AI_VERIFICATION_FUZZY_THRESHOLD

    if not haystack or not needle:
        return False

    # Tier 1: Exact substring (fast path)
    if needle in haystack:
        return True

    # Tier 2: Normalized substring (lowercase, strip punctuation/extra whitespace)
    norm_haystack = _normalize_text(haystack)
    norm_needle = _normalize_text(needle)

    if norm_needle and norm_needle in norm_haystack:
        return True

    # Tier 3: Sliding-window SequenceMatcher
    if len(norm_needle) < 20:
        # Too short for reliable fuzzy matching
        return False

    needle_len = len(norm_needle)
    best_ratio = 0.0

    # Slide window over haystack in steps of needle_len/3
    step = max(needle_len // 3, 10)
    for start in range(0, max(1, len(norm_haystack) - needle_len + 1), step):
        window = norm_haystack[start:start + needle_len + needle_len // 4]
        ratio = difflib.SequenceMatcher(None, norm_needle, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
        if best_ratio >= threshold:
            return True

    return best_ratio >= threshold


def _normalize_text(text):
    """Lowercase, strip punctuation and collapse whitespace for fuzzy comparison."""
    text = text.lower()
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    # Remove punctuation except alphanumeric and whitespace
    text = re.sub(r'[^\w\s]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Common words and abbreviations that look like gene symbols but aren't
_COMMON_WORDS = frozenset({
    # English words
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER", "WAS",
    "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "DID", "GET", "LET", "SAY", "SHE", "TOO", "USE",
    "KEY", "ALSO", "BEEN", "CALL", "COME", "EACH", "FIND", "HAVE", "INTO",
    "MADE", "MANY", "MOST", "MUCH", "MUST", "NAME", "ONLY", "OVER", "SUCH",
    "TAKE", "THAN", "THAT", "THEM", "THEN", "THIS", "VERY", "WHEN", "WILL",
    "WITH", "FROM", "SOME", "WHAT", "MORE", "WERE", "HERE", "JUST", "LIKE",
    "THESE", "THOSE", "OTHER", "WHICH", "THEIR", "THERE", "ABOUT", "WOULD",
    "COULD", "BEING", "AFTER", "BELOW", "ABOVE", "BASED", "GIVEN", "SHOWN",
    "RESULTS", "TABLE", "FIGURE", "HOWEVER", "PATHWAY", "PATHWAYS",
    "ABSTRACT", "METHODS", "ANALYSIS", "BETWEEN", "THROUGH", "BECAUSE",
    "OVERALL", "SUGGEST", "SEVERAL", "FURTHER", "KNOWN", "FOUND", "USING",
    "WHILE", "WHERE", "SINCE", "NOTED", "LEVEL", "LEVELS", "GENES", "STUDY",
    "DATA", "TYPE", "ROLE", "CELL", "HIGH", "BOTH", "WELL", "THUS", "ALSO",
    "FIRST", "SECOND", "THIRD", "NOTE", "UPON", "NEXT", "ONLY", "DONE",
    # Scientific/bioinformatics abbreviations
    "DNA", "RNA", "ATP", "GTP", "ADP", "NAD", "FAD", "SAM", "UDP", "AMP",
    "PCR", "SNP", "UTR", "CDS", "ORF", "TSS", "TTS",
    "FC", "DE", "FDR", "LOG", "SD", "SE", "CI", "OR", "HR", "RR",
    "GO", "BP", "MF", "CC",  # Gene Ontology terms
    "KEGG", "MAPK", "MTOR", "AMPK", "VEGF", "ERBB",  # Pathway names (not individual genes)
    "MHC", "TCR", "BCR", "TLR", "NLR", "RIG",  # Receptor family names
    "TNF", "TGF", "EGF", "FGF", "IGF", "NGF", "PDGF", "CSF",  # Growth factor families
    "AGE", "RAGE", "SASP",  # Biological process abbreviations
    "HPV", "HIV", "HBV", "HCV", "HSV", "HTLV", "EBV", "CMV",  # Virus names
    "NK", "DC", "TH", "TH1", "TH2", "TH17", "TREG",  # Immune cell types
    "ER", "PM", "ECM",  # Cellular compartments
    "WNT", "JAK", "STAT", "RAS", "PI3K", "AKT", "NOTCH",  # Signaling pathway names
    "ATAC", "CHIP", "HIC",  # Assay types
    "H2", "H3", "H4", "K4", "K9", "K27", "K36",  # Histone marks
    "D1", "D2", "D3", "G1", "G2", "S1", "S2", "M1", "M2",  # Cell cycle / numbered terms
    "Q1", "Q2", "Q3", "Q4",  # Quantile labels
    "K1", "K2", "K3",  # Cluster labels
    "VS", "WT", "KO", "KD", "OE", "CTL", "CTRL",  # Experimental terms
    "PMID", "DOI", "REF",  # Citation terms
    "IL", "IFN", "CCL", "CXCL", "CCR", "CXCR",  # Cytokine/chemokine families
    "MM", "BP", "KB", "MB", "GB",  # Units
    "PC", "PCA", "UMAP", "TSNE",  # Analysis methods
    "DNase", "RNase",  # Enzyme types
    "GNRH", "GABA", "NMDA", "AMPA",  # Neurotransmitter terms
})

def _extract_gene_mentions(text):
    """Extract likely gene symbols — require at least 2 uppercase chars and filter common words."""
    candidates = set(re.findall(r'\b([A-Z][A-Z0-9][A-Za-z0-9]{0,6})\b', text))
    return [g for g in candidates if g.upper() not in _COMMON_WORDS]

def _check_pvalues(text, job_instance):
    issues = []
    matches = re.findall(r'p[\s-]*(?:value)?[\s=<]*(\d+\.?\d*(?:e[+-]?\d+)?)', text, re.IGNORECASE)
    # Multi-condition analyses store one p-value per condition rather than a
    # scalar, so `f"{val:.4f}"` raises
    #   TypeError: unsupported format string passed to list.__format__
    # and the whole verification pass dies. Every condition's value is a
    # legitimate figure for the report to cite, so all of them are registered.
    actual_pvals = {}
    for pw in job_instance.getMatchedPathways().values():
        for method, val in (pw.combinedSignificancePvalues or {}).items():
            candidates = val if isinstance(val, (list, tuple)) else [val]
            for value in candidates:
                if isinstance(value, (int, float)):
                    actual_pvals[f"{value:.4f}"] = pw.name
    for claimed in matches[:10]:
        try:
            claimed_f = float(claimed)
            if not any(abs(claimed_f - float(actual)) / max(float(actual), 1e-10) < 0.5
                       for actual in actual_pvals):
                issues.append(f"Claimed p-value {claimed} doesn't match any pathway")
        except ValueError:
            pass
    return issues
