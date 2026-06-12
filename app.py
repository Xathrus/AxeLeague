import sqlite3

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   url_for)

import bracket as bracket_mod
import scoring
import stats as stats_mod
from db import get_db, init_db

app = Flask(__name__)
init_db(app)


# ------------------------------------------------------------------ helpers

def _season_or_404(season_id):
    s = get_db().execute("SELECT * FROM seasons WHERE id=?", (season_id,)).fetchone()
    if not s:
        abort(404)
    return s


def _match_or_404(match_id):
    m = get_db().execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        abort(404)
    return m


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# -------------------------------------------------------------------- pages

@app.route("/")
def index():
    db = get_db()
    seasons = db.execute("SELECT * FROM seasons ORDER BY id DESC").fetchall()
    return render_template("index.html", seasons=seasons)


@app.post("/seasons")
def create_season():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO seasons (name) VALUES (?)", (name,))
        db.commit()
    return redirect(url_for("index"))


@app.route("/season/<int:season_id>")
def season_home(season_id):
    s = _season_or_404(season_id)
    db = get_db()
    n_teams = db.execute("SELECT COUNT(*) c FROM teams WHERE season_id=?",
                         (season_id,)).fetchone()["c"]
    n_matches = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE season_id=? AND stage='regular'",
        (season_id,)).fetchone()["c"]
    n_done = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE season_id=? AND stage='regular' AND completed=1",
        (season_id,)).fetchone()["c"]
    has_playoffs = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE season_id=? AND stage='playoff'",
        (season_id,)).fetchone()["c"] > 0
    top = stats_mod.standings(db, season_id)[:4]
    return render_template("season.html", season=s, n_teams=n_teams,
                           n_matches=n_matches, n_done=n_done,
                           has_playoffs=has_playoffs, top=top)


@app.route("/season/<int:season_id>/teams")
def teams_page(season_id):
    s = _season_or_404(season_id)
    db = get_db()
    teams = db.execute("SELECT * FROM teams WHERE season_id=? ORDER BY name",
                       (season_id,)).fetchall()
    players = db.execute(
        """SELECT p.* FROM players p JOIN teams t ON t.id=p.team_id
           WHERE t.season_id=? ORDER BY p.name""", (season_id,)).fetchall()
    by_team = {}
    for p in players:
        by_team.setdefault(p["team_id"], []).append(p)
    scheduled = db.execute("SELECT COUNT(*) c FROM matches WHERE season_id=?",
                           (season_id,)).fetchone()["c"] > 0
    return render_template("teams.html", season=s, teams=teams,
                           by_team=by_team, scheduled=scheduled)


@app.post("/season/<int:season_id>/teams")
def add_team(season_id):
    _season_or_404(season_id)
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO teams (season_id, name) VALUES (?,?)",
                   (season_id, name))
        db.commit()
    return redirect(url_for("teams_page", season_id=season_id))


@app.post("/team/<int:team_id>/players")
def add_player(team_id):
    db = get_db()
    t = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not t:
        abort(404)
    name = request.form.get("name", "").strip()
    if name:
        db.execute("INSERT INTO players (team_id, name) VALUES (?,?)",
                   (team_id, name))
        db.commit()
    return redirect(url_for("teams_page", season_id=t["season_id"]))


@app.post("/team/<int:team_id>/rename")
def rename_team(team_id):
    db = get_db()
    t = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not t:
        abort(404)
    name = request.form.get("name", "").strip()
    if name:
        db.execute("UPDATE teams SET name=? WHERE id=?", (name, team_id))
        db.commit()
    return redirect(url_for("teams_page", season_id=t["season_id"]))


@app.post("/team/<int:team_id>/delete")
def delete_team(team_id):
    db = get_db()
    t = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not t:
        abort(404)
    used = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE home_team_id=? OR away_team_id=?",
        (team_id, team_id)).fetchone()["c"]
    if not used:
        db.execute("DELETE FROM teams WHERE id=?", (team_id,))
        db.commit()
    return redirect(url_for("teams_page", season_id=t["season_id"]))


@app.post("/player/<int:player_id>/delete")
def delete_player(player_id):
    db = get_db()
    p = db.execute("SELECT p.*, t.season_id FROM players p JOIN teams t ON t.id=p.team_id WHERE p.id=?",
                   (player_id,)).fetchone()
    if not p:
        abort(404)
    used = db.execute("SELECT COUNT(*) c FROM throws WHERE player_id=?",
                      (player_id,)).fetchone()["c"]
    if not used:
        db.execute("DELETE FROM players WHERE id=?", (player_id,))
        db.commit()
    return redirect(url_for("teams_page", season_id=p["season_id"]))


@app.route("/season/<int:season_id>/schedule")
def schedule_page(season_id):
    s = _season_or_404(season_id)
    db = get_db()
    matches = db.execute(
        """SELECT m.*, th.name AS home_name, ta.name AS away_name,
                  tw.name AS winner_name
           FROM matches m
           LEFT JOIN teams th ON th.id=m.home_team_id
           LEFT JOIN teams ta ON ta.id=m.away_team_id
           LEFT JOIN teams tw ON tw.id=m.winner_team_id
           WHERE m.season_id=? AND m.stage='regular'
           ORDER BY m.week, m.id""", (season_id,)).fetchall()
    weeks = {}
    for m in matches:
        weeks.setdefault(m["week"], []).append(m)
    return render_template("schedule.html", season=s, weeks=weeks)


@app.post("/season/<int:season_id>/schedule/generate")
def generate_schedule(season_id):
    _season_or_404(season_id)
    db = get_db()
    existing = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE season_id=? AND stage='regular'",
        (season_id,)).fetchone()["c"]
    if not existing:
        try:
            bracket_mod.generate_double_round_robin(db, season_id)
            db.commit()
        except ValueError:
            db.rollback()
    return redirect(url_for("schedule_page", season_id=season_id))


@app.route("/season/<int:season_id>/standings")
def standings_page(season_id):
    s = _season_or_404(season_id)
    rows = stats_mod.standings(get_db(), season_id)
    return render_template("standings.html", season=s, rows=rows)


@app.route("/season/<int:season_id>/stats")
def stats_page(season_id):
    s = _season_or_404(season_id)
    db = get_db()
    players = stats_mod.player_season_stats(db, season_id)
    teams = stats_mod.team_season_stats(db, season_id)
    weeks, weekly = stats_mod.player_weekly_averages(db, season_id)
    return render_template("stats.html", season=s, players=players,
                           teams=teams, weeks=weeks, weekly=weekly)


@app.route("/season/<int:season_id>/playoffs")
def playoffs_page(season_id):
    s = _season_or_404(season_id)
    db = get_db()
    rows = db.execute(
        """SELECT m.*, th.name AS home_name, ta.name AS away_name
           FROM matches m
           LEFT JOIN teams th ON th.id=m.home_team_id
           LEFT JOIN teams ta ON ta.id=m.away_team_id
           WHERE m.season_id=? AND m.stage='playoff'
           ORDER BY m.bracket, m.bracket_round, m.bracket_slot""",
        (season_id,)).fetchall()

    def card(m):
        st = scoring.compute_match_state(db, m["id"])
        return {
            "id": m["id"], "home": m["home_name"], "away": m["away_name"],
            "home_id": m["home_team_id"], "away_id": m["away_team_id"],
            "completed": bool(m["completed"]),
            "winner_team_id": m["winner_team_id"],
            "home_wins": st["status"]["home_wins"],
            "away_wins": st["status"]["away_wins"],
            "bye": bool(m["completed"]) and (m["home_team_id"] is None or m["away_team_id"] is None),
        }

    wb, lb, gf = {}, {}, []
    for m in rows:
        c = card(m)
        if m["bracket"] == "W":
            wb.setdefault(m["bracket_round"], []).append(c)
        elif m["bracket"] == "L":
            lb.setdefault(m["bracket_round"], []).append(c)
        else:
            gf.append((m["bracket_round"], c))
    gf.sort()
    champ_id = bracket_mod.champion(db, season_id)
    champ = None
    if champ_id:
        champ = db.execute("SELECT name FROM teams WHERE id=?", (champ_id,)).fetchone()["name"]
    seeds_preview = stats_mod.standings(db, season_id)
    return render_template("playoffs.html", season=s, wb=wb, lb=lb,
                           gf=[c for _, c in gf], champ=champ,
                           created=bool(rows), seeds=seeds_preview)


@app.post("/season/<int:season_id>/playoffs/create")
def create_playoffs(season_id):
    _season_or_404(season_id)
    db = get_db()
    existing = db.execute(
        "SELECT COUNT(*) c FROM matches WHERE season_id=? AND stage='playoff'",
        (season_id,)).fetchone()["c"]
    if not existing:
        seeds = [r["team_id"] for r in stats_mod.standings(db, season_id)]
        try:
            bracket_mod.create_bracket(db, season_id, seeds)
            db.commit()
        except ValueError:
            db.rollback()
    return redirect(url_for("playoffs_page", season_id=season_id))


@app.post("/season/<int:season_id>/playoffs/reset")
def reset_playoffs(season_id):
    _season_or_404(season_id)
    db = get_db()
    db.execute("DELETE FROM matches WHERE season_id=? AND stage='playoff'",
               (season_id,))
    db.commit()
    return redirect(url_for("playoffs_page", season_id=season_id))


@app.route("/match/<int:match_id>")
def match_page(match_id):
    m = _match_or_404(match_id)
    db = get_db()
    home = db.execute("SELECT name FROM teams WHERE id=?", (m["home_team_id"],)).fetchone()
    away = db.execute("SELECT name FROM teams WHERE id=?", (m["away_team_id"],)).fetchone()
    return render_template("match.html", match=m,
                           home_name=home["name"] if home else "TBD",
                           away_name=away["name"] if away else "TBD")


# ---------------------------------------------------------------------- API

@app.get("/api/match/<int:match_id>/state")
def api_state(match_id):
    st = scoring.compute_match_state(get_db(), match_id)
    if st is None:
        return _err("Match not found", 404)
    return jsonify(st)


@app.post("/api/set/<int:set_id>/assign")
def api_assign(set_id):
    db = get_db()
    s = db.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if not s:
        return _err("Set not found", 404)
    g = db.execute("SELECT * FROM games WHERE id=?", (s["game_id"],)).fetchone()
    m = db.execute("SELECT * FROM matches WHERE id=?", (g["match_id"],)).fetchone()
    if m["completed"]:
        return _err("Match is completed.")
    data = request.get_json(force=True)
    for field, team_col in (("home_player_id", "home_team_id"),
                            ("away_player_id", "away_team_id")):
        if field in data:
            pid = data[field]
            # block changing a player who already has throws in this set
            current = s[field]
            if current and pid != current:
                n = db.execute(
                    "SELECT COUNT(*) c FROM throws WHERE set_id=? AND player_id=?",
                    (set_id, current)).fetchone()["c"]
                if n:
                    return _err("That thrower already has throws in this set. "
                                "Use edit mode to correct throws instead.")
            if pid is not None:
                p = db.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
                if not p or p["team_id"] != m[team_col]:
                    return _err("Player is not on that team's roster.")
            db.execute(f"UPDATE sets SET {field}=? WHERE id=?", (pid, set_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/set/<int:set_id>/throw")
def api_throw(set_id):
    db = get_db()
    s = db.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if not s:
        return _err("Set not found", 404)
    g = db.execute("SELECT * FROM games WHERE id=?", (s["game_id"],)).fetchone()
    m = db.execute("SELECT * FROM matches WHERE id=?", (g["match_id"],)).fetchone()
    if m["completed"]:
        return _err("Match is completed. Reopen it to make changes.")
    data = request.get_json(force=True)
    pid = data.get("player_id")
    outcome = data.get("outcome")
    if pid not in (s["home_player_id"], s["away_player_id"]) or pid is None:
        return _err("Player is not throwing in this set.")
    if outcome not in scoring.OUTCOME_POINTS:
        return _err("Unknown outcome.")
    rows = db.execute(
        "SELECT outcome FROM throws WHERE set_id=? AND player_id=? ORDER BY throw_number",
        (set_id, pid)).fetchall()
    seq = [r["outcome"] for r in rows]
    if len(seq) >= scoring.THROWS_PER_SET:
        return _err("All 10 throws are already recorded for this player.")
    if outcome in scoring.KS_OUTCOMES and scoring.ks_calls_remaining(seq) <= 0:
        return _err("No killshot calls remaining for this player.")
    try:
        db.execute(
            "INSERT INTO throws (set_id, player_id, throw_number, outcome, points)"
            " VALUES (?,?,?,?,?)",
            (set_id, pid, len(seq) + 1, outcome, scoring.OUTCOME_POINTS[outcome]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _err("Another scorekeeper just recorded a throw. Try again.", 409)
    return jsonify({"ok": True})


@app.post("/api/set/<int:set_id>/undo")
def api_undo(set_id):
    db = get_db()
    s = db.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if not s:
        return _err("Set not found", 404)
    g = db.execute("SELECT * FROM games WHERE id=?", (s["game_id"],)).fetchone()
    m = db.execute("SELECT * FROM matches WHERE id=?", (g["match_id"],)).fetchone()
    if m["completed"]:
        return _err("Match is completed. Reopen it to make changes.")
    pid = request.get_json(force=True).get("player_id")
    last = db.execute(
        """SELECT * FROM throws WHERE set_id=? AND player_id=?
           ORDER BY throw_number DESC LIMIT 1""", (set_id, pid)).fetchone()
    if not last:
        return _err("Nothing to undo.")
    db.execute("DELETE FROM throws WHERE id=?", (last["id"],))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/throw/<int:throw_id>/edit")
def api_edit_throw(throw_id):
    db = get_db()
    t = db.execute("SELECT * FROM throws WHERE id=?", (throw_id,)).fetchone()
    if not t:
        return _err("Throw not found", 404)
    s = db.execute("SELECT * FROM sets WHERE id=?", (t["set_id"],)).fetchone()
    g = db.execute("SELECT * FROM games WHERE id=?", (s["game_id"],)).fetchone()
    m = db.execute("SELECT * FROM matches WHERE id=?", (g["match_id"],)).fetchone()
    if m["completed"]:
        return _err("Match is completed. Reopen it to make changes.")
    outcome = request.get_json(force=True).get("outcome")
    if outcome not in scoring.OUTCOME_POINTS:
        return _err("Unknown outcome.")
    rows = db.execute(
        "SELECT * FROM throws WHERE set_id=? AND player_id=? ORDER BY throw_number",
        (t["set_id"], t["player_id"])).fetchall()
    seq = [outcome if r["id"] == throw_id else r["outcome"] for r in rows]
    if not scoring.ks_sequence_valid(seq):
        return _err("That edit would exceed the player's killshot calls "
                    "(2 per set, +1 per drop).")
    db.execute("UPDATE throws SET outcome=?, points=? WHERE id=?",
               (outcome, scoring.OUTCOME_POINTS[outcome], throw_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/sudden_death")
def api_sudden_death(match_id):
    db = get_db()
    m = _match_or_404(match_id)
    if m["completed"]:
        return _err("Match is completed.")
    st = scoring.compute_match_state(db, match_id)
    if st["status"]["state"] not in ("sudden_death", "decided"):
        return _err("Sudden death only applies once all 3 games are complete "
                    "and game wins are tied.")
    winner = request.get_json(force=True).get("winner_team_id")
    if winner not in (m["home_team_id"], m["away_team_id"], None):
        return _err("Winner must be one of the two teams.")
    db.execute("UPDATE matches SET sudden_death_winner_team_id=? WHERE id=?",
               (winner, match_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/complete")
def api_complete(match_id):
    db = get_db()
    m = _match_or_404(match_id)
    if m["completed"]:
        return _err("Match is already completed.")
    st = scoring.compute_match_state(db, match_id)
    if st["status"]["state"] != "decided":
        return _err("Match isn't decided yet.")
    winner = st["status"]["winner_team_id"]
    db.execute("UPDATE matches SET completed=1, winner_team_id=? WHERE id=?",
               (winner, match_id))
    m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if m["stage"] == "playoff":
        bracket_mod.apply_advancement(db, m, winner)
        bracket_mod.propagate(db, m["season_id"])
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/reopen")
def api_reopen(match_id):
    db = get_db()
    m = _match_or_404(match_id)
    if not m["completed"]:
        return _err("Match isn't completed.")
    if m["stage"] == "playoff":
        return _err("Playoff matches can't be reopened once completed — "
                    "the bracket has already advanced. Use 'Reset playoffs' "
                    "on the playoffs page if you need to start the bracket over.")
    db.execute(
        "UPDATE matches SET completed=0, winner_team_id=NULL,"
        " sudden_death_winner_team_id=NULL WHERE id=?", (match_id,))
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
