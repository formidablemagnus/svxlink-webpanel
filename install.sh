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
WEB_SRC="$SCRIPT_DIR/web"
WEB_DST="/usr/local/share/svxlink-webpanel"
ENV_FILE="/etc/default/svxlink-webpanel"
SERVICE_FILE="/etc/systemd/system/svxlink-webpanel.service"
BUTTONS_FILE="/var/lib/svxlink/webpanel-buttons.json"
SUDOERS_FILE="/etc/sudoers.d/svxlink-webpanel"

if [[ ! -f "$APP_SRC" ]]; then
  echo "Hittar inte $APP_SRC. Packa upp hela paketet och kör install.sh därifrån." >&2
  exit 1
fi
for web_file in index.html schedules.html style.css common.js app.js schedules.js; do
  if [[ ! -f "$WEB_SRC/$web_file" ]]; then
    echo "Hittar inte $WEB_SRC/$web_file. Packa upp hela paketet och kör install.sh därifrån." >&2
    exit 1
  fi
done

if ! id svxlink >/dev/null 2>&1; then
  echo "Användaren 'svxlink' finns inte. Installera SvxLink först eller anpassa paketet för din distribution." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 saknas. Installera python3 och kör scriptet igen." >&2
  exit 1
fi
if ! command -v visudo >/dev/null 2>&1; then
  echo "visudo saknas. Installera sudo innan du installerar panelen." >&2
  exit 1
fi
if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab saknas. Installera cron innan du installerar panelen." >&2
  exit 1
fi

if [[ -e "$APP_DST" ]]; then
  cp -a "$APP_DST" "${APP_DST}.bak.$(date +%Y%m%d-%H%M%S)"
  echo "Säkerhetskopia skapad av befintlig panel."
fi

install -o root -g root -m 0755 "$APP_SRC" "$APP_DST"
python3 -m py_compile "$APP_DST"
install -d -o root -g root -m 0755 "$WEB_DST"
for web_file in index.html schedules.html style.css common.js app.js schedules.js; do
  install -o root -g root -m 0644 "$WEB_SRC/$web_file" "$WEB_DST/$web_file"
done

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
  # Preserve systemd EnvironmentFile compatibility with URL-safe credentials.
  PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' | tr -d '=')"
  if [[ ! -r /dev/tty ]]; then
    echo "Installationen behöver en interaktiv terminal för att fråga efter inloggningsuppgifter." >&2
    exit 1
  fi
  while true; do
    read -r -p "Välj användarnamn (1–64 tecken: A–Z, a–z, 0–9, punkt, _ eller -): " USERNAME </dev/tty
    if [[ "$USERNAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
      break
    fi
    echo "Användarnamnet har ogiltigt format." >&2
  done
  while true; do
    read -r -s -p "Välj lösenord (12–128 tecken: A–Z, a–z, 0–9, _ eller -): " PASSWORD </dev/tty
    echo
    if [[ ! "$PASSWORD" =~ ^[A-Za-z0-9_-]{12,128}$ ]]; then
      echo "Lösenordet måste vara 12–128 tecken och endast innehålla A–Z, a–z, 0–9, _ eller -." >&2
      continue
    fi
    read -r -s -p "Bekräfta lösenordet: " PASSWORD_CONFIRM </dev/tty
    echo
    if [[ "$PASSWORD" == "$PASSWORD_CONFIRM" ]]; then
      unset PASSWORD_CONFIRM
      break
    fi
    echo "Lösenorden matchar inte. Försök igen." >&2
  done
  cat > "$ENV_FILE" <<EOF_ENV
# SvxLink Web Panel settings. Keep this file private.
SVXLINK_WEB_HOST=0.0.0.0
SVXLINK_WEB_PORT=8090
SVXLINK_WEB_USER=$USERNAME
SVXLINK_WEB_PASSWORD=$PASSWORD
SVXLINK_LOGFILE=/var/log/svxlink
SVXLINK_SERVICE=svxlink
SVXLINK_DTMF_PTY=/dev/shm/dtmf_ctrl
SVXLINK_BUTTONS_FILE=/var/lib/svxlink/webpanel-buttons.json
EOF_ENV
  chmod 0640 "$ENV_FILE"
  chown root:svxlink "$ENV_FILE"
  echo
  echo "Dina inloggningsuppgifter har sparats i $ENV_FILE (root:svxlink, 0640)."
  echo
  unset PASSWORD
else
  echo "Behåller befintlig konfiguration i $ENV_FILE."
  if grep -q '^SVXLINK_WEB_HOST=' "$ENV_FILE"; then
    sed -i 's/^SVXLINK_WEB_HOST=.*/SVXLINK_WEB_HOST=0.0.0.0/' "$ENV_FILE"
  else
    printf '\nSVXLINK_WEB_HOST=0.0.0.0\n' >> "$ENV_FILE"
  fi
  chmod 0640 "$ENV_FILE"
  chown root:svxlink "$ENV_FILE"
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
Environment=SVXLINK_WEB_ROOT=/usr/local/share/svxlink-webpanel
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=false
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/svxlink /dev/shm
ReadWritePaths=-/var/spool/cron -/var/spool/cron/crontabs
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictRealtime=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF_SERVICE
chmod 0644 "$SERVICE_FILE"

# Permit only the two service actions required by the panel.
SUDOERS_TMP="$(mktemp /etc/sudoers.d/svxlink-webpanel.XXXXXX)"
printf '%s\n' 'svxlink ALL=(root) NOPASSWD: /usr/bin/systemctl start svxlink, /usr/bin/systemctl stop svxlink' > "$SUDOERS_TMP"
chmod 0440 "$SUDOERS_TMP"
if ! visudo -cf "$SUDOERS_TMP"; then
  rm -f "$SUDOERS_TMP"
  echo "Sudoers-konfigurationen är ogiltig; avbryter installationen." >&2
  exit 1
fi
mv "$SUDOERS_TMP" "$SUDOERS_FILE"

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
  Lyssnar på alla IPv4-adresser på port 8090.
  Begränsa åtkomsten med brandvägg och använd HTTPS via en betrodd proxy.
  Konfigurationsfil: /etc/default/svxlink-webpanel
  Grundläggande kontroll:
    systemctl status svxlink-webpanel.service
    journalctl -u svxlink-webpanel.service -n 50 --no-pager
    curl -I http://127.0.0.1:8090/

Skydda port 8090 så att endast avsedda klienter eller proxyservrar når den.
HTTPS hanteras normalt av NGINX.
EOF_DONE
