-- Track each session's source file path and modification time so the cache
-- can be refreshed incrementally instead of fully rebuilt.

ALTER TABLE sessions ADD COLUMN source_path  TEXT;
ALTER TABLE sessions ADD COLUMN source_mtime REAL;

CREATE INDEX IF NOT EXISTS idx_sessions_source_path ON sessions(source_path);
