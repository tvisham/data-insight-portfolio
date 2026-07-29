# Data Catalog

Every dataset that lands in DataHub deserves a one-page entry so
future maintainers know what it is, where it came from, and whether
they can trust it. This file is the demo version.

---

## `courses`

**Description.** Class registration records for UIUC courses in a given
subject / term / year. One row per (subject, number, term, year).

**Source.** `https://courses.illinois.edu/cisapp/explorer/schedule/...` — a
public XML endpoint operated by the University. No authentication.

**Extraction.** [src/scrapers/uiuc_courses.py](../src/scrapers/uiuc_courses.py)
via [scripts/scrape_uiuc.py](../scripts/scrape_uiuc.py).

**Refresh cadence.** Ad hoc. Recommended: once per term after registration
opens, then a delta at term start.

**Owner.** Data engineering intern (you).

**PII.** None — course catalog data only.

**Known issues.**
- The description field is free text and inconsistent across departments.
  Prerequisites are parsed with a rule-based extractor
  ([prereq_parser.py](../src/pipeline/prereq_parser.py)) that handles
  the ~90% case and records unparsed phrasings as notes.
- Some cross-listed courses appear under multiple subject codes; we do
  not dedupe those into a single row.

**Sample query.**
```sql
SELECT course_code, title, credit_hours
FROM courses
WHERE subject = 'CS' AND number LIKE '4%'
ORDER BY number;
```

---

## `prereq_edges`

**Description.** Flattened prerequisite graph derived from `courses.description`.

**Shape.** `(target_course, prereq_course, group_id, term, year)`.
- Rows sharing a `group_id` are OR-alternatives.
- Different `group_id`s under the same `target_course` are AND-required.

**Extraction.** Rebuilt every time `upsert_courses()` runs, so it's
always consistent with the current `courses.description`.

**Known issues.** Non-course prerequisites ("consent of instructor",
"senior standing", etc.) are recorded as parser notes and not stored as
edges. If those become important, promote them to their own table.

**Sample query.**
```sql
-- What are all the courses that require CS 225?
SELECT DISTINCT target_course
FROM prereq_edges
WHERE prereq_course = 'CS 225'
ORDER BY target_course;
```

---

## `works`

**Description.** Scholarly works ingested from the OpenAlex API.

**Source.** `https://api.openalex.org/works?search=<query>`. Free, no
key required. Setting `OPENALEX_MAILTO` opts you into the polite pool
(faster, less rate-limited).

**Extraction.** [src/scrapers/openalex.py](../src/scrapers/openalex.py)
via [scripts/scrape_openalex.py](../scripts/scrape_openalex.py).

**Refresh cadence.** As needed per research topic; each pull is a
snapshot for that query.

**PII.** Author names are public bibliographic metadata.

**Known issues.**
- `cited_by_count` drifts over time — a snapshot from month X won't
  match a re-pull in month Y. Store the ingest timestamp
  (`ingested_at`) alongside any published analysis.
- `authors` and `concepts` are stored as JSON. Fine at this scale; if
  the table grows past ~100k rows, normalize them.

---

## `rejects`

**Description.** Rows that failed row-level validation, with a reason
string. Every scraper writes here instead of silently dropping data.

**Sample query.**
```sql
SELECT source, reason, COUNT(*) AS n
FROM rejects
GROUP BY source, reason
ORDER BY n DESC;
```

Use this during EDA to decide whether a validation rule is too strict.
