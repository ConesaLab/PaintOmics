#!/usr/bin/env python3
"""Run and score the AI interpretation arms against the pre-registered rule.

`docs/ai-agent-benchmark.md` describes a protocol; this is the thing that
executes it. Until now it did not live in the repository at all -- it was a
scatter of scripts in a session scratchpad, which meant the documented protocol
could not be re-run by anyone, including a later session of mine. Round 25 was
pre-registered against a runner that would have vanished with the tmpdir.

Three subcommands:

    python -m src.benchmarks.ai_arm_bench ready
        One 8-token call to the configured gateway. Exit 0 if it answers.
        Two replicates once burned ten minutes each against a gateway that was
        504-ing every request and produced two "results" that were outage
        reports. A round should not start unless the instrument works.

    python -m src.benchmarks.ai_arm_bench run <jobID> <base|agent> <outdir>
        Runs one arm in-process on a stored job, writes <label>.json (metrics)
        and <label>.report.md (the prose -- the next run on that job overwrites
        the stored record, and metrics cannot answer "is this any good").

    python -m src.benchmarks.ai_arm_bench score <outdir>
        Groups the runs by arm, prints the table, and applies the five rules.

THE COVERAGE METRIC IS PROSE-ONLY, and that is not a detail. The first version
counted a pathway as covered if its name appeared anywhere in the report -- and
both arms APPEND a table of pathway names (the workflow arm's "Enriched Pathway
Summary", the agent arm's "Pathway Clusters"). The agent arm scored 102/102 by
printing a table it had not analysed. Everything from the first table marker or
the References heading onward is excluded here, permanently.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Everything after one of these is appended data, not analysis.
TABLE_MARKERS = ("## Enriched Pathway Summary", "## Pathway Clusters",
                 "### References", "## References")

CEILING_SECONDS = 600

STATEGRA_DESIGN = (
    "Mouse B-cell precursor differentiation time course (Ikaros induction vs "
    "control) over six time points: 0h, 2h, 6h, 12h, 18h, 24h. Five omic "
    "layers: gene expression (RNA-seq), proteomics, miRNA-seq, DNase-seq "
    "chromatin accessibility, metabolomics.")


def prose_of(report):
    """The part of a report a model actually wrote about the biology."""
    end = len(report)
    for marker in TABLE_MARKERS:
        i = report.find(marker)
        if i != -1:
            end = min(end, i)
    return report[:end]


# -- ready -----------------------------------------------------------------

def cmd_ready(_args):
    """Is the gateway answering? Exit 0 if yes, 1 if not -- and say WHICH failure.

    "Unreachable" was the wrong word for two and a half hours of outage. The
    host resolved, TCP connected in 0.02 s and TLS handshook; what failed was the
    model service behind the proxy, which nginx eventually reported as HTTP 504.
    A probe that calls all of that "unreachable" sends you looking at your own
    network. The three cases need different responses:

        cannot resolve / connect  -> your side, or the host is gone
        proxy answers 5xx         -> the gateway is up, its upstream is down
        no answer at all in time  -> upstream is hanging rather than refusing
    """
    import socket
    import requests
    from src.conf.serverconf import AI_PROVIDERS, AI_LLM_PROVIDER
    cfg = AI_PROVIDERS[AI_LLM_PROVIDER]
    base = cfg["api_base"].rstrip("/")
    host = base.split("//")[-1].split("/")[0]

    if not cfg.get("api_key"):
        print("no API key configured for provider %r" % AI_LLM_PROVIDER)
        return 1
    try:
        socket.create_connection((host, 443), timeout=10).close()
    except Exception as exc:
        print("cannot reach %s: %s: %s -- this is the network or the host, "
              "not the model service" % (host, type(exc).__name__, str(exc)[:70]))
        return 1

    try:
        response = requests.post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer %s" % cfg["api_key"],
                     "Content-Type": "application/json"},
            json={"model": cfg["model"], "max_tokens": 4, "temperature": 0.0,
                  "messages": [{"role": "user", "content": "say ready"}]},
            timeout=(10, 45))
    except requests.Timeout:
        print("%s accepts connections but did not answer an 8-token call in 45 s: "
              "the proxy is up and its upstream is hanging" % host)
        return 1
    except Exception as exc:
        print("gateway probe failed: %s: %s" % (type(exc).__name__, str(exc)[:80]))
        return 1

    if response.status_code == 200:
        print("gateway ready")
        return 0
    if response.status_code in (502, 503, 504):
        print("%s answered HTTP %d: the proxy is healthy, the model service "
              "behind it is down. Nothing to fix on this side."
              % (host, response.status_code))
    else:
        print("gateway unhealthy: HTTP %d %s"
              % (response.status_code, response.text[:80].replace("\n", " ")))
    return 1


# -- run -------------------------------------------------------------------

def cmd_run(args):
    os.environ["AI_AGENT_MAX_RUN_SECONDS"] = str(CEILING_SECONDS)
    if args.arm == "agent":
        os.environ["AI_FULL_AGENT"] = "1"
    else:
        os.environ.pop("AI_FULL_AGENT", None)

    from src.classes.AIInterpret.agent import run_ai_agent
    from src.common.DAO.AIInterpretDAO import AIInterpretDAO
    from src.paintomicsserver import Response

    label = args.label or "%s-%s" % (args.arm, args.jobID)
    os.makedirs(args.outdir, exist_ok=True)

    started = time.time()
    response = Response()
    run_ai_agent(args.jobID, args.design, response)
    wall = time.time() - started

    dao = AIInterpretDAO()
    try:
        record = dao.find_by_job_id(args.jobID) or {}
    finally:
        dao.closeConnection()

    metrics = _measure(record, args.arm, args.jobID, wall, response)
    path = os.path.join(args.outdir, label + ".json")
    with open(path, "w") as handle:
        json.dump(metrics, handle, indent=1)
    with open(os.path.join(args.outdir, label + ".report.md"), "w") as handle:
        handle.write(record.get("report") or "")
    print(json.dumps(metrics, indent=1))
    return 0 if metrics["status"] == "done" else 1


# Where the ten minutes went. Archived per run so a round is diagnosable
# afterwards: until now only wall_s survived, so a 602 s timeout and a 240 s
# run were equally opaque about which stage ate the budget.
STAGE_TIMES = ("topup_fulltext_s", "topup_verify_s",
               "verify_fanout_s", "verify_repair_s",   # the two halves of the loop
               "triage_s", "plan_s", "retrieval_s", "interpret_s", "gap_fill_s",
               "synth_s", "topup_s", "verify_loop_s", "verify_s",       # shipped arm
               "loop_s", "fulltext_s", "quotes_s", "merge_s",           # agent arm
               "results_s")                                             # Results section
STAGE_COUNTS = ("delegate_fulltext_gained", "quotes_supplied", "quotes_reused", "quotes_from_delegation", "refs_rendered", "verify_unchecked", "verify_cut_short", "unquotable_markers_dropped", "agent_tool_calls", "agent_searches", "agent_notebook", "stitch_truncated", "topup_evidence_chars", "genes_shown", "genes_flat", "merge_gain_chars", "verify_iterations", "batches_failed", "truncated_calls",
                "forced_synthesis", "topup_added", "quotes_unverifiable",
                # The other half of the top-up's bet. Recording only
                # topup_added archives its wins and drops its losses, and a
                # stage measured on its wins alone can never be retired.
                "topup_added_failed", "topup_rejected",
                # Seconds are half of what a tool costs; the other half is the
                # context every later turn has to carry.
                "tool_chars",
                # The Results-section rewrite. Both word counts, because the
                # stage exists to compress and the RATIO is the measurement --
                # round 66 caught a 984-word interpretation coming back at 1408.
                "results_words_before", "results_words_after",
                # How many citations survived, and how many attempts it took.
                # attempts>1 is the norm, not an anomaly: the first pass drops
                # citations in most runs, so this is the number that says
                # whether the guard is load-bearing.
                "results_citations_kept", "results_attempts", "results_section",
                # "short" is a ratio, so keep the ratio, not just the verdict.
                "topup_candidate_ratio",
                # How big the agent's territory was, and how much of it a tool
                # edited out of a request without saying so. Coverage means
                # nothing without the denominator: 15 pathways of 15 and 15 of
                # 114 are the same numerator and opposite results, and every
                # round before this one recorded only the numerator.
                "universe_pathways", "detail_deferred",
                "delegate_trimmed_for_time",
                # Gene-level access. Whether the agent USES the way out of the
                # top-ten cut is the whole question the tools were added to
                # answer, and it is unanswerable from the report alone.
                "kgml_files_read",
                # The Results rewrite's conservation, which is the whole point
                # of the stage: round 66 kept every citation and still lost 22%
                # of the rubric's coverage, so citations alone cannot say
                # whether the findings survived. Both sides of the ratio are
                # kept -- "kept" without "before" cannot distinguish a rewrite
                # that held 16 of 16 from one that held 16 of 40.
                "results_pathways_kept", "results_pathways_before",
                # How many calls the chunked rewrite took, how many pathways a
                # per-chunk retry had to restore, how many sections a named-
                # furniture heading turned out to be carrying, and how many
                # chunks ended up keeping their ORIGINAL dossier text because
                # three attempts would not conserve their markers or pathways.
                # `reverted` is the stage's real cost now: a rewrite is no
                # longer all-or-nothing, so this is where the loss shows up.
                "results_chunks", "results_retried_pathways",
                "results_furniture_kept", "results_chunk_reverted",
                # How many furniture sections a post-gate rewrite put BACK and
                # the final strip had to remove. Non-zero means the correction
                # or top-up pass is undoing the Results section, which is
                # invisible in `results_section` -- that flag reports what the
                # stage produced, not what was stored.
                "results_furniture_restripped",
                # Which searches reached the report. Retrieval novelty is
                # ~99.9%; conversion to a citation is ~12%, so the useful
                # question is per-theme, not per-call.
                "tags_searched", "tags_with_a_cited_paper",
                # Did each delegated chunk get its own literature, or the
                # fallback that hands it somebody else's?
                "delegate_matched", "delegate_fallback",
                # The one retrieval measure both arms report on equal terms.
                "themes_retrieved", "themes_cited",
                # Gateway weather. Round 34 saw 1 transport rate-limit retry
                # across 8 replicates; round 35 saw 16.
                "gateway_retries", "gateway_rate_limited",
                # The sentence-repair outcome. Set in agent.py from the first
                # commit and captured here from none of them, which is how a
                # round got launched that could not answer its own question:
                # the stage runs, the stats exist, and the archived row is
                # silent. A measurement not in STAGE_* is a measurement that
                # did not happen.
                "sentences_repaired", "repairs_rejected", "repair_unlocatable",
                "verify_citations_checked", "verify_memo_skipped",
                # Verifier deaths. 53 across rounds 34-36, all in the base arm.
                "verifier_raised",
                # Tool YIELD, not adoption or cost: did the surviving quote come
                # from the abstract search already had, or from a full-text
                # upgrade that read_paper and the fetch stage paid for?
                "quotes_from_abstract", "quotes_from_full_text",
                "quotes_unlocatable_here",
                # Where base's citations are BORN. Its interpretation batches
                # were logged emitting zero [N] markers while the run shipped
                # 17-26 citations, so they arrive later -- and the code counts
                # this exactly because "the batches never cited" and "the
                # synthesis dropped what the batches supplied" look identical
                # from outside. Set since the counter was written and archived
                # by nothing, so there is no history to check it against.
                "batches", "batches_with_citations", "batch_citations",
                "synth_citations",
                # The agent arm's symmetric pair: what its delegated writers
                # were shown, and how many distinct [N] they actually wrote.
                "delegate_papers_shown", "delegate_markers",
                # The screen base has always had and this arm never did.
                "papers_screened_out",
                # Claims destroyed, as distinct from `redacted`, which counts
                # markers removed plus dropped reference entries.
                "sentences_dropped", "topup_fulltext_gained",
                "topup_pulled_back", "topup_markers_pulled",
                "topup_dropped_existing",
                # Does the agent fill notebook_write's `subject`? The
                # pre-registered falsifier for that argument was a blank rate
                # above ~30%, and nothing recorded it.
                "note_subjects_blank", "note_subjects_total",
                # Turns spent re-reading an abstract already in the listing.
                "abstract_rereads")

# Itemised bills. A per-tool breakdown is the only form in which "which tool is
# worth its place" can be asked of the archive rather than of one live run.
STAGE_MAPS = ("tool_chars_by_tool", "tool_calls_by_tool")

# Outcomes whose value is a sentence, not a number: a stage that skipped or
# failed says WHY, and "absent from the archive" reads identically to "never
# happened". Truncated because a traceback string is not a metric.
STAGE_NOTES = ("results_chunk_revert_why", "delegate_fulltext_failed", "fulltext_candidates", "fulltext_upgraded", "fulltext_skipped", "fulltext_failed", "framing_failed", "correction_failed", "correction_skipped", "framing_reused", "trace_file", "merge_citations", "merge_grounded",
               "merge_coverage", "merge_mode", "merge_probe_failed",
               "failed_refs", "topup_refs", "topup_verify_failed",
               "topup_fulltext_skipped", "topup_fulltext_failed",
               "topup_disabled", "topup_failed", "topup_skipped", "merge_rejected",
               "merge_skipped", "merge_failed", "loop_backstop",
               "deterministic_fallback",
               # WHICH guard condition rejected a top-up (short / no_gain /
               # dropped) and which rejected a Results section. A stage that
               # only records THAT it failed cannot be aimed at: 3 of 5
               # archived top-up rejections recorded no reason at all.
               "topup_rejected_why", "results_rejected", "results_retried",
               "results_failed",
               # "significant" or "top_p_fallback". A run that quietly fell back
               # to the top fifteen would otherwise look like a run with only
               # fifteen significant pathways.
               "universe_source")


# Stats the two arms write that the archive deliberately does not keep. The
# point is the RATCHET, not the list: anything a stage starts writing from now
# on shows up in `unarchived_stats` until somebody either archives it or adds it
# here on purpose.
#
# The list itself became the hole it was meant to close. Audited at round 49 it
# held 40 entries, 16 of them for stats no arm still writes, and it was silencing
# 24 of the 82 stats agent_loop produces -- including the ENTIRE evidence-supply
# picture (fulltext_candidates / _upgraded / _skipped, quotes_supplied,
# quotes_reused, quotes_from_delegation, refs_rendered) and every failure signal
# in the gate (framing_failed, correction_failed, correction_skipped,
# verify_cut_short, verify_unchecked). Three consecutive rounds of work converged
# on "how much evidence did each writer actually have", and the numbers that
# answer it were being dropped on purpose, while `unarchived_stats` reported a
# clean archive. The merge stats had already been found in here for the same
# reason a round earlier.
#
# 76 of 82 are archived now. What remains is redundant rather than uninteresting,
# and each entry has to earn its place:
#
#   loop_final       the final report text; the .report.md file is the artifact
#   verification     the whole verification dict; its counts are archived flat
#   papers_retrieved already a top-level row key, written from a better source
#   tool_calls       duplicate of agent_tool_calls
#   total_s          duplicate of wall_s
#   topup_added_refs a list consumed inside the run by the gate-side pull-back
#
# This exists because round 36 was launched to measure sentence repair and could
# not: agent.py had written sentences_repaired / repairs_rejected /
# repair_unlocatable since the first commit, the bench captured none of them,
# and the archived row was silent. The stage ran; the measurement did not. The
# log could not stand in either -- it is configured at WARNING and the repair
# line is INFO.
# Twenty-one entries were pruned when the six-phase workflow arm was removed:
# they were stats only that arm wrote (partial-report salvage, cluster batch
# counts, draft scoring, gap-fill). The ratchet flags a stale entry as loudly
# as a missing one, on purpose -- a list of keys nobody writes protects
# nothing and teaches the next reader to ignore it.
KNOWN_UNARCHIVED = frozenset([
     'loop_final',
     'papers_retrieved',
     'tool_calls',
     'topup_added_refs',
     'total_s',
     'verification',])


def unarchived_stats(stats):
    """Stats this run produced that nothing will keep. Empty is the good case."""
    kept = (set(STAGE_TIMES) | set(STAGE_COUNTS) | set(STAGE_NOTES)
            | set(STAGE_MAPS) | KNOWN_UNARCHIVED)
    return sorted(k for k in (stats or {})
                  if not k.startswith("_") and k not in kept)


def _stage_budget(stats):
    """Timing and retry counters, kept flat so they land in the archived JSON."""
    out = {}
    for key in STAGE_TIMES + STAGE_COUNTS:
        value = stats.get(key)
        if isinstance(value, (int, float)):
            out[key] = round(value, 1) if key.endswith("_s") else value
    for key in STAGE_NOTES:
        value = stats.get(key)
        if value:
            out[key] = str(value)[:120]
    for key in STAGE_MAPS:
        value = stats.get(key)
        if isinstance(value, dict) and value:
            out[key] = {str(k): v for k, v in value.items()}
    return out


SALVAGE_MARK = "**Incomplete interpretation.**"
_SALVAGE_STAGE = re.compile(r"last completed stage:\s*([^)]+)\)")


def _salvage_stage(report_text):
    """The stage named in the salvage header, or None for a complete report."""
    head = (report_text or "")[:600]
    if SALVAGE_MARK not in head:
        return None
    found = _SALVAGE_STAGE.search(head)
    return found.group(1).strip() if found else "unknown"


def _measure(record, arm, job_id, wall, response=None):
    """Metrics for one run -- or an honest blank when the run did not finish.

    MongoDB keeps one interpretation per JOB. A run that errors never writes its
    own record, so everything report-derived read here belongs to whatever ran
    on this job LAST -- usually the other arm, minutes earlier. Round 30's
    errored agent replicate reported 17 citations, 19 redactions and 32 393
    characters: base-r1's numbers exactly, and briefly the best citation count
    this arm had ever "produced".

    Report-derived fields are therefore left None unless the record says done.
    wall_s and status are real either way, and rule 1 fails on both.
    """
    if record.get("status") != "done":
        return {
            "arm": arm, "jobID": job_id, "status": record.get("status"),
            "wall_s": round(wall, 1), "detail": record.get("detail"),
            "stale_record": True,
        }
    report = record.get("report") or ""
    papers = record.get("papers") or []
    stats = record.get("stats") or {}
    verification = record.get("verification") or {}

    body = report.split("### References")[0]
    prose = prose_of(report)
    known = {p.get("ref_index") for p in papers}
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", body)} & known

    pathway_index = record.get("pathwayIndex") or []
    covered = [pw.get("name") for pw in pathway_index
               if pw.get("name") and pw["name"].lower() in prose.lower()]

    # A salvaged partial report is not comparable to a finished one. It ships
    # BEFORE verification and redaction, so its citation count is inflated by
    # exactly the citations a complete run would have removed: round 33's
    # base-r2 timed out at "references rendered" and reported 30 citations with
    # 0 redactions, its best-looking numbers of the round, precisely because the
    # gate never ran. Counted as a run, flagged, and kept out of the means.
    stats = record.get("stats") or {}
    stage = stats.get("timed_out_at_stage")
    if not stage:
        # The salvage stamps its own header into the report. Read that too: the
        # stats dict is per-run and can be absent (an older metrics file, a
        # record re-read after the next run overwrote stats), while the header
        # travels with the text the user actually sees.
        stage = _salvage_stage(report)
    partial = bool(stage)
    row = {
        "arm": arm,
        "jobID": job_id,
        "status": record.get("status"),
        "partial_report": partial or None,
        "timed_out_at_stage": stage,
        "wall_s": round(wall, 1),
        "report_chars": len(report),
        "prose_chars": len(prose),
        # `papers` is the reference list that survived, not what was retrieved:
        # it is filtered to cited papers only when citations survive, so a
        # collapsed run reports MORE papers than a healthy one. Prefer the
        # run's own retrieval count and keep the reference count separately.
        "papers_retrieved": (stats.get("papers_retrieved")
                             or stats.get("papers") or len(papers)),
        "papers_in_references": len(papers),
        "citations_in_body": len(cited),
        "prose_citations": len({int(n) for n in re.findall(r"\[(\d+)\]", prose)}),
        "full_text_cited": sum(1 for p in papers
                               if p.get("ref_index") in cited
                               and p.get("full_text_available")),
        "redacted": verification.get("redacted_count", 0),
        "pathways_indexed": len(pathway_index),
        "prose_pathways_covered": len(covered),
        # Citations that say something about THIS experiment, not just about a
        # paper. See citation_grounding: 90-95% of citation sentences in the
        # archive are freestanding literature facts, and every existing citation
        # metric scores them as successes.
        "citation_sentences": citation_grounding(report)[0],
        "citations_linked_to_data": citation_grounding(report)[1],
        "citation_sentences_repeated": repeated_citation_sentences(report),
        # Ground truth: did the report reach the published paper's conclusions,
        # and did it narrate anything the paper says but this job cannot support?
        "rubric_coverage": rubric_score(report)[0],
        "rubric_fabricated": len(rubric_score(report)[1] or [])
        if rubric_score(report)[0] is not None else None,
        "prose_pathway_names": sorted(covered)[:40],
        "tool_calls": stats.get("tool_calls"),
        "forced_synthesis": stats.get("forced_synthesis", False),
        "detail": record.get("detail"),
    }
    # "redacted" counts SENTENCES removed plus reference entries, not distinct
    # citations -- and those two diverge hard by arm. agent-v33-r3 had ONE
    # citation fail verification and lost 15 sentences to it, because a stitched
    # report cites the same paper across many per-pathway sections; base loses
    # 2-3 for the same mistake. Both numbers are real and they answer different
    # questions, so both are recorded. The pre-registered rule keeps using
    # "redacted": it was written before the data and it measures what the reader
    # actually loses.
    failed = (record.get("verification") or {}).get("failed_citations")
    if isinstance(failed, list):
        row["failed_citations"] = len(failed)
        if failed and isinstance(row.get("redacted"), int):
            row["sentences_per_failed_citation"] = round(
                float(row["redacted"]) / len(failed), 1)
    checked = (record.get("verification") or {}).get("citations_checked")
    if isinstance(checked, int):
        row["citations_checked"] = checked
    row.update(_stage_budget(stats))
    return row


# -- score -----------------------------------------------------------------

METRICS = ("wall_s", "prose_chars", "report_chars", "citations_in_body",
           "full_text_cited", "redacted", "prose_pathways_covered",
           "papers_retrieved", "papers_in_references",
           # Rule 3 compares REDACTIONS, and the two arms fail differently:
           # base loses the sentence, this arm strips the marker and keeps it.
           # Measured over rounds 46-49, the agent arm has 8.0 citations fail
           # verification per run against base's 1.8, and reports 0.0 redacted
           # because the gate-side pull-back removes them from failed_citations
           # before it is counted. That is a better OUTCOME -- a stripped marker
           # keeps the finding, a redaction deletes it -- but it is damage
           # control, not grounding quality, and the redaction figure alone
           # reads as the second. These two columns put the failure rate beside
           # it in every score table, not only when a rule fails.
           "topup_added", "topup_added_failed",
           "citation_sentences", "citations_linked_to_data",
           "citation_sentences_repeated",
           "rubric_coverage", "rubric_fabricated")


REPORT_DERIVED = {"citations_in_body", "redacted", "prose_pathways_covered",
                  "citation_sentences", "citations_linked_to_data",
                  "citation_sentences_repeated",
                  "rubric_coverage", "rubric_fabricated",
                  "full_text_cited", "report_chars", "prose_chars",
                  "prose_citations", "papers_in_references"}


def _mean(rows, key):
    """Mean over the runs that HAVE the value; failed runs carry none.

    Report-derived quantities skip salvaged partial runs: they ship before
    verification, so their citation counts are inflated by whatever the gate
    would have removed. wall_s and status still count -- a partial run is a run,
    and it still fails rule 1.

    Reported alongside the replicate count so a mean over one surviving run of
    two cannot be read as a mean over two.
    """
    usable = [r for r in rows
              if not (r.get("partial_report") and key in REPORT_DERIVED)]
    values = [r[key] for r in usable if r.get(key) is not None]
    return statistics.mean(values) if values else None


def resolvable(agent_rows, base_rows, key, margin):
    """Is this round's margin bigger than the noise it was measured against?

    The five rules compare two means and print PASS or FAIL. Neither says whether
    the round had enough replicates to tell the difference from run-to-run
    spread, and measured over rounds 46-48 (11 replicates per arm) the spread is
    large:

        coverage    agent 14.6 +- 2.3   base 13.3 +- 1.9   gap +1.36  needs n~19
        citations   agent 21.5 +- 3.6   base 18.3 +- 3.9   gap +3.18  needs n~11
        redactions  agent  0.0 +- 0.0   base  5.1 +- 6.8   gap -5.09  needs n~7

    Base is FIXED code and still ranges 10-15 on coverage, 10-24 on citations and
    0-25 on redactions. Rounds are run at n=4, so rule 4 in particular is decided
    by noise -- rounds 47 and 48 ran effectively identical code and produced
    coverage 14.0 vs 13.0 (pass) and 12.7 vs 15.0 (fail).

    This does not touch a threshold. The rules stay exactly as pre-registered;
    this only says how much to believe the verdict, which was previously printed
    with no uncertainty at all.
    """
    a = [r.get(key) for r in agent_rows if isinstance(r.get(key), (int, float))]
    b = [r.get(key) for r in base_rows if isinstance(r.get(key), (int, float))]
    if len(a) < 2 or len(b) < 2:
        return None
    def _sd(v):
        m = sum(v) / len(v)
        return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
    se = ((_sd(a) ** 2) / len(a) + (_sd(b) ** 2) / len(b)) ** 0.5
    if se == 0:
        return ("resolved", se, 0)
    # Replicates per arm that would put the margin two standard errors clear.
    need = (2 * ((_sd(a) ** 2 + _sd(b) ** 2) / 2) ** 0.5 / abs(margin)) ** 2 * 2 \
        if margin else float("inf")
    return ("resolved" if abs(margin) >= 2 * se else "NOISE", se, need)


# How many citation sentences ALSO say something about this experiment -- a gene
# value, a p-value, a pathway id, a timepoint.
#
# Read the name carefully: this is NOT a quality score, and an earlier version of
# this comment called it one. `build_evidence_shelf_block` instructs the writers
# to keep the two kinds of sentence apart -- "What YOUR DATA shows... No citation
# belongs on these" and "What the LITERATURE says... every one of these needs a
# passage standing behind it" -- for a stated reason: a claim written first and
# supported afterwards is the one that fails verification. So a low number here
# is COMPLIANCE with the design, not a defect, and I reported it as a defect
# before reading the prompt that produces it.
#
# It is still worth measuring, because it prices a real trade-off nothing else
# can see. Separating the sentences makes every citation verifiable and leaves
# the reader to connect the data to the literature themselves; joining them --
# "Ccr2 falls 7.7-fold, consistent with the loss of chemokine responsiveness in
# [2]" -- is what a scientist means by grounded, and is exactly the shape the
# instruction forbids. Which side of that trade is right is a judgement about the
# product, and this number is the evidence for having the argument.
_EXPERIMENT_REF = re.compile(
    r"\b(?:mmu\d{5}|R-[A-Z]{3}-\d+|p\s*=|peak|[-+]?\d+\.\d+|\d+\s*h)\b")


def citation_grounding(report_text):
    """(citation sentences, how many make a claim about THIS experiment).

    Every citation metric in this file counts markers and asks whether a quote
    supports the sentence carrying them. Both arms pass that test and neither is
    grounded in the sense the brief means. Read across 21 archived reports, 90 to
    95% of citation-bearing sentences are statements ABOUT A PAPER placed beside
    the data rather than claims about the data supported by a paper:

        "Integrin beta3 acts as a threshold regulator of B cell activation [1],
         reframing beta3 as a threshold regulator of B-cell activation."
        "NOB1 is a ribosome assembly factor that plays a crucial role in the
         maturation of the 40S ribosomal small subunit [9]."
        "BCL6 is required for efficient CNS entry of encephalitogenic T cells in
         EAE models [1], and while that study is in T cells, it demonstrates..."

    None of those says anything about the experiment, and all of them verify --
    which is exactly why they survive. A sentence that restates its own source is
    trivially supported by it, so the verification gate cannot see the problem
    and `redacted` reads 0. The measurement rewarded exactly the failure it was
    meant to catch.

    Deliberately crude: it asks whether the sentence mentions the experiment at
    all, not whether the inference is sound. A model could satisfy it by
    appending a gene name. It is a floor, not a judgement, and it is one no
    current report clears.
    """
    body = (report_text or "").split("## References")[0]
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", body)
                 if re.search(r"\[\d+\]", x)]
    grounded = [x for x in sentences if _EXPERIMENT_REF.search(x)]
    return len(sentences), len(grounded)


def repeated_citation_sentences(report_text):
    """Citation sentences the report prints more than once.

    Base averages 4.2 per report and the agent arm 1.4. A sentence repeated
    verbatim in three pathway sections is padding that every length and citation
    count rewards.
    """
    body = (report_text or "").split("## References")[0]
    seen = {}
    for x in re.split(r"(?<=[.!?])\s+", body):
        if not re.search(r"\[\d+\]", x):
            continue
        key = re.sub(r"\[\d+\]", "", x).strip().lower()[:110]
        seen[key] = seen.get(key, 0) + 1
    return sum(v - 1 for v in seen.values() if v > 1)


# The one scorer here that knows what the right answer IS.
#
# The five rules compare this arm to the incumbent on counts. None of them asks
# whether the report reached the paper's conclusions, and no number of replicates
# fixes that -- which is why the arm read as "nominally ahead, not resolved" for a
# whole session while a ground-truth scorer put it 44% ahead and resolved.
#
# The rubric is AgentEvolve's, sealed and hashed, derived from the published
# PaintOmics 4 Results section (PMC9252773) for this exact STATegra job. It is
# NOT copied here as a fork: `stategra_rubric.json` carries the sha256 of the
# original, this module verifies it against the sibling repo when that repo is
# present, and a changed rubric is reported rather than silently scored against.
#
# Deliberately NOT a sixth rule. The five are pre-registered and a rule added
# after seeing the numbers is not a rule. This is reported beside them, and the
# honest reading is that it is the better measure and the five are the weaker one.
_RUBRIC_SHA = "599d5817c955230100829eed371e3bafde13195d4c382ffd10eeeadc9e10664c"
_RUBRIC_SRC = os.path.expanduser(
    "~/Desktop/github_dev/agentevolve/evaluators/stategra-v4/rubric.yaml")
_SCORER_DIR = os.path.expanduser("~/Desktop/github_dev/agentevolve")


def _rubric():
    """The sealed rubric, and whether it still matches the original."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "stategra_rubric.json")
    try:
        blob = json.load(open(path))
    except Exception:
        return None, "no local copy"
    if os.path.exists(_RUBRIC_SRC):
        live = hashlib.sha256(open(_RUBRIC_SRC, "rb").read()).hexdigest()
        if live != blob.get("_sha256"):
            return None, "rubric.yaml changed upstream (%s...)" % live[:12]
    if blob.get("_sha256") != _RUBRIC_SHA:
        return None, "local copy is not the sealed rubric"
    return blob["rubric"], None


def rubric_score(report_text):
    """(coverage, fabricated_items) or (None, reason).

    Best-effort: a scorer that cannot run must never take a round down with it,
    and the five rules stand on their own.
    """
    rubric, why = _rubric()
    if rubric is None:
        return None, why
    try:
        if _SCORER_DIR not in sys.path:
            sys.path.insert(0, _SCORER_DIR)
        from score import score_rubric          # pure stdlib, no yaml needed
        result = score_rubric.score(report_text or "", rubric)
        data = result if isinstance(result, dict) else vars(result)
        return data.get("coverage"), list(data.get("fabricated") or [])
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def judge(agent_rows, base_rows):
    """The five pre-registered rules. Every one must hold.

    Written before any arm ran, and deliberately not touched since: a rule
    edited after seeing the numbers is not a rule.
    """
    citations_a = _mean(agent_rows, "citations_in_body") or 0
    citations_b = _mean(base_rows, "citations_in_body") or 0
    redacted_a = _mean(agent_rows, "redacted") or 0
    redacted_b = _mean(base_rows, "redacted") or 0
    coverage_a = _mean(agent_rows, "prose_pathways_covered") or 0
    coverage_b = _mean(base_rows, "prose_pathways_covered") or 0
    chars_a = _mean(agent_rows, "report_chars") or 0
    chars_b = _mean(base_rows, "report_chars") or 0

    rules = [
        ("1 every replicate done within %ds" % CEILING_SECONDS,
         bool(agent_rows) and all(r.get("status") == "done"
                                  and (r.get("wall_s") or 0) <= CEILING_SECONDS
                                  for r in agent_rows),
         "%d replicate(s)" % len(agent_rows)),
        ("2 citations >= base",
         citations_a >= citations_b, "%.1f vs %.1f" % (citations_a, citations_b)),
        ("3 redactions <= base + 2",
         redacted_a <= redacted_b + 2, "%.1f vs %.1f" % (redacted_a, redacted_b)),
        ("4 prose coverage >= base",
         coverage_a >= coverage_b, "%.1f vs %.1f" % (coverage_a, coverage_b)),
        ("5 length within [0.6x, 2.0x] of base",
         bool(chars_b) and 0.6 * chars_b <= chars_a <= 2.0 * chars_b,
         "%.0f vs base %.0f" % (chars_a, chars_b)),
    ]
    # Annotate each comparative rule with whether this round could tell the
    # difference from noise. Thresholds untouched; only the confidence is new.
    margins = {"2 citations >= base": ("citations_in_body", citations_a - citations_b),
               "3 redactions <= base + 2": ("redacted", (redacted_b + 2) - redacted_a),
               "4 prose coverage >= base": ("prose_pathways_covered",
                                            coverage_a - coverage_b)}
    annotated = []
    for label, passed, detail in rules:
        info = margins.get(label)
        if info:
            verdict = resolvable(agent_rows, base_rows, info[0], info[1])
            if verdict:
                state, se, need = verdict
                detail = "%s [margin %+.1f, se %.1f -> %s%s]" % (
                    detail, info[1], se, state,
                    "" if state == "resolved" or need == float("inf")
                    else ", needs n~%.0f/arm" % need)
        annotated.append((label, passed, detail))
    return annotated, all(passed for _, passed, _ in annotated)


# Diagnostics printed beside every failing rule, chosen because each one has
# already explained a failure that cost rounds to chase by other means.
RULE_DIAGNOSTICS = {
    "2 citations": ("papers_retrieved", "papers_screened_out", "topup_added",
                    "tags_with_a_cited_paper", "tags_searched"),
    "3 redactions": ("failed_citations", "topup_added", "topup_added_failed",
                     "sentences_dropped", "failed_refs"),
    "4 prose coverage": ("delegate_markers", "prose_chars", "sentences_dropped"),
    "1 every replicate": ("loop_s", "topup_s", "verify_loop_s", "gateway_retries"),
    "5 length": ("prose_chars", "report_chars"),
}


def _diagnose(label, rows, base_rows):
    """The columns that explain THIS rule, printed only when it fails.

    Every metric here was already being recorded when a failure it explains went
    unexplained. topup_added_failed equalled failed_citations in all twelve
    replicates of rounds 39-41 -- every failed citation came from the top-up --
    while rule 3 was chased through screening strictness, pool ceilings,
    delegation attribution and framing permissions. The number was archived and
    nobody read it.

    A benchmark that stores a diagnostic and never surfaces it has the same
    failure mode as one that never stored it, and costs more to build.
    """
    keys = next((v for k, v in RULE_DIAGNOSTICS.items() if label.startswith(k)), ())
    out = []
    for key in keys:
        vals = [r.get(key) for r in rows if r.get(key) is not None]
        if not vals:
            continue
        if all(isinstance(v, (int, float)) for v in vals):
            mine = sum(vals) / len(vals)
            theirs = [r.get(key) for r in base_rows
                      if isinstance(r.get(key), (int, float))]
            cmp = (" vs %.1f" % (sum(theirs) / len(theirs))) if theirs else ""
            out.append("%s %.1f%s" % (key, mine, cmp))
        else:
            out.append("%s %s" % (key, vals[0]))
    return out


def cmd_power(args):
    """How big must an effect be for this many replicates to see it?

    Every pre-registration in this document names a predicted effect. None of
    them named the sample needed to detect it, and the consequences ran through
    the whole session: "converted themes 9.8 -> 13" was checked at n=4 against a
    spread that needs far more; "citations +4.6, resolved" at n=8 became +2.0 and
    unresolved at n=19; "chunk=3 cost 3.4 pathways, resolved" became +2.0 and
    unresolved once a third round landed.

    The variances are not a mystery -- they are in the archive, and base alone,
    which is FIXED code, ranges 10-15 on coverage and 10-26 on citations. So a
    round can be told in advance what it is capable of resolving, which is the
    difference between a pre-registration and a wish.

        ai_arm_bench power out/round46,out/round47,... --n 4
    """
    rows = []
    for d in args.rounds.split(","):
        for arm in ("agent", "base"):
            for path in sorted(glob.glob(os.path.join(d.strip(), "%s*.json" % arm))):
                try:
                    rows.append((arm, json.load(open(path))))
                except ValueError:
                    continue
    if not rows:
        print("no rows in %s" % args.rounds)
        return 1
    print("variance from %d archived replicates; a round of n=%d per arm\n"
          % (len(rows), args.n))
    print("%-26s %8s %10s %14s" % ("metric", "sd", "detectable", "n for +1.0"))
    for key in ("citations_in_body", "prose_pathways_covered", "redacted",
                "report_chars", "wall_s"):
        vals = [r.get(key) for _a, r in rows if isinstance(r.get(key), (int, float))]
        if len(vals) < 4:
            continue
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        # Resolvable here means |margin| >= 2 * se(difference), matching judge().
        detectable = 2 * sd * (2.0 / args.n) ** 0.5
        need_one = (2 * sd * (2.0 ** 0.5) / 1.0) ** 2
        print("%-26s %8.1f %10.1f %14.0f"
              % (key, sd, detectable, need_one))
    print("\nA round of n=%d resolves only effects at least as large as the"
          % args.n)
    print("'detectable' column. Anything smaller will read as NOISE however")
    print("many rounds are run, unless they are POOLED -- which is what")
    print("`compare` is for.")
    return 0


def cmd_compare(args):
    """Pool several round directories and compare two configurations.

    Written because I have hand-rolled this analysis six times in a session and
    the errors kept entering there, not in the pipeline: a join on file mtime
    that matched a run 28 hours old, a `sorted(keys)[:40]` slice that hid the
    stat I was looking for, and twice a mean taken across configurations that no
    longer applied. Each produced a confident wrong answer.

    It also exists because single rounds do not resolve these metrics. Two claims
    made from n=4 and n=8 this session -- "citations +4.6, resolved" and
    "DELEGATE_CHUNK=3 cost 3.4 pathways, resolved" -- both shrank and lost
    resolution when more replicates arrived. Pooling by CONFIGURATION rather than
    by round is the operation that answers the question, so it should be a
    command rather than a heredoc.

        ai_arm_bench compare out/round46,out/round50 out/round47,out/round48
    """
    def load(spec, arm):
        rows = []
        for d in spec.split(","):
            for path in sorted(glob.glob(os.path.join(d.strip(), "%s*.json" % arm))):
                try:
                    rows.append(json.load(open(path)))
                except ValueError:
                    continue
        return rows

    a_ag, a_bs = load(args.a, "agent"), load(args.a, "base")
    b_ag, b_bs = load(args.b, "agent"), load(args.b, "base")
    if not a_ag or not b_ag:
        print("no agent rows in one of the sets")
        return 1
    print("A: %s  (agent n=%d, base n=%d)" % (args.a, len(a_ag), len(a_bs)))
    print("B: %s  (agent n=%d, base n=%d)\n" % (args.b, len(b_ag), len(b_bs)))
    print("%-26s %10s %10s %26s" % ("metric", "A", "B", "A - B"))
    for key in ("prose_pathways_covered", "citations_in_body", "redacted",
                "report_chars", "wall_s", "topup_added", "topup_added_failed"):
        ma, mb = _mean(a_ag, key), _mean(b_ag, key)
        if ma is None or mb is None:
            continue
        verdict = resolvable(a_ag, b_ag, key, ma - mb)
        note = ""
        if verdict:
            state, se, need = verdict
            note = "%+9.2f [se %.2f -> %s%s]" % (
                ma - mb, se, state,
                "" if state == "resolved" or need == float("inf")
                else ", n~%.0f" % need)
        print("%-26s %10.1f %10.1f %26s" % (key, ma, mb, note))
    # The number the rules actually compare is the margin over the base run
    # PAIRED with each configuration, not the raw agent value: base drifts.
    print("\nmargin over each set's own base:")
    for key in ("prose_pathways_covered", "citations_in_body"):
        for label, ag, bs in (("A", a_ag, a_bs), ("B", b_ag, b_bs)):
            m = (_mean(ag, key) or 0) - (_mean(bs, key) or 0)
            print("   %-24s %s %+6.2f" % (key, label, m))
    return 0


def cmd_score(args):
    runs = []
    for path in sorted(glob.glob(os.path.join(args.outdir, "*.json"))):
        try:
            runs.append((os.path.basename(path)[:-5], json.load(open(path))))
        except ValueError:
            print("skipped unreadable %s" % path)
    if not runs:
        print("no run metrics in %s" % args.outdir)
        return 1

    groups = {}
    for name, row in runs:
        # The arm is recorded in the row; the label prefix separates variants
        # of the same arm (agent-v20-r1 -> agent-v20).
        # "mode" is what the pre-repo scripts wrote; rounds 1-24 are readable.
        key = row.get("arm") or row.get("mode") or "unknown"
        if key != "base":
            key = name.rsplit("-r", 1)[0] if "-r" in name else name
        groups.setdefault(key, []).append(row)

    order = ["base"] + sorted(k for k in groups if k != "base")
    # Label every cell with its arm, not just the header row. Reading a bare
    # column of numbers against a header printed twenty lines earlier is how I
    # published two inverted claims off this table -- "the agent repeats more
    # citation sentences" and "the agent links more citations to data" were both
    # base's numbers. A header is not enough when the reader is scrolling.
    print("%-26s %s" % ("", "".join("%18s" % k[:18] for k in order)))
    for metric in METRICS:
        cells = "".join(
            "%18s" % ("%s=n/a" % k[:9] if _mean(groups[k], metric) is None
                      else "%s=%.1f" % (k[:9], _mean(groups[k], metric)))
            for k in order)
        print("%-26s %s" % (metric, cells))
    print("%-26s %s" % ("replicates",
                        "".join("%12d" % len(groups[k]) for k in order)))
    print("%-26s %s" % ("of which partial",
                        "".join("%12d" % sum(1 for r in groups[k]
                                             if r.get("partial_report"))
                                for k in order)))
    print("%-26s %s" % ("of which measurable",
                        "".join("%12d" % sum(1 for r in groups[k]
                                             if not r.get("stale_record"))
                                for k in order)))

    base = groups.get("base") or []
    if not base:
        print("\nno base replicates: nothing to judge against")
        return 1
    for key in order:
        if key == "base":
            continue
        rules, verdict = judge(groups[key], base)
        print("\n%s:" % key)
        for label, passed, detail in rules:
            print("  %-38s %-5s (%s)" % (label, "PASS" if passed else "FAIL", detail))
            if not passed:
                for line in _diagnose(label, groups[key], base):
                    print("       %s" % line)
        print("  => %s" % ("BETTER than base"
                           if verdict else "NOT better - the incumbent stands"))
    return 0


# -- jobs ------------------------------------------------------------------

def cmd_jobs(args):
    """Create fresh STATegra jobs (step 1 + step 2) to measure against.

    The protocol needs jobs that exist before an arm runs, and this was the one
    piece that never made it into the repository -- so the documented round
    could not actually be started from a clean checkout. Requires the server to
    be running locally.
    """
    from src.benchmarks.bench_http import Client, _selectedCompounds
    client = Client(args.server, verify=False)
    ids = []
    for i in range(args.count):
        started = time.time()
        step1 = client.post("/pa_step1/example/stategra-multiomics")
        job_id = step1["jobID"]
        first = client.waitForJob(job_id, "step1")
        selected = _selectedCompounds(first.get("matchedMetabolites", []))
        form = [("jobID", job_id)] + [("selectedCompounds[]", c) for c in selected]
        client.post("/pa_step2", data=form)
        second = client.waitForJob(job_id, "step2")
        print("job %d/%d: %s  (%d pathways, %.0f s)"
              % (i + 1, args.count, job_id, len(second.get("pathwaysInfo") or {}),
                 time.time() - started), flush=True)
        ids.append(job_id)
    print(json.dumps(ids))
    return 0


# -- round -----------------------------------------------------------------

def cmd_round(args):
    """One complete round: gateway check, interleaved replicates, verdict.

    Interleaved base/agent/base/agent on purpose. The gateway's throughput
    varies over tens of minutes, and running one arm's replicates back to back
    lets that weather land entirely on one side of the comparison.
    """
    if cmd_ready(args) != 0:
        print("round not started: the gateway is not answering. "
              "A ten-minute replicate against a 504 is an outage report, "
              "not a measurement.")
        return 2

    jobs = args.jobs.split(",")
    plan = []
    # `--arms agent` skips the control. It is the right shape for an
    # agent-versus-agent question, which is most of them now that the sealed
    # rubric gives an ABSOLUTE score: comparing two agent configurations needs
    # neither arm of base, and base in cluster mode costs 486-1014 s a replicate
    # -- two thirds of a round's wall clock spent re-measuring a control whose
    # numbers are already in hand. Use `compare` across the two round
    # directories afterwards.
    #
    # It is NOT the shape for a rule verdict: rules 2, 3, 4 and 5 are all
    # relative to base, and cmd_score will report them against whatever base
    # rows happen to be in the directory. A round run with --arms agent should be
    # read on rubric_coverage and the agent columns, not on the five rules.
    arms = [a.strip() for a in (getattr(args, "arms", None) or "base,agent").split(",")]
    for replicate, job in enumerate(jobs, start=1):
        if "base" in arms:
            plan.append((job, "base", "base-r%d" % replicate))
        if "agent" in arms:
            plan.append((job, "agent", "%s-r%d" % (args.label, replicate)))

    for job, arm, label in plan:
        print("\n=== %s  job=%s  %s" % (label, job, time.strftime("%H:%M:%S")),
              flush=True)
        run_args = argparse.Namespace(jobID=job, arm=arm, outdir=args.outdir,
                                      label=label, design=args.design)
        try:
            cmd_run(run_args)
        except Exception as exc:                  # one bad replicate is data
            print("%s FAILED: %s: %s" % (label, type(exc).__name__, exc))
    print("\n=== verdict ===")
    return cmd_score(argparse.Namespace(outdir=args.outdir))


def pin_both_arms():
    """Import BOTH arms before a round starts, so their code cannot mix vintages.

    agent.py imports agent_loop LAZILY, inside run_ai_agent, only when
    AI_FULL_AGENT=1. A round therefore loads the shipped arm at launch and the
    agent arm minutes later -- and an edit in between leaves the process holding
    a NEW agent_loop against the ALREADY-LOADED old verification module.

    Measured, round 37: verification.py gained quote_provenance after launch, and
    both agent replicates died with "cannot import name 'quote_provenance'" at
    wall 0 while the base replicates ran perfectly. The rule I had been relying
    on all session -- "the bench runs in-process, so a mid-round edit cannot
    contaminate a running round" -- is true only for modules already imported.

    Importing both here makes the round's code a snapshot taken at launch, which
    is what the config fingerprint has always claimed it was.
    """
    import src.classes.AIInterpret.agent            # noqa: F401 -- imported for the side effect: snapshots the module at launch (see docstring)
    import src.classes.AIInterpret.agent_loop       # noqa: F401 -- imported for the side effect: snapshots the module at launch (see docstring)


def enable_stage_logging():
    """Let the pipeline's own INFO diagnostics reach the round's log.

    They were suppressed, and it cost a round. The per-iteration verify line
    ("VERIFY iter 2: 25 checked, 5 failed") and the sentence-repair line ("8
    fixed, 3 rejected, 0 unplaceable") are both logger.info, the benchmark ran
    at WARNING, and the log contained ZERO INFO lines -- so when the archived
    stats were also missing, there was no second channel to fall back on and
    round 36 had to be stopped and relaunched.

    Scoped to the pipeline's own loggers: the SDK and urllib3 at INFO would bury
    them.
    """
    import logging
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s:%(name)s:%(message)s")
    for name in ("src.classes.AIInterpret.agent",
                 "src.classes.AIInterpret.agent_loop",
                 "src.classes.AIInterpret.verification",
                 "src.classes.AIInterpret.clusters",
                 "src.benchmarks.ai_arm_bench"):
        logging.getLogger(name).setLevel(logging.INFO)


def main(argv=None):
    enable_stage_logging()
    pin_both_arms()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ready", help="probe the gateway; exit 0 if it answers")

    run = sub.add_parser("run", help="run one arm on one job")
    run.add_argument("jobID")
    run.add_argument("arm", choices=["base", "agent"])
    run.add_argument("outdir")
    run.add_argument("--label", default=None,
                     help="file stem, e.g. agent-v25-r1 (groups by prefix)")
    run.add_argument("--design", default=STATEGRA_DESIGN)

    score = sub.add_parser("score", help="apply the pre-registered rules")
    score.add_argument("outdir")

    pw = sub.add_parser("power", help="what effect size can a round of n resolve?")
    pw.add_argument("rounds", help="comma-separated round dirs to take variance from")
    pw.add_argument("--n", type=int, default=4, help="replicates per arm per round")

    cmp_ = sub.add_parser("compare", help="pool round dirs and compare two configs")
    cmp_.add_argument("a", help="comma-separated round dirs for configuration A")
    cmp_.add_argument("b", help="comma-separated round dirs for configuration B")

    jobs = sub.add_parser("jobs", help="create fresh STATegra jobs to measure on")
    jobs.add_argument("count", type=int, nargs="?", default=2)
    jobs.add_argument("--server", default="http://localhost:8000")

    rnd = sub.add_parser("round", help="gateway check, interleaved replicates, verdict")
    rnd.add_argument("jobs", help="comma-separated jobIDs, one per replicate pair")
    rnd.add_argument("outdir")
    rnd.add_argument("--label", default="agent",
                     help="agent-arm label, e.g. agent-v25")
    rnd.add_argument("--design", default=STATEGRA_DESIGN)
    rnd.add_argument("--arms", default="base,agent",
                     help="which arms to run; 'agent' alone skips the control "
                          "for an agent-vs-agent question (read the rubric, not "
                          "the five rules)")

    args = parser.parse_args(argv)
    return {"ready": cmd_ready, "run": cmd_run, "score": cmd_score,
            "compare": cmd_compare, "power": cmd_power, "jobs": cmd_jobs,
            "round": cmd_round}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
