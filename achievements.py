"""Achievements: automatic detection, storage, and recomputation.

Every achievement is earned once per player/team per season — the first
time the criteria are met (detection walks the season in chronological order,
so "first" is the earliest qualifying moment). Detection is a full,
deterministic recompute per season, run after any scoring mutation. Each occurrence has a stable `uniq` identity, so recomputing is
idempotent: new facts are inserted, facts that no longer hold (a corrected
throw, a reset match) are revoked, and rows that still hold keep their
original earned_at.

Terminology: the spec's "round" is this app's *game* (3 sets). Season-
cumulative achievements (milestones, By the Numbers, Over the Hill, and the
"earlier in the season" comparisons) consider the regular season only;
per-set / per-game / per-match achievements also count in the playoffs.
"""
import sqlite3

import db as dbmod

MILESTONES = [(250, "warming_up"), (500, "splitting_wood"), (1000, "timber")]

DEFS = {
    # ---- personal ----
    "club_50":        ("Club 50", "player", "🪓", "50+ in a set"),
    "club_60":        ("Club 60", "player", "🏅", "60+ in a set"),
    "perfection":     ("Perfection", "player", "💎", "A perfect 64 set"),
    "halfway_there":  ("Halfway There", "player", "🎯", "Half the set's throws were bullseyes"),
    "hard_way_50":    ("50 The Hard Way", "player", "🧱", "50+ with no more than one bullseye"),
    "nailed_it":      ("Nailed It", "player", "📌", "2+ killshots in one set"),
    "by_the_numbers": ("By the Numbers", "player", "🔢", "Scored 1, 2, 3, 4, 5, and a bullseye this season"),
    "great_recovery": ("Great Recovery", "player", "🩹", "Bullseye right after a drop"),
    "phenomenal_recovery": ("Phenomenal Recovery", "player", "⚡", "Killshot right after a drop"),
    "turning_it_around": ("Turning it Around", "player", "🔄", "3 killshots in a set that had a drop"),
    "on_fire":        ("On Fire", "player", "🔥", "5 bullseyes in a row"),
    "first_blood":    ("First Blood", "player", "🗡️", "Killshot on the first throw of a set"),
    "the_closer":     ("The Closer", "player", "🧊", "Clinched the match from 5+ down at the start of a set"),
    "redemption_arc": ("Redemption Arc", "player", "🐦‍🔥", "Beat a thrower who beat you earlier this season"),
    "warming_up":     ("Warming Up", "player", "🌡️", "250 season points"),
    "splitting_wood": ("Splitting Wood", "player", "🪵", "500 season points"),
    "timber":         ("TIMBER!", "player", "🌲", "1000 season points"),
    # ---- team ----
    "suck_less":      ("We Suck Less", "team", "😅", "Won a match in sudden death"),
    "suck_more":      ("We Suck More", "team", "😬", "Lost a match in sudden death"),
    "powers_combined": ("By Our Powers Combined", "team", "🤝", "160+ combined in one game"),
    "comeback":       ("Comeback", "team", "↩️", "Won the match after losing game 1"),
    "over_the_hill":  ("Over the Hill", "team", "⛰️", "Winning record mathematically locked in"),
    "nail_biter":     ("Nail Biter", "team", "😰", "Won a match by exactly 1 point"),
    "mercy_please":   ("Mercy Please", "team", "🥵", "Won a match by 50+ points"),
    "perfect_storm":  ("Perfect Storm", "team", "🌪️", "Every set 55+ in the same game"),
    "giant_toppler":  ("Giant Toppler", "team", "🗿", "Beat a team with a better record"),
    "how_did_that_happen": ("How did that happen?", "team", "🤷", "Won a game without a single bullseye"),
    "try_try_again":  ("If at first you don't succeed", "team", "🔁", "Beat a team that beat you earlier this season"),
}


def ensure_schema():
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS achievements (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               season_id INTEGER NOT NULL
                   REFERENCES seasons(id) ON DELETE CASCADE,
               key TEXT NOT NULL,
               scope TEXT NOT NULL,
               player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
               team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
               match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
               game_number INTEGER,
               set_number INTEGER,
               detail TEXT,
               uniq TEXT NOT NULL,
               earned_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
               UNIQUE (season_id, key, uniq)
           )""")
    conn.commit()
    conn.close()


# --------------------------------------------------------------- data model

def _load(db, season_id):
    """Season snapshot: ordered matches with games/sets/side data."""
    teams = {r["id"]: r["name"] for r in db.execute(
        "SELECT id, name FROM teams WHERE season_id=?", (season_id,))}
    matches = [dict(m) for m in db.execute(
        """SELECT * FROM matches WHERE season_id=?
           ORDER BY CASE stage WHEN 'regular' THEN 0 ELSE 1 END,
                    COALESCE(week, 9999), id""", (season_id,))]
    rows = db.execute(
        """SELECT m.id AS mid, g.game_number AS gn, s.set_number AS sn,
                  s.id AS set_id, s.home_player_id, s.away_player_id,
                  t.player_id, t.throw_number, t.outcome, t.points
           FROM matches m
           JOIN games g ON g.match_id = m.id
           JOIN sets s ON s.game_id = g.id
           LEFT JOIN throws t ON t.set_id = s.id
           WHERE m.season_id=?
           ORDER BY m.id, g.game_number, s.set_number,
                    t.player_id, t.throw_number""", (season_id,))
    by_match = {}
    for r in rows:
        gm = by_match.setdefault(r["mid"], {})
        st = gm.setdefault(r["gn"], {}).setdefault(r["sn"], {
            "set_id": r["set_id"],
            "home": {"pid": r["home_player_id"], "seq": [], "total": 0},
            "away": {"pid": r["away_player_id"], "seq": [], "total": 0},
        })
        if r["player_id"] is None:
            continue
        if r["player_id"] == r["home_player_id"]:
            side = st["home"]
        elif r["player_id"] == r["away_player_id"]:
            side = st["away"]
        else:
            continue  # orphaned throw; recompute after reassignment fixes it
        side["seq"].append(r["outcome"])
        side["total"] += r["points"]
    for m in matches:
        m["games"] = by_match.get(m["id"], {})
    return teams, matches


def _side_stats(side):
    seq = side["seq"]
    return {
        "bulls": seq.count("B"),
        "kh": seq.count("KH"),
        "drops": seq.count("D") + seq.count("KD"),
        "n": len(seq),
        "total": side["total"],
    }


# --------------------------------------------------------------- detection

def _detect(db, season_id):
    teams, matches = _load(db, season_id)
    facts = []
    awarded = set()  # (key, subject) — every achievement is once per season

    def add(key, uniq, player_id=None, team_id=None, match_id=None,
            gn=None, sn=None, detail=None):
        subject = ("p", player_id) if player_id else ("t", team_id)
        if (key, subject) in awarded:
            return
        awarded.add((key, subject))
        facts.append({"key": key, "uniq": uniq, "player_id": player_id,
                      "team_id": team_id, "match_id": match_id,
                      "game_number": gn, "set_number": sn, "detail": detail})

    # ---------- per set / per game / per match (all stages) ----------
    for m in matches:
        opp = {"home": m["away_team_id"], "away": m["home_team_id"]}
        own = {"home": m["home_team_id"], "away": m["away_team_id"]}
        game_wins = {"home": 0, "away": 0}
        match_pts = {"home": 0, "away": 0}
        clinch_gn = None
        for gn in sorted(m["games"]):
            g = m["games"][gn]
            gtot = {"home": 0, "away": 0}
            gbulls = {"home": 0, "away": 0}
            side_totals = {"home": [], "away": []}
            for sn in sorted(g):
                st = g[sn]
                for side in ("home", "away"):
                    sd = st[side]
                    if sd["pid"] is None or not sd["seq"]:
                        gtot[side] += sd["total"]
                        continue
                    x = _side_stats(sd)
                    ref = dict(match_id=m["id"], gn=gn, sn=sn,
                               player_id=sd["pid"])
                    vs = f"vs {teams.get(opp[side], '?')}"
                    u = f"s{st['set_id']}"
                    if x["total"] >= 50:
                        add("club_50", u, detail=f"{x['total']} {vs}", **ref)
                    if x["total"] >= 60:
                        add("club_60", u, detail=f"{x['total']} {vs}", **ref)
                    if x["total"] == 64:
                        add("perfection", u, detail=f"64 {vs}", **ref)
                    if x["n"] == 10 and x["bulls"] * 2 >= x["n"]:
                        add("halfway_there", u,
                            detail=f"{x['bulls']} bullseyes {vs}", **ref)
                    if x["total"] >= 50 and x["bulls"] <= 1:
                        add("hard_way_50", u,
                            detail=f"{x['total']} with {x['bulls']} bullseye(s) {vs}",
                            **ref)
                    if x["kh"] >= 2:
                        add("nailed_it", u,
                            detail=f"{x['kh']} killshots {vs}", **ref)
                    if x["kh"] >= 3 and x["drops"] >= 1:
                        add("turning_it_around", u, detail=vs, **ref)
                    if sd["seq"][0] == "KH":
                        add("first_blood", u, detail=vs, **ref)
                    streak = 0
                    fire = False
                    for i, o in enumerate(sd["seq"]):
                        streak = streak + 1 if o == "B" else 0
                        if streak == 5:
                            fire = True
                        if i and sd["seq"][i - 1] in ("D", "KD"):
                            if o == "B":
                                add("great_recovery", u + f".{i}",
                                    detail=vs, **ref)
                            elif o == "KH":
                                add("phenomenal_recovery", u + f".{i}",
                                    detail=vs, **ref)
                    if fire:
                        add("on_fire", u, detail=vs, **ref)
                    gtot[side] += x["total"]
                    gbulls[side] += x["bulls"]
                    side_totals[side].append(x["total"])
            gwin = ("home" if gtot["home"] > gtot["away"]
                    else "away" if gtot["away"] > gtot["home"] else None)
            for side in ("home", "away"):
                if gtot[side] >= 160:
                    add("powers_combined", f"g{m['id']}.{gn}.{side}",
                        team_id=own[side], match_id=m["id"], gn=gn,
                        detail=f"{gtot[side]} combined vs {teams.get(opp[side], '?')}")
                if (len(side_totals[side]) == 3
                        and all(t >= 55 for t in side_totals[side])):
                    add("perfect_storm", f"g{m['id']}.{gn}.{side}",
                        team_id=own[side], match_id=m["id"], gn=gn,
                        detail="all three sets 55+ vs "
                               + str(teams.get(opp[side], "?")))
            if gwin:
                game_wins[gwin] += 1
                if gbulls[gwin] == 0 and gtot[gwin] > 0:
                    add("how_did_that_happen", f"g{m['id']}.{gn}",
                        team_id=own[gwin], match_id=m["id"], gn=gn,
                        detail=f"won game {gn} bullseye-free vs "
                               + str(teams.get(opp[gwin], "?")))
                if (m["completed"] and m["winner_team_id"] == own[gwin]
                        and not m["sudden_death_winner_team_id"]
                        and game_wins[gwin] == 2 and clinch_gn is None):
                    clinch_gn = gn
            match_pts["home"] += gtot["home"]
            match_pts["away"] += gtot["away"]
        m["_game_wins"] = game_wins
        m["_pts"] = match_pts

        if not m["completed"] or not m["winner_team_id"]:
            continue
        w_side = "home" if m["winner_team_id"] == m["home_team_id"] else "away"
        l_side = "away" if w_side == "home" else "home"
        w_id, l_id = own[w_side], own[l_side]
        w_name, l_name = teams.get(w_id, "?"), teams.get(l_id, "?")
        if m["sudden_death_winner_team_id"]:
            add("suck_less", f"m{m['id']}", team_id=w_id, match_id=m["id"],
                detail=f"sudden death vs {l_name}")
            add("suck_more", f"m{m['id']}", team_id=l_id, match_id=m["id"],
                detail=f"sudden death vs {w_name}")
        margin = match_pts[w_side] - match_pts[l_side]
        if margin == 1:
            add("nail_biter", f"m{m['id']}", team_id=w_id, match_id=m["id"],
                detail=f"{match_pts[w_side]}–{match_pts[l_side]} vs {l_name}")
        if margin >= 50:
            add("mercy_please", f"m{m['id']}", team_id=w_id, match_id=m["id"],
                detail=f"by {margin} vs {l_name}")
        g1 = m["games"].get(1)
        if g1:
            g1h = sum(s["home"]["total"] for s in g1.values())
            g1a = sum(s["away"]["total"] for s in g1.values())
            g1win = ("home" if g1h > g1a else "away" if g1a > g1h else None)
            if g1win and g1win != w_side:
                add("comeback", f"m{m['id']}", team_id=w_id, match_id=m["id"],
                    detail=f"dropped game 1, beat {l_name}")
        # The Closer: sets in the clinching game that began 5+ down
        if clinch_gn is not None:
            g = m["games"][clinch_gn]
            cum = {"home": 0, "away": 0}
            for sn in sorted(g):
                st = g[sn]
                opp_side = "away" if w_side == "home" else "home"
                deficit = cum[opp_side] - cum[w_side]
                pid = st[w_side]["pid"]
                if deficit >= 5 and pid:
                    add("the_closer", f"s{st['set_id']}", player_id=pid,
                        match_id=m["id"], gn=clinch_gn, sn=sn,
                        detail=f"clinched from {deficit} down vs {l_name}")
                cum["home"] += st["home"]["total"]
                cum["away"] += st["away"]["total"]

    # ---------- season-cumulative (regular season only) ----------
    reg = [m for m in matches if m["stage"] == "regular"]

    # By the Numbers + milestones, in chronological set order
    seen_vals, cum_pts, milestones_hit = {}, {}, {}
    NUMBERS = {"1", "2", "3", "4", "5", "B"}
    for m in reg:
        for gn in sorted(m["games"]):
            for sn in sorted(m["games"][gn]):
                st = m["games"][gn][sn]
                for side in ("home", "away"):
                    sd = st[side]
                    pid = sd["pid"]
                    if not pid or not sd["seq"]:
                        continue
                    sv = seen_vals.setdefault(pid, set())
                    if not NUMBERS <= sv:
                        sv.update(o for o in sd["seq"] if o in NUMBERS)
                        if NUMBERS <= sv:
                            add("by_the_numbers", f"p{pid}", player_id=pid,
                                match_id=m["id"], gn=gn, sn=sn,
                                detail="all of 1–5 plus a bullseye")
                    before = cum_pts.get(pid, 0)
                    after = before + sd["total"]
                    cum_pts[pid] = after
                    for thresh, key in MILESTONES:
                        hit = milestones_hit.setdefault(pid, set())
                        if key not in hit and before < thresh <= after:
                            hit.add(key)
                            add(key, f"p{pid}", player_id=pid,
                                match_id=m["id"], gn=gn, sn=sn,
                                detail=f"{thresh} season points")

    # Redemption Arc: head-to-head set results in chronological order
    beaten_by = {}
    for m in reg:
        opp = {"home": m["away_team_id"], "away": m["home_team_id"]}
        for gn in sorted(m["games"]):
            for sn in sorted(m["games"][gn]):
                st = m["games"][gn][sn]
                h, a = st["home"], st["away"]
                if not h["pid"] or not a["pid"] or h["total"] == a["total"]:
                    continue
                if not h["seq"] and not a["seq"]:
                    continue
                win, lose = (h, a) if h["total"] > a["total"] else (a, h)
                if lose["pid"] in beaten_by.get(win["pid"], set()):
                    add("redemption_arc", f"s{st['set_id']}",
                        player_id=win["pid"], match_id=m["id"], gn=gn, sn=sn,
                        detail=f"{win['total']}–{lose['total']} rematch win")
                beaten_by.setdefault(lose["pid"], set()).add(win["pid"])

    # Team records in order: Giant Toppler, If at first…, Over the Hill
    sched_count = {}
    for m in reg:
        for tid in (m["home_team_id"], m["away_team_id"]):
            sched_count[tid] = sched_count.get(tid, 0) + 1
    rec = {tid: [0, 0] for tid in teams}
    lost_to = {}
    hill_done = set()
    for m in matches:
        if not m["completed"] or not m["winner_team_id"]:
            continue
        w = m["winner_team_id"]
        l = (m["away_team_id"] if w == m["home_team_id"]
             else m["home_team_id"])
        if l is None:
            continue
        wd = rec.get(w, [0, 0])
        ld = rec.get(l, [0, 0])
        if (ld[0] - ld[1]) > (wd[0] - wd[1]):
            add("giant_toppler", f"m{m['id']}", team_id=w, match_id=m["id"],
                detail=f"took down {teams.get(l, '?')} "
                       f"({ld[0]}–{ld[1]}) at {wd[0]}–{wd[1]}")
        if l in lost_to.get(w, set()):
            add("try_try_again", f"m{m['id']}", team_id=w, match_id=m["id"],
                detail=f"rematch win over {teams.get(l, '?')}")
        lost_to.setdefault(l, set()).add(w)
        if m["stage"] == "regular":
            rec[w][0] += 1
            rec[l][1] += 1
            n = sched_count.get(w, 0)
            if w not in hill_done and n and 2 * rec[w][0] > n:
                hill_done.add(w)
                add("over_the_hill", f"t{w}", team_id=w, match_id=m["id"],
                    detail=f"{rec[w][0]} wins of {n} — winning season locked")
    return facts


# --------------------------------------------------------------- recompute

def recompute(db, season_id):
    facts = _detect(db, season_id)
    want = {(f["key"], f["uniq"]): f for f in facts}
    have = {}
    for r in db.execute("SELECT id, key, uniq FROM achievements"
                        " WHERE season_id=?", (season_id,)).fetchall():
        have[(r["key"], r["uniq"])] = r["id"]
    stale = [have[k] for k in have if k not in want]
    if stale:
        q = ",".join("?" * len(stale))
        db.execute(f"DELETE FROM achievements WHERE id IN ({q})", stale)
    for k, f in want.items():
        if k in have:
            continue
        db.execute(
            """INSERT INTO achievements (season_id, key, scope, player_id,
                 team_id, match_id, game_number, set_number, detail, uniq)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (season_id, f["key"], DEFS[f["key"]][1], f["player_id"],
             f["team_id"], f["match_id"], f["game_number"], f["set_number"],
             f["detail"], f["uniq"]))
    return len(want)


def backfill(db):
    """Recompute every season — run once at startup so existing data earns
    its achievements retroactively."""
    for s in db.execute("SELECT id FROM seasons").fetchall():
        recompute(db, s["id"])


def list_achievements(db, season_id):
    rows = db.execute(
        """SELECT a.*, p.name AS player_name, pt.name AS player_team,
                  t.name AS team_name,
                  th.name AS m_home, ta.name AS m_away, m.week AS m_week,
                  m.stage AS m_stage
           FROM achievements a
           LEFT JOIN players p ON p.id = a.player_id
           LEFT JOIN teams pt ON pt.id = p.team_id
           LEFT JOIN teams t ON t.id = a.team_id
           LEFT JOIN matches m ON m.id = a.match_id
           LEFT JOIN teams th ON th.id = m.home_team_id
           LEFT JOIN teams ta ON ta.id = m.away_team_id
           WHERE a.season_id=?
           ORDER BY a.earned_at DESC, a.id DESC""", (season_id,)).fetchall()
    out = []
    for r in rows:
        name, scope, icon, desc = DEFS.get(
            r["key"], (r["key"], r["scope"], "🏆", ""))
        ctx = []
        if r["m_home"] and r["m_away"]:
            ctx.append(f"{r['m_home']} vs {r['m_away']}")
        if r["m_stage"] == "playoff":
            ctx.append("Playoffs")
        elif r["m_week"]:
            ctx.append(f"Rd {r['m_week']}")
        if r["game_number"]:
            g = f"Game {r['game_number']}"
            if r["set_number"]:
                g += f", Set {r['set_number']}"
            ctx.append(g)
        out.append({
            "key": r["key"], "name": name, "scope": scope, "icon": icon,
            "desc": desc, "detail": r["detail"], "earned_at": r["earned_at"],
            "who": r["player_name"] or r["team_name"] or "?",
            "who_team": r["player_team"] if r["player_name"] else None,
            "context": " · ".join(ctx),
        })
    return out
