# Five checks before render — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every walk interpretation carries five gate verdicts computed before it is sealed, the client renders Results and statements only when all five pass, and an offline harness measures the five checks five times at production temperatures and reports ranges.

**Architecture:** the gates are code (tiers, currency, structure null, title lexicon, MeSH context, anchor distances) plus two short schema-constrained model calls (the claimed direction of a statement, and its sign-flipped counter-hypothesis); everything hangs off `walker/service.run`, which already owns the pipeline, and lands in `record.checks`. The client reads `view.checks.gates` and `view.checks.rendered`.

**Tech Stack:** Python 3.11 (server, tests as flat `unittest` modules run as `python -m src.tests.<name>`), OpenAI Agents SDK for the Writer/walker loops (unchanged), `LLMClient.complete_json` for the schema calls, ExtJS-era plain JS + jQuery on the client, PubMed E-utilities through `PubMedClient`, OmniPath and TRRUST over HTTPS at install time only.

**Spec:** `docs/superpowers/specs/2026-09-15-five-checks-before-render-design.md`

## Global Constraints

- Tests are flat `src/tests/test_*.py` files with a `main()`/`unittest.main()` entry, offline, no pytest; run one with `cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_tiers`.
- Every top-level definition under `src/classes/AIInterpret` must be read from somewhere outside the tests (`test_tool_descriptions` orphan gate); no new `SYSTEM_PROMPT*` constants nobody sends.
- ruff E9+F clean; vulture ≥ 80 % zero findings; ≥ 60 % no new rows beyond `scripts/ci/vulture_baseline.txt` (refresh only in the commit that removes code).
- Edited JS/CSS bumps its `?v=` in `PaintomicsClient/public_html/index.html`; verify in Chrome (server on port 8790 from this worktree, `SERVER_PORT_NUMBER=8790 SERVER_ALLOW_DEBUG=true`).
- Model output is inserted as text nodes, never as markup.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File structure

| file | responsibility |
|---|---|
| `common/KeggGraph/parser.py` | `Edge` gains `via` (KEGG compound id of a `compound` relation's mediator, else `None`) |
| `walker/tiers.py` | `CURRENCY`, `edge_tier(edge)`, `statement_tier(stmt, chain)`, `MECHANISTIC_RE`/`ASSOCIATIONAL_RE`, `mechanistic_verbs(text)`, `title_outruns_body(results, kept, walker, anchor)`, `neutral_title(scope_name, card)` |
| `walker/null.py` | `permute_flags(overlay, rng)`, `modules_found(walker)`, `structure_null(graph, overlay, params, real_walker, k, seed)` |
| `walker/regulators.py` | `PANEL` rows, `panel_gene(label_or_member)`, `implied_direction(row, value_sign)` |
| `walker/direction.py` | `data_direction(layer)`, `claimed_direction(client, stmt, pathway, layer_text)`, `direction_check(client, statements, walker, card_text)` → verdicts + objections |
| `walker/anchor.py` | `resolve_anchor(card, network, job)`, `load_tf_targets(org_dir)`, `add_anchor_edges(graph, network, anchor, rows)`, `distances(network, anchor)`, `label_segments(walker, dist)`, `reachability(walker, dist)`, `target_enrichment(walker, targets, measured)`, `decoy_for(anchor, rows, network)` |
| `walker/simulate_ko.py` | builds the simulated knockout dataset files + manifest entry |
| `walker/harness.py` | the five-check harness and its Markdown report |
| `AdminTools/omnipathInstaller.py` | `--tf-targets`: CollecTRI (organism + human by symbol) and TRRUST → `mapping/tf_targets.tsv` |
| `AIInterpret/pubmed_client.py` | records carry `mesh` and `pub_types` |
| `walker/literature.py` | `paper_context(paper)`, `context_match(card, ctx)`, `context_line(ctx)` |
| `walker/card.py` | schema + deterministic card gain `perturbed_genes`, `perturbation_direction`, `system`, `organism` |
| `walker/service.py` | wires the gates: `run_gates(...)`, `checks["gates"]`, `checks["rendered"]`, `checks["anchor"]`, view additions |
| client | header line, gate chips, module distances, tier chips, context chips, blocked view |

## View schema additions (contract for the client)

```json
"checks": {
  "results": [...], "sense": ...,
  "rendered": true,
  "anchor": {"gene": "Ikzf1", "node": "g:22778", "direction": "up", "in_graph": true, "source": "CollecTRI-human,TRRUST"},
  "gates": {
    "artifact":  {"pass": true, "k": 50, "real": {"modules": 3, "seed_heat": 2.9}, "null_mean": {"modules": 0.6, "seed_heat": 1.1}, "p_modules": 0.02, "p_heat": 0.02, "currency_legs": 0, "why": ""},
    "title":     {"pass": true, "verbs_found": ["drives"], "rewritten": false, "fallback": false, "not_applicable": false, "why": ""},
    "direction": {"pass": true, "checked": 4, "consistent": 4, "insensitive": 0, "dropped": 0, "why": ""},
    "context":   {"pass": true, "cited": 12, "in_context": 9, "qualified": 3, "unqualified_other": 0, "why": ""},
    "anchor":    {"pass": true, "not_applicable": false, "reachability": 0.62, "baseline_reachability": 0.21, "target_p": 0.003, "why": ""}
  }
},
"segments": [{"seed": "g:...", "label": "Foxo1", "first": 1, "last": 6, "distance": 1}],
"statements": [{"...": "...", "tier": "mechanism"}],
"papers": {"1": {"...": "...", "context": {"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro", "match": {"organism": "same", "system": "same"}}}}
```

---

### Task 1: `Edge.via` in the KGML parser

**Files:** Modify `PaintomicsServer/src/common/KeggGraph/parser.py`; Test `src/tests/test_kegg_parser_via.py`.

**Interfaces:** `Edge = namedtuple("Edge", "a b kind subtype pathway reversible via")`; `via` is the KEGG compound id (`C00002`) for a relation whose subtype list holds `compound`, else `None`. `parse_pathway` resolves the subtype `value` (an entry id) through the pathway's entries.

- [ ] Test: a KGML with `<relation entry1="1" entry2="2" type="ECrel"><subtype name="compound" value="3"/></relation>` and entry 3 `name="cpd:C00002"` yields an edge with `via == "C00002"`; a PPrel activation yields `via is None`; every other field unchanged.
- [ ] Implement; run `test_kegg_parser_via`, `test_kegg_graph*` (existing hub tests) to confirm the namedtuple change breaks nothing.
- [ ] Commit.

### Task 2: `walker/tiers.py`

**Interfaces:**
```python
CURRENCY = frozenset({"C00001", "C00002", ...})          # §2 of the spec
def is_currency(node_id) -> bool                            # "c:C00002" → True
def edge_tier(edge: dict) -> int                            # 1 or 2 from edge["db"], edge["subtype"]
def statement_tier(stmt: dict, chain: list[dict]) -> str    # "mechanism" | "association"
def mechanistic_verbs(text) -> list[str]                    # matches in reading order, passive-with-agent included
def title_outruns_body(results, kept, walker, anchor) -> list[str]   # objections, [] passes
def neutral_title(scope_name, card) -> str
```
Tier rules: db `anchor` → 1; db `job` (miRNA) → 2; KEGG subtype: first signed name in `SIGN_BY_SUBTYPE` plus `phosphorylation`, `dephosphorylation`, `ubiquitination`, `glycosylation`, `methylation` → 1 unless the list also holds `indirect effect`, `binding/association` or `compound` → 2; `rn:` reaction edges (enzyme–metabolite) → 1; Reactome `reaction`/`inhibition` → 1; OmniPath `stimulation`/`inhibition` → 1, `unsigned` → 2; anything else → 2.

- [ ] Tests: each rule above; `statement_tier` mechanism only when all step legs are tier 1 and at least one exists; verbs: "drives" yes, "is induced by Ikaros" yes, "is induced" no, "rises" no; `title_outruns_body` on a hand-built results/kept pair for each of the four objection kinds in spec §4; `neutral_title` ≤ 60 chars of perturbation.
- [ ] Implement, run, commit.

### Task 3: currency and tiers in the network and the walk

**Files:** `walker/network.py` (`CACHE_VERSION = 4`; `_add_kegg` skips an edge whose `edge.via in CURRENCY`), `walker/walk.py` (`neighbour_rows` skips `is_currency(w)`; `_edge_record` adds `"tier": edge_tier(...)`), `walker/heat.py` (`scan_graph` never flags a currency node; accepts `dist=None` and adds `row["dist"]`).

- [ ] Tests in `test_walker_tiers.py`: fixture KGML gains an ECrel through `cpd:C00002` and one through `cpd:C00022`; the first is absent from the network, the second present; a walk on a graph with a currency compound never steps onto it; every step leg records `edge.tier`.
- [ ] Commit.

### Task 4: `walker/null.py`

```python
def permute_flags(overlay, rng) -> Overlay        # copy with r permuted over measured nodes, heat recomputed, K kept
def modules_found(walker, overlay) -> int         # segments (or the whole chain when no segments) with ≥ 3 relevant walked nodes
def seed_heat(walker, overlay) -> float           # mean heat of plan seeds
def structure_null(graph, overlay, params, real_walker, k=50, seed=0) -> dict   # spec §3 gate dict
```
- [ ] Tests on the fixture: permutation keeps K; a planted module (evaluate.grow_module + p_in 1.0) gives p_modules ≤ 0.1 with k=20; shuffled flags give p ≈ uniform (no assertion beyond range [0,1] and keys).
- [ ] Commit.

### Task 5: card anchor fields

**Files:** `walker/card.py`, `walker/service.py` (`CARD_FIELDS` gains the three; `clean_card` accepts `perturbed_genes` as a comma list).
- `CARD_SCHEMA.properties` += `perturbed_genes: {type: array, items: string}`, `perturbation_direction: {enum: up, down, unknown}`, `system: string`.
- `deterministic_card` gains `perturbed_genes` from `find_perturbed_genes(design_text, aliases)` where `aliases` is `{alias.upper(): symbol}` built by `anchor.alias_map(org_dir)` from `kegg2genesymbol.list` (every comma-separated name before `;`), and `perturbation_direction` from the verb nearest the gene (`PERTURB_UP_RE`, `PERTURB_DOWN_RE`).
- `card_text` prints `perturbed genes` and `direction`.
- [ ] Tests: "Ikaros induced by tamoxifen in B3 cells" → `["Ikzf1"]`, `"up"`; "Pten knockout" → `["Pten"]`, `"down"`; no gene → `[]`, `"unknown"`.
- [ ] Commit.

### Task 6: `walker/anchor.py` and the anchored walk

```python
def alias_map(org_dir) -> dict[str, str]
def load_tf_targets(org_dir) -> list[dict]          # rows of mapping/tf_targets.tsv: tf, target, sign, source, references
def resolve_anchor(card, network, org_dir) -> dict | None   # {"gene", "node", "direction", "in_graph", "source"}
def add_anchor_edges(graph, network, anchor, rows) -> int     # adds g:<anchor> and tier-1 edges tagged anchor:<source>; returns edges added
def distances(network, anchor_node) -> dict[str, int]          # BFS, hubs and currency passable
def label_segments(walker, dist) -> None                        # segment["distance"]
def reachability(walker, dist, radius=2) -> float
def target_enrichment(walker, dist, targets, measured) -> dict  # {"walked", "hits", "p"} hypergeometric
def decoy_for(anchor, rows, network, rng) -> str | None         # a TF with a regulon within ±30 % of the anchor's, not the anchor
```
`service.build` gains `anchor` handling: after the overlay, `resolve_anchor`; if in `tf_targets` add edges to `graph` **and** to the copy of `network` used for distances; recompute heat (`overlay_job` is called after the edges exist: restructure `build` so anchor edges are added before `overlay_job`). `sdk.PLANNER_INSTRUCTIONS` and the planner kickoff name the anchor and say to start there; `parallel.code_plan` orders candidates by `(dist, -heat)` when `walker.dist` is set; `Walker` gains `dist: dict | None` and `scan` prints `d=<n>`.
- [ ] Tests on the fixture with a `tf_targets.tsv` naming `Aaa` as TF of `Ggg` (unmeasured, not in pathway one): anchored run adds the edge, distances put `Ggg` at 1, segments get distances, reachability in [0,1], enrichment p in (0,1], decoy chosen ≠ anchor.
- [ ] Commit.

### Task 7: `--tf-targets` in the installer

**Files:** `AdminTools/omnipathInstaller.py`; Test `src/tests/test_tf_targets_installer.py` (stubbed `_fetch` and `urlopen`).
- `fetch_tf_targets(taxid)` → CollecTRI rows for the organism; `fetch_tf_targets(HUMAN_TAXID)` mapped by `symbol.upper()` through the organism's symbol/alias table when taxid ≠ 9606; `fetch_trrust(code)` for `mmu`/`hsa` from `https://www.grnpedia.org/trrust/data/trrust_rawdata.{mouse,human}.tsv`.
- `write_tf_targets(code, rows, kegg_data_dir)` → `current/<code>/mapping/tf_targets.tsv` with header `tf\ttarget\tsign\tsource\treferences`, deduplicated on (tf, target, source), sign `+1/-1/0`.
- CLI: `--tf-targets` runs only this step; `install()` calls it too (failure is a warning, not an error).
- [ ] Test: stubbed bodies → the file has both sources, Ikzf1 rows present via the human table, signs parsed from the `True`/`False` strings.
- [ ] Run for real: `python src/AdminTools/omnipathInstaller.py --organism mmu --tf-targets` against `/Users/tianyuan/Desktop/github_dev/paintomics4_data/KEGG_DATA`; confirm Ikzf1 rows.
- [ ] Commit.

### Task 8: MeSH context in `pubmed_client.py` and `literature.py`

- `fetch_abstracts`/`fetch_papers` records gain `mesh: [descriptor names]` and `pub_types: [names]`.
- `literature.paper_context(paper) -> {"organism", "system", "scope"}`; `context_match(card, ctx) -> {"organism": same|other|unknown, "system": same|other|unknown}` where the card's `organism` is the species word for the job's code (`organisms.py` has the table) and `system` is matched by whole-word overlap between the card's system text and the paper's system headings (synonyms: B cell ↔ B-Lymphocytes, pre-B ↔ Precursor Cells, B-Lymphoid).
- `context_line(ctx)` → `"mouse · B-Lymphocytes · in vitro"`.
- [ ] Tests: a fixture XML with MeSH → fields; rules of spec §6 for scope; match same/other/unknown.
- [ ] Commit.

### Task 9: context and sentence-level pairs in the Writer and the Verifier

**Files:** `walker/writer.py` (`search_literature` sorts and tags hits; INSTRUCTIONS gain the context rule and the regulator rule), `walker/verify.py` (`citation_pairs` sentence-level; `verify_statement` gains the qualifier objection when `contexts` is given), `walker/literature.py` (store keeps `context` per paper), `walker/service.py` (papers carry `context`).
- [ ] Tests: `citation_pairs` returns the prose sentence carrying `[2]`; an *other*-organism citation without the organism word objects; with it passes; hits ordering.
- [ ] Commit.

### Task 10: `walker/regulators.py` and `walker/direction.py`

- `PANEL`: 39 rows `{"symbol", "class": "feedback"|"inhibitor", "pathway", "pmid"}`.
- `direction.data_direction(layer) -> int` (sign of the largest |value| of a relevant layer; 0 when no values).
- `direction.claimed_direction(client, stmt, pathway, layer_text) -> dict` (schema `{claimed: up|down|none, quote}`, temperature 0.1, max_tokens 200).
- `direction.direction_check(client, statements, walker, card_text) -> (verdicts: dict[n, dict], objections: dict[n, list[str]])` implementing spec §5 steps 1–4 (the flip uses `overlay.layer_text` with signs reversed for that node only).
- Wire in `service._model_walk` after the Writers: objections → `rewrite_once` → re-check → drop with `by: "direction check"`; `checks["gates"]["direction"]`.
- `writer.INSTRUCTIONS` and `sense.BRIEF` gain the two-line regulator rule.
- [ ] Tests with a stub client: a Cish-down statement claiming JAK-STAT up is objected; claiming down passes; a flip that does not change the claim is flagged insensitive; `none` passes.
- [ ] Commit.

### Task 11: the title gate in the Narrator stage

- `narrate.BRIEF` gains the tier rule and each kept statement's `tier` in the KEPT STATEMENTS JSON.
- `service._narrate`: after `checked()`, `tiers.title_outruns_body(...)`; objections join the one repair; if still failing, `neutral_title` + drop summary sentences with mechanistic verbs; `checks["gates"]["title"]`.
- [ ] Tests in `test_walker_gates.py` with a stub `narrate`.
- [ ] Commit.

### Task 12: the gates in `service.py` and the view

- `run_gates(...)` assembles `checks["gates"]` (artifact from Task 4 + currency legs, title, direction, context, anchor), `checks["rendered"] = all(pass)`, `checks["anchor"]`.
- Structure null runs right after the model walk (before the Writers), on `graph`/`ov`, k=50 (k=20 when the graph has > 5,000 nodes to stay under ~20 s).
- `view()` adds `checks.gates`, `checks.rendered`, `checks.anchor`, `segments` (with `distance`), `tier` on statements, `context` on papers.
- `record.SCHEMA = 2`.
- [ ] Tests: `test_walker_gates.py` runs `service.run` on the fixture with the greedy policy (no model) and asserts the five gate dicts exist with `pass` booleans and `rendered`; a chain with a currency leg (constructed) fails `artifact`.
- [ ] Commit.

### Task 13: client

**Files:** `PA_Step4WalkView.js`, `PA_AIInterpretView.js`, `graph-walk.css`, `index.html` (`?v=` bumps).
- `paWalkHeaderNode(view)`: "Perturbation: Ikzf1 induced (up) · anchor in the network" or "no perturbed gene named in the design".
- `paWalkGatesNode(view)`: five chips (`pass`/`fail`/`n/a`), title attribute with the numbers, a `<p class="pa-walk-gate-why">` per failure.
- `renderDone`/`displayWalk`: header, gates, then Results + references + statements only when `view.checks.rendered`; else a note "Not rendered: <failed gates>"; the walk details stay.
- Plan/legs: `segments[].distance` shown as "d=1 from Ikzf1" beside each seed; statement tier chip; reference context chips.
- [ ] Verify in Chrome on port 8790 with a real walk (greedy policy view via the CLI-produced record is not enough: run one model walk on `Ku5jMVCL6z pathway:mmu04068` through the UI).
- [ ] Commit.

### Task 14: simulated knockout dataset

- `walker/simulate_ko.py build(network, org_dir, out_dir, gene="Pten", seed=0)`: gene expression (Ensembl ids through `mapping/ensembl_mapping.list`) for every network gene with a symbol; Pten −2.5 at every column; sign propagation two steps along signed edges (activation copies the sign, inhibition flips it, decay 0.6 per step); noise N(0, 0.35); relevant = |value| > 1 at the last column; six columns `KO/WT_0h … 24h`.
- Writes `examplefiles/datasets/13-simulated-pten-knockout/data/{gene_expression_values,gene_expression_relevant}.tab` and adds the manifest scenario (`simulated: true`, organism mmu, pipeline pathway-acquisition, experiment design text "Pten knockout in mouse, six time points, log2 KO over WT").
- [ ] Load it as a job through the running server (example picker) and note the job id.
- [ ] Commit.

### Task 15: the harness

- `walker/harness.py`: `run_five(job_id, out_dir, repeats=5, permutations=20, pathway="pathway:mmu04068", decoy=None)`; each run's record saved under `out_dir/<check>/<label>_<i>.json`; skip a run whose file exists (resume); `report(out_dir) -> str` Markdown with min–max per metric and PASS/FAIL per check.
- `cli.py --five-checks`.
- Direction panel cases from `benchmarks/tellme/direction_cases.json`; context labels from `benchmarks/tellme/context_labels.json`; constructed pairs from `benchmarks/tellme/context_pairs.json`; Ikaros targets from `benchmarks/tellme/ikaros_targets.json`.
- [ ] Run on `Ku5jMVCL6z` and on the simulated job; write `docs/dev/tellme-five-checks.md`.
- [ ] Commit.

### Task 16: docs, changelog, sweep, PR

- `docs/ai-graph-walk.md` section "Five checks before it is rendered"; `docs/ai-interpretation.md` one paragraph; `CHANGELOG.md` Unreleased/Added.
- `scripts/ci/run-unit-tests.sh` full offline sweep on 3.11; ruff; vulture gate; eslint; `mkdocs build --strict`.
- PR, Gate, review, resolve, merge (CLAUDE.md §7), then deploy (spec §10).
