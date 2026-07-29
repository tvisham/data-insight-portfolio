"""FastAPI service that exposes the DataHub demo tables.

Endpoints are deliberately small and read-only. Auth is out of scope
for a demo — see ARCHITECTURE.md "Non-goals".

Run with:
    uvicorn src.api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the API and
http://localhost:8000/app/ for the tiny React UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.nl_to_sql import answer_question
from src.db import database

app = FastAPI(
    title="DataHub Intern Portfolio API",
    description=(
        "Small demo API over a locally-scraped subset of UIUC courses "
        "and OpenAlex works. See /docs for the interactive schema and "
        "/app/ for a tiny React UI over /ask."
    ),
    version="0.1.0",
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/courses", tags=["courses"])
def get_courses(
    subject: str | None = Query(default=None, description="e.g. CS, MATH"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """List courses, optionally filtered by subject."""
    return database.list_courses(subject=subject, limit=limit)


@app.get("/prereqs/{subject}/{number}", tags=["courses"])
def get_prereqs(subject: str, number: str) -> dict[str, Any]:
    """Return the prerequisite edges for a single course.

    Rows sharing a `group_id` are OR-alternatives; different group_ids
    are AND-required.
    """
    rows = database.get_prereqs(subject, number)
    if not rows:
        # Not an error — the course may just have no listed prereqs —
        # but tell the caller so.
        return {
            "course": f"{subject.upper()} {number}",
            "has_prereqs": False,
            "edges": [],
        }
    return {
        "course": f"{subject.upper()} {number}",
        "has_prereqs": True,
        "edges": rows,
    }


@app.get("/works", tags=["works"])
def get_works(
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """List scholarly works ordered by citation count."""
    return database.list_works(
        year_from=year_from, year_to=year_to, limit=limit
    )


class AskPayload(BaseModel):
    question: str


@app.post("/ask", tags=["agent"])
def ask(payload: AskPayload) -> dict[str, Any]:
    """Natural-language endpoint.

    Tries the rule-based planner first; falls over to the LLM tool-call
    agent (if OPENAI_API_KEY is set) for questions the rules can't
    match. See src/agent/nl_to_sql.py and src/agent/llm_agent.py.
    """
    try:
        return answer_question(payload.question)
    except ValueError as exc:
        # E.g. no template matched and no LLM available.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Tiny React UI. Same-origin, so /ask calls just work — no CORS needed.
# ---------------------------------------------------------------------------
_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="web")
