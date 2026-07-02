#!/usr/bin/env bash
# Install dfr-ota-server on the z2m Pi. Run as root. Idempotent.
set -euo pipefail
REPO_DIR="/opt/dfr-ota-server"
id dfr-ota &>/dev/null || useradd --system --home /var/lib/dfr-ota --shell /usr/sbin/nologin dfr-ota
# Served content lives in the `pub` subdir so the atomic swap (which renames the
# serve dir) only needs write on /var/lib/dfr-ota (owned by dfr-ota), not /var/lib.
install -d -o dfr-ota -g dfr-ota /var/lib/dfr-ota /var/lib/dfr-ota/.staging /var/lib/dfr-ota/pub /var/lib/dfr-ota/pub/ota
# Seed an empty index so the static server starts cleanly before the first sync.
[ -f /var/lib/dfr-ota/pub/index.json ] || install -o dfr-ota -g dfr-ota -m 644 /dev/stdin /var/lib/dfr-ota/pub/index.json <<<'[]'
install -d -m 750 -g dfr-ota /etc/dfr-ota
[ -f /etc/dfr-ota/repos.conf ] || install -m 640 -g dfr-ota "$(dirname "$0")/repos.conf.example" /etc/dfr-ota/repos.conf
[ -f /etc/dfr-ota/token ] || { touch /etc/dfr-ota/token; chgrp dfr-ota /etc/dfr-ota/token; chmod 640 /etc/dfr-ota/token; echo "!! put a read-only GitHub PAT in /etc/dfr-ota/token"; }
# /etc/dfr-ota/env holds BASE_URL, BIND_ADDR, SERVE_PORT (edit after install)
[ -f /etc/dfr-ota/env ] || cat > /etc/dfr-ota/env <<'EOF'
# z2m is co-located -> serve on loopback only; z2m fetches via localhost.
# Use 127.0.0.1 (not "localhost"): the server binds IPv4 only, but "localhost"
# can resolve to IPv6 ::1 first (Node/z2m + curl), giving connection-refused.
BASE_URL=http://127.0.0.1:8091
BIND_ADDR=127.0.0.1
SERVE_PORT=8091
EOF
mkdir -p "$REPO_DIR" && cp -r "$(dirname "$0")"/dfr_ota_sync.py "$REPO_DIR"/
cp "$(dirname "$0")"/systemd/dfr-ota-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dfr-ota-serve.service dfr-ota-sync.timer
echo "Installed. Set the token + edit /etc/dfr-ota/env, then: systemctl start dfr-ota-sync.service"
