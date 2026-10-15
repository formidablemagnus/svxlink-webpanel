# SvxLink Webpanel v10 – installationspaket

Installationspaketet innehåller `svxlink-webpanel.py` (v10 hardened), systemd-installation, konfiguration och avinstallation.
Panelen använder endast Python-standardbiblioteket.

## Krav

- Linux med systemd
- Python 3
- SvxLink installerat med systemkontot `svxlink`
- Den här nodens SvxLink-konfiguration har ett DTMF-kontrollgränssnitt på `/dev/shm/dtmf_ctrl` (standard i panelen)
- Kontot `svxlink` behöver kunna läsa SvxLink-loggen och skriva till DTMF-kontrollgränssnittet

## Installation

Packa upp arkivet och kör:

```bash
cd svxlink-webpanel-installer
sudo ./install.sh
```

Installationsscriptet:

- installerar programmet i `/usr/local/bin/svxlink-webpanel.py`
- skapar systemd-tjänsten `svxlink-webpanel.service`
- skapar en unik inloggning om ingen konfiguration finns
- sparar inställningar i `/etc/default/svxlink-webpanel`
- sparar snabbknappar i `/var/lib/svxlink/webpanel-buttons.json`
- aktiverar och startar tjänsten

**Spara lösenordet som visas vid första installationen.** Om konfigurationsfilen redan finns behålls den.

## NGINX Proxy Manager / NGINX

Standard är `127.0.0.1:8090`, avsett för en proxy på samma maskin. Lägg till en proxy host med:

- Forward hostname/IP: `127.0.0.1`
- Forward port: `8090`
- Scheme: `http`
- Websockets: normalt inte nödvändigt

Låt NGINX hantera HTTPS-certifikatet. Behåll autentiseringen i panelen också.

Om proxyn kör på en annan server ska `SVXLINK_WEB_HOST` ändras till nodens interna IP i `/etc/default/svxlink-webpanel`. Begränsa sedan brandväggen så att endast proxyservern når TCP-port 8090. Undvik att exponera porten direkt mot internet.

## Konfiguration

Redigera:

```bash
sudoedit /etc/default/svxlink-webpanel
```

Viktiga variabler:

- `SVXLINK_WEB_HOST=127.0.0.1`
- `SVXLINK_WEB_PORT=8090`
- `SVXLINK_WEB_USER=admin`
- `SVXLINK_WEB_PASSWORD=...`
- `SVXLINK_LOGFILE=/var/log/svxlink`
- `SVXLINK_SERVICE=svxlink`
- `SVXLINK_DTMF_PTY=/dev/shm/dtmf_ctrl`
- `SVXLINK_BUTTONS_FILE=/var/lib/svxlink/webpanel-buttons.json`

Efter ändringar:

```bash
sudo systemctl restart svxlink-webpanel.service
sudo systemctl status svxlink-webpanel.service
sudo journalctl -u svxlink-webpanel.service -n 80 --no-pager
```

## Säkerhetsnoteringar

- Standardinställningen binder endast till localhost.
- HTTPS bör termineras i NGINX eller annan betrodd reverse proxy.
- Om panelen körs på en separat värd eller är åtkomlig från andra nät måste åtkomst till port 8090 begränsas med brandvägg.
- Använd ett unikt lösenord och behåll panelens egen autentisering även bakom NGINX.
- Systemd-tjänsten kör som `svxlink` med flera skyddande inställningar. Om din distribution använder andra logg- eller PTY-rättigheter kan en lokal justering behövas.

## Felsökning

```bash
systemctl status svxlink-webpanel.service
journalctl -u svxlink-webpanel.service -n 100 --no-pager
curl -I http://127.0.0.1:8090/
ls -l /dev/shm/dtmf_ctrl /var/log/svxlink
```

Om tjänsten inte kan läsa loggen eller skriva DTMF, justera rättigheter enligt din SvxLink-installation. Undvik att göra kontroll-PTY eller loggar skrivbara för alla användare.

## Avinstallation

```bash
sudo ./uninstall.sh
```

Avinstallationen tar bort tjänsten och programfilen men behåller konfiguration och snabbknappar. Ta bort `/etc/default/svxlink-webpanel` och `/var/lib/svxlink/webpanel-buttons.json` manuellt endast om du vill radera dessa också.


### Fix i version 10.1

Dynamiska reflektortalgrupper med upp till 10 siffror accepteras nu. Exempelvis TG 2409938 skickas som DTMF-kommandot `912409938#`. Tidigare accepterades högst sex siffror i den dynamiska talgruppsvägen.
