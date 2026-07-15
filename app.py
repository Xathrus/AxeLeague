import sqlite3

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)

import auth
import bracket as bracket_mod
import branding as branding_mod
import csv_import
import season_io
import achievements as ach
import scoring
import stats as stats_mod
from auth import admin_required, scorekeeper_required
from db import get_db, init_db

app = Flask(__name__)
init_db(app)
auth.ensure_schema()
ach.ensure_schema()
app.secret_key = auth.load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # logo uploads

with app.app_context():
    _d = get_db()
    ach.backfill(_d)
    _d.commit()


# ---------------------------------------------------------------- auth gate

@app.before_request
def require_login():
    open_endpoints = {"setup", "login", "logout", "static",
                      "branding_logo_file"}
    if request.endpoint in open_endpoints or request.endpoint is None:
        return None
    db = get_db()
    if not auth.setup_done(db):
        return redirect(url_for("setup"))
    if not session.get("role"):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Please log in."}), 401
        return redirect(url_for("login", next=request.path))
    return None


@app.context_processor
def inject_role():
    role = session.get("role")
    return {
        "role": role,
        "can_edit": role == "admin",
        "can_score": role in ("admin", "scorekeeper"),
        "branding": branding_mod.get_branding(get_db()),
    }


# ---------------------------------------------------------------- branding

@app.route("/branding")
@admin_required
def branding_page():
    return render_template("branding.html",
                           message=request.args.get("m"),
                           error=request.args.get("e"))


@app.post("/branding/colors")
@admin_required
def branding_colors():
    db = get_db()
    for key in branding_mod.FIELDS:
        v = request.form.get(key, "").strip()
        if branding_mod.valid_hex(v):
            branding_mod.set_setting(db, "brand_" + key, v)
    db.commit()
    return redirect(url_for("branding_page", m="Colors saved."))


@app.post("/branding/name")
@admin_required
def branding_name():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        branding_mod.set_setting(db, "brand_name", name)
        msg = "Venue name saved."
    else:
        branding_mod.delete_setting(db, "brand_name")
        msg = "Venue name reset to the default."
    db.commit()
    return redirect(url_for("branding_page", m=msg))


@app.post("/branding/preset")
@admin_required
def branding_preset():
    key = request.form.get("preset", "")
    preset = branding_mod.PRESETS.get(key)
    if not preset:
        abort(400)
    db = get_db()
    if preset["colors"] is None:
        for k in branding_mod.FIELDS:
            branding_mod.delete_setting(db, "brand_" + k)
    else:
        for k, v in preset["colors"].items():
            branding_mod.set_setting(db, "brand_" + k, v)
    db.commit()
    return redirect(url_for("branding_page",
                            m=f"Theme applied: {preset['label']}."))


@app.post("/branding/colors/reset")
@admin_required
def branding_colors_reset():
    db = get_db()
    for key in branding_mod.FIELDS:
        branding_mod.delete_setting(db, "brand_" + key)
    db.commit()
    return redirect(url_for("branding_page", m="Colors reset to the default theme."))


@app.post("/branding/logo")
@admin_required
def branding_logo():
    f = request.files.get("logo")
    if not f or not f.filename:
        return redirect(url_for("branding_page", e="Choose a file first."))
    ok, err = branding_mod.save_logo(get_db(), f)
    if not ok:
        return redirect(url_for("branding_page", e=err))
    return redirect(url_for("branding_page", m="Logo uploaded."))


@app.post("/branding/logo/remove")
@admin_required
def branding_logo_remove():
    branding_mod.remove_logo(get_db())
    return redirect(url_for("branding_page", m="Logo removed."))


@app.route("/branding/logo-file")
def branding_logo_file():
    b = branding_mod.get_branding(get_db())
    if not b["logo"]:
        abort(404)
    from flask import send_from_directory
    return send_from_directory(branding_mod.LOGO_DIR, b["logo"],
                               max_age=3600)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if auth.setup_done(db):
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        admin_pw = request.form.get("admin_password", "")
        admin_pw2 = request.form.get("admin_password2", "")
        sk_pw = request.form.get("scorekeeper_password", "")
        sk_pw2 = request.form.get("scorekeeper_password2", "")
        if len(admin_pw) < 4 or len(sk_pw) < 4:
            error = "Passwords must be at least 4 characters."
        elif admin_pw != admin_pw2 or sk_pw != sk_pw2:
            error = "Passwords don't match. Re-enter them."
        else:
            auth.set_password(db, "admin", admin_pw)
            auth.set_password(db, "scorekeeper", sk_pw)
            db.commit()
            session["role"] = "admin"
            return redirect(url_for("index"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if not auth.setup_done(db):
        return redirect(url_for("setup"))
    error = None
    nxt = request.args.get("next") or url_for("index")
    if request.method == "POST":
        role = request.form.get("role")
        if role == "viewer":
            session["role"] = "viewer"
            return redirect(nxt)
        if role in auth.ROLES:
            if auth.check_password(db, role, request.form.get("password", "")):
                session["role"] = role
                return redirect(nxt)
            error = "Wrong password."
        else:
            error = "Pick a login type."
    return render_template("login.html", error=error, next=nxt)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
@admin_required
def create_season():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO seasons (name) VALUES (?)", (name,))
        db.commit()
    return redirect(url_for("index"))


@app.route("/season/<int:season_id>/export")
@admin_required
def export_season(season_id):
    s = _season_or_404(season_id)
    import re as _re
    data = season_io.export_season(get_db(), season_id)
    from flask import Response
    import json as _json
    fname = _re.sub(r"[^A-Za-z0-9._-]+", "_", s["name"]).strip("_") or "season"
    return Response(
        _json.dumps(data, indent=1),
        mimetype="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=axeleague-{fname}.json"})


@app.post("/seasons/import")
@admin_required
def import_season():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("index", e="Choose a season file first."))
    db = get_db()
    try:
        data = season_io.loads(f.read())
        sid = season_io.import_season(db, data)
        ach.recompute(db, sid)
    except season_io.SeasonImportError as e:
        db.rollback()
        return redirect(url_for("index", e=str(e)))
    db.commit()
    return redirect(url_for("season_home", season_id=sid))


@app.post("/season/<int:season_id>/rename")
@admin_required
def rename_season(season_id):
    _season_or_404(season_id)
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("UPDATE seasons SET name=? WHERE id=?", (name, season_id))
        db.commit()
    return redirect(url_for("season_home", season_id=season_id))


@app.post("/season/<int:season_id>/delete")
@admin_required
def delete_season(season_id):
    _season_or_404(season_id)
    db = get_db()
    db.execute("DELETE FROM seasons WHERE id=?", (season_id,))
    db.commit()
    return redirect(url_for("index"))


@app.post("/season/<int:season_id>/schedule/reset")
@admin_required
def reset_schedule(season_id):
    """Delete the regular-season schedule (and its scores, and any playoff
    bracket built from it) so a new schedule — including new teams — can be
    generated."""
    _season_or_404(season_id)
    db = get_db()
    db.execute("DELETE FROM matches WHERE season_id=?", (season_id,))
    ach.recompute(db, season_id)
    db.commit()
    return redirect(url_for("schedule_page", season_id=season_id))


@app.post("/season/<int:season_id>/teams/copy")
@admin_required
def copy_teams(season_id):
    _season_or_404(season_id)
    db = get_db()
    try:
        src_id = int(request.form.get("source_season_id", ""))
    except ValueError:
        abort(400)
    src_season = db.execute("SELECT * FROM seasons WHERE id=?",
                            (src_id,)).fetchone()
    if not src_season or src_id == season_id:
        abort(400)
    existing = {r["name"].lower() for r in db.execute(
        "SELECT name FROM teams WHERE season_id=?", (season_id,)).fetchall()}
    src_teams = db.execute(
        "SELECT * FROM teams WHERE season_id=? ORDER BY name",
        (src_id,)).fetchall()
    copied_teams = copied_players = skipped = 0
    for t in src_teams:
        if t["name"].lower() in existing:
            skipped += 1
            continue
        cur = db.execute("INSERT INTO teams (season_id, name) VALUES (?, ?)",
                         (season_id, t["name"]))
        new_tid = cur.lastrowid
        copied_teams += 1
        for p in db.execute("SELECT name FROM players WHERE team_id=?"
                            " ORDER BY name", (t["id"],)).fetchall():
            db.execute("INSERT INTO players (team_id, name) VALUES (?, ?)",
                       (new_tid, p["name"]))
            copied_players += 1
    db.commit()
    msg = (f"Copied {copied_teams} team{'s' if copied_teams != 1 else ''} "
           f"and {copied_players} player{'s' if copied_players != 1 else ''} "
           f"from {src_season['name']}.")
    if skipped:
        msg += (f" Skipped {skipped} team{'s' if skipped != 1 else ''} "
                "already in this season.")
    return redirect(url_for("teams_page", season_id=season_id, m=msg))


@app.post("/player/<int:player_id>/rename")
@admin_required
def rename_player(player_id):
    db = get_db()
    p = db.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
    if not p:
        abort(404)
    t = db.execute("SELECT * FROM teams WHERE id=?", (p["team_id"],)).fetchone()
    name = request.form.get("name", "").strip()
    if name:
        db.execute("UPDATE players SET name=? WHERE id=?", (name, player_id))
        db.commit()
    return redirect(url_for("teams_page", season_id=t["season_id"]))


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
    other_seasons = db.execute(
        """SELECT s.id, s.name, COUNT(t.id) AS n_teams
           FROM seasons s JOIN teams t ON t.season_id=s.id
           WHERE s.id != ? GROUP BY s.id ORDER BY s.id DESC""",
        (season_id,)).fetchall()
    return render_template("teams.html", season=s, teams=teams,
                           by_team=by_team, scheduled=scheduled,
                           other_seasons=other_seasons,
                           message=request.args.get("m"),
                           error=request.args.get("e"))


@app.post("/season/<int:season_id>/teams")
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
    dates = stats_mod.round_dates(db, season_id)
    # Group rounds that share a date; keep round order within/between groups
    groups = []  # [{date, rounds: [(round, matches)]}]
    for w in sorted(weeks):
        d = dates.get(w)
        if groups and groups[-1]["date"] == d and d is not None:
            groups[-1]["rounds"].append((w, weeks[w]))
        else:
            groups.append({"date": d, "rounds": [(w, weeks[w])]})
    max_round = max(weeks) if weeks else 0
    return render_template("schedule.html", season=s, weeks=weeks,
                           groups=groups, dates=dates, max_round=max_round)


@app.template_filter("fmt_date")
def fmt_date(iso):
    if not iso:
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%A, %b %-d, %Y")
    except ValueError:
        return iso


@app.post("/season/<int:season_id>/round_date")
@admin_required
def set_round_date(season_id):
    _season_or_404(season_id)
    try:
        rnd = int(request.form.get("round", ""))
    except ValueError:
        abort(400)
    date = request.form.get("date", "").strip()
    if request.form.get("clear"):
        date = ""
    db = get_db()
    if date:
        db.execute(
            "INSERT INTO round_dates (season_id, round, date) VALUES (?, ?, ?)"
            " ON CONFLICT(season_id, round) DO UPDATE SET date=excluded.date",
            (season_id, rnd, date))
    else:
        db.execute("DELETE FROM round_dates WHERE season_id=? AND round=?",
                   (season_id, rnd))
    db.commit()
    return redirect(url_for("schedule_page", season_id=season_id))


@app.post("/match/<int:match_id>/move")
@admin_required
def move_match(match_id):
    db = get_db()
    m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m or m["stage"] != "regular":
        abort(404)
    try:
        rnd = int(request.form.get("round", ""))
    except ValueError:
        abort(400)
    if rnd < 1:
        abort(400)
    db.execute("UPDATE matches SET week=? WHERE id=?", (rnd, match_id))
    db.commit()
    return redirect(url_for("schedule_page", season_id=m["season_id"]))


@app.post("/season/<int:season_id>/schedule/generate")
@admin_required
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
    players = stats_mod.player_season_stats(db, season_id, stage="regular")
    teams = stats_mod.team_season_stats(db, season_id, stage="regular")
    weeks, weekly = stats_mod.player_weekly_averages(db, season_id)
    # Playoff section appears once any playoff throws exist
    has_playoff_data = db.execute(
        """SELECT 1 FROM throws t
           JOIN sets st ON st.id=t.set_id
           JOIN games g ON g.id=st.game_id
           JOIN matches m ON m.id=g.match_id
           WHERE m.season_id=? AND m.stage='playoff' LIMIT 1""",
        (season_id,)).fetchone() is not None
    p_players = p_teams = None
    if has_playoff_data:
        p_players = stats_mod.player_season_stats(db, season_id, stage="playoff")
        p_players = [p for p in p_players if p["sets"]]
        p_teams = stats_mod.team_season_stats(db, season_id, stage="playoff")
    overview = stats_mod.league_overview(db, season_id)
    weekly_highs = stats_mod.weekly_high_scores(db, season_id)
    return render_template("stats.html", season=s, players=players,
                           teams=teams, weeks=weeks, weekly=weekly,
                           has_playoff_data=has_playoff_data,
                           p_players=p_players, p_teams=p_teams,
                           overview=overview, weekly_highs=weekly_highs)


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
@admin_required
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
            ach.recompute(db, season_id)
            db.commit()
        except ValueError:
            db.rollback()
    return redirect(url_for("playoffs_page", season_id=season_id))


@app.post("/season/<int:season_id>/playoffs/reset")
@admin_required
def reset_playoffs(season_id):
    _season_or_404(season_id)
    db = get_db()
    db.execute("DELETE FROM matches WHERE season_id=? AND stage='playoff'",
               (season_id,))
    ach.recompute(db, season_id)
    db.commit()
    return redirect(url_for("playoffs_page", season_id=season_id))


@app.post("/match/<int:match_id>/import")
@admin_required
def import_match_csv(match_id):
    db = get_db()
    m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        abort(404)
    if m["completed"]:
        return redirect(url_for("match_page", match_id=match_id,
                                e="This match is completed — reopen it before importing."))
    f = request.files.get("csv")
    if not f or not f.filename:
        return redirect(url_for("match_page", match_id=match_id,
                                e="Choose a CSV file first."))
    home = db.execute("SELECT name FROM teams WHERE id=?",
                      (m["home_team_id"],)).fetchone()["name"]
    away = db.execute("SELECT name FROM teams WHERE id=?",
                      (m["away_team_id"],)).fetchone()["name"]
    try:
        parsed = csv_import.parse(f.read(), home, away)
    except csv_import.ImportError_ as e:
        return redirect(url_for("match_page", match_id=match_id, e=str(e)))
    summary = csv_import.apply(db, m, parsed)
    ach.recompute(db, m["season_id"])
    db.commit()
    msg = (f"Imported {summary['throws']} throws across "
           f"{summary['sets']} set entries.")
    if summary["created_players"]:
        msg += " New players added: " + ", ".join(
            sorted(set(summary["created_players"]))) + "."
    return redirect(url_for("match_page", match_id=match_id, m=msg))


def _season_of_match(db, match_id):
    r = db.execute("SELECT season_id FROM matches WHERE id=?",
                   (match_id,)).fetchone()
    return r["season_id"] if r else None


def _season_of_set(db, set_id):
    r = db.execute(
        """SELECT m.season_id AS sid FROM sets s
           JOIN games g ON g.id=s.game_id JOIN matches m ON m.id=g.match_id
           WHERE s.id=?""", (set_id,)).fetchone()
    return r["sid"] if r else None


@app.route("/season/<int:season_id>/achievements")
def achievements_page(season_id):
    s = _season_or_404(season_id)
    rows = ach.list_achievements(get_db(), season_id)
    return render_template("achievements.html", season=s, rows=rows)


@app.route("/projector")
def projector_page():
    return render_template("projector.html")


@app.route("/api/projector")
def api_projector():
    db = get_db()
    rows = db.execute(
        """SELECT g.match_id AS mid, MAX(t.id) AS last_throw
           FROM throws t
           JOIN sets s ON s.id = t.set_id
           JOIN games g ON g.id = s.game_id
           JOIN matches m ON m.id = g.match_id
           WHERE m.completed = 0
           GROUP BY g.match_id
           ORDER BY last_throw DESC
           LIMIT 3""").fetchall()
    boards = []
    for r in rows:
        m = db.execute("SELECT * FROM matches WHERE id=?", (r["mid"],)).fetchone()
        state = scoring.compute_match_state(db, m["id"])
        season = db.execute("SELECT name FROM seasons WHERE id=?",
                            (m["season_id"],)).fetchone()
        games = state["games"]
        wins = {"home": sum(1 for g in games if g["winner"] == "home"),
                "away": sum(1 for g in games if g["winner"] == "away")}
        # current set: first incomplete in play order, else the last one
        cur = None
        for g in games:
            for s_ in g["sets"]:
                if not s_["complete"]:
                    cur = (g, s_)
                    break
            if cur:
                break
        if not cur:
            g = games[-1]
            cur = (g, g["sets"][-1])
        cg, cs = cur
        boards.append({
            "match_id": m["id"],
            "season": season["name"] if season else "",
            "stage": m["stage"],
            "home_name": state["match"]["home_team_name"],
            "away_name": state["match"]["away_team_name"],
            "wins": wins,
            "status": state["status"]["state"],
            "games": [{"number": g["number"], "home_total": g["home_total"],
                       "away_total": g["away_total"], "winner": g["winner"],
                       "complete": g["complete"]} for g in games],
            "current": {
                "game": cg["number"], "set": cs["number"],
                "home_player": cs["home_player_name"],
                "away_player": cs["away_player_name"],
                "home_total": cs["home_total"], "away_total": cs["away_total"],
                "home_throws": [t["outcome"] for t in cs["home_throws"]],
                "away_throws": [t["outcome"] for t in cs["away_throws"]],
            },
        })
    standings = None
    if boards:
        lead_season = db.execute(
            "SELECT * FROM seasons WHERE id=?",
            (db.execute("SELECT season_id FROM matches WHERE id=?",
                        (boards[0]["match_id"],)).fetchone()["season_id"],)
        ).fetchone()
        rows_ = stats_mod.standings(db, lead_season["id"])
        standings = {
            "season": lead_season["name"],
            "rows": [{"team": r["name"], "wins": r["wins"],
                      "losses": r["losses"], "bulls": r["bulls"]}
                     for r in rows_],
        }
    recent = None
    if boards:
        sid_lead = db.execute("SELECT season_id FROM matches WHERE id=?",
                              (boards[0]["match_id"],)).fetchone()["season_id"]
        recent = [
            {"icon": a["icon"], "name": a["name"], "who": a["who"],
             "who_team": a["who_team"], "detail": a["detail"]}
            for a in ach.list_achievements(db, sid_lead)[:5]
        ]
    return jsonify({"boards": boards, "standings": standings,
                    "achievements": recent})


@app.post("/match/<int:match_id>/reset")
@admin_required
def reset_match(match_id):
    db = get_db()
    m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        abort(404)
    if m["completed"] and m["stage"] == "playoff":
        return redirect(url_for(
            "match_page", match_id=match_id,
            e="This playoff match already advanced the bracket — use "
              "Reset playoffs on the Playoffs page instead."))
    set_ids = [r["id"] for r in db.execute(
        """SELECT s.id FROM sets s JOIN games g ON g.id=s.game_id
           WHERE g.match_id=?""", (match_id,)).fetchall()]
    qmarks = ",".join("?" * len(set_ids))
    db.execute(f"DELETE FROM throws WHERE set_id IN ({qmarks})", set_ids)
    db.execute(f"UPDATE sets SET home_player_id=NULL, away_player_id=NULL"
               f" WHERE id IN ({qmarks})", set_ids)
    db.execute("""UPDATE matches SET completed=0, winner_team_id=NULL,
                  sudden_death_winner_team_id=NULL WHERE id=?""", (match_id,))
    ach.recompute(db, m["season_id"])
    db.commit()
    return redirect(url_for("match_page", match_id=match_id,
                            m="Match reset — all scores and thrower "
                              "assignments cleared."))


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
@scorekeeper_required
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
            current = s[field]
            if pid is not None:
                p = db.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
                if not p or p["team_id"] != m[team_col]:
                    return _err("Player is not on that team's roster.")
            if current and pid != current:
                if pid is None:
                    n = db.execute(
                        "SELECT COUNT(*) c FROM throws WHERE set_id=?"
                        " AND player_id=?", (set_id, current)).fetchone()["c"]
                    if n:
                        return _err("This thrower has throws in the set — "
                                    "pick a replacement instead of unassigning.")
                else:
                    # re-credit the slot's existing throws to the new thrower
                    db.execute(
                        "UPDATE throws SET player_id=? WHERE set_id=?"
                        " AND player_id=?", (pid, set_id, current))
            db.execute(f"UPDATE sets SET {field}=? WHERE id=?", (pid, set_id))
    ach.recompute(db, _season_of_set(db, set_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/set/<int:set_id>/throw")
@scorekeeper_required
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
        ach.recompute(db, _season_of_set(db, set_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _err("Another scorekeeper just recorded a throw. Try again.", 409)
    return jsonify({"ok": True})


@app.post("/api/set/<int:set_id>/undo")
@scorekeeper_required
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
    ach.recompute(db, _season_of_set(db, set_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/throw/<int:throw_id>/edit")
@scorekeeper_required
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
    _sid_row = db.execute("""SELECT m.season_id AS sid FROM throws t JOIN sets s ON s.id=t.set_id JOIN games g ON g.id=s.game_id JOIN matches m ON m.id=g.match_id WHERE t.id=?""", (throw_id,)).fetchone()
    if _sid_row:
        ach.recompute(db, _sid_row["sid"])
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/sudden_death")
@scorekeeper_required
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
    ach.recompute(db, _season_of_match(db, match_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/complete")
@scorekeeper_required
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
    ach.recompute(db, _season_of_match(db, match_id))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/reopen")
@scorekeeper_required
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
    ach.recompute(db, _season_of_match(db, match_id))
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
