# The full-agent interpretation mode (`AI_FULL_AGENT=1`)

Two arms of the AI interpreter now live side by side. They share the servlet,
the queue, the DAO contract, the progress statuses the widget polls, and the
verification gate — they differ only in **who decides what happens next**.

| | workflow arm (default) | full-agent arm (`AI_FULL_AGENT=1`) |
|---|---|---|
| module | `agent.py::_run_async` | `agent_loop.py::_run_loop_async` |
| control flow | six fixed phases in code | one Lead Interpreter tool loop |
| pathway depth | same treatment for every pathway | the agent chooses where to go deep |
| literature | planner writes ≤12 queries up front | agent searches, reads, re-searches |
| exit | phase 6 runs after phase 5b | `submit_report` → the same gate |
| state | phase-local | a notebook + tool trace in Mongo |

Design drawing: `docs/diagrams/paintomics-ai-agent-proposal.drawio(.png)`. The
four-page map of the *current* framework is `paintomics-ai-framework.drawio`.

## What the loop is allowed to do

The Lead Interpreter gets thirteen tools (`agent_loop.TOOLBELT`) that wrap the
existing modules: `get_experiment_overview`, `get_pathway_details`,
`get_gene_profile`, `compare_gene_profiles`, `cluster_pathways`,
`search_literature`, `read_paper`, `notebook_write`, `notebook_read`,
`check_my_citations`, `delegate_interpretation`, `delegate_literature`,
`submit_report`.

The division of labour is deliberate and is the whole reason the loop is safe
to run against user data:

* **The agent chooses WHAT; the tool enforces HOW MUCH.** The search spend
  meter, the PubMed rate limit, the paper caps and the AgentEvolve retrieval
  guard all live inside the tools. Every tool result ends with a budget line
  (`[budget: 11 searches left · 240 s left · …]`) so the model is *told* its
  remaining budget instead of being trusted to count.
* **`submit_report` is the only door out**, and it opens into the mandatory
  exit gate — never straight to the user.
* **The gate is outside the loop**: quote collection, canonical reference
  rendering, the per-citation Claim Verifier pass, then the deterministic net
  (`verify_report_v2` → `redact_unverified_v2` → `renumber_citations` →
  `sort_references_section`). No decision the agent makes can skip it.
  `test_reference_section_ordering` pins that sequence in *both* modules.
* **Backstops are not the agent's choice**: turn cap, tool-output character
  ledger, wall-clock deadline, cancel flag. Hitting one is recorded in
  `stats["loop_backstop"]`; a loop that ends without submitting gets one
  bounded synthesis from its notebook and sets `stats["forced_synthesis"]`.

## Settings

| variable | default | meaning |
|---|---|---|
| `AI_FULL_AGENT` | `0` | `1` routes `run_ai_agent` to the loop |
| `AI_AGENT_MAX_RUN_SECONDS` | `600` | whole run, loop + gate |
| `AI_AGENT_GATE_RESERVE` | `240` | seconds reserved for the gate; the loop gets the rest |
| `AI_AGENT_MAX_TURNS` | `24` | Decide turns |
| `AI_AGENT_SEARCH_BUDGET` | `18` | `search_literature` calls per run |
| `AI_AGENT_TOOL_CHAR_BUDGET` | `400000` | total characters of tool output the loop may consume |
| `AI_AGENT_DELEGATE_WORKERS` | `4` | parallel single-shot calls inside one `delegate_*` |
| `AI_AGENT_VERIFY_ITERATIONS` | `2` | verify→correct rounds at the gate (capped by `AI_MAX_VERIFICATION_ITERATIONS`) |

Why these numbers: measured on the CSIC gateway, short single-shot calls
parallelise (32 concurrent in 5.6 s) while tool loops and long generations
serialise. So the loop keeps Decide turns terse, every fan-out is single-shot,
and exactly one long-form generation (the report) happens inside it. The
600 s ceiling minus the 240 s gate reserve is what the loop actually gets.

## New per-job state

`aiInterpretationCollection` gains two fields on an agent run:

* `toolTrace` — the last 200 tool calls (`seq`, `t`, `tool`, `args`, `result`,
  `ms`), written through `AIInterpretDAO.append_tool_event`. This is the feed a
  frontend activity panel reads, and the telemetry the benchmark scores search
  spend from.
* `notebook` — the agent's findings journal.

Neither is read by the current client, so the UI is unchanged until a panel is
built for them.

## Running the two arms against each other

The comparison needs a **fresh job per run** (the job cache swallows reuse) and
the STATegra example is the reference dataset:

```bash
# one job per run, through the running server, the way the browser does it
python - <<'EOF'
from src.benchmarks.bench_http import Client, _selectedCompounds
client = Client("http://localhost:8000")
step1 = client.post("/pa_step1/example/stategra-multiomics")
jobID = step1["jobID"]; s1 = client.waitForJob(jobID, "step1")
sel = _selectedCompounds(s1.get("matchedMetabolites", []))
client.post("/pa_step2", data=[("jobID", jobID)] + [("selectedCompounds[]", c) for c in sel])
client.waitForJob(jobID, "step2"); print(jobID)
EOF

# then run one arm in-process on that job
AI_FULL_AGENT=1 AI_AGENT_MAX_RUN_SECONDS=600 python -c "..."   # agent arm
```

Pre-register the decision rule before running: one fold cannot measure a
change on this gateway (same-agent replicates have swung by 0.27 on the
AgentEvolve fold score), so interleave arms, use at least two replicates each,
and treat any agent replicate that errors or overruns while the workflow arm
completes as a loss regardless of the other numbers.

## Tests

```bash
cd PaintomicsServer
python -m src.tests.test_ai_agent_loop_endtoend     # the loop, scripted gateway, real PubMed
python -m src.tests.test_ai_agent_endtoend          # the workflow arm, unchanged
python -m src.tests.test_reference_section_ordering  # the gate sequence, both modules
```

The loop's end-to-end test scripts the gateway into a real tool-calling
investigation (overview → search → notebook → submit) and asserts that the
*submitted* draft is what reached the gate, that `toolTrace` and `notebook`
were persisted, and that the references were rebuilt or redacted. A stub whose
quotes cannot be found in the real papers is *supposed* to lose its citations
at the gate — that is the guard working, and the test asserts the uncited prose
survives.

## Regenerating the diagrams

Only the editable `.drawio` sources are committed (plus one rendered PNG of the
proposal). Re-export any page with the draw.io desktop CLI:

```bash
cd docs/diagrams
drawio -x -f png -e -s 2 --page-index 1 -o ai-1-architecture.drawio.png \
    paintomics-ai-framework.drawio     # pages 1..4 = architecture, phases, lifecycle, citations
drawio -x -f pdf -a --crop -e -o paintomics-ai-framework.pdf paintomics-ai-framework.drawio
```

`-e` embeds the diagram XML in the export, so a PNG round-trips back into
draw.io. draw.io's CLI truncates the IEND chunk of `-e` PNGs; repair with the
`repair_png.py` helper from the drawio skill if a viewer rejects one.

## Activity feed: the states, verified in the browser

The feed under the progress bar is driven by `toolTrace` from
`ai_interpret_status`. Four states, checked in Chrome against the running app:

| state | what the reader sees |
|---|---|
| workflow arm (no `toolTrace` in the response) | nothing -- the list stays hidden |
| agent arm before its first tool call (empty array) | nothing |
| agent arm running | up to six rows, newest last, plus a total when there are more |
| status `done` | the whole progress block hides, feed with it |

The empty case matters: the shipped workflow arm writes no tool trace, so its
runs show a progress bar and no feed. That is correct, not a broken feed.

Long arguments truncate with an ellipsis inside the 380 px panel -- measured, the
row's scroll width equals the list's client width and the page does not scroll
sideways.
