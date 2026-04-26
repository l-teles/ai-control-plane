-- Full-text-search virtual table for the sessions list.
-- Indexes the session summary + cwd + model + first user-message preview
-- so users can search "the session where I worked on the auth refactor"
-- across every tool's history.

CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    session_id UNINDEXED,
    summary,
    cwd,
    model,
    first_user_message,
    tokenize = 'porter unicode61 remove_diacritics 1'
);
