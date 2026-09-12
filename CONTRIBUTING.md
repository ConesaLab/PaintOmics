# Contributing to PaintOmics

This is the working guide for changing the code: where things are, how to get an
instance running, how to run the tests the way CI runs them, and what the pull
request gate checks.

House engineering rules live in [`CLAUDE.md`](CLAUDE.md) — memory-efficient data
structures and vectorised pandas/NumPy over Python loops, defensive handling of
missing values and duplicate identifiers, PEP 8, DRY.

## 1. Where things live

| Path | What is in it |
|---|---|
| `PaintomicsServer/src/servlets/` | Flask routes |
| `PaintomicsServer/src/classes/` | Job classes and the analysis code, including `AIInterpret/` |
| `PaintomicsServer/src/common/` | Shared helpers, `PySiQ.py` (the in-process job queue), `bioscripts/` (the R scripts and the `more-rs` binary) |
| `PaintomicsServer/src/AdminTools/` | `DBManager.py`, the species installer |
| `PaintomicsServer/src/examplefiles/` | Example datasets and their `datasets/manifest.json` |
| `PaintomicsServer/src/benchmarks/` | `bench_runner.py`, the pipeline kernel the regression harness drives |
| `PaintomicsServer/src/tests/` | 282 standalone test suites and `run_all.py` |
| `PaintomicsServer/src/resources/example_serverconf.py` | Configuration template; the real `src/conf/serverconf.py` is gitignored |
| `PaintomicsClient/public_html/` | The ExtJS 4.2.1 client; `index.html` is the entry document |
| `docs/` | mkdocs sources for the user guide (`mkdocs.yml` at the root) |
| `deploy/` | Docker Compose stack, `Dockerfile`, `smoke-test.sh`, `fetch-example-gtf.sh`, operator runbook |
| `scripts/` | `regression.sh` / `regression.py`, `ci/` (the gate's helpers), `perf/`, `deadcode_report.py` |
| `tests/baseline/` | Regression baselines, one directory per example dataset |
| `requirements.txt` | The only pip manifest in the tree |

## 2. A development instance

Requires Python 3.11, MongoDB, R and the `libcairo2` shared library.

```bash
git clone https://github.com/ConesaLab/PaintOmics.git
cd PaintOmics

python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

Rscript -e 'install.packages(c("purrr","cluster","mclust","amap","factoextra",
                               "igraph","ggplot2","jsonlite","stringr","dplyr"))'
# These are the set deploy/smoke-test.sh checks for; a machine without
# them passes the unit tests and fails the smoke test. MORE needs none of this:
# it runs on the more-rs binary, not on R.

cd PaintomicsServer
python src/launch_server.py                # http://localhost:8000
```

The first launch copies `src/resources/example_serverconf.py` to
`src/conf/serverconf.py`. That file is gitignored and is never overwritten
afterwards. Every secret in it is read with `os.getenv`, and a
`PaintomicsServer/.env` (also gitignored) is loaded at import time with
`setdefault`, so a real environment variable always wins.

`./start_server.sh` does the same through a conda environment and starts `mongod`
if it is not already running.

### Installing a species — the slow part

A fresh instance has an empty database and does nothing useful until at least one
species is installed. This is the expensive step, measured in hours and hundreds
of gigabytes, not minutes.

The installer is the one thing that must **not** run in the 3.11 virtual
environment above: `DBManager.py` calls into `scriptine`, which uses
`inspect.getargspec` — removed in Python 3.11 — so it dies on the first
command. Run it under a Python 3.9 interpreter (the conda `paintomics4`
environment); the pipeline itself stays on 3.11.

```bash
cd PaintomicsServer
python3.9 src/AdminTools/DBManager.py download --specie=mmu --kegg=1 --mapping=1 --common=1 --reactome=1
python3.9 src/AdminTools/DBManager.py install  --specie=mmu
```

Install **mmu**: every bundled example dataset uses it, and so does the whole
regression harness. Rough sizes: shared common data ~1.4 GB, shared Reactome
~856 MB, 200–400 MB per species. Use `--common=0` for every species after the
first — the common step re-downloads the shared KEGG reference data and dominates
the runtime. Reactome curates human and infers about twenty other species; use
`--reactome=0` for the rest, or the install fails with a message naming the
species.

The region-based examples and datasets also need
`PaintomicsServer/src/examplefiles/GTF/sorted_mmu.gtf`, which the repository does
not ship. `deploy/fetch-example-gtf.sh` builds it (it defaults to the container
`paintomics-app-1`); it downloads Ensembl GRCm38, trims it, and lands ~566 MB.

For a container instance instead of a source checkout, follow
[`deploy/README.md`](deploy/README.md).

## 3. Running the tests

**There is no pytest here.** Each suite under `PaintomicsServer/src/tests/` is a
standalone `__main__` script, run as a module from `PaintomicsServer/`:

```bash
cd PaintomicsServer
python -m src.tests.test_versioned_assets_are_bumped
python -m src.tests.test_dependencies_declared
```

The whole sweep goes through the repository's own runner, which classifies the
three output conventions in use (`Passed: n / n`, unittest's `OK`/`FAILED`, and a
script that just exits 0) instead of grepping for a line:

```bash
cd PaintomicsServer
python -m src.tests.run_all                  # everything
python -m src.tests.run_all --only ai        # substring filter on the suite name
```

`run_all.BASELINE` records the suites that already fail on `master`, so the run
answers "did this branch introduce a failure", not "is everything green". Do not
add to that list to make a branch look clean.

Each entry states a **count**, and the count is `FAIL` plus `ERROR` — an
exception is a test that broke before it reached an assertion, and unittest
reports those on a separate line. A baselined suite is inherited only while it
actually runs its tests *and* names no more failing ones than its entry
records. Naming **more** is this branch's. So is running **none** of them,
whether the suite died on import or lost a `setUpClass` — unittest reports that
as one `ERROR: setUpClass` line and `Ran 0 tests`, which compared favourably
against a baseline of four while the whole suite had stopped executing. So is
timing out. `run_all --baseline` prints the counts for you; an entry without one
is refused at startup, because a baseline entry with no number shields its suite
from every failure it ever acquires. `test_gate_baseline_rules` holds these
rules in place.

CI runs the same suites in parallel and offline through
`scripts/ci/run-unit-tests.sh`, which is also the fastest way to run them locally:

```bash
PAINTOMICS_KEGG_DATA=/path/to/KEGG_DATA scripts/ci/run-unit-tests.sh --timeout 420
# --only <substring>, --jobs N, --shard 1/2 are passed through to scripts/ci/run_suites.py
```

Note that a suite reporting `OK` after running zero tests is the worst possible
answer. That happens when `serverconf.py` is missing, so the wrapper copies the
template in before it starts.

### The regression harness

`scripts/regression.sh` runs example datasets end to end through
`src/benchmarks/bench_runner.py` — the same job methods the servlets call, in the
same order — normalises the output (job IDs, timestamps, absolute paths and UUIDs
stripped, set-derived collections sorted) and compares it with
`tests/baseline/<dataset>/`.

```bash
export PYTHON=/path/to/venv-py311/bin/python
export PAINTOMICS_KEGG_DATA=/path/to/KEGG_DATA
scripts/regression.sh                                    # all 12 datasets
scripts/regression.sh 01-gene-single-condition           # one
```

It needs, on the host: MongoDB with mmu installed, `Rscript` with the metagene
packages, `examplefiles/GTF/sorted_mmu.gtf`, and a `more-rs` binary at
`src/common/bioscripts/more-rs` (or named by `PAINTOMICS_MORE_RS`) for the MORE
datasets. It refuses to start without the last one rather than reporting R's
different table ordering as a regression.

`--write-baseline` creates **missing** baselines only; it never overwrites one.
A baseline is regenerated by deleting its directory on purpose.

### Baselines are pinned to the environment that recorded them

The comparison is exact: floats bit for bit, NaN equal to NaN, no tolerance. Only
`PYTHONHASHSEED=0` is pinned by the harness itself (`scripts/regression.py`), so
set-iteration order cannot masquerade as a result difference.

What the harness records about the environment is what
`.github/actions/setup-paintomics/action.yml` states: it "runs on macos-26
(arm64), the platform family the regression baseline was produced on", with
Python 3.11 and the R version input documented as "the baseline ran on 4.6.0".
The MORE baselines hold `more-rs` output; it is the only MORE engine.

Nothing in the harness records or checks a Python version, and
`scripts/regression.sh` defaults `PYTHON` to plain `python3`. So a run on a
different interpreter or a different dependency set produces ordinary content
differences with no warning that the environment, not the code, moved. **Always
set `PYTHON` explicitly to a 3.11 interpreter with the pinned requirements**, and
if a baseline is only wrong on your machine, suspect the environment before the
code.

## 4. The pull request gate

`master` requires a pull request, refuses force-pushes and deletions, and
requires one status check: **`Gate`**. That job waits on the other five and
fails unless every one of them succeeded, so it is the single name branch
protection has to know. The five it waits on:

- `Lint`
- `Unit tests (offline)` — a two-shard matrix, so its own check names carry the
  shard and change whenever the matrix does; that is why `Gate` exists
- `Example datasets, one per pipeline class`
- `Secret scan` — gitleaks over the working tree, about four seconds
- `Docs build` — `mkdocs build --strict`, so a page left out of the nav or a link
  that resolves to nothing fails here rather than silently never appearing on
  the published site

Run the lint job locally before pushing — it is the cheapest one to fail:

```bash
pip install ruff==0.14.13 vulture==2.16

ruff check --output-format concise .
scripts/ci/vulture_gate.sh
for s in scripts/*.sh scripts/ci/*.sh deploy/*.sh; do bash -n "$s" || exit 1; done
find PaintomicsClient -name '*.js' \
  | grep -vEi '/(lib|libs|vendor|ext-[0-9]|extjs|jquery|node_modules)/' \
  | xargs -n1 node --check
python -m compileall -q scripts/ci/vulture_whitelist.py
```

**ruff** (`ruff.toml`) selects `E9` plus the whole `F` family at line length 120,
excluding `PaintomicsClient`, `dist`, `docs`, `runs` and
`scripts/ci/vulture_whitelist.py`. The tree is at zero findings. A deliberate
exception carries `# noqa: F401 -- <reason>` with the reason spelled out; a bare
`noqa` fails.

**The vulture dead-code ratchet** (`scripts/ci/vulture_gate.sh`) runs two bands.
At ≥80% confidence there must be zero findings beyond
`scripts/ci/vulture_whitelist.py`, where every row carries its reason. At ≥60%
confidence no *new* candidate may appear beyond `scripts/ci/vulture_baseline.txt`
(line numbers stripped); candidates disappearing is fine, and the baseline is
refreshed in the same commit that removes the code. Flask route handlers are
excluded by `--ignore-decorators`, so adding a route does not fail the gate.

**The offline rule.** The unit-test and dataset jobs run with outbound networking
refused: `scripts/ci/no_network` goes first on `PYTHONPATH` and its
`sitecustomize.py` turns any attempt to reach KEGG, Reactome, PubMed, Europe PMC
or the LLM gateway into a named `OSError`. No test may reach the network — stub it
in the suite. The one deliberate exception is the installer smoke job in
`nightly.yml`, which tests the download path itself. No job reads a secret.

**The dataset job** runs `scripts/regression.sh` over
`01-gene-single-condition`, `04-multiomics-integration`, `05-regulatory-mirna`,
`06-regulatory-more` and `07-region-based` — one per pipeline class, plus one
multi-omic dataset, because a field only a multi-omic job produces is invisible
to the single-omic ones. If your change is additive to the pipeline output, the
other seven baselines can still go stale; `nightly.yml` runs all twelve, and you
can trigger it from the Actions tab.

## 5. Client-side changes

Assets in `PaintomicsClient/public_html/index.html` are cache-busted by hand and
served with a long max-age:

```html
<script type="text/javascript" src="app/view/common/Util.js?v=2.8"></script>
```

If you edit a versioned JS or CSS file, **bump its `?v=` marker in `index.html`**.
Skipping it leaves returning browsers running the old file against new code for
up to 12 hours — how `getClusterColor` and `truncatableTextRenderer` both broke.

`PaintomicsServer/src/tests/test_versioned_assets_are_bumped.py` enforces it. It
holds a `PUBLISHED` table of path → (version, sha256), and fails when a file's
digest no longer matches the version it was published under. Bump the marker in
`index.html` **and** update the digest in the table; updating the digest alone
defeats the point, and the failure message says so. The same suite refuses a
plain `<script src="app/....js">` with no marker at all.

It reads the files from `HEAD` via `git show`, not from the working tree, so
commit first and then run it:

```bash
cd PaintomicsServer
python -m src.tests.test_versioned_assets_are_bumped
```

After a layout or spacing change, the client ships a development overlay:
`app/view/common/AlignmentGuides.js`, off unless you load the page with
`?guides=1` or press Ctrl+Alt+G. It draws the rails and lists off-rail elements.

## 6. Commits and pull requests

Branch off `master` as `<kind>/<slug>` — the kinds in use are `feat/`, `fix/`,
`design/`, `chore/` and `quality/`, for example `fix/pathway-network-ignores-compounds`.

Subject lines are **sentence-case imperative, with no type prefix and no trailing
full stop**, and they name the change in the domain's own terms:

```
Count metabolites in the pathway network, not only genes
Run one multi-omic dataset in the PR gate
Say on every Step 1 file row whether the job needs it
Stop offering Report error when the refusal names the field
```

They run long when the change needs it — 80 to 100 characters is normal here —
because a subject that says what moved is worth more than one that fits in 50.

The body explains **why**, with evidence: the mechanism that was wrong, the
measurement that shows it, the job id or dataset it was observed on, and what the
numbers were before and after. Read `ae55c611` or `4ddb86e9` for the shape.

Pull requests land either as a squash whose subject carries `(#NNN)`, or as a
merge commit `Merge pull request #NNN from ConesaLab/<branch>`. Either is fine.

Before you open one: `Gate` must pass, and any change to the
UI or to server behaviour should have been exercised in a browser against a
running instance, not only in the diff.

## 7. Adding a dependency

`requirements.txt` at the repository root is the only pip manifest, and that is
deliberate — a second copy under `PaintomicsServer/src/` once shipped conflicting
pins and fed GitHub's dependency graph a 2020 snapshot. Add the package there,
**pinned to an exact version**, with a comment saying why it is needed.

```bash
cd PaintomicsServer
python -m src.tests.test_dependencies_declared
```

That suite walks the AST of every module under `src/` and checks each external
import actually resolves, so a dependency that is only installed on your machine
fails here instead of when the container refuses to boot.

Python is 3.11 and both bounds are load-bearing: Pillow, CairoSVG and requests
need ≥ 3.10 for their security fixes, and pandas 1.5.3 publishes no cp312 wheel.
Do not raise or lower it as a side effect of adding a package.

That version is stated once more in `/.python-version`, and
`test_dependencies_declared.py` fails if any workflow step or `FROM python:`
stage disagrees with it. The file is not decoration: dependabot reads it and
nothing else, and while it was missing dependabot resolved the pip group against
the newest Python it knew and proposed `numpy==2.5.2` and `scipy==1.18.1`, both
of which need 3.12 — the whole group failed at `pip install` before a test ran.
It must name exactly one version; a `#` comment beside it is fine, since both
dependabot and pyenv strip those.

## 8. Adding a server configuration setting

`PaintomicsServer/src/conf/serverconf.py` is gitignored, generated once per
deployment from the template, and then never overwritten. That shapes every step:

1. Add the setting to `PaintomicsServer/src/resources/example_serverconf.py`,
   reading it from the environment with a safe default:

   ```python
   MY_SETTING = os.getenv("MY_SETTING", "default")
   ```

   Never put a real key, token or password in that file —
   `src/tests/test_release_hygiene.py` scans tracked files and fails if you do.

2. Import it **defensively** in the code that uses it. A deployment that already
   has a `serverconf.py` will not have your new name, and a plain import would
   take the whole application down at startup over a feature flag:

   ```python
   try:
       from src.conf.serverconf import MY_SETTING
   except ImportError:
       MY_SETTING = "default"          # matching the template
   ```

   `src/servlets/CompoundSuggestionServlet.py` is the worked example.

3. If a container deployment must be able to set it, add it to `deploy/env.example`
   with a note on the consequence of leaving it unset, and pass it through the
   `environment:` block of `deploy/compose.yaml`.

4. For local work, put secrets in `PaintomicsServer/.env` (gitignored). The
   template loads it with `os.environ.setdefault` before anything calls `getenv`,
   so a real environment variable set by systemd or the container always wins,
   and a missing or malformed `.env` fails silently rather than taking the servlet
   down.

## 9. Questions

- Bugs, feature requests and organism requests: an
  [issue](https://github.com/ConesaLab/PaintOmics/issues) on this repository.
- Email: [paintomicsai@gmail.com](mailto:paintomicsai@gmail.com).
- User documentation: [conesalab.github.io/PaintOmics](https://conesalab.github.io/PaintOmics/), published by
  `.github/workflows/docs.yml` on every push to `master` that touches `docs/`.
- Operating an instance: [`deploy/README.md`](deploy/README.md).

PaintOmics is distributed under the GNU General Public License v3; contributions
are accepted under the same licence.
