# The five checks, measured

*Maintainer documentation: how the five gates on a walk interpretation were measured, what
the numbers were on the day the gates shipped, and how to run the measurement again. The
user-facing description is in [ai-interpretation.md](../ai-interpretation.md); the design is
`docs/superpowers/specs/2026-09-15-five-checks-before-render-design.md`.*

## What was measured

Every walk interpretation (the network walk in the AI panel and a pathway walk in the Step 4
Walk column) carries five verdicts in `checks.gates` and a `checks.rendered` flag, and the
client shows the Results section and the statements only when all five pass. The harness
(`walker/harness.py`, run by `walker/cli.py --five-checks`) measures each gate offline the way
the checks were specified: three whole-pipeline runs at the production temperatures against
the null each check names, every number reported as a range over the three runs.

| check | what the harness runs | the null or the reference | passes when |
|---|---|---|---|
| 1 · not a graph artifact | three whole-pipeline walks on the real job and twenty on permuted data, where the measured nodes of the walked graph exchange their measurements (flag, values and layers together) | the permuted runs | every real run keeps more mechanism statements and finds more modules than the permuted ones (empirical p < 0.05 against the twenty), and no leg joins two nodes through a currency metabolite |
| 2 · the title fits the body | the three real runs' first Narrator drafts | the tiered body | every mechanistic verb in a title or summary rests on a mechanism statement about those genes; a first draft that does not is rewritten once, then replaced by code |
| 3 · direction logic | 164 constructed statements about the 41 panel regulators, each in a small walk with values that imply one direction, with and without an injected sign flip | the panel (`walker/regulators.py`, one PubMed id per row) | more than 90 percent accuracy on the feedback reporters and on the true inhibitors, and the injected flips flagged by the counter-hypothesis step |
| 4 · citations in context | the MeSH reading of 56 hand-labelled papers (`benchmarks/tellme/context_labels.json`) and 20 constructed in-context / out-of-context paper pairs | the hand labels | more than 85 percent agreement on organism, cell type and scope; the in-context paper chosen in more than 90 percent of pairs |
| 5 · anchored to the perturbation | three network walks anchored on Ikzf1, three on a decoy factor of the same regulon size chosen for the least neighbourhood overlap, three unanchored; the same on a simulated Pten knockout | the decoy and unanchored arms | the anchored walks reach more of the two-step neighbourhood of the anchor and enrich its 39 known targets (`benchmarks/tellme/ikaros_targets.json`) more than either arm (one-sided exact rank test, p ≤ 0.05; complete separation of three against three is exactly 0.05) |

The job is the STATegra example (Ikzf1 induction in mouse B3 pre-B cells, six time points;
gene expression, DNase-seq, miRNA-seq, proteomics and metabolomics layers); the pathway is the FoxO
signalling pathway (mmu04068), the one the example's walk is shown on. The knockout is
`examplefiles/datasets/13-simulated-pten-knockout`, an unlisted dataset built from the mouse
network by `walker/simulate_ko.py` and stored as a job with `cli.py --make-ko-job`. Model
`deepseek-ai/DeepSeek-V4-Flash-0731` through the CSIC gateway; temperatures planner 0.2,
walker 0.2, writer 0.3, narrator 0.3, sense 0.1, direction 0.1, paper agent 0.0.

## What the measurement changed

The checks were written to fail, and they did. Each of these is a change the measured runs
forced, in the order they were found:

* **The request-time null moved the flag without its values.** The first structure null
  permuted the relevant flag over the measured nodes and left each node's layers in place, so
  a node flagged relevant in the null had no direction, no leg through it could be judged, and
  the null found about one module wherever the real flags found four to seven: every record,
  the permuted-job ones included, passed with p = 0.02. The null now moves a node's flag and
  its layers together (the harness's own permutation restricted to the walked graph).
* **A neutral title read "over control" as a verb.** The mechanistic lexicon matches
  word-initially, so "control" and "coupling" fired on nouns; the lexicon lost those stems,
  the neutral title is cut before any verb, and the scope name is the last resort.
* **The direction gate failed on contradictions it had already dropped.** A statement dropped
  by the direction check is the check working; the gate now judges the kept statements only,
  and `--regate` recomputes it on sealed records.
* **Results were dropped for a five-word overlap and for 352 words.** The dropped-claim rule
  now needs the first eight words in common, and the word floor is the smaller of the range and
  fifty words per statement; runs sealed before the relaxation carry no Results section and
  their title check reads "not applicable".
* **Anchoring was weak.** Neighbourhood-first seeding with the nearest seed first and a
  refusal to step out of the two-step neighbourhood while a relevant neighbour inside it is
  unread took anchored reachability from 0.26–0.32 to 0.97–1.00.
* **The first decoy overlapped the anchor.** Neurod1, chosen by regulon size alone, shares a
  neighbourhood with Ikzf1 and enrichment did not separate the arms (rank p 0.27). A decoy is
  now the gene of the anchor's kind and size whose two-step neighbourhood overlaps the anchor's
  least (Nr0b1 for Ikzf1, Cyp2c39 for Pten). The Neurod1 arm is kept in the harness output as
  `anchor_example_neurod1/` and is not in the tables below.
* **A run answered by the fallback model is not a measurement.** The harness waits for the
  configured model and discards a run another model answered.
* **The harness's own null had more structure than the data.** The first harness permuted
  the job's features (one gene's measurements handed to another gene's key). Several
  features map onto the hub nodes, so a hub received other genes' layers as well: on the
  network the permuted job had 5.1 layers a node against 1.95 and 66 percent of measured
  nodes relevant against 33; on the FoxO map Foxo1, Foxo3 and Foxo4 reached heat 12–13, and
  four of eight permuted FoxO runs passed the request-time null on seed heat. The harness
  now permutes the way the request-time null does: the measured nodes of the walked graph
  exchange their measurements, flag and layers together. The eight feature-level runs are
  kept beside the report (`perm-featurelevel/`) and are not in the tables.
* **A review pass found nine defects, and they split the measurement in two.** The fixes
  (commit `020cfb93`: the design-text reading taking English words for genes, an unanchorable
  gene withholding every interpretation, a rewritten statement keeping its old direction
  verdict, the scope name's own verbs failing the title gate, a same-organism citation that
  could not be qualified, and four more) landed after the anchor and knockout arms were
  walked. Only one of them changes what a walk keeps: a citation to a paper of the design's
  organism and another system is no longer refused for want of a qualifier, so a post-fix run
  keeps marginally more statements. The real runs of check 1 were therefore walked again
  against the permuted ones under the fixed code, since both arms of that comparison must be
  the same program; the five pre-fix runs are kept aside (`stale-prefix-real/`). The anchor
  and knockout arms measure where a walk went, not what it kept, so their pre-fix records
  stand, and their statement counts are the pre-fix ones.
* **Three repeats, not five.** Repeats were cut from five to three, so the exact rank tests
  of check 5 are read at p ≤ 0.05: complete separation of three runs against three is
  exactly 0.05, the smallest value the test can give. Check 1 needs twenty permutations for
  the same reason (the smallest empirical p is 1/21).

## How to run it

```
cd PaintomicsServer
export PAINTOMICS_KEGG_DATA=<KEGG_DATA> PAINTOMICS_CLIENT_TMP=<CLIENT_TMP>   # a worktree reads the env template
python -m src.classes.AIInterpret.walker.cli --make-ko-job                    # once: the knockout job, prints its id
python -m src.classes.AIInterpret.walker.cli --job <jobID> --five-checks \
    --scope pathway:mmu04068 --repeats 3 --permutations 20 --only artifact --out <dir>
python -m src.classes.AIInterpret.walker.cli --job <jobID> --five-checks --only anchor --repeats 3 --out <dir>
python -m src.classes.AIInterpret.walker.cli --job <jobID> --five-checks --only ko --ko-job <koID> --repeats 3 --out <dir>
python -m src.classes.AIInterpret.walker.cli --job <jobID> --five-checks --only direction,context --repeats 3 --out <dir>
python -m src.classes.AIInterpret.walker.cli --job <jobID> --scope network --five-checks \
    --only artifact --repeats 3 --permutations 20 --out <dir>-network
python -m src.classes.AIInterpret.walker.cli --job <jobID> --ko-job <koID> --regate <dir>   # code-only gates, no model
```

Run the parts one after another: the gateway key is paced at about sixty requests a minute and
one walk already runs four walkers, four Writers and six paper agents in parallel, so two
parts at once produce 429 back-offs inside the stage deadlines and truncate the walks. A
pathway walk takes six to eight minutes, a network walk about ten; the whole measurement is a
working day. Every part resumes: a sealed record is not walked again, and `--regate`
recomputes the code-only gates (the structure null, the tiers and the direction gate on the
kept statements) on sealed records when their definition changes, so a rule fix does not cost
another day of walks.

## Measured

*State on 2026-09-15 at 18:10. The harness (`chain12`) is still walking: the knockout arms of
check 5, the direction and context panels of checks 3 and 4, and the twenty permuted runs of
check 1 on the pathway and on the network land in a follow-up to this page as their stages
finish. What is sealed and regated with the corrected null is below; every number is the
range over three runs.*

Job `Ku5jMVCL6z`, 3 repeats at production temperatures (planner 0.2, walker 0.2, writer 0.3,
narrator 0.3, sense 0.1, direction 0.1, paper agent 0.0), model
`deepseek-ai/DeepSeek-V4-Flash-0731`.

### 1 · Not a graph artifact — pending (request-time gate measured)

The whole-pipeline permutation runs are in progress. The request-time gate, recomputed on
the sealed real runs with the corrected null (`--regate`):

| scope | runs | scripted modules, real | null mean | p modules | seed heat, real | null mean | p heat | gate |
|---|---|---|---|---|---|---|---|---|
| FoxO signalling pathway (mmu04068) | 3 | 3 | 1.82 | 0.196 | 1.41 | 1.02 | 0.118 | withheld |
| network, anchored on Ikzf1 | 3 | 5 | 4.58 | 0.588 | 3.35 | 2.23 | 0.020 | rendered |
| network, unanchored | 3 | 5 | 4.52 | 0.471 | 3.36 | 2.17 | 0.020 | rendered |

On the pathway the real data gives the scripted walker no more modules and no hotter seeds
than its permutations do, so the FoxO walk is withheld; on the network the seeds are far
hotter than chance (p = 1/51) while the module count is not, so the network walk is rendered
on the heat rule. Legs through a currency metabolite: 0 in every run.

### 2 · The title fits the body — pending

Measured from the first Narrator drafts of the real runs of check 1; reported with it. Of
the three sealed pathway runs, two first drafts outran the body ("drives" and "shut down" said
of the perturbation with no mechanism statement at the anchor), were sent back once and then
replaced by the neutral title "FoxO signaling pathway: what changed after Ikzf1
perturbation"; the third ("Ikaros time course rewires FoxO signalling…") rested on a
mechanism statement and stood.

### 3 · Direction logic — PASS

164 constructed statements, one per panel row in each direction plus an injected sign flip in
a fifth of them, three repeats:

| | feedback reporters (23 rows) | true inhibitors (18 rows) | injected flips flagged |
|---|---|---|---|
| accuracy | 0.978–1.000 | 1.000 | 0.976–1.000 |

Both halves stay above the 90 percent the check demands in every repeat, and the
counter-hypothesis step flagged all but one or two of the injected flips per run.

### 4 · Citations in context — FAIL

| axis | agreement with the hand labels (56 papers) | the check asks for |
|---|---|---|
| organism | 0.804 | > 0.85 |
| system (cell type or tissue) | 0.589 | > 0.85 |
| scope (in vitro, in vivo, clinical, review) | 0.571 | > 0.85 |

In-context paper chosen in 18 of 20 constructed pairs, 0.90 in each of the three repeats,
against a threshold of more than 0.90: short by one paper.

Two defects the measurement exposed are fixed, and the numbers above are the fixed reading
(the first run gave organism 0.732, system 0.375, scope 0.571):

* PubMed prints MeSH headings alphabetically, and the reading took the first organism heading,
  so "Animals, Humans, Mice" read as human for every mouse paper that also cites human work.
  Every organism heading is read now; a paper indexed for several species reads as "mixed", and
  the design's own organism among them counts as in context rather than as another organism.
* The scope followed from that single organism: any non-human paper with no in-vitro heading
  was called "in vivo". An animal heading says "in vivo" now, and an in-vitro heading outranks
  it, because a cell line is where the work was done whatever the species.
* The benchmark's own scoring compared a reader's sentence ("B cells; mb-1 promoter reporter
  assays") with a MeSH heading ("B-Lymphocytes") by word overlap alone; it consults the same
  synonym table the runtime uses.

What is left is not a bug. MeSH indexes what a paper is about, not how the work was done: 20 of
the 56 papers carry no cell-type heading at all, 13 no study-type heading, and where the reader
wrote "mixed" for a paper doing both in vitro and in vivo work MeSH names one or neither. The
reading agrees with a careful reader on the organism four times in five and on the other two
axes about three times in five, so the runtime gate that rests on it is weaker than the check
specifies. It is reported as failing rather than re-scored, and the qualifier rule is what
still protects the reader: a citation whose organism or system differs from the design must say
so in the sentence, and an unknown context demands nothing.

### 5 · Anchored to the perturbation, the example (Ikzf1) — FAIL on one of four comparisons

| arm | reachability (≤ 2 steps of Ikzf1) | known-target enrichment p | statements | modules |
|---|---|---|---|---|
| anchored | 1.000 | 0.0000–0.0000 | 13–16 | 5 |
| decoy (Nr0b1) | 0.604–0.659 | 0.0000–0.0074 | 5–20 | 5 |
| unanchored | 0.200–0.269 | 0.0003–0.0102 | 9–19 | 5 |

One-sided rank test, anchored against each arm: reachability p = 0.05 (decoy), 0.05 (unanchored); enrichment p = 0.1 (decoy), 0.05 (unanchored); 39 known targets.

Reachability separates the arms completely: every anchored run walks its whole set of nodes
within two steps of Ikzf1 (1.000), the decoy runs 0.60–0.66, the unanchored 0.20–0.27, so
both rank tests give p = 0.05, the smallest value three against three can give. Known-target
enrichment separates anchored from unanchored (p = 0.05) but not from the decoy (p = 0.1):
one anchored run hit 4 of 30 targets among 45 walked nodes and one decoy run hit 4 among 40.
In this dataset the perturbation *is* Ikaros, so the relevant genes a hot walk reaches are
Ikaros targets wherever it starts; target enrichment measures the data more than the
anchoring, and the anchoring's own signature is reachability. The check is reported as
failed on that comparison rather than re-scored. Five-run arms measured before the decoy rule
changed (Neurod1 as decoy) gave the same picture: reachability p = 0.004 against both arms,
enrichment p = 0.27 against the decoy.

### 5 · Anchored to the perturbation, the simulated knockout (Pten) — the arms do not separate

| arm | reachability (≤ 2 steps of Pten) | known-target enrichment p | statements | the run's own anchor gate |
|---|---|---|---|---|
| anchored on Pten | 0.972–1.000 | 0.0000 | 8–14 | passes (3 of 3) |
| decoy (Cyp2c39) | 1.000 | 0.0000 | 8–13 | **fails** (3 of 3), withheld |
| unanchored | 1.000 | 0.0000 | 3–13 | passes (not applicable) |

One-sided rank test: reachability p = 0.95 against both arms, enrichment p = 0.65; 860 planted
targets.

The check as specified fails here, and the reason is the design, not the walk.
`simulate_ko.py` plants the knockout's signal by propagating Pten's sign two steps along signed
edges, so every relevant node of the simulated job lies inside the anchor's two-step
neighbourhood by construction: any walk that follows the values is "near Pten" whatever it was
anchored on (base rate 0.227 against 0.086 for the real example), and the planted set is 860
genes, so target enrichment saturates at p = 0 in all three arms. Both harness statistics are
therefore blind on this design, and the comparison is reported as failed rather than re-scored
after the fact.

What does separate, and is the part the reader depends on, is the per-run gate. A run anchored
on the decoy walks nowhere near Cyp2c39 (reachability 0.000 against a base rate of 0.042), so
its own check 5 fails and all three decoy interpretations are withheld. The gate caught the
mis-anchored run in every repeat; the harness's arm comparison could not, because on this
simulation there is nowhere else for a value-following walk to go.
