"""Season statistics, weekly averages, and standings."""
from collections import defaultdict


def _player_set_rows(db, season_id, stage=None):
    """One row per (player, set) with totals and counts.

    stage=None -> all matches; 'regular' or 'playoff' filters by match stage.
    """
    extra = " AND m.stage = ?" if stage else ""
    args = (season_id, stage) if stage else (season_id,)
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
           WHERE tm.season_id = ? ORDER BY tm.name, p.name""",
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
    rows = _player_set_rows(db, season_id, stage)
    players = {p["id"]: p for p in _players(db, season_id)}
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

    # high set score
    hi = max(rows, key=lambda r: r["total"])

    # per-player aggregates for the leader boards
    agg = defaultdict(lambda: {"bulls": 0, "non_ks": 0, "ks_att": 0, "ks_hit": 0})
    for r in rows:
        a = agg[r["player_id"]]
        a["bulls"] += r["bulls"]
        a["non_ks"] += r["n_throws"] - r["ks_att"]
        a["ks_att"] += r["ks_att"]
        a["ks_hit"] += r["ks_hit"]

    def leader(pool, value):
        items = [(pid, value(a)) for pid, a in pool if value(a) is not None]
        if not items:
            return None
        pid, v = max(items, key=lambda kv: kv[1])
        return {"value": v, **(who(pid) or {})}

    bull_pool = [(pid, a) for pid, a in agg.items() if a["non_ks"] >= MIN_NON_KS]
    if not bull_pool:
        bull_pool = list(agg.items())
    ks_pool = [(pid, a) for pid, a in agg.items() if a["ks_att"] >= MIN_KS_ATT]
    if not ks_pool:
        ks_pool = [(pid, a) for pid, a in agg.items() if a["ks_att"] > 0]

    return {
        "avg_score": sum(totals) / len(totals),
        "drop_pct": (100.0 * drops / n_throws) if n_throws else None,
        "bull_pct": (100.0 * bulls / non_ks) if non_ks else None,
        "high_score": {"value": hi["total"], **(who(hi["player_id"]) or {})},
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
        if k not in best or r["total"] > best[k]["total"]:
            best[k] = r

    out = []
    for k, _ in sorted(first_round.items(), key=lambda kv: kv[1]):
        r = best.get(k)
        if not r:
            continue
        p = players.get(r["player_id"])
        out.append({
            "key": k,
            "label": _date_label(k[1]) if k[0] == "d" else f"Rd {k[1]}",
            "value": r["total"],
            "name": p["name"] if p else "?",
            "team": p["team_name"] if p else "?",
        })
    return out
