"""Ingest scholarly works from the OpenAlex API.

OpenAlex (https://openalex.org) is a free, open catalog of scholarly
papers. Nice fit for a research-oriented data platform demo.

- No API key needed.
- Setting the OPENALEX_MAILTO env var puts us in the "polite pool"
  which gives us more consistent throughput.
- Paginated with `cursor=*` up to a hard cap of 200 rows per page.

Docs: https://docs.openalex.org/how-to-use-the-api/api-overview
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from src.config import settings
from src.scrapers.base import get

log = logging.getLogger(__name__)

_PAGE_SIZE = 50


@dataclass
class WorkRecord:
    openalex_id: str
    title: str | None
    publication_year: int | None
    cited_by_count: int | None
    doi: str | None
    authors: list[str]
    host_venue: str | None
    concepts: list[str]


def _parse_work(raw: dict) -> WorkRecord:
    authors = [
        a.get("author", {}).get("display_name")
        for a in raw.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]
    concepts = [
        c.get("display_name")
        for c in raw.get("concepts", [])[:5]
        if c.get("display_name")
    ]
    host = (
        raw.get("primary_location", {}).get("source", {}).get("display_name")
        if raw.get("primary_location")
        else None
    )
    return WorkRecord(
        openalex_id=str(raw.get("id", "")),
        title=raw.get("title"),
        publication_year=raw.get("publication_year"),
        cited_by_count=raw.get("cited_by_count"),
        doi=raw.get("doi"),
        authors=authors,
        host_venue=host,
        concepts=concepts,
    )


def fetch(*, query: str, limit: int = 50) -> Iterator[WorkRecord]:
    """Yield WorkRecords matching `query`, up to `limit` total.

    Uses cursor pagination. Stops early once we've yielded `limit` rows
    or the API says there's no next cursor.
    """
    url = f"{settings.openalex_base_url}/works"
    cursor: str | None = "*"
    yielded = 0

    while cursor and yielded < limit:
        params: dict[str, str | int] = {
            "search": query,
            "per-page": min(_PAGE_SIZE, limit - yielded),
            "cursor": cursor,
        }
        if settings.openalex_mailto:
            params["mailto"] = settings.openalex_mailto

        try:
            resp = get(url, params=params)
        except Exception as exc:
            log.error("OpenAlex request failed: %s", exc)
            return

        payload = resp.json()
        for raw in payload.get("results", []):
            yield _parse_work(raw)
            yielded += 1
            if yielded >= limit:
                return

        cursor = payload.get("meta", {}).get("next_cursor")
