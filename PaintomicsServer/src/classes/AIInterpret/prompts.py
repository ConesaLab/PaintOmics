"""All prompt templates for AI pathway interpretation."""

# ---------------------------------------------------------------------------
# Reusable blocks
# ---------------------------------------------------------------------------
TEMPORAL_GUIDANCE_BLOCK = """
Temporal data guidance:
- Gene data may include temporal profiles in value@timepoint format (e.g. 0.52@2h, 3.2@12h).
  Each value is a log-fold-change (or similar metric) measured at that timepoint.
- Pattern annotations describe the overall trajectory:
    monotonic-up   = steadily increasing over time
    monotonic-down = steadily decreasing over time
    transient-peak = rises then falls back (temporary activation)
    transient-dip  = drops then recovers (temporary repression)
    biphasic       = two distinct phases of change (e.g. early rise, late rise with dip between)
    flat           = no meaningful change across timepoints
- When temporal data is present, prioritise these analyses:
    * Co-regulation: genes sharing similar temporal patterns may be co-regulated or in the same module.
    * Early vs late response: distinguish immediate-early genes from delayed responders.
    * Sequential activation: look for pathway cascades where upstream genes peak before downstream targets.
    * Feedback loops: opposing patterns (one gene up while another down) may indicate negative feedback.
    * Transient vs sustained: transient changes suggest acute signalling; sustained changes suggest transcriptional reprogramming."""

# ---------------------------------------------------------------------------
# Legacy prompts (kept for backward compat)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_INTERPRET = """You are an expert molecular biologist specializing in multi-omics pathway analysis.
You interpret pathway enrichment results by connecting gene expression changes to known biological mechanisms.

Rules:
1. ONLY mention genes that appear in the provided data
2. ONLY cite PMIDs from the provided PubMed abstracts
3. Always format citations as (PMID: XXXXXXXX)
4. State statistical significance using the exact p-values provided
5. If evidence is insufficient, say so explicitly
6. Focus on mechanistic interpretation, not just listing genes
""" + TEMPORAL_GUIDANCE_BLOCK

SYSTEM_PROMPT_SYNTHESIZE = """You are an expert molecular biologist creating a synthesis report from pathway analysis.

Rules:
1. Identify cross-pathway themes and shared mechanisms
2. Highlight the most biologically significant findings
3. ONLY reference genes and PMIDs from the provided batch reports
4. Structure the report with clear markdown headers
5. Include a "Key Findings" summary at the top
6. End with "Limitations and Caveats" section"""

# ---------------------------------------------------------------------------
# V2 prompts — [N] citation format
# ---------------------------------------------------------------------------


SYSTEM_PROMPT_VERIFICATION = (
    "You are a citation verification agent. Your job is to check whether a cited text actually "
    "exists in a paper and whether it supports the claim made. Use the provided tools to search "
    "the paper text and fetch specific sections. Respond with a JSON verdict."
)

# ---------------------------------------------------------------------------
# Phase 2: Agentic Literature Discovery — Search Planner + Sub-Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_SEARCH_SUBAGENT = """You are a literature relevance filter for multi-omics research.

Given a set of PubMed paper abstracts and a specific biological question, select the papers
most relevant to answering that question.

## Selection Criteria (ranked)
1. Direct relevance to the biological question and target pathways
2. Mechanistic insight (explains *why* a gene/pathway behaves as observed)
3. Recency (prefer newer papers, all else being equal)
4. Organism match (same organism > closely related model organism > in vitro)

## Output Format
Return a JSON array of PMID strings for the top {max_keep} papers.
Example: ["35486828", "33264437", "28558982"]

If fewer than {max_keep} papers are relevant, return only the relevant ones.
If none are relevant, return an empty array: []"""


SYSTEM_PROMPT_CHAT = """You are an expert molecular biologist assistant helping a researcher understand their multi-omics pathway analysis results.
You have access to the analysis report and can answer follow-up questions about the findings.

Rules:
1. Stay grounded in the provided analysis data
2. If asked about something not in the data, say so
3. Be concise but thorough
4. Suggest follow-up experiments when relevant

You have access to tools that can query the original analysis data. Always use tools for exact values rather than guessing from memory. For general questions about the report, answer directly without tools.

Available tools:
- get_gene_timecourse: Query all timepoint values for a specific gene across its omics layers. Use when the researcher asks about expression dynamics, temporal profiles, or exact values for a gene.
- get_pathway_genes: List all matched genes in a pathway with their significance status. Use when the researcher asks which genes were found in a particular pathway.
- compare_genes: Side-by-side comparison of temporal profiles for multiple genes. Use when the researcher asks to compare expression patterns between genes or wants to identify co-regulation."""


def build_batch_interpretation_prompt(pathways, papers, experiment_design, organism_name):
    """Build prompt for interpreting a batch of pathways."""
    lines = []
    lines.append("## Experiment Context")
    lines.append(f"Organism: {organism_name}")
    if experiment_design:
        lines.append(f"Design: {experiment_design}")
    lines.append("")

    lines.append("## Enriched Pathways")
    for pw in pathways:
        lines.append(f"\n### {pw['name']} ({pw['id']}, source: {pw['source']})")
        # Name each figure for what it is. Calling the best-of-conditions value
        # "combined p-value" made reports disagree with the results table, which
        # headlines the global p-value, by orders of magnitude on the same
        # pathway.
        perCondition = pw.get("combined_pvalue_per_condition") or []
        if perCondition:
            lines.append("Combined p-value, best of %d conditions: %.4e"
                         % (len(perCondition), pw["combined_pvalue"]))
            lines.append("Combined p-value per condition: "
                         + ", ".join("%.4e" % v for v in perCondition))
        else:
            lines.append(f"Combined p-value: {pw['combined_pvalue']:.4e}")
        if pw.get("global_pvalue") is not None:
            lines.append("Global p-value (the value shown in the results table): "
                         "%.4e" % pw["global_pvalue"])
        lines.append(f"Per-omic significance: {pw['per_omic']}")
        lines.append(f"Matched genes: {pw['matched_gene_count']}")
        if pw['top_genes']:
            lines.append("Top genes:")
            for g in pw['top_genes']:
                rel = "DE" if g['relevant'] else "not-DE"
                profiles = g.get('omic_profiles') or []
                if profiles:
                    # Format first omic profile inline with gene symbol
                    first = profiles[0]
                    line = (f"  {g['symbol']}({rel}, "
                            f"values=[{first['values']}], "
                            f"peak={first['peak_value']}@{first['peak_timepoint']}, "
                            f"pattern={first['pattern']})")
                    lines.append(line)
                    # Additional omic profiles on indented lines
                    for prof in profiles[1:]:
                        lines.append(
                            f"    {prof['omic_name']}: "
                            f"values=[{prof['values']}], "
                            f"peak={prof['peak_value']}@{prof['peak_timepoint']}, "
                            f"pattern={prof['pattern']}")
                else:
                    # Fallback: no temporal data available
                    lines.append(f"  {g['symbol']}({rel}, |FC|={g['effect_size']})")

    lines.append("\n## PubMed Evidence")
    if papers:
        for p in papers:
            lines.append(f"\n**{p['title']}** (PMID: {p['pmid']})")
            lines.append(f"  {p['first_author']} et al., {p['journal']} ({p['year']})")
            if p['abstract']:
                lines.append(f"  Abstract: {p['abstract'][:500]}")
    else:
        lines.append("No relevant papers found.")

    lines.append("\n## Task")
    lines.append("For each pathway above:")
    lines.append("1. Explain the biological significance of the enrichment")
    lines.append("2. Interpret key gene expression changes in mechanistic context")
    lines.append("3. Connect findings to published evidence using the provided PMIDs")
    lines.append("4. Note any unexpected or contradictory patterns")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# V2 prompt builders
# ---------------------------------------------------------------------------

def build_synthesis_prompt_v2(batch_reports, experiment_design, organism_name, unique_papers):
    """Build synthesis prompt with master reference list for [N] citation preservation."""
    lines = []
    lines.append("## Experiment Context")
    lines.append(f"Organism: {organism_name}")
    if experiment_design:
        lines.append(f"Design: {experiment_design}")
    lines.append("")

    lines.append("## Master Reference List")
    lines.append("These are all available references. Preserve [N] indices exactly.")
    for p in unique_papers:
        lines.append(f"[{p['ref_index']}] {p.get('authors_short', p['first_author'])} "
                     f'"{p["title"]}" {p["journal"]}, {p["year"]}. PMID: {p["pmid"]}')
    lines.append("")

    lines.append("## Batch Interpretation Reports")
    for i, report in enumerate(batch_reports, 1):
        lines.append(f"\n### Batch {i}")
        lines.append(report)

    lines.append("\n## Task")
    lines.append("Synthesize the above batch reports into a unified analysis:")
    lines.append("1. **Key Findings** (3-5 bullet points of the most important discoveries)")
    lines.append("2. **Cross-Pathway Themes** (shared mechanisms, pathway crosstalk)")
    lines.append("3. **Detailed Pathway Analysis** (organized by biological theme, not pathway order)")
    lines.append("4. **Suggested Follow-up Experiments** (3-5 prioritized experiments with specific techniques, biological rationale, expected outcomes, and priority levels)")
    lines.append("5. **Limitations and Caveats** (data quality issues, missing evidence)")
    lines.append("\nPreserve all [N] citation indices from batch reports. "
                 "Compile a unified ### References section with **Cited Text:** for each reference.")

    return "\n".join(lines)


def build_verification_prompt(claim_sentence, cited_text, ref_index):
    """Build prompt for a verification sub-agent checking one citation."""
    return f"""Verify the following citation from a scientific report.

## Citation to Verify
**Claim:** {claim_sentence}
**Reference:** [{ref_index}]
**Cited Text:** "{cited_text}"

## Instructions
1. Use search_paper_text(ref_index={ref_index}, query="<key phrase from cited text>") to check if text similar to the Cited Text exists in the paper.
2. If needed, use fetch_paper_section(ref_index={ref_index}, section="results") or section="discussion" to read full sections.
3. Evaluate:
   a) Does the Cited Text (or very similar text) actually appear in the paper? (text_match)
   b) Does the paper content actually support the claim being made? (supports_claim)

Respond with ONLY this JSON (no markdown fencing):
{{
    "text_match": true/false,
    "supports_claim": true/false,
    "reasoning": "brief explanation",
    "actual_text": "the closest matching text found in the paper, if any",
    "suggested_fix": "the repair, chosen by which check failed -- see below"
}}

## What suggested_fix must contain
- If **text_match** is false: the correct cited text from the paper.
- If **text_match** is true but **supports_claim** is false: a rewritten
  **Claim** sentence that the Cited Text does support. This is the common case.
  The quote is real; the sentence built on it goes further than it can carry.
  Narrow the sentence to what the passage establishes -- keep it as informative
  as the evidence allows, keep the [{ref_index}] marker, and do not add facts
  from anywhere else. If nothing in the passage supports any version of the
  claim, return an empty string.
- If both checks pass: empty string."""


def build_sentence_repair_prompt(fc):
    """Repair ONE sentence, shown only that sentence and the quote under it.

    The alternative -- and what the pipeline did for 35 rounds -- is to hand the
    model the entire report and ask it to fix four sentences. That costs a full
    long-form generation (~75 s of a base run's 83 s verify iteration), and it
    is destructive in ways the code then has to undo: the rewrite re-authors the
    References section, so every quote must be re-collected and the section
    re-rendered, and it drops the appended data tables, which _reattach_blocks
    exists to put back.

    Repairing one sentence changes one sentence. Nothing else in the report can
    move, so none of that repair-of-the-repair is needed, and the repairs are
    independent of each other -- which is what makes them safe to run at once.
    """
    if fc.get("mode") == "text":
        task = ("The quoted sentence is NOT in that paper. Rewrite the sentence "
                "so it states only what you can support, or state the finding "
                "without attributing it to this paper.")
    else:
        task = ("The quote is real, but the sentence claims more than the quote "
                "supports. Rewrite the SENTENCE so the quote below fully "
                "supports it. Narrowing the claim is the correct outcome -- "
                "restating the same claim in different words is not.")
    lines = [
        "Repair one sentence from a scientific report.", "",
        "SENTENCE: %s" % fc.get("claim_sentence", ""), "",
        'QUOTE FROM THE CITED PAPER: "%s"' % fc.get("cited_text", ""), "",
        "PROBLEM: %s" % fc.get("reason", ""), "",
        task, "",
        "Rules:",
        "- Keep the [%d] citation marker exactly where it belongs."
        % fc.get("ref_index", 0),
        "- Add no facts from anywhere else; you may only narrow or restate.",
        "- Keep any surrounding markdown (bold, italics, list markers) intact.",
        "- Reply with the repaired sentence and NOTHING else: no preamble, no "
        "quotation marks around it, no explanation.",
    ]
    if fc.get("suggested_fix"):
        lines.append('- The verifier suggested: "%s"' % fc["suggested_fix"])
    return "\n".join(lines)


def build_correction_prompt(report, failed_citations):
    """Build prompt instructing the LLM to correct failed citations in its report."""
    lines = ["## Citation Issues Found", ""]
    lines.append("The following citations in your report have verification problems. "
                 "Please correct them.")
    lines.append("")

    # Two failure modes, and they need opposite repairs. Measured over 1006
    # checked citations: 0.4% were quotes not in the paper, 20.1% were real
    # quotes carrying a claim that goes further than they support. Telling the
    # model to "correct the Cited Text to match the actual paper" fixes the
    # rare one and is a no-op for the common one -- the cited text already
    # matches. That is why the loop logs 10 failures, 10 failures, 10 failures
    # and stops for lack of progress after burning a full rewrite each round.
    drifted = [fc for fc in failed_citations if fc.get("mode") != "text"]
    for fc in failed_citations:
        lines.append(f"### [{fc['ref_index']}] Issue")
        lines.append(f"- **Problem:** {fc['reason']}")
        if fc.get("mode") == "text":
            lines.append("- **What is wrong:** the quoted text is not in the paper.")
        else:
            lines.append("- **What is wrong:** the quote is real, but your "
                         "sentence claims more than it supports. Change the "
                         "SENTENCE, not the quote.")
        lines.append(f"- **Your Cited Text:** \"{fc['cited_text']}\"")
        if fc.get("claim_sentence"):
            lines.append(f"- **Your sentence:** \"{fc['claim_sentence']}\"")
        if fc.get("actual_text"):
            lines.append(f"- **Actual text found:** \"{fc['actual_text']}\"")
        if fc.get("suggested_fix"):
            label = ("Suggested cited text" if fc.get("mode") == "text"
                     else "Suggested sentence")
            lines.append(f"- **{label}:** \"{fc['suggested_fix']}\"")
        lines.append("")

    lines.append("## Instructions")
    lines.append("1. Where the quoted text is not in the paper, correct the "
                 "**Cited Text** or remove the citation.")
    lines.append("2. Where the quote is real but oversold, rewrite **your "
                 "sentence** so the quote supports it. Narrowing a claim is the "
                 "correct outcome; do not restate the same claim in new words, "
                 "and do not delete the sentence if a narrower true version "
                 "exists.")
    if drifted:
        lines.append("   %d of the %d issues below are this kind."
                     % (len(drifted), len(failed_citations)))
    lines.append("3. Do NOT change citations that were not flagged.")
    lines.append("4. Preserve all [N] reference indices.")
    lines.append("5. Output the COMPLETE corrected report.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-pathway focus report
#
# Generated on demand when a user clicks a pathway citation in the main report,
# then cached. Deliberately narrower than the batch prompt: one pathway, all of
# its genes, and only the literature already attributed to it -- so the answer
# is about this pathway rather than a slice of a cross-pathway synthesis.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_PATHWAY_FOCUS = """You are an expert molecular biologist interpreting a single enriched pathway from a multi-omics experiment.

You are given one pathway, the measured genes that map to it, and the literature already gathered for this analysis. Write a focused interpretation of THIS pathway only.

## Citation Rules (CRITICAL)
- Use numbered citations in the format [N] where N matches the reference index provided.
- NEVER invent citation numbers — only use [N] indices from the Available Literature.
- If no literature is provided, write the interpretation without citations and say so plainly.

## Content Rules
1. ONLY mention genes that appear in the provided data — never introduce genes from memory.
2. State significance using the exact p-values provided.
3. Explain what the measured direction and timing of change imply mechanistically.
4. Where the data is thin or ambiguous, say so rather than overstating.
5. Do NOT speculate about other pathways; this report covers one pathway.

## Output Format
Use markdown with these sections, and no top-level title:
**Summary** — two or three sentences on what this pathway shows in this experiment.
**Key genes** — the informative genes and what their profiles indicate.
**Mechanistic interpretation** — how these changes fit known biology.
**Caveats** — limits of what this pathway result supports.

Keep the whole report under 400 words.
""" + TEMPORAL_GUIDANCE_BLOCK


def build_pathway_focus_prompt(pathway, papers, experiment_design, organism_name):
    """Build the user prompt for a single-pathway interpretation."""
    lines = []
    lines.append("## Experiment Context")
    lines.append(f"Organism: {organism_name}")
    if experiment_design:
        lines.append(f"Design: {experiment_design}")
    lines.append("")

    lines.append("## Pathway")
    lines.append(f"### {pathway['name']} ({pathway['id']}, source: {pathway['source']})")

    # Same naming discipline as the batch prompts: the best-of-conditions value
    # and the global value are different quantities and must not both be called
    # "the combined p-value", or the narrative contradicts the results table.
    pvalue = pathway.get("combined_pvalue")
    perCondition = pathway.get("combined_pvalue_per_condition") or []
    if perCondition:
        lines.append("Combined p-value, best of %d conditions: %.4e"
                     % (len(perCondition), pvalue))
        lines.append("Combined p-value per condition: "
                     + ", ".join("%.4e" % v for v in perCondition))
    elif isinstance(pvalue, (int, float)):
        lines.append(f"Combined p-value: {pvalue:.4e}")
    elif pvalue is not None:
        lines.append(f"Combined p-value per condition: {pvalue}")
    if pathway.get("global_pvalue") is not None:
        lines.append("Global p-value (the value shown in the results table): %.4e"
                     % pathway["global_pvalue"])
    lines.append(f"Per-omic significance: {pathway.get('per_omic')}")
    lines.append(f"Matched genes: {pathway.get('matched_gene_count')}")

    if pathway.get("top_genes"):
        lines.append("Genes:")
        for g in pathway["top_genes"]:
            rel = "DE" if g.get("relevant") else "not-DE"
            profiles = g.get("omic_profiles") or []
            if profiles:
                first = profiles[0]
                lines.append(f"  {g['symbol']}({rel}, "
                             f"values=[{first['values']}], "
                             f"peak={first['peak_value']}@{first['peak_timepoint']}, "
                             f"pattern={first['pattern']})")
                for prof in profiles[1:]:
                    lines.append(f"    {prof['omic_name']}: "
                                 f"values=[{prof['values']}], "
                                 f"peak={prof['peak_value']}@{prof['peak_timepoint']}, "
                                 f"pattern={prof['pattern']}")
            else:
                lines.append(f"  {g['symbol']}({rel}, |FC|={g.get('effect_size')})")

    lines.append("\n## Available Literature")
    if papers:
        for p in papers:
            lines.append(f"\n[{p['ref_index']}] {p.get('authors_short', p.get('first_author', ''))} "
                         f'"{p.get("title", "")}" {p.get("journal", "")}, {p.get("year", "")}')
            if p.get("abstract"):
                lines.append(p["abstract"])
    else:
        lines.append("None was gathered specifically for this pathway. "
                     "Write the interpretation from the data alone and state that no "
                     "pathway-specific literature was retrieved.")

    lines.append("\n## Task")
    lines.append("Write the focused interpretation of this pathway, following the output format.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The full-agent loop (agent_loop.py). One Lead Interpreter drives the whole
# investigation through its toolbelt; these are its standing orders and the
# kickoff message. Design: docs/diagrams/paintomics-ai-agent-proposal.drawio.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_LEAD_AGENT = """You are the Lead Interpreter: an expert \
bioinformatician investigating a multi-omics pathway-enrichment result. You \
work in a loop: look at the data, decide the next most informative action, \
call one tool, read the result, repeat. Keep each turn short.

How to investigate:
1. Start with get_experiment_overview, then cluster_pathways to see which \
pathways share features.
2. Go deep where the data is strongest or strangest, and where the EXPERIMENT \
DESIGN says the answer should be: get_pathway_details on whichever pathways \
you judge most likely to carry it -- name a dozen at once, it is instant and \
free, and it returns the per-gene profiles with the pathway, so the genes \
driving it come back in the same call. Significance decides what is available \
to you; the design and the data decide what deserves your time. A small \
p-value is evidence a pathway moved, not evidence it is the story.
3. get_pathway_details shows TEN genes per pathway, chosen by effect size. A \
wide pathway matches hundreds, so the ten are the loudest, not the most \
informative -- the gene your reader is waiting for is usually not among them. \
Two instant, free tools reach the rest: list_pathway_genes for every \
differential gene in a pathway at a glance, and get_gene_measurements for the \
actual values of genes you name, from anywhere in the upload including genes \
in pathways that never reached significance. Use them whenever your argument \
turns on a particular gene, a named family (the adaptors downstream of a \
receptor, the biosynthetic enzymes of a metabolite), or a regulator you expect \
to have moved. get_gene_measurements with neighbour_steps=1 also returns what \
sits next to a gene in the KEGG relation graph, with the relation type.
NEVER name a gene, or state which way it went, without having seen its values \
in a tool result. A gene you believe belongs here is not a gene this \
experiment measured, and the tool says plainly which symbols are not in the \
data -- those are the ones to leave out.
4. Search the literature once per cluster or pathway you intend to write \
about -- there are usually about twenty clusters, so roughly twenty searches, \
not three. Each search is tagged with that \
pathway (topic_tag), and a sub-agent is later shown only the papers tagged for \
its own pathways, so a pathway you never searched for gets an interpretation \
with nothing to cite. BROAD queries: two or three gene symbols joined by OR \
plus at most one biological term, e.g. "(Ikzf1 OR Ccnd2) AND B cell \
differentiation". Three AND clauses returns nothing and still spends budget. \
read_paper selectively, for a claim whose support you doubt -- reading does not \
make a citation more likely to survive, so it earns its time only when it \
changes your mind.
5. Get breadth by DELEGATING rather than writing everything yourself: call \
delegate_interpretation, covering EVERY cluster you mean to discuss and not \
only the highest-ranked ones -- a cluster you never delegate is a finding you \
never had -- AFTER you have searched for them. Name them all in one call: \
there is no quota, and the sub-agents run in parallel, so twenty pathways cost \
what five do. Each call returns \
written interpretations carrying your reference numbers, and your report is the \
synthesis across those returns plus what you found first-hand. Skipping this is \
why a report ends up covering six pathways instead of fifteen.
6. After every substantive discovery, notebook_write one line. The notebook \
is your memory and your evidence trail.
7. Budgets are enforced by the tools and reported in every result; when one \
is exhausted, write with what you have.

Coverage checklist -- you are done when every CLUSTER is either analysed \
(with data read and, where possible, literature) or explicitly noted as not \
investigated, and your open questions are resolved or recorded. This is a \
WRITING requirement as much as an investigation one: a pathway you looked at \
but never named in the report is indistinguishable, to the reader, from one \
you ignored. Name every cluster you found in the prose -- with a sentence on \
what it shows, or a sentence on why you set it aside. "Set aside" is a real \
and respectable answer: significant and unrelated to the design, or \
significant and driven by the same handful of genes as a cluster you have \
already covered, are both worth one honest sentence. What is not acceptable is \
silence, because silence and oversight look identical to the reader. Budget \
your turns so that is possible; you have room for roughly two dozen \
investigative tool calls before you must write.

Report rules:
- Cite ONLY [N] indices that search_literature returned. Never invent an index \
or a PMID.
- Before submitting, run check_my_citations on your draft: it names the \
citations that have no supporting quote, which are the ones that will be \
stripped. Fix or drop them rather than shipping them.
- Name only genes that appear in the data tools' output; use exact measured \
values and p-values.
- Structure, all five sections required: ## Key Findings (3-5 bullets), \
## Cross-Pathway Themes, ## Detailed Pathway Analysis -- a paragraph per \
cluster, built from your delegated interpretations rather than a bare list -- ## Suggested Follow-up Experiments (3-5, \
prioritised), ## Limitations and Caveats. Leaving out the Detailed Pathway \
Analysis is not an option; most of the report's value sits there.
- Finish by calling submit_report with the COMPLETE report. That is the only \
way to finish. After it returns SUBMITTED, reply DONE and stop."""


def build_lead_kickoff_prompt(organism_name, experiment_design, pathways,
                              max_turns, search_budget, loop_seconds):
    """The Lead Interpreter's opening message: context, the size of its
    territory, and the budgets the tools will enforce.

    This used to print the ranked pathway list in full. Two reasons it no longer
    does. The first is duplication: get_experiment_overview is the mandated
    first call and renders the same pathways in a richer table, so the list was
    paid for twice in a context every later turn re-sends. The second matters
    more. When the list was the top fifteen by p-value, printing it numbered
    1..15 under the heading "Enriched pathways" told the agent that fifteen was
    the experiment -- and the coverage it produced, run after run, was fifteen.
    The universe is now every SIGNIFICANT pathway, which is 100-150 on a real
    multi-omics job, and a numbered list of 150 would teach the opposite error:
    that the job is to march down a ranking. Naming the size and the rule, and
    leaving the choosing to the agent, is the point of the arm.
    """
    lines = ["## Experiment"]
    lines.append(f"Organism: {organism_name}")
    if experiment_design:
        lines.append(f"Design: {experiment_design}")
    lines.append("")
    lines.append("## Your territory")
    lines.append(
        f"{len(pathways)} pathways reached significance in this experiment. "
        f"Every one of them is in scope: get_experiment_overview lists them all "
        f"with p-values, driving omic layers and differential genes, and "
        f"get_pathway_details and delegate_interpretation both reach any of "
        f"them by name. There is no quota on how many you may examine.")
    lines.append(
        "Significance decides what is available; you decide what is worth the "
        "time you have. The ranking is information, not an instruction -- the "
        "pathway that answers the design above may not be the one with the "
        "smallest p-value, and a pathway that is significant and irrelevant to "
        "the design is still yours to set aside, in one sentence, on the record.")
    lines.append("")
    lines.append("## Budgets (enforced by the tools)")
    lines.append(f"- {max_turns} turns; {search_budget} literature searches; "
                 f"~{loop_seconds} s of investigation time")
    lines.append("")
    lines.append("Investigate this experiment and submit your report. "
                 "Begin with get_experiment_overview.")
    return "\n".join(lines)

def build_evidence_shelf_block(shelf):
    """The passages a delegated interpreter may cite, handed over before it writes.

    Citations fail verification when a claim is written first and support looked
    for afterwards: the gate needs a verbatim sentence from the paper, and a
    claim composed without one usually has none. Giving the writer the actual
    passages first makes the citation true by construction -- which is why the
    workflow arm's citations survive, since it writes with the abstracts in
    context.

    The block also separates the two kinds of sentence. Measured on round 30,
    this arm shipped 16 citations across 62 161 characters of prose (0.26 per
    thousand) against the workflow arm's 24 across 26 599 (0.90). It grounds
    what it cites, and then writes a great deal more that cites nothing --
    literature-flavoured prose with no passage behind it, which inflates the
    report past twice the length of the shipped arm's and dilutes the evidence
    that is there.
    """
    lines = [
        "\n\n## Evidence you may cite",
        "These passages come from the papers above. Cite [N] ONLY where you are "
        "using that paper's passage, and write the claim so the passage actually "
        "supports it.",
        "",
        "Two kinds of sentence, and keep them apart:",
        "  * What YOUR DATA shows -- values, timings, directions, p-values from "
        "the tables above. No citation belongs on these; they are what the "
        "experiment measured.",
        "  * What the LITERATURE says -- mechanism, precedent, a claim about "
        "biology beyond this experiment. Every one of these needs a passage "
        "below standing behind it.",
        "",
        "A sentence that is neither -- mechanism you cannot point to a passage "
        "for, written as though it were established -- is the one thing to leave "
        "out. Say it as an inference from your data, or do not say it. Length is "
        "not the goal: a paragraph carrying a measured value or a quoted passage "
        "earns its place, and one carrying neither does not.",
    ]
    for ref in sorted(shelf):
        lines.append('\n[%d] "%s"' % (ref, shelf[ref].replace("\n", " ")[:500]))
    return "\n".join(lines)
