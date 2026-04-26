-- Rebuild the session FTS index with a `content` column so search
-- actually covers the conversation, not just metadata + first user
-- message. FTS5 doesn't support ``ALTER TABLE … ADD COLUMN`` on virtual
-- tables, so we drop and recreate. The cache layer repopulates from the
-- canonical ``sessions`` table on next build / refresh.

DROP TABLE IF EXISTS sessions_fts;

CREATE VIRTUAL TABLE sessions_fts USING fts5(
    session_id UNINDEXED,
    summary,
    cwd,
    model,
    first_user_message,
    content,
    tokenize = 'porter unicode61 remove_diacritics 1'
);
