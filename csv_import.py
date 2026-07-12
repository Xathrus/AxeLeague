"""Import a full match's scores from a CSV file (admin only).

Expected columns: Game, Set, Thrower, Team, Throw 1 ... Throw 10
 - Game and Set are 1-3.
 - Team must be one of the two teams in the match (case-insensitive).
 - Throw values: 1-5 = that score, 6 = bullseye, 8 = killshot hit, plus the
   words Miss, Drop, Kill Miss, Kill Drop (several spellings accepted).
   Trailing blanks are allowed for sets that weren't finished.
 - Unknown throwers are created on their team automatically.

Importing REPLACES any scores already recorded on the match.
"""
import csv
import io

import scoring

# accepted spellings -> outcome code (lowercased, spaces collapsed)
TOKENS = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
    "6": "B", "b": "B", "bull": "B", "bullseye": "B",
    "8": "KH", "kh": "KH", "kill": "KH", "killshot": "KH", "kill hit": "KH",
    "kill shot": "KH", "killshot hit": "KH", "kill shot hit": "KH",
    "km": "KM", "kill miss": "KM", "killshot miss": "KM", "kill shot miss": "KM",
    "kd": "KD", "kill drop": "KD", "killshot drop": "KD", "kill shot drop": "KD",
    "0": "M", "m": "M", "miss": "M",
    "d": "D", "drop": "D",
}


class ImportError_(Exception):
    pass


def _token(raw, where):
    t = " ".join((raw or "").strip().lower().split())
    if t == "":
        return None
    if t not in TOKENS:
        raise ImportError_(
            f"{where}: unrecognized throw value {raw!r}. Use 1-6, 8, "
            "Miss, Drop, Kill Miss, or Kill Drop.")
    return TOKENS[t]


def parse(file_bytes, home_name, away_name):
    """Returns {(game, set): {'home'|'away': (thrower, [outcomes])}}.
    Raises ImportError_ with a human-readable message on any problem."""
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_("That file isn't UTF-8 text. Save it as CSV and retry.")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        raise ImportError_("The file is empty.")

    header = [c.strip().lower() for c in rows[0]]
    if header[:4] != ["game", "set", "thrower", "team"] or len(header) < 14:
        raise ImportError_(
            "Unexpected header. The first row must be: Game, Set, Thrower, "
            "Team, Throw 1 … Throw 10.")

    sides = {home_name.strip().lower(): "home", away_name.strip().lower(): "away"}
    out = {}
    for i, row in enumerate(rows[1:], start=2):
        where = f"Row {i}"
        if len(row) < 4:
            raise ImportError_(f"{where}: not enough columns.")
        try:
            game = int(row[0]); set_ = int(row[1])
        except ValueError:
            raise ImportError_(f"{where}: Game and Set must be numbers 1-3.")
        if not (1 <= game <= 3 and 1 <= set_ <= 3):
            raise ImportError_(f"{where}: Game and Set must be between 1 and 3.")
        thrower = row[2].strip()
        if not thrower:
            raise ImportError_(f"{where}: Thrower name is blank.")
        team = row[3].strip()
        side = sides.get(team.lower())
        if side is None:
            raise ImportError_(
                f"{where}: team {team!r} isn't in this match "
                f"({home_name} vs {away_name}).")
        key = (game, set_)
        if side in out.get(key, {}):
            raise ImportError_(
                f"{where}: duplicate row for Game {game} Set {set_} ({team}).")

        cells = (row[4:14] + [""] * 10)[:10]
        outcomes = []
        ended = False
        for n, cell in enumerate(cells, start=1):
            tok = _token(cell, f"{where}, Throw {n}")
            if tok is None:
                ended = True
                continue
            if ended:
                raise ImportError_(
                    f"{where}: Throw {n} comes after a blank throw — "
                    "fill throws in order with no gaps.")
            outcomes.append(tok)
        if not scoring.ks_sequence_valid(outcomes):
            raise ImportError_(
                f"{where}: killshot calls exceed what's available "
                "(2 per set, +1 per drop).")
        out.setdefault(key, {})[side] = (thrower, outcomes)
    return out


def apply(db, match, parsed):
    """Write parsed scores onto the match, replacing whatever was there.
    Returns a summary dict; caller commits."""
    sets = db.execute(
        """SELECT s.id, s.set_number, g.game_number
           FROM sets s JOIN games g ON g.id=s.game_id
           WHERE g.match_id=? ORDER BY g.game_number, s.set_number""",
        (match["id"],)).fetchall()
    by_key = {(r["game_number"], r["set_number"]): r["id"] for r in sets}

    def find_or_create_player(team_id, name):
        p = db.execute(
            "SELECT id FROM players WHERE team_id=? AND lower(name)=lower(?)",
            (team_id, name)).fetchone()
        if p:
            return p["id"], False
        cur = db.execute("INSERT INTO players (team_id, name) VALUES (?, ?)",
                         (team_id, name))
        return cur.lastrowid, True

    # wipe existing scores + assignments for the whole match
    set_ids = [r["id"] for r in sets]
    qmarks = ",".join("?" * len(set_ids))
    db.execute(f"DELETE FROM throws WHERE set_id IN ({qmarks})", set_ids)
    db.execute(
        f"UPDATE sets SET home_player_id=NULL, away_player_id=NULL"
        f" WHERE id IN ({qmarks})", set_ids)
    db.execute("UPDATE matches SET sudden_death_winner_team_id=NULL WHERE id=?",
               (match["id"],))

    created = []
    n_throws = 0
    for (game, set_), side_rows in parsed.items():
        sid = by_key[(game, set_)]
        for side, (thrower, outcomes) in side_rows.items():
            team_id = match["home_team_id"] if side == "home" \
                else match["away_team_id"]
            pid, was_created = find_or_create_player(team_id, thrower)
            if was_created:
                created.append(thrower)
            db.execute(f"UPDATE sets SET {side}_player_id=? WHERE id=?",
                       (pid, sid))
            for n, o in enumerate(outcomes, start=1):
                db.execute(
                    """INSERT INTO throws
                       (set_id, player_id, throw_number, outcome, points)
                       VALUES (?, ?, ?, ?, ?)""",
                    (sid, pid, n, o, scoring.OUTCOME_POINTS[o]))
                n_throws += 1
    return {"sets": sum(len(v) for v in parsed.values()),
            "throws": n_throws, "created_players": created}
