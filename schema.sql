PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE seasons (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE teams (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    week INTEGER,
    home_team_id INTEGER REFERENCES teams(id),
    away_team_id INTEGER REFERENCES teams(id),
    stage TEXT NOT NULL DEFAULT 'regular',      -- 'regular' | 'playoff'
    bracket TEXT,                               -- 'W' | 'L' | 'GF'
    bracket_round INTEGER,
    bracket_slot INTEGER,
    winner_to_match INTEGER,
    winner_to_pos INTEGER,                      -- 1 = home slot, 2 = away slot
    loser_to_match INTEGER,
    loser_to_pos INTEGER,
    sudden_death_winner_team_id INTEGER REFERENCES teams(id),
    completed INTEGER NOT NULL DEFAULT 0,
    winner_team_id INTEGER REFERENCES teams(id)
);

CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    game_number INTEGER NOT NULL,
    UNIQUE (match_id, game_number)
);

CREATE TABLE sets (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    set_number INTEGER NOT NULL,
    home_player_id INTEGER REFERENCES players(id),
    away_player_id INTEGER REFERENCES players(id),
    UNIQUE (game_id, set_number)
);

CREATE TABLE throws (
    id INTEGER PRIMARY KEY,
    set_id INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id),
    throw_number INTEGER NOT NULL,              -- 1..10
    outcome TEXT NOT NULL,                      -- '1'..'5','B','KH','KD','KM','D','M'
    points INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (set_id, player_id, throw_number)
);

CREATE INDEX idx_throws_set ON throws(set_id);
CREATE INDEX idx_matches_season ON matches(season_id);
