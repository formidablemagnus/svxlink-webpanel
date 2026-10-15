# Changelog

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
