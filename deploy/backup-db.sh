#!/bin/bash
# Nightly backup of the PaintOmics database on this VM.
#
# Until the 2026-09-07 cutover this machine's database was a disposable copy of production, so
# losing it cost nothing. It now holds the live paintomics.org user data -- accounts, jobs and AI
# interpretations that exist nowhere else -- and nothing on this VM is backed up off-machine.
#
#   backup-db.sh            take one dump, prune old ones, print what it did
#   backup-db.sh --verify   also restore the newest dump into a scratch database and count it,
#                           because a dump that has never been read is not a backup
#
# Pull these to the laptop periodically; a copy that only lives on the machine it protects is
# not off-site. Job FILES (/data/CLIENT_TMP, ~19 GB) are deliberately not included: they are far
# larger, change constantly, and are covered by the Cinder volume snapshot instead.
set -euo pipefail
# The dumps carry userCollection, password hashes included. Under cron's default
# umask they would land 0644 in a 0755 directory, readable by every local user;
# 077 makes each new dump 0600, and the chmod below covers a directory that
# already existed with the old mode.
umask 077

DIR=/home/tliu/backups
KEEP_DAILY=14
COMPOSE="sudo -n docker compose -f /home/tliu/paintomics4/deploy/compose.yaml"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$DIR/paintomicsdb-$STAMP.archive.gz
LOG=$DIR/backup.log

mkdir -p "$DIR"; chmod 700 "$DIR"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

# Write to a .part and rename only on success, so an interrupted run never leaves a file that
# looks like a usable backup.
log "dumping PaintomicsDB -> $(basename "$OUT")"
if ! $COMPOSE exec -T mongo mongodump --quiet --archive --gzip --db PaintomicsDB > "$OUT.part" 2>>"$LOG"; then
    rm -f "$OUT.part"; log "ERROR: mongodump failed"; exit 1
fi
size=$(stat -c %s "$OUT.part")
# A valid gzipped archive of this database is megabytes; anything tiny means a failed dump that
# still exited 0 (an empty stream, a container that went away mid-write).
if [ "$size" -lt 1000000 ]; then
    rm -f "$OUT.part"; log "ERROR: dump only $size bytes, refusing to keep it"; exit 1
fi
mv "$OUT.part" "$OUT"
log "wrote $(numfmt --to=iec "$size" 2>/dev/null || echo "$size bytes")"

drop_verify_db() {
    $COMPOSE exec -T mongo mongosh --quiet PaintomicsDB_verify --eval 'db.dropDatabase()' >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--verify" ]; then
    # Whatever happens next, the scratch copy goes. Under set -e a restore or
    # count that failed skipped the drop below and left a full second copy of
    # the live database -- password hashes included -- in the running mongo.
    trap drop_verify_db EXIT
    log "verifying: restoring into PaintomicsDB_verify"
    $COMPOSE exec -T mongo mongorestore --quiet --archive --gzip --drop \
        --nsFrom 'PaintomicsDB.*' --nsTo 'PaintomicsDB_verify.*' < "$OUT" 2>>"$LOG"
    counts=$($COMPOSE exec -T mongo mongosh --quiet PaintomicsDB_verify --eval \
        'print(db.userCollection.countDocuments({}) + " users, " + db.jobInstanceCollection.countDocuments({}) + " jobs, " + db.aiInterpretationCollection.countDocuments({}) + " ai")')
    drop_verify_db
    log "verified: $counts"
fi

# Prune by count, not by age: if the cron stops for a month, age-pruning would delete every
# backup there is, exactly when they matter most.
ls -1t "$DIR"/paintomicsdb-*.archive.gz 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | while read -r old; do
    log "pruning $(basename "$old")"; rm -f "$old"
done
log "done; $(ls -1 "$DIR"/paintomicsdb-*.archive.gz 2>/dev/null | wc -l) backups on disk, $(du -sh "$DIR" | cut -f1) total"
