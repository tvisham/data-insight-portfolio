"""Central config. Reads a .env file if present, otherwise falls back
to sensible defaults. Kept intentionally small — this isn't Django."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    # Optional; if python-dotenv isn't installed the app still runs.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    openalex_mailto: str | None
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str
    uiuc_base_url: str
    openalex_base_url: str
    request_timeout_s: float
    user_agent: str


def _load() -> Settings:
    return Settings(
        db_path=Path(os.getenv("DATAHUB_DB_PATH", PROJECT_ROOT / "datahub.db")),
        openalex_mailto=os.getenv("OPENALEX_MAILTO"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ),
        uiuc_base_url=os.getenv(
            "UIUC_BASE_URL",
            "https://courses.illinois.edu/cisapp/explorer/schedule",
        ),
        openalex_base_url=os.getenv(
            "OPENALEX_BASE_URL", "https://api.openalex.org"
        ),
        request_timeout_s=float(os.getenv("REQUEST_TIMEOUT_S", "15")),
        user_agent=os.getenv(
            "USER_AGENT",
            "datahub-intern-portfolio/0.1 (educational; contact: you@illinois.edu)",
        ),
    )


settings = _load()
