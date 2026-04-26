-- Initial schema for the AI Control Plane cache.
-- Captures the schema previously declared inline in db.py (cache_meta version 3).

CREATE TABLE IF NOT EXISTS cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id       TEXT PRIMARY KEY,
    source   TEXT NOT NULL,
    uuid     TEXT NOT NULL,
    summary  TEXT,
    created  TEXT,
    cwd      TEXT,
    model    TEXT,
    input_tokens          INTEGER DEFAULT 0,
    output_tokens         INTEGER DEFAULT 0,
    cache_read_tokens     INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    estimated_cost        REAL DEFAULT 0,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    encoded_name          TEXT PRIMARY KEY,
    path                  TEXT,
    name                  TEXT,
    session_count         INTEGER DEFAULT 0,
    memory_file_count     INTEGER DEFAULT 0,
    last_cost             REAL,
    last_session_id       TEXT,
    last_input_tokens     INTEGER,
    last_output_tokens    INTEGER,
    has_trust_accepted    INTEGER DEFAULT 0,
    onboarding_seen_count INTEGER DEFAULT 0,
    metadata_json         TEXT
);

CREATE TABLE IF NOT EXISTS project_memory (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_encoded_name  TEXT NOT NULL REFERENCES projects(encoded_name),
    filename              TEXT NOT NULL,
    content               TEXT
);

CREATE TABLE IF NOT EXISTS tool_configs (
    tool        TEXT PRIMARY KEY,
    config_json TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_cwd     ON sessions(cwd);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created);
