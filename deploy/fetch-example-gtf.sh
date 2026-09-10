#!/usr/bin/env bash
#
# Install the mouse reference GTF that the "From Regions to Genes" example needs.
#
# Bed2GenesServlet.py hardcodes examplefiles/GTF/sorted_mmu.gtf for its example
# run, but that file has never been in the repository -- only a GTF/.dummy
# placeholder. Without it the tool fails immediately with "Reference file not
# found.", so this script is part of bringing a deployment up, not an optional
# extra.
#
# Assembly: GRCm38 (mm10), not GRCm39. The example BED carries mm10 coordinates.
# Checked by mapping the shipped regions onto both assemblies and comparing the
# result against the shipped already-mapped gene table
# (examplefiles/dnase_values.tab): GRCm38 recovers 66.2% of those genes,
# GRCm39 only 37.8%. Do not "upgrade" this to GRCm39 -- the example data would
# have to be re-called first.
#
# The GTF is trimmed to the three feature types RGMatch reads (exon, transcript,
# gene) on the primary chromosomes. DHS_exon_association.py ignores CDS, UTR and
# codon rows entirely, so dropping them is lossless here and takes the file from
# 1.03 GB to 566 MB.
#
# Usage:
#   deploy/fetch-example-gtf.sh [container-name]
#
# Since 2026-09-10 examplefiles/GTF is a bind mount (deploy/gtf on the host), so
# this no longer has to be re-run after an image rebuild -- the docker cp below
# lands on the host directory and persists. Registering the file in MongoDB is a
# separate step; see "The reference GTF library survives rebuilds" in
# deploy/README.md. The example itself reads the path directly and needs no row.

set -euo pipefail

CONTAINER="${1:-paintomics-app-1}"
GTF_URL="https://ftp.ensembl.org/pub/release-102/gtf/mus_musculus/Mus_musculus.GRCm38.102.gtf.gz"
DEST="/app/PaintomicsServer/src/examplefiles/GTF/sorted_mmu.gtf"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

log() { printf '==> %s\n' "$*"; }

if docker exec "$CONTAINER" test -s "$DEST" 2>/dev/null; then
    log "$DEST already present in $CONTAINER, nothing to do."
    log "Delete it first if you want to rebuild it."
    exit 0
fi

log "Downloading $GTF_URL"
curl -fsSL -o "$WORKDIR/mmu38.gtf.gz" "$GTF_URL"

log "Decompressing"
gunzip -f "$WORKDIR/mmu38.gtf.gz"

log "Trimming to exon/transcript/gene on primary chromosomes, then sorting"
# Chromosome names are bare (1, 2, X) in both the Ensembl GTF and the example
# BED, so no chr-prefix translation is needed. Note the filter is written in
# awk rather than piped through grep -E: GNU grep does not interpret \t in an
# ERE, so a "^([0-9]+|X|Y|MT)\t" pattern silently matches nothing.
awk -F'\t' '
    /^#/    { next }
    ($3 == "exon" || $3 == "transcript" || $3 == "gene") && $1 ~ /^([0-9]+|X|Y|MT)$/
' "$WORKDIR/mmu38.gtf" | sort -k1,1 -k4,4n -S 2G > "$WORKDIR/sorted_mmu.gtf"

rows=$(wc -l < "$WORKDIR/sorted_mmu.gtf")
if [ "$rows" -lt 500000 ]; then
    echo "ERROR: only $rows rows after filtering; expected ~1.04M." >&2
    echo "The upstream GTF layout probably changed -- check before installing." >&2
    exit 1
fi
log "Built $rows rows ($(du -h "$WORKDIR/sorted_mmu.gtf" | cut -f1))"

log "Installing into $CONTAINER:$DEST"
docker cp "$WORKDIR/sorted_mmu.gtf" "$CONTAINER:$DEST"
docker exec "$CONTAINER" chown paintomics:paintomics "$DEST"

log "Done. 'From Regions to Genes' -> Load example -> Run should now succeed."
