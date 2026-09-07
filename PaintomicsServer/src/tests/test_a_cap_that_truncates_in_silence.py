#!/usr/bin/env python3
"""A tool that drops what it was asked for must say which, and in whose order.

Two caps in this arm truncated silently, and both did it in RANK order over the
whole universe rather than in the order the Lead asked:

  * get_pathway_details broke out of the loop at 8 blocks. Name twenty pathways
    and you got the eight best-ranked of them, with nothing in the answer about
    the other twelve. The agent could not ask again for what it did not know it
    was missing, and could not tell "the tool stopped" from "those pathways have
    no data".
  * delegate_interpretation sliced the resolved list at DELEGATE_MAX_PATHWAYS,
    same silence, same re-sorting of the request into p-value order first.

Neither cap had ever bound in production -- coverage was 15-18 against a cap of
60 -- which is exactly why they survived: an invisible limit that rarely fires
is indistinguishable from no limit until the day the agent finally asks for
more. The point of this arm is that the AGENT decides scope, and it cannot do
that against a tool that edits the request without a word.

    python -m src.tests.test_a_cap_that_truncates_in_silence
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests.agent_tool_call import invokeTool                # noqa: E402
from src.classes.AIInterpret import agent_loop as L           # noqa: E402

L._archive_trace = lambda ctx: None

_PASSED, _FAILED = [], []


def _pw(pid, name, p=1e-4):
    return {"id": pid, "name": name, "source": "KEGG",
            "combined_pvalue": p, "global_pvalue": p * 10,
            "per_omic": {"Gene expression": p}, "matched_gene_count": 2,
            "significant_omic_count": 2,
            "top_genes": [{"symbol": "Ikzf1", "relevant": True,
                           "effect_size": 2.4, "omic_profiles": []}],
            "top_compounds": [], "top_metabolites": [], "genes": [],
            "metabolites": []}


def _context(n=40):
    c = L.LoopContext(job_instance=None, job_id="TEST0002",
                      organism_name="Mus musculus",
                      experiment_design="two conditions")
    # Ranked best-p first, as the real universe is.
    c.pathways = [_pw("mmu%05d" % i, "Pathway %02d" % i, p=1e-6 * (i + 1))
                  for i in range(n)]
    c.started_at = time.time()
    c.hard_deadline = time.time() + 600
    return c


def _call(tool, ctx, **kwargs):
    out = invokeTool(tool, ctx, **kwargs)
    assert "An error occurred while running the tool" not in str(out), (
        "the tool raised and the SDK swallowed it: %s" % str(out)[:300])
    return out


# ------------------------------------------------------- get_pathway_details

def test_it_answers_in_the_order_asked_not_in_rank_order():
    """The Lead's ordering is the only place its judgement about THIS experiment
    is expressed. Sorting it away before answering discards it."""
    c = _context()
    asked = ["Pathway 30", "Pathway 02", "Pathway 17"]
    out = _call(L.get_pathway_details, c, pathway_names=asked)
    positions = [out.index("Pathway 30"), out.index("Pathway 02"),
                 out.index("Pathway 17")]
    assert positions == sorted(positions), (
        "answered in rank order, not the order asked: %s" % positions)


def test_what_did_not_fit_is_named_and_invited_back():
    c = _context()
    over = L.DETAIL_MAX_BLOCKS + 5
    asked = ["Pathway %02d" % i for i in range(over)]
    out = _call(L.get_pathway_details, c, pathway_names=asked)
    for name in asked[L.DETAIL_MAX_BLOCKS:]:
        assert name in out, "%r was dropped without a word" % name
    assert "STILL WAITING" in out, out[-600:]
    assert "unlimited in how often" in out, (
        "the agent is not told it may simply ask again")
    assert c.extra_stats.get("detail_deferred") == over - L.DETAIL_MAX_BLOCKS


def test_a_request_that_fits_carries_no_scope_note():
    """The notice must not become background noise on every call."""
    c = _context()
    out = _call(L.get_pathway_details, c,
                pathway_names=["Pathway 01", "Pathway 02"])
    assert "STILL WAITING" not in out, out[-400:]


def test_a_name_that_matches_nothing_is_reported_as_such():
    """Absent and unasked-for must not look alike."""
    c = _context()
    out = _call(L.get_pathway_details, c,
                pathway_names=["Pathway 01", "Ferroptosis"])
    assert "No enriched pathway matches: Ferroptosis" in out, out[-400:]
    assert "Pathway 01" in out, "a bad name must not cost the good ones"


def test_the_same_pathway_named_twice_is_not_billed_twice():
    c = _context()
    out = _call(L.get_pathway_details, c,
                pathway_names=["Pathway 03", "mmu00003", "Pathway 03"])
    assert out.count("### Pathway 03") == 1, out[:400]


# ------------------------------------------------------- delegation capacity

def test_capacity_comes_from_the_clock_not_a_constant():
    """Sixty pathways is affordable at minute two and impossible at minute
    eight, and the constant said sixty at both."""
    c = _context()
    per_wave = L.DELEGATE_CHUNK * L.DELEGATE_WORKERS

    c.hard_deadline = time.time() + 600
    roomy = L._delegation_capacity(c)
    c.hard_deadline = time.time() + L.WRITE_RESERVE_SECONDS + L.DELEGATE_QUOTE_SECONDS + 5
    tight = L._delegation_capacity(c)

    assert roomy > tight, (
        "capacity ignores the clock: %d with ten minutes, %d with none"
        % (roomy, tight))
    assert tight == per_wave, (
        "the floor is not one wave (%d), it is %d" % (per_wave, tight))
    assert roomy % per_wave == 0, "capacity should be whole waves, got %d" % roomy


def test_a_pinned_ceiling_still_wins_for_a_benchmark_round():
    c = _context()
    before = L.DELEGATE_MAX_PATHWAYS
    try:
        L.DELEGATE_MAX_PATHWAYS = 7
        assert L._delegation_capacity(c) == 7
    finally:
        L.DELEGATE_MAX_PATHWAYS = before


def test_the_floor_is_never_zero():
    """A delegation tool that can return nothing is worse than a slow one;
    _time_guard has already refused the call if there is truly no time."""
    c = _context()
    c.hard_deadline = time.time() - 1000
    assert L._delegation_capacity(c) >= L.DELEGATE_CHUNK * L.DELEGATE_WORKERS


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_it_answers_in_the_order_asked_not_in_rank_order,
              test_what_did_not_fit_is_named_and_invited_back,
              test_a_request_that_fits_carries_no_scope_note,
              test_a_name_that_matches_nothing_is_reported_as_such,
              test_the_same_pathway_named_twice_is_not_billed_twice,
              test_capacity_comes_from_the_clock_not_a_constant,
              test_a_pinned_ceiling_still_wins_for_a_benchmark_round,
              test_the_floor_is_never_zero):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
