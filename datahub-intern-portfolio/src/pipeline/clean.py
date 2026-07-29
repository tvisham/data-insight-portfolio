"""Row-level cleaning. Pure functions, no I/O.

Kept small on purpose — most of what "cleaning" means for this dataset
is trimming whitespace and coercing a couple of numeric columns.
"""

from __future__ import annotations

import re
from typing import Iterable

from src.scrapers.uiuc_courses import CourseRecord
from src.scrapers.openalex import WorkRecord


_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str | None) -> str | None:
    if s is None:
        return None
    return _WS_RE.sub(" ", s).strip() or None


def clean_course(rec: CourseRecord) -> CourseRecord:
    """Return a new CourseRecord with normalized fields."""
    return CourseRecord(
        subject=rec.subject.strip().upper(),
        number=rec.number.strip(),
        title=_norm_ws(rec.title) or "",
        credit_hours=_norm_ws(rec.credit_hours),
        description=_norm_ws(rec.description),
        term=rec.term.strip().lower(),
        year=int(rec.year),
        source_url=rec.source_url,
    )


def dedupe_courses(records: Iterable[CourseRecord]) -> list[CourseRecord]:
    """Keep the last-seen record for each (subject, number, term, year)."""
    seen: dict[tuple[str, str, str, int], CourseRecord] = {}
    for r in records:
        seen[(r.subject, r.number, r.term, r.year)] = r
    return list(seen.values())


def clean_work(rec: WorkRecord) -> WorkRecord:
    """Trim strings on a WorkRecord. Lists are already normalized upstream."""
    return WorkRecord(
        openalex_id=rec.openalex_id.strip(),
        title=_norm_ws(rec.title),
        publication_year=rec.publication_year,
        cited_by_count=rec.cited_by_count,
        doi=(rec.doi or "").strip() or None,
        authors=[_norm_ws(a) or "" for a in rec.authors if a],
        host_venue=_norm_ws(rec.host_venue),
        concepts=[_norm_ws(c) or "" for c in rec.concepts if c],
    )
