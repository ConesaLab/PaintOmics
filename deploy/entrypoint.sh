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

if [ "$(id -u)" = "0" ]; then
    # Only chown when it is actually wrong. Recursively chowning a populated
    # KEGG_DATA volume (hundreds of GB, millions of files) on every restart
    # would add many minutes to each deploy.
    for directory in /data/KEGG_DATA /data/CLIENT_TMP; do
        if [ "$(stat -c '%u' "${directory}")" != "${APP_UID}" ]; then
            log "taking ownership of ${directory} (one time)"
            chown -R "${APP_USER}:${APP_USER}" "${directory}"
        fi
    done
    log "dropping privileges to ${APP_USER}"
    exec setpriv --reuid="${APP_UID}" --regid="${APP_UID}" --init-groups -- "$@"
fi

log "already running as $(id -un)"
exec "$@"
