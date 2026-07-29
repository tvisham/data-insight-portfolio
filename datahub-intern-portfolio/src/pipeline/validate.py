"""Row-level validation.

Instead of raising on bad rows we return a `ValidationResult` — good rows
go to the loader, bad rows go to a `rejects` table with the reason.
This matches how DataHub-style teams actually work: never silently drop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from src.scrapers.uiuc_courses import CourseRecord
from src.scrapers.openalex import WorkRecord


_SUBJECT_RE = re.compile(r"^[A-Z]{2,6}$")
_NUMBER_RE = re.compile(r"^[0-9]{3}[A-Z]?$")


@dataclass
class ValidationResult:
    ok: list[object] = field(default_factory=list)
    rejects: list[tuple[object, str]] = field(default_factory=list)


def _reject(res: ValidationResult, row: object, reason: str) -> None:
    res.rejects.append((row, reason))


def validate_courses(records: Iterable[CourseRecord]) -> ValidationResult:
    res = ValidationResult()
    for r in records:
        if not _SUBJECT_RE.match(r.subject):
            _reject(res, r, f"bad subject code: {r.subject!r}")
            continue
        if not _NUMBER_RE.match(r.number):
            _reject(res, r, f"bad course number: {r.number!r}")
            continue
        if not r.title:
            _reject(res, r, "missing title")
            continue
        if r.year < 2000 or r.year > 2100:
            _reject(res, r, f"implausible year: {r.year}")
            continue
        if r.term not in {"fall", "spring", "summer", "winter"}:
            _reject(res, r, f"unknown term: {r.term!r}")
            continue
        res.ok.append(r)
    return res


def validate_works(records: Iterable[WorkRecord]) -> ValidationResult:
    res = ValidationResult()
    for r in records:
        if not r.openalex_id:
            _reject(res, r, "missing openalex_id")
            continue
        if r.publication_year is not None and (
            r.publication_year < 1800 or r.publication_year > 2100
        ):
            _reject(res, r, f"implausible year: {r.publication_year}")
            continue
        if r.cited_by_count is not None and r.cited_by_count < 0:
            _reject(res, r, "negative cited_by_count")
            continue
        res.ok.append(r)
    return res
