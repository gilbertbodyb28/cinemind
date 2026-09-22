# CineMind — autostart och app-ikon

## Så här ser uppsättningen ut

Två LaunchAgents, båda med `RunAtLoad` och `KeepAlive`: de startar vid varje
inloggning och startas om automatiskt om processen dör.

| Agent | Vad den kör | Port |
|---|---|---|
| `com.cinemind.mongodb` | `~/CineMind/.runtime/mongodb/bin/mongod` mot `~/CineMind/data/mongo` | 127.0.0.1:27017 |
| `com.cinemind.local` | uvicorn med API:t och den byggda frontenden | 0.0.0.0:8001 |

Dessutom `/Applications/CineMind.app` — ikonen du klickar på. Den kollar om
tjänsten svarar, startar den annars, väntar in den och öppnar webbläsaren.
Svarar den inte inom 30 sekunder visas en ruta med sökvägen till loggen.

CineMind-agenten väntar in port 27017 innan uvicorn startar, så att
jobbschemaläggaren inte når efter databasen innan den finns.

### Varför en kopia av koden i ~/CineMind?

macOS nekar launchd-processer åtkomst till `~/Documents`. En LaunchAgent som
pekar rakt in i arbetskopian får `Operation not permitted` och startar aldrig.
Därför ligger den kod tjänsten kör i `~/CineMind`, utanför Documents, och
arbetskopian i `~/Documents/CineMind` förblir git-repot du utvecklar i.

Det betyder att **en ändring i repot syns inte förrän du synkar**:

```bash
./scripts/sync_runtime.sh
```

Skriptet speglar `backend/`, `frontend/build/` och `.runtime/python/` till
`~/CineMind`, startar om tjänsten och väntar tills den svarar. Efter en
frontend-ändring: `cd frontend && yarn build` först.

## Databasen

Den levande databasen ligger i `~/CineMind/data/mongo`. Den kopierades dit en
gång från `data/mongo/` i repot och synkas aldrig därifrån igen — kopian i
repot är bara den backup den föddes ur.

MongoDB 8.3.7 (samma version som skrev filerna) ligger uppackad i
`~/CineMind/.runtime/mongodb`. Det finns ingen Homebrew på maskinen, så
servern hämtas som tarball direkt från fastdl.mongodb.org, checksummeras och
packas upp i hemkatalogen — inget installeras systemvitt och inget
admin-lösenord behövs:

```bash
./scripts/install_mongodb.sh
```

mongod lyssnar bara på 127.0.0.1 och har ingen autentisering, precis som förut.

## Installera om allt från grunden

```bash
./scripts/install_cinemind_app.sh
```

Ett kommando som synkar runtime-kopian, ser till att MongoDB finns, installerar
båda LaunchAgents och bygger om `/Applications/CineMind.app`. Det går att köra
hur många gånger som helst och rör aldrig en databas som redan finns.
Kör det från Terminal — inte från launchd — så att macOS får läsa `~/Documents`.

## Handgrepp

```bash
# status
launchctl print gui/$(id -u)/com.cinemind.local   | head -20
launchctl print gui/$(id -u)/com.cinemind.mongodb | head -20

# starta om
launchctl kickstart -k gui/$(id -u)/com.cinemind.local
launchctl kickstart -k gui/$(id -u)/com.cinemind.mongodb

# stoppa till nästa inloggning
launchctl bootout gui/$(id -u)/com.cinemind.local

# loggar
tail -f ~/CineMind/.runtime/cinemind.log
tail -f ~/CineMind/.runtime/cinemind.error.log
tail -f ~/CineMind/.runtime/mongod.log
```

## Ikonen

Källan ligger i `scripts/icon/`: `cinemind-tile.svg` ritar brickan,
`round_corners.py` rundar hörnen och centrerar den på 1024×1024, och
`make_icon.sh` bygger om `CineMind.icns`:

```bash
./scripts/icon/make_icon.sh
./scripts/install_cinemind_app.sh   # lägger in den nya ikonen i appen
```

## Att köra före inloggning

En LaunchAgent hör till en inloggningssession, så CineMind är uppe från det att
du loggar in. Ska den svara redan vid inloggningsrutan krävs LaunchDaemons i
`/Library/LaunchDaemons`, vilket kräver admin-lösenord. Med automatisk
inloggning påslagen spelar skillnaden ingen roll i praktiken.

## Gamla skript

`enable_autostart.sh`, `run_cinemind.sh`, `install_boot_daemon.sh`,
`install_mongo_daemon.sh`, `open_cinemind.sh` och plist-filerna
`com.cinemind.app.plist` / `com.cinemind.boot.plist` hör till den tidigare
uppsättningen, när projektet låg i `~/CineMind` och hade ett venv och en
brew-installerad MongoDB. De fungerar inte som de står.
