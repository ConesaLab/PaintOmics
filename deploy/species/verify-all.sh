#!/usr/bin/env bash
# Measure what paintomics.org actually serves after the all-species install.
# Runs the read-only checks inside the app container and leaves their output
# under $OUT (default ~/allspecies/verify-<stamp>/):
#   census.tsv        ensembl_census.py census   (Ensembl tables per species)
#   verify.tsv        ensembl_census.py verify   (Ensembl gene -> kegg_id reach, real mapper)
#   idmapping.log     checkIdentifierMapping.py  (configured tables hold the pathway gene ids)
#   report.tsv        species_report.py          (every species x source x id table, stride-sampled reach)
#   species.json      the served organism list
set -uo pipefail
COMPOSE="sudo -n docker compose -f /home/tliu/paintomics4/deploy/compose.yaml exec -T -u paintomics app"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${OUT:-$HOME/allspecies/verify-$STAMP}
mkdir -p "$OUT"
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
say "census -> $OUT/census.tsv"
$COMPOSE sh -c 'cd /app/PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py census' > "$OUT/census.tsv" 2> "$OUT/census.err"
say "census exit=$?"
say "checkIdentifierMapping -> $OUT/idmapping.log"
$COMPOSE python /app/PaintomicsServer/src/AdminTools/checkIdentifierMapping.py > "$OUT/idmapping.log" 2>&1
say "checkIdentifierMapping exit=$? : $(tail -1 "$OUT/idmapping.log")"
say "verify (sample ${SAMPLE:-100}) -> $OUT/verify.tsv"
$COMPOSE sh -c "cd /app/PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py verify --sample ${SAMPLE:-100}" > "$OUT/verify.tsv" 2> "$OUT/verify.err"
say "verify exit=$?"
say "species_report (sample ${REPORT_SAMPLE:-40}) -> $OUT/report.tsv"
$COMPOSE sh -c "cd /app/PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/species_report.py --sample ${REPORT_SAMPLE:-40} ${REPORT_SPECIES:+--species=$REPORT_SPECIES}" > "$OUT/report.tsv" 2> "$OUT/report.err"
say "species_report exit=$?"
sudo -n cp /var/lib/docker/volumes/paintomics_paintomics-data/_data/KEGG_DATA/current/species.json "$OUT/species.json" && sudo -n chown "$USER" "$OUT/species.json"
say "species.json: $(python3 -c "import json,sys;print(len(json.load(open('$OUT/species.json'))['species']))") species served"
say "DONE $OUT"
