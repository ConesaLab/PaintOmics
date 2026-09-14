# How the walk works

The interpretation is a graph walk: an agent starts at the places where your relevant
features cluster, moves along edges the databases draw, reads your values at every stop
against your experiment design, and hands the chain it walked to a writer that may cite
nothing else. This page describes that machinery; [The interpretation](ai-interpretation.md)
describes what you see.

The universal walk over the whole network is the job's interpretation: it is queued when
Step 2 finishes and shown in the AI panel. A pathway walk runs from the **Walk** column of a
Step 4 diagram. The chat can read the universal walk, or walk a few steps from a gene you
name with no model at all.

Walks run on the same queue as the analyses, so the server runs at most one fewer walk than
it has workers, and one job holds at most two walks at a time; a walk over either limit is
refused with the reason and can be started again once one finishes. On a job its owner shared
read-only, anyone with the link can start a walk that has never run, but only the owner can
walk again or change the design card. A walk whose AI service fails is stored as failed and
offers **Try again**; a walk whose job is deleted stops at its next step and stores nothing.

## What is walked

One network per organism, built once at install from the annotation files the installer
already ships: KEGG relations from the KGML, Reactome reactions from each pathway's graph
file, and OmniPath interactions. Nodes are genes and compounds; every edge carries its
database, its pathway and a sign (activation and expression positive, inhibition and
repression negative). On mouse the union is about 14,000 nodes and 131,000 edges once the job's miRNAs are added,
builds in about three seconds, and is cached beside the KGML directory.

Reactome reactions join proteins, and their participants are mostly complexes and sets:
a complex or a set is read as its member proteins, and a reaction whose expansion would join
more than 36 pairs is skipped, because a large set says "in the same bag", not "one acts on
the other". A **pathway walk** uses that network filtered to one pathway's tag. A
**universal walk** uses the whole network. Your job is laid over either: a node is *relevant* when any of its
own layers is in your relevant list, every layer's values travel as text with your own
column labels, and miRNAs from a miRNA-seq input become nodes of their own with an edge to
each gene they are attached to. **Your values are never computed on.** The only number the
arithmetic reads from your data is that relevant flag.

## Heat, scan, plan

Every node gets a *heat*: how surprising the relevant count of its neighbourhood is, given
the neighbourhood's size (a hypergeometric test). A hub is not hot for being a hub.

The walker's first tool, `scan`, ranks nodes by heat. Over the whole graph it flags the
seed candidates: relevant nodes that are not next to a hotter candidate. Around the
current position it ranks what lies one to three steps away. The walker then `plan`s its
seeds and its step budget under a ceiling code sets (40 steps on a pathway, 120 on the
network, and at least three steps per seed on the network), and moves with `step` and `jump`, giving at every move a *reading*: one sentence on
what the values it walks onto say in the design's terms. Code refuses a step to a node that is
not a neighbour, a second pass over an edge in the same direction, and a reading that names
no layer of the node. Every neighbour shown is logged as seen.

## What the writer may say

The Writer receives the design card, the chain with every reading and every layer's values,
the nodes seen but not walked, and the walker's notes. It writes three to five statements.
Each cites its evidence as node and layer, names the legs it rests on, and separates what
the pathway already draws (cited as the leg, never searched) from what it builds on top
(a direction against the drawn sign, a mechanism, a causal timing), which needs a paper
found on PubMed (in PubMed's best-match order, not newest first) and read before it is
cited, whose title or abstract names the claim's genes, or must be worded as a hypothesis. The same read and topic
checks apply to every paper a statement cites, in its papers list or in its own words. Code checks every citation, the relevance
of every value used as evidence, and the grounding; a second model call checks direction,
timing, contrast, layer agreement and literature; a statement that fails is rewritten once
and then dropped, and every drop is listed with its reason.

The Narrator then writes a Results section from the kept statements only, every sentence
tagged with its statement and its legs. It sees only the papers the kept statements cite.
Code checks that every quoted value appears in the record (a signed value verbatim, an
unsigned one as the magnitude of a recorded value), that a paragraph keeps every citation its
statement makes and cites nothing its statement does not, and drops the sentences that fail. A story that
fails twice is dropped and the statements stand alone. The cited papers are then numbered
1..n in the order the reader meets them; retrieved papers nothing cites are left out.

## Running it

```
cd PaintomicsServer
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --policy greedy      # no model: the scripted walker
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --policy model       # walker, Writer, checks, Narrator
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --evaluate           # planted-module recall, no model
```

Each run writes a JSON record and a self-contained HTML report under `CLIENT_TMP_DIR/walks/`
(or `--out`): the legs drawn on the KEGG map, the design card, the plan, the chain with its
readings, the statements with their checks, the Results section, and the nodes seen but not
walked.

## How we know it works

The planted-module test needs no model. On a real graph a connected module is planted with
relevant flags, the scripted walker runs, and seed hit, recall and precision are measured
over a grid of module sizes and background rates. On the STATegra example's FoxO map the seed
step lands in or next to the module in at least 93 percent of plants; the scripted walker's
recall of a 12-node module at a 0.33 background rate is about 0.4, which is the baseline the
model policy is measured against.
