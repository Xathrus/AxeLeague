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
phantom5 = q("SELECT COUNT(*) n FROM matches WHERE season_id=?"
             " AND stage='playoff' AND completed=1 AND winner_team_id IS NULL"
             " AND (home_team_id IS NOT NULL OR away_team_id IS NOT NULL)",
             season_id)[0]["n"]
ok(phantom5 == 0, "5-team bracket: every match with a team has a real result")

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

# drops / drop rate and KS-excluded bull%
# HP[1] in match 1: 100 throws total? No — count his throws:
#   g1s1: 10, g2s1: 10 (incl KH,KM,D,KD,KH), g3 sets 1-3: 30 -> 50 throws
#   drops = D + KD = 2 -> drop rate 4%
#   bulls = 0; non-KS throws = 50 - 4 = 46 -> bull% = 0
hstats = rp[hname2]
ok(hstats["drops"] == 2, f"{hname2} drop count 2 (got {hstats['drops']})")
exp_drop = 100.0 * 2 / (hstats["sets"] * 10)  # every set is 10 throws
ok(abs(hstats["drop_pct"] - exp_drop) < 1e-9,
   f"{hname2} drop rate {exp_drop:.2f}% (got {hstats['drop_pct']:.2f}%)")
# A2/B2 (10 throws all bullseyes in g1s2, 10 misses in g2s2, 0 KS):
# bull% = 100*10/20 = 50 with KS-free denominator
h2s = rp["A2" if home_is_alpha else "B2"]
ok(abs(h2s["bull_pct"] - 50.0) < 1e-9,
   f"bull%% excludes KS attempts (got {h2s['bull_pct']})")

# league overview
with app.app_context():
    ov = statsmod.league_overview(db.get_db(), season_id)
ok(ov is not None, "league overview computed")
ok(ov["avg_score"] > 0, "league average score present")
ok(ov["drop_pct"] is not None and ov["bull_pct"] is not None,
   "league drop rate and bullseye ratio present")
ok(ov["high_score"]["value"] == 60 and len(ov["high_score"]["holders"]) >= 1,
   f"league high score 60 with holder(s) (got {ov['high_score']})")
ok(ov["most_bulls"] and ov["most_bulls"]["value"] >= 10
   and ov["most_bulls"]["holders"], "most bullseyes leader found")
ok(ov["best_kill_pct"] and 0 < ov["best_kill_pct"]["value"] <= 100,
   "best killshot%% leader found")
r = c.get(f"/season/{season_id}/stats")
ok(b"League Overview" in r.data, "stats page shows League Overview")
ok(b"Drop %" in r.data, "player table shows Drop %% column")

# overview excludes playoffs: avg must equal the regular-season set average
exp_avg = q("""SELECT AVG(t2.tot) a FROM (
    SELECT SUM(t.points) tot FROM throws t
    JOIN sets s ON s.id=t.set_id JOIN games g ON g.id=s.game_id
    JOIN matches m ON m.id=g.match_id
    WHERE m.season_id=? AND m.stage='regular'
    GROUP BY t.player_id, s.id) t2""", season_id)[0]["a"]
ok(abs(ov["avg_score"] - exp_avg) < 1e-9,
   "league overview uses regular-season data only")

# weekly high scores match weekly-average grouping; season high 60 present
with app.app_context():
    whs = statsmod.weekly_high_scores(db.get_db(), season_id)
    cols0, _ = statsmod.player_weekly_averages(db.get_db(), season_id)
ok(len(whs) == len(cols0), "weekly highs have one card per weekly-average column")
ok([w["label"] for w in whs] == [cc["label"] for cc in cols0],
   "weekly high labels match weekly columns")
ok(any(w["value"] == 60 for w in whs), "the 60-point set tops its week")
ok(all(w["holders"] for w in whs), "every weekly high has holder(s)")
# filler matches give two different home players a 10-point set in the same
# round, so at least one week must show a genuine tie with all names listed
ok(any(len(w["holders"]) >= 2 for w in whs),
   "tied weekly highs list every tied thrower")
tied_week = [w for w in whs if len(w["holders"]) >= 2][0]
ok(len({h["name"] for h in tied_week["holders"]}) == len(tied_week["holders"]),
   "tied holders are unique players")
names = [h["name"].lower() for h in tied_week["holders"]]
ok(names == sorted(names), "tied holders listed alphabetically")
r2 = c.get(f"/season/{season_id}/stats")
ok(b"-way tie" in r2.data, "tie annotation rendered on stats page")
ok(b"Weekly High Scores" in r.data, "stats page shows Weekly High Scores")
ok(b"Current high score" in r.data, "overview shows current high score card")

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

# --- LB cross-bracket drops: no instant rematches (6-team repro) ---
c.post("/seasons", data={"name": "Bracket Season"})
sidB = q("SELECT id FROM seasons ORDER BY id DESC LIMIT 1")[0]["id"]
for i in range(1, 7):
    c.post(f"/season/{sidB}/teams", data={"name": f"T{i}"})
for row in q("SELECT id FROM teams WHERE season_id=?", sidB):
    c.post(f"/team/{row['id']}/players", data={"name": f"P{row['id']}"})
c.post(f"/season/{sidB}/schedule/generate")

def _play_all(season, stage):
    for _ in range(80):
        nxt = q("SELECT id, home_team_id, away_team_id FROM matches"
                " WHERE season_id=? AND stage=? AND completed=0"
                " AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL"
                " LIMIT 1", season, stage)
        if not nxt:
            break
        mm = nxt[0]
        hp = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
               mm["home_team_id"])[0]["id"]
        ap = q("SELECT id FROM players WHERE team_id=? LIMIT 1",
               mm["away_team_id"])[0]["id"]
        sl = sets_of(mm["id"])
        for gi in (0, 1):
            for si in range(3):
                sx = sl[gi*3+si]["id"]
                assign(sx, hp, ap)
                fill(sx, hp, ap, ["1"]*10 if si == 0 else ["M"]*10, ["M"]*10)
        post_json(f"/api/match/{mm['id']}/complete")

_play_all(sidB, "regular")
c.post(f"/season/{sidB}/playoffs/create")
_play_all(sidB, "playoff")

wb1_pairs = {frozenset((m["home_team_id"], m["away_team_id"]))
             for m in q("SELECT home_team_id, away_team_id FROM matches"
                        " WHERE season_id=? AND stage='playoff' AND bracket='W'"
                        " AND bracket_round=1 AND home_team_id IS NOT NULL"
                        " AND away_team_id IS NOT NULL", sidB)}
lb2_pairs = {frozenset((m["home_team_id"], m["away_team_id"]))
             for m in q("SELECT home_team_id, away_team_id FROM matches"
                        " WHERE season_id=? AND stage='playoff' AND bracket='L'"
                        " AND bracket_round=2 AND home_team_id IS NOT NULL"
                        " AND away_team_id IS NOT NULL", sidB)}
ok(len(wb1_pairs) == 2, f"6-team bracket: 2 real WB R1 matches (got {len(wb1_pairs)})")
ok(len(lb2_pairs) == 2, f"6-team bracket: 2 LB R2 matches (got {len(lb2_pairs)})")
ok(not (wb1_pairs & lb2_pairs),
   "LB round 2 no longer mirrors WB round 1 (cross-bracket drop)")
with app.app_context():
    okc = bmod.champion(db.get_db(), sidB)
ok(okc is not None, "6-team bracket still resolves to a champion")
phantom = q("SELECT COUNT(*) n FROM matches WHERE season_id=?"
            " AND stage='playoff' AND completed=1 AND winner_team_id IS NULL"
            " AND (home_team_id IS NOT NULL OR away_team_id IS NOT NULL)",
            sidB)[0]["n"]
ok(phantom == 0,
   "no match with teams was phantom-completed as a bye (propagate fix)")
c.post(f"/season/{sidB}/delete")

# --- copy teams from another season ---
c.post("/seasons", data={"name": "Next Season"})
sidN = q("SELECT id FROM seasons ORDER BY id DESC LIMIT 1")[0]["id"]
# pre-add one clashing team to prove skipping works
c.post(f"/season/{sidN}/teams", data={"name": "alpha"})  # case-insensitive clash
# viewer can't copy
c.post("/logout"); c.post("/login", data={"role": "viewer"})
r = c.post(f"/season/{sidN}/teams/copy", data={"source_season_id": season_id})
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "viewer cannot copy teams")
c.post("/logout"); c.post("/login", data={"role": "admin", "password": "adminpw"})
r = c.post(f"/season/{sidN}/teams/copy", data={"source_season_id": season_id},
           follow_redirects=True)
ok(b"Copied 4 teams" in r.data, "copied the 4 non-clashing teams")
ok(b"Skipped 1 team" in r.data, "clashing team skipped, not duplicated")
ntN = q("SELECT COUNT(*) n FROM teams WHERE season_id=?", sidN)[0]["n"]
ok(ntN == 5, f"target season has 5 teams (got {ntN})")
# rosters came along (source had 13 players; Alpha's 3 stay behind w/ the clash)
npN = q("SELECT COUNT(*) n FROM players p JOIN teams t ON p.team_id=t.id"
        " WHERE t.season_id=?", sidN)[0]["n"]
ok(npN == 10, f"rosters copied with their teams (got {npN})")
# renamed player from earlier ('A1 Renamed') came across with Alpha? no — Alpha
# was skipped; check a Bravo player copied by name
bnames = {r["name"] for r in q(
    "SELECT p.name FROM players p JOIN teams t ON p.team_id=t.id"
    " WHERE t.season_id=? AND t.name='Bravo'", sidN)}
ok(bnames == {"B1", "B2", "B3"}, f"Bravo roster copied intact (got {bnames})")
# re-run: everything skipped
r = c.post(f"/season/{sidN}/teams/copy", data={"source_season_id": season_id},
           follow_redirects=True)
ok(b"Copied 0 teams" in r.data and b"Skipped 5" in r.data,
   "second copy run skips everything")
ok(c.post(f"/season/{sidN}/teams/copy",
          data={"source_season_id": sidN}).status_code == 400,
   "copying a season into itself rejected")
c.post(f"/season/{sidN}/delete")

# --- CSV import (uses the real sample file) ---
import io
sample = open("SampleMatch.csv", "rb").read()

def csv_named(data, home, away):
    """Rewrite the sample's team names onto an actual match's teams."""
    t = data.decode("utf-8-sig")
    t = t.replace("Axe of Violence", home).replace("Tomahawks", away)
    return t.encode("utf-8")

# fresh season so the import doesn't disturb earlier assertions
c.post("/seasons", data={"name": "Import Season"})
sid3 = q("SELECT id FROM seasons ORDER BY id DESC LIMIT 1")[0]["id"]
for t in ("Axe of Violence", "Tomahawks"):
    c.post(f"/season/{sid3}/teams", data={"name": t})
c.post(f"/season/{sid3}/schedule/generate")
im = q("SELECT * FROM matches WHERE season_id=? ORDER BY id LIMIT 1", sid3)[0]
imid = im["id"]
hname3 = q("SELECT name FROM teams WHERE id=?", im["home_team_id"])[0]["name"]
aname3 = q("SELECT name FROM teams WHERE id=?", im["away_team_id"])[0]["name"]

# viewer / scorekeeper can't import
c.post("/logout"); c.post("/login", data={"role": "scorekeeper", "password": "skpw"})
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(sample), "s.csv")},
           content_type="multipart/form-data")
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "scorekeeper cannot import CSV")
c.post("/logout"); c.post("/login", data={"role": "admin", "password": "adminpw"})

# happy path: the provided sample imports cleanly
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(csv_named(sample, hname3, aname3)), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"Imported 180 throws" in r.data,
   "sample CSV imported (18 rows x 10 throws)")
ok(b"New players added" in r.data, "unknown throwers auto-created")
nplayers = q("SELECT COUNT(*) n FROM players p JOIN teams t ON p.team_id=t.id"
             " WHERE t.season_id=?", sid3)[0]["n"]
ok(nplayers == 6, f"6 throwers created from CSV (got {nplayers})")

st3 = state(imid)
# spot-check from the sample: Game 1 Set 1 = Curtis 53 (5,5,4,6,5,6,4,6,6,6)
# vs Doc 57 (5,6,6,5,6,6,6,6,6,5); verify a known killshot too
g1s1 = st3["games"][0]["sets"][0]
totals = {g1s1["home_player_name"]: sum(t["points"] for t in g1s1["home_throws"]),
          g1s1["away_player_name"]: sum(t["points"] for t in g1s1["away_throws"])}
ok(totals.get("Curtis Johnson") == 53, f"Curtis G1S1 = 53 (got {totals})")
ok(totals.get('Patrick "Doc" Bruton') == 57,
   "quoted thrower name parsed correctly (Doc = 57)")
g1s3 = st3["games"][0]["sets"][2]
last = (g1s3["home_throws"] + g1s3["away_throws"])
ok(any(t["outcome"] == "KH" for t in last), "score of 8 imported as killshot hit")
g3s3 = st3["games"][2]["sets"][2]
kms = [t for t in g3s3["home_throws"] + g3s3["away_throws"]
       if t["outcome"] == "KM"]
ok(len(kms) == 3, f"'Kill Miss' cells imported as KM (got {len(kms)})")
ok(st3["status"]["state"] in ("decided", "sudden_death", "in_progress"),
   "match state recomputed after import")

# re-import replaces, not duplicates
c.post(f"/match/{imid}/import",
       data={"csv": (io.BytesIO(csv_named(sample, hname3, aname3)), "s.csv")},
       content_type="multipart/form-data")
nth = q("""SELECT COUNT(*) n FROM throws t JOIN sets s ON s.id=t.set_id
           JOIN games g ON g.id=s.game_id WHERE g.match_id=?""", imid)[0]["n"]
ok(nth == 180, f"re-import replaced scores (180 throws, got {nth})")
np2 = q("SELECT COUNT(*) n FROM players p JOIN teams t ON p.team_id=t.id"
        " WHERE t.season_id=?", sid3)[0]["n"]
ok(np2 == 6, "re-import didn't duplicate players")

# error cases: wrong team, bad value, KS overuse, gap, completed match
bad = csv_named(sample, hname3, aname3).decode().replace(hname3, "Wrong Team", 1)
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(bad.encode()), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"isn&#39;t in this match" in r.data or b"isn't in this match" in r.data,
   "unknown team rejected with row number")
bad2 = csv_named(sample, hname3, aname3).decode().replace(",6\r\n", ",7\r\n", 1)
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(bad2.encode()), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"unrecognized throw value" in r.data, "invalid score value rejected")
hdr = "Game,Set,Thrower,Team," + ",".join(f"Throw {i}" for i in range(1, 11))
ks_bad = hdr + "\r\n" + f"1,1,P1,{hname3},8,8,8,1,1,1,1,1,1,1\r\n"
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(ks_bad.encode()), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"killshot calls exceed" in r.data, "impossible killshot sequence rejected")
gap = hdr + "\r\n" + f"1,1,P1,{hname3},1,1,,1,1,1,1,1,1,1\r\n"
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(gap.encode()), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"no gaps" in r.data, "gap in throws rejected")
partial = hdr + "\r\n" + f"1,1,P1,{hname3},1,2,3,,,,,,,\r\n"
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(partial.encode()), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"Imported 3 throws" in r.data, "partial set (trailing blanks) accepted")
# completed matches refuse imports
c.post(f"/match/{imid}/import",
       data={"csv": (io.BytesIO(csv_named(sample, hname3, aname3)), "s.csv")},
       content_type="multipart/form-data")
post_json(f"/api/match/{imid}/complete")
r = c.post(f"/match/{imid}/import",
           data={"csv": (io.BytesIO(csv_named(sample, hname3, aname3)), "s.csv")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"reopen it before importing" in r.data, "completed match blocks import")
c.post(f"/season/{sid3}/delete")

# --- branding ---
import io
# viewer & scorekeeper can't touch branding
c.post("/logout"); c.post("/login", data={"role": "viewer"})
r = c.get("/branding")
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "viewer blocked from branding page")
c.post("/logout"); c.post("/login", data={"role": "scorekeeper", "password": "skpw"})
r = c.post("/branding/colors", data={"bg": "#000000"})
ok(r.status_code in (302, 303) and "/login" in r.headers["Location"],
   "scorekeeper blocked from branding")
c.post("/logout"); c.post("/login", data={"role": "admin", "password": "adminpw"})

ok(c.get("/branding").status_code == 200, "admin can open branding page")
ok(b"Branding" in c.get("/").data, "admin sees Branding tab in top bar")

# default: no override style, no logo
r = c.get(f"/season/{season_id}/stats")
ok(b"--bg: #" not in r.data, "no CSS override before customization")
ok(c.get("/branding/logo-file").status_code == 404, "no logo yet -> 404")

# save colors (invalid value ignored, valid ones applied + derived shades)
c.post("/branding/colors", data={"bg": "#101820", "gold": "#00b3e6",
                                 "ink": "not-a-color"})
r = c.get("/")
ok(b"--bg: #101820" in r.data and b"--gold: #00b3e6" in r.data,
   "custom colors injected as CSS variables")
ok(b"--panel-2:" in r.data and b"--ink-dim:" in r.data,
   "derived shades injected")
ok(b"not-a-color" not in r.data, "invalid color value ignored")

# logo upload: bad type rejected, png accepted, served, cache-busted, removed
r = c.post("/branding/logo", data={"logo": (io.BytesIO(b"MZ..."), "virus.exe")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"isn&#39;t supported" in r.data or b"isn't supported" in r.data,
   "non-image logo rejected")
png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
r = c.post("/branding/logo", data={"logo": (io.BytesIO(png), "venue.png")},
           content_type="multipart/form-data", follow_redirects=True)
ok(b"Logo uploaded" in r.data, "logo upload accepted")
r = c.get("/branding/logo-file")
ok(r.status_code == 200 and r.data.startswith(b"\x89PNG"), "logo served")
ok(b"/branding/logo-file?v=" in c.get("/").data, "logo shown in top bar")
c.post("/branding/logo/remove")
ok(c.get("/branding/logo-file").status_code == 404, "logo removed")

# reset colors
c.post("/branding/colors/reset")
ok(b"--bg: #101820" not in c.get("/").data, "color reset restores default theme")

# venue name
c.post("/branding/name", data={"name": "Axe & Ale House"})
r = c.get("/")
ok(b"AXE &amp; ALE HOUSE" in r.data, "venue name shown in top bar")
ok(b"<title>Axe &amp; Ale House</title>" in c.get("/").data,
   "venue name used in page title")
c.post("/branding/name", data={"name": ""})
ok(b"ABILENE <em>AXE</em> LEAGUE" in c.get("/").data,
   "empty name restores default branding")

# preset themes
c.post("/branding/preset", data={"preset": "rwb"})
r = c.get("/")
ok(b"--gold: #d94a4a" in r.data and b"--bg: #141a26" in r.data,
   "Red White & Blue preset applied")
c.post("/branding/preset", data={"preset": "classic"})
ok(b"--gold: #d94a4a" not in c.get("/").data,
   "classic preset restores default theme")
ok(c.post("/branding/preset", data={"preset": "bogus"}).status_code == 400,
   "unknown preset rejected")

# stats sorting assets
r = c.get(f"/season/{season_id}/stats")
ok(b"sort.js" in r.data, "stats page loads the table sorter")

# delete season
c.post(f"/season/{sid2}/delete")
ok(q("SELECT COUNT(*) n FROM seasons WHERE id=?", sid2)[0]["n"] == 0,
   "season deleted")
ok(q("SELECT COUNT(*) n FROM matches WHERE season_id=?", sid2)[0]["n"] == 0,
   "season delete cascaded to matches")

print(f"\nALL {PASS} CHECKS PASSED")
