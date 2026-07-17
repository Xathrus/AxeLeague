"""Export a season to a portable JSON file and import it back.

The file carries everything a season owns: teams, rosters, round dates, the
full schedule and bracket wiring, and every recorded throw. Internal ids are
remapped on import, so a file can be imported into any installation (or the
same one, as a copy).
"""
import json

import scoring

FORMAT = "axeleague-season"
VERSION = 1


class SeasonImportError(Exception):
    pass


def export_season(db, season_id):
    season = db.execute("SELECT * FROM seasons WHERE id=?",
                        (season_id,)).fetchone()
    teams = []
    for t in db.execute("SELECT * FROM teams WHERE season_id=? ORDER BY id",
                        (season_id,)).fetchall():
        players = db.execute(
            "SELECT id, name FROM players WHERE team_id=? ORDER BY id",
            (t["id"],)).fetchall()
        teams.append({"id": t["id"], "name": t["name"],
                      "players": [{"id": p["id"], "name": p["name"]}
                                  for p in players]})
    matches = []
    for m in db.execute("SELECT * FROM matches WHERE season_id=? ORDER BY id",
                        (season_id,)).fetchall():
        games = []
        for g in db.execute(
                "SELECT * FROM games WHERE match_id=? ORDER BY game_number",
                (m["id"],)).fetchall():
            sets_ = []
            for s in db.execute(
                    "SELECT * FROM sets WHERE game_id=? ORDER BY set_number",
                    (g["id"],)).fetchall():
                throws = db.execute(
                    "SELECT player_id, throw_number, outcome FROM throws"
                    " WHERE set_id=? ORDER BY player_id, throw_number",
                    (s["id"],)).fetchall()
                sets_.append({
                    "number": s["set_number"],
                    "home_player_id": s["home_player_id"],
                    "away_player_id": s["away_player_id"],
                    "throws": [dict(t) for t in throws],
                })
            games.append({"number": g["game_number"], "sets": sets_})
        matches.append({
            "id": m["id"], "week": m["week"], "stage": m["stage"],
            "bracket": m["bracket"], "bracket_round": m["bracket_round"],
            "bracket_slot": m["bracket_slot"],
            "home_team_id": m["home_team_id"],
            "away_team_id": m["away_team_id"],
            "winner_to_match": m["winner_to_match"],
            "winner_to_pos": m["winner_to_pos"],
            "loser_to_match": m["loser_to_match"],
            "loser_to_pos": m["loser_to_pos"],
            "sudden_death_winner_team_id": m["sudden_death_winner_team_id"],
            "completed": m["completed"],
            "winner_team_id": m["winner_team_id"],
            "games": games,
        })
    round_dates = [dict(r) for r in db.execute(
        "SELECT round, date FROM round_dates WHERE season_id=?",
        (season_id,)).fetchall()]
    return {
        "format": FORMAT, "version": VERSION,
        "season": {"name": season["name"]},
        "teams": teams, "matches": matches, "round_dates": round_dates,
    }


def import_season(db, data):
    """Create a new season from exported data. Returns the new season id.
    Raises SeasonImportError on malformed input; caller commits."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SeasonImportError(
            "That doesn't look like a season export file.")
    if data.get("version", 0) > VERSION:
        raise SeasonImportError(
            "This file came from a newer version of the app — update first.")
    name = (data.get("season") or {}).get("name") or "Imported Season"

    cur = db.execute("INSERT INTO seasons (name) VALUES (?)", (name,))
    sid = cur.lastrowid

    team_map, player_map = {}, {}
    for t in data.get("teams", []):
        cur = db.execute("INSERT INTO teams (season_id, name) VALUES (?, ?)",
                         (sid, t["name"]))
        team_map[t["id"]] = cur.lastrowid
        for p in t.get("players", []):
            cur = db.execute(
                "INSERT INTO players (team_id, name) VALUES (?, ?)",
                (team_map[t["id"]], p["name"]))
            player_map[p["id"]] = cur.lastrowid

    def team(x):
        if x is None:
            return None
        if x not in team_map:
            raise SeasonImportError("File is inconsistent: unknown team reference.")
        return team_map[x]

    def player(x):
        if x is None:
            return None
        if x not in player_map:
            raise SeasonImportError("File is inconsistent: unknown player reference.")
        return player_map[x]

    match_map = {}
    for m in data.get("matches", []):
        if m.get("stage") not in ("regular", "playoff"):
            raise SeasonImportError("File is inconsistent: bad match stage.")
        cur = db.execute(
            """INSERT INTO matches (season_id, week, stage, bracket,
                 bracket_round, bracket_slot, home_team_id, away_team_id,
                 sudden_death_winner_team_id, completed, winner_team_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, m.get("week"), m["stage"], m.get("bracket"),
             m.get("bracket_round"), m.get("bracket_slot"),
             team(m.get("home_team_id")), team(m.get("away_team_id")),
             team(m.get("sudden_death_winner_team_id")),
             1 if m.get("completed") else 0, team(m.get("winner_team_id"))))
        match_map[m["id"]] = cur.lastrowid
        for g in m.get("games", []):
            cur = db.execute(
                "INSERT INTO games (match_id, game_number) VALUES (?, ?)",
                (match_map[m["id"]], g["number"]))
            gid = cur.lastrowid
            for s in g.get("sets", []):
                cur = db.execute(
                    """INSERT INTO sets (game_id, set_number,
                         home_player_id, away_player_id) VALUES (?,?,?,?)""",
                    (gid, s["number"], player(s.get("home_player_id")),
                     player(s.get("away_player_id"))))
                set_id = cur.lastrowid
                for t in s.get("throws", []):
                    o = t.get("outcome")
                    if o not in scoring.OUTCOME_POINTS:
                        raise SeasonImportError(
                            "File is inconsistent: bad throw outcome.")
                    db.execute(
                        """INSERT INTO throws (set_id, player_id,
                             throw_number, outcome, points) VALUES (?,?,?,?,?)""",
                        (set_id, player(t["player_id"]), t["throw_number"],
                         o, scoring.OUTCOME_POINTS[o]))

    # second pass: bracket advancement pointers
    for m in data.get("matches", []):
        w_to = m.get("winner_to_match")
        l_to = m.get("loser_to_match")
        if w_to is None and l_to is None:
            continue
        if (w_to is not None and w_to not in match_map) or \
           (l_to is not None and l_to not in match_map):
            raise SeasonImportError("File is inconsistent: bad bracket wiring.")
        db.execute(
            """UPDATE matches SET winner_to_match=?, winner_to_pos=?,
                 loser_to_match=?, loser_to_pos=? WHERE id=?""",
            (match_map.get(w_to), m.get("winner_to_pos"),
             match_map.get(l_to), m.get("loser_to_pos"), match_map[m["id"]]))

    for rd in data.get("round_dates", []):
        db.execute(
            "INSERT INTO round_dates (season_id, round, date) VALUES (?,?,?)",
            (sid, rd["round"], rd["date"]))
    return sid


def loads(raw_bytes):
    try:
        return json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SeasonImportError("That file isn't valid JSON.")
