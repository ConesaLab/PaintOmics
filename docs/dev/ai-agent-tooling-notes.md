# Tool usefulness in the agent arm — measured, not assumed

The design argument for the full-agent arm is that the agent chooses WHAT and the
tools enforce HOW MUCH. That only holds if the toolbelt is judged the way any
other interface is: by what callers actually do with it. This file is the running
record.

## How to get the data

Every tool call is archived to `CLIENT_TMP/ai_traces/<jobID>-<loopStart>.jsonl`
(one file per run, `seq`, `t`, `tool`, `args`, `result`, `ms`). The DAO's
`toolTrace` field keeps only the **current** run capped at 200 events, because
that is what a UI needs — twelve benchmark runs left two surviving traces, and
four tools sat unjudged as a result. The archive exists for the other question.

```bash
python - <<'PY'
import glob, json
from collections import Counter, defaultdict
calls, ms = Counter(), defaultdict(list)
for f in glob.glob("<CLIENT_TMP>/ai_traces/*.jsonl"):
    for line in open(f):
        e = json.loads(line); calls[e["tool"]] += 1; ms[e["tool"]].append(e["ms"])
for t, n in calls.most_common():
    v = sorted(ms[t]); print("%-26s %4d calls  median %6d ms" % (t, n, v[len(v)//2]))
PY
```

## First measurement (rounds 1–6, the two traces that survived)

| tool | calls | median ms | reading |
|---|---|---|---|
| `search_literature` | 18 | 1 885 | the workhorse |
| `delegate_interpretation` | 6 | **23 626** | 142 s of a 450 s loop budget for six calls |
| `read_paper` | 5 | 3 202 | |
| `notebook_write` | 6 | 4 | free |
| `get_pathway_details` | 4 | 0 | free, and **under-used** — 2 per run |
| `get_experiment_overview` | 2 | 79 | once per run, as intended |
| `cluster_pathways` | 2 | 411 | once per run, as intended |
| `check_my_citations` | 2 | 6 | free, used just before submitting |
| `submit_report` | 2 | 0 | the door |
| `get_gene_profile` · `compare_gene_profiles` · `notebook_read` · `delegate_literature` | 0 | — | not called *in these two runs* |

## What this changed in the tools

1. **Costs are now in the descriptions.** A 23.6 s tool and a 0 ms tool looked
   identical to the model. `delegate_interpretation` now says it costs ~25 s and
   is the costliest call available; `search_literature` ~2 s; `read_paper` ~3 s;
   the four instant tools say they are free. An agent cannot budget what it
   cannot see, and the ledger line only reports searches, seconds and characters.
2. **The free data tools now invite use.** `get_pathway_details` costs nothing and
   was called twice a run while the agent theorised from the overview; its
   description now says to read the data before theorising and to ask for several
   pathways at once.
3. **`read_paper` states its purpose, not just its mechanics** — an uncited-but-read
   paper is cheap, an unread-but-cited one is what the verifier removes.

## Second measurement — four archived runs, 62 calls (`python -m src.benchmarks.ai_tool_usage`)

| tool | calls | runs using it | median ms |
|---|---|---|---|
| `search_literature` | 20 | **4/4** | 1 673 |
| `notebook_write` | 7 | **4/4** | 2 |
| `delegate_interpretation` | 7 | 2/4 | **28 620** |
| `read_paper` | 9 | 2/4 | 2 263 |
| `get_experiment_overview` · `get_pathway_details` · `cluster_pathways` · `submit_report` | 4 each | **4/4** | 0–420 |
| `check_my_citations` | 2 | 2/4 | 15 |
| `compare_gene_profiles` | 1 | 1/4 | 9 |
| `get_gene_profile` · `notebook_read` · `delegate_literature` | 0 | 0/4 | — |

**`read_paper` payoff: 5 papers opened, 2 cited in the shipped report (40 %).**
Reading is neither decorative nor decisive — worth its 2.3 s, not worth making
mandatory. This is measurable only because the trace now records `pmid=`:
`renumber_citations` rewrites every `ref_index` at the gate, so PMIDs are the
only key that survives into the stored papers.

### What this changed

- **Thirteen tools became eleven.** `get_gene_profile` was
  `compare_gene_profiles` with one argument (my own rule above: if two tools
  differ only by arity, that is one tool), and `notebook_read` re-read what the
  SDK already keeps in the conversation. Both went uncalled in every archived
  run, but the reason to remove them is structural — each cost its schema in
  **every** Decide turn of every run.
- **Delegation latency is per CALL, not per pathway** — 28.6 s median whether it
  covers three pathways or ten, and two runs spent ~100 s on 3.5 calls each. The
  description now says covering ten pathways in one call costs what three would.
- `delegate_literature` stays for now: uncalled in these four runs, but earlier
  rounds did use it, and four runs is not enough to retire a tool that has been
  used.

### A trap this nearly caused

Round 6's two surviving traces showed `compare_gene_profiles` as never called;
round 7 called it. Removing a tool on two traces would have been wrong, and it is
exactly why the archive exists. **Adoption needs runs, not calls.**

## Third measurement — the gate, which was the biggest cost and invisible

Gate-side LLM calls are now archived as `gate: True` events. The first numbers
settled a question that had been costing citations for the whole experiment:

| verifier | per call | verdict returned? | redactions it caused |
|---|---|---|---|
| tool loop (`search_paper_text` + `fetch_paper_section`) | 45 s hedge timeouts, ~90 s worst | **9 of 14 failures were "Max turns (6) exceeded"** | 12–14 per run |
| prefetched, tool-less | **median 2 464 ms** | **29 of 29** | 2 per run |

29 prefetched calls returned 27 `match=True supports=True` and 2 genuine
refutations — and the run's redaction count was exactly 2. **Ten of the twelve
redactions in the previous configuration were a verifier that never reached a
verdict, not a citation that failed.** Verify-loop wall clock fell 291 s → 117 s
and the whole run 485 s → 338 s.

The lesson generalises past this arm: **if a tool loop exists only to fetch
something a function could fetch, it is not agency, it is latency and a failure
mode.** Locating a quote in a paper is `str.find` with a fuzzy fallback; judging
whether it supports a claim is the part that needs a model. Splitting those two
made the check faster, more reliable, and cheaper.

The same `Max turns (6) exceeded` warning appears in the **workflow arm's** logs,
so its shipped reports lose citations the same way. That fix belongs in its own PR.

Also measured, ending two suspicions: the quote-collection phase costs **3 s**,
not the tens of seconds assumed (it is not worth optimising), and
`read_paper`'s payoff held at 40 % (5 opened, 2 cited).

## Open questions the archive will answer

- Are `get_gene_profile`, `notebook_read` and `delegate_literature` dead weight?
  Each unused tool still costs its schema in **every** Decide turn. Remove on
  evidence across runs, not on two traces.
- Does `compare_gene_profiles` subsume `get_gene_profile`? If so, one tool.
- Is `check_my_citations` doing anything? It reported "0 failed" before submitting
  in runs whose citations were then redacted at the gate — a check that always
  passes is worse than none (the same trap as the rubber-stamping verifier).

## Fourth measurement — 17 runs

| tool | calls | runs using it | median ms |
|---|---|---|---|
| `search_literature` | 92 | **17/17** | 1 699 |
| `notebook_write` | 41 | **17/17** | 3 |
| `get_pathway_details` | 20 | **17/17** | 0 |
| `get_experiment_overview` | 17 | **17/17** | 73 |
| `cluster_pathways` | 17 | **17/17** | 412 |
| `submit_report` | 16 | 16/17 | 0 |
| `read_paper` | 45 | 9/17 | 2 210 |
| `delegate_interpretation` | 22 | 9/17 | **29 020** |
| `check_my_citations` | 8 | 8/17 | 10 |
| `compare_gene_profiles` | 1 | 1/17 | 9 |
| `delegate_literature` | **0** | **0/17** | — |

`read_paper` payoff rose with the sample: **38 papers opened, 17 cited (45 %)**.
Reading is worth its 2.2 s and the prompt now says so.

Thirteen tools are now ten. `get_gene_profile` folded into
`compare_gene_profiles` (same tool, different arity); `notebook_read` removed
(the SDK already keeps the notebook in context); `delegate_literature` removed
(0/17, and `search_literature` covers it since the Lead learned to write broad
queries). `compare_gene_profiles` survives at 1/17 because it is free and is the
only gene-level view left — adoption is evidence about a tool, not a verdict on
a capability.

## The failure this framework kept repeating

Three separate defects this session were the same bug wearing different clothes:
**instrumentation that cannot report failure.**

1. **The Claim Verifier that could not lose.** Given `tools=` and `output_type=`
   together, vLLM's grammar made it answer before it could call a tool, so it
   returned `supports_claim=true` having read nothing. (Recorded in `agent.py`;
   it is why the verifier keeps tools and parses text.)
2. **`check_my_citations`, which always passed.** It ran `verify_report_v2` over
   a *draft*, and that function reads quotes out of a References section a draft
   does not have — so it answered "0 failed" in all eight runs that consulted it,
   including runs that then lost 12-17 citations at the gate.
3. **The silent branch.** The full-text upgrade recorded nothing when it did not
   fire, so two rounds of inference could not establish why. It now records its
   candidate counts including the zero case.

And a fourth, in the same family: the merge guard compared **markers** rather
than **grounded citations**, so a stitch that added thirty unquotable markers
passed the check and lost all thirty at the net.

## Fifth measurement — 28 runs, and read_paper does not do what I claimed

| question | answer |
|---|---|
| of papers OPENED, how many get cited? | 15 of 74 (**20 %**) — was 45 % at 8 runs |
| of papers CITED, how many were opened first? | 15 of 133 (**11 %**) |
| do read-backed citations verify better? | **no**: 78 % pass vs 84 % for never-read |
| runs that delegate at all | **15 of 29 (52 %)** |

The middle two are the ones that matter. `opened -> cited` alone cannot separate
"reading is useless" from "reading correctly rejected the paper" — rejecting a
source before it becomes an unquotable citation is the tool working. But
`cited <- opened` says reading is not on the path to a citation at all (11 %),
and the verification split says read-backed citations do **not** survive better.
So `read_paper` is marginal: fine to keep at 2.2 s, wrong to urge.

**The prompt has been corrected accordingly**, and the lesson is about prompts as
much as tools: it had been telling the agent "nearly half the papers opened that
way end up cited", a number I put there from eight runs which twenty-eight runs
more than halved. **Do not put a payoff figure in a prompt unless it is stable
enough to survive the next twenty runs** — the model believes it, and a stale
number is a confident instruction to do the wrong thing.

Delegation adoption is the other headline: only half of runs delegate at all, and
the ones that do not stitch nothing and ship a fifth of the prose. `submit_report`
now nudges exactly once on a thin undelegated draft.

## Rules for adding a tool here

- Say what it costs if it costs more than a few seconds; say it is free if it is.
- Enforce budgets inside the tool and report the remainder in the result.
- Return the reason for an empty result, not just the emptiness.
- If two tools differ only by arity, that is one tool.
- **A check that cannot fail is not a check.** Before shipping a guard, construct
  the input that must make it complain, and confirm it does.
- **A branch that says nothing when it does nothing cannot be debugged.** Record
  the zero case.
- **Measure the outcome, not the proxy.** Citations are markers; grounding is
  markers that survive quote collection and verification. Optimising the first
  moved the second in the wrong direction for six rounds.
- **If a tool loop only fetches what a function could fetch, it is latency and a
  failure mode, not agency.** Deterministic retrieval plus one judgement call
  beat the Claim Verifier's tool loop on speed (2.5 s vs 45 s timeouts) *and*
  reliability (29/29 verdicts vs 9-of-14 turn exhaustion) at once.

## The archive now records its own outcome (2026-08-18)

Each run stamps a `__outcome__` event as its last trace line: prose length,
surviving citations, redactions, papers, full-text papers, seconds. This closes
a gap that made one whole class of question unanswerable.

The question was "does calling this tool produce a better report?" -- the only
version of tool usefulness that matters, as against adoption (it gets called)
and cost (what the call takes). The first attempt joined traces to MongoDB and
came back **n=1**, because Mongo stores one interpretation per *job* and the
benchmark reuses two jobs for every run. Forty runs, two retrievable outcomes.
The archive is per run, so putting the outcome there makes the association
computable over the whole history from the next run on.

The stamp sits inside `try/except Exception`. It runs on the return path of a
run that has already spent ten minutes and its gateway budget; nothing it can
hit is worth discarding a finished interpretation for. A test asserts the guard
is there.

### read_paper, at n=28 runs

| citations that passed verification | rate |
|---|---|
| agent had opened the paper first | 185/252 (73%) |
| never opened | 413/490 (84%) |

Reading still does not raise the pass rate -- the gap has held, and widened
slightly, since it was first measured at n=8. The prompt no longer claims a
payoff for reading. What reading is *for* is rejecting a paper before it becomes
an unquotable citation, and that shows up as a lower cited-share, not a higher
pass rate.

### A test suite can silently skip a test

`main()` in the budget suite lists tests by hand. Twice in this loop a test was
added, the suite printed all green, and the count had not moved -- once through
an anchor that did not match, once through a list that holds references rather
than calls. A suite that skips a test reports confidence it has not earned, so
there is now a test that parses its own file and fails if any `test_` function
is not reachable from `main()`.

## The redactor was destroying the report it cleaned (2026-08-18)

`redact_unverified_v2` split the body on `(?<=[.!?])\s+` and rejoined with `" "`.
That split consumes the whitespace after a full stop, and in markdown that
whitespace is the newline before a heading or a bullet. One failed citation was
enough to delete any heading glued to a removed sentence, inline the rest into
prose, and collapse every list.

Both arms call it, so this damaged **shipped** interpretations, not just the
draft agent arm.

The cause was provable from the stored reports:

| stored reports | n | carried glued structure tokens | mean per report |
|---|---|---|---|
| with a redaction | 29 | **29 / 29** | 37.6 |
| with no redaction | 27 | **0 / 27** | 0 |

The client's `_preprocessMarkdown` had attributed this to the model -- "the model
routinely writes a SPACE where a newline belongs", verified against the stored
reports. The correlation is real; the direction was wrong. Redaction wrote the
space.

Redaction is now block-aware: headings, tables, fenced code and blank lines pass
through untouched, sentences are removed with their original separators intact,
and a heading whose section lost all its prose is dropped rather than left
standing over nothing. Rendered side by side in Chrome on stored report
`sv02V5dAE4`, the same redaction goes from 13 list items and 9 paragraphs to 46
and 31 -- the old output showed literal `*` markers in the middle of paragraphs.

14 tests pin it, 6 of which fail against the old implementation.

**The frontend shim stays.** Checked in the browser against all 27 clean reports:
the rules are structurally neutral on well-formed markdown, and exactly one
report changed -- an after-a-colon bullet the model really had glued. So the
model does do it, about a hundredth as often as the redactor did, and old
reports in the database still need recovering.

## Two tool-building defects found by measuring, not guessing (2026-08-18)

**A repeated delegation costs 30 s of a 600 s budget.** Over 60 archived runs,
`delegate_interpretation` was re-issued with identical arguments in 7 of them,
at 25-62 s each. That is 269 of the 271 seconds the agent spends re-answering
itself -- the overall duplicate-call rate is only 2%, and every other tool is
cheap enough that a repeat does not matter. The tool now caches per run on the
*resolved* pathway set, so a renamed request still hits, and returns the stored
analysis with the coverage ledger attached. A different focus still runs.

Note what this is not: the agent is not being forbidden anything. It asks, it
gets the answer, and it is told the answer is one it already had.

**A tool that raises looked like a tool nobody called.** The SDK catches tool
exceptions, hands the model "An error occurred while running the tool", and
carries on -- and because `_trace` runs at the end of a tool, a raise left no
trace event at all. So every adoption and cost figure in this document counts
successful calls only, and a tool broken on every call would have been read as
one the agent declined to use.

That is not hypothetical. The first version of the delegation-cache tests passed
against a fixture that raised `KeyError` on every single call, because the
swallowed error came back as an ordinary string and the assertions were about
the string.

Each tool now passes a `failure_error_function` that records the failure in the
run journal -- so it reaches the frontend activity feed and the archive -- and
tells the model what broke and not to repeat it unchanged. The analyzer prints a
failures section, and an AST test asserts every tool has a handler named for
itself, so a copy-paste cannot file one tool's failures under another.

Retained logs show no swallowed failures in the 60 archived runs, but those logs
do not go back far enough to be evidence of absence. From here it is recorded.

## check_my_citations is the most useful tool in the belt (2026-08-18)

Adoption and cost said little about it: 41 calls across 28 of 53 runs, 1.3 s
each. The traces say what those numbers cannot -- what happened next.

| runs that called it | | |
|---|---|---|
| re-checked after a bad result | 10 | **all 10 improved, none got worse** |
| checked once | 18 | some submitted with flagged citations still in place |

The improvements are large and consistent: 11/6 -> 7/0, 14/7 -> 8/0,
10/4 -> 10/0, 6/2 -> 4/0, 8/2 -> 7/0. Across every check, 51 of 219 citations
(23%) had no supporting quote when first written; the tool finds them for 1.3 s
where the gate finds them for a redaction that deletes the sentence too.

It was never the last call in a run -- the agent always did something with the
answer. So this is a tool that works, whose only failure mode is not being run
twice.

Two changes followed, both from the measurement:

**Its own description discouraged the thing that works.** It said "worth running
once on your finished draft". It now says to run it, fix what it names, and run
it again, because the second run is where the grounding comes from. No number in
the prompt -- numbers there have gone stale before.

**submit_report asks once when the agent submits citations its own check
flagged.** The check remembers what it flagged; if those markers are still in
the draft at the first submit, the agent is asked once, by index, with the
remedies the tool already suggests. The second submit is always accepted, and
this shares the single nudge with the delegation one -- only one question per
run, ever. A tool that can refuse twice is a workflow step wearing a tool's
clothes.

## Where the ten minutes actually goes (2026-08-18)

Measured over the archived runs, by subtracting traced work from wall clock:

| | share |
|---|---|
| tool execution | 32% |
| the exit gate | 18% |
| **the Lead's own model turns** | **50%** |

Half the run is the agent thinking between calls, which is not something a cache
or a bound can reclaim. Two things follow.

**A hypothesis that turned out to be wrong.** Every Decide turn re-sends the
whole conversation, so I expected thinking time to grow as context accumulated,
which would have made big tool outputs expensive far beyond their own latency.
It does not: median gap by turn bucket runs 0.4, 1.0, 1.6, 0.3, 0.1 s. Whatever
governs turn latency here, it is not context length, and tool-output size is not
the lever it looked like.

**The gap after a submit is the biggest single event in a run:** median 58 s,
mean 69 s over 47 of them. That is the agent rewriting a ten-thousand-character
report after a nudge. So a nudge is not free advice -- it costs about a minute.

Both nudges now check the clock first (`NUDGE_MIN_SECONDS`, 90 s against
`hard_deadline`). Below that, asking for a rewrite buys a minute of work that
cannot finish, and the run ends worse than if it had shipped what it had.

The threshold is measured against `hard_deadline`, which already has the gate
reserve subtracted. The first version added the reserve on top and required
210 s, which silently disabled the nudge inside the end-to-end test's 300 s
budget -- both submits went through and only the e2e suite noticed. A unit test
now pins the semantics as well as the behaviour.

## How citations actually die (2026-08-18)

Every stored report was examined for WHY its citations were removed. The answer
was not what the design assumes.

**All 123 citation failures across 29 reports have one reason: "Reference [N]
has no Cited Text."** Not one is a Claim Verifier refuting a claim. The failure
is a deterministic check in `verify_report_v2`: a reference entry with no quote
attached fails, and every sentence citing it is deleted. The per-citation LLM
verification -- the most expensive consumer in a run, 18% of wall clock -- is
not what kills citations. What kills them is that no supporting quote was found
in the text we hold.

**Which makes holding the text the whole game:**

| papers whose citations were redacted | rate |
|---|---|
| full text available | 4 / 176 (2%) |
| abstract only | 92 / 1113 (8%) |

Four times the failure rate, and only 14% of retrieved papers arrive with full
text. This is the largest single lever on citation grounding found so far.

**A correction I nearly published.** Three reports lost every citation they had
(23/23, 14/14, 14/14) and held zero full-text papers, which looked like a live
defect in the shipped arm. They are dated 2026-03-04 and predate the full-text
fix; recent workflow runs hold 6-11 full-text papers. Checking the dates before
writing it up is the only reason that is a footnote instead of a false alarm.

**It also explains an earlier result that looked damning for read_paper.**
Reading a paper does not raise the per-citation verifier's pass rate (73% read
vs 84% unread, n=28 runs). But `read_paper` upgrades an abstract-only paper to
full text as a side effect -- and so does the agent arm's post-loop upgrade,
automatically, for every cited-but-thin paper. Measured on two runs, that step
recovered 8 of 13 and 11 of 17 thin cited papers with 232 s and 269 s of budget
in hand, so it is neither starved nor rare.

So the most likely reading is that read_paper's grounding value is largely
*already delivered* by the automatic upgrade, and what reading adds beyond it is
the agent's ability to quote precisely rather than the availability of text to
quote from. That is a hypothesis with a mechanism, not a measurement, and it is
recorded as such.

Nothing in the agent was changed on the strength of this. Eight changes already
await round 25, and a ninth argued from correlation is how a bundle becomes
unattributable.

## Silent-degradation audit (2026-08-18)

Two of my own helpers failed silently in one session -- the code fingerprint
stamped "unknown" from a NameError, and the outcome stamp would have discarded a
finished report -- so every exception handler on the AI path was audited by AST.

Of 38 swallowing handlers, 21 are blanket (`except Exception`) and log nothing.
The first pass flagged far more, but most turned out to be narrow, correct
swallows: a `Retry-After` header that will not parse, a publication year that is
not a number. Coarse greps overstate this; the type matters.

**The audit's honest result is that most of these are fine.** The agent loop's
failures record into `stats[...]` -- `merge_failed`, `framing_failed`,
`fulltext_failed`, `correction_failed` -- which lands in the stored record and is
readable afterwards. That is not silence. Two handlers in `run_ai_agent` swallow
a failed error-status write and a failed connection close, both already beneath
a `logger.exception`, and both defensible.

**One was worth fixing.** `build_partition` applies a minimum-features filter by
reading pathway totals from the installed network file. If that file is missing
the loader returns `{}` by design and the filter is skipped -- so pathways the
caller asked to exclude re-enter the universe, the clustering runs over a wider
set than requested, and nothing anywhere says so. Failing soft is right here;
failing silently is not. It now warns, naming the organism and the requested
threshold, and four tests pin that the warning fires when the filter is skipped
and stays quiet when none was asked for.

## A closed hypothesis: preferring open-access papers at search time (2026-08-18)

Since abstract-only papers lose citations four times as often, the obvious next
move is to retrieve papers that have full text -- mark PMC availability in
`search_literature` so the agent can prefer groundable sources. Before building
it, the question that decides whether it would help: are the papers that lose
their citations *available* in PMC and simply never fetched, or not in PMC at
all?

Sampled against NCBI's ID converter, all failures together said 73% of them were
in PMC against 42% of the papers that kept their citations -- which reads as a
retrieval failure worth fixing.

Split by run date, it says the opposite:

| abstract-only papers that lost their citations | in PMC |
|---|---|
| runs before the full-text fetch landed | 43 of 60 (72%) |
| runs on or after 2026-08-14 | 5 of 13 (38%) |

The headline was the historical bug -- reports written when the pipeline fetched
no full text at all -- reappearing in a pooled average. In current behaviour the
majority of papers that lose a citation are genuinely not in PMC, so the ceiling
is open-access coverage, not retrieval strategy, and a marker would mostly label
papers whose text nobody can get.

**Not built.** n=13 is too small to act on either, and the honest reading of it
argues against the change rather than for it.

This is the third time in one session that splitting stored data by date
reversed a conclusion -- the March reports that looked like a live redaction
defect, the missing stats that looked like lost telemetry, and now this. The
database holds several code eras at once, and a pooled average over it describes
none of them.

## Tool descriptions go stale, and they are re-sent every turn (2026-08-18)

Every description in `TOOLBELT` rides in EVERY Decide turn of every run, which
makes them the most-read documentation in the system and a standing token cost.
All ten were audited against the archive.

Every timing claim holds: `search_literature` "about 2 s" against a measured
median of 2000 ms, `read_paper` "about 3 s" against 2532, `cluster_pathways`
"half a second" against 418, `delegate_interpretation` "about 30 seconds"
against 29841, and the tools that call themselves instant and free measure
0-20 ms.

One claim did not. `read_paper` said "an unread citation is the kind the
verifier removes" -- and over 28 runs, citations to papers the agent had read
passed verification at 73% against 84% for ones it had not. The description was
telling the agent the opposite of the evidence, and it survived the earlier
correction of the same claim in the system prompt because nothing looks at
descriptions.

It now says what reading is actually for: checking that a paper says what you
mean to cite it for. Reading is for deciding, not for unlocking text -- a cited
paper has its full text fetched anyway by the post-loop upgrade.

Four tests now guard the class of defect rather than the instance: no
description may promise an outcome the gate decides ("the verifier removes",
"guarantees", "will survive"), every tool must have one, a free tool may not
advertise a seconds figure, and the belt's descriptions must stay under 3500
characters in total -- currently 2381, largest 422. Both guards were checked
against the wording they are meant to catch.

## The prompt contradicted itself about the best tool (2026-08-18)

Auditing the Lead's system prompt the way the tool descriptions were audited.

Its numeric claims hold: "roughly a dozen searches" against a measured ~9 per
run, "delegate a few times" against 2.1, "roughly two dozen investigative tool
calls" against 22-54, and "six pathways instead of fifteen" against 6.5 for runs
that never delegated versus about 16 for runs that did. The read_paper sentence
carries the corrected version -- reading does not make a citation more likely to
survive, so it earns its time only when it changes your mind.

One defect, and an expensive one to leave in place. The report rules said:

    - Before submitting, run check_my_citations on your draft: ... Fix or drop
      them rather than shipping them.
    ...
    - Optionally run check_my_citations on your draft first.

Three bullets apart, requiring and excusing the same call. check_my_citations is
the most valuable tool measured so far -- of 28 runs that called it, the 10 that
ran it again after a bad result improved every time and none got worse -- and
"optionally" is exactly the word most likely to stop it being run twice. The
leftover line is gone; the prompt is 3581 characters and names the tool once.

A test now asserts the prompt never calls it optional and describes it in one
place only, since a second description is how the contradiction arose. Verified
against the old wording.

## Orphaned prompts (2026-08-18)

Twelve `SYSTEM_PROMPT_*` constants exist; three were sent by nothing. No dynamic
lookup exists in the codebase, so a plain reference search settles it.

`SYSTEM_PROMPT_DELEGATED_INTERPRET` was mine, written this session to ask the
delegated interpreters for exactly the citations the gate can verify. It was
measured and reverted on the evidence -- the merge went 5 -> 18 citations under
the old prompt against 7 -> 3 and then 7 -> 10 under the new one -- and then sat
in the file looking exactly like the prompt that is actually used. The next
person tuning delegation would have edited it and measured nothing. Removed.

`SYSTEM_PROMPT_INTERPRET_V2` and `SYSTEM_PROMPT_SYNTHESIZE_V2` date from the
March 2026 commit that introduced AI interpretation and have never been
referenced since. They are named in the test's allowlist rather than deleted
from a branch about the agent arm -- visible instead of merely unused, and a
candidate for a cleanup against master.

A test now fails on any new orphan, checked against a deliberately introduced
one. It also caught a mistake of mine while being written: the first removal cut
at the closing quotes and left `+ TEMPORAL_GUIDANCE_BLOCK` dangling, because
these constants are concatenations rather than plain strings. The file would not
import; reverted and redone by line range.

## Dead code in the AI package (2026-08-18)

`agent_loop.py` was written across 25 rounds with several reverts, so it was the
obvious place to look: 27 functions, 20 constants, **all referenced**. Nothing
to remove.

The shipped package is a different story. Of 166 top-level definitions in
`src/classes/AIInterpret`, five are called from nowhere:

| definition | file |
|---|---|
| `redact_unverified` | verification.py |
| `build_synthesis_prompt` | prompts.py |
| `build_two_pass_interpretation_prompt` | prompts.py |
| `build_subagent_filter_prompt` | prompts.py |
| `build_interpretation_executor` | tools.py |

`redact_unverified` is the v1 of the redactor whose v2 was fixed this session --
exactly the trap worth naming. Someone fixing that bug could edit the dead twin,
see their tests pass against a function nobody calls, and ship nothing. It is
also why `build_synthesis_prompt` reads as live: it is the only caller of
`SYSTEM_PROMPT_SYNTHESIZE`, so a reference search on the prompt alone finds a
user and stops there. Dead code hides behind dead code.

They were pinned in a test allowlist rather than deleted -- removing shipped code
belonged in a change against master, not in a branch about the agent arm -- and
the guard fails on any NEW orphan, verified by adding one. All five, together with
`verify_report` and the `Verdict` model that the AST-based check later surfaced,
were removed in the 2026-09 cleanup; the allowlist is empty.

## References that nothing cites (2026-08-18)

A citation-integrity sweep over every stored report, checking three things: body
citations with no reference entry, reference entries never cited, and gaps in
the numbering.

Dangling citations: one report, from March, pre-fix. The redact/renumber chain
is sound there.

Uncited entries: **11 of 43 reports with a reference section**, all recent, both
arms. `QEJ1240C1u` listed 21 references for 18 citations; the extra three were
papers on non-small cell lung cancer, liver cancer and breast cancer -- real
papers, retrieved for the run, cited by nothing in the report.

The cause is one line in `renumber_citations`: it collects indices with
`re.finditer` over the WHOLE document, so an entry whose citations all vanished
-- redacted, or dropped when the report was rewritten -- still contributes its
index, gets renumbered with the rest, and survives. Nothing ever removed it.

This is not cosmetic. The reference list is the reader's measure of how much
evidence stands behind the report, and three of those twenty-one stood behind
nothing.

Uncited entries are now pruned before renumbering, which also closes the
numbering gaps they left. Conservative where it matters: a report whose
citations were all removed keeps its section rather than being left with an
empty heading. Five tests; over the stored corpus the change would have removed
17 entries across 11 reports and touched nothing else.

## Are the citations actually grounded? (2026-08-18)

The framework's central claim is that a citation carries a verbatim sentence
from the paper it points at. That had never been checked against the papers
themselves -- only enforced at generation time by the gate.

**Against our stored copies:** 459 of 476 quotes across 37 reports appear in the
paper they are attributed to (96.4%). Every report since 2026-08-14 is at zero
unmatched; the 17 misses are older.

That number is weaker than it looks, and worth saying so: the gate enforced this
property with this same fuzzy matcher, so re-running it largely confirms the
gate ran. It is a consistency check, not an independent one.

**Against PubMed, re-fetched:** 12 quotes from the four newest reports were
checked against text fetched fresh from NCBI -- text this system never stored.
Ten matched the abstract directly. The other two came from papers held as PMC
full text (40 394 and 31 426 characters), so their sentences are in the body and
correctly absent from the abstract.

Twelve of twelve consistent, with nothing fabricated and nothing misattributed
in the sample. n=12 is small and only the newest reports were sampled, so this
is evidence the grounding is real rather than proof that it always is -- but it
is the first check of that claim made from outside the pipeline that produced it.

## The citation audit is a command now (2026-08-18)

The grounding checks above were one-off scripts. They are the checks that answer
the question the whole framework exists for, so they belong in the repository:

    python -m src.benchmarks.ai_citation_audit integrity [--since YYYY-MM-DD]
    python -m src.benchmarks.ai_citation_audit quotes    [--since ...]
    python -m src.benchmarks.ai_citation_audit pubmed    [--since ...] [--sample N]

`integrity` is cheap and covers everything: citations with no reference entry,
reference entries nothing cites, gaps in the numbering. `quotes` checks every
Cited Text against the paper copy stored beside it. `pubmed` is the independent
one -- it re-fetches from NCBI and checks against text this system never stored,
and reports quotes from full-text papers separately rather than counting a
body sentence as missing because it is not in the abstract.

Every mode takes `--since`, because the database holds several code eras and
three conclusions this session reversed when the same data was split by date
rather than pooled.

Current state, reports since 2026-08-14: 16 checked, no dangling citations, 242
quotes all matching their stored paper, and an independent re-fetch of 10 coming
back 6 matched, 4 correctly from full text, 0 unexplained. Nine of the sixteen
still carry uncited reference entries -- written before the pruning fix, which is
what that fix stops.

Five tests pin the parsers, because the failure that matters is not a false
alarm but a silent zero: a renamed heading or reworded label would make the
auditor report no problems out of no quotes, which looks exactly like a clean
corpus.

## The grounding claim, checked end to end (2026-08-18)

The first independent pass excused seventeen quotes because they came from
full-text papers and an abstract cannot contain a body sentence. That is not a
check -- a fabricated body sentence passes it silently. The auditor now
re-fetches the body from PMC for exactly those papers.

Sixty quotes from reports written since 2026-08-14, checked against text this
system never stored:

| | |
|---|---|
| matched (abstract-only paper, abstract re-fetched) | 24 |
| matched (full-text paper, body re-fetched from PMC) | 36 |
| **not found in either** | **0** |
| could not be re-fetched | 0 |

Sixty of sixty. Nothing excused, nothing fabricated, nothing misattributed, and
every paper re-fetchable. This is the framework's central claim -- a citation
carries a verbatim sentence from the paper it points at -- verified from outside
the pipeline that produces it, at a sample size where a systematic problem would
have shown.

What it does not check: whether the quote SUPPORTS the sentence it is attached
to. That is a judgement, it needs a model, and the gateway has been down since
04:00. It is the obvious next audit.

## Can quote relevance be screened without a model? No (2026-08-18)

The remaining unchecked half of grounding is whether a quote SUPPORTS the
sentence it is attached to. That is a judgement and needs a model, so I tried a
cheap proxy: content-word overlap between each claim sentence and its quote,
across 487 pairs from recent reports.

Median overlap 0.25, mean 0.31, and the five weakest pairs all scored 0.00 --
none of which survived reading:

  * one claim cited [3] and [5]; the metric compared it against [5]'s quote
    while the sentence was making [3]'s point;
  * one was a Limitations entry *criticising its own citation* -- "Reference
    [17] discusses lipid-induced NK cell dysfunction in DLBCL, not metabolic
    reprogramming" -- which is the agent doing exactly the right thing;
  * others differed only by tokenisation (PLCβ, hsa-mir-122, hyphenated names).

So the metric flags good behaviour and punctuation, and 0.25 median means it
barely discriminates anyway. **Not built into the auditor.** The honest position
is that this half of the claim is unverified until the gateway returns.

The failed attempt did surface something worth keeping: 4 of 16 recent reports
name a specific citation's weakness in their own Limitations -- "the available
references do not directly address these specific pathways, so interpretations
rest on internal temporal coherence rather than external evidence". That is the
behaviour a reader most needs and it was not asked for explicitly.

## Reports are missing required sections (2026-08-18)

`ai_citation_audit structure` checks the five sections the prompt requires. Of
16 recent reports, 3 miss a heading -- but two different things look identical
to a keyword search, which is why the mode reports pathway-named headings beside
the missing ones:

| report | headings | pathway-named | missing |
|---|---|---|---|
| 1354co025T (agent) | 26 | 14 | Detailed Pathway Analysis |
| 5g3vaE24BQ | 16 | 7 | Limitations |
| 22sU1dOIiw | 8 | 1 | Limitations |

The agent report has no "Detailed Pathway Analysis" heading and eleven numbered
pathway sections carrying exactly that content: restructured, not incomplete.
The other two genuinely have no Limitations at all -- and Limitations is where
the caveats that make an interpretation honest are supposed to live.

Measured, not enforced. submit_report already asks one question per run and a
second gate would make it a workflow step.

## Reports were being truncated, silently (2026-08-18)

Two recent reports have no Limitations section, and the prompts do ask for one
-- `SYSTEM_PROMPT_SYNTHESIZE` says "End with 'Limitations and Caveats' section"
and `build_synthesis_prompt_v2` lists it. So the instruction was not the problem.

Reading the tails settled it. `22sU1dOIiw` ends on the bare heading `### 4.`
with nothing after it. That is not a stylistic choice, it is the output stopping
mid-heading: the model hit its token limit, and Limitations is the last section,
so Limitations is what was lost. (A trailing `---` is normal -- reports that do
have Limitations end that way too, which is why the tails had to be read rather
than pattern-matched.)

`finish_reason` was already captured by the streaming shim and never looked at,
so a truncated answer was returned as though it were complete. It now logs a
warning naming the character count, appends to a process-wide list, and
`run_ai_agent` stamps `truncated_calls` on the stored record -- scoped to the
run, so the count belongs to that job rather than to whatever ran before it.

Detection only. Raising the token cap or asking for a continuation is a decision
that needs measurement, and the gateway has been down since 04:00. But from here
a truncated interpretation can be told from a short one, which was not possible
before: the two reports above are indistinguishable, in the database, from
reports whose author simply had less to say.

Two tests drive the shim with a truncated stream and a complete one.

## A report that grounded nothing, shipped as "Ready" (2026-08-18)

`n6e03200f1`, 2026-08-17, status `done`:

    batches 14, batches_with_citations 0, batch_citations 0
    synth_citations 56, quotes_supplied 0, refs_rendered 0
    verification: score 0.07, ref_accuracy 0.0, references_section_found False
    detail: "Ready - 0 of 56 retrieved papers cited"

Fifty-six papers retrieved, fifty-six citations written during synthesis, quotes
found for none of them, no references rendered -- and 49 752 characters of
interpretation shipped with a cheerful progress line. The pipeline knew: it
scored the report 0.07 and said so in `detail`.

The report did not. Searching all 49 752 characters for any sentence about
citations, references or evidence returns nothing relevant. It ends on a pathway
table. A reader cannot tell it apart from a fully cited report -- and the report
is what gets read, shared and exported, long after the progress line is gone.

It now ends with a note naming the number of papers that were tried and stating
plainly that what remains rests on the measured data alone and has not been
corroborated against published work. Only when papers were actually retrieved: a
run that searched for nothing has no literature claim to walk back.

Three tests. The logic was extracted into `_note_if_ungrounded` rather than left
inline precisely so it could have them.

(Found while checking why two reports lacked a Limitations section. The scan
that found those also mis-scored one: `22sU1dOIiw`'s truncated `### 4.` sits at
the end of a long single line, so a last-line check missed it. The truncation
rate of 7 in 56 stored reports is therefore a floor, not a figure.)

## A concurrency claim I got wrong, and the correction (2026-08-18)

Reading the round-25 traces I computed, for the gate's 22 verifier calls, 47 s of
work inside a 55 s window, called it 0.9x, concluded the fan-out was serialised,
and drew that on the diagram as a defect with a named cause: a synchronous
`search_paper_text` blocking the event loop.

Three measurements later, all of it was wrong:

| test | result |
|---|---|
| time `search_paper_text` directly | **1 ms**, not seconds -- cannot serialise anything |
| 8 concurrent calls to the gateway | 4.8 s of work in 0.7 s wall = **7.3x** -- the gateway parallelises |
| overlap of the 22 verifier intervals | **peak 11 concurrent** |

The timeline settles it: two bursts of eleven verifiers, every one starting at
the same instant (272.5 s and 324.1 s into the run) and finishing within ~4 s.
That is healthy 11-wide parallelism.

My ratio was an artifact of dividing total work by a span that contained
something else entirely: the **48 s gap between the two verification rounds**,
which is a single long-form correction rewrite. The gate's serial cost is that
rewrite, not the fan-out.

The lesson is narrow and worth keeping: a work/span ratio is only a concurrency
measure when the span contains nothing but that work. Computing overlap from
per-event intervals -- which the trace already carried -- would have given the
right answer immediately, and did.

The diagram is corrected. The real levers are one wave of delegation instead of
two, grounding citations inside the sub-agent, and rewriting only the failed
sections rather than the whole report.

## Redaction was killing verified citations too (2026-08-18)

Round 27's first agent replicate submitted **11 citations it had checked and
grounded** -- `check_my_citations` returned "11 cited, 0 unquotable" -- and the
gate returned **6**. The grounding fix worked (10 quotes collected at delegation,
all reused by the gate) and the report still lost half its evidence.

The cause is in `redact_unverified_v2`: a failed citation takes its whole
**sentence** with it. Measured over the recent corpus, **39% of citation-bearing
sentences carry two or more citations** -- one carried ten:

    Papers [2], [10], [3], [5], [9], [1], [8], [6], [7], and [4] provide
    mechanistic support for specific pathways

One bad index there destroys nine verified ones.

A sentence that still cites something verified now keeps its place, with only
the failed marker removed. A sentence resting solely on failed citations is
still deleted, because the claim has nothing left behind it.

Editing markers in place does not work -- it produces "from [1] and agrees" and
"rose, [2]". The whole citation list is treated as one token and re-rendered
from its survivors, so `[2], [9], and [4]` becomes `[2] and [4]`, and an Oxford
comma in ordinary prose is left alone.

Replayed over the stored reports: 9 sentences that were deleted would now be
kept, carrying 18 verified citations between them. Five tests, including the
ten-citation case.

## A metric that read backwards, and the conclusion it cost (2026-08-18)

Round 27's agent replicates reported 6 and 3 "papers retrieved" against base's
22, which looked like retrieval had collapsed. It had not: those runs made 26 and
18 searches. The metric was wrong.

`papers_retrieved` was read from the stored `papers` array, which the gate
filters down to the references that SURVIVED -- but only when
`citation_mapping` is non-empty:

    if citation_mapping:
        unique_papers = [p for p in unique_papers if p["ref_index"] in citation_mapping]

A run that keeps its citations therefore reports a small number, and a run that
loses every one skips the filter entirely and reports the full retrieval. **The
worse the run, the more papers it appears to have retrieved.**

That inverted metric produced a conclusion recorded earlier in this document:
"stop rewarding retrieval volume -- r2 pulled 102 papers and scored zero, r1
pulled 11 and scored 11." Those numbers are the artifact, not the cause. 102 was
not breadth; it was the signature of total citation failure. **The claim is
withdrawn.** Whether retrieval breadth helps or hurts grounding is now an open
question, and the earlier finding that abstract-only papers fail 4x more often
stands on its own data, which came from the papers array of individual reports
rather than from this metric.

Runs now stamp `papers_retrieved` from the paper index itself, and the scorer
reports `papers_in_references` beside it. The two differ by exactly the amount
the gate removed, which is worth seeing.

## Where the conversion gap comes from: papers no writer ever sees

With `papers_retrieved` finally measuring retrieval rather than the surviving
reference list, round 29 reads:

| | retrieved | cited | conversion |
|---|---|---|---|
| base | 19.5 | 14.5 | **74%** |
| agent | 75 | 6 | **8%** |

The arithmetic of the arm explains it. `DELEGATE_PAPERS` is 10, chunks are 5
pathways, and 15 pathways make 3 chunks. A writing unit can therefore see at
most 3 x 10 = **30** of the 75 papers retrieved. The other 45 were searched for,
fetched, and never shown to anyone -- they cannot become citations, whatever the
grounding machinery does downstream.

The shipped arm has no such gap: ~19.5 papers spread across fourteen batches,
so nearly every paper reaches a writer that could cite it.

Two levers follow, and they pull in opposite directions on cost:

1. **More writing units.** Chunks of 3 pathways rather than 5 gives five units
   for the same breadth, so up to 50 papers reach a writer. Five chunks against
   four workers is two waves, about 60 s instead of 30 -- affordable, since
   round 29 came in at 399 s against a 600 s ceiling.
2. **Retrieve less.** Fifteen searches at ~2 s each buy 75 papers of which 45
   are unusable by construction. That is 30 s of budget spent on paper the arm
   has no way to read.

Lever 1 raises citations; lever 2 only saves time. Lever 1 is the experiment
worth running, and it is the same "writing units" ratio that predicted the gap:
base has fourteen, this arm has about five.

## Where the 914 s went: untraced post-loop work

Round 30's agent replicate was stopped at the ceiling. Its trace:

    loop ends            222 s   (52 tool calls)
    gate verification    549-552 s   (15 calls in 3 s -- parallel, healthy)
    last event           552 s
    killed               914 s

The traced work is small and fast. **327 seconds sit between the loop ending and
verification starting**, and none of it is traced. Round 29's stats name the
pieces: `topup_s` 113.8, `verify_loop_s` 117.3, `merge_s` 15.9.

The citation top-up is the largest. It rewrites the entire report to add
citations, and it fires whenever the count is under `MIN_CITATIONS` (22) -- which
for this arm is always, since it ships 6 to 17. It was bounded by a per-call
timeout but not by the wall clock, so it ran at the point in the run with the
least time left.

It now requires 200 s of headroom and records `topup_skipped` with the numbers
when it declines. A report that ships with fewer citations beats one that does
not ship.

**The general shape, third time in this arm:** an expensive step guarded on a
quality threshold but not on the clock. The delegation nudge, the merge probes
and now the top-up were all written that way. The pattern to check for is a
`bounded(...)` call whose budget is a fixed per-call timeout rather than
"whatever is left of the run".

## One hung lookup cost 14% of a run, and took three attempts to bound

The tool-usage table over 68 archived runs:

| tool | calls | median ms | max ms |
|---|---|---|---|
| check_my_citations | 70 | 1 324 | **83 217** |
| quote_shelf | 21 | **0** | 4 |
| delegate_grounding | 14 | 1 581 | 3 409 |

The shelf is free as designed and grounding is cheap. But one
`check_my_citations` call took **83.2 seconds** against a median of 1.3 -- a
single hung quote lookup, 14% of a ten-minute run spent on one citation, in a
tool the agent calls up to three times.

`_collect_cited_quotes` is now bounded as a whole. Getting there took three
tries, and the two failures are the interesting part:

1. **A deadline checked between results does nothing.** Four fast lookups
   finished before the deadline could expire, so no check tripped, and
   `as_completed` then blocked on the fifth for its full duration. Measured:
   6.0 s elapsed against a 2 s deadline.
2. **A `with` block joins its threads on exit.** Even after switching to
   `shutdown(wait=False)`, the context manager's `__exit__` waited for the hung
   thread anyway. Measured: 8.0 s against a 2 s deadline.

What works is a timeout on the wait itself -- `as_completed(futures,
timeout=budget)` -- with the executor shut down without waiting. Measured: 2.0 s
against a 2 s deadline, 4 of 5 resolved, and the normal path unchanged at 5 of 5.

A partial answer is strictly better than a late one here: the gate re-checks
anything missing, so the only cost of stopping early is a quote collected twice.

Each attempt LOOKED correct and logged its warning; only timing the call showed
which one worked.

## The shipped arm misses the ten-minute limit too (2026-08-18)

Round 32's `base-r3` errored:

    status  error   wall  600.0
    detail  "The interpretation exceeded its 10-minute limit and was stopped.
             The literature gateway was slow to answer; please try again later."

It is not an isolated draw. Across 14 archived base runs:

    min 184 s   median 399 s   max 602 s   mean 413 s
    over 500 s: 3 of 14 (21%)      over 550 s: 2

Two errored at the ceiling, a third finished 12 seconds inside it. **The
production interpreter fails the ten-minute requirement in roughly one run in
seven**, and the user sees an error after waiting the full ten minutes.

Two things follow.

**For the comparison.** Rule 1 has been treated as an agent-specific weakness --
round 30's 914 s run read as a disqualifying flaw. It is a property of the
pipeline, not of the arm: whichever arm runs, a slow literature gateway can eat
the budget. The agent arm's overruns are worse (914 s against a hard 600 s
ceiling) and its clock guards were still the right fix, but the shipped arm is
not the safe baseline the rule implies.

**For production.** This is the user-visible failure that matters most: ten
minutes of waiting, then "please try again later", with the work discarded. The
partial report exists at that moment -- the loop had a draft, the gate had
quotes -- and nothing ships it. An interpretation that says "verified 8 of 14
citations before the time ran out" beats an error message, and both arms could
produce one.

That is a bigger user-facing win than any citation-count improvement measured so
far, and it is independent of which arm wins.

## A timeout now ships what exists (2026-08-18)

Roughly one base run in seven reaches the ten-minute ceiling -- median 399 s,
max 602 across 14 archived runs, two errored. Until now that run raised, every
phase's work was discarded, and the user who had waited ten minutes was told to
try again. A synthesised report with rendered references was sitting in memory
at the moment the clock expired.

The report is stashed at three points where it is coherent enough to read --
after synthesis, after references are rendered, after verification -- and the
timeout handler ships the latest one under a header that says what it is:

    > **Incomplete interpretation.** The 10-minute limit was reached while the
    > pipeline was still working (last completed stage: references rendered), so
    > this report is what had been produced by then. Citations present here were
    > rendered but may not all have passed verification...

`timed_out_at_stage` goes into the stored stats, so how far runs get before
expiring becomes measurable rather than anecdotal.

A run that times out before any report exists still errors -- salvage must not
manufacture a report from nothing, and a whitespace draft is not a report.

Found while testing rather than while writing: `_partial_result` popped the
stage before the caller read it, so `timed_out_at_stage` would have recorded "?"
every time. The stat is set where the value is known.

## Which tool is really useful: what the correlations can and cannot say

52 scored runs, correlating how often each tool was called against the citations
the run shipped. The first table looked decisive and was mostly an artifact.

**Era dominates.** `quote_shelf` correlated +0.85 with citations -- and appears
only in runs 33 to 51 of 52, so it is measuring "the code got better", not the
tool. `submit_report` correlated **-0.81**: runs with two submits averaged 0.0
citations against 8.8 for one submit. That is not the nudge causing harm, it is
the old collapsed era, where the nudge fired AND the seam bug zeroed citations.

**Within one code era (n=16)** the picture is narrower and more honest:

| tool | calls | r(citations) | range |
|---|---|---|---|
| search_literature | 159 | **+0.91** | 1..28 |
| check_my_citations | 13 | +0.88 | 0..3 |
| notebook_write | 38 | +0.75 | 1..8 |
| read_paper | 41 | +0.65 | 0..13 |
| delegate / grounding / shelf | | +0.96 | 0..1 -- all the same binary |

The +0.96 group is one variable wearing three names: *did this run delegate at
all*. A run that did not produced zero citations, so every delegation-adjacent
tool correlates perfectly. Only `search_literature` has real spread (1 to 28
calls) with a real signal.

**The structural limit.** A tool used in every run cannot be evaluated this way
at all -- five of six had no contrast, and the "not used" column was empty.
Measuring a tool that is never omitted requires ablation, one round per tool.

### compare_gene_profiles removed

13 calls across 72 archived runs, **none at all in the most recent 16**, and no
relationship to citations (r = -0.08). Its schema rode in every Decide turn of
every run regardless. `get_pathway_details` already carries per-gene profiles
for the pathways under examination, which is where the agent answers the same
question.

Nine tools now, 2 349 characters of description. The test that asserted "ten
tools" is replaced by one asserting every declared `@function_tool` is wired
into TOOLBELT -- a count turns a deliberate removal into a failure, while the
real invariant is that nothing is declared and left unregistered.
