"""Season statistics, weekly averages, and standings."""
from collections import defaultdict


def _player_set_rows(db, season_id, stage=None, include_guests=False):
    """One row per (player, set) with totals and counts.

    stage=None -> all matches; 'regular' or 'playoff' filters by match stage.
    """
    extra = " AND m.stage = ?" if stage else ""
    args = [season_id] + ([stage] if stage else [])
    if not include_guests:
        extra += " AND pl.is_guest = 0"
    return db.execute(
        """
        SELECT t.player_id,
               s.id  AS set_id,
               g.id  AS game_id,
               m.id  AS match_id,
               m.week AS week,
               m.stage AS stage,
               SUM(t.points) AS total,
               COUNT(*) AS n_throws,
               SUM(t.outcome = 'B')  AS bulls,
               SUM(t.outcome IN ('D','KD')) AS drops,
               SUM(t.outcome IN ('KH','KD','KM')) AS ks_att,
               SUM(t.outcome = 'KH') AS ks_hit
        FROM throws t
        JOIN players pl ON pl.id = t.player_id
        JOIN sets s    ON s.id = t.set_id
        JOIN games g   ON g.id = s.game_id
        JOIN matches m ON m.id = g.match_id
        WHERE m.season_id = ?""" + extra + """
        GROUP BY t.player_id, s.id
        """,
        args,
    ).fetchall()


def _players(db, season_id):
    return db.execute(
        """SELECT p.id, p.name, p.team_id, tm.name AS team_name
           FROM players p JOIN teams tm ON tm.id = p.team_id
           WHERE tm.season_id = ? AND p.is_guest = 0
           ORDER BY tm.name, p.name""",
        (season_id,),
    ).fetchall()


def player_season_stats(db, season_id, stage=None):
    rows = _player_set_rows(db, season_id, stage)
    by_player = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(r)

    out = []
    for p in _players(db, season_id):
        sets = by_player.get(p["id"], [])
        if not sets:
            out.append({
                "player_id": p["id"], "name": p["name"], "team": p["team_name"],
                "games": 0, "sets": 0, "avg": None, "high": None, "low": None,
                "fifty_pct": None, "bulls": 0, "bull_pct": None,
                "drops": 0, "drop_pct": None,
                "ks_att": 0, "kill_pct": None,
            })
            continue
        totals = [r["total"] for r in sets]
        n_throws = sum(r["n_throws"] for r in sets)
        bulls = sum(r["bulls"] for r in sets)
        drops = sum(r["drops"] for r in sets)
        ks_att = sum(r["ks_att"] for r in sets)
        ks_hit = sum(r["ks_hit"] for r in sets)
        non_ks = n_throws - ks_att  # bullseyes are impossible on KS attempts
        out.append({
            "player_id": p["id"], "name": p["name"], "team": p["team_name"],
            "games": len({r["game_id"] for r in sets}),
            "sets": len(sets),
            "avg": sum(totals) / len(totals),
            "high": max(totals),
            "low": min(totals),
            "fifty_pct": 100.0 * sum(1 for t in totals if t >= 50) / len(totals),
            "bulls": bulls,
            "bull_pct": (100.0 * bulls / non_ks) if non_ks else None,
            "drops": drops,
            "drop_pct": (100.0 * drops / n_throws) if n_throws else None,
            "ks_att": ks_att,
            "kill_pct": (100.0 * ks_hit / ks_att) if ks_att else None,
        })
    out.sort(key=lambda r: (-(r["avg"] or -1), r["name"]))
    return out


def round_dates(db, season_id):
    """{round: 'yyyy-mm-dd'} for rounds the admin has dated."""
    return {r["round"]: r["date"] for r in db.execute(
        "SELECT round, date FROM round_dates WHERE season_id=?",
        (season_id,)).fetchall()}


def _date_label(iso):
    from datetime import datetime
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d, %Y")
    except ValueError:
        return iso


def player_weekly_averages(db, season_id):
    """Weekly per-set averages. Rounds that share an admin-set date are
    combined into one week column labeled with that date; undated rounds get
    their own 'Rd N' column. Returns (columns, rows) where columns is a list
    of {key, label} and rows = [{name, team, weeks: {key: avg}}].
    Regular season only — playoff matches have no round number."""
    rows = _player_set_rows(db, season_id, stage='regular')
    dates = round_dates(db, season_id)

    def col_key(week):
        return ("d", dates[week]) if week in dates else ("r", week)

    # column order follows round order (first round in each group decides)
    first_round = {}
    for w in sorted({r["week"] for r in rows if r["week"] is not None}):
        first_round.setdefault(col_key(w), w)
    columns = [
        {"key": k, "label": _date_label(k[1]) if k[0] == "d" else f"Rd {k[1]}"}
        for k, _ in sorted(first_round.items(), key=lambda kv: kv[1])
    ]

    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["week"] is not None:
            agg[r["player_id"]][col_key(r["week"])].append(r["total"])
    out = []
    for p in _players(db, season_id):
        wk = {k: (sum(v) / len(v)) for k, v in agg.get(p["id"], {}).items()}
        if wk:
            out.append({"name": p["name"], "team": p["team_name"], "weeks": wk})
    return columns, out


def team_season_stats(db, season_id, stage=None):
    rows = _player_set_rows(db, season_id, stage, include_guests=True)
    players = {p["id"]: p for p in db.execute(
        """SELECT p.id, p.team_id FROM players p
           JOIN teams tm ON tm.id = p.team_id WHERE tm.season_id = ?""",
        (season_id,)).fetchall()}
    teams = db.execute(
        "SELECT * FROM teams WHERE season_id=? ORDER BY name", (season_id,)
    ).fetchall()

    by_team = defaultdict(list)
    for r in rows:
        p = players.get(r["player_id"])
        if p:
            by_team[p["team_id"]].append(r)

    match_stage = stage or 'regular'
    match_rows = db.execute(
        """SELECT * FROM matches WHERE season_id=? AND stage=?
           AND completed=1""", (season_id, match_stage)).fetchall()
    wins = defaultdict(int)
    played = defaultdict(int)
    for m in match_rows:
        played[m["home_team_id"]] += 1
        played[m["away_team_id"]] += 1
        if m["winner_team_id"]:
            wins[m["winner_team_id"]] += 1

    out = []
    for t in teams:
        sets = by_team.get(t["id"], [])
        totals = [r["total"] for r in sets]
        n_throws = sum(r["n_throws"] for r in sets)
        bulls = sum(r["bulls"] for r in sets)
        non_ks = n_throws - sum(r["ks_att"] for r in sets)
        out.append({
            "team_id": t["id"], "name": t["name"],
            "avg": (sum(totals) / len(totals)) if totals else None,
            "high": max(totals) if totals else None,
            "fifty_count": sum(1 for x in totals if x >= 50),
            "bull_pct": (100.0 * bulls / non_ks) if non_ks else None,
            "match_wins": wins.get(t["id"], 0),
            "matches_played": played.get(t["id"], 0),
        })
    out.sort(key=lambda r: (-(r["avg"] or -1), r["name"]))
    return out


def standings(db, season_id):
    """Regular-season standings: W-L record, tiebreak total bullseyes."""
    teams = db.execute(
        "SELECT * FROM teams WHERE season_id=? ORDER BY name", (season_id,)
    ).fetchall()
    matches = db.execute(
        """SELECT * FROM matches WHERE season_id=? AND stage='regular'
           AND completed=1""", (season_id,)).fetchall()
    rec = {t["id"]: {"team_id": t["id"], "name": t["name"], "wins": 0,
                     "losses": 0, "played": 0, "bulls": 0} for t in teams}
    for m in matches:
        for tid in (m["home_team_id"], m["away_team_id"]):
            if tid in rec:
                rec[tid]["played"] += 1
        w = m["winner_team_id"]
        if w in rec:
            rec[w]["wins"] += 1
            other = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
            if other in rec:
                rec[other]["losses"] += 1

    bull_rows = db.execute(
        """SELECT p.team_id, COUNT(*) AS bulls
           FROM throws t
           JOIN players p ON p.id = t.player_id
           JOIN sets s ON s.id = t.set_id
           JOIN games g ON g.id = s.game_id
           JOIN matches m ON m.id = g.match_id
           WHERE m.season_id=? AND t.outcome='B'
           GROUP BY p.team_id""", (season_id,)).fetchall()
    for r in bull_rows:
        if r["team_id"] in rec:
            rec[r["team_id"]]["bulls"] = r["bulls"]

    rows = list(rec.values())
    rows.sort(key=lambda r: (-r["wins"], r["losses"], -r["bulls"], r["name"]))
    return rows


def league_overview(db, season_id):
    """Season-to-date league summary — regular season only.

    Bullseye percentages exclude killshot attempts from the denominator,
    since a killshot attempt can never score a bullseye. Percentage-based
    leaders require a minimum body of work (MIN_NON_KS non-killshot throws,
    MIN_KS_ATT killshot attempts) so a hot first set doesn't top the board;
    if nobody qualifies yet, everyone is considered.
    """
    MIN_NON_KS = 30
    MIN_KS_ATT = 5

    rows = _player_set_rows(db, season_id, stage="regular")
    if not rows:
        return None
    players = {p["id"]: p for p in _players(db, season_id)}

    totals = [r["total"] for r in rows]
    n_throws = sum(r["n_throws"] for r in rows)
    bulls = sum(r["bulls"] for r in rows)
    drops = sum(r["drops"] for r in rows)
    ks_att = sum(r["ks_att"] for r in rows)
    non_ks = n_throws - ks_att

    def who(pid):
        p = players.get(pid)
        return {"name": p["name"], "team": p["team_name"]} if p else None

    def holders(pids):
        """Unique holders, name-sorted, for a list of player ids."""
        out, seen = [], set()
        for pid in pids:
            if pid in seen:
                continue
            seen.add(pid)
            w = who(pid)
            if w:
                out.append(w)
        return sorted(out, key=lambda h: h["name"].lower())

    # high set score (all players tied at the top)
    hi_total = max(r["total"] for r in rows)
    hi_holders = holders([r["player_id"] for r in rows
                          if r["total"] == hi_total])

    # per-player aggregates for the leader boards
    agg = defaultdict(lambda: {"bulls": 0, "non_ks": 0, "ks_att": 0,
                               "ks_hit": 0, "total": 0, "sets": 0})
    for r in rows:
        a = agg[r["player_id"]]
        a["bulls"] += r["bulls"]
        a["non_ks"] += r["n_throws"] - r["ks_att"]
        a["ks_att"] += r["ks_att"]
        a["ks_hit"] += r["ks_hit"]
        a["total"] += r["total"]
        a["sets"] += 1

    def leader(pool, value):
        items = [(pid, value(a)) for pid, a in pool if value(a) is not None]
        if not items:
            return None
        best = max(v for _, v in items)
        tied = [pid for pid, v in items if abs(v - best) < 1e-9]
        return {"value": best, "holders": holders(tied)}

    bull_pool = [(pid, a) for pid, a in agg.items() if a["non_ks"] >= MIN_NON_KS]
    if not bull_pool:
        bull_pool = list(agg.items())
    # average leader needs the same body of work (~3 full sets)
    avg_pool = [(pid, a) for pid, a in agg.items()
                if a["sets"] * 10 >= MIN_NON_KS + a["ks_att"]]
    if not avg_pool:
        avg_pool = list(agg.items())
    ks_pool = [(pid, a) for pid, a in agg.items() if a["ks_att"] >= MIN_KS_ATT]
    if not ks_pool:
        ks_pool = [(pid, a) for pid, a in agg.items() if a["ks_att"] > 0]

    return {
        "avg_score": sum(totals) / len(totals),
        "drop_pct": (100.0 * drops / n_throws) if n_throws else None,
        "bull_pct": (100.0 * bulls / non_ks) if non_ks else None,
        "high_score": {"value": hi_total, "holders": hi_holders},
        "best_avg": leader(
            avg_pool,
            lambda a: (a["total"] / a["sets"]) if a["sets"] else None),
        "best_bull_pct": leader(
            bull_pool,
            lambda a: (100.0 * a["bulls"] / a["non_ks"]) if a["non_ks"] else None),
        "most_bulls": leader(
            list(agg.items()), lambda a: a["bulls"] or None),
        "best_kill_pct": leader(
            ks_pool,
            lambda a: (100.0 * a["ks_hit"] / a["ks_att"]) if a["ks_att"] else None),
        "min_non_ks": MIN_NON_KS,
        "min_ks_att": MIN_KS_ATT,
    }


def weekly_high_scores(db, season_id):
    """High set score and holder per week (rounds grouped by their date,
    same grouping as the Weekly Average table). Returns a list of
    {key, label, value, name, team} in round order."""
    rows = _player_set_rows(db, season_id, stage="regular")
    dates = round_dates(db, season_id)
    players = {p["id"]: p for p in _players(db, season_id)}

    def col_key(week):
        return ("d", dates[week]) if week in dates else ("r", week)

    first_round = {}
    for w in sorted({r["week"] for r in rows if r["week"] is not None}):
        first_round.setdefault(col_key(w), w)

    best = {}
    for r in rows:
        if r["week"] is None:
            continue
        k = col_key(r["week"])
        if k not in best or r["total"] > best[k][0]:
            best[k] = (r["total"], [r["player_id"]])
        elif r["total"] == best[k][0]:
            best[k][1].append(r["player_id"])

    def holders(pids):
        out, seen = [], set()
        for pid in pids:
            if pid in seen:
                continue
            seen.add(pid)
            p = players.get(pid)
            if p:
                out.append({"name": p["name"], "team": p["team_name"]})
        return sorted(out, key=lambda h: h["name"].lower())

    out = []
    for k, _ in sorted(first_round.items(), key=lambda kv: kv[1]):
        b = best.get(k)
        if not b:
            continue
        out.append({
            "key": k,
            "label": _date_label(k[1]) if k[0] == "d" else f"Rd {k[1]}",
            "value": b[0],
            "holders": holders(b[1]),
        })
    return out


# ------------------------------------------------------------ player detail

OUTCOME_ORDER = ["B", "5", "4", "3", "2", "1", "KH", "KM", "KD", "D", "M"]
OUTCOME_LABELS = {
    "B": "Bullseye (6)", "5": "5", "4": "4", "3": "3", "2": "2", "1": "1",
    "KH": "Killshot hit (8)", "KM": "Killshot miss", "KD": "Killshot drop",
    "D": "Drop", "M": "Miss",
}


def _rank(values, mine, reverse=True):
    """1-based rank of `mine` among values (ties share the better rank)."""
    if mine is None:
        return None, len(values)
    better = sum(1 for v in values
                 if v is not None and ((v > mine) if reverse else (v < mine)))
    return better + 1, len([v for v in values if v is not None])


def player_detail(db, season_id, player_id):
    p = db.execute(
        """SELECT p.id, p.name, t.name AS team_name, t.id AS team_id
           FROM players p JOIN teams t ON t.id=p.team_id WHERE p.id=?""",
        (player_id,)).fetchone()
    if not p:
        return None

    rows = db.execute(
        """SELECT m.id AS mid, m.week, m.stage, g.game_number AS gn,
                  s.set_number AS sn, s.id AS set_id,
                  CASE WHEN s.home_player_id=:pid THEN 'home' ELSE 'away' END
                      AS side,
                  s.home_player_id, s.away_player_id,
                  m.home_team_id, m.away_team_id,
                  t.throw_number, t.outcome, t.points
           FROM sets s
           JOIN games g ON g.id=s.game_id
           JOIN matches m ON m.id=g.match_id
           JOIN throws t ON t.set_id=s.id AND t.player_id=:pid
           WHERE m.season_id=:sid
             AND :pid IN (s.home_player_id, s.away_player_id)
           ORDER BY CASE m.stage WHEN 'regular' THEN 0 ELSE 1 END,
                    COALESCE(m.week, 9999), m.id, g.game_number,
                    s.set_number, t.throw_number""",
        {"pid": player_id, "sid": season_id}).fetchall()

    sets_ = {}
    order = []
    for r in rows:
        k = r["set_id"]
        if k not in sets_:
            opp_pid = (r["away_player_id"] if r["side"] == "home"
                       else r["home_player_id"])
            opp_tid = (r["away_team_id"] if r["side"] == "home"
                       else r["home_team_id"])
            sets_[k] = {"set_id": k, "mid": r["mid"], "week": r["week"],
                        "stage": r["stage"], "gn": r["gn"], "sn": r["sn"],
                        "opp_pid": opp_pid, "opp_tid": opp_tid,
                        "seq": [], "pts": []}
            order.append(k)
        sets_[k]["seq"].append(r["outcome"])
        sets_[k]["pts"].append(r["points"])
    all_sets = [sets_[k] for k in order]
    reg_sets = [s for s in all_sets if s["stage"] == "regular"]
    if not all_sets:
        return {"player": dict(p), "empty": True}

    # opponent totals + names for the same sets
    opp_totals = {}
    for r in db.execute(
            """SELECT t.set_id, SUM(t.points) AS tot FROM throws t
               JOIN sets s ON s.id=t.set_id
               WHERE t.set_id IN ({q}) AND t.player_id != ?
                 AND t.player_id IN (s.home_player_id, s.away_player_id)
               GROUP BY t.set_id""".format(
                   q=",".join("?" * len(order))),
            (*order, player_id)).fetchall():
        opp_totals[r["set_id"]] = r["tot"]
    names = {r["id"]: r["name"] for r in db.execute(
        "SELECT p.id, p.name FROM players p JOIN teams t ON t.id=p.team_id"
        " WHERE t.season_id=?", (season_id,)).fetchall()}
    team_names = {r["id"]: r["name"] for r in db.execute(
        "SELECT id, name FROM teams WHERE season_id=?", (season_id,))}

    def agg(subset):
        totals = [sum(s["pts"]) for s in subset]
        seq_all = [o for s in subset for o in s["seq"]]
        n = len(seq_all)
        ks_att = sum(1 for o in seq_all if o in ("KH", "KM", "KD"))
        non_ks = n - ks_att
        bulls = seq_all.count("B")
        drops = seq_all.count("D") + seq_all.count("KD")
        mean = sum(totals) / len(totals) if totals else None
        var = (sum((t - mean) ** 2 for t in totals) / len(totals)
               if totals and len(totals) > 1 else 0)
        return {
            "sets": len(subset), "points": sum(totals), "throws": n,
            "avg": mean, "high": max(totals) if totals else None,
            "low": min(totals) if totals else None,
            "fifty": sum(1 for t in totals if t >= 50),
            "fifty_pct": (100 * sum(1 for t in totals if t >= 50)
                          / len(totals)) if totals else None,
            "bulls": bulls,
            "bull_pct": (100 * bulls / non_ks) if non_ks else None,
            "drops": drops,
            "drop_pct": (100 * drops / n) if n else None,
            "ks_att": ks_att, "ks_hit": seq_all.count("KH"),
            "kill_pct": (100 * seq_all.count("KH") / ks_att)
                        if ks_att else None,
            "ppt": (sum(totals) / n) if n else None,
            "stdev": var ** 0.5 if totals else None,
        }

    reg = agg(reg_sets)
    po = agg([s for s in all_sets if s["stage"] == "playoff"]) \
        if any(s["stage"] == "playoff" for s in all_sets) else None

    # throw mix (regular season)
    seq_all = [o for s in reg_sets for o in s["seq"]]
    mix = [{"outcome": o, "label": OUTCOME_LABELS[o],
            "count": seq_all.count(o),
            "pct": (100 * seq_all.count(o) / len(seq_all)) if seq_all else 0}
           for o in OUTCOME_ORDER]

    # lane split: throws 1-5 vs 6-10
    def half(sl):
        pts = [p_ for s in reg_sets for p_ in s["pts"][sl]]
        seq = [o for s in reg_sets for o in s["seq"][sl]]
        ks = sum(1 for o in seq if o in ("KH", "KM", "KD"))
        return {"throws": len(seq),
                "ppt": (sum(pts) / len(pts)) if pts else None,
                "bull_pct": (100 * seq.count("B") / (len(seq) - ks))
                            if (len(seq) - ks) else None}
    lane = {"first": half(slice(0, 5)), "second": half(slice(5, 10))}

    # longest bullseye streak (within a set)
    best_streak = 0
    for s in reg_sets:
        run = 0
        for o in s["seq"]:
            run = run + 1 if o == "B" else 0
            best_streak = max(best_streak, run)

    # form: last 5 regular sets vs season average
    last5 = [sum(s["pts"]) for s in reg_sets[-5:]]
    form = {"n": len(last5),
            "avg": (sum(last5) / len(last5)) if last5 else None}

    # head-to-head vs opposing throwers (regular season)
    h2h = {}
    for s in reg_sets:
        me, them = sum(s["pts"]), opp_totals.get(s["set_id"])
        if s["opp_pid"] is None or them is None:
            continue
        d = h2h.setdefault(s["opp_pid"], {"w": 0, "l": 0, "t": 0,
                                          "for": 0, "against": 0, "n": 0})
        d["n"] += 1
        d["for"] += me
        d["against"] += them
        if me > them:
            d["w"] += 1
        elif me < them:
            d["l"] += 1
        else:
            d["t"] += 1
    h2h_rows = sorted(
        ({"name": names.get(pid, "?"), **d,
          "avg_for": d["for"] / d["n"], "avg_against": d["against"] / d["n"]}
         for pid, d in h2h.items()),
        key=lambda r: (-r["n"], r["name"].lower()))

    # best / worst sets with context
    def setline(s):
        return {"total": sum(s["pts"]), "week": s["week"], "stage": s["stage"],
                "gn": s["gn"], "sn": s["sn"], "mid": s["mid"],
                "opp": names.get(s["opp_pid"]),
                "opp_team": team_names.get(s["opp_tid"], "?")}
    best = setline(max(reg_sets, key=lambda s: sum(s["pts"]))) if reg_sets else None
    worst = setline(min(reg_sets, key=lambda s: sum(s["pts"]))) if reg_sets else None

    # league comparisons + ranks (regular season, players with data)
    league = [q for q in player_season_stats(db, season_id, stage="regular")
              if q["sets"]]
    def lv(key):
        return [q[key] for q in league]
    def lmean(key):
        vals = [v for v in lv(key) if v is not None]
        return sum(vals) / len(vals) if vals else None
    comps = []
    for key, label, reverse in (
            ("avg", "Average / set", True),
            ("high", "High score", True),
            ("bull_pct", "Bullseye %", True),
            ("kill_pct", "Killshot %", True),
            ("drop_pct", "Drop rate", False)):
        mine = reg[key if key != "avg" else "avg"]
        rank, of = _rank(lv(key), mine, reverse)
        comps.append({"label": label, "mine": mine, "league": lmean(key),
                      "rank": rank, "of": of, "reverse": reverse,
                      "pct": key.endswith("_pct")})

    # per-round averages: player vs league
    pr_cols, pr_rows = player_weekly_averages(db, season_id)
    my_weeks = next((r["weeks"] for r in pr_rows if r["name"] == p["name"]), {})
    lg_weeks = {}
    for col in pr_cols:
        vals = [r["weeks"][col["key"]] for r in pr_rows
                if col["key"] in r["weeks"]]
        if vals:
            lg_weeks[col["key"]] = sum(vals) / len(vals)
    rounds = [{"label": col["label"],
               "mine": my_weeks.get(col["key"]),
               "league": lg_weeks.get(col["key"])} for col in pr_cols]

    achievements = db.execute(
        "SELECT key FROM achievements WHERE season_id=? AND player_id=?"
        " ORDER BY earned_at", (season_id, player_id)).fetchall()

    return {"player": dict(p), "empty": False, "reg": reg, "po": po,
            "mix": mix, "lane": lane, "best_streak": best_streak,
            "form": form, "h2h": h2h_rows, "best": best, "worst": worst,
            "comps": comps, "rounds": rounds,
            "achievement_keys": [r["key"] for r in achievements]}
