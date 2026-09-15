# Maintainer documentation

These pages are for people who run, deploy and debug PaintOmics — not for
people who use it. The user guide is at
[conesalab.github.io/PaintOmics](https://conesalab.github.io/PaintOmics/), built
from the rest of `docs/` by `.github/workflows/docs.yml` on every push to
`master`. These pages are listed under `exclude_docs` in
`mkdocs.yml`, so they are not built into that site at all — `not_in_nav`
would only unlink them, leaving them published at their URLs.

They exist because the operational knowledge of this project has lived in one
person's head and in private notes. Everything here is reconstructed from the
repository and from those notes. Claims that come only from the notes, and
cannot be checked against code in this repository, are marked *(from operator
experience; not enforced by code)*.

| Page | What it answers |
|---|---|
| [architecture.md](architecture.md) | What serves a request, what holds state, what the process boundaries are, and what that forbids |
| [deployment.md](deployment.md) | How a merged change reaches the public instance, and how to confirm it arrived |
| [ci.md](ci.md) | What each workflow checks, what a green tick proves, and how to reproduce a failure locally |
| [troubleshooting.md](troubleshooting.md) | The traps, each with the symptom, how it was diagnosed, and what to do |
| [tellme-five-checks.md](tellme-five-checks.md) | How the five gates on a walk interpretation were measured, what they measured, and how to run the harness again |

## What this project is

PaintOmics AI paints several omic layers onto KEGG, Reactome, MapMan and
OmniPath pathways. It is the successor to PaintOmics 4 (*Nucleic Acids
Research* 2022, [10.1093/nar/gkac352](https://doi.org/10.1093/nar/gkac352)),
developed by the Genomics of Gene Expression Lab
([conesalab.org](http://conesalab.org/)), and distributed under GPLv3.

- Source: `https://github.com/ConesaLab/PaintOmics`, default branch `master`.
- Public instance: <https://paintomics.uv.es/>.
- Contact for questions, bug reports and organism requests:
  <paintomicsai@gmail.com>.

## Repository layout

| Path | What lives there |
|---|---|
| `PaintomicsServer/src/` | The Flask application: `paintomicsserver.py` (routes), `servlets/`, `classes/` (job classes, AI interpretation), `common/` (queue, DAOs, mappers, statistics), `AdminTools/` (species installers) |
| `PaintomicsClient/public_html/` | The ExtJS 4.2.1 client: `index.html`, `app/view`, `app/controller`, `resources/css` |
| `deploy/` | Container deployment: `Dockerfile`, `compose.yaml`, `build-image.sh`, `smoke-test.sh`, `load-species.sh`, `swap-and-install.sh`, `entrypoint.sh`, `nginx/`, and `deploy/README.md` |
| `.github/workflows/` | `pr.yml`, `nightly.yml`, `cd.yml`, `data-cache.yml`, `profile.yml` |
| `scripts/` | `regression.sh` and `regression.py` (the end-to-end baseline harness), `ci/`, `perf/` |
| `tests/baseline/` | Stored, exact regression baselines, one directory per example dataset |
| `requirements.txt` | The only pip manifest in the tree, enforced by `PaintomicsServer/src/tests/test_dependencies_declared.py` |
| `paintomics4.ini`, `paintomics.wsgi`, `start_server.sh` | uWSGI/WSGI entry points for a non-container host, and a local launcher |

## A development instance

Python 3.11, MongoDB, R, and the `libcairo2` shared library:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
Rscript -e 'install.packages(c("purrr","cluster","mclust","amap","factoextra",
                               "igraph","ggplot2","jsonlite","stringr","dplyr"))'

cd PaintomicsServer
python src/launch_server.py            # http://localhost:8000
```

The first launch copies `src/resources/example_serverconf.py` to
`src/conf/serverconf.py`, which is gitignored. `./start_server.sh` does the same
through a conda environment and starts MongoDB if it is not already running. A
fresh instance has an empty database; install species with
`src/AdminTools/DBManager.py` (see [deployment.md](deployment.md)).

## The rules that outrank everything else

Each is explained where it belongs, but they are the ones that cost the most
when broken.

1. **uWSGI runs `processes = 1`.** `src/common/PySiQ.py` holds the job queue in
   the memory of the process that accepted the request. A second worker gets its
   own empty queue and jobs vanish silently. Concurrency comes from threads.
   Checked by `deploy/smoke-test.sh`.
2. **MongoDB is never published to the host.** It runs without authentication.
   Also checked by `deploy/smoke-test.sh`.
3. **No request may block on an external service.** Threads are few; a route that
   waits on the LLM gateway takes the site down. Enqueue and let the client poll.
4. **`src/conf/serverconf.py` is per-site, gitignored, and never overwritten by a
   deploy or a container upgrade.** A new setting needs the template entry *and*
   a defensive import, or you break every existing deployment.
5. **`tests/baseline/` is compared bit for bit and is pinned to one interpreter
   and platform.** A last-digit float difference is usually the environment, not
   the code.
6. **A green tick on a `master` commit proves the artifact built, nothing more.**
   Lint, unit tests and the dataset gate run on pull requests only.

## Keeping these pages honest

When you learn something operational the hard way, write it here rather than in
a commit message. When a claim marked *(from operator experience)* gets encoded
in a test, a smoke check or a script, drop the marker and cite the file.
