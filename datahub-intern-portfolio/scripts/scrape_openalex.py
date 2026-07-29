"""End-to-end: pull works from OpenAlex, clean + validate, upsert.

Usage:
    python -m scripts.scrape_openalex --query "climate policy" --limit 50
"""

from __future__ import annotations

import argparse
import logging

from src.db.database import record_rejects, upsert_works
from src.pipeline.clean import clean_work
from src.pipeline.validate import validate_works
from src.scrapers.openalex import fetch

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scrape_openalex")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    log.info("Fetching OpenAlex works for %r (limit=%d)", args.query, args.limit)
    raw = list(fetch(query=args.query, limit=args.limit))
    log.info("Fetched %d works", len(raw))

    cleaned = [clean_work(r) for r in raw]
    result = validate_works(cleaned)
    log.info("Validation: %d ok, %d rejected", len(result.ok), len(result.rejects))

    summary = upsert_works(result.ok)  # type: ignore[arg-type]
    log.info("Upserted %d works", summary["upserted"])

    n_rejects = record_rejects("works", result.rejects)
    if n_rejects:
        log.info("Wrote %d rejected rows to `rejects` table", n_rejects)


if __name__ == "__main__":
    main()
