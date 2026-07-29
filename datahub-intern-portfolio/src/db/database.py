"""Thin SQLite wrapper.

Not a full ORM. The point is to keep SQL visible and honest — a
reviewer can read the schema in schema.sql, read the queries here,
and understand what the API returns.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from src.config import settings
from src.pipeline.prereq_parser import parse_prereq_string
from src.scrapers.openalex import WorkRecord
from src.scrapers.uiuc_courses import CourseRecord

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path | None = None) -> None:
    """Create tables if they don't exist. Idempotent."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(sql)


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------


def upsert_courses(
    records: Iterable[CourseRecord], db_path: Path | None = None
) -> dict:
    """Insert or update rows in `courses` and rebuild prereq_edges for them.

    Returns a small summary dict for logging: {"upserted": N, "edges": N}.
    """
    course_rows = list(records)
    edge_rows: list[tuple] = []
    for r in course_rows:
        parsed = parse_prereq_string(r.description, target_course=r.course_code)
        for target, prereq, gid in parsed.edges:
            edge_rows.append((target, prereq, gid, r.term, r.year))

    with connect(db_path) as conn:
        for r in course_rows:
            conn.execute(
                """
                INSERT INTO courses
                    (subject, number, course_code, title, credit_hours,
                     description, term, year, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject, number, term, year) DO UPDATE SET
                    title=excluded.title,
                    credit_hours=excluded.credit_hours,
                    description=excluded.description,
                    source_url=excluded.source_url,
                    ingested_at=datetime('now')
                """,
                (
                    r.subject,
                    r.number,
                    r.course_code,
                    r.title,
                    r.credit_hours,
                    r.description,
                    r.term,
                    r.year,
                    r.source_url,
                ),
            )

        # Rebuild edges for these courses so re-runs don't accumulate stale
        # prereqs. Only touches rows we're re-ingesting.
        for r in course_rows:
            conn.execute(
                "DELETE FROM prereq_edges WHERE target_course = ? "
                "AND term = ? AND year = ?",
                (r.course_code, r.term, r.year),
            )
        conn.executemany(
            "INSERT OR IGNORE INTO prereq_edges "
            "(target_course, prereq_course, group_id, term, year) "
            "VALUES (?, ?, ?, ?, ?)",
            edge_rows,
        )

    return {"upserted": len(course_rows), "edges": len(edge_rows)}


def upsert_works(
    records: Iterable[WorkRecord], db_path: Path | None = None
) -> dict:
    rows = list(records)
    with connect(db_path) as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO works
                    (openalex_id, title, publication_year, cited_by_count,
                     doi, authors_json, host_venue, concepts_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(openalex_id) DO UPDATE SET
                    title=excluded.title,
                    publication_year=excluded.publication_year,
                    cited_by_count=excluded.cited_by_count,
                    doi=excluded.doi,
                    authors_json=excluded.authors_json,
                    host_venue=excluded.host_venue,
                    concepts_json=excluded.concepts_json,
                    ingested_at=datetime('now')
                """,
                (
                    r.openalex_id,
                    r.title,
                    r.publication_year,
                    r.cited_by_count,
                    r.doi,
                    json.dumps(r.authors),
                    r.host_venue,
                    json.dumps(r.concepts),
                ),
            )
    return {"upserted": len(rows)}


def record_rejects(
    source: str,
    rejects: Iterable[tuple[object, str]],
    db_path: Path | None = None,
) -> int:
    """Persist rows that failed validation, with the reason."""
    n = 0
    with connect(db_path) as conn:
        for row, reason in rejects:
            # dataclasses have __dict__; fall back to str() for anything odd.
            try:
                payload = json.dumps(row.__dict__, default=str)
            except AttributeError:
                payload = json.dumps(str(row))
            conn.execute(
                "INSERT INTO rejects (source, reason, row_json) VALUES (?, ?, ?)",
                (source, reason, payload),
            )
            n += 1
    return n


# ---------------------------------------------------------------------------
# Reads used by the API
# ---------------------------------------------------------------------------


def list_courses(
    subject: str | None = None, limit: int = 100
) -> list[dict]:
    sql = "SELECT * FROM courses"
    params: tuple = ()
    if subject:
        sql += " WHERE subject = ?"
        params = (subject.upper(),)
    sql += " ORDER BY subject, number LIMIT ?"
    params = (*params, limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_prereqs(subject: str, number: str) -> list[dict]:
    code = f"{subject.upper()} {number}"
    with connect() as conn:
        rows = conn.execute(
            "SELECT prereq_course, group_id, term, year "
            "FROM prereq_edges WHERE target_course = ? "
            "ORDER BY year DESC, term, group_id",
            (code,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_works(
    year_from: int | None = None, year_to: int | None = None, limit: int = 100
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if year_from is not None:
        clauses.append("publication_year >= ?")
        params.append(year_from)
    if year_to is not None:
        clauses.append("publication_year <= ?")
        params.append(year_to)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        # COALESCE keeps NULL citations at the bottom on older SQLite
        # builds that don't support "NULLS LAST".
        rows = conn.execute(
            f"SELECT * FROM works{where} "
            "ORDER BY COALESCE(cited_by_count, -1) DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = json.loads(d.pop("authors_json") or "[]")
        d["concepts"] = json.loads(d.pop("concepts_json") or "[]")
        out.append(d)
    return out
