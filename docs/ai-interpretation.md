# The interpretation

When a job finishes, PaintOmics AI interprets it by walking a graph. An agent
walks the network of every KEGG, Reactome and OmniPath interaction known for your
organism, with your values laid on its nodes. It starts where your relevant
features cluster, reads your values at every stop against your experiment
design, and hands the chain it walked to a writer that may cite nothing else. A
checked Results section comes out of that chain.

It is a draft for you to check, not a conclusion. It is grounded in two things
you can inspect: the interactions the databases draw, and your own numbers. A
sentence such as *"Ccnd1 gene expression collapsed from +1.90 at 0h to −4.13 at
24h"* quotes values the code has matched against your upload, on a leg the
network draws. [How the walk works](ai-graph-walk.md) describes the machinery.

## Starting it

You do not press anything. If AI interpretation is enabled on the server and the
job was submitted through the upload form, the walk is queued the moment Step 2
finishes. It runs while you look at your results.

The **AI Interpret** button in the Step 3 toolbar opens the panel. So does the
circular mark in the bottom-right corner, whose badge shows the state: spinning
while it works, a green tick when the result is ready, and a red exclamation if
it failed. The panel is anchored to the page rather than to the results view, so
it stays open over a painted pathway.

Filling in **Experiment design** on the upload form is the most useful thing you
can do for the result. The walk reads every value against a design card built
from that text and from your column headers. The card says what a value is, what
the columns are (time points, doses or unordered conditions), and which omics
have no column labels. Without the text, the card has your column labels and
nothing else.

## While it works

The panel lists the legs as the agent walks them, newest last, with a progress
bar through its stages: reading the network, reading the design, walking,
writing statements, checking them, and writing the Results section. A walk of
the whole network takes a few minutes.

## What the result contains

* **The Results section:** a title, a one-sentence summary, and one paragraph per
  kept statement in the order the walk found them. Each paragraph ends with chips
  for the legs it rests on. Numbers in brackets such as `[1]` link to the papers
  on PubMed, numbered in the order they are first cited.
* **The cited papers**, numbered as the text cites them.
* **The statements** that passed the checks. Each one separates what the pathway
  already draws (cited as a leg) from what goes beyond it (a paper the writer
  read, or a hypothesis worded as one). Dropped statements are listed with the
  reason.
* **The walk itself:** every leg, the edge it followed and the database and
  pathway that draw it, and the reading the agent gave at that stop.

Every quoted value, every leg and every citation is checked by code before you
see it. A statement that fails the checks is rewritten once and then dropped. A
Results section that fails twice is dropped too, and the statements stand alone.

!!! note "There is no export"
    The result lives in the panel. Select the text and copy it; the line saying
    it was drafted by a language model comes with it.

## Following a thread

**Click a pathway** named on a leg to open its diagram in Step 4. The diagram's
**Walk** column walks that one pathway on its own map. It shows the design card,
which you can correct before you start. The legs appear on the map as numbered
arcs while the agent walks, and the column ends with a Results section for that
pathway.

**Ask a follow-up question** in the box at the foot of the panel. The question
goes to the model with the walk as context, plus tools that read this job's
data. Those tools return a gene's values in every layer under your own column
labels, and they can compare genes, list a pathway's matched genes, or walk a few
steps from a gene you name. The conversation is kept with the job.

## When it does not work

| What you see | What it means |
|---|---|
| The bar sits at 0%, "Not started", and never moves | The server has AI interpretation enabled but no API key for its provider. Nothing was spent and nothing was sent; this needs a server administrator. |
| "AI interpretation is not enabled on this server." | `AI_INTERPRETATION_ENABLED` is off here. |
| "The walk was interrupted (no progress for 10 min). Click Retry." | The walk stalled and was marked dead. **Retry** queues it again. |
| "Your session expired…" | Sign in again and reopen the job from your job list. |
| "This job is no longer stored on the server…" | The job passed its retention window: 7 days for a guest job, 14 for one belonging to a registered account. |
| A pathway's Walk column says the pathway draws no interactions | MapMan bins, and the few KEGG and Reactome pathways that draw no gene-to-gene interactions, have nothing to walk. |

## What it cannot do

* It cannot see your uploaded files. It sees matched features, their values and
  their column labels.
* It cannot walk an interaction no database draws. A relation outside KEGG,
  Reactome and OmniPath, or between features that failed to map, is not in the
  network.
* It does not know your hypothesis unless you wrote it in **Experiment design**.
* It is a language model. It can write a fluent paragraph that is wrong about your
  biology. The checks verify that its values are yours, its legs are drawn and its
  papers were retrieved and read. Whether the argument holds is your judgement.
