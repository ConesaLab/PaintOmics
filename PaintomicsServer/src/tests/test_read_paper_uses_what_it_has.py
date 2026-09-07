#!/usr/bin/env python3
"""read_paper must not fetch a paper to return text it is already holding.

search_literature fetches every abstract into paper_index. Measured across the
archive, 342 of 378 read_paper calls (90%) then asked for exactly that
abstract -- and each one triggered a full-text upgrade first, ~2.6 s of NCBI
time, before returning the cached text. read_paper is 10.5% of the gateway
bill; nine calls in ten were paying for something they did not need.

    python -m src.tests.test_read_paper_uses_what_it_has
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# `_trace` archives every tool call to CLIENT_TMP_DIR/ai_traces, which is the
# LIVE corpus every tool-usefulness figure in this project is computed from --
# including runs that came through the servlet, not just the benchmark. A test
# that writes there puts fabricated tool calls into that dataset, and the next
# analysis counts them. Four suites were doing it.
import tempfile as _tempfile
from src.conf import serverconf as _serverconf
_serverconf.CLIENT_TMP_DIR = _tempfile.mkdtemp(prefix="tracetest-")

from src.tests.agent_tool_call import invokeTool
from src.classes.AIInterpret import agent_loop as L      # noqa: E402

_PASSED, _FAILED = [], []


class _PubMed:
    def __init__(self):
        self.fetches = []

    def fetch_papers(self, pmids):
        self.fetches.append(list(pmids))
        return [{"pmid": pmids[0], "ref_index": 1, "fetch_tier": "full",
                 "title": "T", "year": "2020",
                 "sections": {"abstract": "CACHED ABSTRACT",
                              "results": "FULL RESULTS TEXT"}}]


def _ctx(tier="abstract_only", abstract="CACHED ABSTRACT"):
    """A real LoopContext -- a hand-rolled stub silently loses fields the tool
    reads, and the tool's own except-clause turns that into a passing test."""
    import time
    c = L.LoopContext(job_instance=None, job_id="JOB", organism_name="mmu",
                      experiment_design="d")
    c.pubmed = _PubMed()
    c.hard_deadline = time.time() + 600
    c.paper_index = {1: {"pmid": "123", "ref_index": 1, "fetch_tier": tier,
                         "title": "T", "year": "2020",
                         "sections": {"abstract": abstract}}}

    return c


def _read(context, ref, section):
    return invokeTool(L.read_paper, context, ref_index=ref, section=section)


def test_an_abstract_is_served_without_a_fetch():
    c = _ctx()
    out = _read(c, 1, "abstract")
    assert c.pubmed.fetches == [], (
        "read_paper fetched full text to return an abstract it already had: %s"
        % c.pubmed.fetches)
    assert "CACHED ABSTRACT" in out, out[:200]


def test_any_other_section_still_upgrades():
    """The saving must not cost the agent the sections it actually needs."""
    c = _ctx()
    out = _read(c, 1, "results")
    assert c.pubmed.fetches == [["123"]], (
        "a results request no longer fetches full text: %s" % c.pubmed.fetches)
    assert "FULL RESULTS" in out, out[:200]


def test_an_empty_abstract_still_upgrades():
    """Abstract-only records with nothing in them must not become a dead end."""
    c = _ctx(abstract="   ")
    _read(c, 1, "abstract")
    assert c.pubmed.fetches == [["123"]], (
        "an empty cached abstract was served as if it were content")


def test_an_already_upgraded_paper_is_not_refetched():
    c = _ctx(tier="full")
    _read(c, 1, "results")
    assert c.pubmed.fetches == [], "re-fetched a paper already at full tier"


def test_the_description_tells_the_agent_which_call_is_free():
    """The agent decides what to spend on; it can only do that if the tool says
    what each section costs."""
    text = (getattr(L.read_paper, "description", "")
            or (L.read_paper.__doc__ or ""))
    assert "abstract" in text and "free" in text.lower(), (
        "read_paper does not tell the agent the abstract is free: %r" % text[:200])


def test_an_abstract_reread_says_it_bought_nothing():
    """92% of reads ask for the abstract already in the listing.

    Measured across the archive: 477 of 517 read_paper calls asked for the
    abstract, 32 for results, 7 for everything else -- roughly six of a run's ~39
    turns spent re-reading text the search had already shown. The tool still
    answers, because refusing would strand a plan mid-step, but it now says what
    would actually be new. Deeper sections carry 30% of surviving quotes (47 of
    157 over seven runs), so the nudge is toward the tier that earns its cost.
    """
    import time
    from src.classes.AIInterpret import agent_loop as L
    ctx = L.LoopContext(job_instance=None, job_id="T", organism_name="mmu",
                        experiment_design="", started_at=time.time(),
                        hard_deadline=time.time() + 600)
    ctx.paper_index = {1: {"ref_index": 1, "pmid": "1", "title": "t",
                           "abstract": "an abstract",
                           "sections": {"abstract": "an abstract",
                                        "results": "the results"},
                           "fetch_tier": "europepmc"}}
    out = invokeTool(L.read_paper, ctx, ref_index=1, section="abstract")
    assert "already in your search results" in out, out
    assert "results" in out, "it does not name the section that would be new"
    assert ctx.abstract_rereads == 1


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_an_abstract_is_served_without_a_fetch,
              test_any_other_section_still_upgrades,
              test_an_empty_abstract_still_upgrades,
              test_an_already_upgraded_paper_is_not_refetched,
              test_the_description_tells_the_agent_which_call_is_free,
              test_an_abstract_reread_says_it_bought_nothing):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
