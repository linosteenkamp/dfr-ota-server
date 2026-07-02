#!/usr/bin/env bash
# Install dfr-ota-server on the z2m Pi. Run as root. Idempotent.
set -euo pipefail
REPO_DIR="/opt/dfr-ota-server"
id dfr-ota &>/dev/null || useradd --system --home /var/lib/dfr-ota --shell /usr/sbin/nologin dfr-ota
install -d -o dfr-ota -g dfr-ota /var/lib/dfr-ota /var/lib/dfr-ota/.staging /var/lib/dfr-ota/ota
install -d -m 750 /etc/dfr-ota
[ -f /etc/dfr-ota/repos.conf ] || install -m 640 "$(dirname "$0")/repos.conf.example" /etc/dfr-ota/repos.conf
[ -f /etc/dfr-ota/token ] || { touch /etc/dfr-ota/token; chmod 600 /etc/dfr-ota/token; echo "!! put a read-only GitHub PAT in /etc/dfr-ota/token"; }
# /etc/dfr-ota/env holds BASE_URL, BIND_ADDR, SERVE_PORT (edit after install)
[ -f /etc/dfr-ota/env ] || cat > /etc/dfr-ota/env <<'EOF'
# z2m is co-located -> serve on loopback only; z2m fetches via localhost.
BASE_URL=http://localhost:8091
BIND_ADDR=127.0.0.1
SERVE_PORT=8091
EOF
mkdir -p "$REPO_DIR" && cp -r "$(dirname "$0")"/dfr_ota_sync.py "$REPO_DIR"/
cp systemd/dfr-ota-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dfr-ota-serve.service dfr-ota-sync.timer
echo "Installed. Set the token + edit /etc/dfr-ota/env, then: systemctl start dfr-ota-sync.service"
