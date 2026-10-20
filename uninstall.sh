#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo "Kör med sudo: sudo ./uninstall.sh" >&2; exit 1; fi
systemctl disable --now svxlink-webpanel.service 2>/dev/null || true
rm -f /etc/systemd/system/svxlink-webpanel.service
rm -f /etc/sudoers.d/svxlink-webpanel
systemctl daemon-reload
rm -f /usr/local/bin/svxlink-webpanel.py
rm -f /usr/local/libexec/svxlink-webpanel-config
rm -rf /usr/local/share/svxlink-webpanel
# Configuration and custom buttons are intentionally preserved.
echo "Webpanelen och systemd-tjänsten har tagits bort. Konfigurationen och knapparna finns kvar i:"
echo "  /etc/default/svxlink-webpanel"
echo "  /var/lib/svxlink/webpanel-buttons.json"
echo "Ta bort dessa manuellt om du vill radera dem permanent."
