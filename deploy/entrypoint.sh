#!/usr/bin/env bash
# PaintOmics AI container entrypoint.
#
# Runs as root only long enough to fix ownership of the mounted data volume,
# then drops to the unprivileged `paintomics` user for the actual process.
# A named volume is created root-owned by the Docker daemon on first use, so
# this cannot be baked in at build time.
set -euo pipefail

APP_USER=paintomics
APP_UID=1001
SERVER_DIR=/app/PaintomicsServer
CONFIG_PATH="${SERVER_DIR}/src/conf/serverconf.py"
TEMPLATE_PATH="${SERVER_DIR}/src/resources/example_serverconf.py"

log() { printf '[entrypoint] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# The template reads every setting from the environment with safe defaults and
# carries no secrets, so installing it verbatim is enough. The real config is
# gitignored and is never baked into the image.
if [ ! -f "${CONFIG_PATH}" ]; then
    log "generating ${CONFIG_PATH} from the template"
    install -D -m 0644 "${TEMPLATE_PATH}" "${CONFIG_PATH}"
else
    log "using the existing ${CONFIG_PATH}"
fi

# ---------------------------------------------------------------------------
# Fail fast on misconfiguration
# ---------------------------------------------------------------------------
# These produce confusing downstream failures rather than obvious ones, so
# check them here where the message is visible in `docker compose logs`.
if [ "${AI_INTERPRETATION_ENABLED:-true}" = "true" ]; then
    provider="${AI_LLM_PROVIDER:-csic}"
    case "${provider}" in
        csic)       key="${AI_CSIC_API_KEY:-}" ;;
        dashscope)  key="${AI_DASHSCOPE_API_KEY:-}" ;;
        openrouter) key="${AI_OPENROUTER_API_KEY:-}" ;;
        *)          key="" ;;
    esac
    if [ -z "${key}" ]; then
        log "WARNING: AI interpretation is enabled with provider '${provider}' but no API key is set."
        log "WARNING: AI interpretation requests will fail. Set the key, or AI_INTERPRETATION_ENABLED=false."
    fi
fi

# Said out loud at every start so a deploy that lost the switch is visible in
# `docker compose logs` rather than in a user's inbox. The converter is a
# feature the interface offers on every upload strip; a server that has it off
# refuses each attempt at the last step.
if [ "${AI_INPUT_CONVERTER:-false}" = "true" ]; then
    log "AI input converter: on"
else
    log "AI input converter: OFF (AI_INPUT_CONVERTER is not 'true'). Spreadsheets and"
    log "  rejected uploads will end in 'AI file conversion is not enabled on this server'."
fi

if [ -z "${SMTP_PASSWORD:-}" ]; then
    log "WARNING: SMTP_PASSWORD is unset. Registration and password-reset email cannot be sent,"
    log "WARNING: so new users will not be able to activate their accounts."
fi

case "${PAINTOMICS_BASE_URL:-}" in
    ""|*localhost*|*127.0.0.1*)
        log "WARNING: PAINTOMICS_BASE_URL is '${PAINTOMICS_BASE_URL:-unset}'. This URL is embedded in"
        log "WARNING: activation emails; anything pointing at localhost breaks registration."
        ;;
esac

# ---------------------------------------------------------------------------
# Data volume ownership
# ---------------------------------------------------------------------------
# Create the layout the admin tools expect. A fresh volume is empty, and several
# DBManager commands assume these already exist -- the download step failed with
# a bare "FileNotFoundError: /data/KEGG_DATA/download/summary.log" that named a
# file rather than the missing parent directory.
for directory in /data/KEGG_DATA \
                 /data/KEGG_DATA/download \
                 /data/KEGG_DATA/current \
                 /data/CLIENT_TMP; do
    mkdir -p "${directory}"
done

# Give the app user every entry under a directory that it does not already own.
#
# This used to test only the directory itself: `stat %u` on /data/CLIENT_TMP,
# chown -R if it was not 1001. That is the right test for a volume the daemon
# has just created, and the wrong one for a volume that was populated from
# outside after the first start. Production's 388 user directories arrived by
# `docker cp` (deploy/migrate-from-uv.sh restore), which keeps the uid of the
# source -- 1000 -- while /data/CLIENT_TMP itself was already 1001 from the
# first boot. The check passed on every restart, and uid 1001 had no write
# permission inside any of those directories, so every job by a migrated user
# or an anonymous one died in PathwayAcquisitionServlet with
#     PermissionError: [Errno 13] Permission denied: '/data/CLIENT_TMP/nologin/tmp/<jobID>'
# while an account registered after the copy worked, because the server made
# its directory itself. `docker compose exec` runs as root, so an admin command
# that creates files under /data leaves the same kind of entry behind.
#
# `-prune` stops the scan at each entry that is wrong, so one wrong subtree is
# listed once, by its root, and chowned once, recursively. The scan itself is a
# stat per entry (about a second for the 134,000 files of production's job
# tree) and changes nothing when everything is already right.
#
# Extra arguments are passed to find. KEGG_DATA is scanned to depth 2 only: it
# holds millions of files and the server never writes inside a species
# directory, so a deeper scan would cost minutes per restart for nothing.
repair_ownership() {
    local directory="$1" wrong
    shift
    local -a scan=(find "${directory}" "$@" \
                   \( ! -user "${APP_USER}" -o ! -group "${APP_USER}" \) -prune)
    wrong=$("${scan[@]}" -printf . | wc -c)
    if [ "${wrong}" -gt 0 ]; then
        log "taking ownership of ${wrong} subtree(s) under ${directory}"
        "${scan[@]}" -exec chown -R "${APP_USER}:${APP_USER}" {} +
    fi
}

if [ "$(id -u)" = "0" ]; then
    repair_ownership /data/KEGG_DATA -maxdepth 2
    repair_ownership /data/CLIENT_TMP
    log "dropping privileges to ${APP_USER}"
    exec setpriv --reuid="${APP_UID}" --regid="${APP_UID}" --init-groups -- "$@"
fi

log "already running as $(id -un)"
exec "$@"
