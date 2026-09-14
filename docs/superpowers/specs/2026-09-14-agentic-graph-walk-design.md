# Agentic Graph Walk — design

Status: approved for implementation on 2026-09-14 (the user's `/goal`: implement, test on the
example data with regulatory and other omics, visualise the result, open a PR). The full
argued design with every number, the example trace and the animation lives in two private
artifacts ("Walker Protocol" V12 and "Walk Replay" V3); this file is the spec the code follows.

## 1. Goal

Turn a multi-omics job into a publication-style, evidence-bound interpretation of one pathway
or of the whole network by *walking* a graph: an agent chooses where to go, reads the user's
values at every stop against the experiment design, and a second agent writes only what the
chain supports. Every number the user reads points back to a leg of the walk.

Two entry points, one engine:

* **Pathway walk** — the user opens one KEGG / Reactome / OmniPath pathway in Step 4 and
  presses Walk. Graph = the universal network filtered to that pathway's tag, plus the job's
  regulators attached to its genes.
* **Universal walk** — runs as a stage of report generation and again from the chat through
  one tool, `walk`. Graph = the whole network with the job overlay.

This PR implements the engine, the scripted policy, the evaluation and a standalone HTML
report. Step 4 column, chat tool and report-time hook are the next PR.

## 2. Inputs, and what is never computed on

* The organism's **universal network**, built once at install from the annotation files the
  install already ships: KEGG KGML relations (via `common/KeggGraph/parser.py`), Reactome
  `reactome/*.graph.json` reactions (input or catalyst → output), OmniPath `omnipath_network`
  edges (stimulation +, inhibition −). Nodes are keyed by KEGG gene id (`g:<entrez>`) or
  compound (`c:<id>`); UniProt ids map through `mapping/uniprot2kegg.list`. Every edge carries
  its database, pathway and sign. MapMan contributes membership tags only (no edges) and is out
  of scope for mouse. Measured on mmu with the STATegra job laid over it: 13,947 nodes, 131,093 edges (the
  parser keeps only nodes that carry an edge), 124 hubs above the 99th degree percentile, built in
  1.5 s from the cache and about 3 s cold.
* The **job overlay**: for every node the job measured, `r = OR` over its own layers of the
  `relevant` flag (itself OR over conditions); the layer text (omic, member, flag, values with
  the user's column labels); the job's miRNAs as regulator nodes with miRNA → target edges
  (sign −1). A regulatory omic becomes nodes only when its rows carry their own identity
  (miRNA yes; DNase, keyed by gene, stays a layer).
* **The user's values are never computed on.** They travel as text. The only thing the
  arithmetic reads from the data is `r`.

## 3. Heat, scan, seeds

* `heat(v) = −log10 P[X ≥ x]`, `X ~ Hypergeom(N, K, n)` over the 1-hop neighbourhood of v:
  n measured neighbours, x relevant; N, K over the graph without v. Conditioning on n removes
  the degree bias. Computed once at overlay. Hubs above the 99th degree percentile are excluded
  from every scan (`degree cap`, network only).
* `scan(scope="graph")` ranks every measured node by heat and flags seed candidates: r = 1,
  n ≥ 1, and not within `sep` edges of a hotter candidate (sep 1 on a pathway, 2 on the
  network; ≤ 12 / ≤ 40 candidates). `scan(scope="here", radius=1..3)` ranks the measured
  nodes within `radius` steps of the current node with distance and first hop. Same statistic,
  different scope.

## 4. The walk (stage 3): tools and rules

Six tools on an OpenAI Agents SDK loop (the pattern of `agent.py`): `scan`, `plan(seeds,
steps, reason)`, `step(to, reading, reason)`, `jump(to, reading, reason)`, `note(text)`,
`stop(reading, reason)`. Rules enforced in code:

* `plan` once, first: seeds ⊆ candidates, 1–8 (pathway) / 1–16 (network); steps 3..ceiling
  (40 / 120); jumps = seeds; notes = steps // 2. Walker placed on seed 1.
* `step` only to a neighbour; an edge closes per direction; the reading must name a layer the
  node has; refusals cost nothing and repeat the neighbour list.
* `jump` only to an unvisited seed or a node on the chain.
* Every neighbour shown is logged as *seen*; a step answers with the node's layers and its
  neighbours by name with r, heat, sign and open/closed; a scan answers with a ranked table.
* Ends on `stop`, on budget, or on a turn without a tool call. Deterministic ordering (r, heat,
  label) so a replayed transcript reproduces the chain.
* A **scripted greedy policy** (highest-heat relevant unvisited neighbour, `steps // seeds`
  per seed, then next seed) drives the same tool functions with no model. It is the Test 1
  walker and the ablation baseline.

## 5. Design card (stage 2)

One fixed-schema model call from the job's stored experiment design text, condition names
and per-omic column headers → `{perturbation, value, axis, baseline, relevant, columns}`. A
layer whose file has no header is marked *unlabeled* and timing claims on it are forbidden
until confirmed. Without a model, a deterministic card is derived from the headers.

## 6. Writer (stage 4), Verifier (5), sense check (6), Narrator (6b)

* **Writer**: an agent loop with `search_literature`, `read_paper`, `check_my_citations`
  (PubMed via `PubMedClient`) and `submit_statements`. Input: card, chain with readings and
  layer text, seen ledger, notes. 3–5 statements, each `{claim, prose, cites [[node, layer]],
  legs [eN], grounded_in [{leg, db}], beyond [{claim, paper N | "hypothesis"}]}`.
  **Grounding rule**: a drawn relation on the chain is cited as its leg and never searched;
  only what is built on top is searched and needs a paper or the word hypothesis.
* **Verifier** (code): every cite resolves to a chain node with that layer measured; a value
  used as evidence carries the relevant flag; the direction against the edge sign, with
  disagreement stated; every beyond claim has a paper or is a hypothesis. Failures go back to
  the Writer; three waves, then drop and list.
* **Sense check** (one model call, fixed schema): per statement, five verdicts — direction,
  timing (effect after the perturbation, not at baseline), contrast in design, layers stated
  (a sign-flipping series is noise), literature. A failure returns to the Writer once, then
  the statement drops.
* **Narrator** (one model call, fixed schema): a Results section — title, one-sentence
  summary, paragraphs in walk order, each sentence tagged with its statement and legs; *link*
  sentences may say only what a drawn edge or the card says. Code checks: legs resolve, every
  quoted value verbatim in the leg's layer text, every kept statement covered, dropped
  statements absent, link sentences without values or papers, 250–450 words (600–900 on the
  network). One repair, then the story is dropped and the statements stand alone.

## 7. The sealed record

```
{ schema: 1, job, scope, graph: {source, N, K, nodes, edges}, params, design_card,
  ranked: [...], plan, chain: [{n, kind, from, to, edge{db, pathway, subtype, sign, dir},
  reading, reason}], seen: [{id, r, heat, after_legs}], notes, stop_reason, stop_reading,
  statements: [{..., verifier, sense}], dropped: [{claim, why, by}], results: {...},
  papers, model_used, timings }
```

Written as JSON under `CLIENT_TMP_DIR/walks/`; a servlet route stores it on the AI record in
the next PR.

## 8. Evaluation (this PR: Test 1; Tests 2 and 3 are harness-ready)

* **Test 1, planted-module recall** (code only): on a real graph, grow a connected module of
  m nodes, set r = 1 on it with probability 0.9 and elsewhere at rate q; run the greedy
  policy; measure seed hit, recall, precision. Grid m ∈ {6, 12}, q ∈ {0.10, 0.33, 0.50}.
  Pass target: recall ≥ 0.7 at q = 0.33, m = 12; seed hit ≥ 0.9.
* **Test 2, ablation** (random / greedy / model, same seeds and budgets) and **Test 3**
  (counterfactual sign flip and label shuffle on a finished chain) reuse the record and the
  policies; they need model calls and are run by hand.

## 9. Files

`PaintomicsServer/src/classes/AIInterpret/walker/`: `network.py`, `overlay.py`, `heat.py`,
`walk.py`, `policies.py`, `card.py`, `writer.py`, `verify.py`, `sense.py`, `narrate.py`,
`record.py`, `report.py`, `evaluate.py`, `cli.py`. Tests: `src/tests/test_walker_*.py`
(offline, synthetic fixture; the example-data run is opt-in via a job id). Docs:
`docs/` page for the walker.

## 10. Decisions taken (the twelve on the artifact)

1 universal network at install · 2 one `scan` for seeds and look-around, sep 1/2 · 3 the agent
plans seeds and steps under a code ceiling (40/120) · 4 regulators are nodes only with an
identity · 5 hot r=0 walkable, never a seed · 6 universal walk at report time and in chat via
`walk` · 7 editable design card, unlabeled columns block timing claims · 8 grounding: drawn
relations cited, beyond claims searched · 9 sense check rewrites once then drops · 10 pathway
walk includes the job's regulators · 11 the Narrator writes only from kept statements ·
12 the ablation is a ship gate.
