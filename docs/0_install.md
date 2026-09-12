# Installing PaintOmics

This page is for someone standing up their own PaintOmics server. If you only
want to run an analysis, you do not need any of it — start with
[Your first analysis](8_step_by_step.md) instead.

There are two supported ways to install: a Docker Compose stack, which is what
the project deploys and tests, and a from-source install for development. Both
give you the same application; they differ in how the process is supervised and
where the configuration comes from.

PaintOmics is distributed under the **GNU General Public License, version 3**
(see [License](7_license.md)).

## What an installation is made of

| Component | What it is | Notes |
|---|---|---|
| The server | A Python 3.11 Flask application in `PaintomicsServer/` | Run under uWSGI behind nginx in the container stack; run directly for development |
| The client | Static JavaScript and CSS in `PaintomicsClient/public_html/` | Served by the same process. There is no build step |
| MongoDB | Users, jobs, pathway definitions and identifier cross-references | The stack ships MongoDB 7; the driver is pinned to pymongo 4, which is required rather than preferred — pymongo 3.11 speaks wire-protocol opcodes MongoDB removed in 5.1 |
| A data directory | `KEGG_DATA` (the pathway databases you install) and `CLIENT_TMP` (uploads and job output) | Both live under `/data` in the container, on a single named volume |
| R | `Rscript` is called by metagenes (`generateMetaGenes.R`) | See [R packages](#r-packages) below |
| `more-rs` | The engine behind regulatory analysis (MORE). A platform-specific binary, gitignored, dropped in per deployment | Without it no regulatory analysis can run; `deploy/build-image.sh` refuses to build an image that lacks it |

The one architectural fact an operator has to know is that the job queue lives
in the memory of the process that accepted the request
(`PaintomicsServer/src/common/PySiQ.py`). Concurrency comes from threads only.
A second worker process would get its own empty queue: submission would still
succeed, the status endpoint would be served by a process that has never heard
of the job, and the job would appear to hang forever. `processes = 1` in
`deploy/uwsgi.ini` is therefore not a tuning knob.

## Install with Docker

Three containers: **nginx** (TLS, reverse proxy) → **app** (Flask, uWSGI and
the job queue) → **mongo** (MongoDB 7, never published to the host).

You need Docker Engine 24 or newer with the Compose plugin, inbound TCP 80 and
443, and outbound HTTPS to `rest.kegg.jp`, `reactome.org`, `ftp.ebi.ac.uk`,
`packagemanager.posit.co`, PyPI and — if you enable the AI features — the LLM
gateway.

```bash
git clone https://github.com/ConesaLab/PaintOmics.git
cd PaintOmics

cp deploy/env.example deploy/.env
$EDITOR deploy/.env                     # PAINTOMICS_BASE_URL is mandatory

./deploy/make-cert.sh <your-hostname-or-ip>

docker compose -f deploy/compose.yaml up -d --build
./deploy/smoke-test.sh
```

The first build takes 15–30 minutes, most of it R packages.

`smoke-test.sh` checks the things that are cheap to verify here and expensive
to discover in production: that the three containers are running, that the
Python imports resolve and pymongo is 4.x, that the R packages the metagenes
script needs are importable, that MongoDB is not published to the host,
that HTTP redirects to HTTPS, and that uWSGI runs a single process. It exits
non-zero if any of that is wrong. Two more things — debug mode being on, and a
`PAINTOMICS_BASE_URL` that points at localhost — are reported as notes rather
than failures, as is a missing example GTF. It does not check pathway data at
all, deliberately, because a fresh deployment legitimately has none.

!!! warning "Two things that must not be relaxed"
    **`processes = 1`** in `deploy/uwsgi.ini`, for the reason above.
    **MongoDB is never published.** It runs with no authentication and is
    reachable only on the Compose network; adding a `ports:` mapping would put
    an unauthenticated database on the internet.

`make-cert.sh` writes a self-signed certificate, so browsers will warn. HSTS is
commented out in `deploy/nginx/paintomics.conf` while that is true — committing
browsers to HTTPS-only for a host whose certificate they distrust makes the
site unreachable, and the policy is cached. Once you have a DNS name and a
trusted certificate, replace `deploy/nginx/certs/paintomics.{crt,key}`,
uncomment the HSTS header and restart nginx.

## Install from source

For development. Requires Python 3.11 (and only 3.11 — the pins in
`requirements.txt` are resolved against it), MongoDB, R, and the `libcairo2`
shared library, which `cairosvg` renders the pathway PNG exports through.

```bash
git clone https://github.com/ConesaLab/PaintOmics.git
cd PaintOmics

python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

Rscript -e 'install.packages(c("purrr","amap","cluster","factoextra","mclust","optparse"))'

cd PaintomicsServer
python src/launch_server.py             # http://localhost:8000
```

`requirements.txt` at the repository root is the only pip manifest in the tree.

The first launch copies `src/resources/example_serverconf.py` to
`src/conf/serverconf.py` (along with `__init__.py` and `logging.cfg`) and then
never overwrites it. `./start_server.sh` does the same through a conda
environment named `paintomics4` and starts `mongod` if it is not already
running.

### R packages

`generateMetaGenes.R` loads **amap**, **cluster**, **factoextra** and
**mclust**. Those four plus **purrr** are enough to start the application.
Regulatory analysis no longer needs R at all: the MORE R package and its
`runMORE.R` wrapper were removed in September 2026 and MORE runs on the
`more-rs` binary. The container image checks 21 packages at build time, listed in
`deploy/Dockerfile`: the union of every R dependency the repository has carried,
adding **igraph**, **tidyverse** and the ggplot2/ggpubr plotting stack. Most of
that list is historical — the metabolite hub analysis was moved off R entirely,
and `src/tests/test_hub_r_is_gone.py` keeps it that way.

!!! warning "MORE is not in the shipped image"
    The container image does not contain the **MORE** R package, and no
    `more-rs` binary is bundled either. `deploy/Dockerfile` records why: MORE's
    transitive dependency tree has moved past the R 4.2 and toolchain that the
    base image provides, and eleven packages fail for three unrelated reasons
    that pinning does not fix. Until you supply one of the two,
    [regulatory analysis](4_6_Regulatory_omics.md) will not run. PLS1 can run
    on `more-rs`, the Rust port, if you drop an executable at
    `PaintomicsServer/src/common/bioscripts/more-rs` or put one on `PATH`; MLR
    always runs on R and so needs the R package.

## Configuration

All per-site settings live in `PaintomicsServer/src/conf/serverconf.py`. That
file is gitignored, is created once from
`PaintomicsServer/src/resources/example_serverconf.py`, and is then left alone
— by `launch_server.py` on a source install, and by `deploy/entrypoint.sh` at
container start.

Read the template rather than this page for the last word: it is the file that
runs. Every secret in it is read from the environment with an empty default, so
nothing sensitive is ever written into the file. The template also loads a
`.env` from `PaintomicsServer/.env` or the repository root before anything
calls `getenv`, using `setdefault` — a real environment variable always wins,
so a stray `.env` on a production host cannot override what the deployment
configured. A missing or malformed `.env` is ignored silently.

In the Docker stack you set everything in `deploy/.env`, which
`deploy/compose.yaml` passes into the app container as environment variables.

**A setting compose does not forward is not a setting you can change from
`deploy/.env`.** `deploy/compose.yaml` names the variables it passes through,
and anything absent from that list keeps the template's default inside the
container whatever you write in the file — `AI_COMPOUND_SUGGESTIONS_ENABLED`
and `AI_INPUT_CONVERTER` are both in that position today. To change one, add it
to the `environment:` block of the app service.

### Settings a new operator must look at

| Setting | Shipped default | What it does |
|---|---|---|
| `PAINTOMICS_BASE_URL` | `http://localhost:8000` in the template; **compose refuses to start without it** | The externally reachable URL. It is embedded in the links in the welcome, password-reset and job-reminder emails, so a localhost value sends real users links they cannot follow |
| `SERVER_HOST_NAME` | `0.0.0.0` | Interface to listen on |
| `SERVER_PORT_NUMBER` | `8000` | Port |
| `SERVER_ALLOW_DEBUG` | `false` | Never true in production. The smoke test reports it if it is on |
| `SERVER_SUBDOMAIN` | empty | A path prefix, prepended to every route, for serving the application under a path rather than at the root of a host |
| `ADMIN_ACCOUNTS` | `admin` | Comma-separated user names allowed into the admin panel |
| `PAINTOMICS_KEGG_DATA` → `KEGG_DATA_DIR` | `/data/KEGG_DATA` | Where installed pathway data lives |
| `PAINTOMICS_CLIENT_TMP` → `CLIENT_TMP_DIR` | `/data/CLIENT_TMP` | Where user uploads and job output live |
| `MONGODB_HOST` / `MONGODB_PORT` / `MONGODB_DATABASE` | `localhost` / `27017` / `PaintomicsDB` | Compose sets the host to the service name `mongo` |
| `MAX_THREADS` | `6` | Threads one job splits its pathway scoring and feature mapping across |
| `N_WORKERS` | `4` | Queue workers |

Four more are set in the template rather than from the environment, so changing
them means editing `serverconf.py`:

| Setting | Shipped default | What it does |
|---|---|---|
| `SERVER_MAX_CONTENT_LENGTH` | 100 MB | Maximum request body. It must stay equal to nginx's `client_max_body_size` (`100m`) and uWSGI's `limit-post` (`104857600`), or uploads are rejected at whichever limit is lowest |
| `SERVER_MAX_FORM_MEMORY_SIZE` | equal to the above | Werkzeug 3.1 caps urlencoded bodies at 500 kB by default, which is below a large pathway SVG export |
| `MAX_NUMBER_FEATURES` | 1,000,000 | A values file with more rows than this is refused, with an error naming the file and the limit |
| `MAX_CLIENT_SPACE` | 200 MB | **Displayed, not enforced.** Its only consumer is the "Used space" meter in [My files and Jobs](2_2_cloud_drive.md); no upload path refuses a file for exceeding it |

`MAX_WAIT_THREADS` (900 seconds), `JOB_CACHE_MAX_SIZE` (50) and
`KEGG_CACHE_MAX_SIZE` (25) are in the same category. The two cache sizes trade
RAM for reload speed; lower them on a small host.

### Email

Registration mail and password reset need SMTP. Without `SMTP_PASSWORD` the
application still starts and logs a warning. Signing up still works — there is
no activation step on master, the account is usable immediately — but the
welcome mail never arrives and nobody can reset a forgotten password.

| Setting | Shipped default |
|---|---|
| `EMAIL_FROM_ADDRESS` | `noreply@example.org` |
| `EMAIL_FROM_DISPLAY` | `PaintOmics` |
| `SMTP_HOST` | `smtp.sendgrid.net` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | `apikey` (the literal string SendGrid expects) |
| `SMTP_PASSWORD` | empty — this is the secret |
| `EMAIL_REPORT_RECIPIENTS` | empty; a comma-separated list of the addresses that receive error reports, contact-form messages and organism requests. When it is empty they go to `EMAIL_FROM_ADDRESS` |
| `PAINTOMICS_LOGO_PATH` | `/resources/images/paintomics-mark-email.png` — the mark every outgoing message loads, as a path under `PAINTOMICS_BASE_URL`. It must be a raster: Gmail and every Outlook refuse an SVG in an `<img>`, so this cannot be pointed at `paintomics-mark.svg`, the file the application itself uses |

Nothing here is hashed. Older documentation described an `smtp_host` /
`smpt_pass` block; the template carries no such block, and `sendEmail` reads
only the settings above. A `serverconf.py` generated before the SMTP rewrite may
still carry it, commented out and marked deprecated — it is dead either way.

### Job retention

| Setting | Shipped default | Applies to |
|---|---|---|
| `MAX_JOB_DAYS` | 14 | Jobs belonging to a registered account |
| `MAX_GUEST_JOB_DAYS` | 7 | Anonymous jobs, and jobs of guest accounts |
| `MAX_GUEST_DAYS` | 90 | How long a guest account itself survives without a login |

The clock runs from when a job was last **opened**, not when it was created:
the client rewrites the access date every time a job is shown, so a job someone
keeps coming back to never expires. A registered user is emailed a warning in
the last 7 days of a job's life. The 7 and the 14 are quoted verbatim to anyone
who opens a job that has gone ("jobs are automatically removed after 7 days for
guests and 14 days for registered users"), so if you change them, change that
message too — a test in the suite fails if they drift apart.

The sweep itself runs inside the server process on a 24-hour timer; no external
cron is needed. `clean_databases.py` deletes the job's features, pathways,
visual options, AI interpretation and files together. A separate script,
`PaintomicsServer/src/AdminTools/scripts/cleanup_orphaned_data.py`, removes
pathway and feature documents whose job no longer exists — the debris a crashed
run leaves behind, which the retention sweep does not reach. It is a dry run by
default and only deletes with `--run`.

## Species and pathway databases

**A fresh installation has an empty database and an empty organism picker.**
Every species is installed explicitly, in two phases: download, then build.

```bash
cd PaintomicsServer
python src/AdminTools/DBManager.py download --specie=mmu --kegg=1 --mapping=1 --common=1 --reactome=1
python src/AdminTools/DBManager.py install  --specie=mmu
```

In the container, run the same two commands through
`docker compose -f deploy/compose.yaml exec -T app python /app/PaintomicsServer/src/AdminTools/DBManager.py …`.

Both phases are restartable: already-downloaded files are validated and skipped
on a re-run. Rough sizes are ~1.4 GB of shared KEGG reference data, ~856 MB of
shared Reactome data, and 200–400 MB per species. Use `--common=0` for every
species after the first — the common step re-downloads the shared reference
data and dominates the runtime.

The downloads are rate-limited and take hours. `deploy/load-species.sh` chains
the whole first-run sequence (wait for the image build, compose up, smoke test,
then human and mouse) under `nohup` so it survives an SSH disconnect, stopping
on the first failure and naming the log to read. `deploy/swap-and-install.sh`
waits for an in-flight download to finish before recreating the app container
onto a new image, because recreating it mid-download destroys work that
`--common=1` makes expensive to redo.

### Other DBManager commands

| Command | What it does |
|---|---|
| `download` | Fetch KEGG, mapping and (optionally) Reactome data for a species into `KEGG_DATA/download/` |
| `install` | Build the database from what was downloaded and promote it into `KEGG_DATA/current/`. Takes `--specie`, `--species=hsa,mmu,ath` or `--inputfile` |
| `reinstall` | Rebuild from the data already in `current/`, without downloading anything. This is what you want after a change to a build script or to `organismDB.py` — minutes instead of hours |
| `hubdoctor` | Check that every installed species yields a usable metabolite interaction graph. Exits with the number of species that do not, so it can gate a deploy |

A species whose data is missing is skipped with a warning rather than ending
the run, so one absent organism does not cost you the rest of a batch.

### Which databases a species gets

* **[KEGG](1_1_kegg.md)** is installed for every species and cannot be
  deselected in the interface.
* **[Reactome](1_2_reactome.md)** comes from `--reactome=1` at download time,
  but only for species that have a bespoke build script that asks for it —
  16 of the shipped scripts do. Reactome also does not cover every KEGG
  organism: installing one it does not curate fails with a message naming the
  species and telling you to use `--reactome=0`.
* **[MapMan](1_3_mapman.md)** is produced by the build script of five plant
  species (`ath`, `bvu`, `osa`, `sly`, `sot`). There is no flag for it.
* **[OmniPath](1_6_omnipath.md)** has its own installer and serves only human,
  mouse and rat — the web service rejects every other organism:

    ```bash
    python src/AdminTools/omnipathInstaller.py --organism mmu
    ```

A species with no `<code>_resources/build_database.py` falls back to the
default build script, which produces the KEGG-only identifier set. Which
identifier types an organism accepts is therefore a property of how it was
built; see [Supported identifiers](1_4_id.md).

### An organism KEGG does not cover

`customSpeciesInstaller.py` installs a species from a gene-to-KO functional
annotation — the output of eggNOG-mapper, BlastKOALA or KAAS:

```bash
python src/AdminTools/customSpeciesInstaller.py \
    --code=nben --name="Nicotiana benthamiana" \
    --annotation=/path/to/annotation.tsv --scope-organism=nta
```

It synthesises the pathway, cross-reference, identifier-registry and version
collections from KEGG's reference KO pathways, registers the code so that the
organism list regenerates, and makes the species appear in the picker. Hub
data, the pathway interaction network and Reactome/MapMan are deliberately not
produced: those tabs are empty and those checkboxes are absent for such a
species.

## AI settings

The AI features are described in [What the AI does](ai-overview.md). None of
them can run without an API key, whatever the switches say, and each is
switchable on its own, because they cost very differently — one short batched
call against a multi-phase run with literature retrieval.

| Setting | Shipped default | What it controls |
|---|---|---|
| `AI_INTERPRETATION_ENABLED` | `true` | The [pathway interpretation](ai-interpretation.md) itself, and the switch the compound suggestions also test. It does not gate the input converter |
| `AI_COMPOUND_SUGGESTIONS_ENABLED` | `true` | The **Choose for me** button on the Step 2 compound disambiguation card. The server refuses a suggestion run while it is off, but the button is drawn on `AI_INTERPRETATION_ENABLED` and a configured key alone — turning only this one off leaves a button that reports the refusal |
| `AI_INPUT_CONVERTER` | `false` | The [AI input converter](ai-input-converter.md). It ships inert: it spends gateway quota shared with report generation, so a deployment opts in |
| `AI_LLM_PROVIDER` | `csic` | Which entry of `AI_PROVIDERS` to use. `dashscope` and `openrouter` are also defined |
| `AI_CSIC_API_BASE` | `https://llm.iiia.es/v1` | The endpoint. An OpenAI-compatible gateway run by IIIA-CSIC; tokens are self-service from `https://console.llm.iiia.es` |
| `AI_CSIC_API_KEY` | empty — the secret | Without it every AI request fails |
| `AI_CSIC_MODEL` | `deepseek-ai/DeepSeek-V4-Flash-0731` | Pinned to a dated snapshot rather than an alias, so a model cannot be repointed under a running deployment |
| `AI_PUBMED_EMAIL` / `AI_PUBMED_API_KEY` | empty | NCBI E-utilities. With a key the rate limit is 10 requests per second instead of 3 |

An unauthenticated `GET /ai_provider` reports `enabled`, `configured`, the
provider name, the host, the model and whether that host is in the EU. It
carries no secret — `configured` says only whether a key is non-empty. The
interface uses it to name the recipient in the consent notice and to decide
whether to draw the AI controls at all, so on a server with no key those
controls never appear rather than appearing and failing.

If you do not want the features, set `AI_INTERPRETATION_ENABLED=false` rather
than leaving the key empty: the rest of the application runs normally either
way, but the switch hides the controls cleanly instead of letting requests
fail. `deploy/entrypoint.sh` logs a warning at start-up if the feature is
enabled with no key for the selected provider.

## The admin panel

A separate application at `<host>/admin/`, open to accounts named in
`ADMIN_ACCOUNTS` (default `admin`). It has five sections:

| Section | What it does |
|---|---|
| Control panel | Live CPU, memory and swap load, a filesystem table, a **Clean databases now** button that runs the retention sweep on demand, and the site-message table |
| Users | List and delete accounts |
| Organisms | Every KEGG organism, badged installed or not installed, with **Download database** and **Install specie** buttons, category facets, a search box and the download/install log paths |
| Files | The inbuilt reference GTF files offered by the Region-based omic panel |
| Requests | Organism requests and error reports sent by users, which are written to MongoDB before any mail delivery is attempted |

The Control panel's message table is worth one caveat. The application asks the
server for a message of type `starting_message` on every boot and opens a
welcome dialog if one exists — but the dialog's text is hardcoded in the client
and the stored message body is never read, so registering the record acts as a
switch that turns the dialog on, not as a way to write what it says. The
application also sets a `silence` cookie with a two-hour lifetime on load, so
the dialog appears at most once every two hours in a given browser.

The Organisms page has three limits worth knowing before you rely on it. Its
install call never passes `--reactome`, so Reactome cannot be installed from
the browser. It runs `DBManager` synchronously inside the web request, while a
full download takes hours and both uWSGI (`harakiri = 300`) and nginx
(`proxy_read_timeout 300s`) give up after five minutes — so use the command
line for anything but a small update. And despite the page's own subtitle,
removal is not implemented: the Uninstall button is commented out of the
template and the delete route returns failure without calling anything.

## After the install

* **Fetch the example GTF.** `Tools ▸ From Regions to Genes ▸ Load
  example` reads `examplefiles/GTF/sorted_mmu.gtf`, which the repository does
  not ship — only a placeholder. Without it the tool fails immediately with
  "Reference file not found." Run `deploy/fetch-example-gtf.sh`; it downloads
  Ensembl GRCm38, trims it to the feature types RGmatch reads and installs it
  (~566 MB). **Re-run it after every image rebuild**, because `examplefiles/`
  is baked into the image rather than mounted from the data volume. It is
  idempotent and exits at once if the file is already there.
* **Try an example.** The [example datasets](examples.md) exercise the whole
  pipeline end to end and are the fastest check that a deployment works.
* **Back up two volumes.** `paintomics-data` (user jobs and uploads) and
  `mongo-data` hold irreplaceable state. Pathway data is reproducible from KEGG
  and Reactome, so it need not be backed up — but re-downloading it takes
  hours.

```bash
docker compose -f deploy/compose.yaml exec -T mongo \
  mongodump --archive --gzip --db=PaintomicsDB > mongo-$(date +%F).archive.gz
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| A job never finishes | Check `processes` in `deploy/uwsgi.ini` is still 1 |
| The organism picker is empty | No species is installed yet; see [Species and pathway databases](#species-and-pathway-databases) |
| Every database checkbox is tickable, whatever the organism | The client could not read the availability map from the server and is offering all of them, with a note saying so under the checkbox group. This is a degraded state, not a well-stocked server |
| A Reactome install fails on one species | Expected for a species Reactome does not curate. Reinstall it with `--reactome=0` |
| Metagenes fails | Missing R packages — `amap`, `cluster`, `factoextra`, `mclust`. The `Rscript` check in `deploy/README.md` names them |
| Regulatory analysis is greyed out, or a job is refused | No `more-rs` binary is installed. It is gitignored; build it from [TianYuan-Liu/MORE](https://github.com/TianYuan-Liu/MORE) (`rust/`) at the commit `scripts/ci/build-more-rs.sh` pins, and drop it at `PaintomicsServer/src/common/bioscripts/more-rs` |
| Uploads rejected around 100 MB | `client_max_body_size`, `SERVER_MAX_CONTENT_LENGTH` and `limit-post` must all agree |

Four standalone test scripts are worth running before a release, from
`PaintomicsServer`: `python -m src.tests.test_release_hygiene` (no secret is
committed — run this before every tag), `test_pymongo4_compat`,
`test_reactome_install` and `test_bug_fixes`.

## Further reading for maintainers

Two places in the repository go further than this page, and neither is part of
the published site — read them in a checkout.

`deploy/README.md` is the reference for the container stack: configuration,
pathway data, backups, certificates and troubleshooting.

The `docs/dev/` directory holds the maintainer documentation —
`docs/dev/architecture.md` for what serves a request and what holds state,
`docs/dev/deployment.md` for the runbook of the public instance,
`docs/dev/ci.md` for what each workflow checks, and
`docs/dev/troubleshooting.md`.
