# CineMind autostart

## What runs today

- **LaunchAgent `com.cinemind.app`** (`~/Library/LaunchAgents/com.cinemind.app.plist`)
  starts CineMind at every login and restarts it if the process dies.
  One uvicorn process serves the API, the built frontend and the job scheduler
  on <http://localhost:8001>.
- **MongoDB** starts on its own through `brew services` (also at login).
- **CineMind.app on the Desktop** — double-click to open the app. If the
  service is down it loads and starts the agent first, waits for it to answer,
  then opens the browser.

### Handy commands

```bash
# status
launchctl print gui/$(id -u)/com.cinemind.app | head -20

# restart after a rebuild
launchctl kickstart -k gui/$(id -u)/com.cinemind.app

# stop until next login
launchctl bootout gui/$(id -u)/com.cinemind.app

# logs
tail -f ~/Library/Logs/cinemind.log
```

After changing frontend code: `cd frontend && yarn build`, then kickstart.

## Running from boot, before anyone logs in

A LaunchAgent belongs to a login session, so CineMind is up from the moment you
log in. To have it running at the login screen too, install the system daemon.
It touches `/Library/LaunchDaemons`, so it needs an admin password — run it
yourself, in one command:

```bash
sudo /Users/gilbert/CineMind/scripts/install_boot_daemon.sh
```

That single script does everything:

1. stops and deletes the per-login LaunchAgent, so nothing starts twice,
2. moves MongoDB from a login service to a boot service,
3. installs `com.cinemind.boot` in `/Library/LaunchDaemons` (root:wheel, 644),
4. bootstraps it into the system domain and enables it,
5. waits for <http://localhost:8001> and prints the status of all three parts.

To go back to the login-only agent:

```bash
sudo /Users/gilbert/CineMind/scripts/uninstall_boot_daemon.sh
```

### MongoDB also has to start at boot

`brew services` refuses to run as root ("Formula mongodb-community has not
implemented #plist"), so the installer cannot move MongoDB on its own. Run this
once, after the CineMind daemon is in place:

```bash
sudo /Users/gilbert/CineMind/scripts/install_mongo_daemon.sh
```

It writes `/Library/LaunchDaemons/homebrew.mxcl.mongodb-community.plist`
(mongod still runs as `gilbert`, which owns `/opt/homebrew/var/mongodb`),
removes the login agent, then restarts CineMind against it.

### Checking it afterwards

```bash
sudo launchctl print system/com.cinemind.boot | head -20   # state = running
brew services list | grep mongodb                          # started as root
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/api/
```

A real proof that it starts before login is a reboot: log out, and the app
still answers; reboot, and it answers from the login screen.
