#!/usr/bin/env python3
"""submit_report may ask once. Never twice, never a veto.

Two things the agent reliably already knows are wrong at submit time:

  * it never delegated, so the report cannot cover the experiment;
  * its own check_my_citations run found citations with no supporting quote,
    and they are still in the draft.

Both are worth one question. Neither is worth a refusal: a tool that can refuse
twice is a workflow step wearing a tool's clothes, and the whole point of this
arm is that the agent decides.

The citation nudge is measured, not guessed. Over the 28 archived runs that
called check_my_citations, the 10 that re-checked after a bad result improved
every time -- 11/6 -> 7/0, 14/7 -> 8/0, 10/4 -> 10/0 -- and none got worse. The
18 that checked once sometimes submitted with flagged citations still in place,
and those become redactions that delete their sentences.

    python -m src.tests.test_submit_nudges
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

L._archive_trace = lambda ctx: None        # keep tests out of the trace corpus

_PASSED, _FAILED = [], []

LONG = ("## Findings\n\n" + "The pathway responds coherently across omics. " * 40)


def _context():
    c = L.LoopContext(job_instance=None, job_id="TEST0003",
                      organism_name="Mus musculus",
                      experiment_design="two conditions")
    c.started_at = time.time()
    c.hard_deadline = time.time() + 600
    c.pathways = [{"id": "mmu04110", "name": "Cell cycle"}]
    return c


def _submit(c, text):
    out = invokeTool(L.submit_report, c, report_markdown=text)
    assert "An error occurred while running the tool" not in str(out), out
    return str(out)


def test_a_flagged_citation_still_in_the_draft_is_questioned_once():
    c = _context()
    c.delegated = ["something"]                 # silence the delegation nudge
    c.flagged_citations = {3, 7}
    out = _submit(c, LONG + " A claim [3].")
    assert "NOT SUBMITTED YET" in out, "the flagged citation went through unasked"
    assert "[3]" in out, "the nudge does not say which citation"
    assert not c.submitted_report, "the draft was stored despite the nudge"


def test_the_second_submit_is_always_accepted():
    """One question, then the agent's judgement stands."""
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3}
    _submit(c, LONG + " A claim [3].")
    out = _submit(c, LONG + " A claim [3].")
    assert "SUBMITTED" in out, "the second submit was refused: %s" % out[:120]
    assert c.submitted_report, "the report was not stored on the second attempt"


def test_a_fixed_draft_is_not_questioned():
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3, 7}
    out = _submit(c, LONG + " A claim [5].")     # the flagged ones are gone
    assert "SUBMITTED" in out, "a clean draft was nudged anyway: %s" % out[:120]


def test_only_one_nudge_can_fire_in_a_run():
    """The invariant that keeps this a tool and not a workflow gate: if the
    delegation nudge answers the first submit, the citation nudge cannot answer
    the second."""
    c = _context()
    c.delegated = []                             # delegation nudge applies
    c.flagged_citations = {3}                    # citation nudge would too
    first = _submit(c, "A draft with a claim [3] and prose. " * 100)
    assert "NOT SUBMITTED YET" in first
    assert "delegate" in first.lower(), "the delegation nudge should come first"
    second = _submit(c, "A draft with a claim [3] and prose. " * 100)
    assert "SUBMITTED" in second, (
        "a second nudge fired; submit_report can now refuse twice: %s" % second[:150])


def test_a_draft_that_is_not_a_report_is_still_rejected_as_such():
    """The short-draft rejection must not be shadowed by citation advice."""
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3}
    out = _submit(c, "Too short [3].")
    assert "not a report" in out, "got citation advice instead: %s" % out[:120]


def test_check_my_citations_records_what_it_flagged():
    """The nudge is only as good as the set the check leaves behind."""
    import src.classes.AIInterpret.agent_loop as M
    c = _context()
    # keyed by ref_index (int), as search_literature builds it
    c.paper_index = {1: {"ref_index": 1, "pmid": "1"},
                     2: {"ref_index": 2, "pmid": "2"}}
    original = M._collect_cited_quotes
    # signature must match the real one, including known= -- the tool swallows
    # a TypeError here and would report every citation as unquotable
    M._collect_cited_quotes = (lambda client, text, index, job, known=None:
                               {1: "a quote"})
    try:
        invokeTool(L.check_my_citations, c,
                   draft="Claim one [1]. Claim two [2].")
    finally:
        M._collect_cited_quotes = original
    assert c.flagged_citations == {2}, (
        "expected [2] flagged as unquotable, got %r" % c.flagged_citations)


def test_a_nudge_is_not_issued_when_the_clock_cannot_afford_it():
    """A nudge asks for a rewrite. Measured over the archived runs, the gap
    after a submit -- the agent rewriting a ten-thousand-character report -- runs
    a median 58 s and a mean 69 s, and the exit gate needs GATE_MIN_SECONDS
    after that. Asking with 90 s left buys a worse ending: a minute of rewriting
    and then a gate cut short, instead of a verified report."""
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3}
    c.hard_deadline = time.time() + 45          # not enough to rewrite
    out = _submit(c, LONG + " A claim [3].")
    assert "SUBMITTED" in out, (
        "nudged with 90 s left; the gate would be cut short: %s" % out[:120])


def test_the_delegation_nudge_checks_the_clock_too():
    c = _context()
    c.delegated = []
    c.flagged_citations = set()
    c.hard_deadline = time.time() + 90
    # over 500 chars so it is a report, under 9000 so the nudge would apply
    out = _submit(c, "An undelegated draft with real prose. " * 100)
    assert "SUBMITTED" in out, "the delegation nudge ignored the clock: %s" % out[:120]


def test_a_nudge_still_fires_with_time_in_hand():
    """The guard must not silently disable the nudge in a normal run: a nudge at
    the 400 s mark of a 600 s budget has time for both the rewrite and the gate."""
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3}
    c.hard_deadline = time.time() + 300
    out = _submit(c, LONG + " A claim [3].")
    assert "NOT SUBMITTED YET" in out, (
        "the clock guard swallowed a nudge that had time: %s" % out[:120])



def test_the_threshold_excludes_the_gate_reserve():
    """hard_deadline already has GATE_RESERVE_SECONDS taken off it.

    The first version of this guard added the gate reserve on top and required
    210 s, which silently disabled the nudge in the end-to-end test's 300 s
    budget: both submits were accepted and nothing complained until the e2e
    suite failed. 150 s left on the loop clock is a normal mid-run moment and
    must still nudge.
    """
    c = _context()
    c.delegated = ["something"]
    c.flagged_citations = {3}
    c.hard_deadline = time.time() + 150
    out = _submit(c, LONG + " A claim [3].")
    assert "NOT SUBMITTED YET" in out, (
        "150 s of loop clock did not nudge; the threshold is double-counting "
        "the gate reserve: %s" % out[:120])



def test_check_my_citations_checks_the_delegated_text_too():
    """The gate ships draft + merged delegated analyses. Checking the draft
    alone checks the wrong artifact: on a live run an agent submitted 11
    citations it had checked and grounded, and 6 survived, because the merge
    brought in citations this tool had never seen."""
    import src.classes.AIInterpret.agent_loop as M
    c = _context()
    c.paper_index = {1: {"ref_index": 1, "pmid": "1"}, 2: {"ref_index": 2, "pmid": "2"}}
    c.delegated = ["A delegated paragraph citing [2]."]
    seen = {}
    original = M._collect_cited_quotes

    def _fake(client, text, index, job, known=None):
        seen["text"] = text
        return {1: "a quote"}
    M._collect_cited_quotes = _fake
    try:
        out = invokeTool(L.check_my_citations, c, draft="My own claim [1].")
    finally:
        M._collect_cited_quotes = original
    assert "[2]" in seen.get("text", ""), (
        "the delegated text was not part of what was checked")
    assert "[2]" in str(out), "the ungrounded delegated citation was not reported"



def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_a_flagged_citation_still_in_the_draft_is_questioned_once,
              test_the_second_submit_is_always_accepted,
              test_a_fixed_draft_is_not_questioned,
              test_only_one_nudge_can_fire_in_a_run,
              test_a_draft_that_is_not_a_report_is_still_rejected_as_such,
              test_check_my_citations_records_what_it_flagged,
              test_a_nudge_is_not_issued_when_the_clock_cannot_afford_it,
              test_the_delegation_nudge_checks_the_clock_too,
              test_a_nudge_still_fires_with_time_in_hand,
              test_the_threshold_excludes_the_gate_reserve,
              test_check_my_citations_checks_the_delegated_text_too):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
