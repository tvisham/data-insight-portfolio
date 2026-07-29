# Examples

Copy-paste-runnable recipes. Each assumes you've done the Quickstart in
[README.md](../README.md) — i.e. venv is active, deps are installed,
and `python -m scripts.init_db` has been run.

## 1. Extract UIUC CS courses and their prerequisites

```powershell
# Live scrape (needs internet). Falls back to the offline sample if
# the endpoint is unreachable.
python -m scripts.scrape_uiuc --subject CS --term fall --year 2026 --limit 20

# Force offline mode so this always works:
python -m scripts.scrape_uiuc --subject CS --term fall --year 2026 --offline
```

What the script does, end-to-end:

1. Calls `https://courses.illinois.edu/cisapp/explorer/schedule/2026/fall/CS.xml`
   and lists every `<course>` link.
2. For each course, GETs the detail XML.
3. Parses subject, number, title, credit hours, description.
4. Feeds the description through the prereq parser — this is the
   interesting part. Example:

   ```
   "Prerequisite: CS 173 and one of CS 125 or CS 128."
   ```

   becomes:

   ```json
   {"op": "AND", "children": [
       {"course": "CS 173"},
       {"op": "OR", "children": [{"course": "CS 125"}, {"course": "CS 128"}]}
   ]}
   ```

   and this flat edge list:

   ```
   (CS 225, CS 173, group=1)
   (CS 225, CS 125, group=2)
   (CS 225, CS 128, group=2)
   ```

5. Validates every row. Bad rows go to `rejects`, not `/dev/null`.
6. Upserts into `courses` and `prereq_edges`.

## 2. Pull scholarly works from OpenAlex

```powershell
python -m scripts.scrape_openalex --query "machine learning education" --limit 50
```

The script paginates via OpenAlex's cursor, cleans each work, and
upserts by `openalex_id`. If you set `OPENALEX_MAILTO` in `.env`
you'll get faster/quieter responses.

## 3. Query the API

```powershell
uvicorn src.api.main:app --reload --port 8000
```

Then, in another shell:

```powershell
# List CS courses
curl "http://localhost:8000/courses?subject=CS&limit=5"

# Get prerequisites for CS 225
curl "http://localhost:8000/prereqs/CS/225"

# Top 10 cited works
curl "http://localhost:8000/works?limit=10"

# Ask a question
curl -X POST "http://localhost:8000/ask" `
    -H "Content-Type: application/json" `
    -d "{\"question\": \"which courses require CS 225\"}"
```

Or open <http://localhost:8000/docs> for the Swagger UI.

## 4. Ad-hoc SQL against the DB

```powershell
python -c "import sqlite3, json; c = sqlite3.connect('datahub.db'); c.row_factory = sqlite3.Row; print(json.dumps([dict(r) for r in c.execute('SELECT course_code, title FROM courses LIMIT 5')], indent=2))"
```

A few analytical queries worth memorizing:

```sql
-- Prereq fan-out: which courses are gatekeepers?
SELECT prereq_course, COUNT(DISTINCT target_course) AS n_dependents
FROM prereq_edges
GROUP BY prereq_course
ORDER BY n_dependents DESC
LIMIT 10;

-- Publication-year distribution of your latest OpenAlex pull
SELECT publication_year, COUNT(*) AS n
FROM works
WHERE publication_year IS NOT NULL
GROUP BY publication_year
ORDER BY publication_year;

-- Validation-rejects summary
SELECT source, reason, COUNT(*) AS n
FROM rejects
GROUP BY source, reason
ORDER BY n DESC;
```

## 5. Reproduce the demo from a fresh clone

```powershell
git clone <this repo>
cd datahub-intern-portfolio
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m scripts.init_db
python -m scripts.scrape_uiuc --subject CS --term fall --year 2026 --offline
python -m scripts.scrape_openalex --query "climate policy" --limit 25
uvicorn src.api.main:app --reload
```

Everything after `init_db` is optional — you can just run `pytest -q`
to see the pipeline exercised end-to-end against the offline sample.
