# paintomics.org → Drago VM: the cutover, and what is left

**The cutover happened on 2026-09-07 16:07 UTC.** `paintomics.org` and `www.paintomics.org`
now resolve to 161.111.18.82 (the CSIC Drago VM) and serve a trusted Let's Encrypt certificate
issued at 16:09 UTC. `paintomics.uv.es` is untouched on 147.156.158.21 and keeps running.

| what | when | result |
|---|---|---|
| VM rebooted onto kernel 6.8.0-139 | 16:06 UTC | stack healthy, smoke test 13/13 |
| IONOS A records → 161.111.18.82, TTL 300 | 16:07 UTC | both names; Google and Cloudflare saw it within a minute |
| certbot issued for both names | 16:09 UTC | CN=paintomics.org, valid to 2026-12-06, chain verified from outside |
| production users and jobs restored onto .org | 16:32 UTC | 392 users, 109 jobs, 86 AI records, 388 job-file directories (19 GB) |
| nightly verified database backup on the VM | 16:39 UTC | `deploy/backup-db.sh`, cron 03:17 UTC, 14 kept, first run restore-verified |
| end-to-end verification | 16:34 UTC | smoke test 13/13; both names 200 over trusted TLS; http 301s to https; `.uv.es` still 200 |

Decisions taken by the owner on the day: `.org` starts **with** a copy of production's users and
jobs; the `.uv.es` / `.org` split is a **transition**, so `.uv.es` is expected to redirect to
`.org` later rather than run in parallel forever.

## Rollback

Set both A records back to `147.156.158.21` at IONOS. Nothing on Valencia was changed, so the old
site answers within the 300 s TTL. The VM keeps serving on its own IP.

## Still to do

- **Decide on HSTS.** The certificate is trusted and renews automatically, so it is now safe in
  principle. It is deliberately still off: the header commits browsers to HTTPS-only for a year
  and they cache that, so a future certificate failure would make the site unreachable with no
  quick undo. If you want it, roll it out as `max-age=300` first, confirm nothing breaks over a
  few days, then raise it. The line is commented in `deploy/nginx/paintomics.conf`.
- **Pull the VM's backups off the machine.** `deploy/backup-db.sh` now runs nightly at 03:17 UTC
  and keeps 14 verified dumps in `~/backups`, but they live on the machine they protect. Copy them
  to the laptop periodically, and take a Cinder snapshot of `paintomics-data` before any upgrade.
  Job files (19 GB in `/data/CLIENT_TMP`) are covered by the snapshot, not by the dump.
- **Plan the redirect.** Since this is a transition, decide when `.uv.es` starts redirecting and
  do a final user/job merge at that point.
- **Announce**, and update `deploy/README.md` and the VM's `CLAUDE.md`, which still describe this
  machine as the staging copy.

## Reference: how it was done

## Where things stand (checked, not assumed)

| item | state |
|---|---|
| stack on the VM | app, mongo, nginx all `Up (healthy)`; `deploy/smoke-test.sh` 13/13 |
| image | built 2026-09-06 23:30 from the laptop branch `deploy/drago-cutover-prep` |
| code drift laptop → VM | none (rsync dry run shows only caches and a `.bak-precutover` file) |
| pathway data | 138 databases on both servers; VM 548 collections, 958 indexes |
| config | `PAINTOMICS_BASE_URL=https://paintomics.org`; `PAINTOMICS_EMAIL_DOMAIN=paintomics.uv.es` (deliberate: guest logins) |
| nginx | `server_name paintomics.org www.paintomics.org _`; ACME path served from `deploy/certbot-webroot` |
| certificate | still self-signed (CN=localhost); certbot 2.9 installed, no account yet |
| certificate automation | `deploy/issue-cert.sh` + cron every 5 min (`--if-dns`); logs in `~/cutover/` |
| DNS | apex and www → 147.156.158.21 at IONOS's own nameservers, TTL 300 s |
| users / jobs | VM: 4 guest users, 119 test jobs. Production: 392 users (70 guests), 108 jobs, 10–20 new jobs a day; PaintomicsDB 2 GB of data, job files 19 GB |
| AI usage watch | the Mac menu-bar app watches both servers (`uv` and `drago` blocks) |
| VM reboot | **pending**: kernel 6.8.0-139 installed, 6.8.0-138 running |

No soak loop or DNS watcher was running as a process on the VM or the Mac when this was
checked; the cron above is what now guarantees the certificate is issued at the flip.

### Before the flip

1. **Reboot the VM now, not during the window.** `ssh dragocloud-vm sudo reboot`, wait
   a minute, then `ssh dragocloud-vm 'cd paintomics4 && sudo docker compose -f deploy/compose.yaml ps && ./deploy/smoke-test.sh'`.
   The containers are `restart: unless-stopped`, so they come back on their own.
2. **Push the branch.** The laptop clone's `deploy/drago-cutover-prep` is 4 commits ahead
   of `origin/master` and exists nowhere else. Add `deploy/issue-cert.sh` and this file, push,
   and merge, so the running image is reproducible from GitHub.
3. **Decide the two open questions** (nothing below depends on them until step 3.3):
   - does `.org` start with a copy of production's 392 users and 108 jobs, or empty?
   - is the `.uv.es` / `.org` split permanent, or a transition after which `.uv.es` redirects?
4. Optional: `ssh dragocloud-vm ~/paintomics4/deploy/issue-cert.sh --check` — it should say
   "ACME webroot is served: ok" and "DNS not here yet".

### The flip

Change the A records for `paintomics.org` **and** `www.paintomics.org` to `161.111.18.82`.
Leave the TTL at 300 s; that is the rollback time.

Within ten minutes the cron notices, certbot issues for every name that already resolves
here, nginx is restarted with the trusted certificate, and HTTPS is verified with a real
chain check. If verification fails the previous certificate is restored automatically.
Watch it: `ssh dragocloud-vm tail -f ~/cutover/issue-cert.log`.
To do it by hand instead: `ssh dragocloud-vm ~/paintomics4/deploy/issue-cert.sh`.

### Right after the flip

1. **Verify from outside** (your Mac):
       dig +short paintomics.org                       # 161.111.18.82
       curl -sI https://paintomics.org/healthz | head -1
       echo | openssl s_client -connect paintomics.org:443 -servername paintomics.org 2>/dev/null | openssl x509 -noout -issuer -dates
   Then run a real analysis in a browser at https://paintomics.org and trigger the AI
   interpretation: it must appear in the **drago** block of the menu-bar app.
2. **Enable HSTS** now that the certificate is trusted: uncomment the
   `Strict-Transport-Security` line in `deploy/nginx/paintomics.conf`, then
   `sudo docker compose -f deploy/compose.yaml restart nginx`.
3. **Migrate users and jobs, if decided.** Do it right after the flip: production keeps
   receiving jobs until caches expire, and anything created on `.uv.es` after the copy simply
   stays on `.uv.es`, which keeps running. Production is Mongo 4.4, the VM Mongo 7; the
   archive restores across that gap (rehearsed). Everything is staged in advance by
   `deploy/migrate-from-uv.sh`, run on the Mac with the VPN up:
       ./deploy/migrate-from-uv.sh stage      # today and again right after the flip: incremental
       ./deploy/migrate-from-uv.sh restore    # once, after the flip: replaces the VM's users/jobs
       ./deploy/migrate-from-uv.sh files      # only the job-file sync, if a pass was interrupted
   `stage` dumps PaintomicsDB on production, relays it and the job files
   (`/home/tian/database/CLIENT_TMP`, 19 GB) through `~/Desktop/paintomics-cutover` on the Mac
   to `~/_tmp/cutover` on the VM, and rehearses the restore into a scratch database there.
   The Mac copy doubles as the only off-site backup of production's user data.
   First pass done 2026-09-07 (14:48 to 16:01 UTC): the dump took 8 min on production and
   restored into the VM's scratch database with 392 users, 108 jobs, 84 AI records and all
   16 indexes; the job files took 39 min to the Mac and 21 min on to the VM. Staged on the
   VM: `~/_tmp/cutover/paintomicsdb-20260907T144805Z.archive.gz` (204 MB) and
   `~/_tmp/cutover/client_tmp` (388 user directories, 116,012 files, 19 GB). The rerun at
   the flip moves only the delta, so budget about 15 min for it plus `restore`.
   Guest accounts keep working because `PAINTOMICS_EMAIL_DOMAIN` stays `paintomics.uv.es`.
4. **Back up the VM's own state from now on.** Until now its database was a copy of
   production; after migration it holds unique user data. Weekly:
       sudo docker compose -f ~/paintomics4/deploy/compose.yaml exec -T mongo \
           mongodump --archive --gzip --db PaintomicsDB > ~/backups/paintomicsdb-$(date +%F).archive.gz
   and pull it to the laptop. A Cinder snapshot of `paintomics-data` before any upgrade.
5. Announce, and update `deploy/README.md` and the VM's `CLAUDE.md`, which still call this
   machine the staging copy.

### Rollback (detail)

Set the two A records back to `147.156.158.21`. Nothing on Valencia was changed, so the old
site answers as soon as the 300 s TTL expires. The VM keeps serving on its IP.
If the certificate swap ever leaves nginx broken: `issue-cert.sh` restores the previous pair
itself; by hand, copy from `deploy/nginx/certs/backup-<stamp>/` and restart nginx.
