"""End-to-end: scrape UIUC courses, clean + validate, upsert to SQLite.

Usage:
    python -m scripts.scrape_uiuc --subject CS --term fall --year 2026 --limit 25

The `--offline` flag reads from data/samples/uiuc_cs_sample.xml, which
also happens automatically if the live endpoint is unreachable.
"""

from __future__ import annotations

import argparse
import logging

from src.db.database import record_rejects, upsert_courses
from src.pipeline.clean import clean_course, dedupe_courses
from src.pipeline.validate import validate_courses
from src.scrapers.uiuc_courses import fetch

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scrape_uiuc")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True, help="e.g. CS")
    parser.add_argument("--term", default="fall")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    log.info(
        "Fetching %s %s %s (limit=%s, offline=%s)",
        args.subject, args.term, args.year, args.limit, args.offline,
    )

    raw = list(
        fetch(
            subject=args.subject,
            term=args.term,
            year=args.year,
            limit=args.limit,
            offline=args.offline,
        )
    )
    log.info("Fetched %d raw course records", len(raw))

    cleaned = dedupe_courses(clean_course(r) for r in raw)
    log.info("After clean + dedupe: %d records", len(cleaned))

    result = validate_courses(cleaned)
    log.info(
        "Validation: %d ok, %d rejected", len(result.ok), len(result.rejects)
    )

    summary = upsert_courses(result.ok)  # type: ignore[arg-type]
    log.info(
        "Upserted %d courses and %d prereq edges",
        summary["upserted"], summary["edges"],
    )

    n_rejects = record_rejects("courses", result.rejects)
    if n_rejects:
        log.info("Wrote %d rejected rows to `rejects` table", n_rejects)


if __name__ == "__main__":
    main()
