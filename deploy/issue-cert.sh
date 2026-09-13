#!/bin/bash
# Issue the Let's Encrypt certificate for paintomics.org on the Drago VM and install it
# into nginx. Runs on the VM as tliu (sudo -n for certbot and docker), or as root from
# certbot's renewal deploy hook -- hence absolute paths, never $HOME.
#
#   issue-cert.sh --check      report prerequisites and DNS state; exit 0 = ready to issue
#   issue-cert.sh --if-dns     issue only if DNS already points here (what the cron runs);
#                              silent and exit 0 while it does not
#   issue-cert.sh              issue now (fails loudly if DNS is not here yet)
#   issue-cert.sh --install    copy the issued files into nginx and restart it
#                              (certbot's deploy hook on every renewal)
#   issue-cert.sh --rehearse   exercise install + verify + restore with the current
#                              certificate, changing nothing that persists
#
# Safety: nothing is touched until the apex name resolves to this VM at the domain's own
# nameservers (Let's Encrypt resolves the same way, so a cached answer cannot mislead).
# After the swap, HTTPS is verified with a real chain check; on any failure the previous
# certificate is put back and nginx restarted again. Renewals go through certbot.timer,
# which already exists, and reach nginx via the same --install path.
set -euo pipefail

DEPLOY=/home/tliu/paintomics4/deploy
CERTS=$DEPLOY/nginx/certs
WEBROOT=$DEPLOY/certbot-webroot
COMPOSE="sudo -n docker compose -f $DEPLOY/compose.yaml"
MY_IP=161.111.18.82
APEX=paintomics.org
EXTRA_NAMES="www.paintomics.org"
LIVE=/etc/letsencrypt/live/$APEX
LOGDIR=/home/tliu/cutover
LOG=$LOGDIR/issue-cert.log
SELF=$(readlink -f "$0")

mkdir -p "$LOGDIR"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG" >&2; }
die() { log "ERROR: $*"; exit 1; }

# -- DNS as Let's Encrypt sees it ------------------------------------------------
resolve() {  # resolve <name> -> the A record at the zone's own nameserver (or empty)
    local ns
    ns=$(dig +short NS "$APEX" 2>/dev/null | head -1)
    if [ -n "$ns" ]; then dig +short @"$ns" "$1" A 2>/dev/null | grep -E '^[0-9.]+$' | tail -1
    else dig +short @1.1.1.1 "$1" A 2>/dev/null | grep -E '^[0-9.]+$' | tail -1; fi
}
names_here() {  # every configured name that already points at this VM, apex first
    local out="" n ip
    for n in $APEX $EXTRA_NAMES; do
        ip=$(resolve "$n" || true)
        [ "$ip" = "$MY_IP" ] && out="$out $n"
    done
    echo "$out"
}

# -- prerequisites ------------------------------------------------------------------
check() {
    local ok=0
    command -v certbot >/dev/null || { log "certbot missing"; ok=1; }
    command -v dig >/dev/null || { log "dig missing (apt install dnsutils)"; ok=1; }
    sudo -n true 2>/dev/null || { log "passwordless sudo unavailable"; ok=1; }
    [ -d "$WEBROOT" ] || { log "webroot $WEBROOT missing"; ok=1; }
    grep -q "^EMAIL_REPORT_RECIPIENTS=." "$DEPLOY/.env" || { log "no EMAIL_REPORT_RECIPIENTS in .env for the ACME account"; ok=1; }
    # Does nginx really serve the ACME path from the webroot, over plain HTTP, before its redirect?
    local probe="$WEBROOT/.well-known/acme-challenge/probe-$$"
    mkdir -p "$(dirname "$probe")" && echo "probe-$$" > "$probe"
    local got
    got=$(curl -s --max-time 8 -H "Host: $APEX" "http://$MY_IP/.well-known/acme-challenge/probe-$$" || true)
    rm -f "$probe"
    if [ "$got" = "probe-$$" ]; then log "ACME webroot is served: ok"; else log "ACME webroot NOT served (got '$got')"; ok=1; fi
    local here; here=$(names_here)
    for n in $APEX $EXTRA_NAMES; do log "DNS $n -> $(resolve "$n" || echo '?') (this VM is $MY_IP)"; done
    # /etc/letsencrypt/live is root-only (0700), so a plain [ -f ] is false for tliu: test through sudo.
    if sudo -n test -f "$LIVE/fullchain.pem"; then log "certificate already issued: $LIVE"; fi
    if echo "$here" | grep -qw "$APEX"; then log "DNS ready: would issue for:$here"; return $ok
    else log "DNS not here yet: apex must resolve to $MY_IP first"; return 1; fi
}

# -- install what certbot issued, verify, or roll back -------------------------------
install_from() {  # install_from <dir with fullchain.pem + privkey.pem>
    local src=$1 ts backup
    [ -r "$src/fullchain.pem" ] && [ -r "$src/privkey.pem" ] || sudo -n test -r "$src/privkey.pem" || die "no certificate in $src"
    ts=$(date -u +%Y%m%dT%H%M%SZ); backup=$CERTS/backup-$ts
    mkdir -p "$backup"; cp -p "$CERTS/paintomics.crt" "$CERTS/paintomics.key" "$backup/"
    sudo -n cp "$src/fullchain.pem" "$CERTS/paintomics.crt.new"
    sudo -n cp "$src/privkey.pem"   "$CERTS/paintomics.key.new"
    sudo -n chown tliu:tliu "$CERTS/paintomics.crt.new" "$CERTS/paintomics.key.new"
    chmod 644 "$CERTS/paintomics.crt.new"; chmod 600 "$CERTS/paintomics.key.new"
    # The key must match the certificate, or nginx will refuse to start.
    local c k
    c=$(openssl x509 -noout -pubkey -in "$CERTS/paintomics.crt.new" | openssl sha256)
    k=$(openssl pkey -pubout -in "$CERTS/paintomics.key.new" | openssl sha256)
    [ "$c" = "$k" ] || { rm -f "$CERTS"/paintomics.*.new; die "certificate and key do not match; nothing changed"; }
    mv -f "$CERTS/paintomics.crt.new" "$CERTS/paintomics.crt"
    mv -f "$CERTS/paintomics.key.new" "$CERTS/paintomics.key"
    log "installed certificate from $src (previous pair kept in $backup)"
    $COMPOSE restart nginx >/dev/null 2>&1 || log "nginx restart reported an error"
    sleep 3
    if verify_https; then log "HTTPS verified with the new certificate"; return 0; fi
    log "HTTPS verification FAILED -- restoring the previous certificate"
    cp -p "$backup/paintomics.crt" "$backup/paintomics.key" "$CERTS/"
    $COMPOSE restart nginx >/dev/null 2>&1 || true
    sleep 3
    if verify_https || verify_https -k; then log "previous certificate restored; nginx answers again"; else log "nginx still not answering after restore -- check $COMPOSE logs nginx"; fi
    return 1
}
verify_https() {  # a real chain check against this VM under the apex name; -k = accept self-signed
    local code
    code=$(curl -s "$@" --max-time 10 --resolve "$APEX:443:$MY_IP" -o /dev/null -w '%{http_code}' "https://$APEX/healthz" || true)
    [ "$code" = "200" ]
}

issue() {
    local here; here=$(names_here)
    echo "$here" | grep -qw "$APEX" || die "DNS for $APEX does not point at $MY_IP yet (see --check)"
    local email; email=$(grep -E '^EMAIL_REPORT_RECIPIENTS=' "$DEPLOY/.env" | cut -d= -f2- | cut -d, -f1)
    local dargs=""; for n in $here; do dargs="$dargs -d $n"; done
    log "issuing for:$here"
    # --keep-until-expiring makes a re-run a no-op while the certificate is fresh, so the
    # cron can never hammer Let's Encrypt's rate limits.
    sudo -n certbot certonly --webroot -w "$WEBROOT" $dargs \
        --non-interactive --agree-tos -m "$email" --keep-until-expiring \
        --deploy-hook "$SELF --install" >>"$LOG" 2>&1 || die "certbot failed; see $LOG"
    # certbot runs the deploy hook only when it actually issued; make the install
    # unconditional so a re-run after a failed install still ends with nginx fixed.
    install_from "$LIVE"
}

case "${1:-}" in
    --check)    check ;;
    --if-dns)
        # The cron path. Quiet while nothing changes; one line when DNS flips; then issue.
        state=$LOGDIR/dns-state; now=$(resolve "$APEX" || echo none)
        prev=$(cat "$state" 2>/dev/null || echo unknown)
        [ "$now" != "$prev" ] && { log "DNS $APEX: $prev -> $now"; echo "$now" > "$state"; }
        if [ "$now" = "$MY_IP" ]; then
            # Through sudo: /etc/letsencrypt/live is root-only, and a plain [ -f ] here is what made
            # the cron re-install the same certificate and restart nginx every 5 minutes.
            if sudo -n test -f "$LIVE/fullchain.pem" && verify_https; then exit 0; fi   # already done
            issue
        fi ;;
    --install)  install_from "$LIVE" ;;
    --rehearse)
        # Prove the swap, verify and restore paths with the certificate already installed:
        # identical bytes go in, so nothing observable changes and nothing persists but a
        # backup directory and the log lines.
        tmp=$(mktemp -d); cp "$CERTS/paintomics.crt" "$tmp/fullchain.pem"; cp "$CERTS/paintomics.key" "$tmp/privkey.pem"
        log "rehearsal: install path"; install_from "$tmp" && log "rehearsal: install path ok" || log "rehearsal: install path FAILED (expected while the certificate is self-signed: the chain check cannot pass, so this proves the RESTORE path instead)"
        rm -rf "$tmp" ;;
    "")         issue ;;
    *)          echo "usage: $0 [--check|--if-dns|--install|--rehearse]" >&2; exit 64 ;;
esac
