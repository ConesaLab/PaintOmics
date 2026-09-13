#!/bin/bash
# Stage production's users, jobs and job files on the Drago VM; apply them with `restore`.
#
# Runs on the Mac: production (paintomics.uv.es) is only reachable through the garnatxa jump
# host over the VPN, the VM only from the internet, and neither can reach the other -- so the
# Mac relays. Every step is incremental: run `stage` today and again right after the DNS flip,
# and the second run moves only what changed in between.
#
#   migrate-from-uv.sh stage     dump PaintomicsDB on production (read-only there), relay the
#                                dump and the job files to the VM's ~/_tmp/cutover, and rehearse
#                                the restore into a scratch database on the VM. The VM's live
#                                database and job files are NOT touched.
#   migrate-from-uv.sh files     only the job-file sync (steps 5 and 6), for reruns
#   migrate-from-uv.sh restore   replace the VM's PaintomicsDB and job files with the staged
#                                copy. This is the decision-gated step; run it once, after the
#                                flip, if .org is to start with production's users and jobs.
set -euo pipefail

UV=tian@paintomics.uv.es
JUMP=garnatxa
JUMP_IP=10.1.0.6
VM=dragocloud-vm
UV_CLIENT_TMP=/home/tian/database/CLIENT_TMP      # production's job files (19 GB, one dir per user)
LOCAL=$HOME/Desktop/paintomics-cutover            # the Mac's relay copy (also an off-site backup)
STAGE=_tmp/cutover                                # under tliu's home on the VM
COMPOSE="sudo -n docker compose -f /home/tliu/paintomics4/deploy/compose.yaml"
KEYCHAIN_ITEM="IntelliJ Platform SshConfigPassword — paintomics.uv.es:22 0fa0a855-d97e-4e0c-9687-d935b6f70474"

SSHPASS=$(security find-generic-password -s "$KEYCHAIN_ITEM" -w 2>/dev/null) \
    || { echo "no keychain password for \"$KEYCHAIN_ITEM\"" >&2; exit 78; }
export SSHPASS
command -v sshpass >/dev/null || { echo "sshpass not on PATH" >&2; exit 78; }
UV_SSH="sshpass -e ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=20 -J $JUMP"

uv()  { $UV_SSH "$UV" "$@"; }
vm()  { ssh -o BatchMode=yes "$VM" "$@"; }
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

stage() {
    nc -z -w6 "$JUMP_IP" 22 || { echo "VPN down: $JUMP_IP unreachable" >&2; exit 1; }
    mkdir -p "$LOCAL/client_tmp"
    local ts dump
    ts=$(date -u +%Y%m%dT%H%M%SZ); dump="paintomicsdb-$ts.archive.gz"

    log "1/6 dumping PaintomicsDB on production -> ~/backups/$dump (read-only for production)"
    uv "mongodump --quiet --db PaintomicsDB --archive --gzip > ~/backups/$dump && ls -la ~/backups/$dump"

    log "2/6 pulling the dump to $LOCAL"
    sshpass -e scp -q -o PreferredAuthentications=password -o PubkeyAuthentication=no -J "$JUMP" \
        "$UV:~/backups/$dump" "$LOCAL/"

    log "3/6 pushing the dump to the VM ~/$STAGE"
    vm "mkdir -p ~/$STAGE"
    scp -q "$LOCAL/$dump" "$VM:$STAGE/"

    log "4/6 rehearsing the restore into PaintomicsDB_staged on the VM (live database untouched)"
    vm "$COMPOSE exec -T mongo mongorestore --quiet --archive --gzip --drop \
            --nsFrom 'PaintomicsDB.*' --nsTo 'PaintomicsDB_staged.*' < ~/$STAGE/$dump \
        && $COMPOSE exec -T mongo mongosh --quiet PaintomicsDB_staged --eval \
            'print(\"   staged: users\", db.userCollection.countDocuments({}), \"jobs\", db.jobInstanceCollection.countDocuments({}), \"ai\", db.aiInterpretationCollection.countDocuments({}), \"collections\", db.getCollectionNames().length, \"indexes\", db.getCollectionNames().reduce((n,c)=>n+db.getCollection(c).getIndexes().length,0))' \
        && $COMPOSE exec -T mongo mongosh --quiet PaintomicsDB_staged --eval 'db.dropDatabase()' >/dev/null"

    files
    log "staged. Dump: $LOCAL/$dump and VM:~/$STAGE/$dump."
}

# Steps 5 and 6 on their own: the long part, safe to rerun any number of times.
files() {
    mkdir -p "$LOCAL/client_tmp"
    log "5/6 syncing job files production -> Mac (incremental, resumable)"
    rsync -a --partial --delete --stats -e "$UV_SSH" "$UV:$UV_CLIENT_TMP/" "$LOCAL/client_tmp/"
    log "6/6 syncing job files Mac -> VM ~/$STAGE/client_tmp (incremental, resumable)"
    vm "mkdir -p ~/$STAGE/client_tmp"
    rsync -a --partial --delete --stats "$LOCAL/client_tmp/" "$VM:$STAGE/client_tmp/"
    log "job files staged: $(du -sh "$LOCAL/client_tmp" | cut -f1) in $LOCAL/client_tmp and VM:~/$STAGE/client_tmp"
}

restore() {
    local dump
    dump=$(vm "ls -t ~/$STAGE/paintomicsdb-*.archive.gz 2>/dev/null | head -1")
    [ -n "$dump" ] || { echo "nothing staged on the VM; run stage first" >&2; exit 1; }
    log "restoring $dump into PaintomicsDB on the VM -- this replaces its users and jobs"
    vm "$COMPOSE exec -T mongo mongorestore --quiet --archive --gzip --drop --nsInclude 'PaintomicsDB.*' < $dump"
    log "copying the staged job files into the app container's /data/CLIENT_TMP"
    # `docker cp` keeps the uid of the staged copy (tliu, 1000). The server runs as
    # uid 1001 and cannot create a job directory inside a 1000-owned user directory,
    # which is exactly what happened on the 2026-09-07 restore: every job by a
    # migrated or anonymous user failed with "Permission denied:
    # /data/CLIENT_TMP/nologin/tmp/<jobID>" until the tree was chowned by hand.
    # The entrypoint now repairs ownership on every start, but the copy must not
    # depend on which image is running, so hand the files over here as well.
    vm "sudo -n docker cp ~/$STAGE/client_tmp/. paintomics-app-1:/data/CLIENT_TMP/ \
        && $COMPOSE exec -T -u 0 app chown -R paintomics:paintomics /data/CLIENT_TMP \
        && $COMPOSE restart app"
    vm "$COMPOSE exec -T mongo mongosh --quiet PaintomicsDB --eval \
        'print(\"VM now: users\", db.userCollection.countDocuments({}), \"jobs\", db.jobInstanceCollection.countDocuments({}), \"ai\", db.aiInterpretationCollection.countDocuments({}))'"
}

case "${1:-}" in
    stage)   stage ;;
    files)   files ;;
    restore) restore ;;
    *)       echo "usage: $0 stage|files|restore" >&2; exit 64 ;;
esac
