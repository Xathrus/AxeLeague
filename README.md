# Abilene Axe League

Self-hosted league management and live scorekeeping app for axe throwing leagues.
Python + Flask + SQLite, no internet required, runs on your local network.

## Features

- **Branding** — admin-only Branding tab: set the venue name (replaces "Abilene Axe League" everywhere), upload a venue logo, and pick a five-color scheme (background, panels, borders, text, accent) with one-click presets — the default Stained Wood theme or a Red, White & Blue theme — so the app can be packaged for any venue
- **Logins & roles** — on first start the app prompts you to create Admin and
  Scorekeeper passwords. Admin has full access; Scorekeeper can score and edit
  matches only; "View Games & Stats" needs no password and is read-only
- **Season export / import** — download any season as a portable JSON file and import it into this or another installation (teams, rosters, schedule, bracket wiring, and every throw come across)
- **Seasons, teams, rosters** — create/rename/delete seasons, add teams and
  players (or copy all teams and rosters from a previous season in one click), rename players anytime; reset the schedule to bring new teams in
- **Double round robin scheduler** — every team plays every other team twice, played one round at a time (Round 1, Round 2, …)
- **Schedule control** — admin can move any match to any round (including a brand-new round) and set a date for each round; the schedule shows rounds grouped under their dates for everyone
- **Live scorekeeper** — side-by-side players, 10-cell color-coded throw grid, buttons for 1–5, bullseye (6), killshot (8), drop, miss, undo; tap any throw to edit it; swap a thrower mid-set (recorded throws re-credit to the new thrower); admin can reset a whole match
- **Full rules engine** — 3 sets per game, 3 games per match, first to 2 game wins; ties handled per league rules; sudden death with manual winner selection; lane swap divider at throw 5
- **Killshot call tracking** — 2 calls per player per set, +1 bonus call per drop, enforced in real time with pip indicators
- **Projector mode** — a big-type display page for a venue screen showing up to three matches at once, automatically featuring the three with the most recent scoring: match score, per-game totals, and the live set with throw-by-throw chips, refreshing every 3 seconds, with current standings for the season being played shown underneath
- **Multi-scorekeeper** — several browsers can score simultaneously; screens stay in sync (3-second polling)
- **CSV score import** — admin can upload a CSV on any match page to import its scores in one shot (replaces anything already recorded). Columns: `Game, Set, Thrower, Team, Throw 1 … Throw 10`; values 1-5, 6 = bullseye, 8 = killshot hit, or Miss / Drop / Kill Miss / Kill Drop; unknown throwers are added to the roster automatically. See `SampleMatch.csv`
- **Short rosters** — the same player may throw multiple sets in a game
- **Achievements** — 30 personal and team achievements (Club 50/60, Perfection, On Fire, First Blood, The Closer, Hope that Isn't a Fluke, season milestones through Deforestation at 1,500 points, Comeback, Nail Biter, Mercy Please, Perfect Storm, Giant Toppler, and more) detected automatically as scores come in — no admin action needed. Each achievement is earned once per player/team per season, credited to the first qualifying moment. Existing data is backfilled on first start, and corrections (edits, undos, resets) revoke achievements that no longer hold. Dedicated Achievements page per season with Personal/Team and per-player filters; the projector shows the five most recent
- **Stats** — regular season and playoffs tracked in separate sections; League Overview cards (regular-season league average, drop rate, bullseye ratio, high score, highest average, record holders) plus Weekly High Score cards per round date; every stats table sorts by clicking a column header; per player per season (avg/set, high, low, 50+%, bullseyes, bullseye %, drops, drop %, KS attempts, kill %). Bullseye percentages exclude killshot attempts, since a killshot can't score a bullseye, Weekly Averages grouped by round dates, per team per season
- **Standings** — W-L record, tiebroken by total bullseyes
- **Playoffs** — double elimination bracket seeded by standings, byes auto-resolved, grand final reset match if the lower-bracket team wins GF1

## Project layout

```
app.py            Flask routes (pages + JSON API)
auth.py           Logins, roles, first-run setup
branding.py       Venue logo + color scheme storage
scoring.py        Rules engine: outcomes, points, KS call logic, match state
bracket.py        Round robin scheduler + double elimination bracket
stats.py          Player/team stats and standings
achievements.py   Achievement definitions, detection, recompute
db.py             SQLite connection helpers
schema.sql        Database schema
templates/        Jinja2 pages
static/           scorekeeper.js + style.css
seed_demo.py      Optional: seed a demo season with 6 teams
set_password.py   Reset the Admin or Scorekeeper password from the shell
csv_import.py     CSV score import parser/validator
season_io.py      Season export/import (portable JSON)
SampleMatch.csv   Example import file
smoke_test.py     End-to-end test of the whole rules engine
deploy/           systemd unit + install script for the LXC
```

## Run locally (development)

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py          # http://localhost:8000
# optional demo data:
./venv/bin/python seed_demo.py
# run the test suite:
./venv/bin/python smoke_test.py
```

## Put it on GitHub

```bash
cd axe-league
git init
git add .
git commit -m "Abilene Axe League app"
git remote add origin https://github.com/Xathrus/AxeLeague.git
git push -u origin main
```

---

## Deploy on Proxmox (Ubuntu LXC)

Run these **on the Proxmox host** shell. Adjust `CTID`, storage names, and the
bridge to match your setup (`local-lvm` and `vmbr0` are the common defaults).

### 1. Download the Ubuntu template (one time)

```bash
pveam update
pveam available --section system | grep ubuntu-24.04
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

(If the exact filename differs, use whatever `pveam available` lists for 24.04.)

### 2. Create and start the container

```bash
CTID=210
pct create $CTID local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname axeleague \
  --memory 1024 --cores 2 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1
```

Prefer a fixed address so the league always knows the URL? Use a static IP
instead of DHCP, e.g.:

```bash
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.50/24,gw=192.168.1.1
```

### 3. Install the app inside the container

```bash
pct exec $CTID -- bash -c "apt-get update && apt-get install -y git"
pct exec $CTID -- git clone https://github.com/Xathrus/AxeLeague.git /opt/axeleague
pct exec $CTID -- bash /opt/axeleague/deploy/install.sh
```

No GitHub / no internet in the LXC? Copy the files in from the Proxmox host
instead of cloning:

```bash
pct push $CTID /path/on/host/axe-league.tar.gz /root/axe-league.tar.gz
pct exec $CTID -- bash -c "mkdir -p /opt/axeleague && tar xzf /root/axe-league.tar.gz -C /opt/axeleague --strip-components=1"
pct exec $CTID -- bash /opt/axeleague/deploy/install.sh
```

### 4. Open it and create your logins

The very first page you see prompts you to set the **Admin** and
**Scorekeeper** passwords. After that, everyone picks a role on the login
page; "View Games & Stats" requires no password.

### Finding the address

```bash
pct exec $CTID -- hostname -I
```

Browse to `http://<that-ip>/` from any device on your network. Scorekeepers
just need that URL — phones, tablets, and laptops all work.

### Optional: demo data

```bash
pct exec $CTID -- /opt/axeleague/venv/bin/python /opt/axeleague/seed_demo.py
pct exec $CTID -- systemctl restart axeleague
```

### Service management

```bash
pct exec $CTID -- systemctl status axeleague
pct exec $CTID -- journalctl -u axeleague -f
pct exec $CTID -- systemctl restart axeleague
```

### Forgot a password?

Reset either login from the Proxmox host (you'll be prompted for the new one):

```bash
pct exec $CTID -- bash -c "cd /opt/axeleague && ./venv/bin/python set_password.py admin"
pct exec $CTID -- bash -c "cd /opt/axeleague && ./venv/bin/python set_password.py scorekeeper"
```

Takes effect on the next login — no restart needed.

### Backups

Everything lives in one file: `/opt/axeleague/data/axeleague.db`. Copy it
anywhere to back it up (or use Proxmox's built-in container backups).

---

## How scoring maps to the rules

| Button | Outcome code | Points | Notes |
|---|---|---|---|
| 1–5 | `1`–`5` | 1–5 | Scored hit |
| 6 | `B` | 6 | Bullseye |
| KS → KS HIT | `KH` | 8 | Uses a killshot call |
| KS → KS Drop | `KD` | 0 | Uses a call, grants +1 bonus call |
| KS → KS Miss | `KM` | 0 | Uses a killshot call |
| Drop | `D` | 0 | Grants +1 bonus killshot call |
| Miss | `M` | 0 | |

Match flow: 3 games × 3 sets × 10 throws per player. Highest combined total
across a game's 3 sets wins the game; equal totals = tie. First team to 2 game
wins takes the match; if game wins are level after 3 games, the scorekeeper
declares the sudden-death winner from a dropdown, then completes the match.
