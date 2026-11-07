# Continuous integration

Five workflows in `.github/workflows/`. None of them deploys to the public
instance; `cd.yml`'s "deploy" is an ephemeral stack on the runner.

| Workflow | Trigger | What it does |
|---|---|---|
| `pr.yml` | every pull request | The gate: lint and the client's JavaScript tests, the unit-test sweep, one example dataset per pipeline class, a secret scan and a documentation build |
| `nightly.yml` | 03:17 daily, or by hand | All twelve example datasets against `tests/baseline/`, plus the species installer end to end against the live KEGG API |
| `cd.yml` | push to `master` | Builds the deployable image and brings up a throwaway staging stack on the runner, checked with `deploy/smoke-test.sh` |
| `data-cache.yml` | weekly, or by hand | Seeds the Actions cache with the species-data snapshot the baselines were recorded against. The only workflow that reads a secret |
| `profile.yml` | by hand | py-spy over `tests/perf/large_input`, optionally A/B against another ref |

## What a green tick on master proves

**It proves the artifact built and the staging stack came up. It does not mean
the tests pass.**

`Lint`, `Unit tests (offline)` and `Example datasets, one per pipeline class`
live in `pr.yml`, which triggers on `pull_request`. Nothing re-runs them after a
merge, so `master` can show `success` on every commit while the dataset gate is
failing on every open pull request — which has happened, and let changes merge
over a gate that had been red for days.

To judge `master`'s actual health, read the last pull request's checks
(`gh pr checks <N>`) or dispatch the nightly:

```bash
gh workflow run nightly.yml --ref master
gh run list --workflow nightly.yml --limit 3
```

## The gate, and what branch protection requires

`master` carries a repository ruleset: pull request required, force-push and
deletion refused, and the status check **`Gate`** must pass.

`Gate` is a job that waits on the other five and fails unless every one of them
succeeded. It exists because the unit-test job is a matrix, so its own check
names carry the shard (`Unit tests (offline) (1/2)`) and change whenever the
shard count is edited — a ruleset pinned to those names would silently stop
gating the moment somebody split the matrix differently. `Gate`'s name never
changes.

The gate's budget is under ten minutes of wall clock. The five real jobs run in
parallel and each carries its own hard timeout.

## The offline rule

The unit-test and dataset jobs run with outbound networking refused:
`scripts/ci/no_network` goes first on `PYTHONPATH`, and its `sitecustomize.py`
turns any attempt to open a non-loopback socket into a named `OSError`. KEGG,
Reactome, PubMed, Europe PMC and the LLM gateway are therefore all unreachable —
a suite that needs one must stub it.

The single deliberate exception is `installer-smoke` in `nightly.yml`, which
downloads one small species (`mge`, about 59 pathways, two seconds between
requests) from the live KEGG API, because the download path is what it tests.

No job in the gate reads a secret.

## The species-data cache

KEGG-derived data may not be redistributed, so the snapshot the baselines were
recorded against is neither in this repository nor a public release asset. It
lives in a private repository and reaches CI like this:

- `data-cache.yml` checks it out with a read-only deploy key held as the
  repository secret `CI_DATA_DEPLOY_KEY`, and saves it to the Actions cache
  under a key ending in the snapshot identifier.
- `pr.yml` and `nightly.yml` only ever *restore* that cache, with
  `fail-on-cache-miss: true`, and use no secrets.

Two consequences worth knowing:

- **Caches unused for seven days are evicted.** The nightly touches this one
  daily and `data-cache.yml` re-seeds weekly in case it was evicted anyway. If
  the gate starts failing with a cache miss, dispatch `data-cache.yml` on
  `master` and re-run.
- **A pull request can only restore caches from the merge ref, the base branch
  or the default branch** — never from its own head branch or a sibling. So a
  new snapshot must be seeded from `master` *before* any pull request can use
  it, and a new snapshot means a new key suffix in **both** `data-cache.yml` and
  `.github/actions/setup-paintomics/action.yml`.

## Why the runner is macOS

`.github/actions/setup-paintomics` pins `macos-26` (arm64) and Python 3.11
because `tests/baseline/` is compared **exactly** — floats bit for bit, NaN
equal to NaN, no tolerance. The baselines were produced on that platform with
those CPython wheels, R 4.6.0, and an arm64 `more-rs`; a different BLAS is a
different summation order, which shows up as a diff. Ubuntu is used only for the
lint, secret-scan, docs and CD jobs, which compare nothing numeric.

## Reading a regression failure

The dataset job runs `scripts/regression.sh` over one dataset per pipeline class
plus one multi-omic dataset. On failure it uploads the differing outputs as an
artifact named `regression-pr-<run id>`, kept for seven days — download that
before re-running anything.

Two failure modes look identical to a real regression and are not:

**Last-digit float differences across datasets your change cannot touch.** That
is the interpreter, not the code. The baselines are pinned to the environment
that recorded them; running the harness under a different Python or a different
NumPy/SciPy build produces ordinary content differences with no warning that the
environment moved. `scripts/regression.sh` defaults `PYTHON` to plain `python3`,
so set it explicitly:

```bash
export PYTHON=/path/to/venv-py311/bin/python
export PAINTOMICS_KEGG_DATA=/path/to/KEGG_DATA
scripts/regression.sh 01-gene-single-condition
```

Note that the installer needs the *other* interpreter: `DBManager.py` depends on
`scriptine`, which dies on Python 3.11's removed `inspect.getargspec`.
Installer under the conda 3.9 environment, pipeline under the 3.11 venv.

**A field you added showing up as a diff in datasets you did not think about.**
The comparison is exact, so a purely additive output field is still a diff. This
has bitten three times; in one case the gate could not see it at all, because
every gate dataset carried a single omic and the new field only appears once a
job has more than one. That is why `04-multiomics-integration` is now in the
gate.

## Recording a baseline

Before rewriting anything, **classify every difference first**: walk both trees
and count `ADDED` / `REMOVED` / `VALUE CHANGE` / `TYPE CHANGE` / `LIST LENGTH`.
Only an additive-only result means the baseline is merely stale; anything else
is a regression hiding behind one. Then confirm the baselines you did *not*
intend to change are byte-identical with `diff -r`.

`--write-baseline` creates **missing** baselines only and never overwrites one.
To regenerate a baseline, delete its directory on purpose and re-run.

After any baseline change, dispatch the nightly — the gate sees five of twelve
datasets, so a stale baseline in the other eight is invisible until 03:17.

## Running the gate locally

Lint is the cheapest job to fail, and every check it makes on the code runs
locally:

```bash
pip install ruff==0.14.13 vulture==2.16
ruff check --output-format concise .
scripts/ci/vulture_gate.sh
for s in scripts/*.sh scripts/ci/*.sh deploy/*.sh; do bash -n "$s" || exit 1; done
npx --yes eslint@10.9.1 .
sh PaintomicsClient/tests/run.sh
python -m compileall -q scripts/ci/vulture_whitelist.py
```

The unit sweep runs through the same script CI uses:

```bash
PAINTOMICS_KEGG_DATA=/path/to/KEGG_DATA scripts/ci/run-unit-tests.sh --timeout 420
```

The secret scan is a single binary over the working tree, and takes about four
seconds:

```bash
gitleaks dir . --config .gitleaks.toml --redact
```

`.gitleaks.toml` must keep its `[extend] useDefault = true`. Without it gitleaks
loads no rules at all and reports a clean tree however many keys are in it —
which is exactly what that file did, unused, from the day it was added until
2026-08-31.

The documentation build is likewise one command, and `--strict` is the point of
it: a page added to `docs/` but left out of the nav, or a link that resolves to
nothing, fails here instead of quietly never appearing on the published site.

```bash
python -m pip install -r docs/mkdocs-pins.txt
mkdocs build --strict
```
