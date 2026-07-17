"""Scoring rules for the Abilene Axe League.

Outcome codes:
    '1'..'5'  scored hit (1-5 pts)
    'B'       bullseye (6 pts)
    'KH'      called killshot hit (8 pts)
    'KD'      called killshot drop (0 pts)
    'KM'      called killshot miss (0 pts)
    'D'       drop (0 pts)
    'M'       miss (0 pts)

Killshot call rules (per player per set):
    - 2 base calls
    - every drop (D or KD) grants +1 bonus call
"""

OUTCOME_POINTS = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "B": 6,
    "KH": 8, "KD": 0, "KM": 0,
    "D": 0, "M": 0,
}
KS_OUTCOMES = {"KH", "KD", "KM"}
DROP_OUTCOMES = {"D", "KD"}
BASE_KS_CALLS = 2
THROWS_PER_SET = 10


def ks_calls_remaining(outcomes):
    """Remaining killshot calls after the given sequence of outcomes."""
    remaining = BASE_KS_CALLS
    for o in outcomes:
        if o in KS_OUTCOMES:
            remaining -= 1
        if o in DROP_OUTCOMES:
            remaining += 1
    return remaining


def ks_sequence_valid(outcomes):
    """True if no killshot was called without an available call at that moment."""
    remaining = BASE_KS_CALLS
    for o in outcomes:
        if o in KS_OUTCOMES:
            if remaining <= 0:
                return False
            remaining -= 1
        if o in DROP_OUTCOMES:
            remaining += 1
    return True


def _throw_dict(t):
    return {
        "id": t["id"],
        "n": t["throw_number"],
        "outcome": t["outcome"],
        "points": t["points"],
    }


def _player_name(db, pid):
    if pid is None:
        return None
    row = db.execute("SELECT name FROM players WHERE id=?", (pid,)).fetchone()
    return row["name"] if row else None


def compute_match_state(db, match_id):
    """Full live state of a match: every game, set, throw, totals, and status."""
    m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if m is None:
        return None
    home_id, away_id = m["home_team_id"], m["away_team_id"]
    teams = {}
    for tid in (home_id, away_id):
        if tid:
            r = db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
            teams[tid] = r["name"] if r else "?"

    rosters = {"home": [], "away": []}
    for side, tid in (("home", home_id), ("away", away_id)):
        if tid:
            rosters[side] = [
                {"id": p["id"], "name": p["name"]}
                for p in db.execute(
                    "SELECT * FROM players WHERE team_id=? ORDER BY name", (tid,)
                ).fetchall()
            ]

    games_out = []
    home_wins = away_wins = ties = complete_games = 0

    games = db.execute(
        "SELECT * FROM games WHERE match_id=? ORDER BY game_number", (match_id,)
    ).fetchall()
    for g in games:
        sets_out = []
        g_home = g_away = 0
        g_complete = True
        sets = db.execute(
            "SELECT * FROM sets WHERE game_id=? ORDER BY set_number", (g["id"],)
        ).fetchall()
        for s in sets:
            throws = db.execute(
                "SELECT * FROM throws WHERE set_id=? ORDER BY throw_number",
                (s["id"],),
            ).fetchall()
            ht = [t for t in throws if t["player_id"] == s["home_player_id"]]
            at = [t for t in throws if t["player_id"] == s["away_player_id"]]
            h_total = sum(t["points"] for t in ht)
            a_total = sum(t["points"] for t in at)
            g_home += h_total
            g_away += a_total
            set_complete = (
                s["home_player_id"] is not None
                and s["away_player_id"] is not None
                and len(ht) == THROWS_PER_SET
                and len(at) == THROWS_PER_SET
            )
            if not set_complete:
                g_complete = False
            sets_out.append({
                "id": s["id"],
                "number": s["set_number"],
                "home_player_id": s["home_player_id"],
                "away_player_id": s["away_player_id"],
                "home_player_name": _player_name(db, s["home_player_id"]),
                "away_player_name": _player_name(db, s["away_player_id"]),
                "home_throws": [_throw_dict(t) for t in ht],
                "away_throws": [_throw_dict(t) for t in at],
                "home_total": h_total,
                "away_total": a_total,
                "home_ks_left": ks_calls_remaining([t["outcome"] for t in ht]),
                "away_ks_left": ks_calls_remaining([t["outcome"] for t in at]),
                "complete": set_complete,
            })

        winner = None
        if g_complete:
            complete_games += 1
            if g_home > g_away:
                winner = "home"
                home_wins += 1
            elif g_away > g_home:
                winner = "away"
                away_wins += 1
            else:
                winner = "tie"
                ties += 1
        games_out.append({
            "id": g["id"],
            "number": g["game_number"],
            "complete": g_complete,
            "home_total": g_home,
            "away_total": g_away,
            "winner": winner,
            "sets": sets_out,
        })

    # Match status
    winner_team_id = None
    if m["completed"]:
        state = "completed"
        winner_team_id = m["winner_team_id"]
    elif home_wins >= 2:
        state, winner_team_id = "decided", home_id
    elif away_wins >= 2:
        state, winner_team_id = "decided", away_id
    elif complete_games == 3:
        if home_wins > away_wins:
            state, winner_team_id = "decided", home_id
        elif away_wins > home_wins:
            state, winner_team_id = "decided", away_id
        elif m["sudden_death_winner_team_id"]:
            state, winner_team_id = "decided", m["sudden_death_winner_team_id"]
        else:
            state = "sudden_death"
    else:
        state = "in_progress"

    return {
        "match": {
            "id": m["id"],
            "season_id": m["season_id"],
            "week": m["week"],
            "stage": m["stage"],
            "bracket": m["bracket"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": teams.get(home_id),
            "away_team_name": teams.get(away_id),
            "completed": bool(m["completed"]),
            "sudden_death_winner_team_id": m["sudden_death_winner_team_id"],
        },
        "rosters": rosters,
        "games": games_out,
        "status": {
            "state": state,
            "home_wins": home_wins,
            "away_wins": away_wins,
            "ties": ties,
            "complete_games": complete_games,
            "winner_team_id": winner_team_id,
            "winner_team_name": teams.get(winner_team_id),
        },
    }


def create_games_and_sets(db, match_id):
    """Every match always has 3 games x 3 sets."""
    for gn in (1, 2, 3):
        cur = db.execute(
            "INSERT INTO games (match_id, game_number) VALUES (?,?)", (match_id, gn)
        )
        gid = cur.lastrowid
        for sn in (1, 2, 3):
            db.execute(
                "INSERT INTO sets (game_id, set_number) VALUES (?,?)", (gid, sn)
            )
