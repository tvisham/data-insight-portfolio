"""Scrape UIUC course + prerequisite data.

The University of Illinois exposes a public XML API used by
courses.illinois.edu. It's not always the best-documented, so the
shape below is what we've observed in practice — keep the parser
tolerant of missing fields.

Endpoints (all XML):
    {base}                          -> list of years
    {base}/{year}.xml               -> list of terms in a year
    {base}/{year}/{term}.xml        -> list of subjects in a term
    {base}/{year}/{term}/{subj}.xml -> list of courses in a subject
    {base}/{year}/{term}/{subj}/{n}.xml -> course detail (has description)

`base` defaults to https://courses.illinois.edu/cisapp/explorer/schedule
(see src/config.py).

Because the endpoint can be flaky or unreachable from CI, this module
also supports parsing a local sample file — that's what the tests use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator
from xml.etree import ElementTree as ET

from src.config import PROJECT_ROOT, settings
from src.scrapers.base import get

log = logging.getLogger(__name__)

_SAMPLE_PATH = PROJECT_ROOT / "data" / "samples" / "uiuc_cs_sample.xml"


@dataclass
class CourseRecord:
    """One row we'll persist. Deliberately flat + JSON-friendly."""

    subject: str
    number: str
    title: str
    credit_hours: str | None
    description: str | None
    term: str
    year: int
    source_url: str | None

    @property
    def course_code(self) -> str:
        return f"{self.subject} {self.number}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip() or None


def parse_course_detail_xml(
    xml_text: str, *, term: str, year: int, source_url: str | None = None
) -> CourseRecord | None:
    """Parse a single course-detail XML document into a CourseRecord.

    Returns None if the XML doesn't look like a course document.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("Bad XML from %s: %s", source_url, exc)
        return None

    # The root element is usually <ns2:course id="225" ...>.
    subject = root.attrib.get("subject") or _text(root.find("subject"))
    number = root.attrib.get("id") or _text(root.find("courseNumber"))
    title = _text(root.find("label")) or _text(root.find("title"))
    credit = _text(root.find("creditHours"))
    desc = _text(root.find("description"))
    # Fallback: some payloads put the full description under
    # <courseSectionInformation>.
    if not desc:
        desc = _text(root.find("courseSectionInformation"))

    if not subject or not number or not title:
        return None

    return CourseRecord(
        subject=subject.strip().upper(),
        number=str(number).strip(),
        title=title,
        credit_hours=credit,
        description=desc,
        term=term.lower(),
        year=int(year),
        source_url=source_url,
    )


def parse_course_list_xml(xml_text: str) -> list[tuple[str, str]]:
    """Given the subject-level XML, return [(course_number, detail_url)].

    We hand back the URL because the list document only has a link;
    the full description lives one HTTP call deeper.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    out: list[tuple[str, str]] = []
    for course in root.iter("course"):
        number = course.attrib.get("id") or _text(course.find("courseNumber"))
        href = course.attrib.get("href")
        if number and href:
            out.append((str(number), href))
    return out


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------


def _list_url(year: int, term: str, subject: str) -> str:
    return f"{settings.uiuc_base_url}/{year}/{term}/{subject}.xml"


def fetch(
    *,
    subject: str,
    term: str = "fall",
    year: int = 2026,
    limit: int | None = None,
    offline: bool = False,
) -> Iterator[CourseRecord]:
    """Yield CourseRecords for a subject/term/year.

    Set `offline=True` (or leave the network unavailable) to read from
    the bundled sample file. This keeps the tutorial runnable without
    an internet connection.
    """
    if offline:
        yield from _iter_from_sample(term=term, year=year, limit=limit)
        return

    subject = subject.upper()
    list_url = _list_url(year, term.lower(), subject)
    try:
        list_resp = get(list_url)
    except Exception as exc:
        log.warning(
            "Live fetch failed (%s). Falling back to sample data.", exc
        )
        yield from _iter_from_sample(term=term, year=year, limit=limit)
        return

    course_links = parse_course_list_xml(list_resp.text)
    if limit is not None:
        course_links = course_links[:limit]

    for number, href in course_links:
        try:
            detail = get(href)
        except Exception as exc:
            log.warning("Skipping %s %s: %s", subject, number, exc)
            continue

        rec = parse_course_detail_xml(
            detail.text, term=term, year=year, source_url=href
        )
        if rec is not None:
            yield rec


def _iter_from_sample(
    *, term: str, year: int, limit: int | None
) -> Iterator[CourseRecord]:
    if not _SAMPLE_PATH.exists():
        log.error("Offline sample not found at %s", _SAMPLE_PATH)
        return
    tree = ET.parse(_SAMPLE_PATH)
    root = tree.getroot()
    count = 0
    for course in root.iter("course"):
        # Serialize back to text and reuse the same parser so the sample
        # exercises the same code path as the live payload.
        rec = parse_course_detail_xml(
            ET.tostring(course, encoding="unicode"),
            term=term,
            year=year,
            source_url=str(_SAMPLE_PATH),
        )
        if rec is None:
            continue
        yield rec
        count += 1
        if limit is not None and count >= limit:
            return


def to_dicts(records: Iterable[CourseRecord]) -> list[dict]:
    """Convenience for scripts / notebooks."""
    return [r.__dict__ for r in records]
