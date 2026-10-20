# SvxLink Webpanel v1.3 – installationspaket

Installationspaketet innehåller `svxlink-webpanel.py` (v1.3), systemd-installation, konfiguration och avinstallation.
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
- frågar efter användarnamn och lösenord om ingen konfiguration finns
- binder tjänsten till alla IPv4-adresser på port 8090
- sparar inställningar i `/etc/default/svxlink-webpanel`
- sparar snabbknappar i `/var/lib/svxlink/webpanel-buttons.json`
- lägger till en sudoers-regel som endast tillåter kontot `svxlink` att starta och stoppa `svxlink`
- aktiverar och startar tjänsten
- visar sökvägen till konfigurationsfilen när installationen är klar

Användarnamnet måste vara 1–64 tecken och endast innehålla bokstäver, siffror, punkt, `_` eller `-`. Lösenordet måste vara 12–128 tecken och endast innehålla bokstäver, siffror, `_` eller `-`. Lösenordet skrivs inte ut i terminalen. Om konfigurationsfilen redan finns behålls inloggningsuppgifterna, men lyssningsadressen sätts till `0.0.0.0`.

## NGINX Proxy Manager / NGINX

Tjänsten lyssnar på alla IPv4-adresser på port `8090`. Lägg till en proxy host med:

- Forward hostname/IP: `127.0.0.1`
- Forward port: `8090`
- Scheme: `http`
- Websockets: normalt inte nödvändigt

Låt NGINX hantera HTTPS-certifikatet. Behåll autentiseringen i panelen också. Eftersom panelen lyssnar på alla IPv4-gränssnitt ska brandväggen begränsa TCP-port 8090 till betrodda klienter eller proxyservrar. Undvik att exponera porten direkt mot internet.

## Konfiguration

Redigera:

```bash
sudoedit /etc/default/svxlink-webpanel
```

Viktiga variabler:

- `SVXLINK_WEB_HOST=0.0.0.0` (alla IPv4-gränssnitt)
- `SVXLINK_WEB_PORT=8090`
- `SVXLINK_WEB_USER=<ditt valda användarnamn>`
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

På statusraden i panelen finns knappar för att starta och stoppa SvxLink. Stopp kräver bekräftelse. Installationen lägger sudoers-regeln i `/etc/sudoers.d/svxlink-webpanel` och tillåter endast `systemctl start svxlink` och `systemctl stop svxlink` för panelkontot. Systemd-inställningen `NoNewPrivileges` är avstängd för paneltjänsten eftersom sudo annars inte kan använda sin begränsade behörighet. Om du ändrar `SVXLINK_SERVICE` från standardvärdet `svxlink` måste du även anpassa sudoers-regeln.

Snabbknapparnas ordning ändras i **Administrera snabbval** genom att dra i handtaget bredvid en knapp. Tangentbordsanvändare kan fokusera handtaget och flytta knappen med upp- och nedpilarna. Klicka på **Spara knappar** för att spara den nya ordningen.

## Säkerhetsnoteringar

- Standardinställningen binder till alla IPv4-gränssnitt. Begränsa därför åtkomsten till TCP-port 8090 med brandvägg.
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

Avinstallationen tar bort tjänsten, programfilen och panelens sudoers-regel men behåller konfiguration och snabbknappar. Ta bort `/etc/default/svxlink-webpanel` och `/var/lib/svxlink/webpanel-buttons.json` manuellt endast om du vill radera dessa också.
