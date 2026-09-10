# Deploying PaintOmics AI

The stack is three containers: **nginx** (TLS, reverse proxy) → **app**
(Flask + uWSGI + the in-process job queue) → **mongo** (MongoDB 7, not
published).

## Requirements

- Docker Engine 24+ with the Compose plugin
- Outbound HTTPS to `rest.kegg.jp`, `reactome.org`, `ftp.ebi.ac.uk`,
  `packagemanager.posit.co`, PyPI and the LLM gateway
- Inbound TCP 80 and 443
- Disk sized for the species you install (see [Pathway data](#pathway-data))

## Install

```bash
git clone https://github.com/ConesaLab/paintomics4.git
cd paintomics4

cp deploy/env.example deploy/.env
$EDITOR deploy/.env               # see "Configuration" below

./deploy/make-cert.sh <your-hostname-or-ip>

docker compose -f deploy/compose.yaml up -d --build
```

The first build takes 15–30 minutes; most of it is R packages.

Then check it came up correctly:

```bash
./deploy/smoke-test.sh                 # defaults to deploy/compose.yaml
```

It verifies the things that are cheap to check here and expensive to discover
in production: the three containers, pymongo 4, the R packages that
`generateMetaGenes.R` and `hubAnalysis.R` need, that MongoDB is not published to
the host, that HTTP redirects to HTTPS, that debug is off, and that uWSGI runs a
single process — the job queue is in-process, so more than one silently loses
jobs. Exits non-zero if any of that is wrong.

It reports missing data as a NOTE rather than a failure, because a deployment
that has not loaded data yet is legitimate. It does not check pathway data at
all, deliberately — a fresh deployment has none. It does check the example GTF
below, so run it again after fetching that and the note should be gone.

## Configuration

Everything lives in `deploy/.env`. Nothing is baked into the image, and
`PaintomicsServer/src/conf/serverconf.py` is generated at container start from
`src/resources/example_serverconf.py`.

| Variable | Consequence if unset |
|---|---|
| `PAINTOMICS_BASE_URL` | **Container refuses to start.** It is embedded in activation emails, so a wrong value silently breaks registration. |
| `SMTP_PASSWORD` | App starts; registration and password-reset email cannot be sent, so **new users cannot activate accounts**. Logged as a warning at start-up. |
| `AI_CSIC_API_KEY` | App starts; AI interpretation requests fail. Set `AI_INTERPRETATION_ENABLED=false` to disable the feature cleanly. |
| `AI_CSIC_FALLBACK_MODELS` | Defaults to `default/llm`: when the pinned model stops being served, calls go to the gateway's default alias and each result records which model answered. Empty means no fallback, and an outage of the pinned model is an outage of every AI feature. |
| `AI_INPUT_CONVERTER` | Defaults to `false`: every spreadsheet a user uploads, and every file the format check rejects, ends in *"AI file conversion is not enabled on this server"*. Set it to `true` on any deployment that has a gateway key. |
| `AI_PUBMED_API_KEY` | Works, but NCBI rate-limits to 3 req/s instead of 10. |

**Every setting must be listed in `compose.yaml`.** The app service's `environment`
block is an allowlist. A variable that is set in `deploy/.env` but not named there is
silently dropped: the container never sees it, `serverconf.py` reads its default, and
the only symptom is a feature that stays off after a deploy that was supposed to turn
it on. Adding a setting means adding it to `env.example`, to `compose.yaml` **and** to
the template; `smoke-test.sh` checks that every key in `.env` reaches the container,
and `test_input_converter_survives_deploy` checks the same for `env.example`.

## Two constraints that must not be relaxed

**`processes = 1` in `uwsgi.ini`.** `src/common/PySiQ.py` is an in-process,
thread-backed job queue holding all job state in the memory of the process that
accepted the request. With two workers, a job submitted to worker A is invisible
to worker B: submission succeeds, the status endpoint is served by a process
that has never heard of the job, and it appears to hang forever. Scaling out
requires replacing PySiQ with a shared broker first. Concurrency comes from
`threads`.

**MongoDB is never published.** It runs without authentication and is reachable
only on the Compose network. Adding a `ports:` mapping would expose an
unauthenticated database to the internet.

## Pathway data

A fresh deployment has an empty database. Install species explicitly.

```bash
# Download, then build. Human, with Reactome:
docker compose -f deploy/compose.yaml exec -T app \
  python /app/PaintomicsServer/src/AdminTools/DBManager.py \
  download --specie=hsa --kegg=1 --mapping=1 --common=1 --reactome=1

docker compose -f deploy/compose.yaml exec -T app \
  python /app/PaintomicsServer/src/AdminTools/DBManager.py \
  install --specie=hsa
```

Repeat with `--specie=mmu` for mouse. Use `--common=0` on every species after
the first: the common step re-downloads the shared KEGG reference data and takes
far longer than the species-specific part.

Rough sizes: shared common data ~1.4 GB, Reactome shared ~856 MB, and roughly
200–400 MB per species. All 94 KEGG species with Reactome comes to ~219 GB.

**Not every KEGG organism exists in Reactome.** Reactome curates human and infers
about 20 other species. Installing one it does not cover fails with a clear
message naming the species; use `--reactome=0` for those.

**Ensembl identifiers come with the mapping data.** `download --mapping=1` fetches,
for every organism Ensembl or Ensembl Genomes annotates, its EntrezGene and UniProt
cross-reference dumps, and the build files them as `ensembl_gene`,
`ensembl_transcript`, `ensembl_peptide` and `entrezgene` tables linked to the KEGG
identifiers. Species with their own `AdminTools/scripts/<code>_resources/` declare the
dumps in `download_conf.py`; every other species is looked up in
`AdminTools/scripts/common_resources/ensembl_genebuilds.json`. A species missing from
both gets KEGG's conversion lists only. To see what is installed, and whether it works:

```bash
# one line per species: rows per identifier table, and whether the Ensembl tables exist
docker compose -f deploy/compose.yaml exec -T -w /app/PaintomicsServer app \
  sh -c 'PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py census'
# sample 100 Ensembl gene ids per species and translate them through the real mapper
docker compose -f deploy/compose.yaml exec -T -w /app/PaintomicsServer app \
  sh -c 'PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py verify --sample 100'
```

`census` exits 1 while a species with a registered genebuild lacks its Ensembl
tables; refresh it with `download --specie=<code> --kegg=0 --mapping=1` followed by
`install --specie=<code> --common=0` (the KEGG data is copied from the installed
tree, only the mapping is fetched again). Run installs one at a time: they rewrite
`species.json` and share `/tmp/xref.tmp`. When KEGG adds an organism, register its
genebuild with `ensembl_census.py registry --species=<code> --merge`.

## Reference GTF for the Regions2Genes example

`Supporting tools -> From Regions to Genes -> Load example` reads
`examplefiles/GTF/sorted_mmu.gtf`, which the repository does not ship — only a
`GTF/.dummy` placeholder. Without it the tool fails immediately with
"Reference file not found.":

```bash
deploy/fetch-example-gtf.sh            # defaults to container paintomics-app-1
```

It downloads Ensembl GRCm38 (mm10 — the assembly the example BED was called
against), trims it to the feature types RGMatch reads, and installs it. Takes a
few minutes and lands ~566 MB.

**Re-run this after every image rebuild.** `examplefiles/` is baked into the
image rather than mounted from the `paintomics-data` volume, so a rebuilt image
loses the GTF. The script is idempotent: it exits immediately if the file is
already in place.

## Everyday operations

```bash
docker compose -f deploy/compose.yaml ps
docker compose -f deploy/compose.yaml logs -f app
docker compose -f deploy/compose.yaml restart app
docker compose -f deploy/compose.yaml down          # keeps volumes
```

Deploying a new version:

```bash
git pull
docker compose -f deploy/compose.yaml up -d --build
```

Volumes survive; pathway data is not re-downloaded.

## Backups

Two volumes hold irreplaceable state: `paintomics-data` (user jobs and uploads)
and `mongo-data`.

```bash
docker compose -f deploy/compose.yaml exec -T mongo \
  mongodump --archive --gzip --db=PaintomicsDB > mongo-$(date +%F).archive.gz
```

Pathway data is reproducible from KEGG and Reactome, so it need not be backed up
— but re-downloading it takes hours.

On the Drago VM, take a Cinder snapshot before any upgrade. The volume quota has
400 GiB free specifically so snapshots are possible.

## Switching to a trusted certificate

`make-cert.sh` produces a self-signed certificate, so browsers warn. HSTS is
deliberately commented out in `nginx/paintomics.conf` while that is true —
committing browsers to HTTPS-only for a host whose certificate they distrust
makes the site unreachable, and the policy is cached.

Once a DNS name exists, replace `nginx/certs/paintomics.{crt,key}`, uncomment
the HSTS header, and `docker compose restart nginx`.

## Troubleshooting

**A job never finishes.** Check `processes` in `uwsgi.ini` is still 1.

**"AI file conversion is not enabled on this server."** `AI_INPUT_CONVERTER` is off in the
container. Set `AI_INPUT_CONVERTER=true` in `deploy/.env` and recreate the app
(`docker compose -f deploy/compose.yaml up -d`); environment changes need a recreate,
not a restart. If it is already `true` in `.env`, check it is listed in `compose.yaml`
-- `deploy/smoke-test.sh` reports every `.env` key the container does not see.

**"The AI service did not answer" / interpretation never finishes.** Ask the gateway
directly, with the container's own configuration:

```bash
docker compose -f deploy/compose.yaml exec -T app \
  python /app/PaintomicsServer/src/AdminTools/check_llm_gateway.py
```

It asks every model on the ladder and ends with one verdict line: `OK` (the pinned model
answers), `DEGRADED` (only a fallback answers, so features work but on another model),
`FAIL` (nothing answers) or `SKIP` (no key). Exit 0 / 3 / 1 / 2. The smoke test runs it
too and reports `DEGRADED` as a note rather than a failure; the nightly job fails on
anything but `OK`, because the pinned model being down is worth an alert.

**Reactome install fails on a species.** Expected for species Reactome does not
cover. The error names the species; reinstall with `--reactome=0`.

**Reactome downloads repeatedly fail for the same pathway.** Previously, an HTTP
error body could be cached as a `.json` file and reused forever. That is fixed —
invalid cached downloads are now detected and re-fetched — but a download
directory poisoned by an older version self-heals only on the next run.

**Hub Analysis or Metagenes fails.** These shell out to R. Verify inside the
container:

```bash
docker compose -f deploy/compose.yaml exec app \
  Rscript -e 'for (p in c("purrr","amap","cluster","factoextra","mclust")) \
              if (!requireNamespace(p, quietly=TRUE)) stop("missing: ", p); cat("ok\n")'
```

**Uploads rejected at ~100 MB.** `client_max_body_size` (nginx),
`SERVER_MAX_CONTENT_LENGTH` (app) and `limit-post` (uWSGI) must all agree.

## Tests

```bash
cd PaintomicsServer
python -m src.tests.test_release_hygiene     # no secret is committed
python -m src.tests.test_pymongo4_compat     # no removed pymongo API is used
python -m src.tests.test_reactome_install    # Reactome installer invariants
python -m src.tests.test_bug_fixes           # multi-condition statistics
```

`test_release_hygiene` should be run before every tag: it is what stops a
credential reaching a public repository again.
