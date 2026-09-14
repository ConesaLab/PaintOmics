"""The full-agent workflow: one Lead Interpreter Agent running a tool loop.

Design document: ``docs/diagrams/paintomics-ai-agent-proposal.drawio`` (and the
exported ``.drawio.png``). This module is that page, translated:

  * **The loop** -- one SDK ``Agent`` ("Lead Interpreter") whose Observe ->
    Decide -> Act cycle is the Agents SDK tool loop: every turn the model sees
    the notebook and tool results so far (Observe), emits one tool call
    (Decide/Act), and continues until it calls ``submit_report`` -- the only
    door out of the loop.
  * **The toolbelt** -- the existing modules wrapped as tools. The agent
    chooses WHAT; the tool enforces HOW MUCH: the search spend meter, the
    PubMed rate limit and the retrieval guard all live inside
    ``search_literature``, never in the prompt.
  * **Sub-agents** -- reachable only through the ``delegate_*`` tools, and the
    fan-out inside them is parallel bounded calls, not unbounded sub-loops.
  * **The mandatory exit gate** -- outside the loop, where no decision can
    skip it: the same quote collection, canonical references rebuild,
    per-citation Claim Verifier pass and Phase-6 programmatic net
    (verify -> redact -> renumber -> sort) the workflow arm runs.
  * **Loop backstops** -- max turns, a tool-output character ledger, the
    wall-clock deadline and the cancel flag. Hitting one ends the run loudly
    (``stats`` records which), never silently.

Why the loop is budgeted the way it is -- measured on the CSIC gateway
(2026-08-17, see agent.py's transport notes): short single-shot calls
parallelise (32 concurrent in 5.6 s) while tool loops and long generations
serialise, so the design keeps Decide turns terse, makes every fan-out
single-shot, and allows exactly one long-form generation (the report) inside
the loop. The default budget is 10 minutes end to end: the loop gets what is
left after ``AI_AGENT_GATE_RESERVE`` seconds are set aside for the exit gate.

Off by default. ``AI_FULL_AGENT=1`` routes ``run_ai_agent`` here; everything
else (servlet, queue, DAO contract, progress statuses) is unchanged, so the
two arms stay comparable run for run.
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool

from src.conf.serverconf import (
    AI_LLM_PROVIDER, AI_PROVIDERS, AI_MAX_PATHWAYS, AI_TEMPERATURE,
    AI_MAX_VERIFICATION_ITERATIONS,
)
from src.classes.AIInterpret import tools as tools_mod
from src.classes.AIInterpret import prompts as prompts_mod
from src.classes.AIInterpret import clusters as clusters_mod
from src.classes.AIInterpret.agent import (
    AgentContext, _build_agents, bounded, configure_sdk, _model, run_hedged,
    SDK_LONG_CALL_TIMEOUT, SDK_VERIFY_CONCURRENCY, SDK_MIN_CITATIONS,
    RelevantPMIDs, SDK_CALL_TIMEOUT, SENTENCE_REPAIR, _repair_sentences,
    reset_run_retries, run_retry_counts, set_run_deadline,
)
from src.classes.AIInterpret.context_builder import (
    build_pathway_context, build_gene_symbol_whitelist, get_organism_name,
    build_differential_metabolites_block,
    build_cross_omic_matrix, build_key_regulators_block, render_pathway_table,
    triage_pathways, build_gene_index, gene_measurements, pathway_gene_list,
    _build_omic_header_map, _gene_entry,
)
from src.classes.AIInterpret import neighbours as neighbours_mod
from src.classes.AIInterpret.llm_client import LLMClient
from src.classes.AIInterpret.pubmed_client import PubMedClient
from src.classes.AIInterpret.shared import _collect_cited_quotes, _parse_json_verdict
from src.classes.AIInterpret.verification import (
    _fuzzy_contains,
    count_body_citations, normalize_citation_markers, parse_references_section,
    redact_unverified_v2, render_references_section, renumber_citations,
    resolve_pmid_mentions, score_topup_survival, sort_references_section,
    last_sentences_dropped, quote_provenance, strip_markers,
    theme_conversion,
    verify_report_v2,
)

logger = logging.getLogger(__name__)

# The whole run, end to end. 600 s is the product constraint this arm is
# benchmarked under; the workflow arm's 2700 s ceiling still applies above it
# in run_agent_workflow-compatible deployments that raise it.
AGENT_RUN_SECONDS = float(os.getenv("AI_AGENT_MAX_RUN_SECONDS", "600"))
# Seconds set aside for the exit gate (quotes + verifier + programmatic net).
# The loop is given the remainder; a loop that spent everything would hand the
# gate a report it has no time to check, which converts "checked" to "shipped".
# Measured over the first live runs: the loop's whole gate (quotes,
# per-citation verification, the programmatic net) cost ~74 s, against the
# workflow arm's 293 s verify loop -- so a 240 s reserve was holding time
# that nothing spent while the loop stopped investigating at 172 s of 600.
GATE_RESERVE_SECONDS = float(os.getenv("AI_AGENT_GATE_RESERVE", "150"))
# Of the loop's own time, the last stretch belongs to writing the report: a
# long-form generation costs ~70-90 s on this gateway, so the tools start
# refusing further investigation this far from the loop deadline. Without it
# the agent is told "stop" only when there is no time left to write, and the
# run lands on the forced-synthesis backstop instead of its own report.
WRITE_RESERVE_SECONDS = float(os.getenv("AI_AGENT_WRITE_RESERVE", "110"))
# Decide turns. Each is one short streamed call at a measured ~3.5 s median.
# Round 1 used 34 tool calls in 172 s and stopped with 300 s of budget
# unspent, so the cap moves up; it is a backstop, not a target, and the
# clock guard still ends investigation in time to write.
AGENT_MAX_TURNS = int(os.getenv("AI_AGENT_MAX_TURNS", "40"))
# The literature spend meter: how many search_literature calls one run may
# make. Enforced in the tool -- the agent is TOLD the remaining budget in
# every result instead of being trusted to count.
# Parity with the workflow arm, which issues 35-45 queries (planner + per-pathway
# backfill) and reaches 27 papers. At 18 the agent retrieved a third of that and
# lost on citations for want of literature rather than judgement -- measured
# over four runs, see docs/ai-agent-benchmark.md.
SEARCH_BUDGET = int(os.getenv("AI_AGENT_SEARCH_BUDGET", "40"))
# Tool-output ledger: total characters of tool results the loop may consume,
# a proxy token backstop on the input side (the model's context is the real
# resource; characters are what this layer can measure without a tokenizer).
TOOL_CHAR_BUDGET = int(os.getenv("AI_AGENT_TOOL_CHAR_BUDGET", "400000"))
# Papers shown to one delegated interpretation. More retrieved literature is
# good; more literature per prompt is not.
DELEGATE_PAPERS = int(os.getenv("AI_AGENT_DELEGATE_PAPERS", "10"))
# Hits per search. Deliberately NOT widened: round 6 tried ten and the pool grew
# from ~13 papers to ~49 while citations collapsed 11 -> 3 and redactions rose
# 5.5 -> 11. It is the same effect agent.py records for its batches ("a batch
# handed 20+ abstracts cites fewer of them, not more") reappearing at the merge
# step, which hands the Report Writer the whole master reference list. More
# literature in one prompt buys fewer citations, not more.
# DEFAULT 10 as of the agent-v54-r3 ship, pinned here rather than inherited from
# the old papers-per-search setting. Round 51 halved it to 5 and citations fell 22.8 ->
# 12.5, a RESOLVED -10.25 at se 2.57 against round 50 where nothing else
# differed. The wide pool is where the citations come from; the earlier reading
# that retrieval volume buys nothing was a median hiding a gradient.
SEARCH_HITS = int(os.getenv("AI_AGENT_SEARCH_HITS", "10"))
# Parallel single-shot calls one delegate_* tool may run at once.
DELEGATE_WORKERS = int(os.getenv("AI_AGENT_DELEGATE_WORKERS", "4"))
DELEGATE_CHUNK = int(os.getenv("AI_AGENT_DELEGATE_CHUNK", "5"))
"""Pathways per sub-agent, i.e. how many writing units a delegation makes.

This is the ratio that tracks the citation gap. This arm writes about five
units and converts 8% of retrieved papers into shipped citations against the
shipped arm's 58-74%. With DELEGATE_PAPERS at 10, three chunks can show a
writer only 30 of 75 retrieved papers -- the other 45 cannot become citations
at all.

CAUTION, 2026-08-18: the note here used to say the shipped arm "writes fourteen
batches, each citing its own papers", and that premise is now in doubt. With
INFO logging finally reaching the round log, round 37's base replicate logged
"3 batches, 0 citing, 0 distinct markers" -- three batches, and not one [N]
marker among them -- while the run went on to ship 17-26 citations. So in that
run the citations were born in the SYNTHESIS, not in the batches.
batch_citations and batches_with_citations were set from the day they were
written and archived by nothing, so there is no history to settle it with; they
are archived from round 38. If the batches genuinely do not cite, then chunk
COUNT is not what converts papers, and raising the unit count here would be
tuning the wrong ratio.

Left at 5 so it changes nothing until a round sets it deliberately:
AI_AGENT_DELEGATE_CHUNK=3 gives five units for the same breadth, which the
config stamp records, so the round is identified without a code edit.
"""

DELEGATE_MAX_PATHWAYS = int(os.getenv("AI_AGENT_DELEGATE_MAX_PATHWAYS", "0"))
"""Hard ceiling on one delegate call. 0 -- the default -- means NO fixed ceiling.

The history is a lesson in fixed numbers. It was 10 (two chunks, so two of the
four worker slots never ran), then 20, then 60 to match what the description
promised. Every one of those was measured NOT to bind: coverage sat at 15-18
throughout, because the LEAD names about fifteen, and it went on naming fifteen
when the description offered sixty.

So the ceiling never did the job it was there for, and it could do real harm in
the one case it would bind -- a Lead that finally asks for eighty gets sixty and
is never told about the twenty. What actually costs something is WALL CLOCK, and
that is knowable exactly: see _delegation_capacity, which derives the number from
the time left instead of guessing it in advance. Set this to a positive integer
only to pin a ceiling for a benchmark round.
"""

DELEGATE_WAVE_SECONDS = float(os.getenv("AI_AGENT_DELEGATE_WAVE_SECONDS", "35"))
"""Measured cost of one delegation wave: DELEGATE_WORKERS sub-agents in parallel.

A wave is DELEGATE_CHUNK x DELEGATE_WORKERS = 20 pathways at the defaults, and
takes about 35 s on this gateway whether it carries five pathways or twenty.
This is what _delegation_capacity spends the remaining clock in units of.
"""

DETAIL_MAX_BLOCKS = int(os.getenv("AI_AGENT_DETAIL_BLOCKS", "20"))
"""Pathway blocks one get_pathway_details call returns.

This is a CONTEXT limit, never a limit on reach: the tool is instant and may be
called again for whatever did not fit, and the result now says so by name. It
was 8, applied by breaking out of a loop over the rank-ordered universe -- so a
Lead that named twenty pathways got the eight best-ranked of them, in rank order
rather than the order it asked, and no part of the answer mentioned the other
twelve. It could not ask again for what it did not know it was missing.
"""

DELEGATE_QUOTE_SECONDS = float(os.getenv("AI_AGENT_DELEGATE_QUOTE_SECONDS", "45"))
"""Ceiling on grounding a delegation's citations, so it cannot eat the run.

Collection is a thread pool of 8 and costs about 5 s for 20 citations; 45 s
is room for a wide delegation plus a slow gateway, and no more.
"""
# Below this many cited papers the gate asks the Report Writer once more to
# use the literature the agent retrieved but never cited. Same floor as the
# workflow arm (SDK_MIN_CITATIONS): the two arms are only comparable if the
# incumbent's own grounding pass exists on both sides.
MIN_CITATIONS = int(os.getenv("AI_AGENT_MIN_CITATIONS", str(SDK_MIN_CITATIONS)))
# Merge the sub-agents' interpretations into the final report rather than
# shipping the Lead's compression of them. AI_AGENT_MERGE_DELEGATED=0 restores
# the round-3 behaviour for comparison.
MERGE_DELEGATED = os.getenv("AI_AGENT_MERGE_DELEGATED", "1") == "1"
# "stitch" keeps each delegated report's own grounded text and spends the writer
# call only on framing prose; "rewrite" is round 4-5's single pass over
# everything, which measured 4.5-11 citations because the writer saw the whole
# reference list at once.
MERGE_MODE = os.getenv("AI_AGENT_MERGE_MODE", "stitch")

# Rewrite the finished dossier as a paper Results section.
#
# What the arm produces today is a DOSSIER, not a Results section: measured on a
# real run, 8 184 words over 497 lines, opening with a "Key Findings" bullet
# block, then fifteen numbered pathway sections each carrying the same three
# sub-headings, then a 37-row table. It also reads as stitched -- a second H1
# mid-document and "Cross-Pathway Synthesis" appearing twice -- because it is
# the concatenation of delegated chunks.
#
# A Results section is continuous prose, organised by FINDING rather than by
# pathway id, with the clusters described in a sentence rather than tabulated.
RESULTS_SECTION = os.getenv("AI_AGENT_RESULTS_SECTION", "0") == "1"
# No word ceiling. The first version of this stage carried one and used it as a
# TARGET: it cut 8 184 words to 2 957 and took 22% of the rubric coverage with
# them (round 66), while keeping every citation. A length knob on a stage whose
# job is to reorganise is an instruction to discard, so there is no longer one
# to set. What bounds the output now is the findings guard.
SCREEN_TARGET_POOL = int(os.getenv("AI_AGENT_SCREEN_TARGET", "35"))
"""How many screened papers the pool should aim for.

Derived, not guessed. Across round 39's four screened replicates:

    citations = 0.91 x papers - 7.2   (r = +0.997)

so a pool of 35 predicts ~25 citations, comfortably past the incumbent's 20.5,
and the replicate that kept 17 papers shipped 8. Every kept paper is worth about
0.91 citations once screening has removed the keyword-only ones.

The target is NOT the delegation window (DELEGATE_PAPERS x chunks). That
described how many papers a delegated writer can be shown, and measurement
killed the assumption that it bounds citations: delegate_markers is 0 on every replicate, so the
delegated analyses cite nothing at all. The Lead writes the citing draft and sees
every paper through the search listings, so the delegation window has no say in
how many citations a run can carry.
"""

# DEFAULT ON as of the agent-v54-r3 ship. The gate-side pull-back strips a
# top-up marker that fails verification instead of letting redaction delete the
# whole sentence. About 8 of the ~14 citations the top-up adds per run fail, and
# without this every one of them costs the finding it was attached to.
VERIFY_TOPUP = os.getenv("AI_AGENT_VERIFY_TOPUP", "1") == "1"
"""Check the top-up's own citations before the gate charges a sentence for them.

The top-up bolts [N] onto sentences that already stood on their own, and 40-50%
of those citations then fail -- in every replicate of rounds 39-43,
`topup_added_failed` equalled `failed_citations` exactly, while base's equalled
zero. Round 44 removed the stage and fixed rule 3 completely (0 redactions across
replicates) at the cost of rule 2 (16.0 citations against base's 22.3).

The asymmetry that makes a third option work: pulling a marker back BEFORE the
gate costs nothing, because the sentence stood without it. Letting it through
costs the sentence. So the top-up can keep the citations that hold and give back
the ones that do not, which neither removing nor capping the stage can do.
"""

SHOW_UNCITED = os.getenv("AI_AGENT_SHOW_UNCITED", "0") == "1"
"""Whether check_my_citations names the retrieved papers the draft never cites.

Off by default for a reason that is about the EXPERIMENT, not the change. Round
39 is the first round in which the agent arm leads every rule, and the shipping
bar is 5/5 on two consecutive rounds -- so round 40 has to be a replication of
round 39's exact configuration. A change committed between them, however good,
turns the replication into a new experiment and the bar can never be met.

The change itself is sound and measured-motivated: the top-up costs 83.5 s (24%
of a run) and supplies 9 of 26 citations by bolting markers onto finished prose.
It waits for the round after the replication.
"""

# DEFAULT ON as of the agent-v54-r3 ship. The screen is a selection step, and
# selection is the clearest thing this arm has bought: base cites every paper it
# retrieves (52/52, 45/45, 28/28 in every archived run) and pays 12-16
# redactions for it in cluster mode, while this arm cites ~20 of ~67 and ships
# 0.0 redactions, resolved in 7 of 7 rounds.
SCREEN_PAPERS = os.getenv("AI_AGENT_SCREEN_PAPERS", "1") == "1"

# Render a temporal profile only for genes that are actually differential.
#
# get_pathway_details is the chattiest tool in the belt at 24 058 characters per
# call and ZERO seconds, so its whole price is context -- and the Lead re-sends
# its context on every Decide turn, which makes a result returned on turn 3 of 44
# get re-sent about forty times. Per-call size is the multiplier; the per-run
# total hid it.
#
# _get_top_genes already sorts (-relevant, -effect_size) and takes ten, so the
# SELECTION is relevance-first. What it does not do is stop when the relevant
# genes run out: a pathway with three differential genes fills the remaining
# seven slots with flat ones, and each of those still renders its full per-layer
# time course. A matched-but-flat gene has no differential signal by definition,
# so its series is not evidence for anything, and it costs roughly 180 of the
# ~250 characters on its line.
#
# The gene is still named and still carries its effect size, so nothing is
# hidden from the agent -- only the series it could not draw a conclusion from.
LEAN_PROFILES = os.getenv("AI_AGENT_LEAN_PROFILES", "0") == "1"

# Splice the delegated detail into the LEAD's own framing instead of an LLM's.
#
# The stitch does not use the Lead's report. It feeds the report and 60 kB of
# delegated detail to a framing agent, asks it for Key Findings, Cross-Pathway
# Themes, Follow-up Experiments and Limitations, and builds the candidate from
# THAT -- so everything the Lead wrote is thrown away and rewritten.
#
# Two measurements say this is a bad trade. The Lead already writes all four of
# those sections: across the six runs whose merge was rejected (which are pure
# Lead output) all six have Cross-Pathway Themes, Follow-up and Limitations, and
# five of six have Key Findings. And the rewrite is where grounding goes -- round
# 47 r3's draft carried 25 citations, 21 of them grounded, and the candidate
# built from the framing carried 12, which is why the guard threw the whole
# stitch away and the run shipped without a pathway section at all.
#
# The framing agent also has strictly LESS context than the Lead did: it sees the
# draft and a truncated detail block, while the Lead ran the whole investigation
# and had already read every delegated analysis as a tool result before writing.
# Re-deriving a conclusion from a subset of its inputs cannot improve it.
# DEFAULT ON as of the agent-v54-r3 ship. Measured in round 49: merge rejections
# went from 21% of runs to 0 of 4, and merge_s from 18-20 s to 6-7 s, because the
# splice reuses the Lead's own framing instead of paying an LLM to rewrite it and
# lose its citations.
FRAMING_REUSE_LEAD = os.getenv("AI_AGENT_FRAMING_REUSE_LEAD", "1") == "1"

# How much of each offered paper the citation top-up gets to read.
#
# The top-up is the only writer in the pipeline with no evidence in hand. The
# delegates get a `_quote_shelf` of real passages; the Lead gets real quotes
# through `check_my_citations` and `_quote_evidence_lines`. The top-up got
# `abstract[:220]`, and it is the stage whose citations die: measured over 23
# archived runs it adds 11-14 citations for ~101 s, about 8 of them fail
# verification and are pulled back, and ~6 survive -- a precision of roughly 43%.
#
# 220 characters is not a short abstract, it is the FIRST 15% of one. Measured
# over 899 stored abstracts the median is 1 428 characters, and what 220 buys is
# the background sentence:
#
#   "Cathelicidins have been reported to inhibit human papillomavirus infection
#    in vitro; however, nothing is known about their activity in vivo. In this
#    study, experimental skin infection with Mus musculus papillomavirus 1 r"
#
# It stops at "In this study". The model is asked whether a paper supports a
# specific claim while being shown the setup and never the result, so a citation
# that is topically plausible and factually unsupported is the expected output,
# not a surprise.
#
# The candidate COUNT is deliberately not reduced alongside this. Precision is
# the measured problem, not volume, and halving the offer would cut the number of
# citations attempted at the same time as improving each one -- two changes, and
# the net could easily be fewer survivors.
TOPUP_ABSTRACT_CHARS = int(os.getenv("AI_AGENT_TOPUP_ABSTRACT", "220"))
TOPUP_OFFER_PAPERS = int(os.getenv("AI_AGENT_TOPUP_OFFER", "30"))

# Tell the Lead the number it is being held to.
#
# MIN_CITATIONS is 22 and this arm's median is ~20, so the citation top-up fires
# on EVERY run by construction -- 101 s, 28% of the clock, at ~43% precision,
# bolting markers onto sentences that were written without them. That is the
# claim-drift failure mode as a pipeline stage.
#
# Meanwhile check_my_citations, at 100% adoption and called while the draft can
# still change, reports STATUS and never the TARGET: "17 citations will ship, 15
# have a supporting quote". The Lead has no way to know it is five short, so it
# submits, and a blind stage with no quotes in hand makes up the difference.
#
# The wording deliberately names the mechanism that measurement supports --
# reading papers, worth +3 citations from 0 to 9 reads against +1 for retrieving
# 2.3x as many papers -- and repeats the cost of forcing one in, because the
# failure this is meant to prevent is a keen agent inventing markers to hit a
# number.
SHOW_CITATION_TARGET = os.getenv("AI_AGENT_CITATION_TARGET", "0") == "1"

# Fetch full text for a chunk's papers BEFORE the sub-agent writes about them.
#
# Measured over round 50: of 35 cited papers, 34 were abstract-only at the moment
# the report was written. Full text is fetched at the GATE, for papers already
# cited, and its only consumer is the verifier. So the pipeline pays to retrieve
# full text and spends all of it checking work that was done without it.
#
# `_quote_shelf` -- the delegates' entire evidence -- runs `search_paper_text`
# over each paper's `sections`, and for an abstract-only paper that is just the
# abstract. The delegated interpreters write ~40 kB of a ~65 kB report from
# abstract snippets, and the gate then judges those claims against text nobody
# writing had.
#
# Priced before building: the gate's upgrade runs at ~0.5 s per paper with 276 s
# of unused budget at that point, and runs finish at ~380 s of a 600 s ceiling.
# Ten papers per chunk across four concurrent workers is a few seconds.
DELEGATE_FULLTEXT = os.getenv("AI_AGENT_DELEGATE_FULLTEXT", "0") == "1"

# Let a citation sit on a sentence that also states what the data did.
#
# `build_evidence_shelf_block` tells the writers to keep the two kinds of
# sentence apart: "What YOUR DATA shows... No citation belongs on these". Its
# stated reason is that a claim written first and supported afterwards is the one
# that fails verification -- which is about ORDER, and the shelf already fixes
# order by handing the passages over BEFORE the writer starts.
#
# The instruction over-corrects. Measured across the archive, 5-10% of citation
# sentences join a data claim to a citation anyway, they all survived the gate,
# and reading them they are the best citations in the corpus:
#
#   "surrogate light-chain genes Igll1 (peak -4.43) and Vpreb1b (peak -4.39) are
#    strongly repressed, matching the known role of Ikaros/Aiolos as direct
#    repressors of Igll1 and Vpreb1 in small pre-B cells [2]"
#
#   "Prkcb shows profound, sustained repression (-4.87 to -5.03) yet PKCb is
#    described as promoting the germinal center reaction in B cells [6]"
#
# The second sets the data against the literature, which is interpretation rather
# than recitation. So joined sentences verify fine and the rule suppressing them
# is costing the arm its strongest output.
#
# Appended to the per-chunk prompt rather than edited into the shared block, so
# the shipped arm stays the control.
JOIN_DATA_AND_CITATION = os.getenv("AI_AGENT_JOIN_CITATIONS", "0") == "1"

# Show the metabolite layer to the writers.
#
# The job carries five omics layers; four are gene-based and Metabolomics is
# compound-based. Nothing in AIInterpret read it -- "compound" appeared zero times
# in context_builder.py, agent.py and agent_loop.py, and clusters.py used
# matchedCompounds only for Sorensen-Dice similarity. So a five-omics experiment
# was interpreted as four, in BOTH arms.
#
# The cost is measurable against the published paper. Rubric item E2 reads
# "polyamines -- spermidine, putrescine, spermine -- decline toward pre-BII", and
# the job holds exactly that, all three flagged differential:
#
#   Spermidine  0.18  -0.10  -0.07  -0.42  -0.46  -0.56
#   Putrescine  0.26   0.12   0.12  -0.53  -0.96  -1.27
#   Spermine    0.16   0.09   0.03  -0.30  -0.37  -0.34
#
# No report in either arm has ever named a metabolite. Section E is four items at
# weight 3, about a fifth of the rubric, and the polyamine story is one of the two
# findings the paper is FOR.
SHOW_COMPOUNDS = os.getenv("AI_AGENT_SHOW_COMPOUNDS", "0") == "1"

# Scope the Lead by SIGNIFICANCE, and let it choose within that.
#
# History, because the shape of the mistake matters more than the fix. The Lead
# covered a median 16 pathways out of the 102 it indexed, and every constant
# suspected of causing it was measured NOT to bind: DELEGATE_MAX_PATHWAYS was
# never reached at 20 or at 60, SEARCH_BUDGET is 40 with about 15 used. The
# limiter was prose -- SYSTEM_PROMPT_LEAD_AGENT said "top-ranked" five times,
# and in a 102-pathway context that reads as the top fifteen.
#
# AI_AGENT_CLUSTER_SCOPE was the flag that rewrote those five clauses to "every
# cluster". Round 67 measured it over 8 replicates: coverage 15.4 -> 17.4,
# rubric coverage 0.571 -> 0.611, wall clock 422 s -> 420 s. Free, slightly
# positive, and it did not bind either -- so the flag is now folded into the
# prompt itself and deleted, rather than left as a knob nobody turns.
#
# The rest of the answer is not a prompt at all: the agent was only ever HANDED
# fifteen pathways, and widened to the significant set only if it happened to
# call cluster_pathways. See _significant_universe -- an agent cannot choose
# against pathways it was never shown, and no amount of instruction fixes that.


def _lead_instructions():
    """The Lead's prompt. One source, no rewrites -- see the note above."""
    return prompts_mod.SYSTEM_PROMPT_LEAD_AGENT


_JOIN_NOTE = (
    "\n\nOne more thing about the two kinds of sentence above. Where a passage "
    "bears on something you actually measured, write ONE sentence carrying both "
    "-- the measured change and what the passage establishes -- instead of a data "
    "sentence followed by a separate literature sentence. This shape:\n"
    "  \"Igll1 falls to -4.43 by 24h, matching the known role of Ikaros as a "
    "direct repressor of Igll1 in small pre-B cells [2].\"\n"
    "and this one, where the two disagree:\n"
    "  \"Prkcb is repressed throughout (-4.87 to -5.03), yet PKCb is described "
    "as promoting the germinal centre reaction [6].\"\n"
    "A tension stated plainly is worth more than either half alone. This is NOT "
    "a licence to cite more: a passage that bears on nothing you measured still "
    "goes uncited, and a measured value with no passage behind it still carries "
    "no marker.")
"""Screen search hits for a quotable finding before they enter the pool.

The one mechanism the shipped arm has that this arm has never had. Base runs a
Paper Filter on every search -- "keep at most a handful", the test being whether
the paper holds a specific quotable finding about the MECHANISM -- and this arm
registers everything PubMed returns.

Measured round 38, same jobs: base carried 27-31 papers and converted 13 of 14
retrieved themes into cited papers; this arm carried 65 and converted 7 of 14.
Per paper, base ships ~0.78 citations and this arm ~0.22. Retrieval VOLUME is not
the cause -- across 72 archived runs corr(papers, citations) is only +0.16 -- so
what differs is that base's pool is screened and this arm's is not.

Deliberately inside the TOOL, not a new pipeline stage: the agent still decides
what to search for, and the tool decides what is worth keeping. Costs one short
call per search, which is exactly what base pays.
"""

FRAMING_MAY_CITE = os.getenv("AI_AGENT_FRAMING_MAY_CITE", "0") == "1"
"""Whether the framing call may cite papers the delegated analyses never used.

Measured round 38, same denominator on both arms: base converted 13 of 14
retrieved themes into cited papers; this arm converted 7 of 14. Base's
interpretation batches cite NOTHING (0 of 3 batches, 0 markers) and its synthesis
produces 31 markers -- so in the shipped arm the citations are written FRESH by
one writer holding the whole reference list.

This arm cannot do that, by instruction. The framing prompt says "Reuse [N]
citation markers ONLY where they already appear above for that claim. Do not
invent markers." So a theme no delegate happened to cover can never be cited,
however good the paper is -- which is the 7-of-14 gap stated as a rule.

Off by default because the instruction is not arbitrary: MERGE_MODE="rewrite"
tried letting one writer redo everything with the full list in view and measured
4.5-11 citations, worse than stitch. The difference matters -- rewriting text
that ALREADY carries markers loses them (the same failure as the sentence-repair
marker drop), while writing fresh sections that carry none can only add. This
flag targets the second case only: the framing sections, which today carry no
citations at all.
"""
# The gate's own floor. Post-loop work (the merge) is bounded by the clock that
# is actually left after reserving this, and skipped when there is not enough --
# measured the hard way: a 50 s merge on top of a 450 s loop and a 150 s reserve
# put a run at 602 s and it died on the 600 s ceiling with nothing to show. A
# step that can push a run past its deadline is not optional work, it is a bug.
GATE_MIN_SECONDS = float(os.getenv("AI_AGENT_GATE_MIN", "130"))
# Ceiling on the stitched per-pathway detail. Measured: stitching every delegated
# report verbatim produced a 90 621-char report, 2.2x the workflow arm's, which
# is not "thorough" but unreadable.
# 42 000 was a first guess and it bound too tightly: with it, a run shipped
# 61 090 chars against a 82 586-char sanity ceiling and lost coverage (12 pathways
# named against the workflow arm's 15) because the truncated tail took its
# citations with it. 56 000 leaves the report around 75 000 -- still inside the
# ceiling, with the paragraphs that carry the grounding.
STITCH_MAX_CHARS = int(os.getenv("AI_AGENT_STITCH_MAX_CHARS", "40000"))
"""Ceiling on the delegated text handed to the merge.

Was 56 000. Round 32 shipped 44 593 characters of prose in a 60 258-character
report -- 2.16x the shipped arm's, failing the length rule that exists to catch
degenerate output. The round before that tried to fix length by ASKING the
writer for less (separate data claims from literature claims, drop mechanism you
cannot point at); the prediction was recorded, the falsifier fired, and prose
length turned out not to be under prompt control. A cap is.
"""
# The LLM verify->correct loop's share of the clock. It is the single most
# expensive thing in a run once grounding works -- 291 s to check 19 citations,
# the same price the workflow arm pays for 20 -- and it is also the most
# interruptible: whatever it does not reach, verify_report_v2 and
# redact_unverified_v2 still handle deterministically. So it gets a slice, not
# a promise.
VERIFY_MAX_SECONDS = float(os.getenv("AI_AGENT_VERIFY_MAX_SECONDS", "300"))
# Hand the verifier its evidence instead of making it hunt for it.
# Measured: 9 of the 14 redactions in the best-grounded run so far were the
# Claim Verifier reporting "Max turns (6) exceeded" -- it spent its whole tool
# budget on search_paper_text round-trips and never returned a verdict, and a
# verifier that raises counts as a failure, so real citations were redacted for a
# tooling reason. The same warning appears in the workflow arm's logs.
#
# Finding the quote in the paper is mechanical: tools.py does it in pure Python.
# So the passage is extracted in code and pasted into the prompt, and the model
# is left with the one judgement only it can make -- does this text support this
# claim. No tools, one call, no turns to exhaust. The deterministic quote check
# in verify_report_v2 still runs afterwards either way.
# Default ON as of the measurement above: 29 of 29 calls returned a verdict at a
# median 2 464 ms, redactions fell 12 -> 2 (the survivors being the only two
# genuine refutations), the verify loop 291 s -> 117 s and the run 485 s -> 338 s.
# AI_AGENT_VERIFY_PREFETCH=0 restores the tool-loop verifier for comparison.
# NOTE the env var: this arm reads AI_AGENT_VERIFY_PREFETCH, while the shipped
# arm reads AI_VERIFY_PREFETCH for a constant of the SAME NAME. Setting one does
# not touch the other, so "AI_VERIFY_PREFETCH=0" disables prefetch in base only
# and leaves this arm prefetching -- a comparison that would look like an arm
# difference and is a flag difference. Both default ON; an exported-but-empty
# value counts as unset here, as it does for the shipped arm.
VERIFY_PREFETCH = (os.getenv("AI_AGENT_VERIFY_PREFETCH") or "1").strip().lower() \
    not in ("0", "false", "no")
# How many abstract-only papers may be upgraded to full text before quoting.
# Measured: the workflow arm cites 13-14 full-text papers of ~21 (~65 %) because
# it batch-fetches full text for everything it retrieves; this arm cited 2-4 of
# 11-19 (~20 %) because it upgraded lazily, one paper per read_paper call. A
# quote has to be found VERBATIM in the paper, and a 250-word abstract rarely
# contains the sentence a specific claim needs -- so the citation arrives
# unquotable and the net strips it. That is most of the grounding gap.
FULLTEXT_MAX_PAPERS = int(os.getenv("AI_AGENT_FULLTEXT_MAX", "24"))
# Verify->correct rounds at the gate. 3 = two correction passes.
#
# This was 2, justified as "the 600 s budget does not fit the workflow arm's 3".
# Round 57 measured that claim and it is false at current speeds: 8 replicates
# at 3 gave a median span of 450 s with a maximum of 470 and none over the bar.
#
# The third wave is not marginal. Paired within each run -- wave 3's input is
# wave 2's output, so no baseline is involved -- ungrounded citations fell
# 4.00 -> 1.20, removing 70% of what wave 2 left, and all five runs that reached
# a third wave improved. One finished with zero ungrounded citations out of 33.
#
# Grounding failure here is overshoot, not fabrication: across 1038 archived
# verdicts the quote is real in 99.5% of cases and 37.8% are a real quote
# attached to a claim it does not support. Overshoot is repairable by rewriting
# the sentence, which is what a correction wave does, which is why more waves
# keep paying.
#
# Two of the eight corrections were still cancelled mid-flight for want of
# budget, and those runs pay for a wave and get no repair. That is a fault of
# the whole-report rewrite, not of the wave; see the sentence-repair retest.
VERIFY_ITERATIONS = min(int(os.getenv("AI_AGENT_VERIFY_ITERATIONS", "3")),
                        AI_MAX_VERIFICATION_ITERATIONS)

TOPUP_ENABLED = (os.getenv("AI_AGENT_TOPUP") or "1").strip().lower() \
    not in ("0", "false", "no")
"""Whether the citation top-up stage runs at all.

It is the largest workflow remnant left in this arm. Measured over round 36:
99 s of a 304 s run -- 32.5% of the wall clock -- and it fires on EVERY run,
because the trigger is "citations under MIN_CITATIONS" and this arm is always
under it. Together with the merge (5.2%) it is 37.7% of the clock spent on two
stages that sit outside the Lead-then-Verifier shape.

It is also a bet with asymmetric stakes, priced only since this session: it adds
[N] markers to sentences that already stood on their own, so a marker that
verifies buys one citation and a marker that fails costs the WHOLE SENTENCE.
Round 34 recorded 5 added and 0 failed on one job and 2 added against 2 failures
on another.

The Lead already owns this job: check_my_citations is in its belt, at 100%
adoption, and it is called while the draft can still change. A dedicated flag
rather than AI_AGENT_MIN_CITATIONS=0 so the config stamp and the code
fingerprint both record which pipeline ran -- the same reason the fingerprint
now hashes flags.
"""

TOPUP_MIN_SECONDS = float(os.getenv("AI_AGENT_TOPUP_MIN", "200"))
"""Clock a citation top-up needs before it is worth starting.

The top-up rewrites the whole report to add citations, and it fires whenever
the count is under MIN_CITATIONS -- which for this arm is always. Measured at
114 s, spent at the point in the run with the least time left. Round 30's
replicate was stopped at the 600 s ceiling with 327 s of untraced post-loop
work behind it, and this is the largest single piece of it.

A report that ships with fewer citations beats one that does not ship.
"""

NUDGE_MIN_SECONDS = float(os.getenv("AI_AGENT_NUDGE_MIN", "90"))
"""Don't ask the agent to redo work there is no time to redo.

Measured over the archived runs, the gap after a submit_report -- the agent
rewriting a ten-thousand-character report -- runs a median 58 s and a mean 69 s.
A nudge with less than that left costs a minute of rewriting and delivers
nothing.

Measured against `hard_deadline`, which ALREADY has GATE_RESERVE_SECONDS taken
off it, so the exit gate is not part of this sum. The first version added the
gate reserve on top and set the bar at 210 s, which silently disabled the nudge
in the end-to-end test's 300 s budget -- both submits sailed through and the
suite caught it.
"""


@dataclass
class LoopContext(AgentContext):
    """AgentContext plus the loop's ledger, notebook and trace.

    Subclassing keeps the Claim Verifier's tools working unchanged: they read
    ``ctx.paper_index`` and ``ctx.tool_calls`` through the same attributes.
    """
    agents: dict = field(default_factory=dict)          # the sub-agent registry
    pathways: list = field(default_factory=list)        # ranked context dicts
    gene_whitelist: set = field(default_factory=set)
    partition: Any = None                               # cluster partition or None
    pubmed: Any = None                                  # PubMedClient
    hooks: dict = field(default_factory=dict)
    notebook: list = field(default_factory=list)
    delegated: list = field(default_factory=list)      # sub-agent interpretations
    trace: list = field(default_factory=list)           # toolTrace events
    searches_used: int = 0
    searched_tags: set = field(default_factory=set)     # topic_tags with a search
    # Did each delegated chunk get ITS OWN literature, or the fallback?
    delegate_attribution: dict = field(default_factory=dict)
    delegate_markers: int = 0                          # distinct [N] the sub-agents wrote
    papers_screened_out: int = 0                       # hits the screen rejected
    abstract_rereads: int = 0                          # turns spent re-reading the listing
    # What each note is ABOUT, in the agent's own words -- parallel to notebook
    # so the two fallback paths keep rendering plain strings.
    note_subjects: list = field(default_factory=list)
    tool_chars: int = 0
    # Per-tool attribution of the same ledger. The total tells the agent
    # what it has left; the breakdown tells US which tool is eating the
    # context the investigation has to reason through.
    tool_chars_by_tool: dict = field(default_factory=dict)
    # Chars alone cannot tell a chatty tool from a popular one, and those need
    # opposite fixes: shrink the payload, or leave it alone because the agent
    # keeps choosing it. Counting calls beside characters makes cost-per-call
    # readable straight off the archive instead of by joining a trace file.
    tool_calls_by_tool: dict = field(default_factory=dict)
    # Stats raised from inside the loop, where the `stats` dict is not in scope.
    # `delegate_attribution` already does this by hand for three keys; this is
    # the same idea without a bespoke field per counter.
    extra_stats: dict = field(default_factory=dict)
    genes_shown: int = 0
    genes_flat: int = 0
    # Which trace file this run wrote. 177 archived traces share two job IDs
    # -- a benchmark replicate reuses the job -- so a stats record could not
    # name its own trace, and every per-tool call count had to be recovered by
    # guessing which file on disk belonged to which run.
    trace_path: str = ""
    # KGML adjacency, parsed once per run (364 files, ~1 s for mmu).
    adjacency: Any = None
    pmid_to_ref: dict = field(default_factory=dict)
    next_ref: int = 1
    submitted_report: str = ""
    submit_attempts: int = 0                            # nudges are one-shot
    started_at: float = 0.0                             # loop start (wall clock)
    archived: list = field(default_factory=list)         # events already on disk
    hard_deadline: float = 0.0                          # loop must be done by
    delegation_cache: dict = field(default_factory=dict)  # resolved key -> report
    quotes: dict = field(default_factory=dict)          # ref_index -> verbatim quote
    flagged_citations: set = field(default_factory=set)   # unquotable at last check


def _hb(ctx, status, percent, detail):
    hooks = ctx.hooks or {}
    if hooks.get("progress"):
        try:
            hooks["progress"](status, percent, detail)
        except Exception:
            logger.debug("progress hook failed", exc_info=True)
    if hooks.get("cancelled") and hooks["cancelled"]():
        raise InterruptedError("Cancelled")


def _archive_trace(ctx):
    """Append this run's trace to a per-run JSONL file.

    The DAO keeps the last 200 events of the current run because that is what a
    UI needs. Deciding which TOOLS are worth their place needs the opposite:
    every run, kept. Measured the hard way -- twelve benchmark runs produced two
    surviving traces, so "never called" could not be told apart from "not called
    in the run that happened to be last", and four tools sat unjudged.

    Best-effort by design: a failed write must never affect an interpretation.
    """
    try:
        from src.conf.serverconf import CLIENT_TMP_DIR
        directory = os.path.join(CLIENT_TMP_DIR, "ai_traces")
        os.makedirs(directory, exist_ok=True)
        stamp = int(ctx.started_at) if ctx.started_at else 0
        path = os.path.join(directory, "%s-%d.jsonl" % (ctx.job_id, stamp))
        with open(path, "a") as handle:
            for event in ctx.trace[len(ctx.archived):]:
                handle.write(json.dumps(event) + "\n")
        ctx.archived = list(ctx.trace)
        ctx.trace_path = path
    except Exception:
        logger.debug("trace archive failed", exc_info=True)


def _trace(ctx, tool, args_summary, result, started):
    """Record one tool call: the run journal, the DAO toolTrace, and progress.

    The trace is the design's run journal made real -- it is what the frontend
    activity feed reads, what a resumed session inspects, and the telemetry
    the benchmark harness scores search spend from.
    """
    event = {
        "seq": len(ctx.trace) + 1,
        "t": round(time.time() - ctx.started_at, 1),
        "tool": tool,
        "args": str(args_summary)[:200],
        "result": str(result)[:200],
        "ms": int((time.time() - started) * 1000),
    }
    ctx.trace.append(event)
    ctx.tool_calls += 1
    # Failures are counted here too, deliberately: a call that raised still
    # spent a turn and still went through the model, so hiding it would flatter
    # exactly the tools that are hardest to use correctly.
    ctx.tool_calls_by_tool[tool] = ctx.tool_calls_by_tool.get(tool, 0) + 1
    _archive_trace(ctx)
    hooks = ctx.hooks or {}
    if hooks.get("tool_event"):
        try:
            hooks["tool_event"](event)
        except Exception:
            logger.debug("tool_event hook failed", exc_info=True)
    # Turn-count progress: the loop has no phases, so the bar walks with work.
    percent = min(70, 15 + int(55 * len(ctx.trace) / max(AGENT_MAX_TURNS, 1)))
    _hb(ctx, "interpreting", percent, "Agent: %s" % tool)


# Stamps whose result is archived whole. See the note inside _trace_gate.
VERBATIM_TRACE_STAMPS = frozenset(("__config__", "__outcome__", "__stats__"))


def _trace_gate(ctx, tool, args_summary, result, started):
    """Archive a GATE-side LLM call (verification, quotes) as its own event.

    The toolbelt trace answers "which tools does the Lead use". It says nothing
    about the per-citation verifier, which is the most expensive LLM consumer in
    a run -- 291 s of a 597 s run, hedged calls timing out at 45 s and retrying,
    9 of 14 redactions caused by it exhausting its turns rather than refuting
    anything. Instrumentation that cannot see the biggest cost is not
    instrumentation.

    Marked `gate: True` and deliberately NOT counted in ctx.tool_calls, so the
    toolbelt numbers stay comparable across every round measured so far.
    """
    ctx.trace.append({
        "seq": len(ctx.trace) + 1,
        "t": round(time.time() - ctx.started_at, 1),
        "gate": True,
        "tool": tool,
        "args": str(args_summary)[:120],
        # Some stamps are not a summary of an event -- their whole value IS the
        # text, and half of a JSON object is not half as useful, it is
        # unparseable. The 160-char cap once cut `__config__` mid-string and
        # made every run unreadable to the analyzer that stamp exists to feed.
        #
        # Kept as a SET rather than `== "__config__"` because the next such
        # stamp is the failure this comment describes, one exemption too late.
        # `__outcome__` currently fits in 160 chars with seven fields and would
        # break silently on the eighth.
        "result": (str(result) if tool in VERBATIM_TRACE_STAMPS
                   else str(result)[:160]),
        "ms": int((time.time() - started) * 1000),
    })
    _archive_trace(ctx)


def _flag_snapshot():
    """Every AI_AGENT_* knob and the value it holds THIS run, read from source.

    The config stamp was a hand-written dict of about fifteen keys, so a flag was
    only recorded if someone remembered to add it there. Audited: 35 flags exist
    in this module and 12 have ever appeared in an archived stamp. Round 49 ran
    with FRAMING_REUSE_LEAD=1 and its own stamp does not say so -- which means a
    trace cannot be asked, later, which pipeline produced it.

    `_code_fingerprint` already refuses a hand-kept list for exactly this reason
    ("without a hand-kept list that would drift out of date") and hashes the
    module instead. It answers "are these runs the same code" and never "what
    differs". This answers the second question, and stays correct when a flag is
    added, because it is derived rather than maintained.
    """
    try:
        import inspect
        import sys as _sys
        src = inspect.getsource(_sys.modules[__name__])
        g = globals()
        out = {}
        for const, env in re.findall(
                r'^([A-Z_][A-Z_0-9]*)\s*=\s*[^\n]*?os\.getenv\(\s*"(AI_AGENT_[A-Z_]+)"',
                src, re.M):
            if const not in g:
                continue
            value = g[const]
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[env[len("AI_AGENT_"):].lower()] = value
        return out
    except Exception:
        # Telemetry must never be the reason a run fails.
        logger.debug("flag snapshot failed", exc_info=True)
        return {}


def _code_fingerprint():
    """A short hash of everything that decides how this agent behaves.

    The config stamp records tunable constants, which is what the early rounds
    varied. It cannot see a behaviour change: a delegation cache, a nudge, a
    reworded tool description all leave the constants identical, so two runs of
    genuinely different agents stamp the same line and get averaged together.

    Hashing the module source, the Lead's prompt and every tool description
    catches all of them without a hand-kept list that would drift out of date.
    It says "these runs are the same code", never what changed -- git does that.
    """
    try:
        import hashlib
        import inspect
        import sys as _sys
        parts = [inspect.getsource(_sys.modules[__name__]),
                 _lead_instructions()]
        parts.extend(sorted(str(t.description or "") for t in TOOLBELT))
        # Flags decide behaviour without touching a byte of source, so hashing
        # source alone left the exact hole this function exists to close:
        # AI_SENTENCE_REPAIR=1 and =0 run different pipelines and stamped the
        # same fingerprint. Anything that gates a stage belongs here.
        parts.extend(["VERIFY_TOPUP=%s" % VERIFY_TOPUP,
                      "SHOW_UNCITED=%s" % SHOW_UNCITED,
                      "SCREEN_PAPERS=%s" % SCREEN_PAPERS,
                      "FRAMING_MAY_CITE=%s" % FRAMING_MAY_CITE,
                      "TOPUP_ENABLED=%s" % TOPUP_ENABLED,
                      "SENTENCE_REPAIR=%s" % SENTENCE_REPAIR,
                      "SDK_STREAM=%s" % os.getenv("AI_SDK_STREAM", ""),
                      "FULL_AGENT=%s" % os.getenv("AI_FULL_AGENT", "")])
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()[:10]
    except Exception:
        logger.debug("code fingerprint failed", exc_info=True)
        return "unknown"


def _ledger_note(ctx):
    remaining = max(0, int(ctx.hard_deadline - time.time()))
    note = ("\n[budget: %d searches left · %d s left · %d/%d tool-output chars"
            % (max(0, SEARCH_BUDGET - ctx.searches_used), remaining,
               ctx.tool_chars, TOOL_CHAR_BUDGET))
    # Coverage, not just constraint. Measured: citations track searches at about
    # 0.64 each, and two replicates of the same agent searched 28 and 16 times --
    # so what limits grounding is how much of its own map the agent has looked
    # for literature on. Budget tells it what it MAY spend; this tells it what is
    # still unlit. It remains free to decide the answer is "nothing".
    if ctx.partition is not None:
        units = len(ctx.partition.get("clusters") or []) + len(
            ctx.partition.get("standalone") or [])
        if units:
            note += (" · literature searched for %d of %d clusters"
                     % (len(ctx.searched_tags), units))
    return note + "]"


def _unrepresented_notes(notebook, text, limit=5, subjects=None):
    """Notes the agent recorded whose subject never reached the draft.

    notebook_write has 100% adoption -- every one of 25 real runs called it, a
    median of 3 times -- and until now its stored output had exactly two
    readers, both on failure paths: the forced synthesis and the model-free
    assembly. Measured across 64 archived runs, ONE hit those (2%). On the other
    98% the agent wrote notes that nothing ever read.

    That does not make the tool worthless -- the note stays in the SDK's
    conversation context, which is its own kind of rehearsal -- but a store with
    no reader cannot help the report. This gives it one, for free: a finding the
    agent thought worth recording and then left out is either a deliberate cut
    or a dropped finding, and only the agent knows which.

    Judged on entity-like tokens (gene symbols, pathway names) rather than
    whole-note matching, because a note is prose and the report rephrases it. A
    note with no identifiable entity is not judged at all.
    """
    lowered = (text or "").lower()
    subjects = list(subjects or [])
    missing = []
    for i, note in enumerate(notebook or []):
        subject = subjects[i].strip() if i < len(subjects) and subjects[i] else ""
        if subject:
            # Told, not inferred. The agent named what the note is about, so the
            # match is on that phrase rather than on entities guessed out of
            # prose -- the difference between a reader that is right and one
            # that is usually right.
            if subject.lower() in lowered:
                continue
        else:
            tokens = re.findall(
                r"\b[A-Z][A-Za-z0-9]{2,}\b|\b[a-z]+\d+[a-z0-9]*\b", note or "")
            if not tokens:
                continue                  # nothing checkable; do not guess
            if any(t.lower() in lowered for t in tokens):
                continue
        missing.append(" ".join(note.split())[:110])
    return missing[:limit]


async def _upgrade_new_citations(ctx, stats, refs):
    """Fetch full text for papers a later stage decided to cite.

    Bounded by the same clock reserve as every other optional step: what it does
    not reach stays an abstract, and the gate redacts whatever cannot be quoted --
    which is exactly the behaviour this exists to reduce, not to replace.
    """
    thin = [ctx.paper_index[r] for r in (refs or [])
            if r in ctx.paper_index
            and ctx.paper_index[r].get("fetch_tier") == "abstract_only"]
    if not thin:
        return
    left = (ctx.started_at + AGENT_RUN_SECONDS) - time.time() - GATE_MIN_SECONDS
    if left < 20:
        stats["topup_fulltext_skipped"] = "%.0fs left" % left
        return
    t0 = time.time()
    try:
        upgraded = await bounded(
            asyncio.to_thread(ctx.pubmed.fetch_papers,
                              [p["pmid"] for p in thin[:FULLTEXT_MAX_PAPERS]]),
            min(left, 60), label="top-up full text")
        by_pmid = {str(p.get("pmid")): p for p in (upgraded or [])}
        gained = 0
        for paper in thin[:FULLTEXT_MAX_PAPERS]:
            fresh = by_pmid.get(str(paper.get("pmid")))
            if fresh and fresh.get("fetch_tier") != "abstract_only":
                fresh["ref_index"] = paper["ref_index"]
                fresh["pathways"] = paper.get("pathways", [])
                ctx.paper_index[paper["ref_index"]] = fresh
                gained += 1
        stats["topup_fulltext_gained"] = gained
    except (Exception, asyncio.TimeoutError) as e:
        stats["topup_fulltext_failed"] = "%s: %s" % (type(e).__name__, e)
    stats["topup_fulltext_s"] = round(time.time() - t0, 1)


def _uncited_papers(paper_index, cited, limit=12):
    """Retrieved papers nothing in the draft cites, newest reference first.

    This is precisely the top-up's input, moved to where the agent can act on it.
    Measured round 39: the top-up costs 83.5 s -- 24% of the run -- and supplies 9
    of 26 citations by taking the finished report and adding markers to sentences
    that already stood on their own. That is the asymmetric bet priced earlier:
    a marker that verifies buys a citation, one that fails costs the whole
    sentence.

    The Lead already has check_my_citations in its belt, calls it in every run,
    and calls it while the draft can still change. Handing it the same list lets
    the citation be written into the sentence rather than bolted onto it, which
    is the difference between a claim that was drafted with evidence and one that
    had evidence attached afterwards.
    """
    missing = [p for ref, p in sorted((paper_index or {}).items())
               if ref not in (cited or set())]
    # Papers with full text first: a quotable sentence for a specific claim
    # usually sits in Results, and 30% of surviving quotes come from there.
    missing.sort(key=lambda p: not p.get("full_text_available"))
    return missing[:limit]


def _quote_evidence_lines(cited, quotes, limit=12):
    """The quotes themselves, for the agent to read against its own sentences.

    check_my_citations answers "does a supporting sentence exist". The gate asks
    "does THIS sentence support THAT claim", and measured across the archive its
    verdicts run 79.5% supported, 20.1% claim drift, 0.4% fabrication -- so
    roughly a fifth of the citations that pass the pre-submit check still lose
    their sentence at the gate, and until now the agent never saw the evidence
    it was being judged against. The quotes are already collected; withholding
    them was free to nobody.

    Only citations that will actually ship are listed -- the quote cache is
    seeded from earlier delegation and carries papers this draft never cites.
    """
    shown = sorted(set(cited) & set(quotes))
    if not shown:
        return []
    lines = ["The supporting quote found for each -- read it against YOUR "
             "sentence. Where it does not state your claim, narrow the claim to "
             "what it does say: the gate deletes the sentence, not just the "
             "citation."]
    for idx in shown[:limit]:
        quote = " ".join(str(quotes[idx]).split())      # one citation, one line
        lines.append('  [%d] "%s"' % (idx, quote[:180]))
    if len(shown) > limit:
        lines.append("  ...and %d more, same rule." % (len(shown) - limit))
    return lines


def _theme_conversion(searched_tags, cited_papers):
    """How many searched themes put a paper in the finished references.

    Returns (themes_searched, themes_that_converted). The tags on a paper are
    whatever the Lead typed into topic_tag, so they are matched the way
    search_literature stores them -- stripped and lowercased -- and a paper
    carrying no tag simply votes for nothing.
    """
    cited = {str(t).strip().lower()
             for paper in (cited_papers or [])
             for t in (paper.get("pathways") or []) if str(t).strip()}
    searched = {str(t).strip().lower() for t in (searched_tags or []) if str(t).strip()}
    return len(searched), len(cited & searched)


def _spend(ctx, text, tool=None):
    """Count a tool result against the character ledger before returning it.

    Seconds are only half of what a tool costs. Every character it returns
    enters the Lead's context and is re-sent on EVERY later Decide turn, so a
    tool that answers in 6 kB where 600 bytes would do is taxing the whole
    remainder of the investigation -- and the trace records a hand-written
    summary of each result, never its size. Attributing the ledger per tool is
    what makes "which tool is worth its place" answerable on context as well as
    on clock.
    """
    ctx.tool_chars += len(text)
    if tool:
        ctx.tool_chars_by_tool[tool] = (ctx.tool_chars_by_tool.get(tool, 0)
                                        + len(text))
    if ctx.tool_chars > TOOL_CHAR_BUDGET:
        return (text[: max(0, TOOL_CHAR_BUDGET - (ctx.tool_chars - len(text)))]
                + "\n[TOOL BUDGET EXHAUSTED — write your report from the "
                  "notebook and call submit_report now]")
    return text


def _time_guard(ctx):
    """Every tool names the wall it is about to hit; a backstop is never silent.

    The threshold is WRITE_RESERVE_SECONDS rather than a few seconds, because
    being told to stop with 30 s left is being told too late: the report is a
    long-form generation and one of those takes ~70-90 s on this gateway. A
    guard that fires only when there is no time left to write converts "the
    agent chose to stop" into "the loop was cut off mid-investigation with
    nothing submitted", which is the forced-synthesis path, not the design's.
    """
    if time.time() > ctx.hard_deadline - WRITE_RESERVE_SECONDS:
        return ("[TIME IS UP for investigating — %d s remain, and writing the "
                "report needs them. Do not call another data or literature "
                "tool. Write the report from your notebook and call "
                "submit_report now.]"
                % max(0, int(ctx.hard_deadline - time.time())))
    return None


# ---------------------------------------------------------------------------
# The toolbelt. Docstrings are the tool descriptions the model sees.
# ---------------------------------------------------------------------------

def _assemble_without_synthesis(ctx):
    """The report a run can still produce when no model is reachable.

    Measured: a 504 from the gateway killed the loop, then killed the forced
    synthesis that exists to rescue it, and a run holding two delegated analyses,
    a notebook and retrieved papers shipped nothing. This assembles what was
    gathered -- no model, no network -- and says what it is at the top so no
    reader mistakes it for a finished interpretation.

    Returns "" when nothing was gathered, so the caller can fail honestly
    instead of shipping a stub.
    """
    pieces = ["# Interpretation (assembled without synthesis)",
              "",
              "*The language model became unavailable before this report could "
              "be written. What follows is the material the run had already "
              "gathered: the analyses its sub-agents completed, the findings it "
              "recorded, and the enrichment data itself. It has not been "
              "synthesised, and its citations have not been through the usual "
              "verification.*"]
    if ctx.notebook:
        pieces += ["", "## Findings recorded during the investigation", ""]
        pieces += ["- %s" % n for n in ctx.notebook]
    if ctx.delegated:
        pieces += ["", "## Pathway analyses", "", "\n\n".join(ctx.delegated)]
    report = "\n".join(pieces)
    return report if (ctx.notebook or ctx.delegated) else ""


def _pathways_named(text, ctx):
    """How many of the run's pathways this text actually discusses.

    The merge needs a second currency besides grounding. A stitch that adds
    35 000 characters and no grounded citation is padding; the same stitch is
    worth taking if it doubles the pathways the report covers, which is what
    round 32 measured -- coverage 10 to 19 with grounding flat at 9.
    """
    lowered = (text or "").lower()
    return sum(1 for p in ctx.pathways
               if p.get("name") and str(p["name"]).lower() in lowered)


def _verified_quotes(ctx, quotes):
    """Keep only quotes the gate's own matcher can find in their paper.

    Used on BOTH sides of the merge comparison. Filtering only the candidate
    compared a strict count against a lenient one, so the guard could not accept
    anything: one run reported 15 unverifiable quotes, rejected a stitch that was
    genuinely better, and shipped the thin draft instead. An asymmetric test is
    not a strict test, it is a broken one.
    """
    out = {}
    for ref, quote in (quotes or {}).items():
        paper = ctx.paper_index.get(ref) or {}
        text = " ".join((paper.get("sections") or {}).values()) or (
            paper.get("abstract") or "")
        # _fuzzy_contains(haystack, needle): is the QUOTE inside the PAPER.
        # These were reversed, asking whether a whole paper fits inside a
        # one-sentence quote, which is never true -- so every quote was judged
        # unverifiable, the merge saw 0 grounded on both sides, and the guard
        # lost the only signal it had. Round 29 reported quotes_unverifiable 11
        # of 11 with merge_grounded 0->0.
        if quote and text and _fuzzy_contains(text, quote):
            out[ref] = quote
    return out


def _ctx_by_id(ctx):
    """id -> pathway context dict, for the cluster renderers."""
    return {p["id"]: p for p in ctx.pathways}


_FOLLOWUP_RE = re.compile(r"^##+\s*Suggested Follow[- ]?up.*$", re.M | re.I)
_DETAIL_RE = re.compile(r"^##+\s*Detailed Pathway Analysis.*$", re.M | re.I)
_FINDINGS_RE = re.compile(r"^##+\s*Key Findings.*$", re.M | re.I)


def _lead_framing(report):
    """(head, tail) from the Lead's own report, or (None, None) if it did not
    write the sections the splice needs.

    Deliberately strict: a partial match would splice the detail into a report
    missing the sections that frame it, which is worse than paying for the LLM
    call. Falling back is cheap; shipping a malformed report is not.
    """
    text = str(report or "")
    follow = _FOLLOWUP_RE.search(text)
    if not follow or not _FINDINGS_RE.search(text):
        return None, None
    detail = _DETAIL_RE.search(text)
    head_end = detail.start() if detail and detail.start() < follow.start() \
        else follow.start()
    head = text[:head_end].rstrip()
    tail = text[follow.start():]
    if not head.strip() or not tail.strip():
        return None, None
    return head, tail


def _build_framing_prompt(ctx, report, detail):
    """The framing call's prompt, built where it can be TESTED.

    It lived inline inside _run_loop_async, so every test of it inspected
    source text instead of building it -- and a precedence bug survived all
    of them. `base + (branch_a if flag else branch_b) % (report, detail)`
    formats the BRANCH, because % binds tighter than +, and the off-branch
    has no placeholders. That is a TypeError 153 s into every agent
    replicate, on the DEFAULT path, from a change meant to be dark.
    """
    return (
        "You are finishing a multi-omics interpretation report.\n\n"
        "## The lead interpreter's draft\n%s\n\n"
        "## The per-pathway analyses that will follow it verbatim\n%s\n\n"
        "## Task\nWrite ONLY these sections, in this order, as markdown:\n"
        "## Key Findings (3-5 bullets, the most important results across "
        "everything above)\n"
        "## Cross-Pathway Themes (shared mechanisms and crosstalk; name "
        "the pathways)\n\n"
        "Then stop -- do NOT write a pathway-by-pathway section, it is "
        "already written and will be appended after yours. Afterwards add:\n"
        "## Suggested Follow-up Experiments (3-5, prioritised, each with "
        "technique, rationale, expected outcome)\n"
        "## Limitations and Caveats\n\n"
        # The %-format is applied to THIS string and nothing else. An
        # earlier version wrote `base + (branch_a if flag else branch_b)
        # % (report, detail)` -- and % binds tighter than +, so the
        # formatting landed on the BRANCH, which has no placeholders when
        # the flag is off. That broke the DEFAULT path with a TypeError
        # 153 s into every agent replicate, and no test caught it because
        # every test inspected the source instead of building the prompt.
        % (report, detail[:60000])
        + (("Cite from the reference list below where a paper genuinely "
            "supports a claim you make in YOUR sections. Do not touch the "
            "per-pathway analyses' own markers and do not renumber "
            "anything.\n\n## Reference list you may cite\n%s")
           % _citable_reference_list(ctx)
           if FRAMING_MAY_CITE else
           "Reuse [N] citation markers ONLY where they already appear "
           "above for that claim. Do not invent markers and do not "
           "renumber."))

def _citable_reference_list(ctx, limit=40):
    """The papers the framing call may cite, as [N] title (author, year).

    Only papers already in the index, so a marker cannot point at nothing; the
    gate would redact it anyway, but a citation that was never citable wastes a
    verification slot. Uncited papers come first -- they are the ones a theme
    gap is made of.
    """
    papers = [ctx.paper_index[k] for k in sorted(ctx.paper_index)]
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", "\n".join(ctx.delegated))}
    papers.sort(key=lambda p: p.get("ref_index") in cited)
    return "\n".join(
        "[%d] %s (%s, %s)" % (p["ref_index"], (p.get("title") or "")[:110],
                              p.get("first_author", "?"), p.get("year", "?"))
        for p in papers[:limit])


def _profile_summary(profiles):
    """One line per OMICS LAYER, not one per feature.

    A tool should answer, not dump. Measured on a real job: 8 pathways showed 80
    genes carrying 540 omic profiles -- 6.8 per gene -- and 355 of them were
    miRNA-seq, because several miRNAs target one gene. Ccr2 alone showed SEVEN
    profiles, five of them anonymous miRNA series: no identity is carried on a
    profile, so the agent could not tell them apart, cite one, or act on the
    difference. Five unnamed 56-character series is noise that costs context in
    every later Decide turn.

    A layer with one feature still shows its series -- that is the trustworthy,
    identifiable form. A layer with several is summarised: how many, the
    direction split, and the strongest feature's own series, which is the one an
    interpretation would reach for. start_end_fc, peak_value and peak_timepoint
    were already computed for every profile and used by nothing.
    """
    layers = OrderedDict()
    for op in (profiles or []):
        name = op.get("omic_name") or op.get("omic") or "omic?"
        layers.setdefault(name, []).append(op)
    out = []
    for name, ops in layers.items():
        if len(ops) == 1:
            op = ops[0]
            out.append("%s: %s (%s)"
                       % (name, op.get("values", op.get("value_pairs", "")),
                          op.get("pattern", "")))
            continue

        def _fc(op):
            try:
                return float(op.get("start_end_fc") or 0.0)
            except (TypeError, ValueError):
                return 0.0
        up = sum(1 for op in ops if _fc(op) > 0)
        strongest = max(ops, key=lambda op: abs(_fc(op)))
        out.append("%s: %d features, %d up / %d down; strongest %+.2f "
                   "start->end: %s (%s)"
                   % (name, len(ops), up, len(ops) - up, _fc(strongest),
                      strongest.get("values", strongest.get("value_pairs", "")),
                      strongest.get("pattern", "")))
    return "; ".join(out)


def _pathway_block(p):
    lines = ["### %s (%s, %s)" % (p.get("name"), p.get("id"), p.get("source"))]
    lines.append("Combined p=%.3g · global p=%s · significant omic layers: %s"
                 % (p.get("combined_pvalue") or 1.0, p.get("global_pvalue"),
                    p.get("significant_omic_count")))
    if p.get("per_omic"):
        lines.append("Per-omic: %s" % p["per_omic"])
    for g in (p.get("top_genes") or [])[:10]:
        # context_builder emits "omic_name"; this read "omic" and got None every
        # time, so every gene line the agent saw was "None: -0.42@0h, ...; None:
        # ...; None: ..." -- three unlabelled layers per gene in a MULTI-OMICS
        # tool, with no way to tell transcript from protein from metabolite.
        # Found by measuring context cost: get_pathway_details is 33.7% of the
        # per-tool character bill, the largest single consumer, so it was the
        # first thing read closely.
        if LEAN_PROFILES and not g.get("relevant"):
            # Named, scored, and explicitly labelled -- so the agent can see the
            # gene matched the pathway without paying for a flat time course.
            lines.append("- %s [effect %.2f] (matched, not differential)"
                         % (g.get("symbol"), g.get("effect_size") or 0))
            continue
        profs = _profile_summary(g.get("omic_profiles"))
        lines.append("- %s%s [effect %.2f] %s"
                     % (g.get("symbol"), "*" if g.get("relevant") else "",
                        g.get("effect_size") or 0, profs))
    if SHOW_COMPOUNDS:
        compounds = p.get("top_compounds") or []
        if compounds:
            lines.append("Matched metabolites (%d of %d shown, Metabolomics layer):"
                         % (len(compounds), p.get("matched_compound_count") or 0))
            for c in compounds:
                lines.append("- %s%s [effect %.2f] %s (%s)"
                             % (c.get("name"), "*" if c.get("relevant") else "",
                                c.get("effect_size") or 0, c.get("values"),
                                c.get("pattern")))
        elif p.get("matched_compound_count"):
            # Absent and uninteresting must not look alike: the pathway matched
            # compounds but none carried a usable series.
            lines.append("Matched metabolites: %d matched, none with values."
                         % p["matched_compound_count"])
    return "\n".join(lines)


def _tool_failure(name):
    """Make a raising tool visible instead of silently generic.

    The SDK catches any exception a tool raises and hands the model
    "An error occurred while running the tool", then carries on. Because _trace
    runs at the END of each tool, a raise also means no trace event at all -- so
    a tool that fails on every call looks, in the archive, exactly like a tool
    the agent chose not to use. Every adoption and cost figure measured so far
    counts successful calls only.

    This was not theoretical: the first version of the delegation-cache tests
    passed against a fixture that raised KeyError on every call, because the
    swallowed error came back as an ordinary string.

    The handler records the failure in the run journal (so it reaches the
    frontend activity feed and the trace archive) and tells the model what broke
    and not to repeat it unchanged.
    """
    def handler(ctx, error):
        c = getattr(ctx, "context", None)
        detail = "%s: %s" % (type(error).__name__, error)
        if c is not None:
            try:
                _trace(c, name, "(raised)", "ERROR " + detail, time.time())
            except Exception:
                logger.debug("failed to trace a tool failure", exc_info=True)
        logger.warning("[AGENT] tool %s raised: %s", name, detail, exc_info=True)
        return ("%s failed -- %s. This is a fault in the tool, not in what you "
                "asked for. Do not call it again with the same arguments; use "
                "another tool or carry on with what you have." % (name, detail))
    handler.tool_name = name
    return handler


@function_tool(failure_error_function=_tool_failure("get_experiment_overview"))
def get_experiment_overview(ctx: RunContextWrapper[LoopContext]) -> str:
    """The experiment at a glance: design, enriched pathway table, cross-omic matrix and key regulators. Start here."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    major, _minor = triage_pathways(c.pathways)
    parts = ["Organism: %s" % c.organism_name,
             "Design: %s" % (c.experiment_design or "(none given)"),
             render_pathway_table(c.pathways)]
    try:
        parts.append(build_cross_omic_matrix(major))
    except Exception as e:
        logger.warning("[%s][loop] cross-omic matrix failed: %s", c.job_id, e)
    try:
        parts.append(build_key_regulators_block(c.job_instance, limit=30))
    except Exception as e:
        logger.warning("[%s][loop] regulators block failed: %s", c.job_id, e)
    if SHOW_COMPOUNDS:
        # Genes get build_key_regulators_block; compounds had no equivalent, and
        # the pathway table cannot stand in for one -- the polyamine pathway is
        # rank #421 of 887 here while the polyamines themselves are the paper's
        # finding. A metabolite that changes in an unenriched pathway is
        # unreachable through pathway context by construction.
        try:
            block = build_differential_metabolites_block(c.job_instance, limit=20)
            if block:
                parts.append(block)
        except Exception as e:
            logger.warning("[%s][loop] metabolite block failed: %s", c.job_id, e)
    out = _spend(c, "\n\n".join(x for x in parts if x) + _ledger_note(c),
                 "get_experiment_overview")
    _trace(c, "get_experiment_overview", "", "%d chars" % len(out), t0)
    return out


@function_tool(failure_error_function=_tool_failure("get_pathway_details"))
def get_pathway_details(ctx: RunContextWrapper[LoopContext],
                        pathway_names: list[str]) -> str:
    """Detailed data (p-values per layer, top genes with temporal profiles) for the named or ID'd pathways. Instant and free -- read the data before theorising about it, and ask for several pathways at once. Name as many as you like: if more come back than fit in one answer, the rest are listed by name for you to ask for again."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    # Resolve in the order the Lead ASKED, not in rank order. The old loop walked
    # c.pathways and stopped at eight, so the eight it returned were the eight
    # best-ranked of whatever matched -- the Lead's own ordering, which is the
    # only place its judgement about this experiment was expressed, was discarded
    # before the answer was built.
    blocks, matched_ids, deferred, unresolved = [], [], [], []
    seen = set()
    for raw in [w.strip() for w in pathway_names if w and w.strip()]:
        needle = raw.lower()
        hit = None
        for p in c.pathways:
            keys = (str(p.get("name", "")).lower(), str(p.get("id", "")).lower())
            if needle in keys or any(needle in k for k in keys):
                hit = p
                break
        if hit is None:
            unresolved.append(raw)
            continue
        pid = str(hit.get("id"))
        if pid in seen:
            continue
        seen.add(pid)
        if len(blocks) >= DETAIL_MAX_BLOCKS:
            deferred.append(str(hit.get("name") or raw))
            continue
        # The flat-gene share is what decides whether LEAN_PROFILES can save
        # anything at all. I predicted a 25% cut from it without ever having
        # measured the fraction it depends on, so the prediction was a guess
        # wearing a number. Counted here, where ctx is in scope.
        shown = (hit.get("top_genes") or [])[:10]
        c.genes_shown += len(shown)
        c.genes_flat += sum(1 for g in shown if not g.get("relevant"))
        blocks.append(_pathway_block(hit))
        matched_ids.append(hit.get("id"))

    notes = []
    if deferred:
        # Truncation the caller is told about is a pause; truncation it is not
        # told about is a silent narrowing of the investigation.
        c.extra_stats["detail_deferred"] = (
            c.extra_stats.get("detail_deferred", 0) + len(deferred))
        notes.append(
            "You named %d pathways and this answer carries %d of them, in the "
            "order you asked. STILL WAITING, ask again in another call: %s. "
            "get_pathway_details is instant and unlimited in how often you may "
            "call it -- nothing here is a decision that these pathways matter "
            "less."
            % (len(blocks) + len(deferred), len(blocks), ", ".join(deferred)))
    if unresolved:
        notes.append("No enriched pathway matches: %s." % ", ".join(unresolved))
    if not blocks:
        out = ("No match among the %d significant pathways for %s. The first 30 "
               "by rank are: %s"
               % (len(c.pathways), pathway_names,
                  ", ".join(p.get("name", "?") for p in c.pathways[:30])))
    else:
        out = "\n\n".join(blocks)
        if notes:
            out += "\n\n" + "\n\n".join(notes)
        out += _ledger_note(c)
    out = _spend(c, out, "get_pathway_details")
    _trace(c, "get_pathway_details", pathway_names, matched_ids or "none", t0)
    return out

@function_tool(failure_error_function=_tool_failure("cluster_pathways"))
def cluster_pathways(ctx: RunContextWrapper[LoopContext]) -> str:
    """Group the significant pathways by shared matched features (deterministic; no LLM). Returns clusters with their shared gene cores. Costs about half a second; worth calling once, early."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    if c.partition is None:
        try:
            candidate = clusters_mod.build_partition(
                c.job_instance, always_include=[p["id"] for p in c.pathways])
            member_ids = clusters_mod.partition_member_ids(candidate)
            if candidate.get("clusters") and member_ids:
                c.partition = candidate
                # Clustering used to be what WIDENED the agent's reach: the
                # context arrived as the top fifteen and the partition covers
                # every significant pathway, so this rebuilt over its members
                # and was the only path to the rest of the experiment. That made
                # the agent's territory a side effect of calling a clustering
                # tool -- see _significant_universe, which now hands over the
                # significant set at turn zero.
                #
                # So this is normally a no-op and skipped: partition members and
                # the universe are the same set. It stays for the case where the
                # partition somehow names an id the context does not hold, which
                # would otherwise put a pathway in the cluster table that
                # get_pathway_details cannot look up -- a map with no territory
                # behind it.
                if set(member_ids) - {str(p.get("id")) for p in c.pathways}:
                    c.pathways = build_pathway_context(c.job_instance,
                                                       pathway_ids=member_ids)
                if (c.hooks or {}).get("partition"):
                    try:
                        c.hooks["partition"](candidate)
                    except Exception:
                        logger.debug("partition hook failed", exc_info=True)
                if (c.hooks or {}).get("pathways"):
                    try:
                        c.hooks["pathways"](c.pathways)
                    except Exception:
                        logger.debug("pathway-index hook failed", exc_info=True)
        except Exception as e:
            out = "Clustering failed: %s" % e
            _trace(c, "cluster_pathways", "", out, t0)
            return out
    if c.partition is None:
        out = "No clusters found: the significant pathways share too few features."
    else:
        out = clusters_mod.render_partition_table(c.partition, _ctx_by_id(c))
    out = _spend(c, out + _ledger_note(c), "cluster_pathways")
    _trace(c, "cluster_pathways", "",
           clusters_mod.partition_summary(c.partition) if c.partition else "none", t0)
    return out


async def _screen_papers(ctx, papers, query, topic_tag):
    """Keep only the papers that could carry a quotable finding for this claim.

    Base's Paper Filter, ported into this arm's search tool. Three outcomes and
    only one of them is a judgement: a screener that ANSWERS keeps its picks, a
    screener that fails keeps everything (a broken screen must not silently empty
    the pool), and an explicit empty answer keeps nothing, because "nothing here
    fits" is the most useful thing a strict filter can say.

    Reuses SYSTEM_PROMPT_SEARCH_SUBAGENT and RelevantPMIDs so both arms screen to
    the same standard -- if the standard is wrong, it is wrong in one place.
    """
    if not papers:
        return papers, 0
    listing = "\n".join(
        "PMID %s: %s\n%s" % (p.get("pmid"), p.get("title", ""),
                              (p.get("abstract") or "")[:600])
        for p in papers)
    screener = Agent[LoopContext](
        name="Paper Screen",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_SEARCH_SUBAGENT,
        model_settings=ModelSettings(temperature=0.1),
        output_type=RelevantPMIDs,
        tools=[],
    )
    # Strictness stays CONSTANT. Round 40 made it depend on the pool -- permissive
    # below half the target, on the reasoning that a thin paper beats an empty
    # theme -- and measured the cost over three replicates: keep rate rose
    # 24% -> 28-32%, the pool barely moved (27 -> 29), and failures went
    # 0.50 -> 3.33, redactions 1.2 -> 8.7, coverage 16.2 -> 11.7.
    #
    # The bar is what makes a screened paper worth 0.91 citations. Lowering it
    # admits papers that cannot be quoted, their citations fail, and redaction
    # takes the sentence and its pathway mention with them. A thin paper does NOT
    # beat an empty theme; it costs the sentence it lands on.
    #
    # Starvation (round 39 r3 kept 9% and shipped 8 citations) is real, and the
    # answer is more candidates, not a lower standard: raise supply, keep the bar.
    # ONE standard, at every pool size. Both attempts to make it depend on the
    # pool have now been refuted by measurement:
    #
    #   the FLOOR (round 40): below half the target, "a thin paper beats an empty
    #   theme". Keep rate rose to 28-32%, failures 0.50 -> 3.33, redactions
    #   1.2 -> 8.7. The bar is what makes a screened paper worth citing.
    #
    #   the CEILING (added and removed within one round): past the target, keep
    #   only what is clearly stronger. I justified it on ONE replicate -- pool 55
    #   shipping 22 citations against a 37-paper run's 26 -- and the next
    #   replicate landed at pool 89 with 24 citations, coverage 16, zero
    #   redactions. Across ten screened runs r(pool, citations) = +0.62 and the
    #   median above 35 papers is 23 against 18 below it. The pool-37 runs
    #   themselves span 18 to 26, so the within-condition spread is wider than
    #   the effect I attributed to pool size.
    #
    # More screened literature is better, or at worst neutral. What is not better
    # is a lower bar.
    stance = ("Keep the ones with a specific quotable finding. You hold %d "
              "papers and about %d is a healthy pool for this report."
              % (len(ctx.paper_index), SCREEN_TARGET_POOL))
    # Each piece is finished BEFORE it is joined: writing this as
    # `("...%s..." + stance + "...") % args` puts the formatting on the trailing
    # literal, because % binds tighter than +. That bug has cost this project two
    # rounds already.
    header = ("Experiment: %s\nOrganism: %s\nTheme: %s\nQuery: %s\n\n"
              "Candidate papers:\n%s\n\n"
              % (ctx.experiment_design, ctx.organism_name, topic_tag or "-",
                 query, listing))
    task = ("Return ONLY the PMIDs that could support a claim in a report about "
            "this experiment. The test is whether the paper holds a specific, "
            "quotable finding about the MECHANISM these genes take part in. "
            "REJECT anything sharing only a keyword -- above all a paper matched "
            "because a pathway is NAMED after a disease -- reviews with nothing "
            "specific to quote, and results running opposite to what is "
            "described. A kept paper with no quotable finding COSTS a citation, "
            "because the claim it gets attached to is removed with it.")
    prompt = header + stance + "\n\n" + task
    try:
        res = await bounded(Runner.run(screener, prompt, context=ctx, max_turns=2),
                            SDK_CALL_TIMEOUT, label="screen")
        answered = {str(x).strip() for x in (res.final_output.pmids or [])}
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning("[%s][loop] paper screen failed (%s); keeping all %d",
                       ctx.job_id, e, len(papers))
        return papers, 0
    kept = [p for p in papers if str(p.get("pmid")) in answered]
    return kept, len(papers) - len(kept)


def _register_papers(c, papers, tag):
    """Give new papers a global ref_index and remember them; dedup by PMID."""
    listed = []
    for paper in papers:
        pmid = str(paper.get("pmid") or "")
        if not pmid:
            continue
        if pmid in c.pmid_to_ref:
            ref = c.pmid_to_ref[pmid]
            existing = c.paper_index[ref]
            if tag and tag not in existing.setdefault("pathways", []):
                existing["pathways"].append(tag)
        else:
            ref = c.next_ref
            c.next_ref += 1
            c.pmid_to_ref[pmid] = ref
            paper = dict(paper)
            paper.setdefault("sections", {"abstract": paper.get("abstract", "")})
            paper.setdefault("fetch_tier", "abstract_only")
            paper.setdefault("full_text_available", False)
            paper["ref_index"] = ref
            paper["pathways"] = [tag] if tag else []
            c.paper_index[ref] = paper
        p = c.paper_index[c.pmid_to_ref[pmid]]
        listed.append("[%d] %s (%s, %s) — %s"
                      % (p["ref_index"], p.get("title", "")[:110],
                         p.get("first_author", "?"), p.get("year", "?"),
                         (p.get("abstract") or "")[:240]))
    return listed


@function_tool(failure_error_function=_tool_failure("search_literature"))
async def search_literature(ctx: RunContextWrapper[LoopContext], query: str,
                            topic_tag: str) -> str:
    """Search PubMed (about 2 s). Returns papers as [N] entries you may cite. Keep queries BROAD: two or three gene symbols joined by OR, AND at most one biological term, e.g. "(Ikzf1 OR Ccnd2) AND B cell differentiation". Extra AND clauses return nothing and still cost budget. topic_tag names the pathway/theme this search supports. Spend-metered."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    if c.searches_used >= SEARCH_BUDGET:
        out = ("SEARCH BUDGET EXHAUSTED (%d used). Cite what you have or "
               "read_paper for depth; do not ask again." % c.searches_used)
        _trace(c, "search_literature", query, "budget exhausted", t0)
        return out
    c.searches_used += 1
    if topic_tag:
        # Recorded HERE, before any hit is fetched or screened, and that ordering
        # is the metric's whole validity. `searched_tags` is the only denominator
        # the paper screen cannot shrink: themes_retrieved counts themes that
        # brought a paper back, so a screen rejecting every hit for a theme
        # removes it from the denominator too and
        # themes_cited/themes_retrieved rises with no extra citation earned.
        # A search that ran is a search that ran, however little survives it.
        c.searched_tags.add(str(topic_tag).strip().lower())
    try:
        pmids = await asyncio.to_thread(c.pubmed.search, query, SEARCH_HITS)
        new = [p for p in pmids if str(p) not in c.pmid_to_ref]
        papers = (await asyncio.to_thread(c.pubmed.fetch_abstracts, new)
                  if new else [])
        screened_here = 0
        if SCREEN_PAPERS and papers:
            papers, screened_here = await _screen_papers(c, papers, query, topic_tag)
            c.papers_screened_out += screened_here
    except Exception as e:
        out = "Search failed (%s). The budget was still spent." % e
        _trace(c, "search_literature", query, out, t0)
        return out + _ledger_note(c)
    listed = _register_papers(c, papers, topic_tag)
    for pmid in pmids:                       # already-known hits are re-listed
        ref = c.pmid_to_ref.get(str(pmid))
        if ref and not any(l.startswith("[%d]" % ref) for l in listed):
            p = c.paper_index[ref]
            listed.append("[%d] %s (%s) — already retrieved"
                          % (ref, p.get("title", "")[:110], p.get("year", "?")))
    if not listed and screened_here:
        # Empty for the OPPOSITE reason, and the opposite advice. The screen
        # rejected everything PubMed returned, so the query reached literature
        # that exists and does not hold a quotable mechanism finding. Telling
        # the agent to broaden here -- which is what the message below says --
        # would return more of the same and spend more budget having it
        # rejected again. This is a defect I introduced with the screen: the
        # only signal the tool had for "nothing to show" was an empty list.
        body = ("PubMed matched %d paper(s) for that query and the screen "
                "rejected all of them -- they share the keywords but hold no "
                "specific quotable finding about this mechanism. Broadening "
                "will return more of the same. Try a different mechanism angle, "
                "a different gene set, or accept that this theme has no "
                "literature worth citing." % screened_here)
    elif not listed:
        # Measured in the first live runs: 7 of 14 searches came back empty
        # because the query stacked too many AND terms. The budget was spent
        # either way, so the result says how to spend the next one better.
        body = ("no hits. PubMed matched nothing for that query -- it is "
                "probably too narrow. Drop an AND clause, use gene symbols "
                "with OR, or search the biology without the organism term.")
    else:
        body = "\n".join(listed)
    if listed and screened_here:
        body += ("\n(%d further hit(s) were screened out as keyword-only.)"
                 % screened_here)
    out = "Results for '%s': %s%s" % (query, body, _ledger_note(c))
    out = _spend(c, out, "search_literature")
    _trace(c, "search_literature", query, "%d hits, %d new" %
           (len(pmids), len(listed)), t0)
    return out


@function_tool(failure_error_function=_tool_failure("read_paper"))
async def read_paper(ctx: RunContextWrapper[LoopContext], ref_index: int,
                     section: str) -> str:
    """Read one section (abstract, introduction, results, discussion, other) of a retrieved paper [N]. section="abstract" is free and instant -- the search already fetched it. Any other section fetches full text on first use, about 3 s. Use it to check a paper really says what you want to cite it for. Reading is for deciding: a paper you cite has its full text fetched anyway, so reading first does not by itself make a citation survive."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    paper = c.paper_index.get(int(ref_index))
    if paper is None:
        out = "No paper [%s]. Cite only indices search_literature returned." % ref_index
        _trace(c, "read_paper", ref_index, "unknown ref", t0)
        return out
    # search_literature already fetched every abstract into paper_index, and
    # 342 of 378 reads across the archive asked for exactly that -- yet the
    # upgrade below ran first regardless, paying ~2.6 s of NCBI time to return
    # text the process was already holding. Upgrade only when the request needs
    # more than the abstract, or when the abstract came back empty.
    wants_abstract = str(section or "").strip().lower() == "abstract"
    have_abstract = bool(((paper.get("sections") or {}).get("abstract") or "").strip())
    if paper.get("fetch_tier") == "abstract_only" and not (wants_abstract
                                                           and have_abstract):
        try:
            upgraded = await asyncio.to_thread(c.pubmed.fetch_papers,
                                               [paper["pmid"]])
            if upgraded:
                fresh = upgraded[0]
                fresh["ref_index"] = paper["ref_index"]
                fresh["pathways"] = paper.get("pathways", [])
                c.paper_index[paper["ref_index"]] = fresh
                paper = fresh
        except Exception as e:
            logger.warning("[%s][loop] full-text upgrade failed for %s: %s",
                           c.job_id, paper.get("pmid"), e)
    executor = tools_mod.build_verification_executor(c.paper_index)
    out = executor("fetch_paper_section",
                   {"ref_index": int(ref_index), "section": section})
    # An abstract re-read costs a TURN for text the agent already has. Measured
    # across the archive: 477 of 517 read_paper calls (92%) asked for the
    # abstract, against 32 for results and 7 for everything else -- roughly six
    # of a run's ~39 turns spent re-reading the listing. The tool answers anyway,
    # because refusing would strand a plan mid-step, but it says what would
    # actually be new. The deeper sections are where 30% of surviving quotes come
    # from (47 of 157 across seven runs), so this is a nudge toward the tier that
    # earns its cost, not away from reading.
    if str(section).strip().lower() in ("", "abstract"):
        deeper = [k for k, v in (paper.get("sections") or {}).items()
                  if k != "abstract" and v]
        out += ("\n[This abstract was already in your search results, so this "
                "turn added no new text. %s]"
                % ("Sections you have not seen: %s." % ", ".join(sorted(deeper))
                   if deeper else
                   "Ask for results or discussion to fetch full text -- that is "
                   "where a quotable sentence for a specific claim usually sits."))
        c.abstract_rereads += 1
    out = _spend(c, out + _ledger_note(c), "read_paper")
    _trace(c, "read_paper",
           "[%s] %s pmid=%s" % (ref_index, section, paper.get("pmid")),
           "%d chars" % len(out), t0)
    return out


@function_tool(failure_error_function=_tool_failure("notebook_write"))
def notebook_write(ctx: RunContextWrapper[LoopContext], note: str,
                   subject: str) -> str:
    """Record a finding, hypothesis or open question in your run notebook. Free. subject is the pathway, gene or theme it is about -- one short phrase, the same way topic_tag names a search. Write one after every substantive discovery -- it is what the report is assembled from."""
    c = ctx.context
    t0 = time.time()
    c.notebook.append(note.strip())
    c.note_subjects.append((subject or "").strip())
    if (c.hooks or {}).get("notebook"):
        try:
            c.hooks["notebook"](list(c.notebook))
        except Exception:
            logger.debug("notebook hook failed", exc_info=True)
    out = "Noted (%d entries)." % len(c.notebook)
    _trace(c, "notebook_write", note[:80], out, t0)
    return out


@function_tool(failure_error_function=_tool_failure("check_my_citations"))
def check_my_citations(ctx: RunContextWrapper[LoopContext], draft: str) -> str:
    """Check a draft's [N] citations BEFORE submitting: which resolve to real papers, and which have no supporting quote and will therefore be dropped. Costs a few seconds. Shows the quote found for each surviving citation, so you can check it really states your claim. Run it on your draft, fix what it names, then run it AGAIN -- the second run is what turns a flagged citation into a grounded one."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    valid = set(c.paper_index)
    # Check what will SHIP, not just what was typed here. The gate merges the
    # delegated analyses into the report, and measured over rounds 25-27 those
    # carried most of the citations: an agent submitted 11 citations it had
    # checked and grounded and the report went out with 6, because the merge
    # brought in citations this tool had never seen. Checking the draft alone is
    # checking the wrong artifact.
    delegated = "\n\n".join(c.delegated)
    text = normalize_citation_markers(draft)
    shipping = normalize_citation_markers(draft + "\n\n" + delegated) if delegated else text
    cited_draft = count_body_citations(text, valid)
    cited = count_body_citations(shipping, valid)
    text = shipping
    invalid = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)} - valid)

    # The real question. verify_report_v2 cannot answer it on a draft -- it reads
    # quotes out of a References section the draft does not have yet, so it
    # reported "0 failed" for every draft ever passed to it, in eight runs. What
    # decides whether a citation survives is whether a verbatim supporting
    # sentence exists in the paper, and _collect_cited_quotes answers exactly
    # that for ~3 s.
    quotes = {}
    try:
        quotes = dict(c.quotes)
        quotes.update(_collect_cited_quotes(
            LLMClient(AI_PROVIDERS[AI_LLM_PROVIDER]), text, c.paper_index,
            c.job_id, known=quotes))
        c.quotes.update(quotes)
    except Exception as e:
        logger.warning("[%s][loop] pre-submit quote check failed: %s", c.job_id, e)
    unquotable = sorted(cited - set(quotes))

    lines = ["%d citation(s) will ship (%d in this draft, the rest in the "
             "delegated analyses the gate merges in); %d have a supporting quote."
             % (len(cited), len(cited_draft), len(quotes))]
    if SHOW_CITATION_TARGET:
        short = MIN_CITATIONS - len(quotes)
        if short > 0:
            lines.append(
                "A finished report is expected to carry about %d GROUNDED "
                "citations and you have %d, so you are %d short. The gap is "
                "usually a paper you retrieved and never opened: read_paper, "
                "then cite the sentence you actually find. Do NOT add a marker "
                "you cannot quote to reach the number -- an unsupported "
                "citation costs the whole sentence it sits on, and you lose the "
                "finding as well as the marker."
                % (MIN_CITATIONS, len(quotes), short))
        else:
            lines.append("That meets the ~%d grounded citations a finished "
                         "report is expected to carry." % MIN_CITATIONS)
    if invalid:
        lines.append("INVALID, no such paper -- remove these: %s"
                     % ", ".join("[%d]" % i for i in invalid[:15]))
    if unquotable:
        lines.append("NO SUPPORTING QUOTE FOUND, these will be removed from the "
                     "report along with the sentence carrying them: %s"
                     % ", ".join("[%d]" % i for i in unquotable[:15]))
        lines.append("Either cite a different paper for that claim, soften the "
                     "claim to what the paper does say, or read_paper first and "
                     "cite the sentence you find.")
    if not invalid and not unquotable:
        lines.append("Every citation resolves and has a quote. That is NOT the "
                     "same as the quote supporting your sentence -- the gate "
                     "asks that next, and it is where most citations die.")
    # Show the quote itself. This tool answers "does a supporting sentence
    # exist"; the gate asks "does THIS sentence support THAT claim", and
    # measured over the archive those verdicts run 79.5% supported, 20.1% claim
    # drift, 0.4% fabrication -- so a fifth of the citations that pass this
    # check still lose their sentence at the gate, and the agent never saw the
    # evidence it was being judged against. The quotes are already collected
    # above; withholding them was free to nobody.
    lines.extend(_quote_evidence_lines(cited, quotes))
    unused = _uncited_papers(c.paper_index, cited) if SHOW_UNCITED else []
    if unused:
        lines.append("Retrieved papers your draft cites NOWHERE -- if one "
                     "genuinely supports a claim you are making, cite it in the "
                     "sentence now rather than leaving the gate to bolt it on "
                     "afterwards. Leaving a paper uncited is a fine outcome; "
                     "forcing one in costs the sentence it lands on:")
        lines.extend("  [%d] %s%s" % (p["ref_index"],
                                      (p.get("title") or "")[:95],
                                      " (full text)" if p.get("full_text_available") else "")
                     for p in unused)
    orphaned = _unrepresented_notes(c.notebook, text, subjects=c.note_subjects)
    if orphaned:
        lines.append("Findings you recorded that this draft does not mention -- "
                     "add them or drop them deliberately:")
        lines.extend("  - %s" % n for n in orphaned)
    # Remembered so submit_report can tell whether the agent acted on its own
    # check. Measured over 28 runs that called this tool: the 10 that re-checked
    # after a bad result improved every time (11/6 -> 7/0, 14/7 -> 8/0,
    # 10/4 -> 10/0), and none got worse. The 18 that checked once sometimes
    # submitted with citations this tool had already flagged.
    c.flagged_citations = set(unquotable)
    out = _spend(c, "\n".join(lines), "check_my_citations")
    _trace(c, "check_my_citations", "%d chars" % len(draft),
           "%d cited, %d unquotable" % (len(cited), len(unquotable)), t0)
    return out


async def _single_shot(agent, prompt, ctx, timeout, label):
    try:
        r = await bounded(Runner.run(agent, prompt, context=ctx, max_turns=2),
                          timeout, label=label)
        return str(r.final_output)
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning("[%s][loop] %s failed: %s", ctx.job_id, label, e)
        return ""


def _clean_passage(raw):
    """Strip the search tool's framing so the writer sees only paper text.

    search_paper_text answers a human: "Found 3 passage(s) in paper [1]:
    --- Passage 1 --- ...". Handing that to a model as evidence invites it to
    quote the framing, and the gate then looks for that sentence in the paper
    and does not find it.
    """
    body = re.sub(r'^\s*Found\s+\d+\s+passage\(s\)[^:]*:', '', raw).strip()
    parts = [p.strip() for p in re.split(r'-{2,}\s*Passage\s*\d+\s*-{2,}', body)]
    parts = [p for p in parts if len(p) > 40]
    if not parts:
        return ""
    # Two passages is enough context to write from without burying the prompt.
    return "  ".join(parts[:2])[:600]


async def _upgrade_chunk_papers(c, papers):
    """Full text for this chunk's abstract-only papers, before the shelf is built.

    Best-effort and bounded: a delegation that cannot get full text must still
    write from abstracts rather than fail or stall. The paper index is updated in
    place so a later chunk citing the same paper, and the gate's own upgrade
    step, both find the text already there.
    """
    thin = [p for p in papers
            if p.get("fetch_tier") == "abstract_only" and p.get("pmid")]
    if not thin:
        return papers
    t0 = time.time()
    try:
        fresh = await bounded(
            asyncio.to_thread(c.pubmed.fetch_papers,
                              [p["pmid"] for p in thin[:DELEGATE_PAPERS]]),
            DELEGATE_QUOTE_SECONDS, label="chunk full-text")
    except (Exception, asyncio.TimeoutError) as e:
        c.extra_stats["delegate_fulltext_failed"] = "%s: %s" % (type(e).__name__, e)
        return papers
    by_pmid = {str(p.get("pmid")): p for p in (fresh or [])}
    gained, out = 0, []
    for paper in papers:
        new = by_pmid.get(str(paper.get("pmid")))
        if new and new.get("fetch_tier") != "abstract_only":
            new["ref_index"] = paper.get("ref_index")
            new["pathways"] = paper.get("pathways", [])
            if paper.get("ref_index") is not None:
                c.paper_index[paper["ref_index"]] = new
            out.append(new)
            gained += 1
        else:
            out.append(paper)
    c.extra_stats["delegate_fulltext_gained"] = (
        c.extra_stats.get("delegate_fulltext_gained", 0) + gained)
    _trace(c, "chunk_fulltext", "%d thin" % len(thin),
           "%d upgraded" % gained, t0)
    return out


def _quote_shelf(c, chunk, papers):
    """Candidate supporting sentences, pulled BEFORE the sub-agent writes.

    The arm loses citations because it writes claims and then hunts for support.
    The shipped workflow arm does not have that problem: it writes each citation
    in the same call that holds the paper, so the quote is findable afterwards
    because the sentence was written from it.

    This inverts the sub-agent the same way. For each paper assigned to a chunk,
    pull the passages that match that chunk's pathways and genes and hand them to
    the writer as the evidence it may cite. search_paper_text is a 1 ms substring
    and keyword search -- no model, no gateway -- so a shelf for ten papers costs
    nothing measurable.
    """
    executor = tools_mod.build_verification_executor(c.paper_index)
    terms = [str(p.get("name") or "") for p in chunk if p.get("name")]
    for pathway in chunk:
        for gene in (pathway.get("top_genes") or [])[:4]:
            symbol = gene.get("symbol")
            if symbol:
                terms.append(str(symbol))
    shelf = {}
    for paper in papers:
        ref = paper.get("ref_index")
        if ref is None:
            continue
        for term in terms[:8]:
            try:
                passage = executor("search_paper_text",
                                   {"ref_index": ref, "query": term})
            except Exception:
                continue
            if passage and not passage.lower().startswith(("error", "no text",
                                                           "no match")):
                cleaned = _clean_passage(passage)
                if cleaned:
                    shelf[ref] = cleaned
                    break
    return shelf


def _delegation_capacity(ctx):
    """How many pathways this delegation can still afford, read off the clock.

    A fixed ceiling cannot know what time it is. Sixty pathways is twelve chunks,
    three waves, about 105 s: comfortable at minute two of a ten-minute run and
    impossible at minute eight, and the constant said sixty at both. So the one
    number that was supposed to protect the run protected it exactly when it did
    not need protecting.

    The cost is knowable instead of guessable. A wave is DELEGATE_CHUNK x
    DELEGATE_WORKERS pathways for about DELEGATE_WAVE_SECONDS, flat, because the
    sub-agents run in parallel. Two things still have to be paid after the last
    wave -- grounding the citations it produced, and writing the report -- so
    both come off the top before the division. Whatever is left over buys waves.

    Floored at one wave: a delegation tool that can return nothing is worse than
    a slow one, and _time_guard has already refused the call outright if there
    is genuinely no time. Ceiling only if someone pins DELEGATE_MAX_PATHWAYS for
    a benchmark round.
    """
    per_wave = max(1, DELEGATE_CHUNK * DELEGATE_WORKERS)
    if DELEGATE_MAX_PATHWAYS > 0:
        return DELEGATE_MAX_PATHWAYS
    left = (ctx.hard_deadline - WRITE_RESERVE_SECONDS
            - DELEGATE_QUOTE_SECONDS - time.time())
    waves = int(max(0.0, left) // max(1.0, DELEGATE_WAVE_SECONDS))
    return max(per_wave, waves * per_wave)


@function_tool(failure_error_function=_tool_failure("delegate_interpretation"))
async def delegate_interpretation(ctx: RunContextWrapper[LoopContext],
                                  pathway_names: list[str], focus: str) -> str:
    """Delegate deep interpretation of the named pathways to sub-agents (parallel, single-shot). Returns their reports; their [N] citations use your reference numbers. Name as many pathways as the experiment justifies -- no fixed quota; the tool works out what it can afford from the clock and names any it left. Cost is per WAVE, not per pathway: four at a time in chunks of five, so twenty pathways cost what five do -- roughly 35 seconds a wave. It is where breadth comes from, and every pathway you delegate is somewhere a citation can be earned. Make ONE call, never one per pathway."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    # Resolved in the order NAMED, so a trim for time drops the Lead's last
    # choices rather than the lowest-ranked. Walking c.pathways instead, as this
    # did, silently re-sorted the request into p-value order before truncating
    # it -- which is the ranking deciding the scope one more time, in the tool
    # that exists to let the agent decide it.
    chosen, seen, unresolved = [], set(), []
    for raw in [w.strip() for w in pathway_names if w and w.strip()]:
        needle = raw.lower()
        hit = None
        for p in c.pathways:
            keys = (str(p.get("name", "")).lower(), str(p.get("id", "")).lower())
            if needle in keys or any(needle in k for k in keys):
                hit = p
                break
        if hit is None:
            unresolved.append(raw)
            continue
        pid = str(hit.get("id"))
        if pid not in seen:
            seen.add(pid)
            chosen.append(hit)
    capacity = _delegation_capacity(c)
    trimmed = [str(p.get("name") or p.get("id")) for p in chosen[capacity:]]
    chosen = chosen[:capacity]
    if trimmed:
        c.extra_stats["delegate_trimmed_for_time"] = (
            c.extra_stats.get("delegate_trimmed_for_time", 0) + len(trimmed))
    if not chosen:
        out = "No enriched pathway matches %s." % pathway_names
        _trace(c, "delegate_interpretation", pathway_names, "no match", t0)
        return out
    # A delegation already run is not run again. Measured over 60 archived runs:
    # 7 of them re-issued an identical delegation, costing 25-62 s each (mean 40)
    # out of a 600 s budget, for an answer the run was already holding. That is
    # 99% of all wall clock this agent spends repeating itself -- every other
    # tool is cheap enough for a repeat not to matter.
    #
    # The key is the RESOLVED pathway set, not the argument spelling, so asking
    # for the same pathways by a different name still hits. A different focus is
    # a different question and runs.
    cache_key = (tuple(sorted(str(p.get("id")) for p in chosen)),
                 (focus or "").strip().lower())
    if cache_key in c.delegation_cache:
        cached = c.delegation_cache[cache_key]
        out = _spend(c, ("You have already delegated exactly these pathways with "
                         "this focus, and the analysis below is that same result "
                         "-- it was not run again, which just saved you about 30 "
                         "seconds. Do not request it a third time; spend the "
                         "budget on what is still uncovered.\n\n"
                         + cached + _ledger_note(c)),
                     "delegate_interpretation")
        _trace(c, "delegate_interpretation",
               "%d pathways (cached)" % len(chosen), "cache hit", t0)
        return out

    papers = [c.paper_index[k] for k in sorted(c.paper_index)]
    ctx_local = c
    # The loop's own interpreter: same model and settings as the workflow arm's
    # single-shot one, different instructions -- [N] markers on quotable claims
    # rather than "(PMID: X)" prose. Built here rather than in _build_agents so
    # the workflow arm keeps exactly the agents it was measured with.
    # REVERTED to the workflow arm's interpreter prompt, on evidence. Giving the
    # sub-agents their own instructions -- [N] markers, and "cite only where you
    # could point to a specific sentence" -- suppressed the citations the report
    # is built from: the merge went 5 -> 18 markers under the old prompt (round
    # 11) and 7 -> 10, then 7 -> 3, under the new one (rounds 13-15), until the
    # grounded-citation guard started rejecting the stitch outright for costing
    # grounding. Careful phrasing produced caution, not accuracy.
    #
    # The PMID format it asks for is fine now: resolve_pmid_mentions converts the
    # markers inside the merge, and the full-text upgrade was taught to count
    # PMID-form citations, which was the real defect behind that format.
    interpreter = Agent[LoopContext](
        name="Delegated Interpreter",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_INTERPRET,
        model_settings=ModelSettings(temperature=AI_TEMPERATURE),
        tools=[],
    )
    chunks = [chosen[i:i + DELEGATE_CHUNK]
              for i in range(0, len(chosen), DELEGATE_CHUNK)]
    sem = asyncio.Semaphore(DELEGATE_WORKERS)

    def _papers_for(chunk):
        """The papers ATTRIBUTED to this chunk's pathways, as the workflow arm
        selects them (agent.py::_one_batch).

        Handing every sub-agent the same arbitrary first-N papers is why round 4
        wrote 39 k characters and cited four of them: an interpretation of
        pathways whose literature it was never shown has nothing to cite. The
        topic_tag the Lead passes to search_literature is the attribution key,
        matched loosely because the Lead writes it in its own words.
        """
        names = {str(p.get("name", "")).lower() for p in chunk}
        ids = {str(p.get("id", "")).lower() for p in chunk}
        hits = []
        for paper in papers:
            tags = [str(t).lower() for t in (paper.get("pathways") or [])]
            if any(t in names or t in ids
                   or any(t in n or n in t for n in names if n and t)
                   for t in tags):
                hits.append(paper)
        # No attribution match (the Lead tagged by theme, not pathway): fall back
        # to the most recently retrieved, which are its latest lines of enquiry.
        #
        # Counted, because this fallback quietly restores the very failure the
        # attribution exists to prevent -- a sub-agent reasoning over papers
        # retrieved for somebody else's pathways, which is round 4's 39 k
        # characters and four citations. Measured separately: ~15 themes are
        # searched per run and only ~8 put a paper in the references, and a
        # chunk handed the wrong literature is the leading explanation left
        # standing. It was invisible: both paths return papers, so the prompt
        # looks identical from the outside.
        tally = ctx_local.delegate_attribution
        tally["papers_shown"] = tally.get("papers_shown", 0) + len(
            hits if hits else papers[-DELEGATE_PAPERS:])
        if not hits:
            tally["fallback"] = tally.get("fallback", 0) + 1
            hits = papers[-DELEGATE_PAPERS:]
        else:
            tally["matched"] = tally.get("matched", 0) + 1
        # Cap what one prompt reasons over. The workflow arm measured citations
        # COLLAPSING 15 -> 3 when a batch was handed 20+ abstracts, so more
        # literature per prompt is not better; full text first, then earliest.
        if len(hits) > DELEGATE_PAPERS:
            hits = sorted(hits, key=lambda p: (not p.get("full_text_available"),
                                               p.get("ref_index", 0)))[:DELEGATE_PAPERS]
        return hits

    async def _one(chunk):
        async with sem:
            chunk_papers = _papers_for(chunk)
            if DELEGATE_FULLTEXT:
                chunk_papers = await _upgrade_chunk_papers(c, chunk_papers)
            t_shelf = time.time()
            shelf = _quote_shelf(c, chunk, chunk_papers)
            # Traced because an untraced step cannot be told apart from a step
            # that never ran, and round 26 was scored on exactly that mistake.
            _trace(c, "quote_shelf", "%d papers" % len(chunk_papers),
                   "%d passage(s)" % len(shelf), t_shelf)
            prompt = prompts_mod.build_batch_interpretation_prompt(
                chunk, chunk_papers, c.experiment_design, c.organism_name)
            if shelf:
                prompt += prompts_mod.build_evidence_shelf_block(shelf)
            if shelf and JOIN_DATA_AND_CITATION:
                prompt += _JOIN_NOTE
            if focus:
                prompt += "\n\nFocus of this delegation: %s" % focus
            return await _single_shot(interpreter, prompt, c, 150,
                                      "delegate[%d pathways]" % len(chunk))

    reports = await asyncio.gather(*[_one(ch) for ch in chunks])
    # Keep the sub-agents' text: measured over three rounds, the Lead compresses
    # these interpretations into a summary a fifth the length of the workflow
    # arm's report, and the detail is lost with them. The gate re-synthesises
    # from what is kept here (see _merge_delegated).
    c.delegated.extend(r for r in reports if r)
    # The agent arm's answer to base's batch_citations, which has existed for
    # rounds while this arm had no equivalent. Base logs "3 batches, 0 citing, 0
    # distinct markers" -- its interpretation batches cite NOTHING and the
    # citations are born in the synthesis. Without the same count here there is
    # no way to say whether this arm's writers cite and the merge loses it, or
    # they never cite either. Two different problems, identical from outside.
    # Count BOTH notations. The delegated interpreters are told to cite as
    # "(PMID: XXXXXXXX)" -- SYSTEM_PROMPT_INTERPRET says so, and
    # resolve_pmid_mentions converts those to [N] later in the pipeline. Counting
    # only [N] therefore read 0 on every replicate, and I concluded from that,
    # repeatedly, that delegation contributes no citations at all.
    #
    # It was measuring the wrong notation. Round 44's first replicate ran with
    # the top-up DISABLED and still shipped 16 citations, every one of them
    # inside the delegated section, with none in the Lead's own prose.
    # int() on both halves: re.findall yields strings and pmid_to_ref yields
    # ints, so an unconverted union counts a paper cited BOTH ways twice -- as
    # "7" and as 7.
    c.delegate_markers = len(
        {int(m) for r in reports if r for m in re.findall(r"\[(\d+)\]", r)}
        | {int(ctx_local.pmid_to_ref[p]) for r in reports if r
           for p in re.findall(r"PMID:?\s*(\d{6,9})", r)
           if p in ctx_local.pmid_to_ref})
    out = "\n\n---\n\n".join(r for r in reports if r) or "(delegation produced nothing)"
    # The sub-agents are told to write "(PMID: 12345)", not "[N]" -- that prompt
    # was reverted to the workflow arm's on evidence, and the markers are only
    # converted later, inside the merge. Convert here instead: the Lead then
    # reads the same citation form it must check, and the grounding step below
    # can see citations at all. Without this its guard is simply never true, and
    # round 26 measured a fix that had not run.
    out = resolve_pmid_mentions(out, c.paper_index)

    # Ground the sub-agents' citations NOW, while their papers are the ones in
    # hand -- and tell the Lead which ones cannot be grounded.
    #
    # Round 25 r2 is why. The agent submitted a 7 726-character draft carrying 7
    # citations, every one checked with check_my_citations and grounded. The gate
    # then merged in 52 000 characters of this delegated text, carrying citations
    # the agent had never checked, could not find quotes for them in the time it
    # had left, and redacted all of them: a 64 830-character report shipped with
    # ZERO citations. check_my_citations was validating the draft while the gate
    # shipped draft + merge.
    #
    # Collection is a thread pool of 8, so ~20 citations cost about 5 s. The
    # quotes are cached on the context and reused by the gate, which is the part
    # that stops the collapse; the message back is what lets the Lead act.
    ungrounded = []
    if out and "[" in out:
        t_q = time.time()
        try:
            fresh = await bounded(
                asyncio.to_thread(_collect_cited_quotes,
                                  LLMClient(AI_PROVIDERS[AI_LLM_PROVIDER]), out,
                                  c.paper_index, c.job_id, c.quotes),
                DELEGATE_QUOTE_SECONDS, label="delegate quote grounding")
            c.quotes.update(fresh or {})
        except (Exception, asyncio.TimeoutError) as exc:
            logger.warning("[%s][loop] delegated-citation grounding failed: %s",
                           c.job_id, exc)
        cited_here = {int(n) for n in re.findall(r"\[(\d+)\]", out)} & set(c.paper_index)
        ungrounded = sorted(cited_here - set(c.quotes))
        _trace(c, "delegate_grounding",
               "%d cited" % len(cited_here),
               "%d grounded, %d not (%.1fs)"
               % (len(cited_here) - len(ungrounded), len(ungrounded), time.time() - t_q),
               t_q)
    if ungrounded:
        out += ("\n\n[grounding] %d citation(s) in this analysis have no verifiable "
                "quote yet: %s. The gate deletes each one along with its sentence, "
                "so read_paper on them, or drop them from what you keep."
                % (len(ungrounded), ", ".join("[%d]" % i for i in ungrounded[:12])))
    # Cached for the run, and deliberately NOT re-appended to c.delegated on a
    # hit: the gate stitches from that list, and a duplicated interpretation
    # would both pad the merge toward STITCH_MAX_CHARS and let the same claim be
    # counted twice when the stitch is compared with the draft.
    c.delegation_cache[cache_key] = out
    # What was asked for and did not happen, said out loud. Both of these used
    # to be silent: an unmatched name produced nothing at all, and a list longer
    # than the cap was truncated with no mention of the tail. An agent choosing
    # its own scope has to be able to tell "I decided against that pathway" from
    # "the tool dropped it".
    if trimmed:
        out += ("\n\n[scope] You named %d pathways and %d were delegated. The "
                "other %d would not fit before the writing reserve -- a wave of "
                "up to %d pathways costs about %d s and there was room for %d "
                "pathway(s) of delegation when you called. Not delegated: %s. "
                "Call again for them if the clock allows; if it does not, name "
                "them in the report as significant but not investigated."
                % (len(chosen) + len(trimmed), len(chosen), len(trimmed),
                   DELEGATE_CHUNK * DELEGATE_WORKERS, int(DELEGATE_WAVE_SECONDS),
                   capacity, ", ".join(trimmed[:20])))
    if unresolved:
        out += ("\n\n[scope] No significant pathway matches: %s. Nothing was "
                "delegated for these; check the name against the pathway table."
                % ", ".join(unresolved[:20]))
    out = _spend(c, out + _ledger_note(c), "delegate_interpretation")
    _trace(c, "delegate_interpretation",
           "%d pathways / %d chunks" % (len(chosen), len(chunks)),
           "%d chars" % len(out), t0)
    return out


@function_tool(failure_error_function=_tool_failure("submit_report"))
def submit_report(ctx: RunContextWrapper[LoopContext], report_markdown: str) -> str:
    """Submit your final report (markdown, [N] citations). The only way to finish. It goes to the mandatory verification gate, never straight to the user."""
    c = ctx.context
    t0 = time.time()
    c.submit_attempts += 1
    # One nudge, never a veto. Measured: two replicates of the same code differed
    # by a factor of five in prose and 15 vs 9 pathways named, and the difference
    # was that one of them never called delegate_interpretation -- so nothing was
    # stitched and the report covered a fraction of the experiment. Delegation is
    # the behaviour the whole report rests on and it is entirely optional, which
    # is fine as a choice and expensive as an oversight. So the first submit that
    # arrives thin and undelegated says so; the second goes through regardless,
    # because a tool that can refuse twice is a workflow step wearing a tool's
    # clothes.
    time_to_act = c.hard_deadline - time.time()
    if (c.submit_attempts == 1 and not c.delegated
            and len(report_markdown.strip()) < 9000
            and time_to_act > NUDGE_MIN_SECONDS):
        out = ("NOT SUBMITTED YET (this is the only time you will be asked). You "
               "have not delegated any pathway analysis, and %d characters cannot "
               "cover %d enriched pathways -- the per-pathway detail is what makes "
               "a report usable. Call delegate_interpretation over the top "
               "clusters (two calls cover everything, ~30 s each), then submit "
               "again. If you have a considered reason to submit as it stands, "
               "call submit_report again now and it will be accepted."
               % (len(report_markdown.strip()), len(c.pathways)))
        _trace(c, "submit_report", "%d chars, no delegation"
               % len(report_markdown.strip()), "nudged once", t0)
        return out
    if len(report_markdown.strip()) < 500:
        out = ("REJECTED: that is not a report (%d chars). Write the full "
               "analysis: Key Findings, Cross-Pathway Themes, Detailed Pathway "
               "Analysis, Suggested Follow-up Experiments, Limitations."
               % len(report_markdown.strip()))
        _trace(c, "submit_report", "%d chars" % len(report_markdown), "rejected", t0)
        return out
    # The same one-shot nudge, for the other thing the agent already knows is
    # wrong. check_my_citations names the citations with no supporting quote and
    # says what to do about them; when the agent re-checks it fixes them every
    # time (10 of 10 runs improved, none got worse). When it submits anyway,
    # those citations become redactions and take their sentences with them.
    #
    # Only ever one nudge per run, shared with the delegation nudge above: the
    # first submit may be answered once, the second is always accepted. A tool
    # that can refuse twice is a workflow step wearing a tool's clothes.
    still_flagged = sorted(i for i in c.flagged_citations
                           if ("[%d]" % i) in report_markdown)
    if (c.submit_attempts == 1 and still_flagged
            and time_to_act > NUDGE_MIN_SECONDS):
        out = ("NOT SUBMITTED YET (this is the only time you will be asked). "
               "Your own check_my_citations run found no supporting quote for "
               "%s, and they are still in this draft -- the gate will delete "
               "each one along with the sentence carrying it. Fix them the way "
               "that tool suggested (cite another paper, soften the claim, or "
               "read_paper and quote what you find), then submit. If you have a "
               "considered reason to submit as it stands, call submit_report "
               "again now and it will be accepted."
               % ", ".join("[%d]" % i for i in still_flagged[:10]))
        _trace(c, "submit_report", "%d chars, %d flagged citations"
               % (len(report_markdown.strip()), len(still_flagged)),
               "nudged once", t0)
        return out
    c.submitted_report = report_markdown
    _trace(c, "submit_report", "%d chars" % len(report_markdown), "accepted", t0)
    return "SUBMITTED. Reply with the single word DONE and stop."


# Eleven, not thirteen: get_gene_profile was compare_gene_profiles with one
# argument, and notebook_read re-read what the SDK already keeps in context.
# Every tool here costs its schema in EVERY Decide turn, so an unused tool is a
# tax on the prompt that carries the whole investigation.
# Ten. delegate_literature went uncalled in all 17 archived runs -- the Lead
# writes its own queries and search_literature covers the same ground -- and a
# declared tool is not free: its schema rides in every Decide turn of every run.
# compare_gene_profiles removed on the evidence: 13 calls across 72 archived
# runs, none at all in the most recent 16, and no relationship to citations
# (r = -0.08). Its schema rode in every Decide turn of every run regardless.
# get_pathway_details already carries per-gene profiles for the pathways the
# agent is looking at, which is where the same question gets answered.
GENE_LIST_LIMIT = int(os.getenv("AI_AGENT_GENE_LIST_LIMIT", "400"))
"""Genes one list_pathway_genes call returns per pathway.

Sized from the data, not guessed: on the frozen STATegra job the widest
significant pathway matches 460 genes of which 287 are differential, the median
pathway has 55, and a compact one-line-per-gene listing of the widest is ~8 kB
against a 400 kB context budget. 400 clears every real pathway measured; the cap
exists so a pathological organism cannot hand over a novel.
"""

NEIGHBOUR_CAP = int(os.getenv("AI_AGENT_NEIGHBOUR_CAP", "60"))


def _profile_line(g):
    """One gene with its per-omic time courses, rendered exactly as
    _pathway_block renders it -- so a gene reached by name and the same gene
    reached through a pathway do not read as two different measurements."""
    return "- %s%s [effect %.2f] %s" % (
        g.get("symbol"), "*" if g.get("relevant") else "",
        g.get("effect_size") or 0, _profile_summary(g.get("omic_profiles")))


@function_tool(failure_error_function=_tool_failure("get_gene_measurements"))
def get_gene_measurements(ctx: RunContextWrapper[LoopContext],
                          gene_symbols: list[str],
                          neighbour_steps: int = 0) -> str:
    """Measured values for genes you name, ANYWHERE in the upload -- not just the ten a pathway shows, and not only in enriched pathways. Instant and free. A gene you cannot quote a value for is one you must not write about. neighbour_steps=1 or 2 also returns what sits next to it in the KEGG relation graph, with the relation type. Symbols not in the upload are listed back: absent and unchanged are different facts."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    found, missing = gene_measurements(c.job_instance, gene_symbols)
    lines = []
    if found:
        # Which significant pathways hold it, so a value never arrives without
        # somewhere to put it.
        where = {}
        for p in c.pathways:
            for g in (p.get("top_genes") or []):
                where.setdefault(str(g.get("symbol", "")).lower(), set()).add(p.get("name"))
        lines.append("### Measured values (%d gene(s))" % len(found))
        for g in found:
            line = _profile_line(g)
            if g.get("duplicates"):
                line += " (+%d more row(s) share this symbol; this is the "
                line += "strongest)"
                line = line % g["duplicates"]
            lines.append(line)
            homes = sorted(where.get(str(g.get("symbol", "")).lower(), []))[:3]
            if homes:
                lines.append("  in: %s" % "; ".join(homes))
    if missing:
        # A symbol absent from the UPLOAD is a different fact from a symbol the
        # experiment measured and found flat, and neither may be written as the
        # other.
        lines.append("Not in this experiment's data (do not write about these): %s"
                     % ", ".join(missing[:30]))
    steps = max(0, min(int(neighbour_steps or 0), 2))
    if steps and found:
        lines.append(_neighbour_block(c, found, steps))
    out = _spend(c, "\n".join(lines) + _ledger_note(c), "get_gene_measurements")
    _trace(c, "get_gene_measurements", gene_symbols,
           "%d found, %d missing" % (len(found), len(missing)), t0)
    return out


def _neighbour_block(ctx, found, steps):
    """The KEGG neighbourhood of the genes just returned.

    Cached on the context: parsing 364 KGML files is about a second, which is
    nothing once and something on every call.
    """
    if ctx.adjacency is None:
        try:
            adj, read, _missing = neighbours_mod.load_relations(
                ctx.job_instance.getOrganism())
            ctx.adjacency = adj
            ctx.extra_stats["kgml_files_read"] = read
        except Exception as exc:
            logger.warning("[loop] KGML load failed: %s", exc)
            ctx.adjacency = {}
    if not ctx.adjacency:
        return ("No neighbour graph for this organism (KGML ships with a KEGG "
                "install; Reactome and OmniPath pathways have none).")
    index = build_gene_index(ctx.job_instance)
    seeds = [gid for g in found
             for gid in index.get(str(g.get("symbol", "")).lower(), [])]
    hood, trimmed = neighbours_mod.expand(ctx.adjacency, seeds, steps=steps,
                                          cap=NEIGHBOUR_CAP)
    if not hood:
        return ("No KEGG neighbours for these genes. That is not evidence of "
                "isolation: only KEGG pathways carry a relation graph, so a gene "
                "significant only in Reactome or OmniPath has none here.")
    genes = ctx.job_instance.getInputGenesData() or {}
    header_map = _build_omic_header_map(ctx.job_instance)
    lines = ["### Neighbours in the KEGG graph (%d, up to %d step(s))"
             % (len(hood), steps)]
    for gid, step, edges in hood:
        gene = genes.get(gid) or genes.get(str(gid))
        kinds = sorted({e[1] for e in edges if e[1]})[:3]
        if gene is None:
            # In the graph but not in the upload. Named anyway, so the agent can
            # see the edge exists without being tempted to report a value for a
            # gene this experiment never measured.
            lines.append("- (gene %s, not measured in this experiment) step %d via %s"
                         % (gid, step, ", ".join(kinds)))
            continue
        entry = _gene_entry(gene, header_map)
        if not entry:
            continue
        lines.append("%s  <- step %d via %s"
                     % (_profile_line(entry), step, ", ".join(kinds)))
    if trimmed:
        lines.append("(more neighbours exist than the %d shown; name a specific "
                     "gene to go further)" % NEIGHBOUR_CAP)
    return "\n".join(lines)


@function_tool(failure_error_function=_tool_failure("list_pathway_genes"))
def list_pathway_genes(ctx: RunContextWrapper[LoopContext],
                       pathway_names: list[str],
                       include_unchanged: bool = False) -> str:
    """EVERY matched gene in a pathway -- symbol, effect size, temporal pattern, no time courses. get_pathway_details shows ten in full; this shows all of them in brief, and answers "what else is in here". Differential only unless include_unchanged. Instant and free; follow with get_gene_measurements for values."""
    c = ctx.context
    t0 = time.time()
    guard = _time_guard(c)
    if guard:
        return guard
    try:
        matched = c.job_instance.getMatchedPathways() or {}
    except Exception as exc:
        return "The pathway store is unavailable here (%s)." % exc
    blocks, unresolved = [], []
    for raw in [w.strip() for w in pathway_names if w and w.strip()]:
        needle = raw.lower()
        hit = None
        for p in c.pathways:
            keys = (str(p.get("name", "")).lower(), str(p.get("id", "")).lower())
            if needle in keys or any(needle in k for k in keys):
                hit = p
                break
        pw = matched.get(str(hit.get("id"))) if hit else None
        if pw is None:
            unresolved.append(raw)
            continue
        rows, total, flat = pathway_gene_list(
            c.job_instance, pw, differential_only=not include_unchanged,
            limit=GENE_LIST_LIMIT)
        head = ("### %s (%s) -- %d of %d differential gene(s)%s"
                % (hit.get("name"), hit.get("id"), len(rows), total,
                   ", %d unchanged hidden" % flat if flat and not include_unchanged else ""))
        body = ", ".join("%s%s[%.2f %s]" % (
            g["symbol"], "*" if g["relevant"] else "", g["effect_size"],
            (g["omic_profiles"][0]["pattern"] if g["omic_profiles"] else "?"))
            for g in rows)
        blocks.append(head + "\n" + body)
        if len(rows) < total:
            blocks.append("(%d more not listed; name them in get_gene_measurements "
                          "to see their values)" % (total - len(rows)))
    if unresolved:
        blocks.append("No significant pathway matches: %s" % ", ".join(unresolved[:20]))
    out = _spend(c, "\n\n".join(blocks) or "(nothing to list)", "list_pathway_genes")
    _trace(c, "list_pathway_genes", pathway_names, "%d block(s)" % len(blocks), t0)
    return out


TOOLBELT = [get_experiment_overview, get_pathway_details,
            list_pathway_genes, get_gene_measurements,
            cluster_pathways, search_literature, read_paper, notebook_write,
            check_my_citations, delegate_interpretation, submit_report]


# ---------------------------------------------------------------------------
# Orchestration: the loop, then the mandatory exit gate.
# ---------------------------------------------------------------------------

RESULTS_PROMPT = """Rewrite the interpretation below as the RESULTS section of a
research paper. Not a summary of it -- the Results section itself.

WHAT TO CHANGE
- Continuous prose. No bullet lists anywhere. No "Key Findings" block: a Results
  section states its findings in order, it does not preface itself.
- Four to six subsections. Title each one by the FINDING it reports, as a paper
  would ("Chromatin accessibility changes precede transcriptional response"),
  never by a pathway name or accession ("Autophagy - Animal (mmu04140)").
- Each subsection tells ONE story and draws on whichever pathways serve it.
  Do not walk through pathways one at a time; that is the structure being
  replaced.
- Describe the pathway clusters in prose -- what groups with what, and what the
  grouping means biologically. Do not reproduce the cluster table.
- No tables of any kind.
- Report numbers the way a paper does: inside the sentence, with the measure
  named ("accessibility rose at 83 of 130 autophagy loci, p = 0.0029"), not as
  a parenthetical dump.
- There is NO length target. Do not summarise, condense, or decide something
  is minor. This rewrite changes the SHAPE of the text, not how much of it
  there is: the same findings, reorganised into prose that reads like a paper.
  A Results section that is shorter because it dropped a finding is a failure,
  not a success. Measured: a first version of this stage cut 8 184 words to
  2 957 and lost 22%% of the findings with them.

WHAT MUST NOT CHANGE
- **These exact markers must all appear in your prose: %(markers)s**
  Check them off before you answer; a rewrite missing any one is discarded and
  the original is shipped instead, so the work is wasted.
- **Every [N] citation marker must survive, on the sentence it supports.** The
  citations were verified against the papers they point at; a marker moved to a
  different claim is a false citation, and a marker dropped loses a verified
  fact. Carry each one to whichever sentence now carries its claim. Do not
  renumber, do not add new markers, do not cite anything not already cited.
- Every numeric value, gene symbol, p-value and direction of change.
- The "### References" section at the end, exactly as it stands.

EVERY ONE OF THESE PATHWAYS MUST STILL BE DISCUSSED BY NAME:
%(pathways)s
Each is a finding. Reorganise them under whatever headings tell the story best
-- several pathways may share one subsection -- but a pathway that vanishes is a
result thrown away, and the rewrite is discarded if any is missing.

THE CLUSTERS (describe these in prose; do not tabulate):
%(clusters)s

THE INTERPRETATION TO REWRITE:
%(report)s
"""


RESULTS_CHUNK_PROMPT = """Rewrite the pathway analyses below as ONE section of a
research paper's Results.

- Continuous prose. No bullet lists, no tables, no sub-headings inside it.
- One `## ` heading, titled by the FINDING these pathways share, as a paper
  would ("Chromatin accessibility changes precede transcriptional response"),
  never by a pathway name or accession.
- Every pathway below must still be discussed by name. They are findings, not
  examples; you may not drop one for being minor.
- Keep every [N] citation marker on the sentence it supports, every numeric
  value, every gene symbol and every direction of change. Do not renumber and
  do not add markers.
- Report numbers inside the sentence with the measure named ("accessibility
  rose at 83 of 130 autophagy loci, p = 0.0029").
- No preamble, no closing summary, and NO "Limitations" or "Caveats" section --
  the report's own caveats are appended once after all sections, and one written
  here becomes a duplicate.
- No bullet lists. Any list in the source becomes prose.
- Return only the section.

HEADINGS ALREADY USED BY EARLIER SECTIONS -- yours must be clearly different,
and must not restate the same claim in other words:
%(taken)s

THE PATHWAYS THIS SECTION MUST COVER:
%(names)s

THE ANALYSES TO REWRITE:
%(chunk)s
"""


def _split_pathway_sections(report):
    """The dossier's per-pathway sections, in order.

    A section runs from a `## ` heading naming a pathway to the next `## `.
    Returns (preamble, [(heading, body), ...], tail) so the framing above the
    first pathway and the References below the last are preserved verbatim
    rather than being rewritten -- neither is a finding, and both are exactly
    what a rewrite tends to drop.
    """
    ref = report.split("### References")
    body, tail = ref[0], ("### References" + ref[1] if len(ref) > 1 else "")
    # Split on H1 as well as H2. Measured on a live job: that report opened
    # with "# Key Findings" as an H1, so an H2-only split swept it into the
    # preamble and carried the bullet block through verbatim -- the one thing
    # the rewrite exists to remove.
    parts = re.split(r"(?m)^(#{1,2}\s+[^\n]+)$", body)
    preamble = parts[0]
    secs = [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return preamble, secs, tail


def _without_table_rows(text):
    """The section with its markdown table rows removed.

    Used to decide whether a section carries evidence. A table is data the job
    already holds rendered as rows; it asserts nothing the prose does not, and
    treating its cells as findings readmits the exact furniture the rewrite
    exists to strip.
    """
    return re.sub(r"(?m)^\s*\|.*$", "", str(text))


# A pathway written as a bold lead-in rather than a heading:
# `**Cadherin signaling (p=5.03e-06, 20/26)** -- converges on ...`
_LEAD_IN = re.compile(r"(?m)^\*\*([^*\n]{4,160}?)\*\*\s*(?=[\u2014\u2013:-])")


# A `###` subsection: the other way a report nests several findings under one
# `##`. Three is the threshold, not two -- a single pathway's own write-up can
# reasonably carry a couple of subsections, but not a list of them.
_SUBHEAD = re.compile(r"(?m)^#{3,4}\s+([^\n]+)$")
CONTAINER_MIN_SUBHEADS = 3


def _explode_container(head, body, pw_names=()):
    """Split a section that holds several findings under one heading.

    A container is not one finding, and handing the chunker one is handing it
    the whole report again -- the single-call rewrite that chunking exists to
    replace. Both nesting styles seen in production are handled:

    * **`###` subsections.** Job `Yj5oty03Uf`, run through the UI: 12 pathways
      under `## Pathway-Specific Findings`, 7 themes under `## Cross-Pathway
      Themes`, 6 under `## Most Biologically Significant Findings`. The splitter
      only descends to `##`, so each of those arrived as ONE section; the stage
      accepted its own output and the user got the dossier back nearly verbatim,
      because a chunk that IS a report has nothing to reorganise it into.
    * **Bold lead-ins.** Two other production reports had their entire body
      under `## Detailed Pathway Analysis` with each pathway a
      `**Name (p=...)** -- prose` paragraph. That one chunk dropped 28 of 35
      citations.

    Children are promoted to `##` so the rewritten section keeps the one-heading
    convention the chunk prompt asks for. Returns the section unchanged when it
    is not a container: relabelling a single finding buys nothing.

    **A section whose own heading names a pathway is never a container.** One
    report writes `## 2. ATP-Dependent Chromatin Remodeling (mmu03082)` with
    three `###` subsections inside it; splitting that fragments one finding into
    three chunks that each own nothing, and it took a live report from 18
    sections to 40 while dropping six pathway names. A container's heading is
    generic precisely because it holds things that are not it.
    """
    if any(n and n.lower() in head.lower() for n in pw_names):
        return [(head, body)]
    subs = list(_SUBHEAD.finditer(body))
    if len(subs) >= CONTAINER_MIN_SUBHEADS:
        return _split_on(head, body, subs, lambda m: m.group(1))
    hits = list(_LEAD_IN.finditer(body))
    if len(hits) >= 2:
        return _split_on(head, body, hits, lambda m: m.group(1))
    return [(head, body)]


def _split_on(head, body, hits, title_of):
    """One section per match, with the framing above the first carried along."""
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        out.append(("## " + title_of(m).strip(),
                    "\n" + body[m.start():end].strip()))
    # The container's own heading and whatever preceded the first match ride
    # with the first child rather than being dropped -- either can carry a
    # citation, and the heading can carry a pathway name.
    lead = (head + "\n" + body[:hits[0].start()]).strip()
    if lead:
        out[0] = (out[0][0], "\n" + lead + "\n\n" + out[0][1].strip())
    return out


# Headings that USUALLY introduce framing rather than a pathway finding.
# A name is a suspicion, not a verdict -- see _loses_nothing.
RESULTS_FURNITURE = ("key findings", "cross-pathway", "detailed pathway analysis",
                     "pathway clusters", "enriched pathway summary", "summary of")

# How many subsections a Results section should aim for.
RESULTS_TARGET_SECTIONS = int(os.getenv("AI_AGENT_RESULTS_SECTIONS", "5"))


def _loses_nothing(head, body, surv_marks, surv_low, pw_names):
    """Is this section safe to drop -- does everything in it survive elsewhere?

    Markers count wherever they are: a citation is evidence even in a table
    cell. Pathway NAMES count only in PROSE, because the enriched-pathway
    summary and the cluster table are furniture by construction -- rows of data
    the job already holds, appended deterministically below the report, naming
    dozens of pathways the prose never discusses. Judge their cells as findings
    and every one of those names reads as an orphan, the whole table is
    readmitted as evidence, and the rewrite ships with exactly the 46-row table
    it was asked to remove (measured on job 4j00f2377Y). A table row restates;
    it does not find.
    """
    prose = _without_table_rows(body)
    marks = set(re.findall(r"\[(\d+)\]", body))
    if marks - surv_marks:
        return False
    return not any(n and n.lower() in (head + prose).lower()
                   and n.lower() not in surv_low for n in pw_names)


def _strip_reintroduced_furniture(report, pw_names, stats):
    """Remove dossier furniture that a post-gate rewrite put back.

    The Results section is written BEFORE the exit gate, on purpose -- its prose
    then gets top-up, the Claim Verifier and the programmatic net like any other
    draft. But two of those steps hand the WHOLE report back to the synthesizer
    (the top-up candidate and the correction rewrite), and the synthesizer's
    standing instructions say to include a "Key Findings" summary at the top.
    So a run whose Results section was written, accepted and measured
    (`results_section: true`) can still be stored with a bulleted Key Findings
    block above it -- which is exactly what job `m4gs607z4Z` came back with when
    a citation correction fired.

    Rather than fight the system prompt, the same test that decided furniture in
    the first place is applied once more at the end: a section goes only if
    every citation marker in it survives elsewhere and it names no pathway that
    would otherwise go unmentioned. Idempotent, and it cannot delete evidence.
    """
    preamble, secs, tail = _split_pathway_sections(report)
    suspect = [i for i, (h, _) in enumerate(secs)
               if any(k in h.lower() for k in RESULTS_FURNITURE)]
    if not suspect:
        return report
    surviving = preamble + "\n".join(h + b for i, (h, b) in enumerate(secs)
                                     if i not in set(suspect))
    surv_marks = set(re.findall(r"\[(\d+)\]", surviving))
    surv_low = _without_table_rows(surviving).lower()
    drop = {i for i in suspect
            if _loses_nothing(secs[i][0], secs[i][1], surv_marks, surv_low,
                              pw_names)}
    if not drop:
        return report
    stats["results_furniture_restripped"] = len(drop)
    kept = "".join(h + b for i, (h, b) in enumerate(secs) if i not in drop)
    return (preamble.rstrip() + "\n\n" + kept.strip() + "\n\n" + tail).strip()


def _partition_sections(report, pw_names, stats):
    """Sort the dossier into (preamble, findings, closing, tail).

    Pure and side-effect free apart from the stats counter, so the
    classification can be replayed over real reports without a gateway --
    which is how the four production rejections were reproduced.
    """
    preamble, secs, tail = _split_pathway_sections(report)
    # Caveats close a Results section; they are not a finding to reorganise.
    # Grouped with the pathways they landed third of six, which reads as the
    # report giving up halfway through. Follow-up experiments belong with them:
    # a proposal is not a result, and rewritten as one it acquires a finding
    # heading for work nobody did.
    LAST = ("limitation", "caveat", "follow-up experiment", "follow up experiment")
    kinds = []
    for h, b in secs:
        low = h.lower()
        kinds.append(("last" if any(k in low for k in LAST)
                      else "skip" if any(k in low for k in RESULTS_FURNITURE)
                      else "path", h, b))

    # A SKIP-named section is dropped only if dropping it LOSES NOTHING.
    #
    # The name list was a guess about shape, and on live reports the guess was
    # wrong in the most expensive way: four consecutive production runs had the
    # whole rewrite rejected, and every rejection traces here. In the flat
    # report shape the entire body is one `## Detailed Pathway Analysis` whose
    # pathways are bold lead-ins, so the name list threw away all 15 (and all
    # 35) citations at once; in the sectioned shape `## Key Findings` carried
    # one or two markers that appeared nowhere else, which cost `lost_1` and
    # `lost_2`. Both are the same mistake -- deciding a section's role from its
    # title instead of its content. So the test is now what it should always
    # have been: a section is furniture when every citation marker in it is
    # also somewhere that survives and it names no pathway that would otherwise
    # go unmentioned. Anything else is evidence and is rewritten like the rest.
    surviving = preamble + "\n".join(h + b for k, h, b in kinds if k != "skip")
    surv_marks = set(re.findall(r"\[(\d+)\]", surviving))
    surv_low = _without_table_rows(surviving).lower()
    pathway_secs, closing = [], []
    for kind, h, b in kinds:
        if kind == "last":
            closing.append(h + b)
        elif kind == "path":
            # Exploded too, not only the furniture. `## Pathway-Specific
            # Findings` is on no name list and held all 12 pathways of job
            # Yj5oty03Uf under `###`.
            pathway_secs.extend(_explode_container(h, b, pw_names))
        elif _loses_nothing(h, b, surv_marks, surv_low, pw_names):
            continue                           # genuinely furniture
        else:
            stats["results_furniture_kept"] = (
                stats.get("results_furniture_kept", 0) + 1)
            pathway_secs.extend(_explode_container(h, b, pw_names))
    return preamble, pathway_secs, closing, tail


async def _results_by_chunk(agent, ctx, report, pw_names, job_id, stats, timeout):
    """Rewrite the dossier section-group by section-group.

    One call asked to reorganise the whole dossier by finding cannot conserve
    it. Measured twice: with a word target it kept every citation and lost 22%%
    of the rubric's coverage (round 66); with NO length target at all it dropped
    8 of 16 pathways. The length instruction was never the cause. Reorganising
    by finding means SELECTING, and selection discards -- so the fix cannot be a
    better prompt, it has to be a smaller question.

    Each call here sees ~4 pathway sections and is told which pathways it owns.
    No call can drop a pathway another call was responsible for, because no call
    ever holds them all. The preamble and the reference list are carried across
    verbatim: neither is a finding, and both are what a rewrite loses first.
    """
    preamble, pathway_secs, closing, tail = _partition_sections(
        report, pw_names, stats)
    if not pathway_secs:
        return None
    # Chunk size is derived from the report, not fixed. A flat 4 gives a
    # 4-section dossier exactly ONE subsection -- measured on a live job, whose
    # Results section came back as a single wall under one heading. A Results
    # section wants several subsections each telling a different story, so aim
    # for about RESULTS_TARGET_SECTIONS of them and never fewer than two
    # sections per chunk (a chunk of one cannot find a theme, only restate the
    # pathway).
    # Every marker the report carries, so a chunk reusing a marker another
    # chunk introduced is not mistaken for inventing one.
    all_marks = set(re.findall(r"\[(\d+)\]", report.split("### References")[0]))
    size = max(2, -(-len(pathway_secs) // RESULTS_TARGET_SECTIONS))
    groups = [pathway_secs[i:i + size]
              for i in range(0, len(pathway_secs), size)]
    async def one(group, taken):
        chunk = "\n\n".join(h + b for h, b in group)
        owned = [n for n in pw_names
                 if any(n.lower() in (h + b).lower() for h, b in group)]
        prompt = RESULTS_CHUNK_PROMPT % {
            "names": "\n".join("  - %s" % n for n in owned) or "  (see below)",
            "taken": ("\n".join("  - %s" % t for t in taken)
                      if taken else "  (none yet -- this is the first section)"),
            "chunk": chunk}
        # Checked and retried per CHUNK, and on failure the chunk keeps its
        # ORIGINAL text rather than costing the whole rewrite.
        #
        # This is the second half of the production failure. Citations were
        # only ever counted once, over the finished report, and any loss threw
        # everything away -- so two live runs that had reorganised the entire
        # dossier correctly were discarded for `lost_1` and `lost_2`. A chunk
        # owns a known handful of markers and pathways, so the loss is
        # detectable exactly where it happens and cheap to name in a retry;
        # and if the retry still will not conserve them, one section reading
        # like the old dossier is a far smaller loss than no rewrite at all.
        # Only invented markers are judged against the whole report: a chunk
        # may legitimately reuse a marker another chunk introduced, and
        # reverting for that would punish good prose.
        own_marks = set(re.findall(r"\[(\d+)\]", chunk))
        text = None
        for attempt in (1, 2, 3):
            try:
                r = await bounded(Runner.run(agent, prompt, context=ctx,
                                             max_turns=2), timeout,
                                  label="results chunk")
                text = str(r.final_output or "").strip()
            except (Exception, asyncio.TimeoutError) as e:
                logger.warning("[%s][loop] results chunk failed: %s", job_id, e)
                stats["results_chunk_reverted"] = (
                    stats.get("results_chunk_reverted", 0) + 1)
                return chunk, owned          # keep the ORIGINAL sections
            got = set(re.findall(r"\[(\d+)\]", text))
            gone = [n for n in owned if n.lower() not in text.lower()]
            lost = own_marks - got
            made_up = got - all_marks
            if not gone and not lost and not made_up:
                break
            if attempt == 3:
                stats["results_chunk_reverted"] = (
                    stats.get("results_chunk_reverted", 0) + 1)
                stats["results_chunk_revert_why"] = ",".join(
                    filter(None, ["names_%d" % len(gone) if gone else "",
                                  "cites_%d" % len(lost) if lost else "",
                                  "invented_%d" % len(made_up) if made_up else ""]))
                logger.info("[%s][loop] results chunk kept verbatim (%s)",
                            job_id, stats["results_chunk_revert_why"])
                return chunk, owned          # keep the ORIGINAL sections
            asks = []
            if gone:
                asks.append(
                    "It stopped discussing these pathways, each of which is a "
                    "finding and not an optional detail:\n%s"
                    % "\n".join("  - %s" % n for n in gone))
            if lost:
                asks.append(
                    "It dropped these citation markers: %s\nEach marks a claim "
                    "that was checked against the paper it points at, so a "
                    "dropped marker loses a verified fact. Put each one back on "
                    "the sentence carrying its claim."
                    % " ".join("[%s]" % m for m in sorted(lost, key=int)))
            if made_up:
                asks.append(
                    "It used these markers, which do not exist in this report: "
                    "%s\nRemove them; do not renumber the rest."
                    % " ".join("[%s]" % m for m in sorted(made_up, key=int)))
            prompt = ("Your section did not conserve its source.\n\n%s\n\n"
                      "Return the SAME section with that fixed, keeping "
                      "everything else exactly as you wrote it.\n\n%s"
                      % ("\n\n".join(asks), text))
        return text, owned

    # Sequential, not gathered. Run in parallel the chunks cannot see each
    # other's headings and converge: five of them opened with a variant of
    # "Chromatin accessibility ... precedes transcription", which reads as one
    # argument made five times. Each call is a few seconds, so ordering them
    # costs little and is what makes the section titles distinct.
    out = []
    taken = []
    for g in groups:
        text, owned = await one(g, taken)
        out.append((text, owned))
        for h in re.findall(r"(?m)^##\s+(.+)$", text):
            taken.append(h.strip())
    stats["results_chunks"] = len(groups)
    body = "\n\n".join(t for t, _ in out if t)
    if closing:
        # Deduplicated on the HEADING, keeping the longest body. A stitched
        # dossier carries the caveats block once per stitched part, and those
        # copies differ in wording -- so a body-text key keeps them all, which
        # is how one live report ended with three "Limitations and Caveats".
        by_head = {}
        for c in closing:
            m = re.match(r"\s*(#+\s*[^\n]+)", c)
            key = " ".join((m.group(1) if m else c[:60]).lower().split())
            if key not in by_head or len(c) > len(by_head[key]):
                by_head[key] = c.strip()
        uniq = list(by_head.values())
        body = body.rstrip() + "\n\n" + "\n\n".join(uniq)
    return (preamble.rstrip() + "\n\n" + body + "\n\n" + tail).strip()


async def _write_results_section(agent, ctx, report, cluster_text, pathways,
                                 job_id, stats, timeout):
    """Rewrite the dossier as a Results section, or keep the dossier.

    Runs BEFORE the exit gate on purpose. Everything it writes then goes
    through top-up, the Claim Verifier and the programmatic net, so its prose is
    grounded by the same machinery as any other draft. Placing it after the gate
    would let a rewrite move a verified marker onto a claim nobody checked --
    which is precisely the overshoot the gate exists to remove.

    The citation guard is not advisory. Measured across this document, a stage
    that silently costs citations is the failure mode that killed sentence
    repair (-33%) and the top-up deadline (-21%), so this one is REJECTED
    outright if a single marker goes missing, exactly as the merge and top-up
    candidates are.
    """
    t0 = time.time()

    def _named_pathways(text, names):
        """Which of the report's pathways are still discussed by name.

        This is the guard round 66 lacked. That version kept every citation and
        still lost 22% of the rubric's coverage, because the rubric scores
        findings -- it matches `truth_pathways`, a list of pathway NAMES from
        the published Results -- and citations say nothing about which findings
        survived. A rewrite can carry all 22 markers and quietly drop half the
        biology.

        Matched case-insensitively on the name, not on the accession: a Results
        section reorganised by finding has no reason to keep "(mmu04060)" in a
        heading, and requiring it would reject good prose. Losing the NAME is
        what loses the finding.
        """
        low = str(text).lower()
        return {n for n in names if n and n.lower() in low}

    def _body_markers(text):
        """Markers in the PROSE, not in the reference list.

        The reference list repeats every marker as its own line ("[2] Smith et
        al..."), so counting the whole document hides the exact failure this
        guard exists to catch: prose that drops a citation while the entry it
        pointed at stays in the list. That is marker-stripping, and this arm
        already does it -- redaction removes the marker and leaves the sentence,
        which is why "failed citations" reads 0.0 while grounding is imperfect.
        """
        return set(re.findall(r"\[(\d+)\]", str(text).split("### References")[0]))

    before = _body_markers(report)
    pw_names = [p.get("name") for p in (pathways or []) if p.get("name")]
    # Prose only, on both sides. The dossier's enriched-pathway table lists
    # EVERY enriched pathway -- 46 rows on one live job -- while the prose
    # discusses a handful. Counting table cells as findings makes the guard
    # demand that a Results section name all of them, which no Results section
    # does, and the rewrite is rejected for dropping a table it was asked to
    # drop.
    pw_before = _named_pathways(_without_table_rows(report), pw_names)
    prompt = RESULTS_PROMPT % {
        "markers": " ".join("[%s]" % m for m in sorted(before, key=int)) or "(none)",
        "pathways": "\n".join("  - %s" % n for n in sorted(pw_before)) or "  (none)",
        "clusters": cluster_text or "(no clustering available)",
        "report": report}

    # One retry, naming what went missing.
    #
    # Measured on the first live attempt: the prompt alone lost 5 of 22
    # citations while compressing 8 184 words to a Results section. Asking a
    # model to reorganise prose and carry two dozen markers is two tasks, and
    # the second is the one that silently fails -- so it gets told exactly which
    # ones it dropped rather than being asked again to be careful.
    # Chunked first: one call cannot conserve the whole dossier (see
    # _results_by_chunk). The single-call path stays only as the fallback for a
    # report with no per-pathway sections to split.
    chunked = await _results_by_chunk(agent, ctx, report, pw_names, job_id,
                                      stats, timeout)
    if chunked:
        cand_after = _body_markers(chunked)
        cand_pw = _named_pathways(_without_table_rows(chunked), pw_names)
        reasons = []
        if before - cand_after:
            reasons.append("lost_%d" % len(before - cand_after))
        if cand_after - before:
            reasons.append("invented_%d" % len(cand_after - before))
        if pw_before - cand_pw:
            reasons.append("droppedpathways_%d" % len(pw_before - cand_pw))
        if "### References" in report and "### References" not in chunked:
            reasons.append("no_references")
        if reasons:
            stats["results_rejected"] = ",".join(reasons)
            stats["results_s"] = time.time() - t0
            logger.info("[%s][loop] results section rejected (%s); keeping the "
                        "dossier", job_id, ",".join(reasons))
            return report, False
        stats["results_words_before"] = len(report.split())
        stats["results_words_after"] = len(chunked.split())
        stats["results_citations_kept"] = len(cand_after)
        stats["results_pathways_kept"] = len(pw_before & cand_pw)
        stats["results_pathways_before"] = len(pw_before)
        stats["results_s"] = time.time() - t0
        return chunked, True

    candidate = ""
    after = set()
    for attempt in (1, 2, 3):
        try:
            r = await bounded(Runner.run(agent, prompt, context=ctx, max_turns=2),
                              timeout, label="results section")
            candidate = str(r.final_output or "").strip()
        except (Exception, asyncio.TimeoutError) as e:
            stats["results_failed"] = "%s: %s" % (type(e).__name__, e)
            return report, False
        after = _body_markers(candidate)
        pw_after = _named_pathways(candidate, pw_names)
        missing = before - after
        pw_missing = pw_before - pw_after
        if (not missing and not pw_missing) or attempt == 3:
            stats["results_attempts"] = attempt
            break
        stats["results_retried"] = ",".join(sorted(missing, key=int)) or "pathways"
        if pw_missing:
            stats["results_retried_pathways"] = len(pw_missing)
        if pw_missing and not missing:
            prompt = (
                "Your Results section stopped discussing these pathways:\n%s\n\n"
                "Each is a finding from the analysis, not an optional detail. "
                "Put them back -- under whatever heading fits the story, and "
                "several may share one subsection -- keeping everything else "
                "exactly as you wrote it. End with the \"### References\" "
                "section exactly as it appears below, every entry unchanged; a "
                "reply that omits it is discarded.\n\n%s"
                % ("\n".join("  - %s" % n for n in sorted(pw_missing)), candidate))
            continue
        prompt = (
            "Your Results section dropped these citations: %s\n\n"
            "Each one marks a claim that was verified against the paper it "
            "points at, so a dropped marker loses a verified fact and the "
            "rewrite is discarded. Return the SAME Results section with every "
            "one of them restored on the sentence carrying its claim. Change "
            "nothing else. "
            "End with the \"### References\" section exactly as it appears in the "
            "text below -- every entry, unchanged. A reply that omits it is "
            "discarded.\n\n%s"
            % (" ".join("[%s]" % m for m in sorted(missing, key=int)), candidate))
    lost = before - after
    gained = after - before
    pw_lost = pw_before - _named_pathways(candidate, pw_names)
    reasons = []
    if lost:
        reasons.append("lost_%d" % len(lost))
    if pw_lost:
        # The round-66 failure, now refusable. Coverage fell 0.571 -> 0.446
        # there while every citation survived, because nothing checked that the
        # FINDINGS did.
        reasons.append("droppedpathways_%d" % len(pw_lost))
    if gained:
        # A marker that was not there before points at a paper this text was
        # never verified against.
        reasons.append("invented_%d" % len(gained))
    if "### References" in report and "### References" not in candidate:
        reasons.append("no_references")
    if len(candidate) < 0.2 * len(report):
        reasons.append("truncated")
    if reasons:
        stats["results_rejected"] = ",".join(reasons)
        stats["results_s"] = time.time() - t0
        logger.info("[%s][loop] results section rejected (%s); keeping the "
                    "dossier", job_id, ",".join(reasons))
        return report, False

    stats["results_words_before"] = len(report.split())
    stats["results_words_after"] = len(candidate.split())
    stats["results_citations_kept"] = len(after)
    stats["results_pathways_kept"] = len(pw_before & _named_pathways(candidate, pw_names))
    stats["results_pathways_before"] = len(pw_before)
    stats["results_s"] = time.time() - t0
    return candidate, True


def _significant_universe(job_instance, budgets, stats):
    """Every SIGNIFICANT pathway, in rank order -- the agent's whole territory.

    What this replaces: ``build_pathway_context(max_pathways=AI_MAX_PATHWAYS)``,
    the top fifteen by combined p. The universe then widened to the significant
    set only as a SIDE EFFECT of the cluster_pathways tool, which rebuilds the
    context over the partition's members. So the agent's reach depended on
    whether it happened to call a clustering tool, and a run that skipped it
    never learned the other ninety pathways existed.

    That is not a scope the agent chose. It could not have chosen otherwise: no
    tool named the pathways outside the fifteen, so "should I look at this one?"
    was a question it was never in a position to ask. Deciding what to
    investigate is the whole job of this arm, and the input to that decision was
    being made for it, upstream, by a p-value sort.

    Significance is the membership rule, and it is the user's own: the same
    filter the Step 3 network view draws with -- combined Fisher p <= 0.05 over
    conditions, the organism-wide map dropped, capped at AI_CLUSTER_MAX_NODES
    (150) so a pathological job cannot hand the Lead a thousand rows. Within
    that, nothing is chosen for it.

    Falls back to the old top-N when the significant set comes back empty, which
    happens when a job has no pathway under 0.05 at all. An agent with fifteen
    weak pathways can still write about weak evidence; an agent with none cannot
    write anything.
    """
    fallback = int((budgets or {}).get("max_pathways") or AI_MAX_PATHWAYS)
    ids = []
    try:
        ids = [pid for pid, _pw in clusters_mod.select_network_nodes(job_instance)]
    except Exception as exc:
        logger.warning("[loop] significant-pathway selection failed (%s); "
                       "falling back to the top %d by p-value", exc, fallback)
    if not ids:
        ctx_pathways = build_pathway_context(job_instance, max_pathways=fallback)
        stats["universe_source"] = "top_p_fallback"
        stats["universe_pathways"] = len(ctx_pathways)
        return ctx_pathways
    ctx_pathways = build_pathway_context(job_instance, pathway_ids=ids)
    stats["universe_source"] = "significant"
    stats["universe_pathways"] = len(ctx_pathways)
    return ctx_pathways


async def _run_loop_async(job_instance, job_id, experiment_design, budgets,
                          stats, hooks=None):
    hooks = hooks or {}
    configure_sdk()
    agents = _build_agents()

    organism = job_instance.getOrganism()
    organism_name = get_organism_name(organism)
    pathways = _significant_universe(job_instance, budgets, stats)
    gene_whitelist = build_gene_symbol_whitelist(job_instance)
    if hooks.get("pathways"):
        try:
            hooks["pathways"](pathways)
        except Exception:
            logger.debug("pathway-index hook failed", exc_info=True)

    ctx = LoopContext(job_instance=job_instance, job_id=job_id,
                      organism_name=organism_name,
                      experiment_design=experiment_design or "",
                      agents=agents,
                      pathways=pathways, gene_whitelist=gene_whitelist,
                      pubmed=PubMedClient(), hooks=hooks,
                      started_at=time.time(),
                      hard_deadline=time.time() + AGENT_RUN_SECONDS
                                    - GATE_RESERVE_SECONDS)
    # The transport retries on its own budget, and without this it never learns
    # the run's. This arm IMPORTED set_run_deadline and never called it -- so
    # _run_seconds_left() returned None here, the guard that refuses a retry it
    # cannot finish in time never fired, and the shim could go on retrying past
    # the deadline the rest of the arm respects. Every other bound (the loop's
    # _time_guard, AGENT_RUN_SECONDS, bounded() per call) sits ABOVE the
    # transport, which is why runs still finished and the hole stayed invisible.
    set_run_deadline(ctx.hard_deadline)
    reset_run_retries()

    lead = Agent[LoopContext](
        name="Lead Interpreter",
        model=_model(),
        instructions=_lead_instructions(),
        model_settings=ModelSettings(temperature=AI_TEMPERATURE),
        # No output_type, ever: with tools it silences every tool call (the
        # rubber-stamp verifier, agent.py _build_agents). submit_report IS the
        # structured exit.
        tools=TOOLBELT,
    )

    # Stamp the configuration into the run's own trace. Every analysis so far has
    # depended on me remembering which round carried which knobs -- and one of
    # them (the delegated-interpreter prompt) took three rounds to convict
    # because its effect was mixed with two other changes. A run that cannot say
    # what it was is a measurement waiting to be misattributed.
    # Derived flags first, hand-named keys second: the explicit ones below have
    # been in the stamp for every round measured so far and keep their exact
    # spelling, so old traces stay parseable by anything that reads them.
    _config_stamp = dict(_flag_snapshot())
    _config_stamp.update({
        # First, so it survives any truncation downstream: two runs share a
        # fingerprint exactly when they are the same agent.
        "code": _code_fingerprint(),
        "merge_mode": MERGE_MODE,
        "verify_prefetch": VERIFY_PREFETCH,
        "stitch_max": STITCH_MAX_CHARS,
        "search_budget": SEARCH_BUDGET,
        "search_hits": SEARCH_HITS,
        "delegate_papers": DELEGATE_PAPERS,
        "delegate_chunk": DELEGATE_CHUNK,
        # In plain text as well as in the hash: the fingerprint proves two runs
        # differ, this says how.
        "sentence_repair": SENTENCE_REPAIR,
        "topup_enabled": TOPUP_ENABLED,
        "framing_may_cite": FRAMING_MAY_CITE,
        "screen_papers": SCREEN_PAPERS,
        "show_uncited": SHOW_UNCITED,
        "verify_topup": VERIFY_TOPUP,
        "max_turns": AGENT_MAX_TURNS,
        "gate_reserve": GATE_RESERVE_SECONDS,
        "lead_prompt_chars": len(_lead_instructions()),
        "tools": len(TOOLBELT),
        # Synthetic runs must be separable from real ones. The end-to-end test
        # drives a scripted agent against a stand-in gateway with a 2-search
        # budget, and two of those had already landed in the tool-adoption
        # tables as if a model had chosen those calls.
        "label": os.getenv("AI_AGENT_RUN_LABEL", "live"),
    })
    _trace_gate(ctx, "__config__", "run start", json.dumps(_config_stamp), time.time())

    _hb(ctx, "extracting", 10, "Agent reading the enrichment results...")
    kickoff = prompts_mod.build_lead_kickoff_prompt(
        organism_name, experiment_design, pathways, AGENT_MAX_TURNS,
        SEARCH_BUDGET, int(AGENT_RUN_SECONDS - GATE_RESERVE_SECONDS))

    loop_budget = max(60.0, ctx.hard_deadline - time.time())
    t0 = time.time()
    try:
        result = await bounded(
            Runner.run(lead, kickoff, context=ctx, max_turns=AGENT_MAX_TURNS),
            loop_budget, label="lead loop")
        stats["loop_final"] = str(result.final_output)[:200]
    except asyncio.TimeoutError:
        stats["loop_backstop"] = "wall-clock"
        logger.warning("[%s][loop] the loop hit its wall-clock backstop", job_id)
    except Exception as e:
        # MaxTurnsExceeded lands here too: the turn cap is a backstop, and a
        # submitted report survives it.
        stats["loop_backstop"] = "%s: %s" % (type(e).__name__, e)
        logger.warning("[%s][loop] loop ended by backstop: %s", job_id, e)
    stats["loop_s"] = time.time() - t0
    stats["agent_tool_calls"] = len(ctx.trace)
    stats["agent_searches"] = ctx.searches_used
    stats["agent_notebook"] = len(ctx.notebook)
    # Whether the agent actually fills the `subject` argument, which is the
    # falsifier I pre-registered for it and then made unmeasurable: "a blank
    # rate above ~30% is the verdict by another route -- the model declining the
    # field is evidence the field is wrong". The field has been required on
    # notebook_write for several rounds and nothing recorded how often it
    # arrived empty, so the test could never run.
    if ctx.note_subjects:
        blank = sum(1 for t in ctx.note_subjects if not str(t).strip())
        stats["note_subjects_blank"] = blank
        stats["note_subjects_total"] = len(ctx.note_subjects)
    if ctx.delegate_attribution:
        stats["delegate_matched"] = ctx.delegate_attribution.get("matched", 0)
        stats["delegate_fallback"] = ctx.delegate_attribution.get("fallback", 0)
        stats["delegate_papers_shown"] = ctx.delegate_attribution.get("papers_shown", 0)
    for key, value in (ctx.extra_stats or {}).items():
        stats.setdefault(key, value)
    stats["delegate_markers"] = ctx.delegate_markers
    stats["abstract_rereads"] = ctx.abstract_rereads
    if SCREEN_PAPERS:
        stats["papers_screened_out"] = ctx.papers_screened_out
    # The context bill, itemised. TOOL_CHAR_BUDGET was enforced from the first
    # run and archived by none of them: the agent was shown its own spend on
    # every turn while the record kept no total, so no completed run could say
    # which tool ate the context the investigation had to reason through.
    stats["tool_chars"] = ctx.tool_chars
    stats["tool_chars_by_tool"] = dict(ctx.tool_chars_by_tool)
    stats["tool_calls_by_tool"] = dict(ctx.tool_calls_by_tool)
    if ctx.genes_shown:
        stats["genes_shown"] = ctx.genes_shown
        stats["genes_flat"] = ctx.genes_flat
    if ctx.trace_path:
        stats["trace_file"] = ctx.trace_path

    report = ctx.submitted_report
    if not report.strip():
        # The loop ended without walking through its one door. The backstop
        # answer is a single bounded synthesis from whatever the run learned:
        # loud in stats, and strictly better than shipping nothing.
        stats["forced_synthesis"] = True
        _hb(ctx, "synthesizing", 78, "Synthesizing report from the notebook...")
        papers = [ctx.paper_index[k] for k in sorted(ctx.paper_index)]
        notebook = "\n".join("- %s" % n for n in ctx.notebook) or "(empty)"
        prompt = prompts_mod.build_synthesis_prompt_v2(
            ["## Investigation notebook\n" + notebook],
            experiment_design, organism_name, papers)
        try:
            r = await bounded(Runner.run(agents["synth"], prompt, context=ctx,
                                         max_turns=3),
                              SDK_LONG_CALL_TIMEOUT, label="forced synthesis")
            report = str(r.final_output)
        except (Exception, asyncio.TimeoutError) as e:
            # Last resort, and it calls no model at all. The forced synthesis
            # above needs the same gateway that just failed -- measured: a 504
            # from the gateway killed the loop, then killed the synthesis, and a
            # run that had already delegated two interpretations, kept a
            # notebook and retrieved papers shipped NOTHING. Material that has
            # been gathered should not be lost to the thing that stopped the
            # gathering.
            stats["deterministic_fallback"] = "%s: %s" % (type(e).__name__, e)
            logger.warning("[%s][loop] synthesis unavailable (%s); assembling "
                           "the report from gathered material without a model",
                           job_id, e)
            report = _assemble_without_synthesis(ctx)
            if not report.strip():
                raise RuntimeError(
                    "The agent produced no report and had nothing gathered to "
                    "fall back on (loop: %s; synthesis: %s)"
                    % (stats.get("loop_backstop", "ended"), e))

    # ---- carry the delegated detail into the report ------------------------
    # Round 4 merged by RE-AUTHORING: one Report Writer pass over the draft plus
    # every delegated report, with the full master reference list attached. It
    # fixed length (8 870 -> 38 480 chars) and coverage (9 -> 18.5 pathways) and
    # wrecked grounding -- citations fell to 4.5, and round 6 showed why: a writer
    # handed ninety references cites three of them. agent.py records the same
    # effect for its batches ("a batch handed 20+ abstracts cites fewer of them").
    #
    # The delegated reports are ALREADY grounded: each was written against the <=10
    # papers attributed to its own pathways. So do not re-author them. Stitch them
    # in as the per-pathway analysis and spend the single writer call only on the
    # framing prose, where reference dilution costs nothing. That is the workflow
    # arm's grounding property -- per-batch attributed slices, preserved through
    # synthesis -- without its fixed control flow: the agent still chose what to
    # investigate, what to delegate and what to submit.
    # ---- give the citations something to be quoted FROM --------------------
    # Deterministic, batched, and placed before the merge guard so both its quote
    # probe and the gate's collection see the fuller text. Bounded by the clock
    # like every other post-loop step, and skipped without ceremony when tight.
    # Count BOTH citation forms. The delegated reports cite by PMID -- their
    # instructions (SYSTEM_PROMPT_INTERPRET) say "(PMID: XXXXXXXX)" -- and those
    # only become [N] markers when resolve_pmid_mentions runs inside the merge,
    # which is after this step. Counting markers alone saw 5 citations where the
    # run actually had 18, so this step found "0 thin" every time and quietly did
    # nothing: the diagnostic said "5 cited, 0 thin" and that number was the bug.
    cited_anywhere = set()
    for text in ctx.delegated + [report]:
        text = text or ""
        cited_anywhere |= {int(n) for n in re.findall(r"\[(\d+)\]", text)}
        for pmid in re.findall(r"PMID:?\s*(\d{6,9})", text):
            ref = ctx.pmid_to_ref.get(str(pmid))
            if ref:
                cited_anywhere.add(ref)
    thin = [ctx.paper_index[r] for r in sorted(cited_anywhere)
            if r in ctx.paper_index
            and ctx.paper_index[r].get("fetch_tier") == "abstract_only"]
    fulltext_budget = ((ctx.started_at + AGENT_RUN_SECONDS) - time.time()
                       - GATE_MIN_SECONDS - 60)
    # Always recorded, including the zero case. The first run with this step
    # showed no upgrade stat at all and left me inferring why from stored
    # documents -- a branch that says nothing when it does nothing is a branch
    # you cannot debug.
    stats["fulltext_candidates"] = "%d cited, %d thin, %ds budget" % (
        len(cited_anywhere), len(thin), max(0, int(fulltext_budget)))
    if thin and fulltext_budget > 20:
        t_f = time.time()
        try:
            upgraded = await bounded(
                asyncio.to_thread(ctx.pubmed.fetch_papers,
                                  [p["pmid"] for p in thin[:FULLTEXT_MAX_PAPERS]]),
                min(fulltext_budget, 90), label="full-text upgrade")
            by_pmid = {str(p.get("pmid")): p for p in (upgraded or [])}
            gained = 0
            for paper in thin[:FULLTEXT_MAX_PAPERS]:
                fresh = by_pmid.get(str(paper.get("pmid")))
                if fresh and fresh.get("fetch_tier") != "abstract_only":
                    fresh["ref_index"] = paper["ref_index"]
                    fresh["pathways"] = paper.get("pathways", [])
                    ctx.paper_index[paper["ref_index"]] = fresh
                    gained += 1
            stats["fulltext_upgraded"] = "%d of %d cited abstracts" % (gained,
                                                                      len(thin))
        except (Exception, asyncio.TimeoutError) as e:
            stats["fulltext_failed"] = "%s: %s" % (type(e).__name__, e)
        stats["fulltext_s"] = time.time() - t_f
    elif thin:
        stats["fulltext_skipped"] = "%d thin papers, %ds left" % (
            len(thin), max(0, int(fulltext_budget)))

    premade_quotes = None
    merge_budget = ((ctx.started_at + AGENT_RUN_SECONDS) - time.time()
                    - GATE_MIN_SECONDS)
    if ctx.delegated and MERGE_DELEGATED and merge_budget < 30:
        stats["merge_skipped"] = ("%ds left, the gate needs %ds"
                                  % (max(0, int(merge_budget + GATE_MIN_SECONDS)),
                                     int(GATE_MIN_SECONDS)))
        logger.info("[%s][loop] skipping the merge: %s", job_id,
                    stats["merge_skipped"])
    elif ctx.delegated and MERGE_DELEGATED:
        t_m = time.time()
        merge_timeout = min(SDK_LONG_CALL_TIMEOUT, merge_budget)
        papers_now = [ctx.paper_index[k] for k in sorted(ctx.paper_index)]
        valid = {p["ref_index"] for p in papers_now}
        before = len(count_body_citations(str(report), valid))
        detail = "\n\n".join(ctx.delegated)
        if len(detail) > STITCH_MAX_CHARS:
            # Delegations arrive in rank order, so truncating the tail drops the
            # lowest-ranked pathways' paragraphs -- the ones the report can most
            # afford to lose -- and says so rather than trailing off mid-sentence.
            detail = (detail[:STITCH_MAX_CHARS].rsplit("\n\n", 1)[0]
                      + "\n\n*(Lower-ranked pathways are listed in the tables "
                        "below; their per-pathway analysis was trimmed to keep "
                        "this report readable.)*")
            stats["stitch_truncated"] = True
        reused_head = reused_tail = None
        if MERGE_MODE == "stitch" and FRAMING_REUSE_LEAD:
            reused_head, reused_tail = _lead_framing(report)
            stats["framing_reused"] = bool(reused_head)
        if MERGE_MODE == "stitch" and reused_head:
            # No LLM call at all: the Lead's own framing, its own citations, and
            # the delegated detail spliced between them.
            candidate = resolve_pmid_mentions(
                "%s\n\n## Detailed Pathway Analysis\n\n%s\n\n%s"
                % (reused_head, detail, reused_tail), ctx.paper_index)
        elif MERGE_MODE == "stitch":
            framing_prompt = _build_framing_prompt(ctx, report, detail[:60000])
            try:
                framed = await bounded(
                    Runner.run(agents["synth"], framing_prompt, context=ctx,
                               max_turns=3),
                    merge_timeout, label="framing")
                framing = str(framed.final_output)
            except (Exception, asyncio.TimeoutError) as e:
                stats["framing_failed"] = "%s: %s" % (type(e).__name__, e)
                framing = report
            # Splice: framing's forward-looking sections stay last, the stitched
            # per-pathway detail goes between themes and follow-ups.
            marker = "## Suggested Follow-up Experiments"
            if marker in framing:
                head, tail = framing.split(marker, 1)
                candidate = "%s\n## Detailed Pathway Analysis\n\n%s\n\n%s%s" % (
                    head.rstrip(), detail, marker, tail)
            else:
                candidate = "%s\n\n## Detailed Pathway Analysis\n\n%s" % (
                    framing.rstrip(), detail)
            candidate = resolve_pmid_mentions(candidate, ctx.paper_index)
        else:
            prompt = prompts_mod.build_synthesis_prompt_v2(
                ctx.delegated + ["## The lead interpreter's own draft\n" + report],
                experiment_design, organism_name, papers_now)
            prompt += ("\n\nThe last block is the lead interpreter's draft: keep "
                       "its structure, its Key Findings and its judgements, and "
                       "restore the per-pathway detail from the batch reports that "
                       "the draft compressed away. Every pathway named in any block "
                       "gets its own paragraph. Do not invent citations.")
            try:
                merged = await bounded(
                    Runner.run(agents["synth"], prompt, context=ctx, max_turns=3),
                    merge_timeout, label="delegated merge")
                candidate = resolve_pmid_mentions(str(merged.final_output),
                                                  ctx.paper_index)
            except (Exception, asyncio.TimeoutError) as e:
                stats["merge_failed"] = "%s: %s" % (type(e).__name__, e)
                candidate = report
        after = len(count_body_citations(candidate, valid))
        # Count GROUNDED citations, not markers. The first version of this guard
        # compared raw [N] counts, and a stitch that added thirty markers passed
        # it -- then the deterministic net removed them all for having no quote:
        # one run reported 22 verifier calls, 5 refutations, and 32 redactions,
        # which is arithmetic that only works if two thirds of the citations were
        # never quote-backed at all. A citation without a quote is not grounding,
        # it is a number in brackets.
        #
        # Quote collection costs ~3 s (measured), so the guard can afford to ask
        # the real question, and the quotes it collects are reused by the gate
        # rather than recomputed.
        quote_probe = LLMClient(AI_PROVIDERS[AI_LLM_PROVIDER])
        # Both sides through the same sieve: a model reporting support is not the
        # same fact as the support being there, and the gate checks the second.
        #
        # Bounded, because these were the last unbounded work left in the run.
        # _collect_cited_quotes makes one LLM call per cited paper and the guard
        # runs it twice; with twenty papers on a slow gateway that is minutes,
        # and it sat OUTSIDE merge_timeout, which bounds only the writer call.
        # A run died at 601 s of a 600 s ceiling here. Every step after the loop
        # now answers to the clock, without exception.
        probe_budget = max(20.0, min(merge_timeout,
                                     (ctx.started_at + AGENT_RUN_SECONDS)
                                     - time.time() - GATE_MIN_SECONDS))
        try:
            before_quotes = _verified_quotes(ctx, await bounded(
                asyncio.to_thread(_collect_cited_quotes, quote_probe,
                                  str(report), ctx.paper_index, job_id),
                probe_budget / 2, label="quote probe (draft)"))
            raw_after = await bounded(
                asyncio.to_thread(_collect_cited_quotes, quote_probe,
                                  candidate, ctx.paper_index, job_id),
                probe_budget / 2, label="quote probe (candidate)")
        except (Exception, asyncio.TimeoutError) as e:
            # No probe, no informed judgement: keep the draft rather than accept
            # a stitch whose grounding could not be checked.
            stats["merge_probe_failed"] = "%s: %s" % (type(e).__name__, e)
            stats["merge_s"] = time.time() - t_m
            before_quotes, raw_after, candidate = {}, {}, str(report)
        grounded_after_quotes = _verified_quotes(ctx, raw_after)
        stats["quotes_unverifiable"] = len(raw_after) - len(grounded_after_quotes)
        grounded_before = len(before_quotes)
        grounded_after = len(grounded_after_quotes)
        # Judged on GROUNDED citations, not the raw marker count. The raw count
        # includes citations with no quote, which the very next block strips out
        # on acceptance and which the gate would delete anyway -- so requiring it
        # to rise rejects stitches for losing citations that were never going to
        # survive. Round 31 measured exactly that: "len 11209->55618, cites
        # 15->12, GROUNDED 8->10" -- a candidate with two MORE grounded
        # citations and 44 000 characters of pathway coverage, thrown away
        # because three unquotable markers went with it. That run shipped 10
        # pathways against base's 15.
        # Grounding must not fall, and the extra length must buy something:
        # more grounded citations, or more of the experiment covered. Round 31
        # rejected a stitch with two MORE grounded citations because three
        # unquotable markers went with it; round 32 accepted 35 120 characters
        # for grounded 9->9. Requiring the raw count to rise is too strict and
        # requiring nothing is too loose.
        coverage_before = _pathways_named(str(report), ctx)
        coverage_after = _pathways_named(candidate, ctx)
        stats["merge_coverage"] = "%d->%d" % (coverage_before, coverage_after)
        buys_something = (grounded_after > grounded_before
                          or coverage_after > coverage_before)
        if (len(candidate) > 1.2 * len(str(report))
                and grounded_after >= grounded_before
                and buys_something):
            stats["merge_gain_chars"] = len(candidate) - len(str(report))
            stats["merge_citations"] = "%d->%d" % (before, after)
            stats["merge_grounded"] = "%d->%d" % (grounded_before, grounded_after)
            # Strip the citations we already know have no quote. The guard
            # accepts a stitch when grounded citations RISE, so it can pass a
            # batch that adds ten grounded markers and eleven unquotable ones --
            # measured: one run had 33 verifier calls, 4 refutations and 15
            # redactions, the other 11 coming from the net removing citations
            # with no quote at all, and redaction takes the whole SENTENCE with
            # them. The claim usually came from the data, not the paper; the
            # citation was the decoration. So drop the decoration here and let
            # the sentence stand, rather than let the gate delete both.
            keep = set(grounded_after_quotes)
            def _strip(match):
                return "" if int(match.group(1)) not in keep else match.group(0)
            trimmed = re.sub(r"\s*\[(\d+)\]", _strip, candidate)
            dropped = (len(count_body_citations(candidate, valid))
                       - len(count_body_citations(trimmed, valid)))
            if dropped:
                stats["unquotable_markers_dropped"] = dropped
            report = trimmed
            premade_quotes = grounded_after_quotes
        else:
            stats["merge_rejected"] = (
                "len %d->%d, cites %d->%d, GROUNDED %d->%d"
                % (len(str(report)), len(candidate), before, after,
                   grounded_before, grounded_after))
        stats["merge_mode"] = MERGE_MODE
        stats["merge_s"] = time.time() - t_m

    # ---- optional: rewrite the dossier as a paper Results section ---------
    # Before the tables are appended and before the exit gate, so the rewritten
    # prose is what gets top-up, verification and redaction. See
    # _write_results_section for why the order is not negotiable.
    results_written = False
    if RESULTS_SECTION:
        cluster_text = ""
        if ctx.partition is not None:
            cluster_text = clusters_mod.render_partition_table(
                ctx.partition, _ctx_by_id(ctx)) or ""
        report, results_written = await _write_results_section(
            agents["synth"], ctx, report, cluster_text, pathways, job_id, stats,
            min(SDK_LONG_CALL_TIMEOUT, 180))
        stats["results_section"] = results_written

    # The deterministic tables ride below the prose, exactly as the workflow
    # arm ships them (its phase 5d): data the job already holds is appended,
    # never asked of the model.
    #
    # When a Results section was written, BOTH tables are suppressed. The
    # section describes the clustering in prose, which is the whole point of
    # asking for it, and a table restating it undoes that; the enriched-pathway
    # table is the same trade, and a Results section carries its numbers in the
    # sentences. The first live job with the stage on came back with prose AND
    # a 17-row table under it -- the dossier furniture the rewrite exists to
    # remove.
    #
    # (An earlier comment here claimed the enriched summary stays. It never
    # did; the condition below has always suppressed it. The comment was the
    # thing that was wrong.)
    if not results_written and "## Enriched Pathway Summary" not in report:
        report = report.rstrip() + "\n\n" + render_pathway_table(pathways)
    if ctx.partition is not None:
        if not results_written:
            table = clusters_mod.render_partition_table(ctx.partition,
                                                       _ctx_by_id(ctx))
            if table and "## Pathway Clusters" not in report:
                report = report.rstrip() + "\n\n" + table
        note = clusters_mod.render_reading_note(ctx.partition)
        if note and note not in report and not results_written:
            note = note + "\n\n" + report
            report = note

    # ---- The mandatory exit gate ------------------------------------------
    # Identical sequence to agent.py's tail (phases 5a''''-6): marker hygiene,
    # quotes, canonical references, the Claim Verifier pass, then the
    # programmatic net. Kept in the same order on purpose -- and pinned by
    # test_reference_section_ordering against THIS module too.
    unique_papers = [ctx.paper_index[k] for k in sorted(ctx.paper_index)]
    report = resolve_pmid_mentions(report, ctx.paper_index)
    report = normalize_citation_markers(report)

    # ---- citation top-up (parity with the workflow arm's phase 5) ---------
    valid_indices = {p["ref_index"] for p in unique_papers}
    cited_now = count_body_citations(str(report), valid_indices)
    uncited = [p for p in unique_papers if p["ref_index"] not in cited_now]
    topup_headroom = (ctx.started_at + AGENT_RUN_SECONDS) - time.time()
    if not TOPUP_ENABLED:
        stats["topup_disabled"] = True
        logger.info("[%s][loop] citation top-up disabled; the Lead's own "
                    "check_my_citations is the only citation pass", job_id)
    elif uncited and len(cited_now) < MIN_CITATIONS and topup_headroom < TOPUP_MIN_SECONDS:
        # Skipped, and said so: an unexplained absence in the stats is the same
        # shape as a step that silently never ran.
        stats["topup_skipped"] = "%.0f s left, needs %.0f" % (topup_headroom,
                                                              TOPUP_MIN_SECONDS)
        logger.info("[%s][loop] citation top-up skipped: %.0f s left",
                    job_id, topup_headroom)
    elif uncited and len(cited_now) < MIN_CITATIONS:
        listing = "\n".join(
            "[%d] %s — %s" % (p["ref_index"], (p.get("title") or "")[:110],
                              (p.get("abstract") or "")[:TOPUP_ABSTRACT_CHARS])
            for p in uncited[:TOPUP_OFFER_PAPERS])
        stats["topup_evidence_chars"] = len(listing)
        t_top = time.time()
        try:
            topped = await bounded(Runner.run(
                agents["synth"],
                "Here is your report:\n\n%s\n\n"
                "These retrieved papers are not cited anywhere in it:\n\n%s\n\n"
                "Return the SAME report with citations added wherever one of "
                "these papers genuinely supports a statement you already make. "
                "Change nothing else: no new findings, no rewritten analysis, "
                "no altered numbers. Add [N] only where that paper really does "
                "support that sentence -- a citation that does not fit is "
                "removed later along with the claim it sits on, so forcing one "
                "in costs you the finding. Leaving a paper uncited is a fine "
                "outcome." % (report, listing),
                context=ctx, max_turns=3),
                SDK_LONG_CALL_TIMEOUT, label="loop citation top-up")
            candidate = resolve_pmid_mentions(str(topped.final_output),
                                             ctx.paper_index)
            cited_after = count_body_citations(candidate, valid_indices)
            added = len(cited_after)
            # Same acceptance test as the workflow arm: a "top-up" that
            # shortens the report into a summary is a regression wearing a
            # bigger citation count.
            # A top-up must ADD. Measured: one replicate returned a net gain of
            # +1 while introducing SIXTEEN new references -- it had dropped
            # fifteen citations the report already had and swapped in its own.
            # Eleven of those failed at the gate and took 42 markers with them,
            # leaving five citations in a report that started with twenty.
            #
            # The old test asked only whether the net count rose and the length
            # held, so a wholesale citation swap passed it. Its own prompt says
            # "Return the SAME report with citations added ... Change nothing
            # else"; this is the check that the instruction was followed.
            dropped = set(cited_now) - set(cited_after)
            if dropped:
                stats["topup_dropped_existing"] = len(dropped)
            if (len(candidate) > 0.6 * len(str(report))
                    and added > len(cited_now) and not dropped):
                report = candidate
                stats["topup_added"] = added - len(cited_now)
                # WHICH references it added, so the gate can price the trade.
                # Top-up marks sentences that already stood on their own; if
                # one of its citations then fails, redact_unverified_v2 deletes
                # that whole sentence along with it. Counting only the
                # citations gained measures the upside of a bet whose downside
                # is prose, which is how a stage can look free and not be.
                stats["topup_added_refs"] = sorted(set(cited_after)
                                                   - set(cited_now))
                # As a string too, because the archive keeps scalars and notes
                # and this list is the only way to ask WHERE in the top-up's
                # sequence its failures fall. Base adds 1.5 citations a run and
                # fails none; this arm adds 13 and fails 5.5, so the marginal
                # addition is the suspect and the marginal addition is exactly
                # what a list of indices identifies.
                stats["topup_refs"] = ",".join(
                    str(r) for r in stats["topup_added_refs"])
                # Give the newly cited papers the full text the upgrade could
                # not have fetched for them.
                #
                # The upgrade earlier in this function selects `thin` = papers
                # ALREADY cited that are abstract-only. The top-up then cites
                # papers that were NOT cited -- by definition the ones the
                # upgrade skipped -- so every citation it adds points at an
                # abstract, and a specific mechanistic claim rarely has a
                # quotable sentence in one.
                #
                # That is the whole of rule 3's failure. Measured across rounds
                # 39-41, topup_added_failed EQUALS failed_citations in every
                # replicate: each failed citation was one the top-up added. The
                # shipped arm has no such problem because it batch-fetches full
                # text for everything it retrieves, and its top-up fails 0.0.
                await _upgrade_new_citations(ctx, stats,
                                             stats["topup_added_refs"])
            else:
                stats["topup_rejected"] = True
                # WHICH of the three conditions rejected it. Measured over 13
                # archived runs: 5 top-ups (38%) were rejected, and only 2 of
                # those recorded anything about why -- `topup_dropped_existing`
                # happens to be set independently. The other 3 failed on
                # "too short" or "added nothing" and are indistinguishable, so
                # the one lever in this pipeline with no citation cost (a
                # rejected top-up delivers zero BY DEFINITION, so removing it
                # cannot lose a citation) cannot be aimed at anything.
                #
                # The prompt already says "Return the SAME report with citations
                # added ... Change nothing else", so each condition implies a
                # different failure: `short` means it truncated the report,
                # `no_gain` means it complied and found nothing to add, and
                # `dropped` means it rewrote instead of appending. Only the
                # first and third are prompt-compliance failures worth fixing.
                reasons = []
                if len(candidate) <= 0.6 * len(str(report)):
                    reasons.append("short")
                if added <= len(cited_now):
                    reasons.append("no_gain")
                if dropped:
                    reasons.append("dropped")
                stats["topup_rejected_why"] = ",".join(reasons) or "unknown"
                # The sizes too: "short" is a ratio and the ratio is the datum.
                stats["topup_candidate_ratio"] = round(
                    len(candidate) / max(len(str(report)), 1), 3)
        except (Exception, asyncio.TimeoutError) as e:
            stats["topup_failed"] = "%s: %s" % (type(e).__name__, e)
            logger.warning("[%s][loop] citation top-up failed: %s: %s",
                           job_id, type(e).__name__, e)
        stats["topup_s"] = time.time() - t_top
        # The top-up was the single largest untraced stage in the pipeline.
        # Measured across the archive it costs a median 101 s -- 28% of a 380 s
        # run, more than the whole verify fan-out -- and the gate trace showed
        # nothing at all for it: bursts of verify events with a 65-120 s hole
        # between them and "nothing traced in this window". An instrumentation
        # that cannot see the second-biggest cost in the run is not tracking the
        # pipeline, it is tracking the parts that were easy to reach.
        _trace_gate(ctx, "citation_topup",
                    "%d uncited papers offered" % len(uncited),
                    "added %s, failed %s" % (stats.get("topup_added", 0),
                                             stats.get("topup_added_failed", 0)),
                    t_top)

    llm_for_quotes = LLMClient(AI_PROVIDERS[AI_LLM_PROVIDER])
    _hb(ctx, "verifying", 80, "Collecting supporting quotes...")
    t_q = time.time()
    if premade_quotes is not None:
        # The merge guard already collected these for the text it accepted.
        quotes = dict(ctx.quotes)
        quotes.update(premade_quotes)
        stats["quotes_reused"] = len(quotes)
    else:
        # Seeded with whatever delegation already grounded: those citations were
        # quoted while the sub-agent's own papers were in hand, and re-deriving
        # them here, against a merged report and a shrinking clock, is what left
        # round 25's second replicate with no citations at all.
        quotes = dict(ctx.quotes)
        stats["quotes_from_delegation"] = len(quotes)
        quotes.update(_collect_cited_quotes(llm_for_quotes, report,
                                            ctx.paper_index, job_id, known=quotes))
    stats["quotes_s"] = time.time() - t_q
    _trace_gate(ctx, "collect_quotes", "%d citation(s)" % len(quotes),
                "reused" if premade_quotes is not None else "collected", t_q)
    report, rendered = render_references_section(report, ctx.paper_index, quotes)
    stats["refs_rendered"] = len(rendered)
    stats["quotes_supplied"] = len(quotes)

    _hb(ctx, "verifying", 85, "Verifying citations...")
    # Same instructions as agents["verify"], no tools: with the passage in the
    # prompt there is nothing to look up, and nothing to run out of turns on.
    verifier_solo = Agent[LoopContext](
        name="Claim Verifier (prefetched)",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_VERIFICATION,
        model_settings=ModelSettings(temperature=0.1),
        tools=[],
    )
    t0 = time.time()
    verify_deadline = min(t0 + VERIFY_MAX_SECONDS,
                          ctx.started_at + AGENT_RUN_SECONDS - 45)
    previous_failures = None
    for _iteration in range(VERIFY_ITERATIONS):
        if time.time() > verify_deadline:
            stats["verify_cut_short"] = _iteration
            logger.info("[%s][loop] verification out of clock after %d round(s); "
                        "the programmatic net takes the rest", job_id, _iteration)
            break
        stats["verify_iterations"] = _iteration + 1
        citations = parse_references_section(report)
        to_verify = [c for c in citations if c.get("cited_text")]
        if not to_verify:
            break
        vsem = asyncio.Semaphore(SDK_VERIFY_CONCURRENCY)

        async def _verify_one_prefetched(cit):
            """One short call, with the paper's own words already in the prompt."""
            async with vsem:
                executor = tools_mod.build_verification_executor(ctx.paper_index)
                passage = ""
                try:
                    passage = executor("search_paper_text",
                                       {"ref_index": cit["ref_index"],
                                        "query": (cit["cited_text"] or "")[:180]})
                    if not passage or passage.lower().startswith("error"):
                        passage = executor("fetch_paper_section",
                                           {"ref_index": cit["ref_index"],
                                            "section": "abstract"})
                except Exception as e:
                    passage = "(the paper's text could not be searched: %s)" % e
                prompt = (prompts_mod.build_verification_prompt(
                              cit["claim_sentence"], cit["cited_text"],
                              cit["ref_index"])
                          + "\n\n## What paper [%s] actually says, retrieved for "
                            "you\n%s\n\nJudge from the text above. Do not ask for "
                            "more; if the quote is not in it, say so."
                          % (cit["ref_index"], passage[:6000]))
                try:
                    r = await bounded(
                        Runner.run(verifier_solo, prompt, context=ctx, max_turns=2),
                        SDK_CALL_TIMEOUT, label="verify[%s]" % cit["ref_index"])
                    return cit, str(r.final_output)
                except Exception as e:
                    logger.warning("[%s][loop] prefetched verifier failed for "
                                   "[%s]: %s", job_id, cit["ref_index"], e)
                    return cit, ""

        async def _verify_one(cit):
            async with vsem:
                try:
                    r = await run_hedged(
                        agents["verify"],
                        prompts_mod.build_verification_prompt(
                            cit["claim_sentence"], cit["cited_text"],
                            cit["ref_index"]),
                        ctx, max_turns=6, label="verify[%s]" % cit["ref_index"])
                    return cit, str(r.final_output)
                except Exception as e:
                    logger.warning("[%s][loop] verifier raised for [%s]: %s",
                                   job_id, cit["ref_index"], e)
                    return cit, ""

        checker = _verify_one_prefetched if VERIFY_PREFETCH else _verify_one

        async def _timed(cit):
            started = time.time()
            cit, text = await checker(cit)
            verdict = _parse_json_verdict(text) if text else None
            _trace_gate(ctx, "verify_citation" + ("_prefetched" if VERIFY_PREFETCH
                                                  else "_toolloop"),
                        "[%s]" % cit["ref_index"],
                        "no verdict (raised or empty)" if verdict is None else
                        "match=%s supports=%s" % (verdict.get("text_match"),
                                                  verdict.get("supports_claim")),
                        started)
            return cit, text

        # Hard-bound the fan-out itself, not just the decision to start another
        # round. A deadline checked at the top of the loop bounds nothing: one
        # round over nineteen citations, each hedged at 45 s x2 across eight
        # workers, ran a run to 602 s and it died on the ceiling with a finished
        # report it never got to ship.
        #
        # Whatever does not finish in time counts as unverified, which is exactly
        # what verify_report_v2 and redact_unverified_v2 handle deterministically
        # a few lines below. Stopping early costs a redaction; overrunning costs
        # the entire interpretation.
        budget = max(20.0, verify_deadline - time.time())
        tasks = [asyncio.ensure_future(_timed(c)) for c in to_verify]
        done, pending = await asyncio.wait(tasks, timeout=budget)
        for task in pending:
            task.cancel()
        if pending:
            stats["verify_unchecked"] = (stats.get("verify_unchecked", 0)
                                         + len(pending))
            logger.info("[%s][loop] %d of %d citations unchecked when the clock "
                        "ran out; the programmatic net takes them", job_id,
                        len(pending), len(to_verify))
        verdicts = []
        for task in done:
            try:
                verdicts.append(task.result())
            except Exception:
                continue
        failed = []
        for cit, text in verdicts:
            v = _parse_json_verdict(text) if text else {
                "text_match": False, "supports_claim": False,
                "reasoning": "Verification error"}
            if not v.get("text_match") or not v.get("supports_claim"):
                failed.append({"ref_index": cit["ref_index"],
                               "reason": v.get("reasoning", "Verification failed"),
                               "cited_text": cit["cited_text"],
                               "claim_sentence": cit["claim_sentence"],
                               "actual_text": v.get("actual_text", ""),
                               "suggested_fix": v.get("suggested_fix", ""),
                               # Which repair the correction prompt should ask
                               # for: a real quote with an oversold sentence
                               # needs the SENTENCE changed, not the quote.
                               "mode": ("text" if not v.get("text_match")
                                        else "claim")})
        failed.sort(key=lambda c: c["ref_index"])
        logger.info("[%s][loop] VERIFY iter %d: %d checked, %d failed",
                    job_id, _iteration + 1, len(to_verify), len(failed))
        if not failed:
            break
        if previous_failures is not None and len(failed) >= previous_failures:
            break
        previous_failures = len(failed)
        if _iteration == VERIFY_ITERATIONS - 1:
            break
        # A correction rewrite regenerates the entire report. Measured at 117 s
        # inside verify_loop_s, and it was bounded only by a per-call timeout --
        # so the last thing a run out of time did was start the most expensive
        # call it has. What is already verified ships; what failed is redacted
        # deterministically a few lines below, with no model needed.
        correction_budget = ((ctx.started_at + AGENT_RUN_SECONDS) - time.time()
                             - GATE_MIN_SECONDS)
        if correction_budget < 30:
            stats["correction_skipped"] = "%.0f s left" % max(0, correction_budget)
            logger.info("[%s][loop] correction rewrite skipped: %.0f s left",
                        job_id, correction_budget)
            break
        if SENTENCE_REPAIR:
            # Same trade as the shipped arm: repair the failed sentences
            # themselves, in parallel, and leave everything else alone. The
            # References section, its quotes and the appended tables all stay
            # valid, so the three repair steps below are not needed either.
            report, fixed = await _repair_sentences(
                agents["synth"], ctx, report, failed, job_id, stats,
                min(SDK_CALL_TIMEOUT, correction_budget))
            if not fixed:
                logger.info("[%s][loop] no sentence could be repaired; leaving "
                            "%d citation(s) to the programmatic net",
                            job_id, len(failed))
                break
            continue

        try:
            corr = await bounded(Runner.run(
                agents["synth"],
                "Here is your report:\n\n%s\n\n%s"
                % (report, prompts_mod.build_correction_prompt(report, failed)),
                context=ctx, max_turns=3),
                min(SDK_LONG_CALL_TIMEOUT, correction_budget),
                label="loop correction rewrite")
        except (Exception, asyncio.TimeoutError) as e:
            stats["correction_failed"] = "%s: %s" % (type(e).__name__, e)
            break
        report = resolve_pmid_mentions(str(corr.final_output), ctx.paper_index)
        report = normalize_citation_markers(report)
        quotes.update(_collect_cited_quotes(llm_for_quotes, report,
                                            ctx.paper_index, job_id, known=quotes))
        report, _ = render_references_section(report, ctx.paper_index, quotes)
    stats["verify_loop_s"] = time.time() - t0

    # ---- Phase 6, verbatim: the programmatic net --------------------------
    t0 = time.time()
    final = verify_report_v2(report, gene_whitelist, unique_papers, job_instance)
    # WHICH references failed, not just how many. The count cannot answer the
    # question round 40 raised -- are the failures concentrated in papers admitted
    # while the screen was being permissive? -- because a paper's ref_index IS its
    # admission order. stats["verification"] holds this and is a dict, so the
    # bench drops it and every archived round kept only the total.
    _failed = final.get("failed_citations") or []
    if _failed:
        stats["failed_refs"] = ",".join(
            str(c.get("ref_index")) for c in _failed
            if isinstance(c.get("ref_index"), int))
    score_topup_survival(stats, final)
    # Which retrieval machinery the surviving quotes actually came from. Placed
    # here on purpose: renumber_citations rewrites every ref_index a few lines
    # below, and the quotes dict is keyed on the OLD ones.
    stats.update(quote_provenance(quotes, ctx.paper_index))
    if final.get("failed_citations") and VERIFY_TOPUP:
        # A failed citation the TOP-UP added is a marker bolted onto prose that
        # already stood on its own, so pulling it back restores the sentence
        # exactly. A failed citation the writer put there is a claim with no
        # support left, and redaction is right for it.
        #
        # The gate has already decided which citations fail; this only decides
        # what failing costs. An earlier attempt ran its own quote check before
        # the gate and was wrong twice over -- it tested whether a quote EXISTS
        # while the gate tests whether the quote SUPPORTS the claim, and it read
        # `known` backwards, stripping markers off citations that were fine.
        added = set(stats.get("topup_added_refs") or [])
        bolted = [c for c in final["failed_citations"]
                  if c.get("ref_index") in added]
        if bolted:
            report, pulled = strip_markers(report, [c["ref_index"] for c in bolted])
            stats["topup_markers_pulled"] = pulled
            stats["topup_pulled_back"] = len(bolted)
            final["failed_citations"] = [c for c in final["failed_citations"]
                                         if c.get("ref_index") not in added]
    if final.get("failed_citations"):
        report, removed = redact_unverified_v2(report, final["failed_citations"])
        final["redacted_count"] = removed
        # Markers taken out vs claims destroyed. `redacted` has always counted
        # the former -- bad markers plus dropped reference entries -- while this
        # project's notes described it as sentences lost. A sentence that keeps
        # another verified citation survives with the bad marker stripped, so the
        # two numbers can differ by a factor of three.
        stats["sentences_dropped"] = last_sentences_dropped()
    # Last, after every step that could hand the whole report back to the
    # synthesizer: take the dossier furniture off again. See
    # _strip_reintroduced_furniture -- `results_section: true` is not a promise
    # that the stored report still looks like one.
    if results_written:
        report = _strip_reintroduced_furniture(
            report, [p.get("name") for p in (pathways or []) if p.get("name")],
            stats)
    report, citation_mapping = renumber_citations(report)
    report = sort_references_section(report)
    if citation_mapping:
        kept = []
        for p in unique_papers:
            if p["ref_index"] in citation_mapping:
                p["ref_index"] = citation_mapping[p["ref_index"]]
                kept.append(p)
        kept.sort(key=lambda p: p["ref_index"])
        unique_papers = kept
    stats["verify_s"] = time.time() - t0
    stats["tool_calls"] = ctx.tool_calls
    # The TRUE retrieval count, into stats so it reaches the stored record and
    # the scorer. It was stamped only into the outcome trace before, so the
    # scorer kept falling back to len(papers) -- the reference list the gate had
    # already filtered -- and reported 9 for a run that retrieved 68.
    stats["papers_retrieved"] = len(ctx.paper_index)
    # Which SEARCHES earned their place. search_literature is ~60% of every tool
    # call the Lead makes, it retrieves at ~99.9% novelty -- and 88% of what it
    # brings back is never cited. Retrieval is not the waste; conversion is.
    #
    # Each search is tagged with the theme it supports, and that tag rides on
    # every paper it registered, so the tags left on the CITED papers say which
    # searches reached the report and which only spent budget. A per-tag figure
    # is the difference between "the agent over-searches" (untrue: novelty is
    # near-perfect) and "the agent searches themes it never writes about".
    searched, converted = _theme_conversion(ctx.searched_tags, unique_papers)
    stats["tags_searched"] = searched
    stats["tags_with_a_cited_paper"] = converted
    # Comparable across arms: themes that brought literature back, and themes
    # whose literature survived into the references.
    retrieved_all = list(ctx.paper_index.values()) if ctx.paper_index else []
    themes, converted = theme_conversion(retrieved_all, unique_papers)
    stats["themes_retrieved"] = themes
    stats["themes_cited"] = converted
    _retries = run_retry_counts()
    if _retries:
        stats["gateway_retries"] = _retries.get("transport", 0)
        stats["gateway_rate_limited"] = _retries.get("rate_limited", 0)
    stats["verification"] = final
    # Stamp the outcome next to the tool calls that produced it. Mongo keeps one
    # interpretation per JOB, so it can answer "how did this job's last run go"
    # and nothing else -- an association between a tool and the report it helped
    # produce came out at n=1 because two jobs were reused for forty runs. The
    # archive now closes that loop: configuration at the start, tool calls in the
    # middle, outcome at the end, one self-contained file per run.
    try:
        body = str(report).split("### References")[0]
        _trace_gate(ctx, "__outcome__", "run end", json.dumps({
            "prose_chars": len(body),
            "citations": len(count_body_citations(
                body, {p["ref_index"] for p in unique_papers})),
            "redacted": final.get("redacted_count", 0),
            "papers": len(unique_papers),
            # unique_papers is filtered to the SURVIVING references above -- but
            # only when citation_mapping is non-empty. A run that keeps its
            # citations therefore reports a small number and a run that loses
            # every one reports the full retrieval, so the metric reads
            # backwards exactly when it matters. The index is the truth.
            "papers_retrieved": len(ctx.paper_index),
            "full_text_papers": sum(1 for p in unique_papers
                                    if p.get("full_text_available")),
            "seconds": round(time.time() - ctx.started_at, 1),
        }), time.time())
    except Exception as exc:                      # never lose a finished report
        # This is measurement, and it sits on the return path of a run that has
        # already spent its ten minutes. Nothing it can hit -- a paper without a
        # ref_index, a report that is not a string -- is worth discarding the
        # interpretation for. Log and hand back the report.
        logging.warning("[AGENT] outcome stamp failed: %s", exc)
    # And stamp the stats dict itself, DERIVED rather than hand-listed.
    #
    # `__outcome__` above names seven fields by hand. Everything else this run
    # measured -- why the top-up was rejected, what it dropped, which stage
    # timed out -- goes only into Mongo, and Mongo keeps ONE interpretation per
    # JOB. Measured: 218 runs in the archive, 81 interpretation documents for 81
    # jobs, and of those, `topup_s` survives in 3 and `topup_rejected` in 1. So
    # of 23 top-up calls the archive can price, the REASON each one added
    # nothing survives for at most three, because every later run on the same
    # job overwrote it.
    #
    # This is the same bug `__config__` had: a hand-written list that reports
    # exactly the fields someone thought to add and silently omits the one a
    # later question needs. That was fixed by deriving the config from the
    # module source (12 hand-listed flags became 34). Derive here too -- take
    # every scalar the run recorded, whatever it is called -- so the next
    # question does not need this line edited before it can be asked.
    try:
        scalars = {}
        for k, v in (stats or {}).items():
            if isinstance(v, bool) or isinstance(v, (int, float)):
                scalars[k] = v
            elif isinstance(v, str) and len(v) <= 120:
                scalars[k] = v
            # dicts/lists (verification, pathwayIndex, topup_added_refs) are
            # deliberately skipped: they are large, they are already elsewhere,
            # and one of them is what makes a trace file worth megabytes.
        if scalars:
            _trace_gate(ctx, "__stats__", "%d scalars" % len(scalars),
                        json.dumps(scalars, sort_keys=True), time.time())
    except Exception as exc:                      # same contract as above
        logging.warning("[AGENT] stats stamp failed: %s", exc)
    if hooks.get("notebook") and ctx.notebook:
        try:
            hooks["notebook"](list(ctx.notebook))
        except Exception:
            pass
    return report, unique_papers, ctx


def run_agent_loop_workflow(job_instance, job_id, experiment_design,
                            budgets=None, hooks=None):
    """Synchronous entry point; same contract as run_agent_workflow."""
    budgets = budgets or {"max_pathways": AI_MAX_PATHWAYS}
    stats = {"mode": "full_agent"}
    t0 = time.time()

    async def _with_deadline():
        try:
            return await asyncio.wait_for(
                _run_loop_async(job_instance, job_id, experiment_design,
                                budgets, stats, hooks=hooks),
                timeout=AGENT_RUN_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "The agent interpretation exceeded its %d-minute limit and "
                "was stopped. Please try again later."
                % max(1, int(AGENT_RUN_SECONDS // 60)))

    report, papers, ctx = asyncio.run(_with_deadline())
    stats["total_s"] = time.time() - t0
    return {"report": report, "papers": papers, "stats": stats}
