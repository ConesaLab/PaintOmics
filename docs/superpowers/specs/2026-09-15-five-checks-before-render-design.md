# Five checks before an interpretation is rendered — design

Status: approved for implementation on 2026-09-15 by the user's `/goal` (build the checks,
test on the example and on simulated data, take the PR through review to merge, deploy to
paintomics.org and paintomics.uv.es). Extends the walk spec,
`2026-09-14-agentic-graph-walk-design.md` (PR #171). "TELLME interpretation" in the goal is
the walk interpretation that PR delivers: the network walk in the AI panel and the pathway
walk in the Step 4 Walk column.

## 1. Goal

No interpretation reaches the reader until it has passed five checks that can fail. Each
check has a **runtime gate** that runs inside every walk on code alone or on one short model
call, and an **offline harness** that measures the whole pipeline at its production
temperatures, five times, and reports every number as a range. A gate that fails blocks the
Results section and the statements; the reader sees the walk, the five verdicts and the
reason, never a story the checks could not stand behind.

| # | the check, as stated | runtime gate | offline harness |
|---|---|---|---|
| 1 | not a graph artifact | structure null (code); no leg through a currency metabolite; every edge tiered | 20 permuted-data runs of the whole pipeline: fewer statements kept, fewer modules found, p < 0.05 |
| 2 | title and summary do not outrun the body | every mechanistic verb in the title or summary maps to a body statement of the mechanism tier; an all-association body gives a title with none | violation rate before the gate and after it, on every run |
| 3 | direction logic holds | feedback reporters read *with* their pathway, true inhibitors *against*; a claimed direction that disagrees is rewritten once, then dropped; a sign flip changes the verdict | ~30-gene panel, both halves > 90 %, injected flips flagged |
| 4 | citations match context and passage | organism, system and scope of every cited paper against the design; out-of-context papers need the qualifier in the sentence; the sentence that cites, not a paraphrase, must sit in the passage | > 85 % agreement with hand labels; in-context paper chosen in > 90 % of constructed pairs |
| 5 | anchored to the perturbation | the design card names the perturbed gene and its direction; the walk starts there; every module is labelled by its distance from the anchor; the header states the direction | Ikzf1-anchored > decoy-anchored and unanchored on reachability and on known-target enrichment, on the example and on a simulated knockout |

## 2. Vocabulary

* **module** — one segment of the merged chain: a seed and the legs its walker took. The
  record already stores `walk.segments`; a module is *found* when its walked nodes hold at
  least three relevant ones.
* **tier** — of an edge: 1 *mechanism* (a signed, directed relation: activation, inhibition,
  expression, repression, phosphorylation, dephosphorylation, ubiquitination, OmniPath
  stimulation or inhibition, a Reactome reaction, a TF → target row, a KEGG enzyme →
  metabolite reaction), 2 *association* (binding/association, indirect effect, state change,
  dissociation, a relation mediated by a shared compound, an unsigned OmniPath edge, the
  job's own miRNA → target pairing, anything else). Of a statement: *mechanism* when it
  cites at least one step leg and every step leg it cites is tier 1; else *association*.
* **currency metabolite** — KEGG compounds C00001 (H2O), C00002 (ATP), C00008 (ADP), C00020
  (AMP), C00009 (Pi), C00013 (PPi), C00003 (NAD+), C00004 (NADH), C00005 (NADPH), C00006
  (NADP+), C00080 (H+), C00007 (O2), C00011 (CO2), C00010 (CoA), C00044 (GTP), C00035 (GDP),
  C00144 (GMP), C00075 (UTP), C00015 (UDP), C00105 (UMP), C00063 (CTP), C00112 (CDP), C00055
  (CMP), C00027 (H2O2), C00014 (NH3), C00019 (SAM), C00021 (SAH). Not walkable, never a
  seed, and a gene–gene relation the KGML draws *through* one of them is not an edge.
* **anchor** — the node of the perturbed gene the design card names, with the direction of
  the perturbation (*up*: induced, overexpressed, activated, agonist; *down*: knockout,
  knockdown, inhibitor, deletion). A run is *anchored* when the anchor resolves to a node
  of the organism's network (after its TF → target rows are added), *unanchored* otherwise.
* **gate** — one of five verdicts `{pass: bool, ...numbers, why}` in `record.checks.gates`.
  `checks.rendered` is true only when all five pass; the client renders Results and
  statements only then.

## 3. Check 1 — the walk is not a graph artifact

**Tiers and currency (code, every walk).** `walker/tiers.py` gives every edge a tier from
its database and subtype; the leg record carries `edge.tier`. The KGML parser
(`common/KeggGraph/parser.py`) records the mediating compound of a `compound` relation in a
new `Edge.via` field, and the network builder drops a relation whose `via` is a currency
metabolite. Currency compound nodes stay in the network (the hub analysis and the record
count them) but `Walker.neighbour_rows` skips them the way it skips hubs, `scan_graph`
never flags one as a candidate, and the sealed chain is checked: a leg whose end is a
currency compound fails the gate. The network cache version goes to 4.

**Structure null (code, every walk).** After the walk, before the Writers, `walker/null.py`
permutes the relevant flag over the measured nodes of the walked graph K = 50 times (a
permutation moves a node's *values* with its flag, so a permuted job is a job whose
measurements landed on other genes), recomputes heat, runs the scripted greedy walker with
the run's own parameters, and records two statistics per permutation: modules found and
the mean heat of the chosen seeds. The real run's statistics come from the model walk. The
empirical p is `(1 + #{null ≥ real}) / (K + 1)`. The gate passes when `p_modules < 0.05` or
the real walk found ≥ 2 modules with `p_heat < 0.05` — a small pathway can hold one module
that is nonetheless far hotter than chance. Greedy on permuted data is a stronger walker than
the model on real data, so the null is conservative. The gate stores `{K, real: {modules,
seed_heat}, null_mean, p_modules, p_heat, pass}`.

**Offline.** The harness runs the whole model pipeline (walker, Writers, paper agents, sense
check, Narrator) on the example pathway walk (`pathway:mmu04068`) 5 times on the real job
and 20 times on permuted jobs (one permutation each; every permuted run is also an
independent model sample), then reports statements kept and modules found for both, and
the empirical p of each real run against the 20. Pass: every real run p < 0.05 on both.

## 4. Check 2 — the title and the summary do not outrun the body

`walker/tiers.py` also carries the verb lexicon. **Mechanistic**: drive(s), rewire(s),
shut(s) down/off, switch(es) on/off, activate(s), inhibit(s), induce(s), repress(es),
suppress(es), trigger(s), block(s), abolish(es), silence(s), control(s), regulate(s),
promote(s), cause(s), phosphorylate(s), degrade(s), derepress(es), reprogram(s), remodel(s),
mediate(s), depend(s) on, signal(s) through, upregulate(s), downregulate(s), turn(s) on/off,
and their passive forms when an agent follows (“is induced **by**”). **Associational**:
rise(s), fall(s), increase(s), decrease(s), change(s), accompan(y/ies), coincide(s),
correlate(s), track(s), parallel(s), co-var(y/ies), is associated with, is higher/lower,
show(s), display(s), and passives with no agent (“is induced”, “is repressed”).

Every kept statement gets `tier` from its legs (§2). The Narrator's brief lists each
statement's tier and the rule; after the Narrator, `tiers.title_outruns_body(results, kept,
walker, anchor)` returns objections:

* a title or summary sentence with a mechanistic verb names chain genes → some *mechanism*
  statement must name one of them (its cites or its claim);
* the sentence names no gene → some mechanism statement must exist;
* the sentence's subject is the perturbation (“Ikaros perturbation drives …”) → some
  mechanism statement must rest on a leg within one step of the anchor; unanchored runs
  may not use a mechanistic verb with the perturbation as subject;
* the body has no mechanism statement → the title and summary may carry no mechanistic
  verb at all.

The objections go back to the Narrator once. If they stand, code replaces the title with
`"{pathway or network}: what changed after {perturbation}"` (the card's perturbation text,
first clause, ≤ 60 characters), removes the summary sentences that still carry a
mechanistic verb, and records `gates.title = {verbs_found, rewritten, fallback: true}`. The
gate then passes by construction; it fails only when the Results section itself failed and
the statements stand alone — then there is no title to check and the gate records
`not_applicable: true, pass: true`.

## 5. Check 3 — direction logic

`walker/regulators.py` is a curated panel, one row per gene: symbol, class, the pathway it
reports on, one PubMed id. **Feedback reporters** (induced by their own pathway; their
change reads *with* the pathway): Cish, Socs1, Socs2, Socs3, Dusp1, Dusp4, Dusp5, Dusp6,
Nfkbia, Nfkbiz, Tnfaip3, Spry1, Spry2, Spry4, Errfi1, Axin2, Nkd1, Smad7, Rgs2, Rgs16,
Mdm2, Hes1, Ptch1, Dkk1. **True inhibitors** (upstream brakes; their change reads *against*
the pathway): Pten, Tsc1, Tsc2, Cbl, Cblb, Nf1, Rasa1, Ptpn1, Ptpn6, Ptpn22, Inpp5d,
Pik3ip1, Dab2ip, Csk, Apc, Gsk3b, Rb1, Cdkn1b. Thirty-nine rows, so a ~30-gene panel of
either half can be drawn for the harness.

`walker/direction.py` runs after the Writers, before the sense check:

1. For every kept statement, code finds panel genes among its cites (by node label or
   member). The **data direction** of each is the sign of the largest-magnitude value in
   the cited relevant layer. The **implied pathway direction** is that sign for a feedback
   reporter and its negation for a true inhibitor.
2. One schema-constrained model call per statement with a panel hit (`{n, pathway,
   claimed: "up" | "down" | "none", quote}`) reads the statement's claim and prose and
   answers what direction it claims for that pathway, quoting the words that say so.
   `none` (the statement makes no pathway claim) passes.
3. **Counter-hypothesis.** The same call is repeated on the statement with the panel gene's
   values sign-flipped in the layer text it is shown. A claim whose extracted direction
   does not flip when its evidence flips is data-insensitive and fails with
   `"the claim does not follow from the values: it reads the same with the signs reversed"`.
4. A statement whose claimed direction contradicts the implied one gets the objection
   `"Cish is a negative-feedback target of JAK-STAT: its fall reports the pathway down, not
   derepressed"`, is rewritten once through `service.rewrite_once`, re-checked, and dropped
   with `by: "direction check"` when it still disagrees.

The Writer's instructions and the sense check's brief carry the same two-line rule. The gate
records `{checked, consistent, flagged_insensitive, dropped, pass}`; it passes when no kept
statement contradicts the panel.

**Offline.** For each panel gene a statement is constructed on the synthetic organism's
graph with the gene's values set down (and, separately, up); the direction stage must
return the hand-labelled pathway direction. Accuracy per half over 5 repeats; both > 90 %.
Sign flips are injected into 20 % of the cases and every injected flip must be flagged.

## 6. Check 4 — citations match the context and the passage

**Context from MeSH (code).** `pubmed_client.fetch_abstracts` and `fetch_papers` keep each
record's MeSH headings and publication types. `literature.paper_context(paper)` reads them:
`organism` (Mice → mouse, Humans → human, Rats → rat, else the first organism heading or
“unknown”), `system` (the cell, tissue or disease headings: B-Lymphocytes, Precursor Cells,
B-Lymphoid, Liver, …), `scope` (review when the publication type says so; in vitro when
Cell Line or Cells, Cultured; clinical when Humans with a patient or cohort heading; in
vivo when an animal heading without Cell Line; else unknown). Papers without MeSH (ahead
of print) are “unknown” on every axis and never fail a match.

**Card context.** `card.py` gains `organism` (the job's organism code and species name) and
`system` (free text the model extracts: “mouse B3 pre-B cell line”). `literature.
context_match(card, paper_context)` returns `{organism: same | other | unknown, system: same
| other | unknown}`.

**Ranking and the qualifier rule.** `search_literature` orders the hits it lists by context
match (same organism first, then same system) and tags each line `[mouse · B cells · in
vitro]`. The Writer's instructions say to prefer the in-context paper and, when citing an
out-of-context one, to name its context in the sentence (“in human T cells [3]”). The
Verifier objects to a citation whose paper is *other* on organism or system when the citing
sentence does not name the paper's organism or system word.

**Sentence, not paraphrase.** `verify.citation_pairs` pairs each `[N]` with the prose
sentence that carries it (the beyond claim only when no sentence does), so the paper agent
checks the sentence the reader sees. A sentence that says more than its passage is
unsupported and goes back to the Writer as today.

The reference list shows each paper's context chips. The gate records `{cited, in_context,
qualified, unqualified_other, pass}` and passes when every out-of-context citation is
qualified and every citation has a confirmed passage.

**Offline.** Forty papers the example walks cited or the harness retrieves are hand-labelled
(organism, system, scope) in `benchmarks/tellme/context_labels.json`; agreement of
`paper_context` with the labels > 85 % per axis. Twenty constructed pairs (one in-context,
one out-of-context paper for one claim) are put through a Writer with a stub PubMed 5 times
each; the in-context paper must be cited in > 90 % of runs.

## 7. Check 5 — the walk is anchored to the perturbation

**Card.** `CARD_SCHEMA` gains `perturbed_genes` (symbols, may be empty), `perturbation_
direction` (`up | down | unknown`) and `system`. The deterministic card finds them without a
model: a regex over the design text for the perturbation verbs of §2 near a token that
resolves through the organism's symbol and alias table (`kegg2genesymbol.list` lists
`Ikzf1, Ikaros, LyF-1, …` for one gene). The user can edit the fields in the Walk column's
card form.

**TF → target rows (install).** `AdminTools/omnipathInstaller.py --tf-targets` writes
`<org>/mapping/tf_targets.tsv` (`tf, target, sign, source, references`): CollecTRI for the
organism from OmniPath, CollecTRI human mapped by ortholog symbol for mouse and rat (the
mouse table drops Ikzf1; the human one has 52 targets, 51 with a mouse symbol), and TRRUST
mouse or human. The file is optional; a species without it walks as today.

**Anchor edges (overlay).** `walker/anchor.py` resolves the card's genes to nodes. When the
anchor is a TF in `tf_targets.tsv`, its rows become tier-1 edges tagged `anchor:<source>`
(subtype “transcriptional regulation”, sign from the row), added to the walked graph the
way the job's miRNAs are: for this run only. The anchor then has a neighbourhood even on an
organism whose pathways never drew it. BFS from the anchor over the universal network
(currency and hubs passable for distance) gives every node a distance; every module gets
`distance` = the least distance of its walked nodes; the plan and the scan show `d=` next to
each candidate.

**The walk.** The planner's brief names the anchor, its direction and the candidates'
distances, and says to start at the anchor's neighbourhood; `code_plan` orders candidates
by (distance, −heat) when anchored. The header of the record (`checks.anchor`) is
`{gene, node, direction, in_graph, source}`; the client shows “Perturbation: Ikzf1 induced
(up) · anchor in the network” above the Results, and each module's distance in the plan and
the legs list. Unanchored runs show “no perturbed gene named in the design”.

**Metrics.** `reachability` = the fraction of walked nodes within 2 steps of the anchor;
`target_enrichment` = the hypergeometric p of walked genes among the known targets
(`benchmarks/tellme/ikaros_targets.json`: TRRUST mouse rows for Ikzf1 and the B-cell targets
of Ferreirós-Vidal 2013, each with its PubMed id) against the measured genes. The gate
passes when the run is anchored and reachability ≥ the unanchored baseline stored for the
job, or when the design names no gene (then `not_applicable: true, pass: true`).

**Offline.** Three network walks 5 times each on the example job: anchored on Ikzf1,
anchored on a decoy TF with a regulon of similar size and no B-cell role (Hnf4a, or the
nearest by regulon size), unanchored. Pass: every anchored run beats both others on
reachability and on target enrichment. The same on a **simulated knockout**: a mouse job
built by `walker/simulate_ko.py` from the real network — Pten set to −2.5 at every column
with sign propagated along signed edges for two steps and noise elsewhere, relevance by
|value| > 1 — anchored on Pten against a decoy and unanchored. The simulated files are
written under `examplefiles/datasets/13-simulated-pten-knockout/` as an unlisted dataset (no
manifest entry: a knockout planted two steps down a network cannot meet the picker's
planted-pathway coverage floor, and a listed dataset needs a CI baseline); the harness and the
Step 1 upload form load it by path.

## 8. What the reader sees

`service.view` adds `checks.gates` (five verdicts), `checks.rendered`, `checks.anchor`,
`segments` (label, first, last, distance), `tier` on every statement and `context` on
every paper. The Walk column and the AI panel render, in order: the header line
(perturbation, direction, anchor); the five checks as a row of chips, each with its numbers
on hover and its reason when it failed; then, only when `rendered` is true, the Results
section, references and statements. When a gate failed, the panel says which and why, and
the walk (plan, modules with distances, legs) stays open below, because the legs are data.
Statement tiers show as a chip (“mechanism” / “association”); reference entries carry the
context chips.

## 9. The harness

`walker/harness.py`, run as `python -m src.classes.AIInterpret.walker.cli --job <id>
--five-checks --repeats 5 --permutations 20 --out <dir>`. Every run's sealed record is saved
before the next starts, so a stopped harness resumes. Temperatures are the production
ones: planner 0.2, walkers 0.2, Writers 0.3, Narrator 0.3, sense 0.1, direction 0.1, paper
agent 0.0. The report (`docs/dev/tellme-five-checks.md`, generated) shows every metric as
min–max over the repeats and the pass verdict per check, for the example job and for the
simulated knockout.

## 10. Files

New: `walker/tiers.py`, `walker/null.py`, `walker/regulators.py`, `walker/direction.py`,
`walker/anchor.py`, `walker/simulate_ko.py`, `walker/harness.py`,
`benchmarks/tellme/{context_labels.json, ikaros_targets.json, direction_cases.json}`,
`tests/test_walker_tiers.py`, `test_walker_null.py`, `test_walker_direction.py`,
`test_walker_context.py`, `test_walker_anchor.py`, `test_walker_gates.py`,
`test_kegg_parser_via.py`.

Modified: `common/KeggGraph/parser.py` (`Edge.via`), `walker/network.py` (currency
relations dropped, cache v4), `walker/walk.py` (currency skipped, `edge.tier`),
`walker/heat.py` (`dist` on scan rows), `walker/card.py` (anchor and system fields),
`walker/sdk.py` (anchor in the briefs), `walker/parallel.py` (module distance), `walker/
writer.py` (context tags, rules, sentence-level pairs), `walker/verify.py`
(`citation_pairs`, qualifier objection), `walker/literature.py` (`paper_context`,
`context_match`), `walker/narrate.py` (tier rule), `walker/sense.py` (direction rule),
`walker/service.py` (the gates), `walker/record.py` (schema 2), `walker/cli.py`,
`AIInterpret/pubmed_client.py` (MeSH), `AdminTools/omnipathInstaller.py` (`--tf-targets`),
`PA_Step4WalkView.js`, `PA_AIInterpretView.js`, `graph-walk.css`, `index.html`,
`docs/ai-graph-walk.md`, `docs/ai-interpretation.md`, `CHANGELOG.md`,
`examplefiles/datasets/manifest.json`.

Deploy: after the code, `omnipathInstaller.py --organism mmu --tf-targets` (and hsa) on
both hosts; the network cache rebuilds itself from its signature.

## 11. Decisions taken

1. Gates block rendering; they never silently soften a claim except the title fallback
   of §4, which is recorded. 2. The runtime null uses the scripted walker on permuted
   flags: conservative and seconds, not minutes. 3. Currency metabolites are unwalkable
   even when measured. 4. A statement's tier comes from its edges, never from a paper.
   5. Anchor edges are added per run, like the job's miRNAs, not to the shared network.
   6. Context comes from MeSH, not from a model, so it is free and deterministic; the
   model is asked only what it alone can answer (the direction a sentence claims).
   7. Harness runs use production temperatures and save every record. 8. The simulated
   knockout is shipped as an example dataset so the UI can be checked on it.

## 12. Out of scope

Rewriting the Writers' statement schema; a TF → target source for species other than
human, mouse and rat; a permutation null on the Writers at request time.
