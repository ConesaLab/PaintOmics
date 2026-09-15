# How the walk works

The interpretation is a graph walk: AI agents start at the places where your relevant
features concentrate, move along edges the databases draw, read your values at every stop
against your experiment design, and hand the chain they walked to writers that may cite
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

## Agents in parallel

A model walk runs as a team inside one time budget:

1. A **planner** scans the graph and chooses the seeds and the steps.
2. One **walker per seed** walks that seed's neighbourhood, four at a time. The walkers share
   the edges already walked and the nodes already read, so no two walk the same edge in the
   same direction and each sees what the others found. Their legs are merged into one
   numbered chain, joined by a jump leg from one seed to the next.
3. The chain is split into parts and one **Writer per part** writes its statements, four at a
   time, over one shared paper list.
4. A **paper agent** reads every paper a Writer cites and returns only the passage that states
   the claim. It reads the abstract and, when PubMed Central or Europe PMC carries the
   article, its introduction, results and discussion, not the 6,000 characters the Writer reads.
   Code then finds that passage in the paper's own text and widens it to whole sentences.
   A citation whose passage is not in the paper, or whose paper states no such thing, goes
   back to the Writer as an objection.
5. A **sense check** reads every statement, and the **Narrator** writes the Results section.

| | network (the interpretation) | one pathway |
|---|---|---|
| seeds | up to 10, 5 to 8 steps each | up to 5, 3 to 6 steps each |
| statements | 3 to 5 per part, up to 6 parts | 2 to 4 per part, up to 4 parts |
| papers asked for | about 24 | about 12 |
| Results section | 800 to 2,000 words | 400 to 1,200 words |
| time budget | 10 minutes | 8 minutes |

Every stage has a deadline. A walker past it is stopped at its next turn, a Writer past it
keeps the statements that passed on its latest submission, and the sense check and the
Narrator run only when there is time left for them. On the STATegra example the network
interpretation took under five minutes: 49 legs from 10 seeds, 20 statements and 26 cited
papers, each with its passage, in a 1,900-word Results section.

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

The first tool, `scan`, ranks nodes by heat. Over the whole graph it flags the
seed candidates: relevant nodes that are not next to a hotter candidate. Around the
current position it ranks what lies one to three steps away. The planner `plan`s the
seeds and the step budget under the limits in the table above, and each walker moves with
`step` and `jump`, giving at every move a *reading*: one sentence on what the values it
walks onto say in the design's terms. Code refuses a step to a node that is
not a neighbour, a second pass over an edge in the same direction, and a reading that names
no layer of the node. Every neighbour shown is logged as seen.

## What the writer may say

Each Writer receives the design card, its part of the chain with every reading and every
layer's values, the nodes seen from those legs, the walkers' notes, and one line on what the
other Writers cover. It writes statements about its legs only. Each cites its evidence as node
and layer, names the legs it rests on, and separates what the pathway already draws (cited as
the leg, never searched) from what published biology adds (the established role of the genes,
a known regulation, the mechanism behind a direction), which needs a paper found on PubMed (in
PubMed's best-match order, not newest first), read before it is cited, and confirmed by the
paper agent, or must be worded as a hypothesis. Code checks every citation, the relevance of
every value used as evidence, the grounding, and the wording: a statement that calls a set of
genes a cluster, or narrates the walk, goes back to be rewritten with the genes named. A second
model call checks direction, timing, contrast, layer agreement and literature; a statement that
fails is rewritten once and then dropped, and every drop is listed with its reason.

The Narrator then writes a Results section from the kept statements only, every sentence
tagged with its statement and its legs. It sees only the papers the kept statements cite, each
with the claim its paper agent confirmed, and writes as a paper reports results: code drops a
sentence that calls genes a cluster or narrates the walk.
Code checks that every quoted value appears in the record (a signed value verbatim, an
unsigned one as the magnitude of a recorded value), that a paragraph keeps every citation its
statement makes and cites nothing its statement does not, and drops the sentences that fail. A story that
fails twice is dropped and the statements stand alone. The cited papers are then numbered
1..n in the order the reader meets them; retrieved papers nothing cites are left out, and
each cited paper carries its passages, the section each sits in, and the claim each supports.

## Five checks before it is rendered

Nothing the Writers and the Narrator produce reaches the page until the walk has passed five
checks that can fail. Each is a verdict stored with the walk, shown as a chip above the
Results section with its numbers on hover and its reason when it failed; a walk that fails
any of them shows the checks, the reason and the walk itself (the legs are database
relations and your values, not findings), and no Results section or statements.

1. **Not a graph artifact.** After the walk, code runs a permutation test of the data: the
   scripted walker walks the real relevant flags once and flags permuted over the measured
   nodes fifty times, and counts how many *modules* it finds -- a stretch of the chain
   between jumps holding at least three relevant nodes whose signed arrows agree with the
   values at least half the time -- and how hot its seeds are. The real flags must give more
   modules than the permuted ones do (empirical p below 0.05), or, when the graph holds only
   one or two modules, seeds far hotter than chance. A pathway whose relevant genes are
   spread as the job's genes are at large fails here, and its walk is withheld: on the
   STATegra example the FoxO pathway does, the whole network does not. A statement resting on
   an arrow the values move against is an association whatever the arrow says. Every edge is also
   *tiered*: a signed, directed relation (activation, inhibition, expression, a reaction, a
   transcription-factor target) is a **mechanism**; a binding, an indirect effect, a shared
   metabolite or the job's own miRNA pairing is an **association**. Currency metabolites --
   ATP, ADP, phosphate, NAD, water and their kin -- are never walked and never a seed, and a
   relation the KGML draws through one of them is not an edge of the network at all.
2. **The title fits the body.** A statement inherits the weaker tier of the legs it rests
   on. The title and the summary may use a mechanistic verb ("drives", "rewires", "shuts
   down", "is induced by") only where a mechanism statement about those genes stands behind
   it; a sentence whose subject is the perturbation needs a mechanism statement at the
   anchor; a body of associations allows no such verb at all. The Narrator is sent back
   once; if the title still outruns the body, code replaces it with "*pathway*: what changed
   after *perturbation*" and removes the summary sentences that carried the verbs.
3. **Direction logic.** A curated panel names the regulators whose sign is read backwards
   by habit: **feedback reporters** (Cish, the Socs and Dusp families, Nfkbia, Spry, Axin2,
   Smad7 ...) are induced by their own pathway, so their fall reports the pathway *down*;
   **true inhibitors** (Pten, Tsc2, Cbl, Nf1, Ptpn6 ...) are brakes, so their fall reports
   it *up*. For every statement that cites a panel gene, code derives the direction the
   values imply, one short model call reads the direction the statement claims, and a
   second asks whether the statement would still read as true with that gene's values
   reversed. A claim that contradicts the data is sent back once and then dropped; a
   statement that fits the values and their opposite alike is flagged as saying nothing.
4. **Citations in context.** Every retrieved paper's MeSH headings give its organism, its
   cell type or tissue and its scope (in vitro, in vivo, clinical, review). Search hits are
   listed in-context first and tagged; a citation to a paper from another organism or
   system must say so in the sentence that cites it ("in human T cells [3]") or it is
   refused. The paper agent is shown the sentence as the reader sees it, not only the
   claim, and refuses a sentence that attributes to the paper more than its passage states.
5. **Anchored to the perturbation.** The design card names the perturbed gene and its
   direction (read from the design text, or corrected by you before the walk). When the
   organism ships a transcription-factor target table (`mapping/tf_targets.tsv`, CollecTRI
   and TRRUST, written by `omnipathInstaller.py --tf-targets`), the anchor gets its known
   targets as edges for this run, so a factor the pathway maps never drew -- Ikaros in the
   example -- still has a neighbourhood to start from. The seed candidates are drawn first
   from that neighbourhood (within two steps, hottest first) and listed nearest first, the
   first seed must be among the nearest, and every neighbour a walker sees carries its
   distance. Every module is labelled by its distance from the anchor and the header states
   the perturbation and its direction. The check passes when the walked nodes lie within two
   steps of the anchor more often than the measured nodes at large do.

## Running it

```
cd PaintomicsServer
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --policy greedy      # no model: the scripted walker
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --policy model       # walker, Writer, checks, Narrator
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --evaluate           # planted-module recall, no model
PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job <jobID> \
    --scope pathway:mmu04068 --five-checks --repeats 5 --permutations 20 \
    --ko-job <simulated knockout job>             # the five checks, offline, as ranges
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

The five checks are measured offline as well, five times at the production temperatures
so the guarantees hold across the model's own variation, and every number is reported as
a range: twenty whole-pipeline runs on permuted data against five on the real data
(statements kept, modules found); the title gate's first-draft violations; a panel of
forty-one regulators in constructed statements, both classes above 90 percent with the
injected sign flips flagged; fifty-six hand-labelled papers against the MeSH reading and
twenty constructed paper pairs; and Ikzf1-anchored network walks against a decoy factor
and unanchored walks on reachability and known-target enrichment, on the STATegra
example and on a simulated Pten knockout (`examplefiles/datasets/13-simulated-pten-knockout`,
an unlisted dataset built from the mouse network by `walker/simulate_ko.py`). The report
is `docs/dev/tellme-five-checks.md`.
