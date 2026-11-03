#!/usr/bin/env bash
# Sets up Kelly Terminal on a fresh Ubuntu server (Hetzner or any other) behind https with Google sign-in.
#
# Run it as root on the server:
#     DOMAIN=riskdesk-you.duckdns.org EMAIL=you@gmail.com bash setup-server.sh
# It asks for the Google client id and secret (and your optional AI and price keys) without showing them
# and keeps them in /opt/riskdesk/trader, which is never in git. Safe to run again.
set -euo pipefail

: "${DOMAIN:?set DOMAIN to your https name, for example riskdesk-you.duckdns.org}"
: "${EMAIL:?set EMAIL to the Google address that may sign in}"
REPO="${REPO:-https://github.com/eabfx/Ai-Bots-.git}"
APP=/opt/riskdesk
TRADER=$APP/trader

ask() {                                  # ask <variable> <prompt> [optional]
    local var=$1 prompt=$2 opt=${3:-}
    if [ -z "${!var:-}" ]; then
        read -rsp "$prompt: " "$var"; echo
    fi
    if [ -z "${!var:-}" ] && [ -z "$opt" ]; then echo "$var is required" >&2; exit 1; fi
}
ask GOOGLE_CLIENT_ID "Google client id"
ask GOOGLE_CLIENT_SECRET "Google client secret"
ask GEMINI_KEY "Gemini key (Enter to skip)" optional
ask ANTHROPIC_KEY "Anthropic key (Enter to skip)" optional
ask TWELVEDATA_KEY "Twelve Data key (Enter to skip)" optional

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 git ufw curl gpg debian-keyring debian-archive-keyring apt-transport-https unattended-upgrades

if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
fi

id riskdesk >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin riskdesk
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone "$REPO" "$APP"; fi
mkdir -p "$TRADER/journal"

# the settings: who may sign in and what name this answers to
DOMAIN="$DOMAIN" EMAIL="$EMAIL" TRADER="$TRADER" python3 - <<'PY'
import json, os
path = os.path.join(os.environ["TRADER"], "config.json")
try:
    cfg = json.load(open(path, encoding="utf-8-sig"))
except (OSError, ValueError):
    cfg = {}
cfg["allowed_hosts"] = [os.environ["DOMAIN"]]
cfg["auth"] = {"public_url": "https://" + os.environ["DOMAIN"], "emails": [os.environ["EMAIL"]]}
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
PY

# the secrets, readable only by the riskdesk user
write_secret() ( umask 077; printf '%s' "$2" > "$TRADER/$1" )     # a subshell, so the tighter umask does not leak into the files below
GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET" TRADER="$TRADER" python3 - <<'PY'
import json, os
fd = os.open(os.path.join(os.environ["TRADER"], "google_oauth.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    json.dump({"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"]}, fh)
PY
[ -n "${GEMINI_KEY:-}" ] && write_secret gemini_key.txt "$GEMINI_KEY"
[ -n "${ANTHROPIC_KEY:-}" ] && write_secret anthropic_key.txt "$ANTHROPIC_KEY"
[ -n "${TWELVEDATA_KEY:-}" ] && write_secret twelvedata_key.txt "$TWELVEDATA_KEY"
chown -R riskdesk:riskdesk "$TRADER"
chmod 700 "$TRADER"

cat > /etc/systemd/system/riskdesk.service <<UNIT
[Unit]
Description=Kelly Terminal dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=riskdesk
WorkingDirectory=$APP
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 $APP/riskdesk/dashboard.py --no-browser --config $TRADER/config.json
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$TRADER

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
    }
    reverse_proxy 127.0.0.1:8765
}
CADDY

# a copy of the journal every night, the last 14 kept
install -d -m 700 /var/backups/riskdesk
cat > /etc/cron.d/riskdesk-backup <<CRON
30 3 * * * root tar -czf /var/backups/riskdesk/journal-\$(date +\%F).tgz -C $TRADER journal && find /var/backups/riskdesk -name 'journal-*.tgz' -mtime +14 -delete
CRON

# the newest version from GitHub every hour, with a way back if it does not start
cat > /etc/systemd/system/riskdesk-update.service <<UNIT
[Unit]
Description=Update Kelly Terminal from GitHub

[Service]
Type=oneshot
ExecStart=/bin/bash $APP/riskdesk/deploy/update-server.sh
UNIT
cat > /etc/systemd/system/riskdesk-update.timer <<UNIT
[Unit]
Description=Update Kelly Terminal every hour

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# only ssh and the web are reachable from outside
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

systemctl daemon-reload
systemctl enable --now riskdesk
systemctl restart riskdesk
systemctl enable --now riskdesk-update.timer
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

echo
echo "Done. Open https://$DOMAIN and sign in with $EMAIL."
echo "If the page does not load yet, the name may not point at this server yet: give it a minute and check the DuckDNS address."
