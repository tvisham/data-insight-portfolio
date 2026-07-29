"""A very small "agent" that turns natural-language questions into
parameterized SQL over our SQLite tables.

Why rule-based? Two reasons that match the DataHub job description:
    1. It gives us a deterministic baseline to evaluate a real LLM
       agent against later.
    2. It keeps the tests hermetic — no network, no API keys.

The seams are set up so you can plug in an LLM tool-call backend by
implementing `LLMBackend.plan(question)` and wiring it into
`answer_question`. See the comment at the bottom for what that would
look like.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Callable

from src.db.database import connect


@dataclass
class QueryPlan:
    sql: str
    params: tuple
    explanation: str


# ---------------------------------------------------------------------------
# Templates. Each is (matcher, planner) — matcher returns captured groups
# or None; planner turns those groups into a QueryPlan.
# ---------------------------------------------------------------------------


def _plan_prereqs_of(subject: str, number: str) -> QueryPlan:
    code = f"{subject.upper()} {number}"
    return QueryPlan(
        sql=(
            "SELECT prereq_course, group_id FROM prereq_edges "
            "WHERE target_course = ? ORDER BY group_id"
        ),
        params=(code,),
        explanation=f"Prerequisite edges for {code}.",
    )


def _plan_courses_that_require(subject: str, number: str) -> QueryPlan:
    code = f"{subject.upper()} {number}"
    return QueryPlan(
        sql=(
            "SELECT DISTINCT target_course FROM prereq_edges "
            "WHERE prereq_course = ? ORDER BY target_course"
        ),
        params=(code,),
        explanation=f"Courses that list {code} as a prerequisite.",
    )


def _plan_count_by_subject(subject: str) -> QueryPlan:
    return QueryPlan(
        sql="SELECT COUNT(*) AS n FROM courses WHERE subject = ?",
        params=(subject.upper(),),
        explanation=f"Number of courses in subject {subject.upper()}.",
    )


def _plan_top_cited(n: int) -> QueryPlan:
    return QueryPlan(
        sql=(
            "SELECT title, publication_year, cited_by_count FROM works "
            "ORDER BY COALESCE(cited_by_count, -1) DESC LIMIT ?"
        ),
        params=(n,),
        explanation=f"Top {n} works by citation count.",
    )


# Matcher = str -> (planner_args | None). Each returns None if the
# question doesn't fit the template.
Matcher = Callable[[str], tuple | None]
Planner = Callable[..., QueryPlan]


_TEMPLATES: list[tuple[Matcher, Planner]] = [
    (
        lambda q: (
            m.groups()
            if (
                m := re.search(
                    r"(?:prereq|prerequisite)s?\s+(?:for|of)\s+"
                    r"([A-Z]{2,6})\s?([0-9]{3}[A-Z]?)",
                    q,
                    flags=re.IGNORECASE,
                )
            )
            else None
        ),
        _plan_prereqs_of,
    ),
    (
        lambda q: (
            m.groups()
            if (
                m := re.search(
                    r"(?:which|what)\s+courses?\s+(?:require|need|list)\s+"
                    r"([A-Z]{2,6})\s?([0-9]{3}[A-Z]?)",
                    q,
                    flags=re.IGNORECASE,
                )
            )
            else None
        ),
        _plan_courses_that_require,
    ),
    (
        lambda q: (
            (m.group(1),)
            if (
                m := re.search(
                    r"how many (?:courses|classes) (?:are there )?in ([A-Z]{2,6})",
                    q,
                    flags=re.IGNORECASE,
                )
            )
            else None
        ),
        _plan_count_by_subject,
    ),
    (
        lambda q: (
            (int(m.group(1)),)
            if (
                m := re.search(
                    r"top\s+(\d{1,3})\s+(?:most\s+)?cited",
                    q,
                    flags=re.IGNORECASE,
                )
            )
            else None
        ),
        _plan_top_cited,
    ),
]


def plan(question: str) -> QueryPlan:
    """Route `question` to the first template that matches."""
    for matcher, planner in _TEMPLATES:
        args = matcher(question)
        if args is not None:
            return planner(*args)
    raise ValueError(
        "I don't know how to answer that yet. Try:\n"
        "  - 'prereqs for CS 421'\n"
        "  - 'which courses require CS 225'\n"
        "  - 'how many courses in CS'\n"
        "  - 'top 5 most cited works'"
    )


def _run(plan_: QueryPlan) -> list[dict]:
    with connect() as conn:
        rows: list[sqlite3.Row] = conn.execute(plan_.sql, plan_.params).fetchall()
    return [dict(r) for r in rows]


def answer_question(question: str) -> dict:
    """Public entry point used by the API.

    Strategy: try the rule-based planner first (cheap, deterministic,
    tested). If it can't match the question, fall over to the LLM
    tool-call agent in :mod:`src.agent.llm_agent`. If the LLM also
    fails (or isn't configured), surface the original rule-based
    error so the caller sees a helpful "try one of these templates"
    message.
    """
    try:
        p = plan(question)
    except ValueError as rule_err:
        # Import lazily so the rule-based path has no LLM dependency.
        from src.agent.llm_agent import LLMError, llm_answer
        from src.config import settings

        if not settings.openai_api_key:
            # No LLM available — surface the rule-based hint verbatim.
            raise
        try:
            return llm_answer(question)
        except LLMError as llm_err:
            # Chain both: the user sees "LLM failed: ...; also, ...".
            raise ValueError(
                f"LLM fallback failed ({llm_err}); rule-based path: {rule_err}"
            ) from llm_err

    return {
        "question": question,
        "explanation": p.explanation,
        "sql": p.sql,
        "params": list(p.params),
        "rows": _run(p),
        "source": "rules",
    }


# ---------------------------------------------------------------------------
# LLM fallover
# ---------------------------------------------------------------------------
# See src/agent/llm_agent.py for the OpenAI-compatible tool-call agent.
# It complements the rule-based planner above: rules run first, and the
# LLM only handles what the rules can't match. The rule-based path
# stays as the deterministic baseline you can evaluate the LLM against.
