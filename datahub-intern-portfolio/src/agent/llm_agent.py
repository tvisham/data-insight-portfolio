"""LLM tool-call agent that complements the rule-based nl_to_sql.

Design (matches the DataHub posting's "agentic development" bullet):

1. The rule-based planner in ``nl_to_sql.plan()`` is the first line of
   defense — cheap, deterministic, unit-tested.
2. When it can't match a question, we call an OpenAI-compatible chat
   completions endpoint with **tool calling** enabled. The model has
   one tool available, ``query_datahub``, which takes ``sql`` and
   ``explanation`` arguments. We parse the tool call, validate the
   SQL, and execute it read-only.
3. If the LLM step fails for any reason (no API key, HTTP error,
   invalid SQL, unknown table, ...), we raise ``LLMError`` so the
   caller can decide what to do. The API wrapper turns that into a
   422 with the original rule-based error message.

Safety rails on the SQL the model proposes:
- Must be a single statement (no ``;`` except optional trailing one).
- Must start with ``SELECT`` (no INSERT/UPDATE/DELETE/DROP/etc.).
- Only references tables in ``ALLOWED_TABLES``.
- Executed against SQLite opened in read-only URI mode, so even a
  bypass of the string checks can't mutate state.
- Result set capped at ``MAX_ROWS``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

import httpx

# Import the module (not the value) so `settings` is looked up lazily.
# That way tests can reload src.config and this module still sees the
# fresh settings on the next call.
from src import config

# Imported lazily inside functions to avoid a circular import with
# nl_to_sql (which imports us for fallover).


class LLMError(RuntimeError):
    """Any failure in the LLM path — missing key, HTTP error, unsafe SQL."""


ALLOWED_TABLES = {"courses", "prereq_edges", "works", "rejects"}
MAX_ROWS = 500

_FORBIDDEN_KW_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|"
    r"replace|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE
)


SCHEMA_HINT = """\
You are a data analyst for DataHub. Translate the user's question into
a SQLite SELECT query against this schema and call the query_datahub
tool. Never write DDL or DML — SELECT only.

Tables:
  courses(subject TEXT, number TEXT, course_code TEXT, title TEXT,
          credit_hours TEXT, description TEXT, term TEXT, year INTEGER,
          source_url TEXT, ingested_at TEXT)
  -- course_code is denormalized, e.g. 'CS 225'.

  prereq_edges(target_course TEXT, prereq_course TEXT, group_id INTEGER,
               term TEXT, year INTEGER, ingested_at TEXT)
  -- Rows sharing (target_course, group_id) are OR-alternatives.
  -- Different group_ids under the same target_course are AND-required.

  works(openalex_id TEXT, title TEXT, publication_year INTEGER,
        cited_by_count INTEGER, doi TEXT, authors_json TEXT,
        host_venue TEXT, concepts_json TEXT, ingested_at TEXT)
  -- authors_json / concepts_json are JSON arrays; use json_each() to
  -- unnest if needed.

Guidelines:
- Prefer using course_code over reassembling subject + number.
- LIMIT results to at most 200 rows.
- If the question is ambiguous, make a reasonable choice and explain it.
"""


_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "query_datahub",
        "description": (
            "Execute a read-only SQLite SELECT against DataHub and "
            "return the rows. Use this to answer the user's question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SQLite SELECT statement.",
                },
                "explanation": {
                    "type": "string",
                    "description": (
                        "One-sentence, plain-English description of "
                        "what the query returns."
                    ),
                },
            },
            "required": ["sql", "explanation"],
        },
    },
}


# ---------------------------------------------------------------------------
# HTTP transport (indirected so tests can inject a fake)
# ---------------------------------------------------------------------------


Transport = Callable[[dict], dict]


def _default_transport(payload: dict) -> dict:
    if not config.settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY not set")
    url = f"{config.settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            url,
            headers=headers,
            json=payload,
            timeout=config.settings.request_timeout_s,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenAI request failed: {exc}") from exc
    return resp.json()


# ---------------------------------------------------------------------------
# SQL safety
# ---------------------------------------------------------------------------


def _validate_sql(sql: str) -> str:
    """Return a cleaned SQL string or raise LLMError."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise LLMError("empty SQL")
    if ";" in cleaned:
        raise LLMError("multiple statements are not allowed")
    if not re.match(r"(?is)^\s*(with\b.+?\bselect\b|select\b)", cleaned):
        raise LLMError("only SELECT statements are allowed")
    if _FORBIDDEN_KW_RE.search(cleaned):
        raise LLMError("query references a forbidden keyword")
    for tbl in _TABLE_REF_RE.findall(cleaned):
        if tbl.lower() not in ALLOWED_TABLES:
            raise LLMError(f"unknown table referenced: {tbl}")
    return cleaned


def _execute_readonly(sql: str) -> list[dict[str, Any]]:
    """Run `sql` against SQLite in read-only URI mode. Row cap enforced."""
    # file: URI keeps this safe even if the string checks miss something.
    uri = f"file:{config.settings.db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(MAX_ROWS)
    except sqlite3.DatabaseError as exc:
        raise LLMError(f"SQL execution failed: {exc}") from exc
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class LLMPlan:
    sql: str
    explanation: str


def _extract_tool_call(response: dict) -> tuple[str, str]:
    try:
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise LLMError("model did not call the query_datahub tool")
        args_raw = tool_calls[0]["function"]["arguments"]
        args = json.loads(args_raw)
        return args["sql"], args.get("explanation", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"malformed model response: {exc}") from exc


def llm_answer(
    question: str, *, transport: Transport | None = None
) -> dict[str, Any]:
    """Ask the LLM to write SQL, validate it, run it, return the result.

    Returns a dict shaped like the rule-based ``answer_question``:
    ``{question, explanation, sql, params, rows, source}`` with
    ``source="llm"``.
    """
    call = transport or _default_transport

    payload = {
        "model": config.settings.openai_model,
        "messages": [
            {"role": "system", "content": SCHEMA_HINT},
            {"role": "user", "content": question},
        ],
        "tools": [_TOOL_SPEC],
        "tool_choice": {
            "type": "function",
            "function": {"name": "query_datahub"},
        },
        "temperature": 0,
    }

    response = call(payload)
    raw_sql, explanation = _extract_tool_call(response)
    sql = _validate_sql(raw_sql)
    rows = _execute_readonly(sql)

    return {
        "question": question,
        "explanation": explanation or "(model did not provide an explanation)",
        "sql": sql,
        "params": [],
        "rows": rows,
        "source": "llm",
    }
