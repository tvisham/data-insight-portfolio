"""Initialize (or re-initialize) the SQLite database.

Usage:
    python -m scripts.init_db
"""

from __future__ import annotations

from src.config import settings
from src.db.database import init_schema


def main() -> None:
    init_schema()
    print(f"Initialized schema at {settings.db_path}")


if __name__ == "__main__":
    main()
