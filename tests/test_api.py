"""End-to-end-ish tests: run the offline pipeline against the bundled
sample XML, then hit the FastAPI app in-process with TestClient.

Every test uses the `tmp_db` fixture so we never touch the developer's
real datahub.db.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _seed(tmp_db):
    """Run scrape → clean → validate → upsert against the offline sample."""
    from src.db.database import upsert_courses
    from src.pipeline.clean import clean_course, dedupe_courses
    from src.pipeline.validate import validate_courses
    from src.scrapers.uiuc_courses import fetch

    raw = list(fetch(subject="CS", term="fall", year=2026, offline=True))
    assert raw, "sample fixture should yield at least one record"

    cleaned = dedupe_courses(clean_course(r) for r in raw)
    result = validate_courses(cleaned)
    upsert_courses(result.ok)  # type: ignore[arg-type]
    return len(result.ok)


def test_health(tmp_db):
    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_courses_after_seed(tmp_db):
    n = _seed(tmp_db)
    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/courses", params={"subject": "CS", "limit": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == n
    codes = {row["course_code"] for row in body}
    assert "CS 225" in codes


def test_prereqs_endpoint(tmp_db):
    _seed(tmp_db)
    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/prereqs/CS/225")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_prereqs"] is True
    prereqs = {e["prereq_course"] for e in body["edges"]}
    # From the sample: "CS 125 or CS 128; CS 173"
    assert {"CS 125", "CS 128", "CS 173"}.issubset(prereqs)


def test_ask_endpoint_prereqs(tmp_db):
    _seed(tmp_db)
    from src.api.main import app

    client = TestClient(app)
    resp = client.post("/ask", json={"question": "prereqs for CS 225"})
    assert resp.status_code == 200
    body = resp.json()
    assert "SELECT" in body["sql"].upper()
    prereqs = {row["prereq_course"] for row in body["rows"]}
    assert "CS 173" in prereqs


def test_ask_endpoint_unknown_question_returns_422(tmp_db):
    from src.api.main import app

    client = TestClient(app)
    resp = client.post("/ask", json={"question": "what is the meaning of life"})
    assert resp.status_code == 422
