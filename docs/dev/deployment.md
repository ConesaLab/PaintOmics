# Deployment

> **This page is a first draft, reconstructed from the repository and from the
> maintainer's private operating notes. It has not yet been reviewed by the
> person who actually runs the public instance. Read it as a starting point and
> confirm each step before relying on it.**
>
> Nothing here contains credentials, host addresses or access routes. Those live
> in the operator's password manager and are not published.

There are two deployments, and they are not the same shape.

| | Container stack | The public instance |
|---|---|---|
| Where | Anywhere with Docker | A university host, reachable only from inside its network through a jump host |
| Built by | `deploy/build-image.sh` | Not built — files are synchronised into a checkout |
| Runs as | Compose: nginx → app → MongoDB | systemd unit driving uWSGI, behind the host's nginx |
| Config | `deploy/.env` | `PaintomicsServer/src/conf/serverconf.py` plus a systemd `EnvironmentFile` |
| Documented in | `deploy/README.md` | this page |

`cd.yml` exercises the container stack on every push to `master` and runs
`deploy/smoke-test.sh` against it. Nothing in CI touches the public instance;
there is no step, host or credential for it.

## The container stack

`deploy/README.md` is the operator guide and is the authority for this path.
In outline:

```bash
deploy/build-image.sh          # packs deploy/app.tar and builds the image
cp deploy/env.example deploy/.env   # then fill it in
docker compose -f deploy/compose.yaml up -d
deploy/smoke-test.sh           # non-zero on the mistakes that are expensive in production
```

Two constraints must not be relaxed, and the smoke test checks both:

1. **uWSGI runs a single process.** The job queue (`src/common/PySiQ.py`) lives
   in the memory of the process that accepted the request. A second worker gets
   its own empty queue and jobs disappear with no error. Concurrency comes from
   threads, not processes.
2. **MongoDB is never published to the host.** It runs without authentication
   and is reachable only on the Compose network. Do not add a `ports:` mapping.

`deploy/make-cert.sh` produces a *self-signed* certificate as an interim
measure. Replace it before anyone else uses the instance.

## The public instance

### Deploy from `origin/master`, never from a feature branch

Export the tree from `origin/master`, not from the commit of the branch you just
merged. A feature branch does not contain whatever else merged to `master` while
you were working, and every individual file you ship will still hash-verify
correctly against your own commit — so the deploy looks clean while the site
silently keeps an older version of files you did not touch. This has happened:
two merged pull requests sat live on GitHub for hours without reaching the
server, and it was noticed by a person looking at the page, not by any check.

```bash
git fetch origin master
git archive origin/master --prefix=export/ | tar -x -C /tmp
```

### Prefer a per-file sync with no `--delete`

For anything short of a whole-tree upgrade, name the files explicitly:

1. **Hash-check first.** For each file you are about to replace, compare the
   remote copy (`git hash-object`) with the blob in the commit the server is
   supposed to be at (`git rev-parse <commit>:<path>`). Any mismatch is
   uncommitted work living only on the server; stop and find out what it is
   before overwriting it.
2. `rsync -a --checksum --files-from=<list>` with **no `--delete`**.
3. **Hash-check again afterwards**, and confirm every file now matches the
   commit you deployed.

This sidesteps the entire question of whether the protect list is complete,
because nothing you did not name is ever touched.

### If you must run a whole-tree sync

A `git archive` export contains no gitignored files at all, so a
`rsync --delete` run will queue deletions for every file that exists only on the
server. Some of those are load-bearing:

- the per-site `serverconf.py`, and the `AdminTools/conf` symlink to it;
- `.env` files holding the AI gateway and PubMed keys — note that these exist at
  more than one level of the tree, and the protect list has historically been
  written for only one of them;
- the `more-rs` binary, which is gitignored, exists only on the server, and is
  the **default** MORE engine — deleting it breaks every MORE job;
- example data files that predate the tracked datasets and are still read at
  runtime;
- logs, and the server's own stale `.git` directory.

Always run `--dry-run --itemize-changes` first and **read every `*deleting`
line**. Never pipe that dry run through `head`: SIGPIPE kills rsync mid-listing,
and the truncated output reads as "only two files differ".

Before trusting the protect list, re-audit it against a fresh `find` on the
server rather than assuming it is still exhaustive. It is written against
whatever existed when it was last edited, and nothing enforces that it keeps up.

### Restart only when server code changed — and verify it

Client-only changes need no restart. A restart is not free: sessions are held in
the memory of the running process (`UserSessionManager` keeps a plain dict, and
Mongo has no session collection), so **restarting logs every active user out**.
Their browser keeps its cookies, so the next request looks authenticated to them
and fails server-side, surfacing as a puzzling authorisation error rather than a
login prompt.

When you do restart, confirm it actually happened:

```bash
systemctl show paintomics4.service -p ActiveEnterTimestamp
```

`is-active: active` proves nothing — the service was already active. Compare the
timestamp before and after.

One trap that produces a convincing false success: a privileged command sent
over SSH inside a double-quoted string has its shell variables expanded **on the
remote host**, where the local variable holding the password does not exist.
`sudo` then receives nothing, the restart never happens, the service stays up on
the old code, and every other line of the deploy script reports success. Pass
the password on the command's standard input rather than interpolating it into
the command string.

### Secrets do not travel with the code

`git archive` cannot carry gitignored files, so no deploy ever ships a secret.
On the server they come from two places:

- **`serverconf.py`**, generated once per deployment from
  `src/resources/example_serverconf.py` and never overwritten afterwards. Every
  secret in it is read with `os.getenv` and an empty default.
- **A systemd `EnvironmentFile` drop-in** for the service unit.

Note that **uWSGI has no `env-file` option**. `paintomics4.ini` carried an
`env-file =` line for months; uWSGI ignores unknown ini keys silently, so it
read as working configuration while delivering nothing, and `SMTP_PASSWORD` was
empty in every worker — user-submitted reports were discarded rather than merely
undelivered. Verify with:

```bash
systemctl show paintomics4.service -p EnvironmentFiles
sudo cat /proc/$(pgrep -f uwsgi | head -1)/environ | tr '\0' '\n' | sort
```

Because that file had been inert, making it work *activates* everything else in
it. After changing how the environment is delivered, diff what the process
actually has now against what it had before, and confirm each newly-live setting
is intended.

## Species data

Pathway data is installed per species and is the slow, destructive part —
measured in hours and hundreds of gigabytes, not minutes.

Run the installer under a Python 3.9 interpreter, not the 3.11 environment the
application uses: `DBManager.py` goes through `scriptine`, which calls
`inspect.getargspec`, removed in 3.11.

```bash
python3.9 src/AdminTools/DBManager.py download --specie=mmu --kegg=1 --mapping=1 --common=1 --reactome=1
python3.9 src/AdminTools/DBManager.py install  --specie=mmu
```

Use `--common=0` for every species after the first; the common step re-downloads
the shared KEGG reference data and dominates the runtime. Reactome curates human
and infers about twenty other species, so `--reactome=0` for the rest.

A reinstall promotes `download/<species>` over `current/<species>`. Generated
files that no download ever contains — hub data, the pathway network JSON, the
Reactome and MapMan build products — live only in `current/` and have been lost
this way. Never bulk-reinstall species on the public instance to fix one of
them, and read the guard's refusal rather than exempting the thing it names.

## Mail that bounces

The server cannot see bounces. Gmail's SMTP accepts an address that does not
exist and sends the bounce later to our own mailbox, so every run that mails
such an address bounces again. After reading a bounce there, turn mail off for
that account:

```bash
docker exec -w /app/PaintomicsServer paintomics-app-1 \
    python -m src.AdminTools.scripts.user_mail disable someone@example.org --reason "bounced: no such user"
```

`status <address>` shows the switch, `list` shows every account that has it,
and `enable <address>` turns mail back on. Nothing else about the account
changes: the user can still sign in and their jobs are kept. Every message to a
user goes through `src/common/UserMail.py`, which honours the switch, and an
address the SMTP server refuses while sending (a 5xx answer) is switched off
automatically. Guest accounts are never mailed: their addresses are made up.

## Retention

The daily cleaning run (`AdminTools/scripts/clean_databases.py`, also behind the
admin panel's *Clean databases* button) removes anonymous and guest jobs after
`MAX_GUEST_JOB_DAYS` and registered users' jobs after `MAX_JOB_DAYS`, counted
from the day the job was last opened. Registered users get one warning a day at
most, listing every job that is due to go.

It also removes files under `CLIENT_TMP/nologin` that no job in the database
owns or names and that are older than `MAX_GUEST_JOB_DAYS`. Preview a run
without changing anything:

```bash
docker exec -w /app/PaintomicsServer paintomics-app-1 \
    python -m src.AdminTools.scripts.clean_databases --dry-run
```

## After every deploy

- `deploy/smoke-test.sh` for the container stack.
- For the public instance: fetch the site over HTTPS and confirm a 200; confirm
  a versioned asset you changed is being served with its new `?v=` marker;
  re-run the hash check over the files you shipped; and open one real job end to
  end in a browser.
- If server code changed, confirm the restart timestamp moved.

## Rollback

The previous release's virtual environment is kept alongside the current one for
exactly this reason, and a dated backup tarball of the application tree is taken
before large deploys. Rolling back is: restore the tree, point the systemd unit
back at the previous environment — the unit references that path in **two**
places, both of which must be changed — and restart, verifying the timestamp.

Rolling back does **not** roll back the database. An installer run or a schema
change is not undone by restoring the application tree.
