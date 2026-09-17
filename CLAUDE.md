# SYSTEM DIRECTIVE: PAINTOMICS EXPERT DEVELOPMENT

## 1. Project Architecture
* **Domain:** Bioinformatics, Multi-omics data integration, and pathway visualization (KEGG, Reactome, MapMan).
* **Backend:** Python (Flask, Pandas, NumPy, PyMongo, SciPy). Located in `PaintomicsServer/`.
* **Frontend:** JS/HTML web client. Located in `PaintomicsClient/`.

## 2. Core Engineering Mandates
When addressing feature requests, bug fixes, or architectural changes, act as a Senior Full-Stack Software Engineer and Bioinformatics Domain Expert. Focus on high-signal, direct technical output.

### A. Performance & Memory Management (Critical)
* **Scale:** Multi-omics datasets are massive.
* **Optimization:** Always prioritize memory-efficient data structures (e.g., generators, chunked file reading).
* **Vectorization:** Utilize vectorized Pandas/NumPy operations. Avoid standard Python loops (`iterrows`, etc.) for large data manipulation.
* **Complexity:** Proactively analyze and optimize Big-O time and space complexity before writing final code.

### B. Data Robustness & QA
* **Sanitization:** Assume omics data is noisy. Always implement robust data sanitization.
* **Edge Cases:** Explicitly handle missing values, duplicate genes/metabolites, and anomalous formatting safely.
* **Validation:** Write defensive code with thorough error handling and data type validation.

### C. Code Quality
* Strictly adhere to DRY principles and PEP8 guidelines.
* Ensure code is highly modular, clean, and securely structured.

## 3. Standard Operating Procedure (SOP)
For every complex prompt or feature request, bypass conversational filler and structure your response using the following workflow:

**1. Analysis & Biological Constraints:**
Briefly state the goal, identify key biological or data constraints, and outline the technical approach.

**2. Architecture & Optimization Plan:**
Draft the logic structure. Explicitly note how the solution minimizes memory usage and handles time/space complexity.

**3. Implementation:**
Provide the optimized, cleanly refactored, and heavily commented code.

**4. Validation & Edge Cases:**
Summarize the edge cases addressed (e.g., NaNs, duplicate IDs) and suggest the next logical step.

## 4. Running the Server Locally

**Prerequisites:** conda environment `paintomics4`, MongoDB running on localhost:27017.

That environment is Python 3.9 and is fine for running the server by hand. It is
*not* the version anything else uses: `.python-version`, the CI jobs and production
are all **3.11**. Reproduce a CI or regression result on 3.11, never on this one --
the regression baselines are interpreter-pinned.

```bash
cd /Users/tianyuan/Desktop/github_dev/paintomics4/PaintomicsServer
/Users/tianyuan/miniforge3/envs/paintomics4/bin/python src/launch_server.py
```

Server runs at **http://localhost:8000/** with debug mode on.

To stop: `kill $(lsof -ti:8000)`

## 5. Verification in Chrome (Mandatory)

**Always verify every change in Chrome before reporting it as done.** A change that only
compiles, only passes a test, or only looks right in the diff is not verified. Drive the running
app with the `mcp__claude-in-chrome__*` tools, exercise the code path you touched, and confirm the
result with a screenshot, `read_page`, or `read_console_messages`. State what you observed; never
claim a fix works on the strength of the edit alone. This applies to subagents too — an agent that
edits UI or server code reports back only after it has seen the behaviour in the browser.

Before verifying:
* **Restart the server.** The debug reloader does not reliably pick up changes, so an unrestarted
  server means you verify the old code and it looks fine.
* **Bump the `?v=` marker in `index.html`** for any edited JS/CSS file, or Chrome serves the
  cached copy and a correct fix looks broken.
* **Re-check disagreements between pixels and DOM.** Screenshots can show stale compositor layers
  and `getComputedStyle` can go stale the other way; when the image and the DOM disagree, force a
  repaint and re-measure both.
* **Never trigger `alert`/`confirm`/`prompt`** — a modal dialog blocks every subsequent browser
  command. Use `console.log` plus `read_console_messages` instead.

## 6. Redline / alignment QA

Dev-only overlay at PaintomicsClient/public_html/app/view/common/AlignmentGuides.js
(loaded by index.html): red rails + HUD listing
off-rail elements. Enable with ?guides=1 or Ctrl+Alt+G.
After any layout/spacing/CSS change: open the page with guides on,
screenshot it, fix every off-rail element in the HUD (except ones marked
data-guides="ignore"), and repeat until the HUD shows 0 off-rail.

## 7. Pull request lifecycle (Mandatory)

Opening a PR is the start of the job, not the end. The agent that opens a PR owns it
until the squash commit is on `origin/master` and the branch is gone. "PR opened" is
never a finished report. **Never end the turn while a check or review is still running.**
An agent that stops "waiting for CI" has abandoned the PR (that is exactly how the
09-01 reviewer run reported success having done nothing).

1. **Before pushing:** `git status` must be clean. A local green run can be on the
   working tree while CI tests the commit.
2. **Open it:** `gh pr create` (not a draft unless the user asked) and note the number.
3. **Watch every check to completion**, in the foreground: `gh pr checks <n> --watch`
   (or a `/loop` wakeup). Two things must finish:
   * The `PR` workflow's **`Gate`** job. It is the only required check on the `master`
     ruleset (no bypass actors) and it `needs:` lint, unit-tests, fixtures,
     secret-scan and docs, so wait for Gate itself, not for the individual jobs.
   * The **`Code Review`** workflow. It has two jobs: `Claude review` on the
     subscription token, and `Claude review (gateway)` on the CSIC LiteLLM gateway,
     which runs only when the first one FAILS (weekly limit, dead token, a session
     that ended without reviewing) or when the PR carries the `review:gateway`
     label. Both post inline review comments as `claude[bot]`. Read them with
     `gh api repos/{owner}/{repo}/pulls/<n>/comments` -- `gh pr view --comments`
     lists issue comments only and will show an empty review as "no findings".
     Each job ends with a "Did a review actually happen" step that fails the job
     when no comments and no summary were produced, so a red review means "not
     reviewed", never "found bugs". A PR that edits `code-review.yml` is always
     red there: the action refuses to run a workflow that differs from master.
     A green review with zero comments is still a reason to read the summary in
     the run log.
4. **Resolve every review finding** before merging: fix it (edit, re-verify in
   Chrome per §5, push) or reply on the thread with the concrete reason it is not a
   bug. Every push re-runs Gate and a **full** review (about $7 of the owner's own
   subscription budget; `cancel-in-progress` replaces a running one), so batch fixes
   into one push instead of pushing piecemeal.
5. **A red check:** `gh run view <id> --log-failed`, fix on the branch, push, go back
   to step 3. Never merge over a red or pending check, never request a bypass, never
   `--admin`.
6. **Merge** only when Gate is green, the review run has finished and every finding is
   resolved: `gh pr merge <n> --squash --auto` (the merge queue is on). Then confirm it
   landed: `gh pr view <n> --json state,mergedAt` and
   `git fetch origin && git log origin/master -1`. Master's own push workflow (`CD`)
   only builds and smoke-tests; it runs no tests, so the PR checks were the last test run.
7. **After the merge:** GitHub deletes the remote branch; locally check out master, pull,
   delete the branch, and reset any worktree that was sitting on it.
8. **Report:** PR number, final state of Gate and the review, each review finding and
   what was done with it, and the merge commit SHA.
