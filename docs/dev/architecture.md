# Architecture

How the system fits together, and which of its properties are load-bearing.
The short version: one Python process serves the whole site, keeps the job
queue, the sessions and several caches in its own memory, and shells out to R
and to a Rust binary for parts of the analysis. Almost every operational rule
in this documentation follows from that sentence.

## The request path

```
browser
  |  HTTPS
nginx                 TLS, 100 MB body limit, gzip, 300 s proxy_read_timeout
  |  proxy_pass / uwsgi_pass
uWSGI  ── 1 process, N threads, harakiri 300 s
  |
Flask application (PaintomicsServer/src/paintomicsserver.py)
  |            \                    \                  \
MongoDB      PySiQ queue          Rscript            more-rs
             (in-process,         generateMetaGenes.R  (Rust MORE PLS1)
              N_WORKERS threads)
```

Entry points:

- `PaintomicsServer/src/launch_server.py` — creates `Application` and exposes
  `app`. Run directly for development; also the WSGI callable used by
  `deploy/uwsgi.ini`. On first launch, if `src/conf/serverconf.py` is absent, it
  copies `src/resources/{__init__.py,example_serverconf.py,logging.cfg}` into
  `src/conf/`.
- `paintomics.wsgi` — the WSGI shim for a non-container host, imported by
  `paintomics4.ini`.
- `deploy/entrypoint.sh` — the container entry point. It installs
  `serverconf.py` from the template *only if absent*, warns about
  misconfiguration, creates the data directories, fixes volume ownership once,
  then drops to an unprivileged user.

Routes are declared with `@self.app.route(...)` inside `Application` in
`paintomicsserver.py`; the servlet modules under `src/servlets/` hold the
handlers.

## The one-process constraint

`src/common/PySiQ.py` is a vendored, in-process, thread-backed job queue. All
job state lives in the memory of the process that accepted the submission. With
two uWSGI workers, a job submitted to worker A is invisible to worker B: the
submission succeeds, the status endpoint is served by a process that has never
heard of the job, and the job appears to hang forever.

So:

- `deploy/uwsgi.ini` sets `processes = 1`, `threads = 16`, `enable-threads`,
  and `lazy-apps = true` (the queue and the scheduler start threads at import
  time, which do not survive uWSGI's pre-fork).
- `deploy/smoke-test.sh` fails the deployment if `processes` is not 1.
- `paintomics4.ini` — the non-container uWSGI configuration in the repository
  root — sets `processes = 1` and `threads = 4`.
- Scaling out requires replacing PySiQ with a shared broker first. There is no
  Redis or RQ anywhere in this project despite the queue vocabulary.

**Consequence: no request handler may wait on something slow.** With four
threads, a route that blocks for two minutes on an external gateway holds a
quarter of the site; four concurrent such requests are an outage, and at
`harakiri = 300` the request is killed anyway. The AI interpretation route is
the worked example of the correct shape: `AIInterpretServlet` enqueues into the
queue instance and returns a ticket, and the browser polls — poll requests take
milliseconds. *(The thread-exhaustion arithmetic is from operator experience;
the thread counts themselves are in the ini files.)*

`reload-on-rss = 4096` (MB) recycles the worker on memory growth rather than on
a request count, because job cost varies by orders of magnitude.

## What lives in process memory, and therefore dies on restart

| State | Where | Consequence of a restart |
|---|---|---|
| Job queue and job status | `src/common/PySiQ.py`, started with `N_WORKERS` workers | In-flight jobs are lost; their records go stale and the client reports an error on the next status poll |
| Login sessions | `UserSessionManager.logged_users`, a plain dict on the singleton — MongoDB has no session collection | Every browser is logged out. The cookies survive, so the next request looks authenticated to the user and fails server-side, which reads as a permissions bug rather than a logout *(from operator experience; not enforced by code)* |
| Job instances | `JobInformationManager`, a bounded cache, `JOB_CACHE_MAX_SIZE = 50` | Editing a job document directly in MongoDB under a running server changes nothing the server sees, until restart |
| KEGG data | `KeggInformationManager`, `KEGG_CACHE_MAX_SIZE = 25` | Reinstalling species data needs a restart to be visible |

A background APScheduler job runs `cleanDatabases` and `clearFailedData` on a
24-hour interval, started at application start — so the first run is 24 hours
after each restart, and frequent restarts postpone it indefinitely.

## Configuration

`PaintomicsServer/src/conf/serverconf.py` is the live configuration. It is
**gitignored** (`.gitignore`), per-site, and never overwritten by a deployment
or by a container upgrade — `deploy/entrypoint.sh` writes it only `if [ ! -f ]`.

The tracked template is `PaintomicsServer/src/resources/example_serverconf.py`.
It reads every secret from the environment with an empty default, so it can be
installed verbatim; `src/tests/test_release_hygiene.py` enforces that no real
credential ever appears in it, and that the template covers every setting the
app imports.

It also loads a `.env` file beside the server (`PaintomicsServer/.env`, or one
at the repository root) with `os.environ.setdefault`, so a real environment
variable always wins and a stray `.env` on a production box cannot override what
the deployment configured. The loader fails silently by design: a config module
that raises on import takes the whole servlet down.

**Adding a setting takes three steps, not one:**

1. the value in your own `serverconf.py` (invisible to git),
2. the same line in `example_serverconf.py` (what fresh deployments get),
3. a **defensive import at the use site**, because an already-deployed config
   predates the setting and a bare
   `from src.conf.serverconf import X` raises at module import — taking down
   every servlet that imports it:

```python
try:
    from src.conf.serverconf import MORE_RS_BINARY
except ImportError:
    MORE_RS_BINARY = os.getenv("PAINTOMICS_MORE_RS", "")
```

Step 2 is enforced by `test_release_hygiene`. Nothing enforces step 3, and its
failure mode is the worst of the three: an optional feature becomes an outage on
every existing deployment. *(From operator experience, with the enforcement
detail checkable in the test.)*

Settings worth knowing, all in `example_serverconf.py`:

| Setting | Default | Note |
|---|---|---|
| `SERVER_ALLOW_DEBUG` | `false` | Never true in production; the smoke test checks it |
| `SERVER_MAX_CONTENT_LENGTH` | 100 MB | Must equal nginx `client_max_body_size` and uWSGI `limit-post` |
| `SERVER_MAX_FORM_MEMORY_SIZE` | = `SERVER_MAX_CONTENT_LENGTH` | Werkzeug 3.1 caps urlencoded bodies at 500 kB otherwise; see [troubleshooting.md](troubleshooting.md) |
| `MAX_THREADS` / `N_WORKERS` | 6 / 4 | Queue-side concurrency inside the single process |
| `JOB_CACHE_MAX_SIZE` / `KEGG_CACHE_MAX_SIZE` | 50 / 25 | The in-process caches above |
| `MAX_GUEST_JOB_DAYS` / `MAX_JOB_DAYS` | 7 / 14 | Job retention, from `accessDate`; the interface promises these exact numbers and `test_retention_matches_the_promise` reads them out of the servlet's own message so the two cannot drift |
| `MAX_GUEST_DAYS` | 90 | Guest *account* inactivity — a different question from job retention |
| `PAINTOMICS_BASE_URL` | — | Embedded in activation emails. A localhost value silently breaks registration; the container refuses to start without it |
| `AI_INTERPRETATION_ENABLED` | true | Master switch for the AI feature |
| `AI_COMPOUND_SUGGESTIONS_ENABLED` | true | Step 2 compound disambiguation, separate because it costs very differently |
| `AI_INPUT_CONVERTER` | false | Ships inert; a deployment opts in |

## Storage

**MongoDB.** One application database (`PaintomicsDB` by default) plus one
database per installed species. In the container stack it is `mongo:7`; the
long-running non-container host has been seen on MongoDB 4.4, which matters —
see the `$lookup` entry in [troubleshooting.md](troubleshooting.md). *(The
production version is from operator notes.)*

- `PaintomicsDB`: users, jobs, pathways, features, files, visual options, AI
  interpretations, reports.
- `<species>-paintomics`: `kegg` (pathway documents, with a `source` field of
  KEGG / Reactome / MapMan / OmniPath), `xref`, `dbname`, `versions`.
  **`xref` documents carry `dbname_id`, a foreign key into `dbname` — not a
  `dbname` string.** A count filtered on `xref.dbname` matches nothing for any
  species and has already produced one false "the data is missing" report.
- `global-paintomics`: shared versions and compound tables.

**Filesystem.** Two trees, both configurable and both outside the code:

- `KEGG_DATA_DIR` (`PAINTOMICS_KEGG_DATA`, `/data/KEGG_DATA` in the container)
  with `download/`, `current/` and `old/` subtrees. The installers stage into
  `download/` and *move* a completed species into `current/`.
- `CLIENT_TMP_DIR` (`PAINTOMICS_CLIENT_TMP`, `/data/CLIENT_TMP`) for uploads and
  job output.

In the container both are one named volume, `paintomics-data`, alongside
`mongo-data`. Those two volumes hold everything irreplaceable; pathway data is
reproducible from the source databases but takes hours to refetch.

## Subprocesses

The application shells out on the user-facing request path, so a host that
serves pages perfectly can still fail the moment someone clicks a button:

- **R** — `src/common/bioscripts/generateMetaGenes.R` for
  Metagenes and Hub Analysis. `deploy/smoke-test.sh` checks that `purrr`,
  `cluster`, `mclust`, `amap`, `factoextra`, `igraph`, `ggplot2`, `jsonlite`,
  `stringr` and `dplyr` all import.
- **MORE** — the regulatory model. Both methods, PLS1 and MLR, run on
  `more-rs`; the MORE R package and its `runMORE.R` wrapper were removed in
  September 2026, so there is no second backend and no fallback. The binary is
  discovered at `src/common/bioscripts/more-rs` or on `PATH` and is
  **gitignored**, so `git archive` drops it while `deploy/build-image.sh` packs
  it from the working tree; `build-image.sh` reads its ELF header and refuses to
  build if it is for the wrong architecture, or absent. A host without it
  refuses regulatory submissions up front (`MOREServlet.engineRefusal`) rather
  than failing inside the job. `PAINTOMICS_MORE_RS=off` disables regulatory
  analysis outright.
- **The identifier mapper** — `FeatureNamesToKeggIDsMapper` parallelises with
  forked processes from inside the threaded worker. Forking a threaded process
  is delicate; see the deadlock entry in
  [troubleshooting.md](troubleshooting.md).

## External services

The application and its installers reach out to: `rest.kegg.jp`, `reactome.org`,
`ftp.ebi.ac.uk` and the Ensembl FTP dumps, NCBI E-utilities (PubMed),
`omnipathdb.org`, an OpenAI-compatible LLM gateway, and an SMTP relay.
`deploy/compose.yaml` deliberately does *not* mark its network `internal:`
because the species download needs egress; isolation comes from nginx being the
only service with a published port.

CI is the mirror image: the PR gate and the nightly run with
`scripts/ci/no_network/sitecustomize.py` first on `PYTHONPATH`, which raises an
`OSError` naming the host on any non-loopback connection.

## The client

`PaintomicsClient/public_html` is an ExtJS 4.2.1 application served as static
files. Two mechanics matter operationally:

- **Cache markers.** `index.html` references assets as `Util.js?v=2.8` and
  friends. `revalidateEntryDocument` in `paintomicsserver.py` serves
  `index.html` itself as `no-cache, must-revalidate` while every asset gets
  `max-age=43200`. An edited asset whose `?v=` marker did not change is served
  from cache to returning visitors — new view code running against an old
  library, which surfaces as `ReferenceError: <fn> is not defined`.
  `src/tests/test_versioned_assets_are_bumped.py` fails when a listed asset
  changes without a bump.
- **`Ext.Loader`.** Files loaded through `Ext.Loader` rather than a `<script>`
  tag in `index.html` are not cache-busted by a `?v=` marker at all; a server
  restart is what picks them up. `PA_Step2Views.js` is one of these. *(From
  operator experience.)*

## Job lifecycle

A pathway-acquisition job moves through Step 1 (upload and organism/database
choice), Step 2 (identifier mapping and compound disambiguation), and Step 3/4
(enrichment, networks, hub and class analyses, metagenes, painted diagrams).
`src/classes/JobInstances/` holds the four job classes — pathway acquisition,
Regions2Genes, miRNA2Genes and MORE — and `src/benchmarks/bench_runner.py`
drives exactly the same methods the servlets call, which is what makes the
regression harness in [ci.md](ci.md) possible.

Retention runs from `accessDate`, which the client rewrites through
`/pa_touch_job` each time a job is opened, so a job someone keeps coming back to
never expires.
