"""Tests for the LLM tool-call agent.

The LLM is mocked via the `transport` parameter, so these tests never
hit the network and don't need OPENAI_API_KEY.
"""

from __future__ import annotations

import json

import pytest

from src.agent.llm_agent import LLMError, _validate_sql, llm_answer


def _fake_tool_response(sql: str, explanation: str = "why") -> dict:
    """Build an OpenAI-shaped response that returns the given SQL."""
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "query_datahub",
                                "arguments": json.dumps(
                                    {"sql": sql, "explanation": explanation}
                                ),
                            },
                        }
                    ]
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------


def test_validate_accepts_plain_select():
    out = _validate_sql("SELECT 1")
    assert out == "SELECT 1"


def test_validate_rejects_non_select():
    with pytest.raises(LLMError):
        _validate_sql("DROP TABLE courses")


def test_validate_rejects_multiple_statements():
    with pytest.raises(LLMError):
        _validate_sql("SELECT 1; SELECT 2")


def test_validate_rejects_forbidden_keyword():
    with pytest.raises(LLMError):
        _validate_sql(
            "WITH x AS (SELECT 1) INSERT INTO courses VALUES (1)"
        )


def test_validate_rejects_unknown_table():
    with pytest.raises(LLMError):
        _validate_sql("SELECT * FROM secrets")


def test_validate_allows_join_on_allowed_tables():
    sql = (
        "SELECT c.course_code, p.prereq_course "
        "FROM courses c JOIN prereq_edges p ON c.course_code = p.target_course "
        "LIMIT 5"
    )
    assert _validate_sql(sql) == sql


def test_validate_trims_trailing_semicolon():
    assert _validate_sql("SELECT 1;") == "SELECT 1"


# ---------------------------------------------------------------------------
# End-to-end with a mocked transport (uses tmp_db from conftest)
# ---------------------------------------------------------------------------


def _seed_courses(tmp_db):
    """Seed the ephemeral DB from the offline sample."""
    from src.db.database import upsert_courses
    from src.pipeline.clean import clean_course, dedupe_courses
    from src.pipeline.validate import validate_courses
    from src.scrapers.uiuc_courses import fetch

    raw = list(fetch(subject="CS", term="fall", year=2026, offline=True))
    cleaned = dedupe_courses(clean_course(r) for r in raw)
    ok = validate_courses(cleaned).ok
    upsert_courses(ok)  # type: ignore[arg-type]


def test_llm_answer_runs_generated_sql(tmp_db):
    _seed_courses(tmp_db)
    fake = lambda payload: _fake_tool_response(  # noqa: E731
        "SELECT course_code FROM courses WHERE subject = 'CS' ORDER BY course_code LIMIT 3"
    )
    result = llm_answer("give me 3 CS courses", transport=fake)
    assert result["source"] == "llm"
    assert result["sql"].startswith("SELECT")
    assert len(result["rows"]) == 3
    assert "course_code" in result["rows"][0]


def test_llm_answer_surfaces_validation_error(tmp_db):
    fake = lambda payload: _fake_tool_response("DELETE FROM courses")  # noqa: E731
    with pytest.raises(LLMError):
        llm_answer("delete everything", transport=fake)


def test_llm_answer_requires_tool_call(tmp_db):
    def no_tool_call(_payload):
        return {"choices": [{"message": {"content": "sorry, I refuse"}}]}

    with pytest.raises(LLMError):
        llm_answer("hi", transport=no_tool_call)


# ---------------------------------------------------------------------------
# Fallover: rule-based -> LLM via answer_question
# ---------------------------------------------------------------------------


def test_rule_based_still_wins_when_it_matches(tmp_db, monkeypatch):
    _seed_courses(tmp_db)
    # Even with a "key set", a matching template shouldn't hit the LLM.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Force settings reload so the change is visible.
    from importlib import reload
    from src import config as cfg
    reload(cfg)

    def boom(_payload):
        raise AssertionError("LLM should not have been called")

    monkeypatch.setattr(
        "src.agent.llm_agent._default_transport", boom
    )

    from src.agent.nl_to_sql import answer_question
    result = answer_question("prereqs for CS 225")
    assert result["source"] == "rules"


def test_fallover_hits_llm_when_no_template_matches(tmp_db, monkeypatch):
    _seed_courses(tmp_db)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from importlib import reload
    from src import config as cfg
    reload(cfg)

    def fake(_payload):
        return _fake_tool_response(
            "SELECT COUNT(*) AS n FROM courses",
            explanation="Total course count.",
        )

    monkeypatch.setattr(
        "src.agent.llm_agent._default_transport", fake
    )

    from src.agent.nl_to_sql import answer_question
    result = answer_question("random freeform question the rules don't cover")
    assert result["source"] == "llm"
    assert result["rows"][0]["n"] > 0


def test_fallover_surfaces_original_hint_when_no_key(tmp_db, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from importlib import reload
    from src import config as cfg
    reload(cfg)

    from src.agent.nl_to_sql import answer_question
    with pytest.raises(ValueError) as exc_info:
        answer_question("random freeform question the rules don't cover")
    # The rule-based hint (with example prompts) should be preserved.
    assert "prereqs for" in str(exc_info.value)
