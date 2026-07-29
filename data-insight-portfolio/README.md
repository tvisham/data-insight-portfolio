# Data Insights Practice

## What's inside

| Area | What it does | Where |
|---|---|---|
| Web scraping | Pulls UIUC course + prerequisite data from `courses.illinois.edu` XML endpoints | [src/scrapers/uiuc_courses.py](src/scrapers/uiuc_courses.py) |
| Public API ingest | Pulls scholarly works from the OpenAlex API | [src/scrapers/openalex.py](src/scrapers/openalex.py) |
| Cleaning + validation | Normalizes rows, parses prerequisite strings into structured graphs | [src/pipeline](src/pipeline) |
| Storage | SQLite schema + loader | [src/db](src/db) |
| Serving | FastAPI app exposing `/courses`, `/prereqs`, `/works`, `/ask` | [src/api/main.py](src/api/main.py) |
| Rule-based agent | Deterministic NL → SQL templates | [src/agent/nl_to_sql.py](src/agent/nl_to_sql.py) |
| LLM tool-call agent | OpenAI-compatible tool call → validated SELECT, wired as fallover | [src/agent/llm_agent.py](src/agent/llm_agent.py) |
| Web UI | Zero-build React page over `/ask`, served at `/app/` | [web/index.html](web/index.html) |
| Tests | pytest for parser, cleaner, API, LLM agent | [tests/](tests) |

Architecture and dataflow are in [ARCHITECTURE.md](ARCHITECTURE.md).
Runnable examples are in [docs/examples.md](docs/examples.md).

## Quickstart

```powershell
# 1. Create a venv and install deps
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Initialize the SQLite database
python -m scripts.init_db

# 3. Scrape a small sample (works offline against bundled sample data too)
python -m scripts.scrape_uiuc --subject CS --term fall --year 2026 --limit 10
python -m scripts.scrape_openalex --query "machine learning education" --limit 25

# 4. Serve the API + tiny React UI
uvicorn src.api.main:app --reload --port 8000
# API docs:  http://localhost:8000/docs
# React UI:  http://localhost:8000/app/

# 5. Run tests
pytest -q
```

If you have no internet, the scrapers fall back to
[data/samples/uiuc_cs_sample.xml](data/samples/uiuc_cs_sample.xml) so the rest
of the pipeline still works.

## Two "real" extraction examples

These are the walkthroughs a researcher on the team would actually run.

### 1. Class registration + prerequisite scraping (web/XML)

Goal: for every CS course offered in Fall 2026, capture the course code,
title, credit hours, description, and a **structured** prerequisite graph
(so you can answer "what do I need before CS 421?").

```powershell
python -m scripts.scrape_uiuc --subject CS --term fall --year 2026
```

Under the hood ([src/scrapers/uiuc_courses.py](src/scrapers/uiuc_courses.py)):

1. Hit `https://courses.illinois.edu/cisapp/explorer/schedule/{year}/{term}/{subject}.xml`
   to list courses in the subject.
2. Follow each `<course>` link to get the course detail XML.
3. Parse the free-text description; regex out the **"Prerequisite:"** clause.
4. Feed that string into [`prereq_parser.parse`](src/pipeline/prereq_parser.py)
   to build a small AND/OR tree of prerequisite courses.
5. Clean + validate rows, then upsert into the `courses` and `prereq_edges`
   tables in SQLite.

Then query it:

```powershell
curl http://localhost:8000/prereqs/CS/421
```

### 2. Research works from a public JSON API

Goal: pull a small corpus of scholarly works matching a topic so a researcher
can see which authors/institutions/years dominate.

```powershell
python -m scripts.scrape_openalex --query "climate policy" --limit 200
```

This uses the free [OpenAlex API](https://docs.openalex.org/) — no key
required, but you can set `OPENALEX_MAILTO` in `.env` to get the
"polite pool" (faster, more reliable). See [src/scrapers/openalex.py](src/scrapers/openalex.py).

## Project layout

```
datahub-intern-portfolio/
├── src/
│   ├── config.py           # env loading + settings
│   ├── scrapers/           # one module per source
│   ├── pipeline/           # clean, validate, parse prereqs
│   ├── db/                 # schema.sql + tiny helper
│   ├── api/                # FastAPI app (also mounts /app)
│   └── agent/              # nl_to_sql (rules) + llm_agent (tool-call)
├── web/                    # zero-build React page over /ask
├── scripts/                # entrypoints you actually run
├── tests/                  # pytest
├── data/samples/           # offline fixtures
└── docs/                   # architecture, examples, catalog
```

## The `/ask` agent

Two agents cooperate behind the same endpoint:

1. **Rule-based planner** ([src/agent/nl_to_sql.py](src/agent/nl_to_sql.py)) —
   regex templates → parameterized SQL. Fast, deterministic, unit-tested.
   Handles the common questions (`prereqs for CS 421`, `how many courses
   in CS`, `top 5 most cited works`, …).
2. **LLM tool-call agent** ([src/agent/llm_agent.py](src/agent/llm_agent.py)) —
   OpenAI-compatible chat completions with a single `query_datahub` tool.
   The model calls the tool with a SQL + explanation; we **validate**
   the SQL (SELECT-only, allowlisted tables, no dangerous keywords) and
   **execute** it against SQLite in read-only URI mode.

Fallover is one-way and lazy: the rule-based path runs first, and the
LLM only sees a question the rules can't match. If `OPENAI_API_KEY`
isn't set (or the LLM step fails validation), the API surfaces the
rule-based hint message instead. The response includes a
`"source": "rules" | "llm"` field so the UI can badge which path
answered.

Enable it by setting these in `.env` (see [.env.example](.env.example)):

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini            # optional; default shown
OPENAI_BASE_URL=https://api.openai.com/v1   # optional; override for Azure/local
```

## The React page

A single self-contained [web/index.html](web/index.html) using React 18
+ Babel-standalone from a CDN — **no `npm install`, no build step**.
FastAPI mounts it at `/app/` so the browser POSTs to `/ask` on the same
origin (no CORS to configure). Deliberately zero-tooling: a real
deployment would swap this for a Vite/Next.js build, but for a demo it
keeps the diff you're reviewing small.
