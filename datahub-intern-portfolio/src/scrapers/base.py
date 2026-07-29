"""Shared HTTP helpers for scrapers.

Nothing fancy — a thin wrapper around httpx that:
- sets a polite User-Agent
- retries a couple of times on transient failures
- respects a global timeout from `settings`

Each scraper module builds on this so we don't scatter retry/timeout
logic across the codebase.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.config import settings

log = logging.getLogger(__name__)

_DEFAULT_RETRIES = 3
_BACKOFF_S = 0.75


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": settings.user_agent, "Accept": "*/*"}
    if extra:
        headers.update(extra)
    return headers


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = _DEFAULT_RETRIES,
) -> httpx.Response:
    """GET a URL with retries. Raises the final exception on failure."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(
                url,
                params=params,
                headers=_headers(headers),
                timeout=settings.request_timeout_s,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_exc = exc
            log.warning(
                "GET %s failed (attempt %d/%d): %s", url, attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(_BACKOFF_S * attempt)
    assert last_exc is not None  # for the type checker
    raise last_exc
