"""Double round-robin scheduling and double-elimination playoff bracket."""
import math

import scoring


# ---------------------------------------------------------------- scheduling

def round_robin_rounds(team_ids):
    """Circle-method round robin. Returns list of rounds, each a list of
    (home_id, away_id) pairs."""
    ts = list(team_ids)
    if len(ts) % 2:
        ts.append(None)  # bye
    n = len(ts)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ts[i], ts[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        ts = [ts[0]] + [ts[-1]] + ts[1:-1]
    return rounds


def generate_double_round_robin(db, season_id):
    teams = [t["id"] for t in db.execute(
        "SELECT id FROM teams WHERE season_id=? ORDER BY id", (season_id,)).fetchall()]
    if len(teams) < 2:
        raise ValueError("Need at least 2 teams to generate a schedule.")
    week = 0
    for cycle in (0, 1):
        for rnd in round_robin_rounds(teams):
            week += 1
            for home, away in rnd:
                if cycle == 1:
                    home, away = away, home  # flip home/away second time around
                cur = db.execute(
                    "INSERT INTO matches (season_id, week, home_team_id, away_team_id, stage)"
                    " VALUES (?,?,?,?, 'regular')",
                    (season_id, week, home, away))
                scoring.create_games_and_sets(db, cur.lastrowid)
    return week


# ------------------------------------------------------------------ playoffs

def seed_order(size):
    """Slot order of seeds for a bracket of `size` (power of 2).
    e.g. size 8 -> [1, 8, 4, 5, 2, 7, 3, 6]"""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [s for seed in order for s in (seed, n + 1 - seed)]
    return order


def _lb_round_count(size, m):
    """Number of matches in losers-bracket round m (1-indexed)."""
    return size >> (((m + 1) // 2) + 1)


def create_bracket(db, season_id, seeds):
    """Create a double-elimination bracket. `seeds` is a list of team ids,
    best seed first. Bracket is padded to the next power of two with byes."""
    n = len(seeds)
    if n < 2:
        raise ValueError("Need at least 2 teams for playoffs.")
    size = 1
    while size < n:
        size *= 2
    R = int(math.log2(size))

    ids = {}

    def new_match(bk, rnd, slot):
        cur = db.execute(
            "INSERT INTO matches (season_id, stage, bracket, bracket_round, bracket_slot)"
            " VALUES (?,?,?,?,?)",
            (season_id, "playoff", bk, rnd, slot))
        mid = cur.lastrowid
        scoring.create_games_and_sets(db, mid)
        ids[(bk, rnd, slot)] = mid
        return mid

    # Winners bracket
    for r in range(1, R + 1):
        for slot in range(size >> r):
            new_match("W", r, slot)
    # Losers bracket
    lb_last = 2 * (R - 1) if R >= 2 else 0
    for m_r in range(1, lb_last + 1):
        for slot in range(_lb_round_count(size, m_r)):
            new_match("L", m_r, slot)
    # Grand final (round 1; a reset match GF round 2 is created on demand)
    gf = new_match("GF", 1, 0)

    # Seed teams into WB round 1
    order = seed_order(size)
    for slot in range(size // 2):
        s1, s2 = order[2 * slot], order[2 * slot + 1]
        home = seeds[s1 - 1] if s1 <= n else None
        away = seeds[s2 - 1] if s2 <= n else None
        db.execute("UPDATE matches SET home_team_id=?, away_team_id=? WHERE id=?",
                   (home, away, ids[("W", 1, slot)]))

    def link(mid, w_to=None, w_pos=None, l_to=None, l_pos=None):
        db.execute(
            "UPDATE matches SET winner_to_match=?, winner_to_pos=?,"
            " loser_to_match=?, loser_to_pos=? WHERE id=?",
            (w_to, w_pos, l_to, l_pos, mid))

    # Wire winners bracket
    for r in range(1, R + 1):
        for slot in range(size >> r):
            mid = ids[("W", r, slot)]
            if r < R:
                w_to, w_pos = ids[("W", r + 1, slot // 2)], 1 + slot % 2
            else:
                w_to, w_pos = gf, 1
            if R >= 2:
                if r == 1:
                    l_to, l_pos = ids[("L", 1, slot // 2)], 1 + slot % 2
                else:
                    l_to, l_pos = ids[("L", 2 * (r - 1), slot)], 2
            else:  # 2-team bracket: loser goes straight to the grand final
                l_to, l_pos = gf, 2
            link(mid, w_to, w_pos, l_to, l_pos)

    # Wire losers bracket
    for m_r in range(1, lb_last + 1):
        for slot in range(_lb_round_count(size, m_r)):
            mid = ids[("L", m_r, slot)]
            if m_r == lb_last:
                w_to, w_pos = gf, 2
            elif m_r % 2 == 1:  # minor round -> same slot, home side of major round
                w_to, w_pos = ids[("L", m_r + 1, slot)], 1
            else:               # major round -> pair up into next minor round
                w_to, w_pos = ids[("L", m_r + 1, slot // 2)], 1 + slot % 2
            link(mid, w_to, w_pos)

    propagate(db, season_id)
    return gf


def _feeders(db, season_id, match_id):
    return db.execute(
        "SELECT * FROM matches WHERE season_id=? AND stage='playoff'"
        " AND (winner_to_match=? OR loser_to_match=?)",
        (season_id, match_id, match_id)).fetchall()


def apply_advancement(db, m, winner_id):
    """Push winner/loser of a completed playoff match into downstream slots."""
    home, away = m["home_team_id"], m["away_team_id"]
    loser_id = None
    if winner_id is not None:
        loser_id = away if winner_id == home else home

    def place(mid, pos, team):
        col = "home_team_id" if pos == 1 else "away_team_id"
        db.execute(f"UPDATE matches SET {col}=? WHERE id=?", (team, mid))

    if m["winner_to_match"]:
        place(m["winner_to_match"], m["winner_to_pos"], winner_id)
    if m["loser_to_match"]:
        place(m["loser_to_match"], m["loser_to_pos"], loser_id)

    # Grand final won by the losers-bracket team -> bracket reset (GF game 2)
    if m["bracket"] == "GF" and m["bracket_round"] == 1 and winner_id is not None \
            and winner_id == m["away_team_id"]:
        exists = db.execute(
            "SELECT id FROM matches WHERE season_id=? AND stage='playoff'"
            " AND bracket='GF' AND bracket_round=2", (m["season_id"],)).fetchone()
        if not exists:
            cur = db.execute(
                "INSERT INTO matches (season_id, stage, bracket, bracket_round,"
                " bracket_slot, home_team_id, away_team_id) VALUES"
                " (?,?,'GF',2,0,?,?)",
                (m["season_id"], "playoff", m["home_team_id"], m["away_team_id"]))
            scoring.create_games_and_sets(db, cur.lastrowid)


def propagate(db, season_id):
    """Auto-resolve byes: any playoff match whose feeders are all completed but
    which has fewer than two teams completes automatically."""
    changed = True
    while changed:
        changed = False
        rows = db.execute(
            "SELECT * FROM matches WHERE season_id=? AND stage='playoff'"
            " AND completed=0 ORDER BY id", (season_id,)).fetchall()
        for m in rows:
            feeders = _feeders(db, season_id, m["id"])
            if any(not f["completed"] for f in feeders):
                continue
            teams = [t for t in (m["home_team_id"], m["away_team_id"]) if t]
            if len(teams) >= 2:
                continue  # real match, must be played
            winner = teams[0] if teams else None
            db.execute("UPDATE matches SET completed=1, winner_team_id=? WHERE id=?",
                       (winner, m["id"]))
            m = db.execute("SELECT * FROM matches WHERE id=?", (m["id"],)).fetchone()
            apply_advancement(db, m, winner)
            changed = True


def champion(db, season_id):
    """Return champion team id if the bracket is finished, else None."""
    gf2 = db.execute(
        "SELECT * FROM matches WHERE season_id=? AND stage='playoff'"
        " AND bracket='GF' AND bracket_round=2", (season_id,)).fetchone()
    if gf2:
        return gf2["winner_team_id"] if gf2["completed"] else None
    gf1 = db.execute(
        "SELECT * FROM matches WHERE season_id=? AND stage='playoff'"
        " AND bracket='GF' AND bracket_round=1", (season_id,)).fetchone()
    if gf1 and gf1["completed"]:
        # If the LB team had won game 1 a reset match would exist, so the
        # winner here is the champion.
        return gf1["winner_team_id"]
    return None
