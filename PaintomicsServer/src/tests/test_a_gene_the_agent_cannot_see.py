#!/usr/bin/env python3
"""The agent was shown 9.5% of the genes and none of the ones that mattered.

Measured on the frozen STATegra job (102 significant pathways, the same job the
sealed AgentEvolve rubric was written against):

    matched genes in the universe          10583
    genes the agent was ever shown          1010   (9.5%)
    widest pathway: Pathways in cancer   460 -> 10

The cut is `_get_top_genes(..., limit=10)`, ordered by relevance then effect
size, so what survives is the loudest ten. The rubric names eleven genes:

    Srm Sms Amd1 Odc1 Dok1 Dok2 Dok3 Myc Igll1 Ikzf1   in the job, INVISIBLE
    Ret                                                 visible

Ten of eleven, including Ikzf1 -- the transcription factor the experiment is
ABOUT. Scoring nine production STATegra reports against that rubric, four items
were never earned in any of them, and three of the four (D3 DOK down, E3
polyamine genes down, E4 Myc) are exactly the genes above. They were not missed;
they were unreachable, and no prompt can fix a gene that is not in the context.

So: two tools, both instant and both free -- name a gene and get its values from
anywhere in the upload, and list every gene in a pathway rather than ten.

    python -m src.tests.test_a_gene_the_agent_cannot_see
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests.agent_tool_call import invokeTool                # noqa: E402
from src.classes.AIInterpret import agent_loop as L               # noqa: E402
from src.classes.AIInterpret import context_builder as CB         # noqa: E402
from src.classes.AIInterpret import neighbours as N               # noqa: E402

L._archive_trace = lambda ctx: None

_PASSED, _FAILED = [], []


# --------------------------------------------------------------- fixtures

class _OmicValue(object):
    def __init__(self, name, values, relevant):
        self._n, self._v, self._r = name, values, relevant

    def getOmicName(self): return self._n
    def getValues(self): return self._v
    def isRelevant(self): return self._r


class _Gene(object):
    def __init__(self, symbol, values, relevant=True, omic="Gene expression"):
        self._s = symbol
        self._o = [_OmicValue(omic, values, relevant)] if values else []

    def getName(self): return self._s
    def getOmicsValues(self): return self._o


class _Pathway(object):
    def __init__(self, pid, name, gene_ids):
        self.ID, self.name, self.source = pid, name, "KEGG"
        self.matchedGenes = list(gene_ids)
        self.matchedCompounds = []
        # (adjusted, combined, corrected) per omic, as the real Pathway holds
        # them -- context_builder reads index 2 for the p-value it ranks on.
        self.significanceValues = {"Gene expression": (0.001, 0.001, 0.001)}

    def getSignificanceValues(self):
        return self.significanceValues


def _job(n_loud=10, n_quiet=40):
    """A pathway wide enough that the top-ten cut hides most of it."""
    genes, ids = {}, []
    for i in range(n_loud):
        gid = "loud%d" % i
        genes[gid] = _Gene("Loud%d" % i, [0.0, -5.0 + i * 0.1], True)
        ids.append(gid)
    for i in range(n_quiet):
        gid = "quiet%d" % i
        genes[gid] = _Gene("Quiet%d" % i, [0.0, -0.5], True)
        ids.append(gid)
    # The gene the paper names: real, differential, and far down the ranking.
    genes["srm"] = _Gene("Srm", [-0.33, -3.36], True)
    ids.append("srm")
    # Matched but not differential.
    genes["flat1"] = _Gene("Flat1", [0.0, 0.0], False)
    ids.append("flat1")

    pw = _Pathway("mmu05200", "Pathways in cancer", ids)

    class Job(object):
        def getMatchedPathways(self): return {"mmu05200": pw}
        def getInputGenesData(self): return genes
        def getGeneBasedInputOmics(self):
            return [{"omicName": "Gene expression",
                     "omicHeader": ["gene", "0h", "24h"]}]
        def getOrganism(self): return "mmu"
        def getInputCompoundsData(self): return {}
    return Job(), pw, genes


def _ctx(job):
    c = L.LoopContext(job_instance=job, job_id="GENETEST",
                      organism_name="Mus musculus", experiment_design="x")
    c.pathways = CB.build_pathway_context(job, pathway_ids=["mmu05200"])
    c.started_at = time.time()
    c.hard_deadline = time.time() + 600
    return c


def _call(tool, ctx, **kw):
    out = invokeTool(tool, ctx, **kw)
    assert "An error occurred while running the tool" not in str(out), (
        "the tool raised and the SDK swallowed it: %s" % str(out)[:300])
    return out


# ------------------------------------------------- the defect, pinned

def test_the_pathway_view_still_hides_most_of_the_pathway():
    """Not a bug to fix -- ten genes with full time courses is the right shape
    for "what does this pathway do". It is only a defect when it is the ONLY
    way in, which is what the two tools below change."""
    job, _pw, _g = _job()
    ctx = _ctx(job)
    shown = [g["symbol"] for g in ctx.pathways[0]["top_genes"]]
    assert len(shown) == 10, shown
    assert "Srm" not in shown, (
        "fixture is wrong: Srm must be outside the top ten for this to test "
        "anything")


def test_a_gene_outside_the_top_ten_is_reachable_by_name():
    """The whole point. D3, E3 and E4 in the sealed rubric are unreachable
    without this."""
    job, _pw, _g = _job()
    out = _call(L.get_gene_measurements, _ctx(job), gene_symbols=["Srm"])
    assert "Srm" in out, out[:300]
    assert "-3.36" in out, "the measured value did not come back: %s" % out[:300]


def test_absent_and_unchanged_are_different_answers():
    """A symbol the experiment never measured and one it measured as flat must
    not read alike -- one forbids writing about the gene, the other is a
    finding."""
    job, _pw, _g = _job()
    out = _call(L.get_gene_measurements, _ctx(job),
                gene_symbols=["Srm", "Nosuchgene"])
    assert "Nosuchgene" in out
    assert "do not write about" in out.lower(), out[-300:]
    assert "Srm" in out, "a bad symbol must not cost the good ones"


def test_a_duplicated_symbol_collapses_to_the_strongest_and_says_so():
    """Symbols are not unique in an omics upload, and the first live run showed
    why that matters: 13 symbols asked for came back as 38 rows, because this
    data carries about three ids per symbol. That tripled the tool's context
    bill for rows nobody asked for, and left the writer three near-identical
    numbers to pick from -- which is how a report ends up quoting a value that
    is real but not the one it means.

    One row, the strongest, and the count of what was collapsed. Keeping the
    first silently would make the answer depend on dict order; dropping the
    others silently would hide that the ambiguity exists at all."""
    job, _pw, genes = _job()
    genes["srm_dup"] = _Gene("Srm", [0.0, 1.5], True)
    found, missing = CB.gene_measurements(job, ["Srm"])
    assert len(found) == 1, [g["symbol"] for g in found]
    assert found[0]["effect_size"] == 3.36, found[0]
    assert found[0]["duplicates"] == 1, found[0]
    assert not missing
    out = _call(L.get_gene_measurements, _ctx(job), gene_symbols=["Srm"])
    assert "share this symbol" in out, out[:400]


def test_the_gene_index_is_case_insensitive():
    job, _pw, _g = _job()
    idx = CB.build_gene_index(job)
    assert idx.get("srm"), idx.get("srm")
    found, missing = CB.gene_measurements(job, ["SRM", "sRm"])
    assert len(found) == 2 and not missing


# ------------------------------------------------------ the full listing

def test_every_differential_gene_is_listed_not_ten():
    job, _pw, _g = _job()
    out = _call(L.list_pathway_genes, _ctx(job),
                pathway_names=["Pathways in cancer"])
    assert "Srm" in out, "the listing is still cut to the loud ten"
    assert "Quiet39" in out, out[:300]
    assert "51 differential" in out, out[:200]


def test_unchanged_genes_are_hidden_but_counted():
    """Silently dropping them would make a 51-gene pathway and a 52-gene
    pathway with one flat member indistinguishable."""
    job, _pw, _g = _job()
    out = _call(L.list_pathway_genes, _ctx(job),
                pathway_names=["Pathways in cancer"])
    assert "Flat1" not in out
    assert "1 unchanged hidden" in out, out[:200]
    out2 = _call(L.list_pathway_genes, _ctx(job),
                 pathway_names=["Pathways in cancer"], include_unchanged=True)
    assert "Flat1" in out2


def test_an_unknown_pathway_is_named_back():
    job, _pw, _g = _job()
    out = _call(L.list_pathway_genes, _ctx(job), pathway_names=["Ferroptosis"])
    assert "Ferroptosis" in out and "No significant pathway matches" in out


# ---------------------------------------------------------- neighbours

def test_neighbour_expansion_is_breadth_first_and_ranked():
    adj = {"a": {"b": [("PPrel", "activation", "p1")],
                 "c": [("PPrel", "activation", "p1")]},
           "b": {"a": [], "d": [("GErel", "expression", "p1")]},
           "c": {"a": [], "d": [("PPrel", "inhibition", "p2")]}}
    one, _ = N.expand(adj, ["a"], steps=1)
    assert {g for g, _s, _e in one} == {"b", "c"}, one
    two, _ = N.expand(adj, ["a"], steps=2)
    assert "d" in {g for g, _s, _e in two}
    assert dict((g, s) for g, s, _e in two)["d"] == 2


def test_the_cap_is_reported_not_silent():
    adj = {"a": {("n%d" % i): [("PPrel", "activation", "p")] for i in range(50)}}
    hood, trimmed = N.expand(adj, ["a"], steps=1, cap=5)
    assert len(hood) == 5 and trimmed is True


def test_a_seed_is_never_returned_as_its_own_neighbour():
    adj = {"a": {"a": [("PPrel", "x", "p")], "b": [("PPrel", "x", "p")]}}
    hood, _ = N.expand(adj, ["a"], steps=1)
    assert {g for g, _s, _e in hood} == {"b"}


def test_no_graph_says_why_rather_than_saying_none():
    """"No neighbours" for a gene whose pathways never had a relation graph is
    a lie by omission: Reactome and OmniPath ship no KGML."""
    job, _pw, _g = _job()
    ctx = _ctx(job)
    ctx.adjacency = {}
    msg = L._neighbour_block(ctx, [{"symbol": "Srm"}], 1)
    assert "KEGG" in msg and ("no neighbour graph" in msg.lower()
                              or "reactome" in msg.lower()), msg


# ------------------------------------------------------------- budgets

def test_both_tools_are_instant():
    """They replace nothing and cost no gateway call; if either ever needs the
    network this assertion is the alarm."""
    job, _pw, _g = _job()
    ctx = _ctx(job)
    t0 = time.time()
    _call(L.get_gene_measurements, ctx, gene_symbols=["Srm", "Loud1"])
    _call(L.list_pathway_genes, ctx, pathway_names=["Pathways in cancer"])
    assert time.time() - t0 < 2.0, "a data tool went slow"


def test_the_descriptions_fit_the_per_turn_budget():
    for name in ("get_gene_measurements", "list_pathway_genes"):
        tool = [t for t in L.TOOLBELT if t.name == name][0]
        assert 100 < len(tool.description or "") <= 700, (
            "%s description is %d chars" % (name, len(tool.description or "")))


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_the_pathway_view_still_hides_most_of_the_pathway,
              test_a_gene_outside_the_top_ten_is_reachable_by_name,
              test_absent_and_unchanged_are_different_answers,
              test_a_duplicated_symbol_collapses_to_the_strongest_and_says_so,
              test_the_gene_index_is_case_insensitive,
              test_every_differential_gene_is_listed_not_ten,
              test_unchanged_genes_are_hidden_but_counted,
              test_an_unknown_pathway_is_named_back,
              test_neighbour_expansion_is_breadth_first_and_ranked,
              test_the_cap_is_reported_not_silent,
              test_a_seed_is_never_returned_as_its_own_neighbour,
              test_no_graph_says_why_rather_than_saying_none,
              test_both_tools_are_instant,
              test_the_descriptions_fit_the_per_turn_budget):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
