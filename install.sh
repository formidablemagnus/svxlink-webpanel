#!/usr/bin/env bash
set -euo pipefail

# Installer for SvxLink Web Panel v10 (hardened)
if [[ ${EUID} -ne 0 ]]; then
  echo "Kör med sudo: sudo ./install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_SRC="$SCRIPT_DIR/svxlink-webpanel.py"
APP_DST="/usr/local/bin/svxlink-webpanel.py"
ENV_FILE="/etc/default/svxlink-webpanel"
SERVICE_FILE="/etc/systemd/system/svxlink-webpanel.service"
BUTTONS_FILE="/var/lib/svxlink/webpanel-buttons.json"

if [[ ! -f "$APP_SRC" ]]; then
  echo "Hittar inte $APP_SRC. Packa upp hela paketet och kör install.sh därifrån." >&2
  exit 1
fi

if ! id svxlink >/dev/null 2>&1; then
  echo "Användaren 'svxlink' finns inte. Installera SvxLink först eller anpassa paketet för din distribution." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 saknas. Installera python3 och kör scriptet igen." >&2
  exit 1
fi

if [[ -e "$APP_DST" ]]; then
  cp -a "$APP_DST" "${APP_DST}.bak.$(date +%Y%m%d-%H%M%S)"
  echo "Säkerhetskopia skapad av befintlig panel."
fi

install -o root -g root -m 0755 "$APP_SRC" "$APP_DST"
python3 -m py_compile "$APP_DST"

install -d -o svxlink -g svxlink -m 0750 /var/lib/svxlink
if [[ ! -e "$BUTTONS_FILE" ]]; then
  cat > "$BUTTONS_FILE" <<'JSON'
[
  {"label": "TG 9", "command": "919#"},
  {"label": "TG 240", "command": "91240#"},
  {"label": "TG 2407", "command": "912407#"}
]
JSON
  chown svxlink:svxlink "$BUTTONS_FILE"
  chmod 0640 "$BUTTONS_FILE"
else
  chown svxlink:svxlink "$BUTTONS_FILE"
  chmod 0640 "$BUTTONS_FILE"
fi

if [[ ! -e "$ENV_FILE" ]]; then
  # URL-safe password to avoid systemd EnvironmentFile quoting problems.
  PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' | tr -d '=')"
  cat > "$ENV_FILE" <<EOF_ENV
# SvxLink Web Panel settings. Keep this file private.
SVXLINK_WEB_HOST=127.0.0.1
SVXLINK_WEB_PORT=8090
SVXLINK_WEB_USER=admin
SVXLINK_WEB_PASSWORD=$PASSWORD
SVXLINK_LOGFILE=/var/log/svxlink
SVXLINK_SERVICE=svxlink
SVXLINK_DTMF_PTY=/dev/shm/dtmf_ctrl
SVXLINK_BUTTONS_FILE=/var/lib/svxlink/webpanel-buttons.json
EOF_ENV
  chmod 0640 "$ENV_FILE"
  chown root:svxlink "$ENV_FILE"
  echo
  echo "En unikt lösenord har skapats. Spara det nu:"
  echo "Användarnamn: admin"
  echo "Lösenord: $PASSWORD"
  echo "Lösenordet finns även i $ENV_FILE (root:svxlink, 0640)."
  echo
else
  echo "Behåller befintlig konfiguration i $ENV_FILE."
fi

cat > "$SERVICE_FILE" <<'EOF_SERVICE'
[Unit]
Description=SvxLink Web Panel
After=network.target svxlink.service
Wants=svxlink.service

[Service]
Type=simple
User=svxlink
Group=svxlink
EnvironmentFile=/etc/default/svxlink-webpanel
ExecStart=/usr/bin/python3 /usr/local/bin/svxlink-webpanel.py
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/svxlink /dev/shm
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
RestrictRealtime=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF_SERVICE
chmod 0644 "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable --now svxlink-webpanel.service
sleep 1
if systemctl is-active --quiet svxlink-webpanel.service; then
  echo "Installationen är klar och tjänsten kör."
else
  echo "Tjänsten startade inte. Visa loggen med: journalctl -u svxlink-webpanel.service -n 80 --no-pager" >&2
  exit 1
fi

cat <<'EOF_DONE'

Standardinställning:
  Lyssnar endast på 127.0.0.1:8090 (lämpligt när NGINX kör på samma maskin).
  Grundläggande kontroll:
    systemctl status svxlink-webpanel.service
    journalctl -u svxlink-webpanel.service -n 50 --no-pager
    curl -I http://127.0.0.1:8090/

Om NGINX kör på en annan server: ändra SVXLINK_WEB_HOST till nodens LAN-IP i
/etc/default/svxlink-webpanel och begränsa port 8090 till proxyserverns IP med brandvägg.
Ändra inte till 0.0.0.0 utan att begränsa nätverksåtkomsten. HTTPS hanteras normalt av NGINX.
EOF_DONE
