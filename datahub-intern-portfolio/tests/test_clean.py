"""Tests for cleaning + validation."""

from src.pipeline.clean import clean_course, dedupe_courses
from src.pipeline.validate import validate_courses
from src.scrapers.uiuc_courses import CourseRecord


def _rec(**over) -> CourseRecord:
    defaults = dict(
        subject="cs",
        number="225",
        title="  Data  Structures  ",
        credit_hours=" 4 hours ",
        description=" Prerequisite: CS 173. ",
        term="Fall",
        year=2026,
        source_url=None,
    )
    defaults.update(over)
    return CourseRecord(**defaults)


def test_clean_normalizes_whitespace_and_case():
    c = clean_course(_rec())
    assert c.subject == "CS"
    assert c.title == "Data Structures"
    assert c.credit_hours == "4 hours"
    assert c.term == "fall"


def test_dedupe_keeps_last_seen():
    a = _rec(title="old")
    b = _rec(title="new")
    out = dedupe_courses([a, b])
    assert len(out) == 1
    assert out[0].title == "new"


def test_validate_accepts_good_row():
    good = clean_course(_rec())
    result = validate_courses([good])
    assert len(result.ok) == 1
    assert result.rejects == []


def test_validate_rejects_bad_subject():
    bad = clean_course(_rec(subject="cs1"))  # digits not allowed
    result = validate_courses([bad])
    assert result.ok == []
    assert len(result.rejects) == 1
    assert "subject" in result.rejects[0][1]


def test_validate_rejects_bad_number():
    bad = clean_course(_rec(number="22"))  # too short
    result = validate_courses([bad])
    assert result.ok == []
    assert "number" in result.rejects[0][1]


def test_validate_rejects_bad_term():
    bad = clean_course(_rec(term="quarter"))
    result = validate_courses([bad])
    assert result.ok == []
    assert "term" in result.rejects[0][1]
