-- SQLite schema for the demo. Kept intentionally flat and readable.
--
-- Design notes:
-- * Every source-fed table has an ingested_at column so we can spot
--   stale rows during EDA.
-- * The "rejects" table stores rows that failed validation, along
--   with the reason string. That's how we avoid silent data loss.
-- * prereq_edges uses a group_id to model OR-groups: rows sharing the
--   same (target_course, group_id) satisfy the prereq if ANY of them
--   is completed. Different group_ids under the same target are ANDed.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS courses (
    subject       TEXT    NOT NULL,
    number        TEXT    NOT NULL,
    course_code   TEXT    NOT NULL, -- "CS 225"; denormalized for easy lookup
    title         TEXT    NOT NULL,
    credit_hours  TEXT,
    description   TEXT,
    term          TEXT    NOT NULL,
    year          INTEGER NOT NULL,
    source_url    TEXT,
    ingested_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (subject, number, term, year)
);

CREATE INDEX IF NOT EXISTS idx_courses_code ON courses(course_code);

CREATE TABLE IF NOT EXISTS prereq_edges (
    target_course TEXT    NOT NULL, -- "CS 421"
    prereq_course TEXT    NOT NULL, -- "CS 233"
    group_id      INTEGER NOT NULL, -- OR-group; different groups AND together
    term          TEXT    NOT NULL,
    year          INTEGER NOT NULL,
    ingested_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (target_course, prereq_course, group_id, term, year)
);

CREATE INDEX IF NOT EXISTS idx_prereq_target ON prereq_edges(target_course);

CREATE TABLE IF NOT EXISTS works (
    openalex_id     TEXT PRIMARY KEY,
    title           TEXT,
    publication_year INTEGER,
    cited_by_count  INTEGER,
    doi             TEXT,
    authors_json    TEXT, -- JSON array; ok for demo scale
    host_venue      TEXT,
    concepts_json   TEXT,
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_works_year ON works(publication_year);

CREATE TABLE IF NOT EXISTS rejects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,  -- 'courses' | 'works'
    reason      TEXT NOT NULL,
    row_json    TEXT NOT NULL,
    rejected_at TEXT NOT NULL DEFAULT (datetime('now'))
);
