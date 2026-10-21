# Changelog

## [Unreleased]

### Added
- Visar RX/TX-läge och mottagen signalstyrka från `STATE_PTY` för den aktiva logiken.

## [1.5.0] - 2026-10-20

### Added
- Visar permanent monitorerade talgrupper från `MONITOR_TGS` i `[ReflectorLogic]`.
- Redigering av `/etc/svxlink/svxlink.conf` i en separat portalundersida med automatisk säkerhetskopia.
- Separat, bekräftad knapp för att starta om SvxLink efter konfigurationsändringar.
- Visar anropssignalen från den aktiva simplex- eller repeaterlogiken i portalens rubrik.

## [1.4.1] - 2026-10-20

### Fixed
- Installationsscriptet stoppar en redan körande webbpanelstjänst innan filer uppdateras.

## [1.4] - 2026-10-20

### Added
- Schemaläggning av DTMF-kommandon via svxlink-användarens crontab.

### Changed
- Webbgränssnittet är uppdelat i separata filer och schemaläggningen har en egen undersida.

## [1.3] - 2026-10-20

### Added
- Knappar för att starta och stoppa SvxLink-tjänsten från webbpanelen.
- Möjlighet att ändra snabbknapparnas ordning med drag-and-drop eller tangentbord.
- Versionsnumret visas från en gemensam variabel i sidtiteln och panelrubriken.

## [1.2] - 2026-10-15

### Added
- Installationen frågar efter användarnamn och lösenord när ingen konfiguration finns.
- Installationen skriver ut sökvägen till konfigurationsfilen när den är klar.

### Changed
- Webpanelen lyssnar på alla IPv4-adresser (`0.0.0.0`) på port 8090.
- Vid ominstallation behålls befintliga inloggningsuppgifter medan lyssningsadressen uppdateras.
- README:n beskriver installationens inloggningskrav och nätverkssäkerhet.

### Security
- Installationsflödet validerar användarnamn och lösenord och ber om lösenordsbekräftelse utan att visa lösenordet.
- Port 8090 bör begränsas till betrodda klienter eller proxyservrar med brandvägg; HTTPS bör hanteras av en betrodd proxy.
