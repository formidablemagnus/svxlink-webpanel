# SvxLink Webpanel v1.5.0 – installationspaket

Installationspaketet innehåller `svxlink-webpanel.py` (v1.5.0), systemd-installation, konfiguration och avinstallation.
Panelen använder endast Python-standardbiblioteket.

## Krav

- Linux med systemd
- Python 3
- SvxLink installerat med systemkontot `svxlink`
- Den här nodens SvxLink-konfiguration har ett DTMF-kontrollgränssnitt på `/dev/shm/dtmf_ctrl` (standard i panelen)
- Kontot `svxlink` behöver kunna läsa SvxLink-loggen och skriva till DTMF-kontrollgränssnittet

## Installation


```bash
git clone -b main https://github.com/formidablemagnus/svxlink-webpanel.git 
cd svxlink-webpanel
sudo chmod +x install.sh
sudo ./install.sh
```

Installationsscriptet:

- installerar programmet i `/usr/local/bin/svxlink-webpanel.py`
- installerar webbgränssnittets HTML, CSS och JavaScript i `/usr/local/share/svxlink-webpanel`
- installerar ett begränsat hjälpprogram för redigering av SvxLinks konfiguration
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

## Webbgränssnittets filer

Webbgränssnittet är uppdelat i `web/index.html`, `web/schedules.html`, `web/style.css` och JavaScript-filerna `web/common.js`, `web/app.js` och `web/schedules.js`. Översikten visas på `/` och schemaläggningen på `/schedules`. Installationen kopierar dessa filer till `/usr/local/share/svxlink-webpanel`; Python-servern hanterar API:et och serverar bara de uttryckligen tillåtna gränssnittsfilerna.

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
- `SVXLINK_CONFIG=/etc/svxlink/svxlink.conf`
- `SVXLINK_SERVICE=svxlink`
- `SVXLINK_DTMF_PTY=/dev/shm/dtmf_ctrl`
- `SVXLINK_BUTTONS_FILE=/var/lib/svxlink/webpanel-buttons.json`

Efter ändringar:

```bash
sudo systemctl restart svxlink-webpanel.service
sudo systemctl status svxlink-webpanel.service
sudo journalctl -u svxlink-webpanel.service -n 80 --no-pager
```

På statusraden i panelen finns knappar för att starta och stoppa SvxLink. Stopp kräver bekräftelse. Installationen lägger sudoers-regeln i `/etc/sudoers.d/svxlink-webpanel` och tillåter endast `systemctl start svxlink`, `systemctl stop svxlink`, `systemctl restart svxlink` samt det fasta konfigurationshjälpprogrammet för panelkontot. Systemd-inställningen `NoNewPrivileges` är avstängd och `RestrictSUIDSGID` används inte, så panelen kan köra den begränsade sudo-regeln och crontab-programmets behörighetsbyte. Om du ändrar `SVXLINK_SERVICE` från standardvärdet `svxlink` måste du även anpassa sudoers-regeln.

Översikten visar permanent monitorerade talgrupper från `MONITOR_TGS` i sektionen `[ReflectorLogic]` i SvxLink-konfigurationen. Standardfilen är `/etc/svxlink/svxlink.conf`; ändra `SVXLINK_CONFIG` om översikten ska läsa talgrupperna från en annan plats. Paneltjänstens användare behöver läsrättighet till den filen.

Portalens rubrik hämtar anropssignalen (`CALLSIGN`) från det första aktiva simplex- eller repeateravsnittet som anges i `[GLOBAL] LOGICS`, till exempel `[SimplexLogic]`.

På sidan **Konfiguration** kan du redigera `/etc/svxlink/svxlink.conf`. Varje sparning skapar en säkerhetskopia bredvid filen med tidsstämpel. Sparning skriver atomiskt och bevarar filens ägare och rättigheter. Om filen har ändrats sedan den lästes in avbryts sparningen, så att externa ändringar inte skrivs över av misstag. Efter sparning väljer du själv när du startar om SvxLink med sidans separata omstartsknapp; en omstart bryter pågående trafik och anslutningar. Panelen använder ett root-ägt hjälpprogram med fast konfigurationssökväg, och systemd-enheten får skrivåtkomst till `/etc/svxlink` för den begränsade operationen. Hjälpprogrammet kan endast läsa och ersätta `/etc/svxlink/svxlink.conf`, inte godtyckliga filer.

Snabbknapparnas ordning ändras i **Administrera snabbval** genom att dra i handtaget bredvid en knapp. Tangentbordsanvändare kan fokusera handtaget och flytta knappen med upp- och nedpilarna. Klicka på **Spara knappar** för att spara den nya ordningen.

Under **Schemalagda DTMF-kommandon** kan du läsa och uppdatera `svxlink`-användarens crontab. Ange DTMF-koden och de fem vanliga cron-fälten; raden visas i förhandsvisningen innan den läggs till. Exempelvis motsvarar `45 19 * * 4` torsdagar kl. 19:45. Fälten stöder `*`, värden, intervall, kommaseparerade listor och steg, till exempel `*/10`. Panelen hanterar endast sitt eget markerade block och lämnar övriga crontab-rader orörda. De övriga raderna visas skrivskyddat. Kommandot använder `printf` för att skriva DTMF-koden till den konfigurerade `SVXLINK_DTMF_PTY`.

Installationen kräver `crontab` och ger systemd-tjänsten skrivåtkomst till vanliga cron-spoolkataloger (`/var/spool/cron` och `/var/spool/cron/crontabs`) för att crontab-programmet ska kunna spara användarens schema. Cron-tjänsten måste också vara installerad och aktiv för att köra schemaläggningarna.

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

Avinstallationen tar bort tjänsten, program- och webbgränssnittsfilerna samt panelens sudoers-regel men behåller konfiguration och snabbknappar. Ta bort `/etc/default/svxlink-webpanel` och `/var/lib/svxlink/webpanel-buttons.json` manuellt endast om du vill radera dessa också.
