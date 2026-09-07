#!/usr/bin/env python3
"""A delegation already run must not be run again.

delegate_interpretation is the agent's most expensive tool by an order of
magnitude -- a median 29.8 s per call against 0-4 ms for the data tools. Across
the 60 archived runs, 7 re-issued an identical delegation, costing 25-62 s each
(mean 40) out of a 600 s budget, for an answer the run was already holding.
Repeated delegations are 269 of the 271 seconds this agent spends re-answering
itself; every other tool is cheap enough that a repeat does not matter.

The cache is keyed on the RESOLVED pathway set rather than the argument
spelling, so the same pathways requested under a different name still hit, while
a different focus is a different question and runs.

    python -m src.tests.test_delegation_cache
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# `_trace` archives to CLIENT_TMP_DIR/ai_traces, the corpus every
# tool-usefulness figure here is computed from -- servlet runs included.
# Redirected BEFORE agent_loop is imported: serverconf is read at import time.
import tempfile as _tempfile
from src.conf import serverconf as _serverconf
_serverconf.CLIENT_TMP_DIR = _tempfile.mkdtemp(prefix="tracetest-")

from src.tests.agent_tool_call import invokeTool                # noqa: E402
from src.classes.AIInterpret import agent_loop as L       # noqa: E402


def _isolate_trace_archive():
    """Keep test traces out of the real archive.

    _archive_trace writes every run to CLIENT_TMP/ai_traces, which is the corpus
    the tool-usage benchmark reads. Unit tests that drive real tools were writing
    there too -- five files, and six simulated faults that the analyzer then
    reported as a defect in get_experiment_overview.
    """
    import tempfile
    L._archive_trace = lambda ctx: None
    return tempfile.gettempdir()


_isolate_trace_archive()

_PASSED, _FAILED = [], []


def _context():
    c = L.LoopContext(job_instance=None, job_id="TEST0001",
                      organism_name="Mus musculus",
                      experiment_design="two conditions")
    # The shape build_batch_interpretation_prompt actually requires. Using a
    # thinner dict made every call fail with KeyError('source') inside the SDK,
    # which swallows tool exceptions into a plain string -- so the tests still
    # "passed" while exercising nothing.
    def _pw(pid, name):
        return {"id": pid, "name": name, "source": "KEGG",
                "combined_pvalue": 1e-4, "global_pvalue": 1e-3,
                "per_omic": {"Gene expression": 1e-4},
                "matched_gene_count": 2,
                "top_genes": [{"symbol": "Ikzf1", "relevant": True,
                               "effect_size": 2.4, "omic_profiles": []}],
                "top_metabolites": [], "genes": [], "metabolites": []}

    c.pathways = [_pw("mmu04110", "Cell cycle"),
                  _pw("mmu04151", "PI3K-Akt signaling"),
                  _pw("mmu00010", "Glycolysis")]
    c.started_at = time.time()
    c.hard_deadline = time.time() + 600
    return c


def _paper(ref):
    """The shape build_batch_interpretation_prompt actually reads."""
    return {"ref_index": ref, "pmid": str(10000 + ref), "title": "Paper %d" % ref,
            "first_author": "Author", "journal": "Journal", "year": "2024",
            "abstract": "An abstract for paper %d." % ref, "pathways": [],
            "sections": {"abstract": "An abstract for paper %d." % ref}}



def _call(tool, ctx, **kwargs):
    """Invoke a @function_tool the way the SDK does.

    The SDK catches any exception a tool raises and hands the model the string
    "An error occurred while running the tool", so a broken fixture looks like a
    working one. Fail loudly instead.
    """
    out = invokeTool(tool, ctx, **kwargs)
    assert "An error occurred while running the tool" not in str(out), (
        "the tool raised and the SDK swallowed it: %s" % str(out)[:200])
    return out


def _stub_single_shot(counter):
    async def _fake(agent, prompt, c, max_turns, label):
        counter.append(label)
        return "Interpretation of %s [1]." % label
    return _fake


def _with_stub(fn):
    """Run fn with the delegated model call replaced by a counter."""
    calls = []
    original = L._single_shot
    L._single_shot = _stub_single_shot(calls)
    try:
        return fn(calls)
    finally:
        L._single_shot = original


def test_an_identical_delegation_is_not_run_twice():
    def body(calls):
        c = _context()
        first = _call(L.delegate_interpretation, c,
                      pathway_names=["Cell cycle", "PI3K-Akt signaling"], focus="")
        second = _call(L.delegate_interpretation, c,
                       pathway_names=["Cell cycle", "PI3K-Akt signaling"], focus="")
        assert len(calls) == 1, (
            "the sub-agents ran %d times for one distinct delegation" % len(calls))
        assert "already delegated" in second, "the repeat was not told it is a repeat"
        assert "Interpretation of" in second, "the cached analysis was not returned"
        assert "Interpretation of" in first
    _with_stub(body)


def test_a_cache_hit_does_not_duplicate_the_delegated_text():
    """The gate stitches from c.delegated; a duplicate would pad the merge
    toward STITCH_MAX_CHARS and let one claim be counted twice."""
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        before = len(c.delegated)
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert len(c.delegated) == before, (
            "cache hit appended the same interpretation again (%d -> %d)"
            % (before, len(c.delegated)))
    _with_stub(body)


def test_the_same_pathways_under_another_name_still_hit():
    """The key is the resolved pathway set, not the spelling the Lead used."""
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        out = _call(L.delegate_interpretation, c, pathway_names=["cell CYCLE"], focus="")
        assert len(calls) == 1, "a spelling variant re-ran the sub-agents"
        assert "already delegated" in out
    _with_stub(body)


def test_a_different_focus_is_a_different_question():
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"],
              focus="metabolic angle")
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"],
              focus="regulatory angle")
        assert len(calls) == 2, "a genuinely different focus was served from cache"
    _with_stub(body)


def test_a_different_pathway_set_is_not_a_hit():
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        _call(L.delegate_interpretation, c, pathway_names=["Glycolysis"], focus="")
        assert len(calls) == 2, "a different pathway set was served from cache"
    _with_stub(body)


def test_the_cache_hit_is_visible_in_the_trace():
    """Otherwise the saving cannot be measured from the archive afterwards."""
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        hits = [e for e in c.trace if "cache" in str(e.get("result", ""))]
        assert hits, "no trace event records the cache hit: %r" % c.trace[-1:]
    _with_stub(body)


def test_a_cache_hit_is_fast():
    """The whole point is the 30 s it does not spend."""
    def body(calls):
        c = _context()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        t0 = time.time()
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert time.time() - t0 < 1.0, "a cache hit took over a second"
    _with_stub(body)


def _with_quotes(fn, quotes=None, boom=False, cites="[1]"):
    """Run fn with the sub-agents stubbed AND quote collection stubbed."""
    calls = []
    original_shot, original_quotes = L._single_shot, L._collect_cited_quotes

    async def _fake_shot(agent, prompt, c, max_turns, label):
        calls.append(label)
        return "Interpretation of %s %s." % (label, cites)
    L._single_shot = _fake_shot

    def _fake_quotes(llm, report, index, job, known=None):
        if boom:
            raise RuntimeError("quote service down")
        return dict(quotes or {})
    L._collect_cited_quotes = _fake_quotes
    try:
        return fn(calls)
    finally:
        L._single_shot, L._collect_cited_quotes = original_shot, original_quotes


def test_delegation_grounds_its_own_citations():
    """Round 25 r2: the agent's 7 citations were all grounded, the gate then
    merged in delegated text carrying citations nobody had checked, could not
    quote them in the time left, and shipped a 64 830-character report with
    zero citations. The quotes are collected here, where the papers are."""
    def body(calls):
        c = _context()
        c.paper_index = {1: _paper(1), 2: _paper(2)}
        _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert c.quotes, "delegation grounded nothing and cached nothing"
        assert 1 in c.quotes, "the grounded citation is not on the context"
    _with_quotes(body, quotes={1: "a verbatim sentence"})


def test_the_lead_is_told_which_citations_cannot_be_grounded():
    """Information, not a veto: the Lead decides whether to read the paper,
    cite something else, or drop the claim."""
    def body(calls):
        c = _context()
        c.paper_index = {1: _paper(1), 2: _paper(2)}
        out = _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert "[grounding]" in out, "the Lead was not told: %s" % out[-200:]
        assert "[2]" in out.split("[grounding]")[1], "the ungrounded index is not named"
        assert "[1]" not in out.split("[grounding]")[1], "a grounded citation was flagged"
    _with_quotes(body, quotes={1: "a verbatim sentence"}, cites="[1] and [2]")


def test_grounding_failure_does_not_lose_the_delegation():
    """The analysis is worth more than its citation check; a quote service that
    falls over must not discard 27 000 characters of interpretation."""
    def body(calls):
        c = _context()
        c.paper_index = {1: _paper(1)}
        out = _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert "Interpretation of" in out, "the delegated text was lost: %s" % out[:120]
    _with_quotes(body, boom=True)


def test_the_gate_reuses_what_delegation_grounded():
    """Source-level: the gate must seed from ctx.quotes rather than re-derive
    them against a merged report and a shrinking clock."""
    import inspect
    src = inspect.getsource(L._run_loop_async)
    assert "dict(ctx.quotes)" in src, "the gate does not seed from the delegation's quotes"
    assert "known=quotes" in src, "the gate re-collects quotes it already has"



def test_grounding_sees_citations_written_as_PMIDs():
    """The bug round 26 measured instead of testing.

    The delegated sub-agents are told to write "(PMID: 12345)", not "[N]" --
    that prompt was reverted to the workflow arm's on evidence, and the markers
    are only converted later, inside the merge. The grounding step guarded on
    '[' being present, so it was never true: no quotes were collected, the gate
    found none either, references rendered without Cited Text, and the run
    shipped 0 citations with 54 redactions. The fix RAN in the sense that the
    code was loaded; it never executed a line that mattered.
    """
    def body(calls):
        c = _context()
        c.paper_index = {1: _paper(1)}          # pmid "10001"
        out = _call(L.delegate_interpretation, c, pathway_names=["Cell cycle"], focus="")
        assert "[1]" in out, (
            "PMID citations were not resolved, so grounding cannot see them: %r"
            % out[:160])
        assert c.quotes, "nothing was grounded even though the text cites a paper"
        assert any("delegate_grounding" == e.get("tool") for e in c.trace), (
            "the grounding step left no trace event, which is how the round-26 "
            "failure hid: %r" % [e.get("tool") for e in c.trace])
    _with_quotes(body, quotes={1: "a verbatim sentence"}, cites="(PMID: 10001)")



def test_the_evidence_shelf_hands_over_paper_text_not_tool_framing():
    """search_paper_text answers a human: "Found 3 passage(s) in paper [1]:
    --- Passage 1 --- ...". Passing that through as evidence invites the writer
    to quote the framing, and the gate then hunts for that sentence in the paper
    and does not find it."""
    raw = ("Found 3 passage(s) in paper [1]:\n\n--- Passage 1 ---\n"
           "Polyamine depletion arrests these cells in G1 and blocks entry to S phase.\n"
           "--- Passage 2 ---\nIkaros binds the Ccnd2 promoter during differentiation.")
    cleaned = L._clean_passage(raw)
    assert "Found" not in cleaned and "Passage 1" not in cleaned, (
        "tool framing survived into the evidence: %r" % cleaned[:90])
    assert "Polyamine depletion arrests" in cleaned
    assert "Ikaros binds" in cleaned, "only one passage was kept"


def test_a_shelf_entry_is_dropped_when_nothing_usable_is_found():
    assert L._clean_passage("Found 0 passage(s) in paper [1]:") == ""
    assert L._clean_passage("--- Passage 1 ---\nshort") == "", (
        "a fragment too short to be evidence was kept")



def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_an_identical_delegation_is_not_run_twice,
              test_a_cache_hit_does_not_duplicate_the_delegated_text,
              test_the_same_pathways_under_another_name_still_hit,
              test_a_different_focus_is_a_different_question,
              test_a_different_pathway_set_is_not_a_hit,
              test_the_cache_hit_is_visible_in_the_trace,
              test_a_cache_hit_is_fast,
              test_delegation_grounds_its_own_citations,
              test_the_lead_is_told_which_citations_cannot_be_grounded,
              test_grounding_failure_does_not_lose_the_delegation,
              test_the_gate_reuses_what_delegation_grounded,
              test_grounding_sees_citations_written_as_PMIDs,
              test_the_evidence_shelf_hands_over_paper_text_not_tool_framing,
              test_a_shelf_entry_is_dropped_when_nothing_usable_is_found):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
