"""Seed a demo season with teams, players, and a generated schedule.

Run once after install if you want sample data to click around in:
    cd /opt/axeleague && ./venv/bin/python seed_demo.py
"""
import os
import sqlite3

import db as dbmod
import bracket

TEAMS = {
    "Timber Wolves": ["Jake", "Maria", "Deke"],
    "Splitting Heirs": ["Tony", "Rachel", "Bo"],
    "Sharp Shooters": ["Carlos", "Amy", "Hank"],
    "Axe Holes": ["Pete", "Lindsey", "Walt"],
    "Bullseye Bandits": ["Sam", "Tess", "Rod"],
    "The Lumber Jacks": ["Vic", "Nora", "Gus"],
}


def main():
    os.makedirs(os.path.dirname(dbmod.DB_PATH), exist_ok=True)
    fresh = not os.path.exists(dbmod.DB_PATH)
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if fresh:
        with open(os.path.join(dbmod.BASE_DIR, "schema.sql")) as f:
            conn.executescript(f.read())

    cur = conn.execute("INSERT INTO seasons (name) VALUES (?)", ("Demo Season",))
    season_id = cur.lastrowid

    for team, players in TEAMS.items():
        cur = conn.execute(
            "INSERT INTO teams (season_id, name) VALUES (?, ?)", (season_id, team))
        tid = cur.lastrowid
        for p in players:
            conn.execute("INSERT INTO players (team_id, name) VALUES (?, ?)", (tid, p))

    weeks = bracket.generate_double_round_robin(conn, season_id)
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE season_id=?", (season_id,)).fetchone()[0]
    conn.close()
    print(f"Seeded demo season (id={season_id}): {len(TEAMS)} teams, "
          f"{n} matches across {weeks} weeks.")


if __name__ == "__main__":
    main()
