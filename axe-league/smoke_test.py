"""End-to-end smoke test. Run: python smoke_test.py (uses a throwaway DB)."""
import json
import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ["AXE_DB"] = os.path.join(tmp, "test.db")

import db  # noqa: E402
import app as appmod  # noqa: E402

app = appmod.app
app.config["TESTING"] = True
c = app.test_client()
PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1
    print("ok -", msg)


def post_json(url, payload=None):
    return c.post(url, data=json.dumps(payload or {}),
                  content_type="application/json")


def state(mid):
    r = c.get(f"/api/match/{mid}/state")
    assert r.status_code == 200
    return r.get_json()


def q(sql, *args):
    with app.app_context():
        return db.get_db().execute(sql, args).fetchall()


# ---------------------------------------------------------------- auth
r = c.get("/", follow_redirects=False)
ok(r.status_code in (302, 303) and "/setup" in r.headers["Location"],
   "first run redirects to setup")
r = c.post("/setup", data={"admin_password": "adminpw", "admin_password2": "nope",
                           "scorekeeper_password": "skpw",
                           "scorekeeper_password2": "skpw"})
ok(b"match" in r.data, "setup rejects mismatched passwords")
r = c.post("/setup", data={"admin_password": "adminpw", "admin_password2": "adminpw",
                           "scorekeeper_password": "skpw",
                           "scorekeeper_password2": "skpw"})
ok(r.status_code in (302, 303), "setup creates users and signs in")
r = c.get("/setup", follow_redirects=False)
ok(r.status_code in (302, 303), "setup unavailable once done")

c.post("/logout")
r = c.get("/", follow_redirects=False)
ok("/login" in r.headers.get("Location", ""), "logged out -> login redirect")

# viewer: read-only
c.post("/login", data={"role": "viewer"})
ok(c.get("/").status_code == 200, "viewer can view pages")
r = c.post("/seasons", data={"name": "Nope"})
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "viewer cannot create season")
r = post_json("/api/set/1/throw", {"player_id": 1, "outcome": "1"})
ok(r.status_code == 403, "viewer blocked from scoring API")
c.post("/logout")

# scorekeeper: wrong then right password
r = c.post("/login", data={"role": "scorekeeper", "password": "wrong"})
ok(b"Wrong password" in r.data, "wrong password rejected")
c.post("/login", data={"role": "scorekeeper", "password": "skpw"})
r = c.post("/seasons", data={"name": "Nope"})
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "scorekeeper cannot create season")
c.post("/logout")

# admin for the rest of the run
c.post("/login", data={"role": "admin", "password": "adminpw"})

# ---------------------------------------------------------------- setup
r = c.post("/seasons", data={"name": "Test Season"})
ok(r.status_code in (302, 303), "create season")
season_id = q("SELECT id FROM seasons")[0]["id"]

for t in ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]:
    c.post(f"/season/{season_id}/teams", data={"name": t})
team_rows = q("SELECT id, name FROM teams WHERE season_id=? ORDER BY id", season_id)
ok(len(team_rows) == 5, "5 teams created")
tid = {r["name"]: r["id"] for r in team_rows}

for name, players in {"Alpha": ["A1", "A2", "A3"], "Bravo": ["B1", "B2", "B3"],
                      "Charlie": ["C1", "C2"], "Delta": ["D1", "D2", "D3"],
                      "Echo": ["E1", "E2"]}.items():
    for p in players:
        c.post(f"/team/{tid[name]}/players", data={"name": p})
pid = {r["name"]: r["id"] for r in q(
    "SELECT p.id, p.name FROM players p JOIN teams t ON p.team_id=t.id"
    " WHERE t.season_id=?", season_id)}
ok(len(pid) == 13, "13 players created")

# ---------------------------------------------------------------- schedule
c.post(f"/season/{season_id}/schedule/generate")
matches = q("SELECT * FROM matches WHERE season_id=? AND stage='regular'"
            " ORDER BY id", season_id)
ok(len(matches) == 20, f"double round robin = 20 matches (got {len(matches)})")
nsets = q("SELECT COUNT(*) n FROM sets s JOIN games g ON s.game_id=g.id"
          " JOIN matches m ON g.match_id=m.id WHERE m.season_id=?",
          season_id)[0]["n"]
ok(nsets == 20 * 9, "9 sets pre-created per match")


# ---------------------------------------------------------------- helpers
def find_match(a, b):
    for m in matches:
        if {m["home_team_id"], m["away_team_id"]} == {tid[a], tid[b]}:
            return m["id"]
    raise RuntimeError("no match")


def sets_of(mid):
    return [s for g in state(mid)["games"] for s in g["sets"]]


def assign(sid, home_pid, away_pid):
    r = post_json(f"/api/set/{sid}/assign",
                  {"home_player_id": home_pid, "away_player_id": away_pid})
    ok(r.status_code == 200, f"assign players to set {sid}")


def throw(sid, player, outcome, expect=200):
    r = post_json(f"/api/set/{sid}/throw",
                  {"player_id": player, "outcome": outcome})
    ok(r.status_code == expect,
       f"throw {outcome} set {sid} -> {expect} (got {r.status_code})")


def fill(sid, hp, ap, ho, ao):
    for o in ho:
        throw(sid, hp, o)
    for o in ao:
        throw(sid, ap, o)


m1 = find_match("Alpha", "Bravo")
st = state(m1)
home_is_alpha = st["match"]["home_team_id"] == tid["Alpha"]
HP = {1: pid["A1"], 2: pid["A2"], 3: pid["A3"]} if home_is_alpha else \
     {1: pid["B1"], 2: pid["B2"], 3: pid["B3"]}
AP = {1: pid["B1"], 2: pid["B2"], 3: pid["B3"]} if home_is_alpha else \
     {1: pid["A1"], 2: pid["A2"], 3: pid["A3"]}
ss = sets_of(m1)

# Game 1: home dominant
assign(ss[0]["id"], HP[1], AP[1]); fill(ss[0]["id"], HP[1], AP[1], ["5"]*10, ["1"]*10)
assign(ss[1]["id"], HP[2], AP[2]); fill(ss[1]["id"], HP[2], AP[2], ["B"]*10, ["2"]*10)
assign(ss[2]["id"], HP[3], AP[3]); fill(ss[2]["id"], HP[3], AP[3], ["3"]*10, ["3"]*10)
st = state(m1)
ok(st["games"][0]["home_total"] == 140 and st["games"][0]["away_total"] == 60,
   "game 1 totals 140-60")
ok(st["games"][0]["winner"] == "home", "game 1 won by home")

# scorekeeper role can record throws (switch roles mid-stream)
c.post("/logout")
c.post("/login", data={"role": "scorekeeper", "password": "skpw"})
throw(ss[3]["id"], HP[1], "1", expect=400)  # set has no players assigned yet -> 400 (auth passed)
c.post("/logout")
c.post("/login", data={"role": "admin", "password": "adminpw"})

# KS enforcement on game 2 set 1
g2s1 = ss[3]["id"]
assign(g2s1, HP[1], AP[1])
throw(g2s1, HP[1], "KH")
throw(g2s1, HP[1], "KM")
throw(g2s1, HP[1], "KH", expect=400)   # 2 base calls used
throw(g2s1, HP[1], "D")                # +1 bonus call
throw(g2s1, HP[1], "KD")               # use it; KS drop grants +1 more
throw(g2s1, HP[1], "KH")               # use that one
throw(g2s1, HP[1], "KM", expect=400)   # exhausted
for _ in range(5):
    throw(g2s1, HP[1], "1")
throw(g2s1, HP[1], "1", expect=400)    # 11th throw rejected
for _ in range(10):
    throw(g2s1, AP[1], "M")

# undo + re-throw
r = post_json(f"/api/set/{g2s1}/undo", {"player_id": AP[1]})
ok(r.status_code == 200, "undo last throw")
throw(g2s1, AP[1], "2")

# edit: change first away miss to bullseye
g2 = state(m1)["games"][1]
t0 = g2["sets"][0]["away_throws"][0]["id"]
r = post_json(f"/api/throw/{t0}/edit", {"outcome": "B"})
ok(r.status_code == 200, "edit throw to bullseye")
# editing into a KS that breaks the call sequence must fail
last_home = state(m1)["games"][1]["sets"][0]["home_throws"][-1]["id"]
r = post_json(f"/api/throw/{last_home}/edit", {"outcome": "KH"})
ok(r.status_code == 400, "edit violating KS sequence rejected")

# finish game 2 as a tie, game 3 as a tie -> home 1 win vs 0 -> decided
g2 = state(m1)["games"][1]
diff = g2["home_total"] - g2["away_total"]
assign(ss[4]["id"], HP[2], AP[2])
fill(ss[4]["id"], HP[2], AP[2], ["M"]*10, ["M"]*10)
away_fill, rem = [], diff
while rem > 0:
    p = min(5, rem); away_fill.append(str(p)); rem -= p
away_fill += ["M"] * (10 - len(away_fill))
assign(ss[5]["id"], HP[3], AP[3])
fill(ss[5]["id"], HP[3], AP[3], ["M"]*10, away_fill)
ok(state(m1)["games"][1]["winner"] == "tie", "game 2 engineered tie")

for i in (6, 7, 8):
    assign(ss[i]["id"], HP[1], AP[1])
    fill(ss[i]["id"], HP[1], AP[1], ["M"]*10, ["M"]*10)
st = state(m1)
ok(st["status"]["state"] == "decided"
   and st["status"]["winner_team_id"] == st["match"]["home_team_id"],
   "1 win vs 0 after 3 games = decided")

ok(post_json(f"/api/match/{m1}/complete").status_code == 200, "complete match 1")
ok(state(m1)["status"]["state"] == "completed", "match 1 completed")
ok(post_json(f"/api/match/{m1}/reopen").status_code == 200, "reopen regular match")
post_json(f"/api/match/{m1}/complete")

# ---------------------------------------------------------------- sudden death
m2 = find_match("Charlie", "Delta")
st2 = state(m2)
hp = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
       st2["match"]["home_team_id"])[0]["id"]
ap = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
       st2["match"]["away_team_id"])[0]["id"]
ss2 = sets_of(m2)
plans = [(["5"]*10, ["1"]*10), (["1"]*10, ["5"]*10), (["M"]*10, ["M"]*10)]
for gi, (ho, ao) in enumerate(plans):
    for si in range(3):
        s = ss2[gi*3+si]["id"]
        assign(s, hp, ap)
        fill(s, hp, ap, ho if si == 0 else ["M"]*10, ao if si == 0 else ["M"]*10)
ok(state(m2)["status"]["state"] == "sudden_death",
   "1-1 after 3 games triggers sudden death")
ok(post_json(f"/api/match/{m2}/sudden_death",
             {"winner_team_id": tid["Charlie"]}).status_code == 200,
   "declare sudden death winner")
ok(post_json(f"/api/match/{m2}/complete").status_code == 200,
   "complete sudden-death match")
st2 = state(m2)
ok(st2["status"]["state"] == "completed"
   and st2["status"]["winner_team_id"] == tid["Charlie"],
   "sudden death winner recorded")
ok(True, "same player throwing every set (short roster) allowed")

# ---------------------------------------------------------------- stats
ok(c.get(f"/season/{season_id}/stats").status_code == 200, "stats page renders")
ok(c.get(f"/season/{season_id}/standings").status_code == 200,
   "standings page renders")

import stats as statsmod  # noqa: E402
with app.app_context():
    d = db.get_db()
    ps = {p["name"]: p for p in statsmod.player_season_stats(d, season_id)}
    stnd = statsmod.standings(d, season_id)
hname = "A1" if home_is_alpha else "B1"
h1 = ps[hname]
ok(h1["high"] == 50, f"{hname} high score 50 (got {h1['high']})")
ok(h1["ks_att"] == 4, f"{hname} KS attempts 4 (got {h1['ks_att']})")
ok(abs(h1["kill_pct"] - 50.0) < 1e-9, f"{hname} kill% 50 (got {h1['kill_pct']})")
h2 = ps["A2" if home_is_alpha else "B2"]
ok(h2["bulls"] == 10, "bullseye count 10")
ok(stnd[0]["wins"] >= 1, "standings computed")

# ---------------------------------------------------------------- playoffs
remaining = q("SELECT id FROM matches WHERE season_id=? AND stage='regular'"
              " AND completed=0", season_id)
for row in remaining:
    mid = row["id"]
    stx = state(mid)
    hpid = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
             stx["match"]["home_team_id"])[0]["id"]
    apid = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
             stx["match"]["away_team_id"])[0]["id"]
    sl = sets_of(mid)
    for gi in (0, 1, 2):
        for si in range(3):
            s = sl[gi*3+si]["id"]
            assign(s, hpid, apid)
            fill(s, hpid, apid,
                 ["1"]*10 if si == 0 and gi < 2 else ["M"]*10, ["M"]*10)
    r = post_json(f"/api/match/{mid}/complete")
    ok(r.status_code == 200, f"complete remaining match {mid}")

r = c.post(f"/season/{season_id}/playoffs/create")
ok(r.status_code in (200, 302, 303), "create playoff bracket")
pms = q("SELECT * FROM matches WHERE season_id=? AND stage='playoff'", season_id)
ok(len(pms) > 0, f"playoff matches created ({len(pms)})")
auto = q("SELECT COUNT(*) n FROM matches WHERE season_id=? AND stage='playoff'"
         " AND completed=1", season_id)[0]["n"]
ok(auto >= 3, f"bye matches auto-resolved ({auto})")

import bracket as bmod  # noqa: E402
for _ in range(60):
    nxt = q("SELECT id, home_team_id, away_team_id FROM matches WHERE season_id=?"
            " AND stage='playoff' AND completed=0 AND home_team_id IS NOT NULL"
            " AND away_team_id IS NOT NULL LIMIT 1", season_id)
    if not nxt:
        break
    mid = nxt[0]["id"]
    hpid = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
             nxt[0]["home_team_id"])[0]["id"]
    apid = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
             nxt[0]["away_team_id"])[0]["id"]
    sl = sets_of(mid)
    for gi in (0, 1, 2):
        for si in range(3):
            s = sl[gi*3+si]["id"]
            assign(s, hpid, apid)
            fill(s, hpid, apid,
                 ["1"]*10 if si == 0 and gi < 2 else ["M"]*10, ["M"]*10)
    r = post_json(f"/api/match/{mid}/complete")
    ok(r.status_code == 200, f"complete playoff match {mid}")

with app.app_context():
    champ = bmod.champion(db.get_db(), season_id)
ok(champ is not None, f"champion determined (team id {champ})")

pm = q("SELECT id FROM matches WHERE season_id=? AND stage='playoff'"
       " AND completed=1 AND home_team_id IS NOT NULL LIMIT 1", season_id)[0]["id"]
ok(post_json(f"/api/match/{pm}/reopen").status_code == 400,
   "playoff match reopen rejected")

# ---------------------------------------------------------------- new admin features
# playoff stats section appears on the stats page
r = c.get(f"/season/{season_id}/stats")
ok(b"Playoffs" in r.data, "playoff stats section shown once playoff data exists")
with app.app_context():
    d = db.get_db()
    pp = {p["name"]: p for p in statsmod.player_season_stats(d, season_id,
                                                             stage="playoff")}
    rp = {p["name"]: p for p in statsmod.player_season_stats(d, season_id,
                                                             stage="regular")}
hname2 = "A1" if home_is_alpha else "B1"
ok(rp[hname2]["high"] == 50, "regular-season stats exclude playoff sets")
any_playoff = [p for p in pp.values() if p["sets"]]
ok(len(any_playoff) > 0, "playoff stats computed separately")

# rename player
some_pid = pid["A1"]
r = c.post(f"/player/{some_pid}/rename", data={"name": "A1 Renamed"})
ok(r.status_code in (302, 303), "rename player accepted")
new_name = q("SELECT name FROM players WHERE id=?", some_pid)[0]["name"]
ok(new_name == "A1 Renamed", "player rename persisted")

# rename season
r = c.post(f"/season/{season_id}/rename", data={"name": "Renamed Season"})
ok(q("SELECT name FROM seasons WHERE id=?", season_id)[0]["name"]
   == "Renamed Season", "season rename persisted")

# schedule page says Round, not Week
r = c.get(f"/season/{season_id}/schedule")
ok(b"Round 1" in r.data and b"Week 1" not in r.data,
   "schedule shows Round labels")

# reset schedule on a fresh throwaway season (with new team added after)
c.post("/seasons", data={"name": "Scratch"})
sid2 = q("SELECT id FROM seasons ORDER BY id DESC LIMIT 1")[0]["id"]
for t in ("X", "Y"):
    c.post(f"/season/{sid2}/teams", data={"name": t})
c.post(f"/season/{sid2}/schedule/generate")
n1 = q("SELECT COUNT(*) n FROM matches WHERE season_id=?", sid2)[0]["n"]
ok(n1 == 2, "scratch schedule generated (2 teams = 2 matches)")
c.post(f"/season/{sid2}/teams", data={"name": "Z"})
c.post(f"/season/{sid2}/schedule/reset")
ok(q("SELECT COUNT(*) n FROM matches WHERE season_id=?", sid2)[0]["n"] == 0,
   "schedule reset removed matches")
c.post(f"/season/{sid2}/schedule/generate")
n2 = q("SELECT COUNT(*) n FROM matches WHERE season_id=?", sid2)[0]["n"]
ok(n2 == 6, f"regenerated schedule includes new team (3 teams = 6 matches, got {n2})")

# --- schedule rearranging & round dates (on the scratch season) ---
mids = q("SELECT id, week FROM matches WHERE season_id=? ORDER BY id", sid2)
nrounds = len({m["week"] for m in mids})
ok(nrounds >= 3, f"scratch season has multiple rounds ({nrounds})")
mv = mids[0]["id"]
# viewer can't move
c.post("/logout"); c.post("/login", data={"role": "viewer"})
r = c.post(f"/match/{mv}/move", data={"round": 2})
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "viewer cannot move matches")
c.post("/logout"); c.post("/login", data={"role": "admin", "password": "adminpw"})
# admin moves match 1 -> round 3, then to brand-new round 4
c.post(f"/match/{mv}/move", data={"round": 3})
ok(q("SELECT week FROM matches WHERE id=?", mv)[0]["week"] == 3,
   "match moved to round 3")
newr = nrounds + 1
c.post(f"/match/{mv}/move", data={"round": newr})
ok(q("SELECT week FROM matches WHERE id=?", mv)[0]["week"] == newr,
   f"match moved to brand-new round {newr}")
r = c.post(f"/match/{mv}/move", data={"round": 0})
ok(r.status_code == 400, "invalid round rejected")

# round dates: set, display, group, clear
c.post(f"/season/{sid2}/round_date", data={"round": 1, "date": "2026-07-18"})
c.post(f"/season/{sid2}/round_date", data={"round": 2, "date": "2026-07-18"})
c.post(f"/season/{sid2}/round_date", data={"round": newr, "date": "2026-07-25"})
r = c.get(f"/season/{sid2}/schedule")
ok(b"Saturday, Jul 18, 2026" in r.data, "round date shown on schedule")
ok(r.data.count(b"Saturday, Jul 18, 2026") == 1,
   "rounds sharing a date grouped under one date header")
ok(b"Date not set" in r.data, "undated round shown under 'Date not set'")
c.post(f"/season/{sid2}/round_date", data={"round": newr, "date": "x", "clear": "1"})
ok(q("SELECT COUNT(*) n FROM round_dates WHERE season_id=? AND round=?",
     sid2, newr)[0]["n"] == 0, "round date cleared")

# weekly averages grouped by date (main season)
c.post(f"/season/{season_id}/round_date", data={"round": 1, "date": "2026-07-04"})
c.post(f"/season/{season_id}/round_date", data={"round": 2, "date": "2026-07-04"})
with app.app_context():
    cols, wrows = statsmod.player_weekly_averages(db.get_db(), season_id)
labels = [cc["label"] for cc in cols]
ok("Jul 4, 2026" in labels, "weekly average column labeled by date")
ok(labels.count("Jul 4, 2026") == 1, "rounds 1+2 merged into one week column")
ok(any(l.startswith("Rd ") for l in labels), "undated rounds keep Rd columns")
jul4 = [cc for cc in cols if cc["label"] == "Jul 4, 2026"][0]
ok(any(jul4["key"] in p["weeks"] for p in wrows),
   "merged week has averaged data")
r = c.get(f"/season/{season_id}/stats")
ok(b"Weekly Average" in r.data, "stats page shows Weekly Average section")

# delete season
c.post(f"/season/{sid2}/delete")
ok(q("SELECT COUNT(*) n FROM seasons WHERE id=?", sid2)[0]["n"] == 0,
   "season deleted")
ok(q("SELECT COUNT(*) n FROM matches WHERE season_id=?", sid2)[0]["n"] == 0,
   "season delete cascaded to matches")

print(f"\nALL {PASS} CHECKS PASSED")
