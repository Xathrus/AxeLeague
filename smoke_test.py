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

print(f"\nALL {PASS} CHECKS PASSED")
